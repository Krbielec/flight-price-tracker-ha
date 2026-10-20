"""Validate the version-one read contract without dropping compatible fields."""

from datetime import date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

import simplejson as json


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


def decode_json(raw):
    """Reject ambiguous duplicate keys and non-finite numbers before any write."""
    try:
        return json.loads(raw, object_pairs_hook=_unique_keys, use_decimal=True,
                          parse_constant=_reject_constant)
    except ArithmeticError:
        raise ValueError("Unsupported JSON number") from None


def _unique_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Duplicate JSON key")
        document[key] = value
    return document


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


def encode_json(document):
    """Preserve numeric precision in compatible fields while merging enabled."""
    return json.dumps(document, use_decimal=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":"))


MAX_NEW_CHECK_DAYS_AHEAD = 730
MAX_NEW_CHECK_RANGE_DAYS = 366
NEW_CHECK_FIELDS = {
    "id", "origin", "destination", "departureDateFrom", "departureDateTo", "currency", "enabled",
}
EDIT_CHECK_FIELDS = {"departureDateFrom", "departureDateTo", "enabled"}


def current_date() -> date:
    return datetime.now(ZoneInfo("Europe/Warsaw")).date()


def validate_check_edit(payload: Any, existing: dict | None = None, *, today: date | None = None) -> dict:
    """Only dates and enabled may change; preserve historical endpoints if unchanged."""
    if not isinstance(payload, dict) or set(payload) != EDIT_CHECK_FIELDS:
        raise ContractError("Expected only departureDateFrom, departureDateTo and enabled.")
    # Reuse calendar/date/boolean validation without imposing new-ID syntax on old checks.
    candidate = {"id": "edit", "origin": "AAA", "destination": "BBB", "currency": "PLN", **payload}
    validate_checks({"schemaVersion": 1, "checks": [candidate]})
    if existing is not None:
        today = today if today is not None else current_date()
        changed = [key for key in ("departureDateFrom", "departureDateTo") if payload[key] != existing[key]]
        for key in changed:
            value = date.fromisoformat(payload[key])
            if value < today or value > today + timedelta(days=MAX_NEW_CHECK_DAYS_AHEAD):
                raise ContractError("Changed dates must be between today and 730 days ahead (Europe/Warsaw).")
        start, end = (date.fromisoformat(payload[key]) for key in ("departureDateFrom", "departureDateTo"))
        if changed and (end - start).days + 1 > MAX_NEW_CHECK_RANGE_DAYS:
            raise ContractError("An edited date range may cover at most 366 departure dates, inclusive.")
    return dict(payload)


def validate_new_check(payload: Any, *, today: date | None = None) -> dict:
    """Stricter input rules for new checks; historical read rules stay unchanged."""
    if not isinstance(payload, dict) or set(payload) - NEW_CHECK_FIELDS:
        raise ContractError("Expected a check object containing only supported fields.")
    check = {**payload}
    check.setdefault("enabled", True)
    validate_checks({"schemaVersion": 1, "checks": [check]})
    if len(check["id"]) > 128 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", check["id"]):
        raise ContractError("ID must be a lowercase slug of at most 128 letters, digits and hyphens.")
    today = today if today is not None else current_date()
    start = date.fromisoformat(check["departureDateFrom"])
    end = date.fromisoformat(check["departureDateTo"])
    if start < today or end > today + timedelta(days=MAX_NEW_CHECK_DAYS_AHEAD):
        raise ContractError("New check dates must be between today and 730 days ahead (Europe/Warsaw).")
    if (end - start).days + 1 > MAX_NEW_CHECK_RANGE_DAYS:
        raise ContractError("A new check may cover at most 366 departure dates, inclusive.")
    return check
