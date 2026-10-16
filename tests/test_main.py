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
    monkeypatch.setattr(entry.Settings, "load", Mock(return_value=settings))
    monkeypatch.setattr(entry.S3Checks, "from_settings", Mock(return_value=store))
    monkeypatch.setattr(entry.web, "run_app", run_app)
    entry.main()
    _, kwargs = run_app.call_args
    assert kwargs == dict(host="0.0.0.0", port=8099, access_log=None, print=None)
