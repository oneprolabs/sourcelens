import logging
import uuid
from contextlib import contextmanager

from django.db import transaction
from django.utils import timezone

from .document_attachments import get_run_document_attachments
from .models import MessageAttachment, Run, RunExecution, RunStep
from .services import (
    LensNodeDispatchError,
    MultimodalPreprocessingError,
    analyze_multimodal_intent,
    build_loaded_plugin_skills,
    build_loaded_plugins,
    build_loaded_skills,
    create_run_execution_snapshot,
    dispatch_run_to_lensnode,
    finish_lensnode_run,
    rewrite_query,
    resolve_run_answer_language,
    run_execution_question,
    validate_run_dispatch,
)

logger = logging.getLogger(__name__)


def _document_analysis_prompt(run):
    """Return the document-only prompt in the user's AI language."""

    profile = getattr(run.session.user, "profile", None)
    language = str(getattr(profile, "language", "") or "").lower()
    if language.startswith("zh"):
        return "请分析所附文档"
    return "Analyze the attached document."


class LensExecutionError(Exception):
    """Base exception for Lens execution failures."""


@contextmanager
def run_step(run, step_type, sequence):
    """Create and finalize a RunStep around one execution phase."""

    step, _ = RunStep.objects.update_or_create(
        run=run,
        sequence=sequence,
        defaults={
            "step_type": step_type,
            "status": RunStep.Status.RUNNING,
            "detail": {},
        },
    )
    try:
        yield step
    except Exception as exc:
        step.status = RunStep.Status.FAILED
        step.detail = {
            **step.detail,
            "status": "failed",
            "error": getattr(exc, "code", str(exc)),
        }
        if hasattr(exc, "reason"):
            step.detail["reason"] = exc.reason
        step.save(update_fields=["status", "detail", "updated_at"])
        raise
    else:
        step.status = RunStep.Status.DONE
        step.save(update_fields=["status", "detail", "updated_at"])


def _mark_run_failed(run, exc):
    """Persist terminal failure state for a run."""

    run.status = Run.Status.FAILED
    run.error = str(exc)
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])
    if hasattr(run, "execution"):
        run.execution.status = RunExecution.Status.FAILED
        run.execution.finished_at = run.finished_at
        run.execution.save(update_fields=["status", "finished_at"])



def _lensnode_dispatch(state):
    """LangGraph node for creating a snapshot and dispatching to LensNode."""

    run = state["run"]
    assistant = run.session.assistant
    question = run_execution_question(run)
    execution = RunExecution.objects.filter(run=run).first()
    selected_image_uuids = set(
        (execution.runtime_snapshot if execution else {}).get(
            "session_attachment_uuids",
            [],
        )
    )
    has_images = MessageAttachment.objects.filter(
        session=run.session,
        kind=MessageAttachment.Kind.IMAGE,
        uuid__in=selected_image_uuids,
    ).exists()
    subject_documents = get_run_document_attachments(run.uuid)
    expected_document_count = int(state.get("expected_document_count") or 0)
    if expected_document_count < 0:
        raise LensNodeDispatchError("DOCUMENT_ATTACHMENT_STATE_UNAVAILABLE")
    if len(subject_documents) < expected_document_count:
        raise LensNodeDispatchError("DOCUMENT_ATTACHMENT_UNAVAILABLE")
    has_documents = bool(subject_documents)
    if has_images:
        with run_step(run, RunStep.StepType.MULTIMODAL, 0) as step:
            analysis = analyze_multimodal_intent(run)
            question = (
                "已成功接收并分析用户上传的图片。以下是视觉模型从图片中"
                "提取的关键信息，请基于这些图片证据回答当前问题，不要声称"
                "没有上传图片。图片识别结果已经包含在下面；不要因为工作区"
                "中没有图片文件而否认这张上传图片，也不要为图片问题搜索工作区：\n"
                f"{analysis['question']}"
            )
            step.detail = {
                "rewritten": analysis["rewritten"],
                "original": analysis.get(
                    "original", run.input_message.content
                ),
                "query": question,
                "image_count": analysis.get("image_count", 0),
                "status": analysis.get("status", "succeeded"),
            }
            if analysis.get("usage"):
                step.detail["usage"] = analysis["usage"]
    elif assistant.preprocess_model_ref and (question or "").strip():
        with run_step(run, RunStep.StepType.QUERY_REWRITE, 0) as step:
            rewrite = rewrite_query(run)
            question = rewrite["question"]
            step.detail = {
                "rewritten": rewrite["rewritten"],
                "original": rewrite.get("original", run.input_message.content),
                "query": question,
            }
            if rewrite.get("usage"):
                step.detail["usage"] = rewrite["usage"]
            if rewrite.get("error"):
                step.detail["error"] = rewrite["error"]
    if has_images and not (question or "").strip():
        # Image-only question whose vision preprocess yielded nothing
        # (e.g. the multimodal call failed). Give the node a usable prompt
        # rather than dispatching an empty query.
        question = "请分析所附图片中的问题"
    if has_documents and not (question or "").strip():
        question = _document_analysis_prompt(run)
    with run_step(run, RunStep.StepType.RETRIEVAL, 1) as step:
        execution = create_run_execution_snapshot(
            run,
            answer_language=resolve_run_answer_language(
                run.session,
                run.input_message.content,
                run.retry_of_run,
            ),
        )
        from .session_workspace import (
            build_session_workspace, session_source_dirs,
        )

        build_session_workspace(run.session)
        execution.target_dirs = session_source_dirs(run.session)
        execution.save(update_fields=["target_dirs"])
        execution.loaded_plugins = build_loaded_plugins(assistant)
        execution.loaded_skills = build_loaded_skills(assistant)
        execution.loaded_skills.extend(
            build_loaded_plugin_skills(
                assistant,
                loaded_plugins=execution.loaded_plugins,
            )
        )
        execution.save(update_fields=["loaded_skills", "loaded_plugins"])
        validate_run_dispatch(run)
        execution.status = RunExecution.Status.DISPATCHED
        execution.started_at = execution.started_at or timezone.now()
        execution.dispatch_id = uuid.uuid4()
        execution.admitted_at = None
        execution.checkpoint_ready_at = None
        execution.save(
            update_fields=[
                "status",
                "started_at",
                "dispatch_id",
                "admitted_at",
                "checkpoint_ready_at",
            ]
        )
        dispatch_run_to_lensnode(
            run,
            question,
            subject_documents=subject_documents,
            dispatch_id=execution.dispatch_id,
        )
        step.detail = {
            "lensnode_uuid": str(run.lensnode.uuid),
            "task": execution.task,
            "target_dirs": execution.target_dirs,
            "dispatched": True,
        }
    return state


def _lensnode_inline_done(state):
    """Complete an inline test run without waiting for an external LensNode."""

    run = state["run"]
    answer = (
        f"Dispatched {run.execution.task} to "
        f"{run.lensnode.name}."
    )
    with transaction.atomic():
        with run_step(run, RunStep.StepType.ANSWER, 2) as step:
            step.detail = {
                "answer_length": len(answer),
                "inline": True,
            }
        with run_step(run, RunStep.StepType.STREAM, 3) as step:
            run.output_message.content = answer
            run.output_message.run = run
            run.output_message.save(update_fields=["content", "run"])
            step.detail = {"content_length": len(answer)}
        finish_lensnode_run(run.uuid, Run.Status.DONE)
    return state


def _build_execution_graph(dispatch):
    """Build the Lens execution LangGraph."""

    from langgraph.graph import END, StateGraph

    graph = StateGraph(dict)
    graph.add_node("dispatch", _lensnode_dispatch)
    graph.set_entry_point("dispatch")
    if dispatch:
        graph.add_edge("dispatch", END)
    else:
        graph.add_node("inline_done", _lensnode_inline_done)
        graph.add_edge("dispatch", "inline_done")
        graph.add_edge("inline_done", END)
    return graph.compile()


def execute_answer_run(
    run,
    dispatch=True,
    expected_document_count=0,
):
    """Execute a Lens answer run through LensNode dispatch flow."""

    run.refresh_from_db(fields=["status"])
    if run.status != Run.Status.QUEUED:
        return run
    assistant = run.session.assistant
    max_concurrency = assistant.max_concurrency
    if max_concurrency > 0:
        active_count = (
            Run.objects.filter(
                session__assistant=assistant,
                status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
            )
            .exclude(uuid=run.uuid)
            .count()
        )
        if active_count >= max_concurrency:
            logger.info(
                "run %s [%s]: assistant concurrency cap (active=%d/%d),"
                " retrying in 3s",
                run.uuid,
                assistant.slug,
                active_count,
                max_concurrency,
            )
            from .tasks import enqueue_answer_run_task

            enqueue_answer_run_task(
                run.uuid,
                expected_document_count,
                countdown=3,
            )
            return run

    logger.info(
        "run %s [%s]: starting execution",
        run.uuid,
        assistant.slug,
    )
    try:
        from .datasource_routing import prepare_run_datasources

        if not prepare_run_datasources(run):
            from .tasks import enqueue_answer_run_task

            run.refresh_from_db(fields=["status"])
            if run.status == Run.Status.QUEUED:
                enqueue_answer_run_task(
                    run.uuid, expected_document_count, countdown=3,
                )
            return run
        run.status = Run.Status.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.last_activity_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "started_at",
                "last_activity_at",
                "updated_at",
            ]
        )

        graph = _build_execution_graph(dispatch)
        graph.invoke(
            {
                "run": run,
                "expected_document_count": expected_document_count,
            }
        )
        run.refresh_from_db()
        if dispatch and run.status not in [
            Run.Status.AWAITING_USER_INPUT,
            Run.Status.DONE,
            Run.Status.FAILED,
            Run.Status.CANCELLED,
        ]:
            run.status = Run.Status.STREAMING
            run.save(update_fields=["status", "updated_at"])

    except (LensNodeDispatchError, MultimodalPreprocessingError) as exc:
        logger.error("run %s: dispatch failed: %s", run.uuid, exc)
        _mark_run_failed(run, exc)
        raise
    except Exception as exc:
        logger.exception("run %s: unexpected execution error", run.uuid)
        _mark_run_failed(run, exc)
        raise

    return run
