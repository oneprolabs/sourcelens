import json
import subprocess
import sys
import threading
import time
import types

from lensnode.agent_runtime import RunCancelledError
from lensnode.datasource_manifest import SyncResult
from lensnode.document_convert import post_process_documents


def document_item(name):
    """Return one persisted source entry for conversion."""

    return {"local_path": name, "status": "synced", "metadata": {}}


def test_parallel_conversion_reports_completed_count_instead_of_source_index(tmp_path, monkeypatch):
    """A later source completing first must not report all sources complete."""

    first_finished = threading.Event()
    events = []
    for name in ("first.txt", "second.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    def convert_job(job, target, context):
        if job.index == 1:
            assert first_finished.wait(timeout=3)
        return {"chars": 1}

    def emit(event):
        events.append(event)
        if event["step"] == "conversion_progress":
            first_finished.set()

    monkeypatch.setattr("lensnode.document_convert.convert_job", convert_job)
    post_process_documents(
        {"target_path": str(tmp_path), "conversion": {"document": True, "workers": 2}},
        SyncResult(items=[document_item("first.txt"), document_item("second.txt")]),
        emit=emit,
    )

    progress = [event for event in events if event["step"] == "conversion_progress"]
    assert [event["current_file"] for event in progress] == ["second.txt", "first.txt"]
    assert [event["progress_current"] for event in progress] == [1, 2]
    assert [event["progress_percent"] for event in progress] == [50, 100]


def test_cancelling_datasource_conversion_interrupts_a_blocked_document_parser(tmp_path, monkeypatch):
    """A stuck parser cannot retain a cancelled sync's execution capacity."""

    completed = threading.Event()
    cancel_event = threading.Event()
    errors = []
    entered_path = tmp_path / "parser-entered"
    release_path = tmp_path / "release-parser"
    worker_script = tmp_path / "parser_worker.py"
    worker_script.write_text(
        "import json, pathlib, sys, time\n"
        "entered = pathlib.Path(sys.argv[2])\n"
        "release = pathlib.Path(sys.argv[3])\n"
        "entered.touch()\n"
        "deadline = time.monotonic() + 5\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
        "print(json.dumps({'text_content': 'Converted content'}))\n",
        encoding="utf-8",
    )

    def worker_command(path):
        return [sys.executable, str(worker_script), str(path), str(entered_path), str(release_path)]

    class BlockingMarkItDown:
        """Run an external blocked parser when the subprocess boundary is absent."""

        def convert(self, path):
            result = subprocess.run(worker_command(path), capture_output=True, text=True, check=True)
            return types.SimpleNamespace(**json.loads(result.stdout))

    monkeypatch.setitem(
        sys.modules,
        "markitdown",
        types.SimpleNamespace(MarkItDown=BlockingMarkItDown, __version__="test"),
    )
    monkeypatch.setattr("lensnode.document_convert.document_stats", lambda *args: {})
    monkeypatch.setattr(
        "lensnode.document_convert._markitdown_worker_command", worker_command, raising=False
    )
    (tmp_path / "blocked.docx").write_bytes(b"document")

    def run():
        try:
            post_process_documents(
                {
                    "target_path": str(tmp_path),
                    "cancel_event": cancel_event,
                    "conversion": {"document": True, "workers": 1},
                },
                SyncResult(items=[document_item("blocked.docx")]),
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while not entered_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered_path.exists()
        cancel_event.set()
        interrupted_before_parser_returns = completed.wait(timeout=0.5)
    finally:
        release_path.touch()
        worker.join(timeout=3)

    assert interrupted_before_parser_returns, "Cancelled conversion remained blocked inside MarkItDown"
    assert len(errors) == 1
    assert isinstance(errors[0], RunCancelledError)


def test_datasource_file_timeout_fails_slow_parser_and_continues_following_files(tmp_path, monkeypatch):
    """A parser's own deadline contains one bad document without cancelling sync."""

    worker_script = tmp_path / "parser_worker.py"
    worker_script.write_text(
        "import json, pathlib, sys, time\n"
        "name = pathlib.Path(sys.argv[1]).name\n"
        "if name == 'blocked.docx': time.sleep(1)\n"
        "print(json.dumps({'text_content': 'Converted ' + name}))\n",
        encoding="utf-8",
    )

    def worker_command(path):
        return [sys.executable, str(worker_script), str(path)]

    class FakeMarkItDown:
        """Use the same external parser when the timeout boundary is absent."""

        def convert(self, path):
            result = subprocess.run(worker_command(path), capture_output=True, text=True, check=True)
            return types.SimpleNamespace(**json.loads(result.stdout))

    monkeypatch.setitem(
        sys.modules,
        "markitdown",
        types.SimpleNamespace(MarkItDown=FakeMarkItDown, __version__="test"),
    )
    monkeypatch.setattr("lensnode.document_convert.document_stats", lambda *args: {})
    monkeypatch.setattr(
        "lensnode.document_convert._markitdown_worker_command", worker_command, raising=False
    )
    for name in ("blocked.docx", "following.docx"):
        (tmp_path / name).write_bytes(b"document")
    cancel_event = threading.Event()
    started = time.monotonic()

    summary = post_process_documents(
        {
            "target_path": str(tmp_path),
            "cancel_event": cancel_event,
            "conversion": {"document": True, "workers": 1, "file_timeout_s": 0.2},
        },
        SyncResult(items=[document_item("blocked.docx"), document_item("following.docx")]),
    )

    assert summary["failed"] == 1
    assert summary["success"] == 1
    assert cancel_event.is_set() is False
    assert time.monotonic() - started < 0.8
    failed_meta = json.loads((tmp_path / "blocked.docx.sourcelens" / "meta.json").read_text())
    assert "TIMEOUT" in failed_meta["conversion"]["error"]
    assert failed_meta["conversion"]["status"] == "failed"
    assert failed_meta["conversion"]["attempts"] == 1
    following_meta = json.loads((tmp_path / "following.docx.sourcelens" / "meta.json").read_text())
    assert following_meta["conversion"]["status"] == "success"
    content = (tmp_path / "following.docx.sourcelens" / "content.md").read_text()
    assert "Converted following.docx" in content
