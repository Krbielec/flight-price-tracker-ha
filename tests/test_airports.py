import importlib.util
import json
from pathlib import Path
import re

import pytest


CATALOG = Path('flight_price_tracker/www/airports.json')


def test_catalog_has_unique_iata_codes_and_city_labels():
    airports = json.loads(CATALOG.read_text())
    codes = [airport['code'] for airport in airports]
    assert len(codes) > 8000
    assert len(set(codes)) == len(codes)
    assert codes == sorted(codes)
    assert all(re.fullmatch('[A-Z]{3}', code) for code in codes)
    assert all(isinstance(a['city'], str) and a['city'].strip() and isinstance(a['name'], str) for a in airports)
    by_code = {a['code']: a for a in airports}
    assert by_code['VLC']['city'] == 'Valencia'
    assert by_code['KRK']['city'] == 'Kraków'


def builder():
    spec = importlib.util.spec_from_file_location('airport_builder', 'scripts/build_airport_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_catalog


def test_catalog_generation_excludes_non_airports_and_codes_without_iata():
    build = builder()
    common = dict(type='medium_airport', municipality='Valencia', name='Valencia Airport', iata_code='VLC')
    rows = [common, {**common, 'type': 'closed', 'iata_code': 'OLD'},
            {**common, 'iata_code': ''}, {**common, 'type': 'heliport', 'iata_code': 'HEL'},
            {**common, 'iata_code': 'bad'}, {**common, 'type': 'seaplane_base', 'iata_code': 'SEA'}]
    assert build(rows) == [{'code': 'VLC', 'city': 'Valencia', 'name': 'Valencia Airport'}]


def test_duplicate_codes_require_manual_review():
    row = dict(type='medium_airport', municipality='Valencia', name='Valencia Airport', iata_code='VLC')
    with pytest.raises(ValueError, match='Duplicate IATA'):
        builder()([row, row.copy()])
