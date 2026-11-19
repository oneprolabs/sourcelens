"""Conversion authorization survives command handling and never enters file metadata."""

import base64
import io
import json
import random

import httpx
import pytest

from lensnode.datasource_sync import convert_managed_workspace, upload_managed_workspace


@pytest.mark.parametrize("upload", [False, True])
def test_image_conversion_forwards_task_grant_and_writes_real_sidecar(tmp_path, upload):
    """Exercise conversion through the real gateway HTTP client and image output."""

    image_module = pytest.importorskip("PIL.Image")
    image = image_module.frombytes("RGB", (128, 128), random.Random(4).randbytes(128 * 128 * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    requests = []

    def gateway(request):
        """Return deterministic provider output using actual gateway wire format."""

        requests.append(json.loads(request.read()))
        return httpx.Response(200, json={"message": {"content": "A patterned image."}, "usage": {"total_tokens": 7}})

    target = tmp_path / "source"
    target.mkdir()
    with httpx.Client(transport=httpx.MockTransport(gateway)) as client:
        command = {
            "datasource_uuid": "conversion-source",
            "task_id": "conversion-task",
            "name": "Conversion source",
            "source_type": "upload" if upload else "managed_workspace",
            "target_path": str(target),
            "conversion": {"image": True, "vision_model_ref": "tenant-vision-model"},
            "conversion_token": "signed-task-grant",
            "ai_gateway_url": "http://gateway.test/ai-gateway/",
            "lensnode_token": "node-token",
            "gateway_http_client": client,
        }
        if upload:
            command.update(filename="photo.png", content_base64=base64.b64encode(buffer.getvalue()).decode())
            result = upload_managed_workspace(command, workspace_path=tmp_path)
        else:
            (target / "photo.png").write_bytes(buffer.getvalue())
            result = convert_managed_workspace(command, workspace_path=tmp_path)

    assert result["status"] == "success"
    assert len(requests) == 1
    assert requests[0]["conversion_token"] == "signed-task-grant"
    assert requests[0]["model_ref"] == "tenant-vision-model"
    assert "run_uuid" not in requests[0]
    sidecar = next(tmp_path.rglob("content.md"))
    assert "A patterned image." in sidecar.read_text()
    for path in tmp_path.rglob("*.json"):
        assert "signed-task-grant" not in path.read_text()
        assert "node-token" not in path.read_text()
