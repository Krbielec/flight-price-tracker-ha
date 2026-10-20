"""Load Supervisor options without disclosing their contents in errors."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re


class SettingsError(ValueError):
    """Safe-to-display configuration error."""


@dataclass(frozen=True)
class Settings:
    aws_region: str
    s3_bucket: str
    aws_access_key_id: str = field(repr=False)
    aws_secret_access_key: str = field(repr=False)
    collector_function_name: str = "flight-price-tracker-collector"

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            options = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise SettingsError("Cannot read valid add-on options.") from None
        if not isinstance(options, dict):
            raise SettingsError("Add-on options must be an object.")
        for key in ("aws_region", "s3_bucket", "aws_access_key_id", "aws_secret_access_key"):
            if not isinstance(options.get(key), str) or not options[key].strip():
                raise SettingsError(f"Required option is missing or invalid: {key}.")
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", options["aws_region"]):
            raise SettingsError("Invalid AWS region format.")
        bucket = options["s3_bucket"]
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or ".." in bucket
            or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket)
        ):
            raise SettingsError("Invalid S3 bucket name.")
        function = options.get("collector_function_name", "flight-price-tracker-collector")
        if not isinstance(function, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", function):
            raise SettingsError("Invalid collector function name.")
        return cls(**{key: options[key] for key in ("aws_region", "s3_bucket", "aws_access_key_id", "aws_secret_access_key")},
                   collector_function_name=function)
