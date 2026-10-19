import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ha_bridge import HomeAssistantBridge, REQUEST_EVENT, RESPONSE_EVENT
from app.s3_checks import ChecksReadError


@pytest.fixture
def integration(monkeypatch):
    # Focused HA doubles: use the real integration and voluptuous schemas, without
    # installing HA Core or making any connection to an actual HA instance.
    root, core, errors = ModuleType('homeassistant'), ModuleType('homeassistant.core'), ModuleType('homeassistant.exceptions')
    class HAError(Exception):
        pass
    class ValidationError(HAError):
        pass
    core.HomeAssistant = object
    core.ServiceCall = object
    core.SupportsResponse = SimpleNamespace(ONLY='only')
    errors.HomeAssistantError = HAError
    errors.ServiceValidationError = ValidationError
    for name, module in [('homeassistant', root), ('homeassistant.core', core), ('homeassistant.exceptions', errors)]:
        monkeypatch.setitem(sys.modules, name, module)
    path = Path('flight_price_tracker/custom_components/flight_price_tracker/__init__.py')
    spec = importlib.util.spec_from_file_location('flight_tracker_test_integration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Bus:
    def __init__(self):
        self.listeners = {}
        self.events = []
        self.relay = None
    def async_listen(self, event_type, handler):
        self.listeners.setdefault(event_type, []).append(handler)
    def async_fire(self, event_type, data, context=None):
        event = SimpleNamespace(data=data, context=context)
        self.events.append((event_type, event))
        for handler in self.listeners.get(event_type, []):
            handler(event)
        if event_type == REQUEST_EVENT and self.relay:
            asyncio.create_task(self.relay(event))


@pytest.fixture
async def hass(integration):
    registry, bus = {}, Bus()
    def register(domain, name, handler, **kwargs):
        assert domain == 'flight_price_tracker'
        registry[name] = (handler, kwargs)
    user = SimpleNamespace(is_active=True, is_admin=False)
    hass = SimpleNamespace(loop=asyncio.get_running_loop(), bus=bus, registry=registry,
        services=SimpleNamespace(async_register=register),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)))
    await integration.async_setup(hass, {})
    return hass


async def invoke(hass, name, data=None, user_id='tablet-viewer'):
    handler, kwargs = hass.registry[name]
    call = SimpleNamespace(context=SimpleNamespace(user_id=user_id), data=kwargs['schema'](data or {}))
    return await handler(call)


async def test_nonadmin_service_returns_correlated_addon_read(hass):
    reader = AsyncMock()
    reader.get_checks.return_value = {'schemaVersion': 1, 'checks': []}
    bridge = HomeAssistantBridge(reader, 'fake-test-token')
    async def relay(event):
        response = await bridge.dispatch({'data': event.data, 'context': {'user_id': event.context.user_id}})
        hass.bus.async_fire(RESPONSE_EVENT, response)
    hass.bus.relay = relay
    assert await invoke(hass, 'get_checks', {'include_disabled': False}) == {'schemaVersion': 1, 'checks': []}
    reader.get_checks.assert_awaited_once_with(include_disabled=False)
    assert set(hass.registry) == {'get_checks', 'get_observations'}
    assert all(kwargs['supports_response'] == 'only' for _, kwargs in hass.registry.values())


@pytest.mark.parametrize('user_id,active', [(None, True), ('inactive', False)])
async def test_anonymous_or_inactive_user_cannot_request_reads(hass, user_id, active):
    hass.auth.async_get_user.return_value = SimpleNamespace(is_active=active)
    with pytest.raises(Exception, match='authenticated active'):
        await invoke(hass, 'get_checks', user_id=user_id)
    assert hass.bus.events == []


async def test_independent_concurrent_callers_do_not_share_responses(hass):
    first = asyncio.create_task(invoke(hass, 'get_observations', {'check_id': 'first'}))
    second = asyncio.create_task(invoke(hass, 'get_observations', {'check_id': 'second'}))
    await asyncio.sleep(0)
    requests = [event.data for event_type, event in hass.bus.events if event_type == REQUEST_EVENT]
    assert len(requests) == 2
    for request in reversed(requests):
        hass.bus.async_fire(RESPONSE_EVENT, {'request_id': request['request_id'], 'ok': True,
            'result': {'checkId': request['parameters']['check_id'], 'observations': [], 'nextCursor': None}})
    assert (await first)['checkId'] == 'first'
    assert (await second)['checkId'] == 'second'


async def test_timeout_releases_pending_capacity(hass, integration, monkeypatch):
    monkeypatch.setattr(integration, 'RESPONSE_TIMEOUT', .01)
    monkeypatch.setattr(integration, 'MAX_PENDING', 1)
    with pytest.raises(Exception, match='did not respond'):
        await invoke(hass, 'get_checks')
    async def relay(event):
        hass.bus.async_fire(RESPONSE_EVENT, {'request_id': event.data['request_id'], 'ok': True,
                                           'result': {'schemaVersion': 1, 'checks': []}})
    hass.bus.relay = relay
    assert (await invoke(hass, 'get_checks'))['checks'] == []


async def test_backend_error_is_not_converted_to_empty_history(hass):
    reader = AsyncMock()
    reader.get_observations.side_effect = ChecksReadError('observations_access_denied', 'Safe failure', 502)
    bridge = HomeAssistantBridge(reader, 'fake-test-token')
    async def relay(event):
        result = await bridge.dispatch({'data': event.data, 'context': {'user_id': event.context.user_id}})
        hass.bus.async_fire(RESPONSE_EVENT, result)
    hass.bus.relay = relay
    with pytest.raises(Exception, match='observations_access_denied'):
        await invoke(hass, 'get_observations', {'check_id': 'example-check'})


@pytest.mark.parametrize('params', [{'check_id': '../private'}, {'check_id': 'valid', 'page_size': True},
    {'check_id': 'valid', 'page_size': 501}, {'check_id': 'valid', 'cursor': ''},
    {'check_id': 'valid', 'bucket': 'private'}, {'check_id': 'valid', 'enabled': False}])
async def test_service_schema_has_no_mutation_or_arbitrary_s3_parameters(hass, params):
    with pytest.raises(Exception):
        await invoke(hass, 'get_observations', params)
    assert hass.bus.events == []
