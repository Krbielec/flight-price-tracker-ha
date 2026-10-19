"""Bounded S3 reads and conditional enabled-state updates of one config object."""

import asyncio
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .contracts import decode_json, encode_json, validate_checks
from .settings import Settings

CHECKS_KEY = "config/checks.json"
MAX_CONFIG_BYTES = 1024 * 1024


class ChecksReadError(Exception):
    """Sanitized adapter failure, safe for HTTP and logs."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


class S3Checks:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Checks":
        client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
        return cls(client, settings.s3_bucket)

    async def read(self) -> dict:
        return await asyncio.to_thread(self._read)

    def _read(self) -> dict:
        return self._snapshot()[0]

    async def snapshot(self) -> tuple[dict, str]:
        document, etag = await asyncio.to_thread(self._snapshot)
        if not valid_etag(etag):
            raise ChecksReadError("invalid_config", "S3 configuration has no valid ETag.")
        return document, etag

    async def set_enabled(self, check_id: str, enabled: bool, expected_etag: str) -> tuple[dict, str]:
        if type(enabled) is not bool or not valid_etag(expected_etag):
            raise ChecksReadError("invalid_request", "A boolean enabled and a single strong ETag are required.", 400)
        return await asyncio.to_thread(self._set_enabled, check_id, enabled, expected_etag)

    def _set_enabled(self, check_id: str, enabled: bool, expected_etag: str) -> tuple[dict, str]:
        document, etag = self._snapshot()
        if not valid_etag(etag):
            raise ChecksReadError("invalid_config", "S3 configuration has no valid ETag.")
        if etag != expected_etag:
            raise ChecksReadError("conflict", "Configuration changed. Refresh before trying again.", 409)
        check = next((item for item in document["checks"] if item["id"] == check_id), None)
        if check is None:
            raise ChecksReadError("check_not_found", "Check was not found.", 404)
        check["enabled"] = enabled
        try:
            raw = encode_json(document).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            raise ChecksReadError("invalid_config", "Check configuration could not be serialized safely.") from None
        if len(raw) > MAX_CONFIG_BYTES:
            raise ChecksReadError("invalid_config", "Updated check configuration is too large.")
        try:
            result = self.client.put_object(
                Bucket=self.bucket, Key=CHECKS_KEY, Body=raw,
                ContentType="application/json", IfMatch=etag,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
                raise ChecksReadError("conflict", "Configuration changed. Refresh before trying again.", 409) from None
            if code in ("AccessDenied", "403"):
                raise ChecksReadError("s3_access_denied", "S3 denied updating check configuration.") from None
            raise ChecksReadError("s3_write_failed", "S3 update could not be confirmed. Refresh before trying again.", 503) from None
        except (BotoCoreError, OSError):
            raise ChecksReadError("s3_write_failed", "S3 update could not be confirmed. Refresh before trying again.", 503) from None
        new_etag = result.get("ETag")
        if not valid_etag(new_etag):
            raise ChecksReadError("s3_write_failed", "S3 update could not be confirmed. Refresh before trying again.", 503)
        return check, new_etag

    def _snapshot(self) -> tuple[dict, str | None]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=CHECKS_KEY)
            body = response["Body"]
            try:
                raw = body.read(MAX_CONFIG_BYTES + 1)
            finally:
                body.close()
            if len(raw) > MAX_CONFIG_BYTES:
                raise ChecksReadError("invalid_config", "Check configuration is too large.")
            # JSON non-finite values are not part of the data contract.
            document = decode_json(raw)
            return validate_checks(document), response.get("ETag")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("AccessDenied", "403"):
                raise ChecksReadError("s3_access_denied", "S3 denied access to check configuration.") from None
            if code in ("NoSuchKey", "NoSuchBucket", "404"):
                raise ChecksReadError("s3_not_found", "S3 check configuration was not found.") from None
            raise ChecksReadError("s3_unavailable", "S3 could not read check configuration.", 503) from None
        except (BotoCoreError, OSError):
            raise ChecksReadError("s3_unavailable", "S3 is currently unavailable.", 503) from None
        except (ValueError, UnicodeError, RecursionError):
            raise ChecksReadError("invalid_config", "S3 check configuration is invalid or unsupported.") from None

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)


def valid_etag(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r'"[A-Za-z0-9-]{1,128}"', value) is not None
