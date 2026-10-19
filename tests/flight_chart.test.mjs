import assert from 'node:assert/strict';
import test from 'node:test';
import {aggregate, chartMarkup, pointDetail, readHistory, TrackerModel} from '../flight_price_tracker/www/flight-chart.mjs';
const check = {id: 'example-check', currency: 'PLN', enabled: true};
const fare = (overrides = {}) => ({checkedAt: '2026-10-19T10:00:00+00:00', checkedDate: '2026-10-19',
  departureDate: '2027-02-19', departureAt: '2027-02-19T23:45:00', arrivalAt: '2027-02-20T02:15:00',
  price: 196, currency: 'PLN', soldOut: false, unavailable: false, ...overrides});
const response = (observations, nextCursor = null) => ({response: {checkId: check.id, observations, nextCursor}});

test('daily aggregation keeps latest run per departure date, including microseconds', () => {
  const older = fare({checkedAt: '2026-10-19T10:00:00.000001+00:00', price: 100});
  const newer = fare({checkedAt: '2026-10-19T10:00:00.000002+00:00', price: 200});
  const anotherDate = fare({departureDate: '2027-02-20'});
  assert.deepEqual(aggregate([newer, older, anotherDate]), [newer, anotherDate]);
  assert.deepEqual(aggregate([older, newer, anotherDate]), [newer, anotherDate]);
  assert.deepEqual(aggregate([fare({price: 10}), fare({price: 20})]), aggregate([fare({price: 20}), fare({price: 10})]));
});

test('pagination continues past empty pages and returns full history', async () => {
  const calls = [];
  const hass = {callWS: async (call) => {
    calls.push(call);
    return calls.length === 1 ? response([], 'next') : response([fare()]);
  }};
  const result = await readHistory(hass, check);
  assert.equal(result.rows.length, 1);
  assert.equal(calls[1].service_data.cursor, 'next');
  assert.equal(calls[1].return_response, true);
  assert.equal(calls[1].service, 'get_observations');
});

test('incremental refresh rereads last Warsaw day inclusively and preserves older history', async () => {
  const older = fare({checkedDate: '2026-10-18', checkedAt: '2026-10-18T10:00:00+00:00'});
  const previous = {rows: [older, fare()]};
  const newer = fare({checkedAt: '2026-10-19T11:00:00+00:00', price: 300});
  const result = await readHistory({callWS: async (call) => {
    assert.equal(call.service_data.checked_from, '2026-10-19');
    return response([newer]);
  }}, check, previous);
  assert.deepEqual(result.rows, [older, newer]);
  assert.equal(previous.rows[1].price, 196);
});

test('failed page, wrong currency and repeated cursors never produce partial success', async () => {
  let count = 0;
  await assert.rejects(readHistory({callWS: async () => {
    if (++count === 2) throw new Error('S3 unavailable');
    return response([fare()], 'next');
  }}, check));
  await assert.rejects(readHistory({callWS: async () => response([fare({currency: 'EUR'})])}, check));
  await assert.rejects(readHistory({callWS: async () => response([], 'same')}, check), /Repeated/);
});

test('cancelled traversal cannot publish late responses', async () => {
  let active = true;
  const result = await readHistory({callWS: async () => {active = false; return response([fare()]);}}, check, null, () => active);
  assert.equal(result, null);
});

test('one chart per check: adding seventh check requires no config change', async () => {
  let checks = Array.from({length: 6}, (_, i) => ({...check, id: `check-${i}`}));
  const hass = {callWS: async (call) => call.service === 'get_checks' ?
    {response: {schemaVersion: 1, checks}} :
    {response: {checkId: call.service_data.check_id, observations: [], nextCursor: null}}};
  const model = new TrackerModel();
  await model.refresh(hass, true);
  assert.equal(model.checks.length, 6);
  checks = [...checks, {...check, id: 'check-7'}];
  await model.refresh(hass, true);
  assert.equal(model.checks.length, 7);
  assert.equal(model.history.size, 7);
});

test('per-check errors retain stale data, other charts load; list errors retain panels', async () => {
  const model = new TrackerModel();
  model.history.set(check.id, {rows: [fare()]});
  const other = {...check, id: 'other'};
  await model.refresh({callWS: async (call) => {
    if (call.service === 'get_checks') return {response: {schemaVersion: 1, checks: [check, other]}};
    if (call.service_data.check_id === check.id) throw new Error('offline');
    return {response: {checkId: 'other', observations: [], nextCursor: null}};
  }}, true);
  assert.ok(model.errors.has(check.id));
  assert.equal(model.history.get(check.id).rows.length, 1);
  assert.ok(model.history.has(other.id));
  await model.refresh({callWS: async () => {throw new Error('offline');}}, true);
  assert.equal(model.listError, true);
  assert.equal(model.checks.length, 2);
});

test('missing prices and missing observation days break lines rather than drawing zeros', () => {
  const day = (date, overrides = {}) => fare({checkedDate: `2026-10-${date}`, ...overrides});
  const html = chartMarkup(check, [day('19'), day('20', {unavailable: true, price: null}), day('21'), day('23')]);
  assert.equal((html.match(/<circle/g) || []).length, 4);
  assert.equal((html.match(/<polyline/g) || []).length, 0);
  assert.match(html, /Niedostępny/);
  assert.match(chartMarkup(check, [day('19'), day('20')]), /<polyline/);
  assert.doesNotMatch(chartMarkup(check, [day('19')], new Set(['2027-02-19'])), /<circle/);
});

test('point details preserve local timestamps, next-day arrival and currency; markup escapes text', () => {
  assert.match(pointDetail(fare()), /2027-02-20 02:15:00/);
  assert.match(pointDetail(fare()), /196.00 PLN/);
  assert.match(pointDetail(fare()), /czasy lokalne lotnisk/);
  const html = chartMarkup({...check, id: '<script>'}, [fare({departureAt: '<img onerror=bad>'})]);
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /&lt;img/);
});

test('Y scale fits visible fares with padding instead of forcing a zero baseline', async () => {
  const {priceScale} = await import('../flight_price_tracker/www/flight-chart.mjs');
  const scale = priceScale([190, 550]);
  assert.equal(scale.minimum, 160);
  assert.equal(scale.maximum, 580);
  for (const values of [[196], [196, 196], [0], [0, 550], []]) {
    const bounds = priceScale(values);
    assert.ok(Number.isFinite(bounds.minimum) && Number.isFinite(bounds.maximum));
    assert.ok(bounds.maximum > bounds.minimum && bounds.minimum >= 0);
    assert.ok(values.every((v) => v >= bounds.minimum && v <= bounds.maximum));
  }
  const rows = [fare({price: 190}), fare({price: 550, departureDate: '2027-02-20'}),
    fare({price: null, unavailable: true, departureDate: '2027-02-21'})];
  const html = chartMarkup(check, rows);
  assert.match(html, /text-anchor="end">160<\/text>/);
  assert.match(html, /text-anchor="end">580<\/text>/);
  const hidden = chartMarkup(check, rows, new Set(['2027-02-20']));
  assert.doesNotMatch(hidden, /text-anchor="end">580<\/text>/);
  assert.doesNotMatch(html, /NaN|Infinity/);
});

test('X axis labels every actual observation day once, retaining real gaps and readable width', () => {
  const rows = ['2026-10-19', '2026-10-20', '2026-10-22', '2026-11-20'].flatMap((date) => [
    fare({checkedDate: date}), fare({checkedDate: date, departureDate: '2027-02-20'}),
  ]);
  const html = chartMarkup(check, rows);
  const dates = [...html.matchAll(/data-observation-date="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(dates, ['2026-10-19', '2026-10-20', '2026-10-22', '2026-11-20']);
  assert.doesNotMatch(html, /data-observation-date="2026-10-21"/);
  const minimumWidth = Number(html.match(/min-width:(\d+)px/)[1]);
  assert.ok(minimumWidth >= 2000);
  assert.match(html, /rotate\(-55/);
});

test('legend shows latest departure wall-clock time without converting timezone or retaining a stale time', async () => {
  const {legendLabel} = await import('../flight_price_tracker/www/flight-chart.mjs');
  const old = fare({departureAt: '2027-02-19T08:35:00'});
  const latest = fare({checkedAt: '2026-10-20T10:00:00+00:00', checkedDate: '2026-10-20', departureAt: '2027-02-19T09:40:00'});
  assert.equal(legendLabel('2027-02-19', [latest, old]), '2027-02-19 09:40');
  assert.match(chartMarkup(check, [old]), />2027-02-19 08:35<\/button>/);
  assert.equal(legendLabel('2027-02-19', [old, {...latest, departureAt: null, unavailable: true}]), '2027-02-19 —');
  const tied = {...latest, departureAt: '2027-02-19T10:50:00'};
  assert.equal(legendLabel('2027-02-19', [latest, tied]), legendLabel('2027-02-19', [tied, latest]));
});
