from unittest.mock import AsyncMock, MagicMock

import pytest

from app.authorization import AuthorizationError, HAAdminAuthorizer


@pytest.fixture
def ha(monkeypatch):
    ws = AsyncMock()
    ws.receive_json.side_effect = [
        {"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": [
            {"id": "admin", "is_active": True, "is_owner": False, "group_ids": ["system-admin"]},
            {"id": "owner", "is_active": True, "is_owner": True, "group_ids": []},
            {"id": "viewer", "is_active": True, "group_ids": ["system-users"]},
            {"id": "inactive", "is_active": False, "group_ids": ["system-admin"]},
        ]},
    ]
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=ws)
    connection.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.ws_connect.return_value = connection
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session_context)
    monkeypatch.setattr("app.authorization.ClientSession", factory)
    return ws, connection, session_context, factory


@pytest.mark.parametrize("identity", ["admin", "owner"])
async def test_active_administrator(ha, identity):
    ws, connection, context, _ = ha
    await HAAdminAuthorizer("private-test-token").require_admin(identity)
    assert ws.send_json.call_args_list[1].args == ({"id": 1, "type": "config/auth/list"},)
    connection.__aexit__.assert_awaited_once()
    context.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("identity", ["viewer", "inactive", "unknown"])
async def test_denies_non_admin_and_inactive(ha, identity):
    with pytest.raises(AuthorizationError) as error:
        await HAAdminAuthorizer("private-test-token").require_admin(identity)
    assert error.value.status == 403


@pytest.mark.parametrize("identity", [None, "", "x" * 129])
async def test_requires_identity_before_contacting_ha(ha, identity):
    with pytest.raises(AuthorizationError) as error:
        await HAAdminAuthorizer("private-test-token").require_admin(identity)
    assert error.value.status == 403
    ha[3].assert_not_called()


async def test_missing_token(monkeypatch, ha):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(AuthorizationError) as error:
        await HAAdminAuthorizer().require_admin("admin")
    assert error.value.status == 503
    ha[3].assert_not_called()


@pytest.mark.parametrize("replies", [
    [{"type": "private-test-token"}],
    [{"type": "auth_required"}, {"type": "auth_invalid"}],
    [{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "success": False}],
    [{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 2, "success": True}],
    [{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "success": True, "result": {}}],
    [{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "success": True, "result": [None]}],
    [None],
])
async def test_bad_ha_reply_fails_closed(ha, replies, caplog):
    ha[0].receive_json.side_effect = replies
    with pytest.raises(AuthorizationError) as error:
        await HAAdminAuthorizer("private-test-token").require_admin("admin")
    assert error.value.status == 503
    assert "private-test-token" not in str(error.value) + caplog.text


async def test_timeout_fails_closed(ha):
    ha[0].receive_json.side_effect = TimeoutError("private-test-token")
    with pytest.raises(AuthorizationError) as error:
        await HAAdminAuthorizer("private-test-token").require_admin("admin")
    assert error.value.status == 503
    assert "private-test-token" not in str(error.value)


async def test_role_is_checked_on_every_request(ha):
    ws = ha[0]
    authorizer = HAAdminAuthorizer("private-test-token")
    await authorizer.require_admin("admin")
    ws.receive_json.side_effect = [
        {"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "success": True, "result": [{"id": "admin", "is_active": True, "group_ids": []}]},
    ]
    with pytest.raises(AuthorizationError) as error:
        await authorizer.require_admin("admin")
    assert error.value.status == 403


async def test_real_websocket_protocol(aiohttp_server, monkeypatch):
    from aiohttp import web
    seen = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        seen.append(await ws.receive_json())
        await ws.send_json({"type": "auth_ok"})
        seen.append(await ws.receive_json())
        await ws.send_json({"id": 1, "type": "result", "success": True,
                           "result": [{"id": "admin", "is_active": True, "group_ids": ["system-admin"]}]})
        async for _message in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/core/websocket", handler)
    server = await aiohttp_server(app)
    monkeypatch.setattr("app.authorization.HA_WEBSOCKET_URL", str(server.make_url("/core/websocket")))
    await HAAdminAuthorizer("fake-test-token").require_admin("admin")
    assert seen == [{"type": "auth", "access_token": "fake-test-token"},
                    {"id": 1, "type": "config/auth/list"}]
