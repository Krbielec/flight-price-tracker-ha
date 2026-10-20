from unittest.mock import Mock

from botocore.exceptions import EndpointConnectionError
import pytest

from app import __main__ as entry
from app.settings import Settings, SettingsError


def test_startup_configuration_failure_is_sanitized(monkeypatch, caplog):
    monkeypatch.setattr(entry.Settings, "load", Mock(side_effect=SettingsError("Required option is missing.")))
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 1
    assert "Required option is missing." in caplog.text


def test_sdk_initialization_failure_is_sanitized(monkeypatch, caplog):
    monkeypatch.setattr(entry.Settings, "load", Mock(return_value=Settings(
        "eu-central-1", "example-flight-checks", "fake-test-key", "fake-test-secret")))
    monkeypatch.setattr(entry.S3Checks, "from_settings", Mock(side_effect=EndpointConnectionError(
        endpoint_url="https://private-test-detail.invalid")))
    with pytest.raises(SystemExit):
        entry.main()
    assert "private-test-detail" not in caplog.text
    assert "fake-test-secret" not in caplog.text


def test_main_starts_ingress_server(monkeypatch):
    settings = Settings("eu-central-1", "example-flight-checks", "fake-test-key", "fake-test-secret")
    store = Mock()
    run_app = Mock()
    monkeypatch.setattr(entry.LambdaCollector, "from_settings", Mock())
    monkeypatch.setattr(entry.Settings, "load", Mock(return_value=settings))
    monkeypatch.setattr(entry.S3Checks, "from_settings", Mock(return_value=store))
    monkeypatch.setattr(entry.web, "run_app", run_app)
    entry.main()
    _, kwargs = run_app.call_args
    assert kwargs == dict(host="0.0.0.0", port=8099, access_log=None, print=None)


async def test_runtime_lifecycle_stops_bridge_before_closing_storage(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    order = []
    started = asyncio.Event()
    async def run_bridge():
        order.append('bridge-start')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append('bridge-stop')
    async def close_observations():
        order.append('observations-close')
    async def close_store():
        order.append('store-close')
    store, observations, bridge = AsyncMock(), AsyncMock(), Mock()
    store.close.side_effect = close_store
    observations.close.side_effect = close_observations
    bridge.run = run_bridge
    monkeypatch.setattr(entry, 'S3Observations', Mock(return_value=observations))
    monkeypatch.setattr(entry, 'HomeAssistantBridge', Mock(return_value=bridge))
    monkeypatch.setattr(entry, 'install_card', Mock())
    monkeypatch.setattr(entry, 'install_integration', Mock())
    app = entry.create_runtime_app(store)
    app.freeze()
    await app.startup()
    await asyncio.wait_for(started.wait(), 1)
    await app.cleanup()
    assert order == ['bridge-start', 'bridge-stop', 'observations-close', 'store-close']


async def test_unmanaged_integration_keeps_admin_app_available(monkeypatch):
    from unittest.mock import AsyncMock
    from app.integration_install import IntegrationInstallError
    store, observations = AsyncMock(), AsyncMock()
    bridge_factory = Mock()
    monkeypatch.setattr(entry, 'S3Observations', Mock(return_value=observations))
    monkeypatch.setattr(entry, 'HomeAssistantBridge', bridge_factory)
    monkeypatch.setattr(entry, 'install_card', Mock())
    monkeypatch.setattr(entry, 'install_integration', Mock(side_effect=IntegrationInstallError('Unmanaged directory')))
    app = entry.create_runtime_app(store)
    app.freeze()
    await app.startup()
    bridge_factory.assert_not_called()
    await app.cleanup()
    observations.close.assert_awaited_once()
    store.close.assert_awaited_once()


async def test_card_install_failure_does_not_stop_read_bridge(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app.integration_install import IntegrationInstallError
    store, observations = AsyncMock(), AsyncMock()
    started = asyncio.Event()
    async def run_bridge():
        started.set()
        await asyncio.Event().wait()
    bridge = Mock()
    bridge.run = run_bridge
    monkeypatch.setattr(entry, 'S3Observations', Mock(return_value=observations))
    monkeypatch.setattr(entry, 'HomeAssistantBridge', Mock(return_value=bridge))
    monkeypatch.setattr(entry, 'install_card', Mock(side_effect=IntegrationInstallError('Unmanaged directory')))
    monkeypatch.setattr(entry, 'install_integration', Mock())
    app = entry.create_runtime_app(store)
    app.freeze()
    await app.startup()
    await asyncio.wait_for(started.wait(), 1)
    await app.cleanup()
    observations.close.assert_awaited_once()
    store.close.assert_awaited_once()
