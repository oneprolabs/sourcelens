"""Admin decision metadata and authenticated LensNode decision calls."""

from agentcore_metering.adapters.django.models import LLMConfig
from agentcore_metering.adapters.django.services.decisions import decision_error_detail
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from lens.decisions import decision_catalog
from lens.models import Run, RunExecution
from lens.runtime_decisions import call_runtime_decision

from .base import LensNodeAuthMixin
from .gateway import LensNodeAIGatewayView


class DecisionCatalogView(APIView):
    """Expose host-owned decision settings without a Plugin connection."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        """Return supported control gates and ranking rubrics."""

        return Response(decision_catalog())


class LensNodeDecisionGatewayView(LensNodeAuthMixin, APIView):
    """Meter decisions using only the model frozen into the authenticated Run."""

    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]

    def post(self, request):
        """Authorize a bounded decision call and delegate it to agentcore."""

        node = self._authenticate_lensnode(request)
        if node is None:
            return Response({"detail": "Invalid LensNode token."}, status=401)
        try:
            run = Run.objects.select_related("session").get(uuid=request.data.get("run_uuid"), lensnode=node)
        except (Run.DoesNotExist, ValidationError, ValueError):
            return Response({"detail": "Run not found."}, status=404)
        if run.status not in {Run.Status.RUNNING, Run.Status.STREAMING}:
            return Response({"detail": "Run is not active."}, status=409)
        execution = get_object_or_404(RunExecution, run=run)
        frozen = (execution.runtime_snapshot or {}).get("decision_model") or {}
        model_ref = str(request.data.get("model_ref") or "")
        if not model_ref or model_ref != frozen.get("model_ref"):
            return Response({"detail": "Decision model is not authorized for this Run."}, status=403)
        model = get_object_or_404(LLMConfig, uuid=model_ref, model_type="decision", scope="global", is_active=True)
        text = request.data.get("input")
        questions = request.data.get("questions")
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            return Response({"detail": "Decision input must be bounded non-empty text."}, status=400)
        source = request.data.get("source")
        if source == "decision_score":
            definitions = {"score": frozen["score"]} if frozen.get("score") else {}
        else:
            definitions = frozen.get("gates" if source == "decision_gate" else "analyses") or {}
        if (
            source not in {"decision_gate", "decision_rank", "decision_score"}
            or not isinstance(questions, list)
            or len(questions) != 1
        ):
            return Response({"detail": "Invalid decision request."}, status=400)
        if not isinstance(questions[0], dict) or questions[0].get("name") not in definitions:
            return Response({"detail": "Decision is not configured for this Run."}, status=403)
        definition = definitions[questions[0]["name"]]
        expected_type = "predicate" if definition.get("kind") == "noul" else definition.get("kind")
        if questions[0].get("type") != expected_type:
            return Response({"detail": "Decision type does not match this Run."}, status=400)
        if source == "decision_score":
            levels = questions[0].get("levels")
            if (
                not isinstance(levels, list)
                or not 2 <= len(levels) <= 10
                or any(
                    not isinstance(level, dict)
                    or not isinstance(level.get("label"), str)
                    or not level["label"].strip()
                    or len(level["label"]) > 1000
                    for level in levels
                )
            ):
                return Response({"detail": "Score requires 2 to 10 non-empty ordered labels."}, status=400)
        call_id = request.data.get("call_id")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 160:
            return Response({"detail": "Invalid decision call identity."}, status=400)
        budget_lock = LensNodeAIGatewayView._acquire_parent_budget_lock(run, wait_timeout_s=0.2)
        if budget_lock in (None, "exhausted"):
            return Response({"detail": "Run token budget is unavailable."}, status=429)
        try:
            config = dict(model.config)
            config["request_timeout_seconds"] = (
                definition["timeout_seconds"]
                if definition.get("timeout_seconds")
                else min(int(config.get("request_timeout_seconds") or 2), 2)
            )
            config["num_retries"] = 0
            answers, usage = call_runtime_decision(
                provider=model.provider,
                config=config,
                config_uuid=model_ref,
                input=text,
                questions=questions,
                state={
                    "user_id": run.session.user_id,
                    "node_name": source,
                    "source_type": "lensnode_gateway",
                    "lensnode_uuid": str(node.uuid),
                    "metadata": {"run_uuid": str(run.uuid), "call_id": call_id, "decision_source": source},
                },
            )
        except ValueError:
            return Response({"detail": "Invalid decision configuration or response."}, status=400)
        except Exception as exc:
            return Response({"detail": decision_error_detail(exc)}, status=502)
        finally:
            LensNodeAIGatewayView._release_parent_budget_lock(budget_lock)
        return Response({"answers": answers, "usage": usage})
