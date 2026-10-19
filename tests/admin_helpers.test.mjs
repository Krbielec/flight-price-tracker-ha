import assert from 'node:assert/strict';
import test from 'node:test';
import {airportChoices, airportLabel, sortChecks} from '../flight_price_tracker/www/admin-helpers.mjs';

const airports = [
  {code: 'VLC', city: 'Valencia', name: 'Valencia Airport'},
  {code: 'AGP', city: 'Málaga', name: 'Málaga-Costa del Sol Airport'},
  {code: 'LIS', city: 'Lisbon', name: 'Humberto Delgado Airport'},
  {code: 'KRK', city: 'Kraków', name: 'Kraków John Paul II International Airport'},
];
const index = new Map(airports.map((airport) => [airport.code, airport]));
const ids = (checks) => checks.map((check) => check.id);

test('IDs sort naturally both ways without changing S3 order or check objects', () => {
  const checks = [{id: 'check-10'}, {id: 'check-2'}, {id: 'check-1'}];
  assert.deepEqual(ids(sortChecks(checks, 'id', 'ascending', index)), ['check-1', 'check-2', 'check-10']);
  assert.deepEqual(ids(sortChecks(checks, 'id', 'descending', index)), ['check-10', 'check-2', 'check-1']);
  assert.deepEqual(ids(checks), ['check-10', 'check-2', 'check-1']);
});

test('inclusive flight dates sort chronologically across years', () => {
  const checks = [{id: 'later', departureDateFrom: '2028-01-01'}, {id: 'earlier', departureDateFrom: '2027-12-31'}];
  assert.deepEqual(ids(sortChecks(checks, 'departureDateFrom', 'ascending', index)), ['earlier', 'later']);
  assert.deepEqual(ids(sortChecks(checks, 'departureDateFrom', 'descending', index)), ['later', 'earlier']);
});

test('status sorts disabled/enabled, with a deterministic ID tiebreak', () => {
  const checks = [{id: 'c', enabled: true}, {id: 'b', enabled: false}, {id: 'a', enabled: true}];
  assert.deepEqual(ids(sortChecks(checks, 'enabled', 'ascending', index)), ['b', 'a', 'c']);
  assert.deepEqual(ids(sortChecks(checks, 'enabled', 'descending', index)), ['a', 'c', 'b']);
});

test('airport columns sort by the displayed city, preserving raw IATA fields', () => {
  const checks = [{id: 'malaga', origin: 'AGP'}, {id: 'lisbon', origin: 'LIS'}];
  assert.deepEqual(ids(sortChecks(checks, 'origin', 'ascending', index)), ['lisbon', 'malaga']);
  assert.equal(checks[0].origin, 'AGP');
  assert.equal(airportLabel('VLC', index), 'Valencia - VLC');
  assert.equal(airportLabel('ZZZ', index), 'ZZZ');
});

test('remaining data columns support both directions', () => {
  for (const [key, a, b] of [['destination', 'LIS', 'VLC'], ['currency', 'EUR', 'PLN'],
                            ['departureDateTo', '2027-01-01', '2027-02-01']]) {
    const checks = [{id: 'b', [key]: b}, {id: 'a', [key]: a}];
    assert.deepEqual(ids(sortChecks(checks, key, 'ascending', index)), ['a', 'b']);
    assert.deepEqual(ids(sortChecks(checks, key, 'descending', index)), ['b', 'a']);
  }
});

test('search accepts cities with or without accents, codes, and full airport names', () => {
  assert.deepEqual(airportChoices(airports, ' valencia ').map((a) => a.code), ['VLC']);
  assert.deepEqual(airportChoices(airports, 'vlc').map((a) => a.code), ['VLC']);
  assert.deepEqual(airportChoices(airports, 'malaga').map((a) => a.code), ['AGP']);
  assert.deepEqual(airportChoices(airports, 'krakow').map((a) => a.code), ['KRK']);
  assert.deepEqual(airportChoices(airports, 'humberto').map((a) => a.code), ['LIS']);
});

test('filtering does not silently clear an already selected airport', () => {
  assert.deepEqual(airportChoices(airports, 'Berlin', 'VLC').map((a) => a.code), ['VLC']);
  assert.deepEqual(airportChoices(airports, 'Berlin'), []);
  assert.deepEqual(airports.map((a) => a.code), ['VLC', 'AGP', 'LIS', 'KRK']);
});

test('no sort selected retains source order without sharing the array', () => {
  const checks = [{id: 'z'}, {id: 'a'}];
  const result = sortChecks(checks, null, 'ascending', index);
  assert.deepEqual(ids(result), ['z', 'a']);
  assert.notEqual(result, checks);
});
