from pathlib import Path

import pytest

from app.integration_install import FILES, MARKER, OWNER, SOURCE, IntegrationInstallError, install_integration


def test_installs_only_own_files_and_preserves_unrelated_files(tmp_path):
    destination = tmp_path / 'custom_components' / 'flight_price_tracker'
    install_integration(destination=destination)
    assert (destination / MARKER).read_text() == OWNER
    for name in FILES:
        assert (destination / name).read_bytes() == (SOURCE / name).read_bytes()
    (destination / 'user-notes.txt').write_text('keep me')
    install_integration(destination=destination)
    assert (destination / 'user-notes.txt').read_text() == 'keep me'


def test_unmanaged_directory_is_never_overwritten(tmp_path):
    destination = tmp_path / 'flight_price_tracker'
    destination.mkdir()
    existing = destination / '__init__.py'
    existing.write_text('unrelated user implementation')
    with pytest.raises(IntegrationInstallError, match='not managed'):
        install_integration(destination=destination)
    assert existing.read_text() == 'unrelated user implementation'
    assert not (destination / MARKER).exists()


def test_symlink_destination_is_refused(tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    destination = tmp_path / 'flight_price_tracker'
    destination.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(IntegrationInstallError):
        install_integration(destination=destination)
    assert list(elsewhere.iterdir()) == []


def test_symlink_file_is_not_overwritten(tmp_path):
    destination = tmp_path / 'flight_price_tracker'
    install_integration(destination=destination)
    target = tmp_path / 'user.py'
    target.write_text('keep')
    (destination / '__init__.py').unlink()
    (destination / '__init__.py').symlink_to(target)
    with pytest.raises(IntegrationInstallError):
        install_integration(destination=destination)
    assert target.read_text() == 'keep'


def test_card_install_is_managed_and_preserves_other_www_content(tmp_path):
    from app.integration_install import install_card
    destination = tmp_path / 'www' / 'flight-price-tracker'
    destination.parent.mkdir()
    unrelated = destination.parent / 'other-card.js'
    unrelated.write_text('keep')
    install_card(destination=destination)
    assert (destination / 'flight-price-tracker-card.js').is_file()
    assert (destination / 'flight-chart.mjs').is_file()
    assert not (destination / 'admin.html').exists()
    assert not (destination / 'airports.json').exists()
    install_card(destination=destination)
    assert unrelated.read_text() == 'keep'


def test_card_install_refuses_unmanaged_directory_and_asset_symlink(tmp_path):
    from app.integration_install import install_card
    destination = tmp_path / 'flight-price-tracker'
    destination.mkdir()
    with pytest.raises(IntegrationInstallError):
        install_card(destination=destination)
    (destination / MARKER).write_text(OWNER)
    unrelated = tmp_path / 'user.js'
    unrelated.write_text('keep')
    (destination / 'flight-chart.mjs').symlink_to(unrelated)
    with pytest.raises(IntegrationInstallError):
        install_card(destination=destination)
    assert unrelated.read_text() == 'keep'
