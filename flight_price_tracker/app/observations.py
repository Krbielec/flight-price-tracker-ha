"""Bounded, read-only pagination over immutable S3 observation snapshots."""

import asyncio
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import math
import re
import secrets
from threading import Lock
import time
from zoneinfo import ZoneInfo

from botocore.exceptions import BotoCoreError, ClientError

from .contracts import decode_json
from .s3_checks import ChecksReadError

MAX_OBJECT_BYTES = 1024 * 1024
MAX_FARES = 1000
MAX_RUNS_PER_PAGE = 10
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_OBJECTS = 64
PAGE_TIMEOUT = 25
WORK_BUDGET = 20
CHECK_FIELDS = ("id", "origin", "destination", "departureDateFrom", "departureDateTo", "currency", "enabled")


def page_parameters(check_id, page_size=200, cursor=None, checked_from=None, checked_to=None):
    if not isinstance(check_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", check_id):
        raise ChecksReadError("invalid_parameters", "Invalid observation check ID.", 400)
    if type(page_size) is not int or not 1 <= page_size <= 500:
        raise ChecksReadError("invalid_parameters", "page_size must be an integer between 1 and 500.", 400)
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096):
        raise ChecksReadError("invalid_cursor", "Invalid or expired observation cursor.", 400)
    try:
        start = _iso_date(checked_from) if checked_from is not None else None
        end = _iso_date(checked_to) if checked_to is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError
    except (ValueError, TypeError):
        raise ChecksReadError("invalid_parameters", "Invalid inclusive checked-date range.", 400) from None


def _iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError
    return date.fromisoformat(value)


def _flight_time(value, missing_allowed):
    if value is None and missing_allowed:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError
    return parsed


def normalize_observation(document, check_id):
    """Keep airport wall-clock timestamps naive; never invent UTC flight times."""
    try:
        if not isinstance(document, dict) or type(document.get("schemaVersion")) is not int or document["schemaVersion"] != 1:
            raise ValueError
        if document.get("checkId") != check_id:
            raise ValueError
        for field in ("origin", "destination"):
            if not isinstance(document.get(field), str) or not re.fullmatch(r"[A-Z]{3}", document[field]):
                raise ValueError
        if document["origin"] == document["destination"]:
            raise ValueError
        departure_from = _iso_date(document.get("departureDateFrom"))
        departure_to = _iso_date(document.get("departureDateTo"))
        if departure_from > departure_to:
            raise ValueError
        raw_checked = document.get("checkedAt")
        if not isinstance(raw_checked, str) or "T" not in raw_checked:
            raise ValueError
        checked = datetime.fromisoformat(raw_checked)
        if checked.tzinfo is None or checked.utcoffset() != timedelta(0):
            raise ValueError
        prices = document.get("prices")
        if not isinstance(prices, list) or len(prices) > MAX_FARES:
            raise ValueError
        seen, result = set(), []
        for fare in prices:
            if not isinstance(fare, dict) or type(fare.get("sold_out")) is not bool or type(fare.get("unavailable")) is not bool:
                raise ValueError
            departure = _iso_date(fare.get("departure_date"))
            if departure in seen or not departure_from <= departure <= departure_to:
                raise ValueError
            seen.add(departure)
            missing = fare["sold_out"] or fare["unavailable"]
            # All v1 keys must be present, including nullable unavailable values.
            if not {"departure_at", "arrival_at", "price"} <= fare.keys():
                raise ValueError
            departure_time = _flight_time(fare["departure_at"], missing)
            _flight_time(fare["arrival_at"], missing)
            if departure_time is not None and departure_time.date() != departure:
                raise ValueError
            currency = fare.get("currency")
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError
            amount = fare["price"]
            if amount is not None and (type(amount) not in (int, float, Decimal) or not math.isfinite(float(amount)) or amount < 0):
                raise ValueError
            if amount is None and not missing:
                raise ValueError
            result.append({
                "checkedAt": checked.astimezone(UTC).isoformat(),
                "checkedDate": checked.astimezone(ZoneInfo("Europe/Warsaw")).date().isoformat(),
                "departureDate": departure.isoformat(),
                "departureAt": fare["departure_at"], "arrivalAt": fare["arrival_at"],
                "price": None if missing else float(amount), "currency": currency,
                "soldOut": fare["sold_out"], "unavailable": fare["unavailable"],
            })
        return sorted(result, key=lambda row: row["departureDate"])
    except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError):
        raise ChecksReadError("invalid_observation", "S3 observation is invalid or uses an unsupported schema.") from None


class S3Observations:
    def __init__(self, checks):
        self._checks = checks
        self._client = checks.client
        self._bucket = checks.bucket
        self._secret = secrets.token_bytes(32)
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="flight-observations")
        self._pending = set()
        self._closed = False

    async def page(self, check_id, page_size=200, cursor=None, checked_from=None, checked_to=None):
        page_parameters(check_id, page_size, cursor, checked_from, checked_to)
        position = self._decode_cursor(cursor, check_id, page_size, checked_from, checked_to) if cursor else ("", 0)
        if self._closed or len(self._pending) >= 2:
            raise ChecksReadError("reader_busy", "Observation reader is busy. Try again later.", 503)
        future = asyncio.get_running_loop().run_in_executor(self._pool, self._page, check_id, page_size, position, checked_from, checked_to)
        self._pending.add(future)
        future.add_done_callback(self._finished)
        try:
            return await asyncio.wait_for(asyncio.shield(future), PAGE_TIMEOUT)
        except TimeoutError:
            # A running SDK call cannot be forcibly canceled. Keep it in the
            # bounded pending set until completion rather than queueing more work.
            raise ChecksReadError("observations_timeout", "Observation read timed out. Try again later.", 503) from None

    def _finished(self, future):
        self._pending.discard(future)
        if not future.cancelled():
            future.exception()  # consume failures after the HTTP/service caller timed out

    def _page(self, check_id, page_size, position, checked_from, checked_to):
        deadline = time.monotonic() + WORK_BUDGET
        try:
            config = self._checks._read()
            if not any(check["id"] == check_id for check in config["checks"]):
                raise ChecksReadError("check_not_found", "Check was not found.", 404)
            prefix = f"observations/check_id={check_id}/"
            after, offset = position
            if not after and checked_from:
                start = _iso_date(checked_from)
                lower = start - timedelta(days=1) if start > date.min else start
                after = f"{prefix}checked_date={lower.isoformat()}/"
            upper = None
            if checked_to and _iso_date(checked_to) < date.max:
                upper = f"{prefix}checked_date={(_iso_date(checked_to) + timedelta(days=1)).isoformat()}/"
            def filtered(rows):
                return [row for row in rows if (not checked_from or row["checkedDate"] >= checked_from)
                        and (not checked_to or row["checkedDate"] <= checked_to)]
            def respond(rows, key, offset, more):
                return self._response(check_id, rows, key, offset, page_size, more, checked_from, checked_to)
            result, scanned = [], 0
            if offset:
                rows = filtered(self._rows(after, check_id, deadline))
                if offset >= len(rows):
                    raise ChecksReadError("invalid_cursor", "Invalid or expired observation cursor.", 400)
                taken = min(page_size, len(rows) - offset)
                result.extend(rows[offset:offset + taken])
                scanned += 1
                if len(result) == page_size:
                    next_offset = offset + taken if offset + taken < len(rows) else 0
                    return respond(result, after, next_offset, True)
            self._budget(deadline)
            params = {"Bucket": self._bucket, "Prefix": prefix, "MaxKeys": MAX_RUNS_PER_PAGE - scanned}
            if after:
                params["StartAfter"] = after
            listing = self._client.list_objects_v2(**params)
            items = listing.get("Contents", [])
            truncated = listing.get("IsTruncated", False)
            if not isinstance(items, list) or type(truncated) is not bool or len(items) > params["MaxKeys"]:
                raise ValueError
            keys = [item["Key"] for item in items]
            if keys != sorted(set(keys)) or (truncated and not keys):
                raise ValueError
            for index, key in enumerate(keys):
                self._key(key, check_id)
                if after and key <= after:
                    raise ValueError
                if upper is not None and key >= upper:
                    return respond(result, after, 0, False)
                rows = filtered(self._rows(key, check_id, deadline))
                taken = min(page_size - len(result), len(rows))
                result.extend(rows[:taken])
                if len(result) == page_size:
                    partial = taken < len(rows)
                    more = partial or index < len(keys) - 1 or truncated
                    return respond(result, key, taken if partial else 0, more)
                after = key
            return respond(result, after, 0, truncated)
        except ChecksReadError:
            raise
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("AccessDenied", "403"):
                raise ChecksReadError("observations_access_denied", "S3 denied reading observations.") from None
            if code in ("NoSuchKey", "NoSuchBucket", "404"):
                raise ChecksReadError("observation_missing", "A listed S3 observation could not be read.") from None
            raise ChecksReadError("observations_unavailable", "S3 observations are currently unavailable.", 503) from None
        except (BotoCoreError, OSError):
            raise ChecksReadError("observations_unavailable", "S3 observations are currently unavailable.", 503) from None
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ChecksReadError("invalid_observation", "S3 observation data or listing is invalid.") from None

    def _rows(self, key, check_id, deadline):
        self._budget(deadline)
        self._key(key, check_id)
        with self._cache_lock:
            raw = self._cache.get(key)
            if raw is not None:
                self._cache.move_to_end(key)
        if raw is None:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            try:
                raw = body.read(MAX_OBJECT_BYTES + 1)
            finally:
                body.close()
            if len(raw) > MAX_OBJECT_BYTES:
                raise ChecksReadError("invalid_observation", "S3 observation exceeds the supported size.")
            rows = normalize_observation(decode_json(raw), check_id)
            with self._cache_lock:
                old = self._cache.pop(key, b"")
                self._cache_bytes -= len(old)
                self._cache[key] = raw
                self._cache_bytes += len(raw)
                while self._cache_bytes > MAX_CACHE_BYTES or len(self._cache) > MAX_CACHE_OBJECTS:
                    _, evicted = self._cache.popitem(last=False)
                    self._cache_bytes -= len(evicted)
            return rows
        return normalize_observation(decode_json(raw), check_id)

    @staticmethod
    def _budget(deadline):
        if time.monotonic() >= deadline:
            raise ChecksReadError("observations_timeout", "Observation read budget exceeded. Try again later.", 503)

    @staticmethod
    def _key(key, check_id):
        prefix = f"observations/check_id={check_id}/checked_date="
        if not isinstance(key, str) or len(key) > 1024 or not key.startswith(prefix):
            raise ValueError
        suffix = key[len(prefix):]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}/[^/]+\.json", suffix):
            raise ValueError
        _iso_date(suffix[:10])

    def _response(self, check_id, rows, after, offset, page_size, more, checked_from=None, checked_to=None):
        cursor = None
        if more:
            state = {"v": 1, "check": check_id, "size": page_size, "after": after, "offset": offset, "from": checked_from, "to": checked_to}
            raw = json.dumps(state, separators=(",", ":")).encode()
            signature = hmac.digest(self._secret, raw, hashlib.sha256)
            cursor = base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode()
        return {"checkId": check_id, "observations": rows, "nextCursor": cursor}

    def _decode_cursor(self, cursor, check_id, page_size, checked_from, checked_to):
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(hmac.digest(self._secret, payload, hashlib.sha256), signature):
                raise ValueError
            state = json.loads(payload)
            if state.get("v") != 1 or state.get("check") != check_id or state.get("size") != page_size:
                raise ValueError
            if state.get("from") != checked_from or state.get("to") != checked_to:
                raise ValueError
            after, offset = state["after"], state["offset"]
            self._key(after, check_id)
            if type(offset) is not int or not 0 <= offset < MAX_FARES:
                raise ValueError
            return after, offset
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ChecksReadError("invalid_cursor", "Invalid or expired observation cursor.", 400) from None

    async def close(self):
        self._closed = True
        await asyncio.to_thread(self._pool.shutdown, wait=True, cancel_futures=True)


class ReadFacade:
    """The bridge exposes only these two read operations, never CRUD methods."""

    def __init__(self, checks, observations):
        self._checks = checks
        self._observations = observations

    async def get_checks(self, include_disabled=True):
        if type(include_disabled) is not bool:
            raise ChecksReadError("invalid_parameters", "include_disabled must be a boolean.", 400)
        config = await self._checks.read()
        return {"schemaVersion": 1, "checks": [
            {field: check[field] for field in CHECK_FIELDS}
            for check in config["checks"] if include_disabled or check["enabled"]
        ]}

    async def get_observations(self, check_id, page_size=200, cursor=None, checked_from=None, checked_to=None):
        return await self._observations.page(check_id, page_size, cursor, checked_from, checked_to)
