from copy import deepcopy
from io import BytesIO
import json
from unittest.mock import AsyncMock

from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.admin import create_app
from app.authorization import AuthorizationError
from app.s3_checks import CHECKS_KEY, S3Checks

BUCKET = "example-flight-checks"
PARAMS = {"Bucket": BUCKET, "Key": CHECKS_KEY}
HEADERS = {"If-Match": '"original"'}


def snapshot(document, etag='"original"'):
    raw = json.dumps(document).encode()
    return {"Body": StreamingBody(BytesIO(raw), len(raw)), "ETag": etag}


def expected_put(document):
    return {**PARAMS, "IfMatch": '"original"', "ContentType": "application/json",
            "Body": json.dumps(document, separators=(",", ":")).encode()}


@pytest.fixture
async def create_client(aiohttp_client, monkeypatch, s3_client):
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    client = await aiohttp_client(create_app(S3Checks(s3_client, BUCKET), AsyncMock()))
    client.session.headers["X-Remote-User-Id"] = "admin"
    return client


@pytest.mark.parametrize("enabled", [None, False])
async def test_create_merges_without_replacing_other_checks(create_client, s3_client, document, new_check, enabled):
    document["future"] = {"keep": [1, 2, "value"]}
    document["checks"][0]["unknown"] = "preserved"
    if enabled is not None:
        new_check["enabled"] = enabled
    expected = deepcopy(document)
    created = {**new_check, "enabled": enabled if enabled is not None else True}
    expected["checks"].append(created)
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot(document), PARAMS)
        stub.add_response("put_object", {"ETag": '"new"'}, expected_put(expected))
        response = await create_client.post("/api/checks", json=new_check, headers=HEADERS)
        assert response.status == 201
        assert await response.json() == created
        assert response.headers["ETag"] == '"new"'
        assert response.headers["Cache-Control"] == "no-store"
        stub.assert_no_pending_responses()


async def test_duplicate_id_is_not_an_update(create_client, s3_client, document, new_check):
    new_check["id"] = document["checks"][0]["id"]
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot(document), PARAMS)
        response = await create_client.post("/api/checks", json=new_check, headers=HEADERS)
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "duplicate_id"
        stub.assert_no_pending_responses()


async def test_stale_form_does_not_write(create_client, s3_client, document, new_check):
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot(document, '"changed"'), PARAMS)
        response = await create_client.post("/api/checks", json=new_check, headers=HEADERS)
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "conflict"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("aws_code,status", [("PreconditionFailed", 409), ("AccessDenied", 502), ("InternalError", 503)])
async def test_intervening_writer_and_write_errors(create_client, s3_client, document, new_check, aws_code, status):
    expected = deepcopy(document)
    expected["checks"].append({**new_check, "enabled": True})
    with Stubber(s3_client) as stub:
        stub.add_response("get_object", snapshot(document), PARAMS)
        stub.add_client_error("put_object", service_error_code=aws_code, service_message="private-error-detail",
                              expected_params=expected_put(expected))
        response = await create_client.post("/api/checks", json=new_check, headers=HEADERS)
        assert response.status == status
        assert "private-error-detail" not in await response.text()
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("payload", [None, [], {}, {"id": "bad"}, {"checks": []}])
async def test_invalid_create_never_reads_s3(create_client, s3_client, payload):
    with Stubber(s3_client) as stub:
        response = await create_client.post("/api/checks", data=json.dumps(payload),
            headers={**HEADERS, "Content-Type": "application/json"})
        assert response.status == 400
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("etag", [None, "*", 'W/"original"', '"one", "two"'])
async def test_strong_client_version_required(create_client, s3_client, new_check, etag):
    with Stubber(s3_client):
        response = await create_client.post("/api/checks", json=new_check,
                                            headers={"If-Match": etag} if etag else {})
        assert response.status == 400


@pytest.mark.parametrize("body,content_type", [("{", "application/json"),
                                               ('{"id":"one","id":"two"}', "application/json"),
                                               ('id=one', "application/x-www-form-urlencoded")])
async def test_bad_json_or_form_submission(create_client, s3_client, body, content_type):
    with Stubber(s3_client):
        response = await create_client.post("/api/checks", data=body,
            headers={**HEADERS, "Content-Type": content_type})
        assert response.status == 400


@pytest.mark.parametrize("denied_status", [403, 503])
async def test_create_requires_live_admin_check(aiohttp_client, monkeypatch, new_check, denied_status):
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    store = AsyncMock()
    authorizer = AsyncMock()
    authorizer.require_admin.side_effect = AuthorizationError(denied_status, "denied", "Denied")
    client = await aiohttp_client(create_app(store, authorizer))
    response = await client.post("/api/checks", json=new_check,
                                headers={**HEADERS, "X-Remote-User-Id": "viewer"})
    assert response.status == denied_status
    store.create.assert_not_awaited()


async def test_direct_peer_cannot_create(aiohttp_client, new_check):
    store, authorizer = AsyncMock(), AsyncMock()
    client = await aiohttp_client(create_app(store, authorizer))
    response = await client.post("/api/checks", json=new_check,
        headers={**HEADERS, "X-Remote-User-Id": "admin", "X-Forwarded-For": "172.30.32.2"})
    assert response.status == 403
    authorizer.require_admin.assert_not_awaited()
    store.create.assert_not_awaited()


async def test_missing_config_is_not_replaced(create_client, s3_client, new_check):
    with Stubber(s3_client) as stub:
        stub.add_client_error("get_object", service_error_code="NoSuchKey", expected_params=PARAMS)
        response = await create_client.post("/api/checks", json=new_check, headers=HEADERS)
        assert response.status == 502
        stub.assert_no_pending_responses()
