from pathlib import Path

import yaml


def test_addon_exposes_only_ingress():
    config = yaml.safe_load(Path("flight_price_tracker/config.yaml").read_text())
    assert config["ingress"] is True
    assert config["panel_admin"] is True
    assert config["ingress_port"] == 8099
    assert set(config["arch"]) == {"amd64", "aarch64"}
    assert "ports" not in config
    assert "map" not in config
    assert config["homeassistant_api"] is True
    for field in ("aws_access_key_id", "aws_secret_access_key"):
        assert config["options"][field] == ""
        assert config["schema"][field] == "password"
