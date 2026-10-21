"""Offline Chromium smoke test. Run: python tests/browser_card_smoke.py.

Install requirements-browser.txt and run `python -m playwright install chromium` first.
All HA responses are synthetic. No live HA, S3, credentials or external assets.
"""
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent / 'flight_price_tracker' / 'www'


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        failures = []
        page.on('pageerror', lambda error: failures.append(str(error)))

        async def serve(route):
            name = urlsplit(route.request.url).path.rsplit('/', 1)[-1]
            if name in ('flight-price-tracker-card.js', 'flight-chart.mjs'):
                await route.fulfill(body=(ROOT / name).read_text(), content_type='text/javascript')
            else:
                await route.fulfill(body='<html><body></body></html>', content_type='text/html')
        await page.route('http://card.test/**', serve)
        await page.goto('http://card.test/')
        await page.evaluate('''async () => {
          document.documentElement.style.cssText = '--primary-text-color:#202124; --secondary-text-color:#60656f; --divider-color:#d4d8de; --secondary-background-color:#f3f4f6; --card-background-color:#fff; --primary-color:#3367d6; font-family:system-ui;';
          const realTimeout = window.setTimeout.bind(window);
          window.setTimeout = (callback, delay, ...args) => {
            if (delay === 60000) {window.nextPoll = callback; return 123456;}
            return realTimeout(callback, delay, ...args);
          };
          window.checkCount = 6; window.failCheck = null; window.failList = false;
          window.calls = [];
          window.hassMock = {user: {id: 'viewer'}, callWS: async (call) => {
            window.calls.push(call);
            if (call.service === 'get_checks') {
              if (window.failList) throw new Error('private transport detail');
              return {response: {schemaVersion: 1, checks: Array.from({length: window.checkCount}, (_, i) => ({
                id: `check-${i}`, origin: 'KRK', destination: 'VLC', currency: 'PLN',
                departureDateFrom: '2027-02-19', departureDateTo: '2027-02-21', enabled: i !== 0,
              })).filter(c => call.service_data.include_disabled || c.enabled)}};
            }
            if (call.service_data.check_id === window.failCheck) throw new Error('private transport detail');
            const row = {checkedAt: '2026-10-19T10:00:00+00:00', checkedDate: '2026-10-19',
              departureDate: '2027-02-19', departureAt: '2027-02-19T23:45:00', arrivalAt: '2027-02-20T02:15:00',
              price: 196, currency: 'PLN', soldOut: false, unavailable: false};
            return {response: {checkId: call.service_data.check_id, observations: window.historyRows || [row,
              {...row, departureDate: '2027-02-20', price: null, currency: null, unavailable: true}], nextCursor: null}};
          }};
          await import('/flight-price-tracker-card.js');
          window.card = document.createElement('flight-price-tracker-card');
          card.setConfig({type: 'custom:flight-price-tracker-card'});
          card.hass = window.hassMock;
          document.body.append(card);
        }''')
        await page.wait_for_function("card.shadowRoot.querySelectorAll('circle').length === 12 && !card._busy")
        assert await page.locator('flight-price-tracker-card section').count() == 6
        heading = page.locator('flight-price-tracker-card section').first.locator('.check-heading')
        assert await heading.locator('strong').inner_text() == 'KRK → VLC'
        assert '2027-02-19 – 2027-02-21 · check-0' in await heading.inner_text()
        assert await heading.evaluate('(node) => node.scrollHeight <= node.clientHeight + 2')
        await page.locator('flight-price-tracker-card circle').first.tap()
        assert '2027-02-20 02:15:00' in await page.locator('flight-price-tracker-card .detail').first.inner_text()
        assert '196.00 PLN' in await page.locator('flight-price-tracker-card .detail').first.inner_text()
        await page.locator('flight-price-tracker-card [data-series="2027-02-19"]').first.click()
        assert await page.locator('flight-price-tracker-card section').first.locator('circle').count() == 1
        assert await page.evaluate("card.shadowRoot.activeElement?.dataset.series === '2027-02-19'")
        # Tap near an SVG point, without requiring a precise hit on a 5px circle.
        plot = page.locator('flight-price-tracker-card svg').first
        await plot.tap(position={"x": 180, "y": 100})
        assert 'Cena:' in await page.locator('flight-price-tracker-card .detail').first.inner_text()
        # Unrelated HA state updates never trigger service reads.
        before = await page.evaluate('calls.length')
        await page.evaluate('card.hass = {...hassMock, states: {unrelated: {state: 1}}}')
        assert await page.evaluate('calls.length') == before
        # Simulate the regular timer: adding a check creates its panel automatically.
        await page.evaluate('window.checkCount = 7; window.nextPoll()')
        await page.wait_for_function("card.shadowRoot.querySelectorAll('section').length === 7 && !card._busy")
        assert await page.locator('flight-price-tracker-card section').count() == 7
        # One failed check leaves its old data marked stale, other checks still load.
        await page.evaluate("window.failCheck = 'check-2'; window.nextPoll()")
        await page.wait_for_function("card._model.errors.has('check-2') && !card._busy")
        assert await page.locator('flight-price-tracker-card [role="alert"]').count() == 1
        assert 'private transport detail' not in await page.locator('flight-price-tracker-card').inner_text()
        # Viewer display toggle hides disabled checks without writes.
        await page.locator('flight-price-tracker-card [data-action="disabled"]').click()
        await page.wait_for_function("card.shadowRoot.querySelectorAll('section').length === 6 && !card._busy")
        assert await page.evaluate("calls.every(c => ['get_checks', 'get_observations'].includes(c.service))")
        # Keyboard interaction and narrow viewport remain usable.
        point = page.locator('flight-price-tracker-card circle').first
        await point.focus()
        await point.press('Enter')
        assert 'Cena:' in await page.locator('flight-price-tracker-card .detail').first.inner_text()
        assert await page.evaluate('document.documentElement.scrollWidth <= 390')
        await page.screenshot(path='/tmp/flight-price-tracker-card-mobile.png', full_page=True)
        # List failure is visible and retains existing panels; recovery reloads cleanly.
        await page.evaluate('window.failList = true; window.nextPoll()')
        await page.wait_for_function('card._model.listError && !card._busy')
        assert await page.locator('flight-price-tracker-card section').count() == 6
        await page.evaluate('window.failList = false; window.failCheck = null; window.nextPoll()')
        await page.wait_for_function('!card._model.listError && card._model.errors.size === 0 && !card._busy')
        # Multiple observation dates: dynamic Y bounds, one tick per day, latest time in legend.
        await page.evaluate('''() => {
          window.historyRows = ['2026-10-19', '2026-10-20', '2026-10-22', '2026-11-20'].map((date, i) => ({
            checkedAt: `${date}T11:00:00+00:00`, checkedDate: date, departureDate: '2027-02-19',
            departureAt: '2027-02-19T08:35:00', arrivalAt: '2027-02-19T12:15:00',
            price: [190, 550, 210, 240][i], currency: 'PLN', soldOut: false, unavailable: false,
          }));
          window.nextPoll();
        }''')
        await page.wait_for_function("card.shadowRoot.querySelectorAll('section')[0].querySelectorAll('.axis-day').length === 4 && !card._busy")
        panel = page.locator('flight-price-tracker-card section').first
        assert await panel.locator('[data-series="2027-02-19"]').inner_text() == '2027-02-19 pt 08:35'
        assert '160' in await panel.locator('svg').text_content()
        assert '580' in await panel.locator('svg').text_content()
        assert await panel.locator('.plot').evaluate('(plot) => plot.scrollWidth > plot.clientWidth')
        assert await page.evaluate('document.documentElement.scrollWidth <= 390')
        # Hit testing also works after the longer chart has scrolled horizontally.
        latest_point = panel.locator('circle[data-point="4"]')
        await latest_point.scroll_into_view_if_needed()
        box = await latest_point.bounding_box()
        await page.mouse.click(box['x'] + box['width'] / 2 + 8, box['y'] + box['height'] / 2)
        assert '240.00 PLN' in await panel.locator('.detail').inner_text()
        # Selecting a line hides other departure series; selecting it again restores visibility.
        await page.evaluate('''() => {
          window.historyRows = ['2026-10-19', '2026-10-20'].flatMap((date) => [
            {checkedAt: `${date}T11:00:00+00:00`, checkedDate: date,
             departureDate: '2027-02-19', departureAt: '2027-02-19T08:35:00',
             arrivalAt: '2027-02-19T12:15:00', price: 190, currency: 'PLN', soldOut: false, unavailable: false},
            {checkedAt: `${date}T11:00:00+00:00`, checkedDate: date,
             departureDate: '2027-02-20', departureAt: '2027-02-20T09:15:00',
             arrivalAt: '2027-02-20T12:30:00', price: 240, currency: 'PLN', soldOut: false, unavailable: false},
          ]);
          window.nextPoll();
        }''')
        await page.wait_for_function("card.shadowRoot.querySelectorAll('section')[0].querySelectorAll('[data-line]').length === 2 && !card._busy")
        panel = page.locator('flight-price-tracker-card section').first
        line = panel.locator('[data-line="2027-02-19"]')
        await line.tap()
        assert await panel.locator('[data-line]').count() == 1
        assert await panel.locator('[data-series="2027-02-20"]').get_attribute('aria-pressed') == 'false'
        await panel.locator('[data-line="2027-02-19"]').focus()
        await panel.locator('[data-line="2027-02-19"]').press('Enter')
        assert await panel.locator('[data-line]').count() == 2
        assert await panel.locator('[data-series="2027-02-20"]').get_attribute('aria-pressed') == 'true'
        await panel.locator('[data-series="2027-02-20"]').click()
        await panel.locator('[data-line="2027-02-19"]').click()
        await panel.locator('[data-line="2027-02-19"]').click()
        assert await panel.locator('[data-series="2027-02-20"]').get_attribute('aria-pressed') == 'false'
        await panel.screenshot(path='/tmp/flight-price-tracker-card-axes.png')
        await page.evaluate('card.remove()')
        assert await page.evaluate('card._timer === null')
        assert not failures, failures
        await browser.close()
        print('PASS: tablet rendering, 6→7 dynamic charts, touch/keyboard details, read-only calls, incremental refresh, errors, cleanup')


if __name__ == '__main__':
    asyncio.run(main())
