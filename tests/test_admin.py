from unittest.mock import AsyncMock

from aiohttp.test_utils import make_mocked_request
import pytest

from app.admin import create_app, ingress_only
from app.s3_checks import ChecksReadError


@pytest.fixture
def store(document):
    return AsyncMock(snapshot=AsyncMock(return_value=(document, '"original"')),
                     close=AsyncMock())


@pytest.fixture
async def ingress_client(aiohttp_client, monkeypatch, store):
    # Change only the trusted peer constant. Keep the real middleware installed.
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    authorizer = AsyncMock()
    client = await aiohttp_client(create_app(store, authorizer))
    client.session.headers["X-Remote-User-Id"] = "administrator"
    return client


async def test_http_contract(ingress_client, document, store):
    response = await ingress_client.get("/api/checks")
    assert response.status == 200
    assert await response.json() == document
    assert response.headers["Cache-Control"] == "no-store"
    store.snapshot.assert_awaited_once()


async def test_panel(ingress_client):
    response = await ingress_client.get("/")
    assert response.status == 200
    html = await response.text()
    assert "Flight Price Tracker" in html
    assert "document.baseURI" in html
    assert "aws_secret_access_key" not in html


async def test_direct_access_denied(aiohttp_client, store):
    client = await aiohttp_client(create_app(store))
    for path in ("/", "/api/checks"):
        response = await client.get(path, headers={"X-Forwarded-For": "172.30.32.2",
                                                  "X-Ingress-Path": "/api/hassio_ingress/fake"})
        assert response.status == 403
    store.snapshot.assert_not_awaited()


async def test_actual_proxy_peer_passes():
    transport = AsyncMock()
    transport.get_extra_info = lambda name: ("172.30.32.2", 1234) if name == "peername" else None
    request = make_mocked_request("GET", "/api/checks", transport=transport)
    handler = AsyncMock(return_value="accepted")
    assert await ingress_only(request, handler) == "accepted"
    handler.assert_awaited_once_with(request)


@pytest.mark.parametrize("code,status", [("s3_access_denied", 502), ("s3_not_found", 502),
                                         ("s3_unavailable", 503), ("invalid_config", 502)])
async def test_failure_is_not_empty_list(ingress_client, store, code, status, caplog):
    store.snapshot.side_effect = ChecksReadError(code, "Safe error", status)
    response = await ingress_client.get("/api/checks")
    assert response.status == status
    assert await response.json() == {"error": {"code": code, "message": "Safe error"}}
    assert code in caplog.text


@pytest.mark.parametrize("method,path", [("post", "/api/checks"), ("put", "/api/checks/example"),
                                          ("delete", "/api/checks/example")])
async def test_writes_not_available(ingress_client, store, method, path):
    response = await getattr(ingress_client, method)(path, json={"enabled": False})
    assert response.status in (404, 405)
    store.snapshot.assert_not_awaited()


async def test_cleanup_closes_client(store):
    app = create_app(store)
    app.freeze()
    await app.cleanup()
    store.close.assert_awaited_once()


async def test_read_returns_s3_etag(ingress_client):
    response = await ingress_client.get("/api/checks")
    assert response.headers["ETag"] == '"original"'


async def test_toggle(ingress_client, store, document):
    changed = {**document["checks"][0], "enabled": False}
    store.set_enabled.return_value = (changed, '"new"')
    response = await ingress_client.patch("/api/checks/example-check/enabled", json={"enabled": False},
                                          headers={"If-Match": '"original"'})
    assert response.status == 200
    assert await response.json() == changed
    assert response.headers["ETag"] == '"new"'
    store.set_enabled.assert_awaited_once_with("example-check", False, '"original"')


@pytest.mark.parametrize("body", [{}, {"enabled": 0}, {"enabled": "false"}, [],
                                  {"enabled": False, "origin": "CCC"}])
async def test_bad_toggle_body(ingress_client, store, body):
    response = await ingress_client.patch("/api/checks/example-check/enabled", json=body,
                                          headers={"If-Match": '"original"'})
    assert response.status == 400
    store.set_enabled.assert_not_awaited()


@pytest.mark.parametrize("etag", [None, "*", 'W/"original"', '"one", "two"', "unquoted"])
async def test_bad_version(ingress_client, store, etag):
    response = await ingress_client.patch("/api/checks/example-check/enabled", json={"enabled": True},
                                          headers={"If-Match": etag} if etag else {})
    assert response.status == 400
    store.set_enabled.assert_not_awaited()


@pytest.mark.parametrize("body,content_type", [("{", "application/json"),
                                              ('{"enabled":true}', "text/plain")])
async def test_rejects_malformed_and_form_content(ingress_client, store, body, content_type):
    response = await ingress_client.patch("/api/checks/example-check/enabled", data=body,
        headers={"If-Match": '"original"', "Content-Type": content_type})
    assert response.status == 400
    store.set_enabled.assert_not_awaited()


@pytest.mark.parametrize("status", [404, 409, 502, 503])
async def test_toggle_errors(ingress_client, store, status):
    store.set_enabled.side_effect = ChecksReadError("safe_code", "Safe message", status)
    response = await ingress_client.patch("/api/checks/example-check/enabled", json={"enabled": True},
                                          headers={"If-Match": '"original"'})
    assert response.status == status
    assert (await response.json())["error"]["message"] == "Safe message"


@pytest.mark.parametrize("method,path", [("get", "/"), ("get", "/api/checks"),
                                          ("patch", "/api/checks/example-check/enabled")])
async def test_unknown_identity_denied(aiohttp_client, monkeypatch, store, method, path):
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    client = await aiohttp_client(create_app(store))
    response = await getattr(client, method)(path, headers={"X-Hass-Is-Admin": "true"})
    assert response.status == 403
    store.snapshot.assert_not_awaited()
    store.set_enabled.assert_not_awaited()


async def test_nonadmin_denied_before_storage(aiohttp_client, monkeypatch, store):
    from app.authorization import AuthorizationError
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    authorizer = AsyncMock()
    authorizer.require_admin.side_effect = AuthorizationError(403, "admin_required", "Denied")
    client = await aiohttp_client(create_app(store, authorizer))
    response = await client.patch("/api/checks/example-check/enabled", json={"enabled": False},
        headers={"X-Remote-User-Id": "viewer", "If-Match": '"original"'})
    assert response.status == 403
    authorizer.require_admin.assert_awaited_once_with("viewer")
    store.set_enabled.assert_not_awaited()


async def test_duplicate_boolean_keys_rejected(ingress_client, store):
    response = await ingress_client.patch("/api/checks/example-check/enabled",
        data='{"enabled":true,"enabled":false}',
        headers={"If-Match": '"original"', "Content-Type": "application/json"})
    assert response.status == 400
    store.set_enabled.assert_not_awaited()


async def test_ha_authorization_failure_does_not_reach_s3(aiohttp_client, monkeypatch, store, caplog):
    from app.authorization import AuthorizationError
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    authorizer = AsyncMock()
    authorizer.require_admin.side_effect = AuthorizationError(503, "authorization_unavailable", "HA unavailable")
    client = await aiohttp_client(create_app(store, authorizer))
    response = await client.patch("/api/checks/example-check/enabled", json={"enabled": True},
        headers={"X-Remote-User-Id": "admin", "If-Match": '"original"'})
    assert response.status == 503
    assert (await response.json())["error"]["code"] == "authorization_unavailable"
    store.set_enabled.assert_not_awaited()


async def test_forged_identity_from_direct_peer_is_rejected(aiohttp_client, store):
    authorizer = AsyncMock()
    client = await aiohttp_client(create_app(store, authorizer))
    response = await client.patch("/api/checks/example-check/enabled", json={"enabled": True},
        headers={"X-Remote-User-Id": "admin", "X-Forwarded-For": "172.30.32.2", "If-Match": '"original"'})
    assert response.status == 403
    authorizer.require_admin.assert_not_awaited()
    store.set_enabled.assert_not_awaited()


async def test_read_preserves_extension_number_precision(ingress_client, document):
    from decimal import Decimal
    document["extension"] = Decimal("0.12345678901234567890123456789")
    response = await ingress_client.get("/api/checks")
    assert response.status == 200
    assert '"extension":0.12345678901234567890123456789' in await response.text()
