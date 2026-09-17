"""Cloudflare R2 through boto3. `client()` is the boundary tests replace."""

from functools import cache

import boto3

from app.errors import ApiError
from app.settings import settings

PRESIGN_SECONDS = 900
IMAGE_MAGIC = {
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"RIFF": "webp",  # RIFF....WEBP; close enough for a magic check
}


@cache
def client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def put(key: str, data: bytes, content_type: str) -> None:
    client().put_object(Bucket=settings.r2_bucket, Key=key, Body=data, ContentType=content_type)


def delete(key: str) -> None:
    client().delete_object(Bucket=settings.r2_bucket, Key=key)


def presigned_get(key: str) -> str:
    return client().generate_presigned_url(
        "get_object", Params={"Bucket": settings.r2_bucket, "Key": key}, ExpiresIn=PRESIGN_SECONDS
    )


class TooLarge(ApiError):
    status = 413
    code = "too_large"


class InvalidFile(ApiError):
    status = 422
    code = "validation_error"


def check_image(data: bytes, max_bytes: int) -> str:
    """Returns the file extension for the detected image type."""
    if len(data) > max_bytes:
        raise TooLarge(f"file exceeds {max_bytes} bytes")
    for magic, ext in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext
    raise InvalidFile("file is not a PNG, JPEG, or WebP image")


AUDIO_MAX_BYTES = 4096
AUDIO_RATE = 16000


def check_opus(data: bytes) -> None:
    """Ogg Opus, ≤ 4096 bytes, 16 kHz input rate (what the base firmware plays)."""
    if len(data) > AUDIO_MAX_BYTES:
        raise TooLarge(f"audio exceeds {AUDIO_MAX_BYTES} bytes")
    # Ogg page header is 27 bytes + segment table; the first page holds only OpusHead.
    if data[:4] != b"OggS" or data[28:36] != b"OpusHead":
        raise InvalidFile("file is not Ogg Opus")
    rate = int.from_bytes(data[40:44], "little")
    if rate != AUDIO_RATE:
        raise InvalidFile(f"audio must be {AUDIO_RATE} Hz, got {rate}")
