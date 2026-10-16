"""Bounded, read-only S3 adapter. No writes or secondary configuration store."""

import asyncio
import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .contracts import validate_checks
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
            document = json.loads(raw, parse_constant=_reject_constant)
            return validate_checks(document)
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


def _reject_constant(_value: str):
    raise ValueError("Non-finite JSON number")
