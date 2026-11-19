"""SourceLens model administration with explicit platform/tenant ownership."""

from uuid import UUID

from agentcore_metering.adapters.django.models import LLMConfig
from agentcore_metering.adapters.django.serializers import LLMConfigSerializer, LLMConfigWriteSerializer
from agentcore_metering.adapters.django.services.model_info import reset_model_info_on_change
from agentcore_metering.adapters.django.views.config_catalog import (
    AdminLLMConfigModelsView,
    AdminLLMConfigProvidersView,
)
from agentcore_metering.adapters.django.views.config_validation import (
    AdminLLMConfigTestCallView,
    AdminLLMConfigTestView,
)
from agentcore_metering.adapters.django.views.decisions import AdminDecisionTestView
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.delegation import get_active_delegation
from accounts.models import Tenant
from lens.model_scope import HasModelAccess, model_call_scope, model_queryset, model_tenant
from lens.models import LLMConfigScope

SECRET_FIELDS = {"api_key", "key", "token", "secret", "password"}


def preserve_secrets(existing, incoming):
    """Merge edits using the same recursive contract as credential masking."""

    if isinstance(incoming, dict):
        previous = existing if isinstance(existing, dict) else {}
        result = dict(previous)
        for key, value in incoming.items():
            if key.lower() in SECRET_FIELDS and value == "***":
                if key not in previous:
                    raise serializers.ValidationError({"config": "Enter a credential before saving."})
                continue
            result[key] = preserve_secrets(previous.get(key), value)
        return result
    if isinstance(incoming, list):
        previous = existing if isinstance(existing, list) else []
        return [preserve_secrets(previous[i] if i < len(previous) else None, item) for i, item in enumerate(incoming)]
    return incoming


class ScopedConfigWriteSerializer(LLMConfigWriteSerializer):
    """Each model type can have a default within its owning scope."""

    def validate(self, attrs):
        """Keep provider validation while owning the default selection policy."""

        default = attrs.pop("is_default", None)
        result = super().validate(attrs)
        if default is not None:
            result["is_default"] = default
        return result


def model_audit(request, action, config_ref=""):
    """Do not put credentials, prompts or provider responses in the audit log."""

    record_audit(
        action=action,
        request=request,
        actor=request.user,
        tenant=model_tenant(request),
        delegation=get_active_delegation(request.user),
        resource_type="llm_config",
        resource_id=str(config_ref),
    )


def config_dict(config):
    """Keep keys write-only while preserving the editor's masked-value contract."""

    data = LLMConfigSerializer(config, context={"default_config_uuid": config.uuid if config.is_default else None}).data

    def redact(value):
        """Mask nested provider authentication fields as well."""

        if isinstance(value, list):
            return [redact(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {key: ("***" if key.lower() in SECRET_FIELDS and item else redact(item)) for key, item in value.items()}

    data["config"] = redact(config.config or {})
    data["scope"] = "global"
    data.pop("user", None)
    data.pop("user_id", None)
    data.pop("username", None)
    data["ownership_scope"] = "tenant" if config.ownership.tenant_id else "platform"
    return data


def scoped_config(request, config_ref=None, data=None):
    """Require both legacy and UUID selectors to refer to the authorized row."""

    query = model_queryset(model_tenant(request)).select_related("ownership")
    if config_ref is not None:
        query = query.filter(pk=config_ref) if isinstance(config_ref, int) else query.filter(uuid=config_ref)
    if data is not None:
        if data.get("config_uuid"):
            query = query.filter(uuid=data["config_uuid"])
        if data.get("config_id") is not None:
            query = query.filter(pk=data["config_id"])
    return get_object_or_404(query)


def validate_fallbacks(config, tenant, instance=None, model_type="llm"):
    """Validate the ordered chain and reject cycles before saving."""

    refs = config.get("fallback_model_uuids", config.get("fallback_model_uuid", []))
    if isinstance(refs, str):
        refs = [refs] if refs else []
    try:
        if not isinstance(refs, list) or len(refs) > 3:
            raise ValueError
        refs = [str(UUID(str(ref))) for ref in refs]
    except (ValueError, TypeError):
        raise serializers.ValidationError({"config": "Choose at most three valid fallback models."}) from None
    if len(set(refs)) != len(refs) or (instance and str(instance.uuid) in refs):
        raise serializers.ValidationError({"config": "Fallback models must be unique and cannot include this model."})
    eligible = model_queryset(tenant).filter(model_type=model_type, is_active=True)
    if eligible.filter(uuid__in=refs).count() != len(refs):
        raise serializers.ValidationError({"config": "Fallback models must be active models in this scope."})
    graph = {}
    for candidate in model_queryset(tenant):
        links = candidate.config.get("fallback_model_uuids", candidate.config.get("fallback_model_uuid", []))
        graph[str(candidate.uuid)] = [links] if isinstance(links, str) else links if isinstance(links, list) else []
    if instance:
        graph[str(instance.uuid)] = refs

        def cyclic(ref, path):
            """Follow only local, bounded edges; invalid legacy edges do not grant access."""

            if ref in path:
                return True
            return any(cyclic(str(child), path | {ref}) for child in graph.get(ref, []))

        if cyclic(str(instance.uuid), set()):
            raise serializers.ValidationError({"config": "Fallback models cannot form a cycle."})
    config["fallback_model_uuids"] = refs
    config.pop("fallback_model_uuid", None)
    return config


def write_config(request, instance=None):
    """Save one model and its ownership atomically; scope is never writable."""

    tenant = model_tenant(request)
    lock = Tenant.objects.select_for_update()
    lock.get(pk=tenant.pk) if tenant else lock.get(slug="default")
    if instance:
        instance.refresh_from_db()
    if request.data.get("scope", "global") != "global" or request.data.get("user_id") is not None:
        raise serializers.ValidationError({"scope": "Models belong to the current platform or tenant scope."})
    payload = dict(request.data)
    if instance and isinstance(payload.get("config"), dict):
        payload["config"] = preserve_secrets(instance.config, payload["config"])
    elif isinstance(payload.get("config"), dict):
        payload["config"] = preserve_secrets({}, payload["config"])
    serializer = ScopedConfigWriteSerializer(data=payload, partial=instance is not None, context={"instance": instance})
    serializer.is_valid(raise_exception=True)
    values = {key: value for key, value in serializer.validated_data.items() if key not in {"config_uuid", "config_id"}}
    if "config" in values:
        previous = instance.config if instance else {}
        values["config"] = validate_fallbacks(
            preserve_secrets(previous, values["config"]), tenant, instance, values["model_type"]
        )
        if instance:
            values["config"] = reset_model_info_on_change(
                instance.provider, instance.config, values.get("provider", instance.provider), values["config"]
            )
    if values.get("is_active") is False:
        values["is_default"] = False
    if instance is None:
        instance = LLMConfig.objects.create(scope="global", user=None, **values)
        LLMConfigScope.objects.create(config=instance, tenant=tenant)
    else:
        for key, value in values.items():
            setattr(instance, key, value)
        instance.save()
    if instance.model_type == "llm" and instance.is_active and "config" in values:
        from lens.vision_capabilities import resolve_model_capability

        refs = instance.config.get("fallback_model_uuids", [])
        if refs and resolve_model_capability(instance.uuid)["supports_vision"]:
            if any(not resolve_model_capability(ref)["supports_vision"] for ref in refs):
                raise serializers.ValidationError({"config": "Fallbacks for a vision model must support images."})
    if instance.is_default:
        if not instance.is_active:
            raise serializers.ValidationError({"is_default": "The default model must be active."})
        model_queryset(tenant).filter(model_type=instance.model_type).exclude(pk=instance.pk).update(is_default=False)
    model_audit(request, "llm_config.update" if serializer.partial else "llm_config.create", instance.uuid)
    return instance


class ModelListView(APIView):
    """List or create configurations in the current scope."""

    permission_classes = [HasModelAccess]

    def get(self, request):
        """Never return models from the dependency's unscoped collections."""

        query = model_queryset(model_tenant(request)).select_related("ownership").order_by("created_at", "pk")
        model_type = request.query_params.get("model_type", "llm")
        if model_type != "all":
            query = query.filter(model_type=model_type)
        return Response([config_dict(config) for config in query])

    @transaction.atomic
    def post(self, request):
        """Create a configuration with server-owned provenance."""

        return Response(config_dict(write_config(request)), status=201)


class ModelDetailView(APIView):
    """UUID and legacy integer URLs share the same authorization."""

    permission_classes = [HasModelAccess]

    def get(self, request, config_ref):
        """Read one local model with credentials masked."""

        return Response(config_dict(scoped_config(request, config_ref)))

    @transaction.atomic
    def put(self, request, config_ref):
        """Update without moving ownership or changing another scope's defaults."""

        return Response(config_dict(write_config(request, scoped_config(request, config_ref))))

    @transaction.atomic
    def delete(self, request, config_ref):
        """Delete only the authorized configuration."""

        config = scoped_config(request, config_ref)
        model_audit(request, "llm_config.delete", config.uuid)
        config.delete()
        return Response(status=204)


class ModelBulkView(APIView):
    """Reject an entire batch containing an unavailable model."""

    permission_classes = [HasModelAccess]

    @transaction.atomic
    def post(self, request):
        """Validate the bounded selection before applying any mutations."""

        refs = request.data.get("config_ids")
        action = request.data.get("action")
        try:
            if not isinstance(refs, list) or not 1 <= len(refs) <= 1000:
                raise ValueError
            refs = [UUID(str(ref)) for ref in refs]
            if len(set(refs)) != len(refs) or action not in {"enable", "disable", "delete"}:
                raise ValueError
        except (ValueError, TypeError):
            raise serializers.ValidationError("Choose unique model UUIDs and a valid action.") from None
        selected = model_queryset(model_tenant(request)).filter(uuid__in=refs)
        if len(list(selected.select_for_update())) != len(refs):
            raise serializers.ValidationError("Some model configurations are unavailable.")
        if action == "delete":
            selected.delete()
        else:
            values = {"is_active": action == "enable"}
            if action == "disable":
                values["is_default"] = False
            selected.update(**values)
        model_audit(request, f"llm_config.bulk_{action}")
        return Response({"count": len(refs)})


class ScopedTestMixin:
    """Pin test-call usage through streaming iteration as well as response creation."""

    permission_classes = [HasModelAccess]

    def post(self, request):
        """Let the dependency implement provider protocols after scope checks."""

        tenant = model_tenant(request)
        with model_call_scope(tenant):
            response = super().post(request)
        if getattr(response, "streaming", False):
            original = response.streaming_content

            def scoped_stream():
                """Do not keep a request ContextVar set while yielding to another request."""

                iterator = iter(original)
                try:
                    while True:
                        with model_call_scope(tenant):
                            try:
                                chunk = next(iterator)
                            except StopIteration:
                                return
                        yield chunk
                finally:
                    with model_call_scope(tenant):
                        if hasattr(iterator, "close"):
                            iterator.close()

            response.streaming_content = scoped_stream()
        return response


class ModelTestView(ScopedTestMixin, AdminLLMConfigTestView):
    """Resolve stored keys only inside the selected scope."""

    def get_stored_config(self, request, data):
        """Draft tests do not resolve a stored credential."""

        if not data.get("config_uuid") and data.get("config_id") is None:
            return None
        return scoped_config(request, data=data)


class ModelTestCallView(ScopedTestMixin, AdminLLMConfigTestCallView):
    """Authorize saved chat-model spending before provider I/O."""

    def can_test(self, request, data):
        """Both selectors must point to the same active local chat model."""

        config = scoped_config(request, data=data)
        return config.is_active and config.model_type == "llm"


class ModelDecisionTestView(ScopedTestMixin, AdminDecisionTestView):
    """Apply identical scope and spending checks to Decisions."""

    def can_test(self, request, config_uuid):
        """Never accept another scope's decision model UUID."""

        config = scoped_config(request, config_uuid)
        return config.is_active and config.model_type == "decision"


class ModelProvidersView(AdminLLMConfigProvidersView):
    """Share static provider definitions with authorized model administrators."""

    permission_classes = [HasModelAccess]


class ModelCatalogView(AdminLLMConfigModelsView):
    """Share static capability metadata without stored credentials."""

    permission_classes = [HasModelAccess]


class RetiredUserModelsView(APIView):
    """Old per-user model management cannot bypass tenant ownership."""

    permission_classes = [HasModelAccess]

    def get(self, request, **kwargs):
        """Models are managed within platform or tenant scope."""

        return Response({"detail": "Use model configurations in the current scope."}, status=410)

    post = put = patch = delete = get
