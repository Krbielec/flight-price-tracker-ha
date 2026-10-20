"""Queue one initial collection server-side after a successful check creation."""

import asyncio
import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class CollectionError(Exception):
    def __init__(self, code):
        super().__init__("Initial collection could not be confirmed.")
        self.code = code


class LambdaCollector:
    def __init__(self, client, function_name):
        self.client = client
        self.function_name = function_name

    @classmethod
    def from_settings(cls, settings):
        client = boto3.client("lambda", region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=Config(connect_timeout=5, read_timeout=10,
                          retries={"mode": "standard", "total_max_attempts": 1}))
        return cls(client, settings.collector_function_name)

    async def enqueue(self, check_id):
        await asyncio.to_thread(self._enqueue, check_id)

    def _enqueue(self, check_id):
        try:
            reply = self.client.invoke(FunctionName=self.function_name, InvocationType="Event",
                Payload=json.dumps({"checkId": check_id}, separators=(",", ":")).encode())
            body = reply.get("Payload")
            if body is not None:
                body.close()
            if reply.get("StatusCode") != 202:
                raise CollectionError("lambda_unconfirmed")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            safe_code = {"AccessDeniedException": "lambda_access_denied",
                         "ResourceNotFoundException": "lambda_not_found",
                         "TooManyRequestsException": "lambda_throttled"}.get(code, "lambda_unconfirmed")
            raise CollectionError(safe_code) from None
        except (BotoCoreError, OSError):
            raise CollectionError("lambda_unconfirmed") from None

    async def close(self):
        await asyncio.to_thread(self.client.close)
