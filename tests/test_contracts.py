import pytest

from app.contracts import ContractError, validate_checks


def test_preserves_history_and_extensions(document):
    document["extension"] = {"future": True}
    document["checks"][0].update(enabled=False, extra="preserved",
                                  departureDateFrom="2020-01-01", departureDateTo="2020-01-01")
    assert validate_checks(document) is document


def test_empty_list():
    assert validate_checks({"schemaVersion": 1, "checks": []})["checks"] == []


@pytest.mark.parametrize("version", [None, True, 1.0, "1", 2])
def test_unsupported_schema(document, version):
    document["schemaVersion"] = version
    with pytest.raises(ContractError):
        validate_checks(document)


@pytest.mark.parametrize("value", [None, [], {"checks": []}, {"schemaVersion": 1, "checks": {}},
                                  {"schemaVersion": 1, "checks": [None]}])
def test_invalid_document(value):
    with pytest.raises(ContractError):
        validate_checks(value)


@pytest.mark.parametrize("key,value", [
    ("id", ""), ("origin", "krk"), ("origin", "AA"), ("destination", "AAA"),
    ("currency", "pln"), ("departureDateFrom", "2027-02-30"),
    ("departureDateFrom", "20270214"), ("departureDateFrom", "2028-01-01"),
    ("enabled", 1), ("enabled", "false"),
])
def test_invalid_check(document, key, value):
    document["checks"][0][key] = value
    with pytest.raises(ContractError):
        validate_checks(document)


@pytest.mark.parametrize("key", ["id", "origin", "destination", "currency", "departureDateFrom", "departureDateTo", "enabled"])
def test_missing_field(document, key):
    del document["checks"][0][key]
    with pytest.raises(ContractError):
        validate_checks(document)


def test_duplicate_id(document):
    document["checks"].append(document["checks"][0].copy())
    with pytest.raises(ContractError):
        validate_checks(document)
