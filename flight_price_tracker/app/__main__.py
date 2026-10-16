"""Start the add-on using Supervisor-managed options."""

import logging
from pathlib import Path

from aiohttp import web
from botocore.exceptions import BotoCoreError

from .admin import create_app
from .s3_checks import S3Checks
from .settings import Settings, SettingsError

LOG = logging.getLogger(__name__)
OPTIONS_PATH = Path("/data/options.json")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        settings = Settings.load(OPTIONS_PATH)
        store = S3Checks.from_settings(settings)
    except SettingsError as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None
    except (BotoCoreError, ValueError):
        LOG.error("Could not initialize the S3 client. Check add-on options.")
        raise SystemExit(1) from None
    # aiohttp manages SIGTERM/SIGINT, server shutdown and cleanup callbacks.
    # Disable access logs to avoid recording Ingress URLs/headers.
    web.run_app(create_app(store), host="0.0.0.0", port=8099, access_log=None, print=None)


if __name__ == "__main__":
    main()
