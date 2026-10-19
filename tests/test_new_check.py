from datetime import date, timedelta

import pytest

from app.contracts import ContractError, validate_new_check


TODAY = date(2026, 10, 19)


def candidate(**overrides):
    return {
        "id": "krk-mad-feb-2027", "origin": "KRK", "destination": "MAD", "currency": "PLN",
        "departureDateFrom": "2027-02-14", "departureDateTo": "2027-02-21", **overrides,
    }


def test_default_enabled_and_input_is_not_mutated():
    payload = candidate()
    assert validate_new_check(payload, today=TODAY) == {**payload, "enabled": True}
    assert "enabled" not in payload


def test_explicit_disabled():
    assert validate_new_check(candidate(enabled=False), today=TODAY)["enabled"] is False


@pytest.mark.parametrize("key,value", [
    ("id", "UPPER"), ("id", " has-space "), ("id", "a/b"), ("id", "a--b"), ("id", "a" * 129),
    ("origin", "krk"), ("origin", "KR"), ("origin", 123), ("destination", "KRK"),
    ("currency", "pln"), ("currency", "PLNN"), ("enabled", "false"), ("enabled", 0),
    ("departureDateFrom", "2027-02-30"), ("departureDateFrom", "20270214"),
    ("departureDateFrom", "2027-02-22"), ("departureDateTo", None),
])
def test_invalid_fields(key, value):
    with pytest.raises(ContractError):
        validate_new_check(candidate(**{key: value}), today=TODAY)


@pytest.mark.parametrize("key", ["id", "origin", "destination", "currency", "departureDateFrom", "departureDateTo"])
def test_required_fields(key):
    payload = candidate()
    del payload[key]
    with pytest.raises(ContractError):
        validate_new_check(payload, today=TODAY)


@pytest.mark.parametrize("payload", [None, [], "text", {}, candidate(extra=True), candidate(schemaVersion=1)])
def test_only_supported_object_fields(payload):
    with pytest.raises(ContractError):
        validate_new_check(payload, today=TODAY)


@pytest.mark.parametrize("first,last", [(0, 0), (730, 730), (0, 365)])
def test_date_bounds_are_inclusive(first, last):
    payload = candidate(departureDateFrom=(TODAY + timedelta(days=first)).isoformat(),
                        departureDateTo=(TODAY + timedelta(days=last)).isoformat())
    assert validate_new_check(payload, today=TODAY)["enabled"] is True


@pytest.mark.parametrize("first,last", [(-1, 0), (730, 731), (0, 366)])
def test_outside_date_limits(first, last):
    payload = candidate(departureDateFrom=(TODAY + timedelta(days=first)).isoformat(),
                        departureDateTo=(TODAY + timedelta(days=last)).isoformat())
    with pytest.raises(ContractError):
        validate_new_check(payload, today=TODAY)
