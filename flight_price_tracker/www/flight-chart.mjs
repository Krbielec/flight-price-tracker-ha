// Pure chart/data code shared by the Lovelace component and offline tests.
export const DAY = 86400000;
export const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g,
  (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
export const dayNumber = (date) => Date.parse(`${date}T00:00:00Z`) / DAY;
export const color = (index) => `hsl(${(index * 137.508 + 205) % 360} 62% 48%)`;

// Preserve microseconds when comparing UTC timestamps; Date truncates to milliseconds.
function runKey(row) {
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(row.checkedAt);
  if (!match) throw new Error('Invalid observation timestamp');
  return `${match[1]}.${(match[2] || '').padEnd(6, '0')}`;
}
function tieKey(row) {
  // Deterministic even if pages/S3 keys arrive in a different order.
  return JSON.stringify([row.currency, row.price, row.soldOut, row.unavailable,
    row.departureAt, row.arrivalAt]);
}
export function aggregate(rows) {
  const latest = new Map();
  for (const row of rows) {
    const key = `${row.checkedDate}/${row.departureDate}`;
    const old = latest.get(key);
    if (!old || runKey(row) > runKey(old) ||
        (runKey(row) === runKey(old) && tieKey(row) > tieKey(old))) latest.set(key, row);
  }
  return [...latest.values()].sort((a, b) => a.checkedDate.localeCompare(b.checkedDate) ||
    a.departureDate.localeCompare(b.departureDate));
}

export async function readService(hass, service, service_data) {
  const result = await hass.callWS({type: 'call_service', domain: 'flight_price_tracker',
    service, service_data, return_response: true});
  if (!result || !result.response || typeof result.response !== 'object') throw new Error('Invalid HA response');
  return result.response;
}

export async function readHistory(hass, check, previous, active = () => true) {
  const params = {check_id: check.id, page_size: 200};
  // Reread the latest Warsaw day inclusively, merging by timestamp below.
  const old = previous?.rows || [];
  if (old.length) params.checked_from = old.reduce((last, row) => row.checkedDate > last ? row.checkedDate : last, '');
  let rows = [], cursor = null;
  const seen = new Set();
  for (let page = 0; page < 1000; page++) {
    if (!active()) return null;
    const result = await readService(hass, 'get_observations', {...params, ...(cursor ? {cursor} : {})});
    if (!active()) return null;
    if (result.checkId !== check.id || !Array.isArray(result.observations) ||
        !(result.nextCursor === null || typeof result.nextCursor === 'string' && result.nextCursor.length > 0)) {
      throw new Error('Invalid observations response');
    }
    for (const row of result.observations) {
      const missing = row.soldOut === true || row.unavailable === true;
      if (!(row.currency === check.currency || missing && row.currency === null) ||
          !/^\d{4}-\d\d-\d\d$/.test(row.checkedDate) ||
          !/^\d{4}-\d\d-\d\d$/.test(row.departureDate) ||
          typeof row.soldOut !== 'boolean' || typeof row.unavailable !== 'boolean' ||
          !(row.price === null || typeof row.price === 'number' && Number.isFinite(row.price) && row.price >= 0)) {
        throw new Error('Invalid fare or currency');
      }
      runKey(row);
    }
    rows.push(...result.observations);
    if (rows.length > 100000) throw new Error('History limit exceeded');
    cursor = result.nextCursor;
    if (cursor === null) {
      const merged = aggregate([...old, ...rows]);
      if (merged.length > 20000) throw new Error('History limit exceeded');
      return {rows: merged};
    }
    if (seen.has(cursor)) throw new Error('Repeated pagination cursor');
    seen.add(cursor);
  }
  throw new Error('History page limit exceeded');
}

export function pointDetail(row) {
  const status = row.soldOut ? 'Wyprzedany' : row.unavailable ? 'Niedostępny' :
    row.price === null ? 'Brak ceny' : `${row.price.toFixed(2)} ${row.currency}`;
  // These are airport wall-clock strings, never parsed as browser-local or UTC times.
  const wall = (value) => value ? value.replace('T', ' ') : 'brak danych';
  return `Wylot: ${wall(row.departureAt)} · Przylot: ${wall(row.arrivalAt)} (czasy lokalne lotnisk). ` +
    `Cena: ${status}. Obserwacja: ${row.checkedDate} (Warszawa), ${row.checkedAt} (UTC).`;
}

export function priceScale(values) {
  if (!values.length) return {minimum: 0, maximum: 1};
  const low = values.reduce((a, b) => Math.min(a, b), Infinity);
  const high = values.reduce((a, b) => Math.max(a, b), 0);
  const padding = Math.max((high - low) * 0.08, high * 0.02, 1);
  const step = 10 ** (Math.floor(Math.log10(Math.max(high - low, padding))) - 1);
  return {
    minimum: Math.max(0, Math.floor((low - padding) / step) * step),
    maximum: Math.ceil((high + padding) / step) * step,
  };
}

export function legendLabel(date, rows) {
  const latest = rows.filter((row) => row.departureDate === date).reduce((old, row) =>
    !old || runKey(row) > runKey(old) || runKey(row) === runKey(old) && tieKey(row) > tieKey(old) ? row : old, null);
  // Slice the local wall-clock time directly, without timezone conversion.
  const time = latest?.departureAt?.match(/T(\d{2}:\d{2}):/);
  const weekday = ['nd', 'pn', 'wt', 'śr', 'cz', 'pt', 'sb'][new Date(`${date}T00:00:00Z`).getUTCDay()];
  return `${date} ${weekday} ${time ? time[1] : '—'}`;
}

export function chartMarkup(check, rows, hidden = new Set(), selected = null) {
  if (!rows.length) return '<p class="muted">Brak obserwacji dla tego checka.</p>';
  const dates = [...new Set(rows.map((r) => r.departureDate))].sort();
  const days = rows.map((r) => dayNumber(r.checkedDate));
  const first = days.reduce((a, b) => Math.min(a, b), Infinity), last = days.reduce((a, b) => Math.max(a, b), -Infinity);
  const values = rows.filter((r) => !hidden.has(r.departureDate) && !r.soldOut && !r.unavailable && r.price !== null).map((r) => r.price);
  const {minimum, maximum} = priceScale(values);
  const width = Math.max(700, (last - first) * 80 + 100);
  const plotWidth = width - 90;
  const x = (r) => 60 + (last === first ? plotWidth / 2 : (dayNumber(r.checkedDate) - first) / (last - first) * plotWidth);
  const y = (r) => 170 - (r.price - minimum) / (maximum - minimum) * 148;
  let svg = `<svg viewBox="0 0 ${width} 295" style="min-width:${Math.max(480, (last - first) * 64 + 100)}px" role="group" aria-label="Historia cen ${escapeHTML(check.id)}">`;
  for (let i = 0; i <= 4; i++) {
    const py = 170 - i * 148 / 4;
    svg += `<path d="M60 ${py}H${width - 30}" class="grid"/><text x="52" y="${py + 4}" text-anchor="end">${Number((minimum + (maximum - minimum) * i / 4).toFixed(2))}</text>`;
  }
  svg += `<text x="60" y="16">${escapeHTML(check.currency)}</text>`;
  for (const date of [...new Set(rows.map((r) => r.checkedDate))].sort()) {
    const px = x({checkedDate: date});
    svg += `<path d="M${px} 170v8" class="grid"/><text class="axis-day" data-observation-date="${date}" x="${px}" y="195" transform="rotate(-55 ${px} 195)" text-anchor="end">${date}</text>`;
  }
  svg += `<text x="${60 + plotWidth / 2}" y="288" text-anchor="middle">Data obserwacji (Europe/Warsaw)</text>`;
  dates.forEach((date, index) => {
    if (hidden.has(date)) return;
    const series = rows.map((row, rowIndex) => ({row, rowIndex})).filter(({row}) => row.departureDate === date);
    let segment = [], previousDay = null;
    const flush = () => {
      if (segment.length > 1) {
        const points = segment.join(' ');
        svg += `<polyline fill="none" stroke="${color(index)}" stroke-width="${selected === date ? 4 : 2}" points="${points}"/>`;
        svg += `<polyline class="line-hit" fill="none" stroke="transparent" stroke-width="18" points="${points}" data-line="${date}" role="button" tabindex="0" aria-label="Pokaż tylko serię ${date}"/>`;
      }
      segment = [];
    };
    for (const {row, rowIndex} of series) {
      const available = !row.soldOut && !row.unavailable && row.price !== null;
      const day = dayNumber(row.checkedDate);
      if (!available || previousDay !== null && day - previousDay > 1) flush();
      if (available) segment.push(`${x(row)},${y(row)}`);
      previousDay = day;
      // Unknown prices sit on the baseline as hollow marks, with status in details.
      const cy = available ? y(row) : 170;
      const label = escapeHTML(pointDetail(row));
      svg += `<circle cx="${x(row)}" cy="${cy}" r="${selected === date ? 6 : 5}" fill="${available ? color(index) : 'var(--card-background-color, white)'}" stroke="${color(index)}" stroke-width="2" tabindex="0" role="button" data-point="${rowIndex}" aria-label="${label}"><title>${label}</title></circle>`;
    }
    flush();
  });
  svg += '</svg>';
  const legend = dates.map((date, i) => `<button class="series" data-series="${date}" aria-pressed="${!hidden.has(date)}" style="border-color:${color(i)}" title="Godzina wylotu z najnowszej obserwacji (czas lokalny lotniska)">${escapeHTML(legendLabel(date, rows))}</button>`).join('');
  const unavailable = rows.some((r) => r.soldOut || r.unavailable || r.price === null);
  return `<div class="plot">${svg}</div>${unavailable ? '<p class="plot-note">○ = brak ceny (nie 0).</p>' : ''}<div class="legend" aria-label="Daty wylotu — przełącz widoczność">${legend}</div>`;
}

export class TrackerModel {
  constructor() {
    this.checks = [];
    this.history = new Map();
    this.errors = new Set();
    this.listError = false;
  }
  async refresh(hass, includeDisabled, active = () => true, changed = () => {}) {
    let result;
    try {
      result = await readService(hass, 'get_checks', {include_disabled: includeDisabled});
      if (!active()) return;
      if (result.schemaVersion !== 1 || !Array.isArray(result.checks) ||
          new Set(result.checks.map((c) => c.id)).size !== result.checks.length) throw new Error('Invalid checks');
    } catch {
      if (active()) { this.listError = true; changed(); }
      return;
    }
    this.listError = false;
    this.checks = result.checks;
    const ids = new Set(this.checks.map((check) => check.id));
    for (const id of this.history.keys()) if (!ids.has(id)) this.history.delete(id);
    for (const id of this.errors) if (!ids.has(id)) this.errors.delete(id);
    changed(); // New check panels exist immediately, even while their history loads.
    // One request at a time avoids exhausting the bounded server-side reader.
    for (const check of this.checks) {
      if (!active()) return;
      try {
        const entry = await readHistory(hass, check, this.history.get(check.id), active);
        if (!active()) return;
        this.history.set(check.id, entry);
        this.errors.delete(check.id);
      } catch {
        if (!active()) return;
        this.errors.add(check.id); // Keep prior data visibly marked stale.
      }
      changed();
    }
  }
}
