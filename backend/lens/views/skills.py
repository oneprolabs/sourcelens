"""Skill and MCP server management views."""

import json
import mimetypes
import shutil
from pathlib import Path, PurePosixPath

from django.db import transaction
from django.http import FileResponse
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from lens.environment_variables import validate_environment_schema
from lens.models import AssistantSkill, MCPServer, Skill
from lens.serializers import MCPServerSerializer, SkillSerializer
from lens.skill_generation import (
    SkillGeneratorNotConfigured,
    beautify_skill_content,
)
from lens.skill_packages import (
    SkillPackageError,
    check_skill_github_update,
    import_skill_from_github,
    import_skill_zip,
    package_zip_bytes,
    update_skill_from_github,
    update_skill_zip,
)
from lens.services import invalidate_skill_cache
from .base import BaseAdminViewSet


class SkillViewSet(BaseAdminViewSet):
    """CRUD for skills."""

    queryset = Skill.objects.all()
    serializer_class = SkillSerializer

    def destroy(self, request, *args, **kwargs):
        skill = self.get_object()
        if skill.assistantskill_set.exists():
            return Response(
                {"detail": "Skill is still bound to assistants."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Delete an unbound Skill and remove its package files."""

        package_path = instance.package_path
        skill_uuid = instance.uuid
        instance.delete()
        transaction.on_commit(
            lambda: self._cleanup_deleted_skill(package_path, skill_uuid)
        )

    @action(detail=True, methods=["get"], url_path="delete-impact")
    def delete_impact(self, request, *args, **kwargs):
        """Return assistants that currently bind this Skill."""

        skill = self.get_object()
        assistants = self._bound_assistants(skill)
        return Response(
            {
                "skill": {
                    "uuid": str(skill.uuid),
                    "name": skill.name,
                    "kind": skill.kind,
                },
                "bound_count": len(assistants),
                "bound_assistants": assistants,
            }
        )

    @action(detail=True, methods=["get"], url_path="file-preview")
    def file_preview(self, request, *args, **kwargs):
        """Return a bounded UTF-8 preview for a package text file."""

        skill = self.get_object()
        relative_path = str(request.query_params.get("path") or "").strip()
        try:
            path = PurePosixPath(relative_path)
        except (TypeError, ValueError):
            return Response(
                {"detail": "A valid package file path is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not relative_path or path.is_absolute() or ".." in path.parts:
            return Response(
                {"detail": "A valid package file path is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        package_root = Path(skill.package_path or "").resolve()
        file_path = (package_root / Path(*path.parts)).resolve()
        if package_root not in file_path.parents or not file_path.is_file():
            return Response(
                {"detail": "Package file was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if file_path.stat().st_size > 512 * 1024:
            return Response(
                {"detail": "This file is too large to preview."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        content_type = mimetypes.guess_type(file_path.name)[0] or ""
        text_types = {
            "application/json",
            "application/javascript",
            "application/xml",
            "application/yaml",
            "text/css",
            "text/csv",
            "text/html",
            "text/javascript",
            "text/markdown",
            "text/plain",
            "text/xml",
        }
        if content_type not in text_types and file_path.suffix.lower() not in {
            ".conf",
            ".env",
            ".ini",
            ".js",
            ".json",
            ".md",
            ".py",
            ".sh",
            ".sql",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".vue",
            ".yaml",
            ".yml",
        }:
            return Response(
                {"detail": "This file type is not supported for preview."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return Response(
                {"detail": "This file cannot be decoded for preview."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        return Response(
            {
                "path": relative_path,
                "content": content,
                "content_type": content_type or "text/plain",
            }
        )

    @action(detail=True, methods=["post"], url_path="force-delete")
    def force_delete(self, request, *args, **kwargs):
        """Delete a Skill after explicit name confirmation."""

        skill = self.get_object()
        confirmation = str(request.data.get("confirmation_name") or "")
        if confirmation != skill.name:
            return Response(
                {"detail": "Skill name confirmation does not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        package_path = skill.package_path
        skill_uuid = skill.uuid
        with transaction.atomic():
            AssistantSkill.objects.filter(skill=skill).delete()
            skill.delete()
            transaction.on_commit(
                lambda: self._cleanup_deleted_skill(
                    package_path,
                    skill_uuid,
                )
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(
        detail=True,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
        url_path="update-upload",
    )
    def update_upload(self, request, *args, **kwargs):
        """Replace an uploaded Skill package while preserving bindings."""

        skill = self.get_object()
        file_obj = request.FILES.get("file")
        if file_obj is None:
            return Response(
                {"detail": "Skill package file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            environment_override = self._environment_override(request)
            skill = update_skill_zip(
                skill,
                file_obj=file_obj,
                original_name=getattr(file_obj, "name", ""),
                environment_override=environment_override,
            )
        except SkillPackageError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(skill).data)

    @action(detail=True, methods=["post"], url_path="update-github")
    def update_github(self, request, *args, **kwargs):
        """Re-import a GitHub Skill while preserving bindings."""

        skill = self.get_object()
        url = str(request.data.get("url") or "").strip()
        if not url:
            return Response(
                {"detail": "GitHub URL is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            skill = update_skill_from_github(skill, url)
        except SkillPackageError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(skill).data)

    @action(detail=False, methods=["post"], url_path="check-updates")
    def check_updates(self, request):
        """Refresh GitHub tag metadata for all imported Skills."""

        skills = self.get_queryset().filter(source_type="github")
        for skill in skills:
            try:
                check_skill_github_update(skill)
            except SkillPackageError:
                continue
        return Response(
            self.get_serializer(self.get_queryset(), many=True).data
        )

    def _bound_assistants(self, skill):
        """Return compact assistant data for delete confirmation."""

        bindings = (
            AssistantSkill.objects.filter(skill=skill)
            .select_related("assistant", "assistant__lensnode")
            .order_by("assistant__name")
        )
        return [
            {
                "uuid": str(binding.assistant.uuid),
                "name": binding.assistant.name,
                "slug": binding.assistant.slug,
                "status": binding.assistant.status,
                "visibility": binding.assistant.visibility,
                "lensnode": binding.assistant.lensnode.name,
            }
            for binding in bindings
        ]

    def _remove_skill_package_path(self, package_path):
        """Remove package files for a deleted Skill."""

        if not package_path:
            return

        shutil.rmtree(package_path, ignore_errors=True)

    def _cleanup_deleted_skill(self, package_path, skill_uuid):
        """Remove control-plane files and notify LensNodes after commit."""

        self._remove_skill_package_path(package_path)
        invalidate_skill_cache(skill_uuid)

    @action(
        detail=False,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
        url_path="upload",
    )
    def upload(self, request):
        """Upload and validate a Skill zip package."""

        file_obj = request.FILES.get("file")
        if file_obj is None:
            return Response(
                {"detail": "Skill package file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            environment_override = self._environment_override(request)
            skill = import_skill_zip(
                file_obj=file_obj,
                original_name=getattr(file_obj, "name", ""),
                environment_override=environment_override,
            )
        except SkillPackageError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(skill).data)

    @staticmethod
    def _environment_override(request):
        """Parse an optional JSON environment declaration from multipart data."""

        if "environment" not in request.data:
            return None
        try:
            payload = json.loads(request.data.get("environment"))
            return validate_environment_schema(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SkillPackageError(
                "Environment variables must be valid JSON."
            ) from exc
        except ValidationError as exc:
            detail = exc.detail
            if isinstance(detail, list) and detail:
                raise SkillPackageError(str(detail[0])) from exc
            raise SkillPackageError(str(detail)) from exc

    @action(detail=False, methods=["post"], url_path="import-github")
    def import_github(self, request):
        """Import a public Skill zip package from GitHub."""

        url = str(request.data.get("url") or "").strip()
        if not url:
            return Response(
                {"detail": "GitHub URL is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            skill = import_skill_from_github(url)
        except SkillPackageError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(skill, many=True).data)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, *args, **kwargs):
        """Download the current Skill package as a zip archive."""

        skill = self.get_object()
        archive = package_zip_bytes(skill)
        return FileResponse(
            archive,
            as_attachment=True,
            filename=f"{skill.uuid}.zip",
        )

    @action(detail=False, methods=["post"])
    def beautify(self, request):
        """Polish a draft SKILL.md via the configured generator model."""

        try:
            content = beautify_skill_content(
                content=request.data.get("content", ""),
                name=request.data.get("name", ""),
                user_id=request.user.id,
            )
        except SkillGeneratorNotConfigured:
            return Response(
                {"detail": "Skill generator model is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"content": content})


class MCPConfigJSONParser(CamelCaseJSONParser):
    """Preserve external MCP configuration keys, including HTTP headers."""

    json_underscoreize = {
        **CamelCaseJSONParser.json_underscoreize,
        "ignore_fields": (
            *(CamelCaseJSONParser.json_underscoreize.get("ignore_fields") or ()),
            "config",
        ),
    }


class MCPServerViewSet(BaseAdminViewSet):
    """CRUD for MCP servers."""

    queryset = MCPServer.objects.all()
    serializer_class = MCPServerSerializer
    parser_classes = [MCPConfigJSONParser]

    def destroy(self, request, *args, **kwargs):
        mcp = self.get_object()
        if mcp.assistantmcp_set.exists():
            return Response(
                {"detail": "MCP server is still bound to assistants."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)
