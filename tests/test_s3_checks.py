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


def snapshot_response(document, etag='"original"'):
    return {**response(json.dumps(document).encode()), "ETag": etag}


def put_params(document, etag='"original"'):
    return {**PARAMS, "IfMatch": etag, "ContentType": "application/json",
            "Body": json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()}


async def test_snapshot_returns_document_and_etag(s3_client, document):
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document), PARAMS)
        assert await S3Checks(s3_client, BUCKET).snapshot() == (document, '"original"')
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("enabled", [False, True])
async def test_toggle_preserves_other_checks_and_unknown_fields(s3_client, document, enabled):
    from copy import deepcopy
    document["future"] = {"nested": [1, "preserved", True]}
    document["checks"][0]["unknown"] = {"retain": "value"}
    document["checks"].append({**document["checks"][0], "id": "second-check", "enabled": False})
    expected = deepcopy(document)
    expected["checks"][0]["enabled"] = enabled
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document), PARAMS)
        stub.add_response("put_object", {"ETag": '"new"'}, put_params(expected))
        check, etag = await S3Checks(s3_client, BUCKET).set_enabled("example-check", enabled, '"original"')
        assert check == expected["checks"][0]
        assert etag == '"new"'
        stub.assert_no_pending_responses()


async def test_stale_browser_rejected_without_write(s3_client, document):
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document, '"changed"'), PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '"original"')
        assert error.value.status == 409
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("aws_code,status", [("PreconditionFailed", 409),
                                             ("ConditionalRequestConflict", 409),
                                             ("AccessDenied", 502), ("InternalError", 503)])
async def test_concurrent_writer_or_storage_failure(s3_client, document, aws_code, status):
    expected = json.loads(json.dumps(document))
    expected["checks"][0]["enabled"] = False
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document), PARAMS)
        stub.add_client_error("put_object", service_error_code=aws_code,
            service_message="private-write-detail", http_status_code=412 if status == 409 else status,
            expected_params=put_params(expected))
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '"original"')
        assert error.value.status == status
        assert "private-write-detail" not in str(error.value)
        stub.assert_no_pending_responses()


async def test_unknown_check_does_not_write(s3_client, document):
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document), PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("missing", False, '"original"')
        assert error.value.status == 404
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("etag", [None, "unquoted", '*', 'W/"one"'])
async def test_missing_or_invalid_s3_etag_prevents_writes(s3_client, document, etag):
    result = response(json.dumps(document).encode())
    if etag is not None:
        result["ETag"] = etag
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", result, PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", True, '"original"')
        assert error.value.status == 502
        stub.assert_no_pending_responses()


async def test_malformed_config_never_written(s3_client):
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", {**response(b'{"schemaVersion":2,"checks":[]}'), "ETag": '"original"'}, PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '"original"')
        assert error.value.code == "invalid_config"
        stub.assert_no_pending_responses()


async def test_duplicate_unknown_keys_are_not_silently_overwritten(s3_client):
    raw = b'{"schemaVersion":1,"checks":[],"future":1,"future":2}'
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", response(raw), PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).read()
        assert error.value.code == "invalid_config"


async def test_put_timeout_is_ambiguous_and_not_retried_by_adapter(document):
    client = Mock()
    client.get_object.return_value = snapshot_response(document)
    client.put_object.side_effect = ReadTimeoutError(endpoint_url="https://private.invalid")
    with pytest.raises(ChecksReadError) as error:
        await S3Checks(client, BUCKET).set_enabled("example-check", False, '"original"')
    assert error.value.status == 503
    assert "Refresh" in str(error.value)
    assert "private.invalid" not in str(error.value)
    client.put_object.assert_called_once()


@pytest.mark.parametrize("etag", [None, "unquoted"])
async def test_unconfirmed_write_requires_refresh(s3_client, document, etag):
    expected = json.loads(json.dumps(document))
    expected["checks"][0]["enabled"] = False
    result = {} if etag is None else {"ETag": etag}
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot_response(document), PARAMS)
        stub.add_response("put_object", result, put_params(expected))
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '"original"')
        assert error.value.code == "s3_write_failed"
        stub.assert_no_pending_responses()


async def test_serialized_size_limit_prevents_write(s3_client, document):
    # Valid UTF-8 input fits, but ASCII-safe serialization expands the extension.
    document["extension"] = "é" * (MAX_CONFIG_BYTES // 4)
    raw = json.dumps(document, ensure_ascii=False).encode()
    assert len(raw) < MAX_CONFIG_BYTES
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", {**response(raw), "ETag": '"original"'}, PARAMS)
        with pytest.raises(ChecksReadError) as error:
            await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '"original"')
        assert error.value.code == "invalid_config"
        stub.assert_no_pending_responses()


async def test_unknown_numeric_fields_keep_precision(s3_client):
    raw = (b'{"schemaVersion":1,"checks":[{"id":"example-check","origin":"AAA",'
           b'"destination":"BBB","departureDateFrom":"2027-01-01",'
           b'"departureDateTo":"2027-01-01","currency":"PLN","enabled":true}],'
           b'"future":{"precise":0.12345678901234567890123456789,"large":1e999}}')
    expected = raw.replace(b'"enabled":true', b'"enabled":false').replace(b'1e999', b'1E+999')
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", {**response(raw), "ETag": '\"original\"'}, PARAMS)
        stub.add_response("put_object", {"ETag": '\"new\"'},
                          {**PARAMS, "IfMatch": '\"original\"', "ContentType": "application/json", "Body": expected})
        await S3Checks(s3_client, BUCKET).set_enabled("example-check", False, '\"original\"')
        stub.assert_no_pending_responses()
