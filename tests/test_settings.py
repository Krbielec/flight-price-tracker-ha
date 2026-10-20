import json

import pytest

from app.settings import Settings, SettingsError


@pytest.fixture
def options():
    return dict(aws_region="eu-central-1", s3_bucket="example-flight-checks",
                aws_access_key_id="fake-test-key", aws_secret_access_key="fake-test-secret")


def test_load_and_redact_repr(tmp_path, options):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    settings = Settings.load(path)
    assert settings.s3_bucket == options["s3_bucket"]
    assert settings.aws_secret_access_key == options["aws_secret_access_key"]
    assert options["aws_access_key_id"] not in repr(settings)
    assert options["aws_secret_access_key"] not in repr(settings)


@pytest.mark.parametrize("key", ["aws_region", "s3_bucket", "aws_access_key_id", "aws_secret_access_key"])
@pytest.mark.parametrize("value", [None, "", "   ", 123])
def test_invalid_required_option(tmp_path, options, key, value):
    options[key] = value
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    with pytest.raises(SettingsError) as error:
        Settings.load(path)
    assert options["aws_secret_access_key"] != str(error.value)
    assert "fake-test-secret" not in str(error.value)


@pytest.mark.parametrize("content", ["{", "[]", '"fake-test-secret"'])
def test_bad_options_are_sanitized(tmp_path, content):
    path = tmp_path / "options.json"
    path.write_text(content)
    with pytest.raises(SettingsError) as error:
        Settings.load(path)
    assert "fake-test-secret" not in str(error.value)


def test_missing_options(tmp_path):
    with pytest.raises(SettingsError):
        Settings.load(tmp_path / "missing.json")


@pytest.mark.parametrize("key,value", [("aws_region", "https://example.invalid"),
                                        ("s3_bucket", "Invalid/Bucket"),
                                        ("s3_bucket", "127.0.0.1")])
def test_invalid_region_or_bucket(tmp_path, options, key, value):
    options[key] = value
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    with pytest.raises(SettingsError):
        Settings.load(path)


@pytest.mark.parametrize('name', [None, '', ' ', 'a' * 65, 'arn:aws:lambda:region:account:function:x', 'https://private.invalid'])
def test_invalid_collector_name_is_sanitized(tmp_path, options, name):
    options['collector_function_name'] = name
    path = tmp_path / 'options.json'
    path.write_text(json.dumps(options))
    with pytest.raises(SettingsError) as error:
        Settings.load(path)
    assert 'private.invalid' not in str(error.value)


def test_collector_name_default_and_override(tmp_path, options):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps(options))
    assert Settings.load(path).collector_function_name == 'flight-price-tracker-collector'
    options['collector_function_name'] = 'example-collector'
    path.write_text(json.dumps(options))
    assert Settings.load(path).collector_function_name == 'example-collector'
