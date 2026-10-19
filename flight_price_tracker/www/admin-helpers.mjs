const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
const sortKeys = new Set(['id', 'origin', 'destination', 'departureDateFrom', 'departureDateTo', 'currency', 'enabled']);

export function airportLabel(code, airports) {
  const airport = airports.get(code);
  return airport ? `${airport.city} - ${code}` : code;
}

export function sortChecks(checks, key, direction, airports) {
  const copy = [...checks];
  if (!sortKeys.has(key)) return copy;
  const value = (check) => {
    if (key === 'origin' || key === 'destination') return airportLabel(check[key], airports);
    if (key === 'enabled') return check.enabled ? 1 : 0;
    return check[key];
  };
  return copy.sort((left, right) => {
    const comparison = key === 'enabled' ? value(left) - value(right) : collator.compare(value(left), value(right));
    return comparison * (direction === 'descending' ? -1 : 1) || collator.compare(left.id, right.id);
  });
}

function searchText(value) {
  return value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
}

export function airportChoices(airports, query, selectedCode = '') {
  const needle = searchText(query.trim());
  return airports.filter((airport) => airport.code === selectedCode ||
    searchText(`${airport.city} ${airport.code} ${airport.name}`).includes(needle))
    .sort((left, right) => collator.compare(left.city, right.city) || collator.compare(left.code, right.code));
}
