"""Datasource credential CRUD and connectivity validation."""

import json
import logging
from urllib import error as urlerror
from urllib import parse, request

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.access import get_user_tenant
from accounts.permissions import HasTenantOrLegacyAdminAccess
from lens.models import DataSourceCredential
from lens.serializers import DataSourceCredentialSerializer
from .base import BaseAuthenticatedViewSet


LOGGER = logging.getLogger(__name__)


class DataSourceCredentialViewSet(BaseAuthenticatedViewSet):
    """CRUD for tenant-scoped datasource credentials.

    Staff and tenant administrators may manage credentials; members cannot.
    Secrets are never returned by list/retrieve; ``reveal`` is the only
    plaintext path and is audited.
    """

    permission_classes = [HasTenantOrLegacyAdminAccess]
    queryset = DataSourceCredential.objects.all().prefetch_related(
        "datasources"
    )
    serializer_class = DataSourceCredentialSerializer

    def get_queryset(self):
        """Scope credentials to the tenant and optional filters."""

        queryset = super().get_queryset().filter(tenant=get_user_tenant(self.request.user))
        provider = self.request.query_params.get("provider")
        auth_type = self.request.query_params.get("auth_type")
        if provider:
            queryset = queryset.filter(provider=provider)
        if auth_type:
            queryset = queryset.filter(auth_type=auth_type)
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Reject deleting credentials that are still referenced."""

        credential = self.get_object()
        if credential.datasources.exists():
            return Response(
                {"detail": "CREDENTIAL_IN_USE"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="reveal")
    def reveal(self, request, uuid=None):
        """Return decrypted credential values for the current edit session."""

        credential = self.get_object()
        secret = credential.get_secret()
        if credential.auth_type == DataSourceCredential.AuthType.FEISHU_APP:
            app_id, _, app_secret = secret.partition(":")
            payload = {
                "app_id": app_id,
                "app_secret": app_secret,
            }
        else:
            payload = {"secret": secret}
        LOGGER.info(
            "credential_reveal actor_id=%s credential_uuid=%s source_ip=%s "
            "request_id=%s result=success",
            request.user.pk,
            credential.uuid,
            request.META.get("REMOTE_ADDR", ""),
            request.headers.get("X-Request-ID", ""),
        )
        response = Response(payload)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response

    @action(detail=True, methods=["post"], url_path="validate")
    def validate_credential(self, request, uuid=None):
        """Validate a stored datasource credential endpoint and scope."""

        credential = self.get_object()
        result = _validate_datasource_credential_connectivity(credential)
        credential.validation_status = result["status"]
        credential.validation_message = result.get("message", "")
        credential.validated_at = timezone.now()
        if credential.provider == DataSourceCredential.Provider.FEISHU:
            credential.endpoint_url = "https://open.feishu.cn"
        credential.save(
            update_fields=[
                "endpoint_url",
                "validation_status",
                "validation_message",
                "validated_at",
                "updated_at",
            ]
        )
        response_status = (
            status.HTTP_200_OK
            if result.get("status") == "success"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(result, status=response_status)


def _validate_datasource_credential_connectivity(credential):
    """Validate datasource credential connectivity from the backend."""

    if credential.auth_type == DataSourceCredential.AuthType.FEISHU_APP:
        return _validate_feishu_credential_connectivity(credential)
    if credential.provider == DataSourceCredential.Provider.GITHUB:
        return _validate_github_credential_connectivity(credential)
    if credential.provider == DataSourceCredential.Provider.GITLAB:
        return _validate_gitlab_credential_connectivity(credential)
    return {
        "status": "failed",
        "message_code": "credential_provider_unsupported",
        "message": "Credential provider is not supported for validation.",
    }


def _validate_github_credential_connectivity(credential):
    endpoint = (credential.endpoint_url or "https://github.com").rstrip("/")
    api_base = "https://api.github.com"
    if endpoint and endpoint != "https://github.com":
        api_base = f"{endpoint}/api/v3"
    headers = {}
    is_anonymous = (
        credential.auth_type == DataSourceCredential.AuthType.NONE
    )
    if not is_anonymous:
        headers = {"Authorization": f"Bearer {credential.get_secret()}"}
        api_url = f"{api_base}/user"
        payload, message = _credential_api_json(
            api_url,
            headers,
        )
        if payload is None:
            return {
                "status": "failed",
                "message_code": "github_credential_invalid",
                "message": message or "GitHub credential validation failed.",
            }
    scope_url = (credential.scope_config or {}).get("organization_url")
    if scope_url:
        scope_path = _credential_scope_path(scope_url)
        parts = [part for part in scope_path.split("/") if part]
        scope_api_url = ""
        if len(parts) >= 2:
            scope_api_url = f"{api_base}/repos/{parts[0]}/{parts[1]}"
        elif len(parts) == 1:
            scope_api_url = f"{api_base}/orgs/{parts[0]}/repos?per_page=1"
        if not scope_api_url:
            return {
                "status": "failed",
                "message_code": "github_scope_invalid",
                "message": "GitHub scope URL is invalid.",
            }
        scope_payload, scope_message = _credential_api_json(
            scope_api_url,
            headers,
        )
        if scope_api_url and scope_payload is None and len(parts) == 1:
            scope_payload, scope_message = _credential_api_json(
                f"{api_base}/users/{parts[0]}/repos?per_page=1",
                headers,
            )
        if scope_api_url and scope_payload is None:
            return {
                "status": "failed",
                "message_code": "github_scope_invalid",
                "message": scope_message or "GitHub scope validation failed.",
            }
    return {
        "status": "success",
        "message_code": "github_credential_valid",
        "message": "GitHub credential is valid.",
        "details": {
            "login": "" if is_anonymous else payload.get("login") or ""
        },
    }


def _validate_gitlab_credential_connectivity(credential):
    endpoint = (credential.endpoint_url or "https://gitlab.com").rstrip("/")
    headers = {}
    is_anonymous = (
        credential.auth_type == DataSourceCredential.AuthType.NONE
    )
    if not is_anonymous:
        headers = {"PRIVATE-TOKEN": credential.get_secret()}
        payload, message = _credential_api_json(
            f"{endpoint}/api/v4/user",
            headers,
        )
        if payload is None:
            return {
                "status": "failed",
                "message_code": "gitlab_credential_invalid",
                "message": message or "GitLab credential validation failed.",
            }
    scope_url = (credential.scope_config or {}).get("organization_url")
    if scope_url:
        scope_path = parse.quote(_credential_scope_path(scope_url), safe="")
        scope_payload, scope_message = _credential_api_json(
            (
                f"{endpoint}/api/v4/groups/{scope_path}/projects"
                "?include_subgroups=true&simple=true&per_page=1"
            ),
            headers,
        )
        if scope_payload is None:
            scope_payload, scope_message = _credential_api_json(
                f"{endpoint}/api/v4/projects/{scope_path}",
                headers,
            )
        if scope_payload is None:
            return {
                "status": "failed",
                "message_code": "gitlab_scope_invalid",
                "message": scope_message or "GitLab scope validation failed.",
            }
    return {
        "status": "success",
        "message_code": "gitlab_credential_valid",
        "message": "GitLab credential is valid.",
        "details": {
            "username": "" if is_anonymous else payload.get("username") or ""
        },
    }


def _validate_feishu_credential_connectivity(credential):
    app_id, _, app_secret = credential.get_secret().partition(":")
    endpoint = "https://open.feishu.cn"
    payload, message = _credential_api_json(
        f"{endpoint}/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json"},
        data=json.dumps(
            {"app_id": app_id, "app_secret": app_secret}
        ).encode("utf-8"),
    )
    token = (payload or {}).get("tenant_access_token")
    if not token:
        return {
            "status": "failed",
            "message_code": "feishu_credential_invalid",
            "message": message or "Feishu app credential validation failed.",
        }
    scope_config = credential.scope_config or {}
    folder_token = scope_config.get("folder_token") or _feishu_folder_token(
        scope_config.get("folder_url")
    )
    if folder_token:
        query = parse.urlencode(
            {
                "folder_token": folder_token,
                "page_size": "1",
            }
        )
        folder_payload, folder_message = _credential_api_json(
            f"{endpoint}/open-apis/drive/v1/files?{query}",
            {"Authorization": f"Bearer {token}"},
        )
        if folder_payload is None:
            return {
                "status": "failed",
                "message_code": "feishu_folder_invalid",
                "message": (
                    folder_message
                    or "Feishu folder validation failed."
                ),
            }
    return {
        "status": "success",
        "message_code": "feishu_credential_valid",
        "message": "Feishu credential is valid.",
    }


def _credential_api_json(url, headers, data=None, timeout=15):
    req = request.Request(url, headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except urlerror.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:500]
        except Exception:
            body = ""
        return None, f"HTTP {exc.code}: {body}"
    except urlerror.URLError as exc:
        return None, str(exc.reason)
    except (TimeoutError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _feishu_folder_token(value):
    parsed = parse.urlsplit(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if "folder" in parts:
        index = parts.index("folder")
        if index + 1 < len(parts):
            return parts[index + 1]
    return str(value or "").strip()


def _credential_scope_path(value):
    parsed = parse.urlsplit(str(value or "").strip())
    path = parsed.path if parsed.scheme or parsed.netloc else str(value or "")
    return path.strip("/").removesuffix(".git")
