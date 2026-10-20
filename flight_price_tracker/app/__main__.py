"""Start the add-on using Supervisor-managed options."""

import asyncio
import logging
from pathlib import Path

from aiohttp import web
from botocore.exceptions import BotoCoreError

from .admin import create_app
from .collection import LambdaCollector
from .s3_checks import S3Checks
from .observations import ReadFacade, S3Observations
from .ha_bridge import HomeAssistantBridge
from .integration_install import IntegrationInstallError, install_card, install_integration
from .settings import Settings, SettingsError

LOG = logging.getLogger(__name__)
OPTIONS_PATH = Path("/data/options.json")


def create_runtime_app(store, collector=None):
    observations = S3Observations(store)
    app = create_app(store, observations=observations, collector=collector)
    bridge_task = None

    async def start_bridge(_app):
        nonlocal bridge_task
        try:
            await asyncio.to_thread(install_card)
        except IntegrationInstallError:
            LOG.error("Lovelace card install failed; check for an unmanaged destination directory")
        try:
            await asyncio.to_thread(install_integration)
        except IntegrationInstallError as exc:
            LOG.error("HA integration install failed: %s", exc)
            return
        LOG.info("Installed managed Flight Price Tracker integration files")
        bridge = HomeAssistantBridge(ReadFacade(store, observations))
        bridge_task = asyncio.create_task(bridge.run())

    async def stop_bridge(_app):
        if bridge_task is not None:
            bridge_task.cancel()
            await asyncio.gather(bridge_task, return_exceptions=True)

    app.on_startup.append(start_bridge)
    app.on_cleanup.insert(0, stop_bridge)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        settings = Settings.load(OPTIONS_PATH)
        store = S3Checks.from_settings(settings)
        collector = LambdaCollector.from_settings(settings)
    except SettingsError as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None
    except (BotoCoreError, ValueError):
        LOG.error("Could not initialize AWS clients. Check add-on options.")
        raise SystemExit(1) from None
    # aiohttp manages SIGTERM/SIGINT, server shutdown and cleanup callbacks.
    # Disable access logs to avoid recording Ingress URLs/headers.
    web.run_app(create_runtime_app(store, collector), host="0.0.0.0", port=8099, access_log=None, print=None)


if __name__ == "__main__":
    main()
