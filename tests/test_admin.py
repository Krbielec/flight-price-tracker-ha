from unittest.mock import AsyncMock

from aiohttp.test_utils import make_mocked_request
import pytest

from app.admin import create_app, ingress_only
from app.s3_checks import ChecksReadError


@pytest.fixture
def store(document):
    return AsyncMock(read=AsyncMock(return_value=document), close=AsyncMock())


@pytest.fixture
async def ingress_client(aiohttp_client, monkeypatch, store):
    # Change only the trusted peer constant. Keep the real middleware installed.
    monkeypatch.setattr("app.admin.INGRESS_PROXY_IP", "127.0.0.1")
    return await aiohttp_client(create_app(store))


async def test_http_contract(ingress_client, document, store):
    response = await ingress_client.get("/api/checks")
    assert response.status == 200
    assert await response.json() == document
    assert response.headers["Cache-Control"] == "no-store"
    store.read.assert_awaited_once()


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
    store.read.assert_not_awaited()


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
    store.read.side_effect = ChecksReadError(code, "Safe error", status)
    response = await ingress_client.get("/api/checks")
    assert response.status == status
    assert await response.json() == {"error": {"code": code, "message": "Safe error"}}
    assert code in caplog.text


@pytest.mark.parametrize("method,path", [("post", "/api/checks"), ("put", "/api/checks/example"),
                                          ("patch", "/api/checks/example/enabled"),
                                          ("delete", "/api/checks/example")])
async def test_writes_not_available(ingress_client, store, method, path):
    response = await getattr(ingress_client, method)(path, json={"enabled": False})
    assert response.status in (404, 405)
    store.read.assert_not_awaited()


async def test_cleanup_closes_client(store):
    app = create_app(store)
    app.freeze()
    await app.cleanup()
    store.close.assert_awaited_once()
