"""Tests never use real AWS credentials or AWS HTTP connections."""

import boto3
from botocore.httpsession import URLLib3Session
import pytest


@pytest.fixture(autouse=True)
def block_aws_network(monkeypatch):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")

    def forbidden(*args, **kwargs):
        raise AssertionError("AWS network access is forbidden in tests")

    monkeypatch.setattr(URLLib3Session, "send", forbidden)


@pytest.fixture
def s3_client():
    client = boto3.client(
        "s3", region_name="eu-central-1",
        aws_access_key_id="test-key", aws_secret_access_key="test-secret",
    )
    yield client
    client.close()


@pytest.fixture
def document():
    return {
        "schemaVersion": 1,
        "checks": [{
            "id": "example-check", "origin": "AAA", "destination": "BBB",
            "departureDateFrom": "2027-02-14", "departureDateTo": "2027-02-21",
            "currency": "PLN", "enabled": True,
        }],
    }


@pytest.fixture
def new_check():
    from datetime import timedelta
    from app.contracts import current_date
    start = current_date() + timedelta(days=30)
    return {
        "id": "new-check", "origin": "AAA", "destination": "CCC", "currency": "PLN",
        "departureDateFrom": start.isoformat(), "departureDateTo": (start + timedelta(days=6)).isoformat(),
    }


@pytest.fixture
def observation():
    return {
        "schemaVersion": 1, "checkId": "example-check", "checkedAt": "2026-10-19T22:30:00+00:00",
        "origin": "AAA", "destination": "BBB", "departureDateFrom": "2027-02-14", "departureDateTo": "2027-02-21",
        "prices": [{"departure_date": "2027-02-19", "departure_at": "2027-02-19T23:45:00",
                    "arrival_at": "2027-02-20T02:15:00", "price": 196.5, "currency": "PLN",
                    "sold_out": False, "unavailable": False}],
    }
