"""Offline admin edit smoke: synthetic configuration/ETags, no HA or AWS traffic."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent / 'flight_price_tracker' / 'www'


async def main():
    check = {'id': 'example-check', 'origin': 'KRK', 'destination': 'VLC', 'currency': 'PLN',
             'departureDateFrom': '2027-02-14', 'departureDateTo': '2027-02-21', 'enabled': True}
    document = {'schemaVersion': 1, 'checks': [check, {**check, 'id': 'other-check'}]}
    etag = '"original"'
    calls = []
    pending = asyncio.Event()
    release = asyncio.Event()
    hold = False
    lose_response = False
    catalog_available = False
    next_collection_status = 'queued'
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def serve(route):
            nonlocal etag, hold, lose_response
            path = urlsplit(route.request.url).path.removeprefix('/ingress/example/')
            if path in ('', 'admin.html', 'admin-helpers.mjs'):
                name = path or 'admin.html'
                await route.fulfill(body=(ROOT / name).read_text(), content_type='text/html' if name.endswith('.html') else 'text/javascript')
            elif path == 'airports.json':
                # Editing historical checks must work independently of the airport catalogue.
                if catalog_available:
                    await route.fulfill(json=[{'code': 'KRK', 'city': 'Kraków', 'name': 'Kraków Airport'},
                                               {'code': 'VLC', 'city': 'Valencia', 'name': 'Valencia Airport'}])
                else:
                    await route.fulfill(status=503, json={'error': 'unavailable'})
            elif path == 'api/checks' and route.request.method == 'GET':
                await route.fulfill(json=document, headers={'ETag': etag})
            elif path == 'api/checks' and route.request.method == 'POST':
                created = route.request.post_data_json
                document['checks'].append(created)
                etag = '"created"'
                await route.fulfill(status=201, json=created,
                    headers={'ETag': etag, 'X-Collection-Status': next_collection_status})
            elif path == 'api/checks/example-check' and route.request.method == 'PUT':
                body = route.request.post_data_json
                version = route.request.headers.get('if-match')
                calls.append((deepcopy(body), version))
                if hold:
                    pending.set()
                    await release.wait()
                    hold = False
                if version != etag:
                    await route.fulfill(status=409, json={'error': {'message': 'Configuration changed.'}})
                    return
                if body['departureDateFrom'] > body['departureDateTo']:
                    await route.fulfill(status=400, json={'error': {'message': 'Check date range is reversed.'}})
                    return
                assert set(body) == {'departureDateFrom', 'departureDateTo', 'enabled'}
                check.update(body)
                etag = '"saved"'
                if lose_response:
                    lose_response = False
                    await route.abort('failed')
                else:
                    await route.fulfill(json=check, headers={'ETag': etag})
            else:
                raise AssertionError(f'Unexpected admin request: {route.request.method} {path}')

        await page.route('http://admin.test/**', serve)
        await page.goto('http://admin.test/ingress/example/')
        await page.wait_for_function("document.querySelectorAll('#checks tr').length === 2")
        await page.get_by_role('button', name='Edit example-check', exact=True).click()
        assert await page.locator('#create-fields').evaluate('el => el.disabled')
        assert not await page.locator('#edit-fields').evaluate('el => el.disabled')
        assert 'KRK' in await page.locator('#edit-identity').inner_text()
        assert not await page.locator('#edit-form [name="origin"]').count()
        await page.locator('#edit-form [name="departureDateTo"]').fill('2027-02-25')
        # Another writer changes the config; refreshing must not rebase the open draft.
        catalog_available = True
        etag = '"changed"'
        document['checks'][1]['enabled'] = False
        await page.locator('#refresh').click()
        await page.wait_for_function("!document.querySelector('#refresh').disabled")
        assert await page.locator('#edit-form [name="departureDateTo"]').input_value() == '2027-02-25'
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.wait_for_function("document.querySelector('#status').textContent.includes('Configuration changed')")
        assert calls[-1][1] == '"original"'
        assert await page.locator('#edit-section').is_visible()
        assert await page.locator('#edit-fields').evaluate('el => el.disabled')
        assert await page.locator('#edit-form [name="departureDateTo"]').input_value() == '2027-02-25'
        # Explicit reload discards the draft and captures the new version.
        await page.locator('#refresh').click()
        await page.wait_for_function("!document.querySelector('#edit-reload').disabled")
        await page.locator('#edit-reload').click()
        assert await page.locator('#edit-form [name="departureDateTo"]').input_value() == '2027-02-21'
        await page.locator('#edit-form [name="departureDateTo"]').fill('2027-02-25')
        await page.locator('#edit-form [name="enabled"]').uncheck()
        await page.wait_for_function("!document.querySelector('#create-fields').disabled")
        hold = True
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await asyncio.wait_for(pending.wait(), 3)
        assert await page.locator('#edit-fields').evaluate('el => el.disabled')
        assert await page.locator('#edit-cancel').is_disabled()
        assert await page.locator('#create-fields').evaluate('el => el.disabled')
        await page.locator('[data-sort="id"]').click()
        assert await page.locator('#checks button:not(:disabled)').count() == 0
        release.set()
        await page.wait_for_function("document.querySelector('#edit-section').hidden && !document.querySelector('#refresh').disabled")
        assert check['departureDateTo'] == '2027-02-25' and check['enabled'] is False
        assert document['checks'][1]['enabled'] is False
        assert await page.locator('[data-sort="id"]').evaluate("el => el.closest('th').getAttribute('aria-sort')") == 'ascending'
        # Validation errors retain input; refresh alone does not overwrite the draft.
        await page.get_by_role('button', name='Edit example-check', exact=True).click()
        await page.locator('#edit-form [name="departureDateFrom"]').fill('2027-02-26')
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.wait_for_function("document.querySelector('#status').textContent.includes('reversed')")
        await page.locator('#refresh').click()
        await page.wait_for_function("!document.querySelector('#edit-reload').disabled")
        assert await page.locator('#edit-form [name="departureDateFrom"]').input_value() == '2027-02-26'
        await page.locator('#edit-reload').click()
        # Lost reply after a successful write must retain input and require refresh.
        await page.locator('#edit-form [name="departureDateTo"]').fill('2027-02-27')
        lose_response = True
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.wait_for_function("document.querySelector('#status').textContent.includes('Refresh before trying again')")
        assert check['departureDateTo'] == '2027-02-27'
        assert await page.locator('#edit-section').is_visible()
        assert await page.locator('#edit-form [name="departureDateTo"]').input_value() == '2027-02-27'
        await page.locator('#refresh').click()
        await page.wait_for_function("!document.querySelector('#edit-reload').disabled")
        await page.locator('#edit-reload').click()
        assert await page.locator('#edit-form [name="departureDateTo"]').input_value() == '2027-02-27'
        before = len(calls)
        await page.locator('#edit-cancel').click()
        assert await page.locator('#edit-section').is_hidden()
        assert len(calls) == before
        # Creation status is independent of persistence: even denied collection resets a saved form.
        await page.locator('#create-section summary').click()
        for index, (collection_status, message) in enumerate([
            ('queued', 'Initial collection queued'),
            ('lambda_access_denied', 'initial collection was denied'),
            ('disabled', 'no initial collection requested'),
        ]):
            next_collection_status = collection_status
            await page.locator('#create-form [name="id"]').fill(f'created-check-{index}')
            await page.locator('#origin').select_option('KRK')
            await page.locator('#destination').select_option('VLC')
            await page.locator('#create-form [name="departureDateFrom"]').fill('2027-02-14')
            await page.locator('#create-form [name="departureDateTo"]').fill('2027-02-21')
            await page.locator('#create-form [name="enabled"]').set_checked(collection_status != 'disabled')
            await page.locator('#create-form button[type="submit"]').click()
            await page.wait_for_function("message => document.querySelector('#status').textContent.includes(message)", arg=message)
            assert await page.locator('#create-form [name="id"]').input_value() == ''
        assert len(document['checks']) == 5
        assert not errors, errors
        await browser.close()
        print('PASS: edit form, stale draft across Refresh, conflict/reload, locking, sorting, validation, lost reply, cancel, initial collection feedback')


if __name__ == '__main__':
    asyncio.run(main())
