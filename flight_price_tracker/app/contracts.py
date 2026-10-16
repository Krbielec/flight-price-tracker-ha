"""Validate the version-one read contract without dropping compatible fields."""

from datetime import date
import re
from typing import Any


class ContractError(ValueError):
    """The stored document does not satisfy the supported read contract."""


def validate_checks(document: Any) -> dict:
    if not isinstance(document, dict):
        raise ContractError("Check configuration must be an object.")
    version = document.get("schemaVersion")
    if type(version) is not int or version != 1:
        raise ContractError("Unsupported or missing check schema version.")
    checks = document.get("checks")
    if not isinstance(checks, list):
        raise ContractError("Check configuration must contain a checks list.")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ContractError("Each check must be an object.")
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id.strip() or check_id in seen:
            raise ContractError("Check IDs must be non-empty unique strings.")
        seen.add(check_id)
        for key in ("origin", "destination", "currency"):
            value = check.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
                raise ContractError("Airport and currency codes must have three uppercase letters.")
        if check["origin"] == check["destination"]:
            raise ContractError("Origin and destination must differ.")
        dates = []
        for key in ("departureDateFrom", "departureDateTo"):
            value = check.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ContractError("Check dates must use YYYY-MM-DD.")
            try:
                dates.append(date.fromisoformat(value))
            except ValueError:
                raise ContractError("Check dates must be valid calendar dates.") from None
        if dates[0] > dates[1]:
            raise ContractError("Check date range is reversed.")
        # Historical/disabled checks remain readable, even after departure.
        if type(check.get("enabled")) is not bool:
            raise ContractError("Check enabled must be a boolean.")
    return document
