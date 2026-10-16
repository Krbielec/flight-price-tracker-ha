from io import BytesIO
import json
from unittest.mock import Mock

from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.s3_checks import CHECKS_KEY, MAX_CONFIG_BYTES, ChecksReadError, S3Checks
from app.settings import Settings

BUCKET = "example-flight-checks"
PARAMS = {"Bucket": BUCKET, "Key": CHECKS_KEY}


def response(raw):
    return {"Body": StreamingBody(BytesIO(raw), len(raw))}


async def test_get_object_contract(s3_client, document):
    document["checks"][0]["enabled"] = False
    document["extra"] = "kept"
    result = response(json.dumps(document).encode())
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", result, PARAMS)
        assert await S3Checks(s3_client, BUCKET).read() == document
        stub.assert_no_pending_responses()
    assert result["Body"]._raw_stream.closed


@pytest.mark.parametrize("aws_code,http_status,error_code,status", [
    ("AccessDenied", 403, "s3_access_denied", 502),
    ("NoSuchKey", 404, "s3_not_found", 502),
    ("NoSuchBucket", 404, "s3_not_found", 502),
    ("SlowDown", 503, "s3_unavailable", 503),
])
async def test_s3_errors_are_sanitized(s3_client, aws_code, http_status, error_code, status):
    with Stubber(s3_client) as stub:
        stub.add_client_error("get_object", service_error_code=aws_code,
                              service_message="private-test-detail", http_status_code=http_status,
                              expected_params=PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).read()
        assert (error.value.code, error.value.status) == (error_code, status)
        assert "private-test-detail" not in str(error.value)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("raw", [b"{private-test-detail", b"\xff", b"[]",
                                  b'{"schemaVersion":2,"checks":[]}',
                                  b'{"schemaVersion":1,"checks":[],"extra":NaN}',
                                  b"x" * (MAX_CONFIG_BYTES + 1)])
async def test_invalid_body(s3_client, raw):
    result = response(raw)
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", result, PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).read()
        assert error.value.code == "invalid_config"
        assert "private-test-detail" not in str(error.value)
    assert result["Body"]._raw_stream.closed


@pytest.mark.parametrize("failure", [EndpointConnectionError(endpoint_url="https://private.invalid"),
                                      ReadTimeoutError(endpoint_url="https://private.invalid")])
async def test_network_failure(failure):
    client = Mock()
    client.get_object.side_effect = failure
    with pytest.raises(ChecksReadError) as error:
        await S3Checks(client, BUCKET).read()
    assert error.value.status == 503
    assert "private.invalid" not in str(error.value)


async def test_stream_failure_closes_body():
    body = Mock()
    body.read.side_effect = ReadTimeoutError(endpoint_url="https://private.invalid")
    client = Mock()
    client.get_object.return_value = {"Body": body}
    with pytest.raises(ChecksReadError):
        await S3Checks(client, BUCKET).read()
    body.close.assert_called_once()


def test_client_uses_explicit_settings_and_bounded_io():
    store = S3Checks.from_settings(Settings("eu-central-1", BUCKET, "test-key", "test-secret"))
    try:
        config = store.client.meta.config
        assert store.client.meta.region_name == "eu-central-1"
        assert config.connect_timeout == 5
        assert config.read_timeout == 10
        assert config.retries["total_max_attempts"] == 3
    finally:
        store.client.close()
