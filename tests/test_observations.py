from copy import deepcopy
from io import BytesIO
import json
from unittest.mock import Mock

from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.observations import S3Observations, normalize_observation, ReadFacade
from app.s3_checks import ChecksReadError, S3Checks

BUCKET = 'example-flight-checks'
PREFIX = 'observations/check_id=example-check/'
KEY = PREFIX + 'checked_date=2026-10-19/run-0000.json'


def response(document):
    raw = json.dumps(document).encode()
    return {'Body': StreamingBody(BytesIO(raw), len(raw))}


def config(stub, document):
    stub.add_response('get_object', response(document), {'Bucket': BUCKET, 'Key': 'config/checks.json'})


def listing(stub, keys, *, after=None, truncated=False, max_keys=10):
    params = {'Bucket': BUCKET, 'Prefix': PREFIX, 'MaxKeys': max_keys}
    if after:
        params['StartAfter'] = after
    stub.add_response('list_objects_v2', {'IsTruncated': truncated, 'Contents': [{'Key': key} for key in keys]}, params)


@pytest.fixture
async def reader(s3_client):
    reader = S3Observations(S3Checks(s3_client, BUCKET))
    yield reader
    await reader.close()


def test_local_flight_timestamps_and_next_day_arrival_are_preserved(observation):
    row = normalize_observation(observation, 'example-check')[0]
    assert row['departureAt'] == '2027-02-19T23:45:00'
    assert row['arrivalAt'] == '2027-02-20T02:15:00'
    assert row['checkedDate'] == '2026-10-20'
    assert row['price'] == 196.5


@pytest.mark.parametrize('checked,expected', [('2026-07-15T22:30:00Z', '2026-07-16'),
                                             ('2026-01-15T23:30:00+00:00', '2026-01-16')])
def test_warsaw_observation_day_at_utc_boundaries(observation, checked, expected):
    observation['checkedAt'] = checked
    assert normalize_observation(observation, 'example-check')[0]['checkedDate'] == expected


@pytest.mark.parametrize('flag', ['sold_out', 'unavailable'])
def test_unavailable_fares_are_gaps_not_fake_zeros(observation, flag):
    observation['prices'][0].update({flag: True, 'price': 0, 'departure_at': None, 'arrival_at': None})
    row = normalize_observation(observation, 'example-check')[0]
    assert row['price'] is None
    assert row['departureAt'] is None


@pytest.mark.parametrize('field,value', [('price', -1), ('price', True), ('price', '123'), ('price', None),
    ('departure_at', '2027-02-19T23:45:00Z'), ('departure_at', '2027-02-18T23:45:00'),
    ('arrival_at', 'not-a-date'), ('sold_out', 0), ('unavailable', None), ('currency', 'pln')])
def test_bad_fare_rejects_snapshot(observation, field, value):
    observation['prices'][0][field] = value
    with pytest.raises(ChecksReadError) as error:
        normalize_observation(observation, 'example-check')
    assert error.value.status == 502


@pytest.mark.parametrize('field,value', [('schemaVersion', 2), ('schemaVersion', True),
    ('checkId', 'another-check'), ('checkedAt', '2026-10-19T22:30:00'),
    ('checkedAt', '2026-10-19T22:30:00+01:00'), ('prices', {}), ('prices', [None])])
def test_bad_snapshot_is_not_an_empty_result(observation, field, value):
    observation[field] = value
    with pytest.raises(ChecksReadError):
        normalize_observation(observation, 'example-check')


def test_no_cross_airport_timezone_assumptions(observation):
    observation['prices'][0]['arrival_at'] = '2027-02-19T22:30:00'
    assert normalize_observation(observation, 'example-check')[0]['arrivalAt'] == '2027-02-19T22:30:00'


def test_duplicate_departure_dates_are_rejected(observation):
    observation['prices'].append(observation['prices'][0].copy())
    with pytest.raises(ChecksReadError):
        normalize_observation(observation, 'example-check')


async def test_single_page_and_disabled_history(reader, s3_client, document, observation):
    document['checks'][0]['enabled'] = False
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY])
        raw = response(observation)
        stub.add_response('get_object', raw, {'Bucket': BUCKET, 'Key': KEY})
        page = await reader.page('example-check')
        assert len(page['observations']) == 1
        assert page['nextCursor'] is None
        assert raw['Body']._raw_stream.closed
        stub.assert_no_pending_responses()


async def test_cursor_resumes_inside_run_without_redownloading(reader, s3_client, document, observation):
    second = deepcopy(observation['prices'][0])
    second.update(departure_date='2027-02-20', departure_at='2027-02-20T23:45:00', arrival_at='2027-02-21T02:15:00')
    observation['prices'].append(second)
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY])
        stub.add_response('get_object', response(observation), {'Bucket': BUCKET, 'Key': KEY})
        first = await reader.page('example-check', 1)
        config(stub, document)
        second = await reader.page('example-check', 1, first['nextCursor'])
        assert first['observations'][0]['departureDate'] == '2027-02-19'
        assert second['observations'][0]['departureDate'] == '2027-02-20'
        config(stub, document)
        listing(stub, [], after=KEY)
        end = await reader.page('example-check', 1, second['nextCursor'])
        assert end == {'checkId': 'example-check', 'observations': [], 'nextCursor': None}
        stub.assert_no_pending_responses()


async def test_unknown_check_never_lists_observations(reader, s3_client, document):
    with Stubber(s3_client) as stub:
        config(stub, document)
        with pytest.raises(ChecksReadError) as error:
            await reader.page('missing')
        assert error.value.status == 404
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('operation', ['list_objects_v2', 'get_object'])
async def test_s3_denial_is_sanitized(reader, s3_client, document, operation):
    with Stubber(s3_client) as stub:
        config(stub, document)
        if operation == 'get_object':
            listing(stub, [KEY])
        stub.add_client_error(operation, service_error_code='AccessDenied', service_message='private-detail')
        with pytest.raises(ChecksReadError) as error:
            await reader.page('example-check')
        assert error.value.status == 502
        assert 'private-detail' not in str(error.value)


async def test_key_outside_prefix_is_never_downloaded(reader, s3_client, document):
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, ['raw/private.json'])
        with pytest.raises(ChecksReadError):
            await reader.page('example-check')
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('check,size,cursor', [('example-check', True, None), ('example-check', 501, None),
    ('../other', 10, None), ('example-check', 10, {}), ('example-check', 10, 'forged')])
async def test_invalid_parameters_never_contact_s3(reader, s3_client, check, size, cursor):
    with Stubber(s3_client):
        with pytest.raises(ChecksReadError) as error:
            await reader.page(check, size, cursor)
        assert error.value.status == 400


async def test_cursor_is_bound_to_check_size_and_server_instance(reader, s3_client):
    cursor = reader._response('example-check', [], KEY, 0, 10, True)['nextCursor']
    another = S3Observations(S3Checks(s3_client, BUCKET))
    try:
        for instance, check_id, size, token in [(reader, 'other', 10, cursor), (reader, 'example-check', 11, cursor),
            (another, 'example-check', 10, cursor), (reader, 'example-check', 10, cursor[:-2] + 'AA')]:
            with Stubber(s3_client):
                with pytest.raises(ChecksReadError) as error:
                    await instance.page(check_id, size, token)
                assert error.value.status == 400
    finally:
        await another.close()


async def test_over_one_thousand_objects_are_paginated(document, observation):
    keys = [PREFIX + f'checked_date=2026-10-19/run-{i:04}.json' for i in range(1001)]
    class Client:
        list_calls = 0
        get_calls = 0
        def get_object(self, **params):
            if params['Key'] == 'config/checks.json':
                return response(document)
            assert params['Key'] in keys
            self.get_calls += 1
            return response(observation)
        def list_objects_v2(self, **params):
            assert params['Prefix'] == PREFIX
            assert params['MaxKeys'] <= 10
            self.list_calls += 1
            start = keys.index(params['StartAfter']) + 1 if 'StartAfter' in params else 0
            batch = keys[start:start + params['MaxKeys']]
            return {'Contents': [{'Key': key} for key in batch], 'IsTruncated': start + len(batch) < len(keys)}
    client = Client()
    reader = S3Observations(S3Checks(client, BUCKET))
    count, cursor = 0, None
    try:
        for _ in range(110):
            page = await reader.page('example-check', 200, cursor)
            count += len(page['observations'])
            cursor = page['nextCursor']
            if cursor is None:
                break
        assert count == 1001
        assert client.list_calls == 101
        assert client.get_calls == 1001
        assert len(reader._cache) <= 64
    finally:
        await reader.close()


async def test_read_facade_projects_known_fields_and_filters_disabled(document):
    store = Mock()
    from unittest.mock import AsyncMock
    document['checks'][0].update(enabled=False, extension='not-for-dashboard')
    store.read = AsyncMock(return_value=document)
    facade = ReadFacade(store, Mock())
    assert (await facade.get_checks(False))['checks'] == []
    result = await facade.get_checks(True)
    assert len(result['checks']) == 1
    assert 'extension' not in result['checks'][0]
    assert not hasattr(facade, 'create')
    assert not hasattr(facade, 'set_enabled')


async def test_checked_date_filters_include_previous_utc_partition(reader, s3_client, document, observation):
    next_key = PREFIX + 'checked_date=2026-10-21/later.json'
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY, next_key], after=PREFIX + 'checked_date=2026-10-19/')
        stub.add_response('get_object', response(observation), {'Bucket': BUCKET, 'Key': KEY})
        page = await reader.page('example-check', checked_from='2026-10-20', checked_to='2026-10-20')
        assert page['observations'][0]['checkedDate'] == '2026-10-20'
        assert page['nextCursor'] is None
        stub.assert_no_pending_responses()  # later partition must not be downloaded


async def test_cursor_is_bound_to_date_filters(reader, s3_client):
    cursor = reader._response('example-check', [], KEY, 0, 10, True, '2026-10-20')['nextCursor']
    with Stubber(s3_client):
        with pytest.raises(ChecksReadError) as error:
            await reader.page('example-check', 10, cursor, checked_from='2026-10-21')
        assert error.value.status == 400


@pytest.mark.parametrize('start,end', [('2026-02-30', None), ('20261020', None), ('2026-10-20', '2026-10-19')])
async def test_invalid_checked_date_filters(reader, s3_client, start, end):
    with Stubber(s3_client):
        with pytest.raises(ChecksReadError) as error:
            await reader.page('example-check', checked_from=start, checked_to=end)
        assert error.value.status == 400


async def test_partial_s3_failure_does_not_return_partial_success(reader, s3_client, document, observation):
    second_key = KEY.replace('0000', '0001')
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY, second_key])
        stub.add_response('get_object', response(observation), {'Bucket': BUCKET, 'Key': KEY})
        stub.add_client_error('get_object', service_error_code='NoSuchKey', service_message='private-key',
                              expected_params={'Bucket': BUCKET, 'Key': second_key})
        with pytest.raises(ChecksReadError) as error:
            await reader.page('example-check')
        assert error.value.code == 'observation_missing'
        assert 'private-key' not in str(error.value)


async def test_oversized_body_is_closed_and_rejected(reader, s3_client, document, monkeypatch):
    monkeypatch.setattr('app.observations.MAX_OBJECT_BYTES', 50)
    raw = b'x' * 100
    body = StreamingBody(BytesIO(raw), len(raw))
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY])
        stub.add_response('get_object', {'Body': body}, {'Bucket': BUCKET, 'Key': KEY})
        with pytest.raises(ChecksReadError):
            await reader.page('example-check')
        assert body._raw_stream.closed


async def test_request_timeout_does_not_allow_unbounded_background_workers(reader, monkeypatch):
    import asyncio
    from threading import Event
    release = Event()
    monkeypatch.setattr('app.observations.PAGE_TIMEOUT', .01)
    def slow_read(*_args):
        release.wait(2)
        return {'observations': [], 'nextCursor': None}
    monkeypatch.setattr(reader, '_page', slow_read)
    try:
        results = await asyncio.gather(reader.page('example-check'), reader.page('example-check'), return_exceptions=True)
        assert all(isinstance(error, ChecksReadError) and error.code == 'observations_timeout' for error in results)
        with pytest.raises(ChecksReadError) as error:
            await reader.page('example-check')
        assert error.value.code == 'reader_busy'
    finally:
        release.set()


async def test_repeated_page_uses_cached_immutable_object(reader, s3_client, document, observation):
    with Stubber(s3_client) as stub:
        config(stub, document)
        listing(stub, [KEY])
        stub.add_response('get_object', response(observation), {'Bucket': BUCKET, 'Key': KEY})
        first = await reader.page('example-check')
        config(stub, document)
        listing(stub, [KEY])
        second = await reader.page('example-check')
        assert second == first
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('field,value', [('origin', None), ('origin', 'aaa'), ('destination', 'AAA'),
    ('departureDateFrom', '2028-01-01'), ('departureDateTo', '2027-02-18')])
def test_observation_route_and_departure_window_are_validated(observation, field, value):
    observation[field] = value
    with pytest.raises(ChecksReadError):
        normalize_observation(observation, 'example-check')
