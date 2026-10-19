import asyncio
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from app.ha_bridge import HomeAssistantBridge, REQUEST_EVENT, RESPONSE_EVENT


def event(operation='get_checks', parameters=None):
    return {'event_type': REQUEST_EVENT, 'context': {'user_id': 'viewer'},
            'data': {'request_id': 'a' * 32, 'operation': operation, 'parameters': parameters or {}}}


@pytest.mark.parametrize('operation', ['create', 'set_enabled', 'delete', 'put_object', 'get_object'])
async def test_event_bridge_never_dispatches_writes_or_generic_s3(operation):
    reader = AsyncMock()
    response = await HomeAssistantBridge(reader).dispatch(event(operation, {'enabled': False}))
    assert response['ok'] is False
    assert response['error']['status'] == 400
    reader.get_checks.assert_not_awaited()
    reader.get_observations.assert_not_awaited()


@pytest.mark.parametrize('bad', [None, {}, {'context': {}, 'data': {}},
                                {'context': {'user_id': 'viewer'}, 'data': {'request_id': 'guess'}}])
async def test_unidentified_or_uncorrelated_events_are_ignored(bad):
    reader = AsyncMock()
    assert await HomeAssistantBridge(reader).dispatch(bad) is None
    reader.get_checks.assert_not_awaited()


async def test_real_ha_websocket_and_response_event_transport(aiohttp_server, monkeypatch):
    received = asyncio.get_running_loop().create_future()
    reader = AsyncMock()
    reader.get_checks.return_value = {'schemaVersion': 1, 'checks': []}
    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({'type': 'auth_required'})
        assert await ws.receive_json() == {'type': 'auth', 'access_token': 'fake-test-token'}
        await ws.send_json({'type': 'auth_ok'})
        assert await ws.receive_json() == {'id': 1, 'type': 'subscribe_events', 'event_type': REQUEST_EVENT}
        await ws.send_json({'id': 1, 'type': 'result', 'success': True})
        await ws.send_json({'type': 'event', 'event': event()})
        async for _message in ws:
            pass
        return ws
    async def respond(request):
        assert request.match_info['event_type'] == RESPONSE_EVENT
        assert request.headers['Authorization'] == 'Bearer fake-test-token'
        received.set_result(await request.json())
        return web.json_response({'ok': True})
    app = web.Application()
    app.router.add_get('/core/websocket', websocket)
    app.router.add_post('/core/api/events/{event_type}', respond)
    server = await aiohttp_server(app)
    monkeypatch.setattr('app.ha_bridge.WEBSOCKET_URL', str(server.make_url('/core/websocket')))
    monkeypatch.setattr('app.ha_bridge.EVENTS_URL', str(server.make_url('/core/api/events/')))
    task = asyncio.create_task(HomeAssistantBridge(reader, 'fake-test-token').run())
    try:
        response = await asyncio.wait_for(received, 3)
        assert response == {'request_id': 'a' * 32, 'ok': True, 'result': {'schemaVersion': 1, 'checks': []}}
        assert 'fake-test-token' not in str(response)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
