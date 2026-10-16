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
