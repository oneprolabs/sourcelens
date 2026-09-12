"""Image attachment storage, normalization and binding for Lens Q&A.

Keeps the upload-side image handling (validation, EXIF stripping,
downscaling, message binding, data-URL encoding) self-contained, the way
``skill_generation`` and ``datasource.services`` isolate their concerns.
The run-flow vision preprocessing that consumes these images lives in
``services.analyze_multimodal_intent``.
"""

from django.db import transaction

from .assistant_lifecycle import lock_assistant_for_new_work
from .models import MessageAttachment
from .session_lifecycle import lock_active_session

ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
ATTACHMENT_MAX_PER_MESSAGE = 4
ATTACHMENT_MAX_DIMENSION = 1600
ATTACHMENT_MAX_PIXELS = 5_000_000
ATTACHMENT_MAX_ASPECT_RATIO = 3
_IMAGE_FORMAT_MAP = {
    "PNG": ("PNG", "image/png", "png"),
    "JPEG": ("JPEG", "image/jpeg", "jpg"),
    "MPO": ("JPEG", "image/jpeg", "jpg"),
    "WEBP": ("WEBP", "image/webp", "webp"),
    "GIF": ("PNG", "image/png", "png"),
}


class AttachmentError(ValueError):
    """Raised when an uploaded image attachment is rejected."""


def _downscale_image(image, max_dimension):
    """Shrink an image so its longest side fits max_dimension."""

    from PIL import Image as _Image

    width, height = image.size
    longest = max(width, height)
    if longest <= max_dimension:
        return image
    scale = max_dimension / float(longest)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, _Image.LANCZOS)


def store_message_attachment(session, user, uploaded_file):
    """Validate, normalize and persist one uploaded image attachment.

    The image is re-encoded (stripping EXIF and other metadata) and
    downscaled so the later vision call stays cheap. Returns a
    MessageAttachment bound to the session and uploader; the message link
    is set later when the run is created.
    """

    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, UnidentifiedImageError

    size = getattr(uploaded_file, "size", 0) or 0
    if size > ATTACHMENT_MAX_BYTES:
        raise AttachmentError("ATTACHMENT_TOO_LARGE")

    try:
        image = Image.open(uploaded_file)
    except Image.DecompressionBombError:
        raise AttachmentError("ATTACHMENT_DIMENSIONS_TOO_LARGE")
    except (UnidentifiedImageError, OSError):
        raise AttachmentError("ATTACHMENT_UNSUPPORTED_TYPE")

    out_format, mime, ext = _IMAGE_FORMAT_MAP.get(
        (image.format or "").upper(),
        (None, None, None),
    )
    if out_format is None:
        raise AttachmentError("ATTACHMENT_UNSUPPORTED_TYPE")

    width, height = image.size
    if width * height > ATTACHMENT_MAX_PIXELS:
        raise AttachmentError("ATTACHMENT_DIMENSIONS_TOO_LARGE")
    if max(width, height) > min(width, height) * ATTACHMENT_MAX_ASPECT_RATIO:
        raise AttachmentError("ATTACHMENT_ASPECT_UNSUPPORTED")

    try:
        image.load()
    except Image.DecompressionBombError:
        raise AttachmentError("ATTACHMENT_DIMENSIONS_TOO_LARGE")
    except OSError:
        raise AttachmentError("ATTACHMENT_UNSUPPORTED_TYPE")

    image = _downscale_image(image, ATTACHMENT_MAX_DIMENSION)
    save_kwargs = {"format": out_format}
    if out_format == "JPEG":
        image = image.convert("RGB")
        save_kwargs.update({"quality": 82, "optimize": True})
    elif image.mode == "P":
        image = image.convert("RGBA")
    width, height = image.size
    buffer = BytesIO()
    image.save(buffer, **save_kwargs)
    data = buffer.getvalue()

    with transaction.atomic():
        assistant = lock_assistant_for_new_work(session.assistant, user)
        session = lock_active_session(session)
        session.assistant = assistant
        attachment = MessageAttachment(
            session=session,
            uploaded_by=user,
            kind=MessageAttachment.Kind.IMAGE,
            original_name=(getattr(uploaded_file, "name", "") or "")[:255],
            mime_type=mime,
            byte_size=len(data),
            width=width,
            height=height,
        )
        attachment.file.save(f"image.{ext}", ContentFile(data), save=False)
        attachment.save()
    return attachment


def bind_attachments_to_message(
    session,
    message,
    attachment_uuids,
    order_by_uuid=None,
):
    """Link previously uploaded attachments to a question message.

    Only unbound attachments owned by the session are linked, in the
    given order. Unknown or already-bound uuids are skipped so a replay
    cannot steal another message's images.
    """

    if not attachment_uuids:
        return []
    order_by_uuid = order_by_uuid or {}
    wanted = [str(value) for value in attachment_uuids]
    by_uuid = {
        str(item.uuid): item
        for item in MessageAttachment.objects.filter(
            session=session,
            message__isnull=True,
            uuid__in=wanted,
        )
    }
    order = 0
    bound = []
    for raw in wanted:
        attachment = by_uuid.get(raw)
        if attachment is None:
            continue
        attachment.message = message
        attachment.order = order_by_uuid.get(raw, order)
        attachment.save(update_fields=["message", "order", "updated_at"])
        bound.append(raw)
        order += 1
    return bound


def get_session_image_attachments(session, attachment_uuids):
    """Return selected session images in the requested order."""

    wanted = [str(value) for value in attachment_uuids or []]
    by_uuid = {
        str(item.uuid): item
        for item in MessageAttachment.objects.filter(
            session=session,
            kind=MessageAttachment.Kind.IMAGE,
            uuid__in=wanted,
        )
    }
    return [by_uuid[value] for value in wanted if value in by_uuid]


def attachment_data_url(attachment):
    """Return a base64 data URL for an image attachment, or None."""

    import base64

    try:
        with attachment.file.open("rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    mime = attachment.mime_type or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"
