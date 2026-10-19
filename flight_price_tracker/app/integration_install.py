"""Install only this add-on's managed HA integration, preserving unrelated files."""

import os
from pathlib import Path
import tempfile

SOURCE = Path(__file__).resolve().parent.parent / "custom_components" / "flight_price_tracker"
DESTINATION = Path("/config/custom_components/flight_price_tracker")
OWNER = "https://github.com/Krbielec/flight-price-tracker-ha"
MARKER = ".flight-price-tracker-managed"
FILES = ("__init__.py", "manifest.json", "services.yaml")


class IntegrationInstallError(Exception):
    pass


def install_integration(source=SOURCE, destination=DESTINATION):
    destination = Path(destination)
    try:
        if destination.is_symlink() or destination.parent.is_symlink():
            raise IntegrationInstallError("Integration install path must not be a symlink.")
        if destination.exists():
            marker = destination / MARKER
            if marker.is_symlink() or not marker.is_file() or marker.read_text() != OWNER:
                raise IntegrationInstallError("Existing integration directory is not managed by this add-on.")
        content = {name: (Path(source) / name).read_bytes() for name in FILES}
        for name in (*FILES, MARKER):
            target = destination / name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise IntegrationInstallError("Integration contains an unsafe destination path.")
        destination.mkdir(parents=True, exist_ok=True)
        # Mark ownership before installing files, so interrupted installs can retry.
        _replace(destination / MARKER, OWNER.encode())
        for name, raw in content.items():
            _replace(destination / name, raw)
    except (OSError, ValueError):
        raise IntegrationInstallError("Could not install the managed HA integration.") from None


def _replace(path, raw):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
