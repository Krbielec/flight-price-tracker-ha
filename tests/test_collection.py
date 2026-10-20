from io import BytesIO
from unittest.mock import AsyncMock, Mock

import boto3
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.admin import create_app
from app.collection import CollectionError, LambdaCollector
from app.s3_checks import ChecksReadError
from app.settings import Settings


@pytest.fixture
def lambda_client():
    client = boto3.client('lambda', region_name='eu-central-1',
                           aws_access_key_id='fake-key', aws_secret_access_key='fake-secret')
    yield client
    client.close()


PARAMS = {'FunctionName': 'example-collector', 'InvocationType': 'Event', 'Payload': b'{"checkId":"new-check"}'}


async def test_single_check_is_queued_asynchronously_and_payload_closed(lambda_client):
    raw = BytesIO(b'')
    body = StreamingBody(raw, 0)
    with Stubber(lambda_client) as stub:
        stub.add_response('invoke', {'StatusCode': 202, 'Payload': body}, PARAMS)
        await LambdaCollector(lambda_client, 'example-collector').enqueue('new-check')
        assert raw.closed
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('code,safe', [('AccessDeniedException', 'lambda_access_denied'),
    ('ResourceNotFoundException', 'lambda_not_found'), ('TooManyRequestsException', 'lambda_throttled'),
    ('ServiceException', 'lambda_unconfirmed')])
async def test_lambda_failures_are_sanitized(lambda_client, code, safe):
    with Stubber(lambda_client) as stub:
        stub.add_client_error('invoke', service_error_code=code, service_message='private-test-detail', expected_params=PARAMS)
        with pytest.raises(CollectionError) as caught:
            await LambdaCollector(lambda_client, 'example-collector').enqueue('new-check')
        assert caught.value.code == safe
        assert 'private-test-detail' not in str(caught.value)
        stub.assert_no_pending_responses()


async def test_network_failure_does_not_claim_acceptance():
    client = Mock()
    client.invoke.side_effect = EndpointConnectionError(endpoint_url='https://private-test-detail.invalid')
    with pytest.raises(CollectionError) as caught:
        await LambdaCollector(client, 'example-collector').enqueue('new-check')
    assert caught.value.code == 'lambda_unconfirmed'
    assert 'private-test-detail' not in str(caught.value)
    client.invoke.assert_called_once()


async def test_unexpected_status_is_not_reported_as_queued(lambda_client):
    with Stubber(lambda_client) as stub:
        stub.add_response('invoke', {'StatusCode': 200}, PARAMS)
        with pytest.raises(CollectionError):
            await LambdaCollector(lambda_client, 'example-collector').enqueue('new-check')


def test_factory_bounds_calls_and_uses_explicit_credentials():
    collector = LambdaCollector.from_settings(Settings('eu-central-1', 'example-bucket', 'fake-key', 'fake-secret', 'example-collector'))
    assert collector.function_name == 'example-collector'
    config = collector.client.meta.config
    assert config.connect_timeout == 5 and config.read_timeout == 10
    assert config.retries['total_max_attempts'] == 1
    collector.client.close()


@pytest.fixture
async def creation(aiohttp_client, monkeypatch, document):
    monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    check = {**document['checks'][0], 'id': 'new-check'}
    store, collector = AsyncMock(), AsyncMock()
    store.create.return_value = (check, '"new"')
    client = await aiohttp_client(create_app(store, AsyncMock(), collector=collector))
    return client, store, collector, check


async def test_trigger_only_after_successful_persistence(creation):
    client, store, collector, check = creation
    order = []
    async def saved(*_args):
        order.append('saved')
        return check, '"new"'
    async def queued(*_args):
        assert order == ['saved']
        order.append('queued')
    store.create.side_effect = saved
    collector.enqueue.side_effect = queued
    reply = await client.post('/api/checks', json=check, headers={'If-Match': '"original"'})
    assert reply.status == 201
    assert await reply.json() == check
    assert reply.headers['ETag'] == '"new"'
    assert reply.headers['X-Collection-Status'] == 'queued'
    collector.enqueue.assert_awaited_once_with(check['id'])
    assert order == ['saved', 'queued']


@pytest.mark.parametrize('status', [400, 409, 502, 503])
async def test_failed_creation_never_triggers(creation, status):
    client, store, collector, check = creation
    store.create.side_effect = ChecksReadError('failure', 'Safe failure', status)
    reply = await client.post('/api/checks', json=check, headers={'If-Match': '"original"'})
    assert reply.status == status
    collector.enqueue.assert_not_awaited()


async def test_disabled_check_does_not_trigger(creation):
    client, store, collector, check = creation
    check['enabled'] = False
    reply = await client.post('/api/checks', json=check, headers={'If-Match': '"original"'})
    assert reply.status == 201 and reply.headers['X-Collection-Status'] == 'disabled'
    collector.enqueue.assert_not_awaited()


@pytest.mark.parametrize('failure,status', [(CollectionError('lambda_access_denied'), 'lambda_access_denied'),
    (CollectionError('lambda_unconfirmed'), 'lambda_unconfirmed'), (RuntimeError('private-test-detail'), 'lambda_unconfirmed')])
async def test_trigger_failure_is_still_a_successfully_created_check(creation, failure, status, caplog):
    client, store, collector, check = creation
    collector.enqueue.side_effect = failure
    reply = await client.post('/api/checks', json=check, headers={'If-Match': '"original"'})
    assert reply.status == 201 and await reply.json() == check
    assert reply.headers['X-Collection-Status'] == status
    assert 'private-test-detail' not in caplog.text + await reply.text()
    store.create.assert_awaited_once()
    collector.enqueue.assert_awaited_once()


async def test_edit_and_toggle_do_not_trigger(creation):
    client, store, collector, check = creation
    store.update.return_value = (check, '"new"')
    store.set_enabled.return_value = (check, '"new"')
    headers = {'If-Match': '"original"'}
    assert (await client.put('/api/checks/new-check', json={}, headers=headers)).status == 200
    assert (await client.patch('/api/checks/new-check/enabled', json={'enabled': True}, headers=headers)).status == 200
    collector.enqueue.assert_not_awaited()


@pytest.mark.parametrize('direct', [False, True])
async def test_unauthorized_create_never_triggers(aiohttp_client, monkeypatch, direct):
    store, collector = AsyncMock(), AsyncMock()
    if not direct:
        monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    client = await aiohttp_client(create_app(store, collector=collector))
    reply = await client.post('/api/checks', json={}, headers={'X-Hass-Is-Admin': 'true'})
    assert reply.status == 403
    store.create.assert_not_awaited()
    collector.enqueue.assert_not_awaited()


async def test_cleanup_closes_lambda_client():
    store, collector = AsyncMock(), AsyncMock()
    app = create_app(store, AsyncMock(), collector=collector)
    app.freeze()
    await app.cleanup()
    collector.close.assert_awaited_once()


@pytest.mark.parametrize('conflict', [False, True])
async def test_real_s3_and_lambda_adapters_obey_creation_boundary(aiohttp_client, monkeypatch, s3_client, lambda_client, document, new_check, conflict):
    from app.contracts import encode_json
    from app.s3_checks import S3Checks
    monkeypatch.setattr('app.admin.INGRESS_PROXY_IP', '127.0.0.1')
    store = S3Checks(s3_client, 'example-flight-checks')
    collector = LambdaCollector(lambda_client, 'example-collector')
    client = await aiohttp_client(create_app(store, AsyncMock(), collector=collector))
    params = {'Bucket': 'example-flight-checks', 'Key': 'config/checks.json'}
    raw = encode_json(document).encode()
    updated = {**document, 'checks': [*document['checks'], {**new_check, 'enabled': True}]}
    put = {**params, 'Body': encode_json(updated).encode(), 'ContentType': 'application/json', 'IfMatch': '"original"'}
    with Stubber(s3_client) as s3, Stubber(lambda_client) as invocation:
        s3.add_response('get_object', {'Body': StreamingBody(BytesIO(raw), len(raw)), 'ETag': '"original"'}, params)
        if conflict:
            s3.add_client_error('put_object', service_error_code='PreconditionFailed', expected_params=put)
        else:
            s3.add_response('put_object', {'ETag': '"new"'}, put)
            invocation.add_response('invoke', {'StatusCode': 202}, PARAMS)
        reply = await client.post('/api/checks', json=new_check, headers={'If-Match': '"original"'})
        assert reply.status == (409 if conflict else 201)
        if not conflict:
            assert reply.headers['X-Collection-Status'] == 'queued'
        s3.assert_no_pending_responses()
        invocation.assert_no_pending_responses()
