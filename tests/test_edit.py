"""Edits stay admin-only and update just three fields under S3 optimistic concurrency."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock

from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.admin import create_app
from app.authorization import AuthorizationError
from app.contracts import ContractError, encode_json, validate_check_edit
from app.s3_checks import CHECKS_KEY, S3Checks

TODAY = date(2026, 10, 19)
PARAMS = {'Bucket': 'example-flight-checks', 'Key': CHECKS_KEY}
HEADERS = {'If-Match': '"original"'}


def changes(**kwargs):
    return {'departureDateFrom': '2027-02-14', 'departureDateTo': '2027-02-24', 'enabled': False, **kwargs}


def snapshot(document, etag='"original"'):
    raw = encode_json(document).encode()
    return {'Body': StreamingBody(BytesIO(raw), len(raw)), 'ETag': etag}


def put_params(document):
    return {**PARAMS, 'Body': encode_json(document).encode(), 'ContentType': 'application/json', 'IfMatch': '"original"'}


@pytest.fixture
async def edit_client(aiohttp_client, monkeypatch, s3_client):
    monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    monkeypatch.setattr('app.contracts.current_date', lambda: TODAY)
    return await aiohttp_client(create_app(S3Checks(s3_client, PARAMS['Bucket']), AsyncMock()))


async def test_edit_preserves_other_checks_extensions_precision_and_immutable_fields(edit_client, s3_client, document):
    document['extension'] = Decimal('0.12345678901234567890123456789')
    document['checks'][0]['privateExtension'] = {'preserve': True}
    document['checks'].append({**document['checks'][0], 'id': 'other'})
    expected = deepcopy(document)
    expected['checks'][0].update(changes())
    with Stubber(s3_client) as stub:
        stub.add_response('get_object', snapshot(document), PARAMS)
        stub.add_response('put_object', {'ETag': '"new"'}, put_params(expected))
        reply = await edit_client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
        assert reply.status == 200
        assert await reply.json() == expected['checks'][0]
        assert reply.headers['ETag'] == '"new"'
        assert reply.headers['Cache-Control'] == 'no-store'
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('payload', [None, [], {}, {'enabled': False}, changes(enabled=1),
    changes(enabled='false'), changes(departureDateFrom='2027-02-30'),
    changes(departureDateFrom='2027-02-25'), changes(departureDateTo=None),
    changes(id='another'), changes(origin='CCC'), changes(destination='DDD'),
    changes(currency='EUR'), changes(extra='value')])
async def test_invalid_edits_never_read_s3(edit_client, s3_client, payload):
    with Stubber(s3_client):
        reply = await edit_client.put('/api/checks/example-check', data=encode_json(payload),
            headers={**HEADERS, 'Content-Type': 'application/json'})
        assert reply.status == 400


@pytest.mark.parametrize('etag', [None, '*', 'W/"original"', '"one", "two"'])
async def test_edit_requires_strong_version(edit_client, s3_client, etag):
    with Stubber(s3_client):
        reply = await edit_client.put('/api/checks/example-check', json=changes(),
                                      headers={'If-Match': etag} if etag else {})
        assert reply.status == 400


@pytest.mark.parametrize('body,content_type', [('{', 'application/json'),
    ('{"enabled":true,"enabled":false}', 'application/json'), ('enabled=false', 'application/x-www-form-urlencoded')])
async def test_edit_rejects_ambiguous_json_and_forms(edit_client, s3_client, body, content_type):
    with Stubber(s3_client):
        reply = await edit_client.put('/api/checks/example-check', data=body,
            headers={**HEADERS, 'Content-Type': content_type})
        assert reply.status == 400


async def test_stale_edit_does_not_write(edit_client, s3_client, document):
    with Stubber(s3_client) as stub:
        stub.add_response('get_object', snapshot(document, '"changed"'), PARAMS)
        reply = await edit_client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
        assert reply.status == 409
        stub.assert_no_pending_responses()


async def test_unknown_id_does_not_create_check(edit_client, s3_client, document):
    with Stubber(s3_client) as stub:
        stub.add_response('get_object', snapshot(document), PARAMS)
        reply = await edit_client.put('/api/checks/missing', json=changes(), headers=HEADERS)
        assert reply.status == 404
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('code,status', [('PreconditionFailed', 409), ('ConditionalRequestConflict', 409),
    ('AccessDenied', 502), ('InternalError', 503)])
async def test_concurrent_write_and_s3_errors_are_safe(edit_client, s3_client, document, code, status):
    expected = deepcopy(document)
    expected['checks'][0].update(changes())
    with Stubber(s3_client) as stub:
        stub.add_response('get_object', snapshot(document), PARAMS)
        stub.add_client_error('put_object', service_error_code=code, service_message='private-test-detail',
                              expected_params=put_params(expected))
        reply = await edit_client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
        assert reply.status == status
        assert 'private-test-detail' not in await reply.text()
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('code', ['NoSuchKey', 'AccessDenied'])
async def test_unreadable_config_is_never_replaced(edit_client, s3_client, code):
    with Stubber(s3_client) as stub:
        stub.add_client_error('get_object', service_error_code=code, expected_params=PARAMS)
        reply = await edit_client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
        assert reply.status == 502
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('status', [403, 503])
async def test_edit_requires_live_admin_authorization(aiohttp_client, monkeypatch, status):
    monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    store, authorizer = AsyncMock(), AsyncMock()
    authorizer.require_admin.side_effect = AuthorizationError(status, 'denied', 'Denied')
    client = await aiohttp_client(create_app(store, authorizer))
    reply = await client.put('/api/checks/example-check', json=changes(),
                              headers={**HEADERS, 'X-Remote-User-Id': 'viewer'})
    assert reply.status == status
    store.update.assert_not_awaited()


async def test_direct_peer_and_forged_headers_cannot_edit(aiohttp_client):
    store, authorizer = AsyncMock(), AsyncMock()
    client = await aiohttp_client(create_app(store, authorizer))
    reply = await client.put('/api/checks/example-check', json=changes(), headers={**HEADERS,
        'X-Remote-User-Id': 'admin', 'X-Forwarded-For': '172.30.32.2', 'X-Hass-Is-Admin': 'true'})
    assert reply.status == 403
    store.update.assert_not_awaited()
    authorizer.require_admin.assert_not_awaited()


async def test_missing_identity_cannot_edit(aiohttp_client, monkeypatch):
    monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    store = AsyncMock()
    client = await aiohttp_client(create_app(store))
    reply = await client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
    assert reply.status == 403
    store.update.assert_not_awaited()


@pytest.mark.parametrize('first,last', [(0, 0), (730, 730), (0, 365)])
def test_edit_date_limits_inclusive(document, first, last):
    payload = changes(departureDateFrom=(TODAY + timedelta(days=first)).isoformat(),
                      departureDateTo=(TODAY + timedelta(days=last)).isoformat())
    assert validate_check_edit(payload, document['checks'][0], today=TODAY) == payload


@pytest.mark.parametrize('first,last', [(-1, 0), (730, 731), (0, 366)])
def test_edit_rejects_new_out_of_bounds_dates(document, first, last):
    payload = changes(departureDateFrom=(TODAY + timedelta(days=first)).isoformat(),
                      departureDateTo=(TODAY + timedelta(days=last)).isoformat())
    with pytest.raises(ContractError):
        validate_check_edit(payload, document['checks'][0], today=TODAY)


def test_unchanged_historical_dates_and_legacy_id_remain_editable(document):
    existing = {**document['checks'][0], 'id': 'Legacy_ID', 'departureDateFrom': '2025-01-01', 'departureDateTo': '2025-01-08'}
    payload = {key: existing[key] for key in ('departureDateFrom', 'departureDateTo', 'enabled')}
    payload['enabled'] = False
    assert validate_check_edit(payload, existing, today=TODAY) == payload
    # Preserving a recently started range can still extend future collection.
    existing.update(departureDateFrom='2026-10-18', departureDateTo='2026-10-20')
    assert validate_check_edit(changes(departureDateFrom='2026-10-18', departureDateTo='2026-10-25'), existing, today=TODAY)


@pytest.mark.parametrize('stored,etag', [({'schemaVersion': 2, 'checks': []}, '"original"'),
    ({'schemaVersion': 1, 'checks': []}, None)])
async def test_invalid_stored_config_or_missing_etag_never_writes(edit_client, s3_client, stored, etag):
    with Stubber(s3_client) as stub:
        response = snapshot(stored)
        if etag is None:
            response.pop('ETag')
        stub.add_response('get_object', response, PARAMS)
        reply = await edit_client.put('/api/checks/example-check', json=changes(), headers=HEADERS)
        assert reply.status == 502
        stub.assert_no_pending_responses()


async def test_legacy_id_and_past_window_can_toggle_via_editor(edit_client, s3_client, document):
    document['checks'][0].update(id='Legacy_ID', departureDateFrom='2025-01-01', departureDateTo='2025-01-08')
    payload = changes(departureDateFrom='2025-01-01', departureDateTo='2025-01-08')
    expected = deepcopy(document)
    expected['checks'][0].update(payload)
    with Stubber(s3_client) as stub:
        stub.add_response('get_object', snapshot(document), PARAMS)
        stub.add_response('put_object', {'ETag': '"new"'}, put_params(expected))
        reply = await edit_client.put('/api/checks/Legacy_ID', json=payload, headers=HEADERS)
        assert reply.status == 200
        stub.assert_no_pending_responses()
