# Flight Price Tracker — Home Assistant

Home Assistant add-on for adding, viewing and enabling/disabling flight monitoring
checks stored in S3. Daily Ryanair fare collection runs separately in AWS.

## Current scope

Version 0.5.0 adds one dynamic Lovelace card: one chart panel per check, with
separate series per flight departure date. It refreshes the check list automatically
and reads observations through the existing response-returning HA services, including
for non-admin viewers. The Ingress panel supports creating checks, enabling/disabling,
sorting and airport selection. Editing existing definitions remains a later milestone.
Native Lovelace rendering was selected with the user; Grafana remains a future option.

The private `flight-price-tracker-aws` repository owns infrastructure and collector
code. This public repository must never contain AWS credentials, private
configuration, downloaded observations, or Terraform state.

[AGENTS.md](AGENTS.md) contains development rules. The detailed product contract
is maintained locally in the ignored `docs/requirements.md`; it is not included
in a public checkout. The read contract supported by this milestone is below.

## Add-on configuration

The add-on reads Supervisor-managed `/data/options.json`. Configure these fields
in Home Assistant's add-on configuration UI:

| Option | Meaning |
| --- | --- |
| `aws_region` | AWS region; defaults to `eu-central-1` |
| `s3_bucket` | Your existing data bucket name; required |
| `aws_access_key_id` | Dedicated IAM user's key ID; required |
| `aws_secret_access_key` | Dedicated IAM user's secret key; required |

Credential fields use HA's `password` schema to mask input. This does **not**
encrypt `/data/options.json`. Treat the add-on data directory and HA backups as
sensitive. Supply credentials outside Git; do not paste options into issues or
logs. This stage uses explicit IAM key options, not ambient AWS profiles.

AWS calls run only on the server. This version needs `s3:GetObject` and
`s3:PutObject` on the single `config/checks.json` object. Observation reads also
require `s3:GetObject` on `observations/*` and `s3:ListBucket` restricted to prefixes
under `observations/`. No access to raw collector data or Terraform state is needed.
No AWS deployment or IAM changes are performed by the add-on. It never writes or deletes observations and
does not create a local config database.

The manifest enables `panel_admin: true`, exposes only Ingress port 8099 internally,
and publishes no host port. The server first checks the actual Ingress proxy peer
`172.30.32.2`; forwarded headers cannot bypass it. It then reads the Supervisor-set
`X-Remote-User-Id` and uses the internal HA WebSocket API (`config/auth/list`) to
verify the account is active and is an owner or member of `system-admin`.
This check covers the page, reads, and writes and runs on every request, without
caching permissions. Browser-supplied role headers are ignored.

`homeassistant_api: true` grants the add-on access to HA Core. Its
`SUPERVISOR_TOKEN` stays on the server and is used for role lookups and the read-only
HA event bridge; it is never returned to the browser. Missing identity or a non-admin account receives
403; an unavailable or malformed HA authorization response receives 503, with no
S3 operation. Older Ingress sessions without identity must be closed and reopened;
there is no fallback to trusting the proxy alone. Non-admin dashboard access uses
the separate read-only HA services below; it never calls the Ingress API.

See the official [Ingress guide](https://developers.home-assistant.io/docs/apps/presentation/)
and [configuration reference](https://developers.home-assistant.io/docs/apps/configuration/).

## Read contract

`GET /api/checks`, through Ingress, returns the validated S3 document:

```json
{
  "schemaVersion": 1,
  "checks": [
    {
      "id": "example-check",
      "origin": "AAA",
      "destination": "BBB",
      "departureDateFrom": "2027-02-14",
      "departureDateTo": "2027-02-21",
      "currency": "PLN",
      "enabled": true
    }
  ]
}
```

Airport codes in this example are placeholders. IDs must be unique non-empty
strings; airport/currency codes must have three uppercase letters. Origin and
destination must differ. Dates must be valid `YYYY-MM-DD` dates, inclusive, with
`From <= To`; `enabled` must be a JSON boolean. Past and disabled checks remain
readable. Compatible unknown fields are preserved. No IATA registry or exchange
rate lookup is performed.

An empty `checks` list is a successful empty result. Missing/unsupported schema,
invalid JSON or checks, S3 access denial, and missing objects return HTTP 502;
network/storage availability failures return 503. Errors have the form
`{"error":{"code":"s3_not_found","message":"..."}}`, without raw AWS details.
The backend limits configuration reads to 1 MiB, uses connection/read timeouts of
5/10 seconds, and at most three SDK attempts. It runs blocking SDK I/O in a worker
thread and closes response streams and the SDK client during cleanup.

## Enable or disable a check

The Ingress table provides an **Enable** or **Disable** button per check. Disabling
stops future scheduled collection; it preserves the check definition and historical
observations. It cannot cancel a collector run already in progress.

`GET /api/checks` now includes the S3 configuration's strong `ETag` response header.
`PATCH /api/checks/{id}/enabled` requires `Content-Type: application/json`, that
exact ETag in `If-Match`, and a body containing only `{"enabled":false}` or
`{"enabled":true}`. Success returns the updated check and a new ETag. No other
fields can be changed through this endpoint.

The backend rereads the full document, validates it, checks the supplied version,
changes only `enabled` for the matching ID, and calls `PutObject` with S3 `IfMatch`.
A stale browser version or an intervening S3 write returns 409 without overwriting
the other writer. Unknown checks return 404; invalid requests return 400. Missing
ETags, unsupported schemas, duplicate JSON keys, and malformed configurations
prevent writes. Compatible unknown fields and all other checks are retained, including numeric
precision in extension fields (via bounded `simplejson` Decimal support).
There are no unconditional writes or automatic conflict retries.

During a save, all mutation buttons are disabled. After an error, refresh before
trying again. A timeout may mean S3 accepted the write but its response was lost;
the panel requests a fresh read instead of claiming that nothing changed.

## Add a check

Open **Add check** in the Ingress panel. Enter a unique permanent ID, origin and
destination IATA codes, inclusive departure-date range, currency (defaults to
`PLN`), and whether collection should be enabled. Airport choices display labels
such as **Valencia - VLC**, while only the IATA code is submitted to S3. Currency
input is normalized to uppercase. New checks default to enabled through the API when
`enabled` is omitted. An enabled check participates in the next scheduled collector
run; creating it does not trigger collection immediately.

`POST /api/checks` accepts a single check object with the same required fields as
the read example (`enabled` is optional). It requires an active HA administrator,
JSON content type, and the configuration's current strong `If-Match` ETag, just
like the enable/disable endpoint. Success returns 201 with the new check and a new
ETag. It appends to the existing configuration rather than replacing the list.

New IDs must be lowercase slugs (`letters`, `digits`, single hyphens between
segments), up to 128 characters. Airport and currency codes must have exactly
three uppercase ASCII letters; origin and destination must differ. Currency
validation checks syntax, not membership of a currency registry; PLN remains the
tested collector currency. There is no exchange-rate conversion or IATA registry
lookup. Unsupported input fields are rejected; existing compatible extension
fields remain preserved.

For new checks only, dates must fall between today (Europe/Warsaw) and 730 days
ahead, inclusive. A check can cover at most 366 departure dates, inclusive. These
initial bounds do not restrict reading or enabling/disabling historical checks.
They are input limits, not a guarantee of airline schedule availability.

Invalid input returns 400. Reusing any existing ID (including a disabled check)
returns 409 without updating that check. A stale ETag or an intervening S3 write
also returns 409. Missing or malformed S3 configuration is an error, never a reason
to initialize a replacement document. Existing write-failure handling applies.

All mutation controls are locked during save. Form values survive failed saves;
after an error, refresh and verify the table before trying again. In particular,
a lost response may mean creation succeeded, and reusing its ID must not create
a duplicate. The form resets only after a confirmed successful response.

## Administration table and airport selection

Click any data-column heading to sort ascending; click it again for descending.
The active heading displays an arrow and exposes its direction to assistive
technology. ID sorting uses natural numeric order; dates sort chronologically,
status groups disabled/enabled checks, and airport columns sort by their displayed
city labels. The chosen sorting remains active across refreshes and saves while
the panel is open. Sorting is local and does not change the check order in S3.
Enable buttons are muted green; Disable buttons are muted red, with text labels
and visible keyboard focus in both light and dark mode.

Origin and destination dropdowns use an offline, bundled catalogue of 8,798
active IATA-coded airports worldwide, based on the public-domain
[OurAirports dataset](https://github.com/davidmegginson/ourairports-data). Filter by
city, full airport name, or code; matching ignores case and decomposable accents.
An already selected airport remains selected when the filter changes. The table
also shows city/code labels, falling back to a raw code for historical or external
checks whose airport is absent from the snapshot.

The catalogue is a local lookup, not a list of Ryanair routes or available flights.
It narrows dropdown choices without changing the backend IATA syntax contract.
No browser request goes to an external airport service. If the local catalogue
cannot be loaded, existing checks and their controls remain available; the create
form stays disabled and Refresh retries loading the catalogue. See
[airport data provenance and update instructions](flight_price_tracker/www/airports.SOURCE.md).

## Observation read contract

Administrator HTTP endpoint: `GET /api/checks/{id}/observations`. Dashboard clients
use `flight_price_tracker.get_observations` through HA instead of Ingress.
Both accept:

| Parameter | Meaning |
| --- | --- |
| `page_size` | Maximum returned fare rows, 1–500; default 200 |
| `cursor` | Opaque `nextCursor` from a previous response; omit for the first page |
| `checked_from` | Optional first **observation** date in Europe/Warsaw, inclusive |
| `checked_to` | Optional last **observation** date in Europe/Warsaw, inclusive |

HA calls additionally require `check_id`; HTTP obtains it from the path. Date
filters are `YYYY-MM-DD` and do not filter flight departure dates.

```json
{
  "checkId": "example-check",
  "observations": [{
    "checkedAt": "2026-10-19T22:30:00+00:00",
    "checkedDate": "2026-10-20",
    "departureDate": "2027-02-19",
    "departureAt": "2027-02-19T23:45:00",
    "arrivalAt": "2027-02-20T02:15:00",
    "price": 196.5,
    "currency": "PLN",
    "soldOut": false,
    "unavailable": false
  }],
  "nextCursor": null
}
```

`checkedAt` must be offset-aware UTC in the source; `checkedDate` is derived in
Europe/Warsaw, including summer/winter offsets. Flight timestamps remain local
airport wall-clock strings, with no added `Z` or inferred cross-airport conversion.
Unavailable/sold-out rows have `price: null` and retain their availability flags
and any known flight times. Available prices must be finite, numeric and nonnegative.
A snapshot may have an empty price list. Missing v1 fields, unsupported schemas,
inconsistent check IDs and duplicate departure dates within a run are errors.

Pages preserve collection history, including multiple runs for the same day. They
are traversed by S3 key order, then departure date within each run; this is not a
global chronological price sort. The Lovelace card selects the latest
`checkedAt` per Warsaw observation day and departure date, breaking timestamp ties by a fixed
lexical comparison of currency/price/status/flight-time fields. Aggregation is only
in browser memory; raw S3 history remains unchanged. No FX conversion is performed.

Pagination uses S3 `ListObjectsV2` with a bounded `MaxKeys` and `StartAfter`, so it
continues beyond 1,000 objects without loading the whole listing. A page visits at
most 10 run objects, each at most 1 MiB and 1,000 fares, and may contain fewer rows
than `page_size`. It may even be empty **with a non-null cursor**. Keep paging until
`nextCursor` is null. Cursors can resume inside a run and are signed and bound to
the check, page size and date filters. They expire when the add-on restarts; begin
a new read after an `invalid_cursor` error. Keep the same filters and page size
throughout a traversal. A resumed final run can require one extra empty end page.

Immutable run bodies are cached in memory with limits of 64 objects / 8 MiB of
source JSON. Config reads and listings are still performed; there is no second
authoritative database. To refresh a chart, request from the last known
`checkedDate` inclusively rather than rereading all history. Filters start listing
one partition day earlier to cover UTC/Warsaw midnight boundaries and stop after
the requested end partition; partitions are expected to use UTC or Warsaw dates
corresponding to their run timestamp. Concurrent backfills into earlier keys can
require a fresh traversal; this is not a transactionally frozen S3 listing.

Two observation workers are allowed. The caller times out after 25 seconds, and
workers check a 20-second work budget between SDK operations. A running SDK call
may outlive the caller timeout, but it retains its worker slot until completion;
new work is rejected as busy instead of queued without bound. SDK timeouts and
retry limits remain bounded by the configured client. Unknown checks return 404,
invalid parameters/cursors 400, malformed data or S3 access denial/missing listed
objects 502, and temporary storage, busy or timeout errors 503. Partial failures
fail the page rather than returning a misleading partial success or empty history.

## Home Assistant read-only integration

On add-on startup, the bundled integration is installed into
`/config/custom_components/flight_price_tracker`. The add-on now mounts HA config
for that purpose. It manages only its own named files and marker. An existing
unmarked directory or symlink is refused rather than overwritten; the admin panel
can still run if integration installation fails. Unrelated files are retained.

When you choose to install this version, start the add-on, then add this top-level
entry to your existing HA `configuration.yaml` and restart **Home Assistant Core**
once. The add-on does not edit YAML or restart HA for you:

```yaml
flight_price_tracker:
```

This registers two read-only, response-returning services/actions:

- `flight_price_tracker.get_checks`: optional `include_disabled` boolean, default
  true. Returns schema version and known check metadata fields; compatible private
  extension fields are not exposed through this dashboard interface.
- `flight_price_tracker.get_observations`: `check_id`, optional page size, cursor
  and inclusive checked-date filters described above. Disabled-check history remains
  available regardless of the list display filter.

For an initial manual check in HA Developer Tools → Actions:

```yaml
action: flight_price_tracker.get_observations
data:
  check_id: example-check
  page_size: 100
```

Replace the synthetic ID with one of your checks. Later dashboard code can use
`hass.callWS` with `type: "call_service"`, the domain/service/data, and
`return_response: true`; no Ingress URL or AWS key belongs in a card. The services
require an authenticated active user and intentionally do not require administrator
rights. Calls without a user context (including unowned automation calls) are
rejected. No create/update/enable/disable services or event operations exist.

The integration passes a correlated request over HA events; the add-on subscribes
using its internal token and emits a safe result or error. Up to 16 service requests
can be pending; the bridge limits concurrent dispatches to four. The integration
waits at most 60 seconds and clears pending state on success, failure, cancellation
or timeout. Disconnected HA transport reconnects with bounded backoff. A bridge or
S3 failure raises an HA action error; it never masquerades as an empty chart.

HA-facing tests use focused Core doubles, real voluptuous schemas, and a local
WebSocket/HTTP server. They cover non-admin reads and absence of mutations without
requiring a real HA instance. Actual Core loading and tablet access remain an
operational test for when you next update HA; you can skip installing 0.3.1 first.
The Lovelace card below uses these services without Ingress access.

## Dynamic Lovelace card

After updating to 0.5.0 and starting the add-on, the managed card assets are copied
into `/config/www/flight-price-tracker/`. Existing unmanaged directories or symlinks
are refused; other files in `www` are preserved. Nothing edits your dashboards or
registers resources automatically. If this is the first time HA's `www` directory
has been created, restart HA Core once so `/local/` becomes available.

Enable the HA integration as described above. In **Settings → Dashboards → Resources**
(enable Advanced Mode if necessary), add this **JavaScript module** once:

```text
/local/flight-price-tracker/flight-price-tracker-card.js?v=0.5.0
```

On the existing tablet dashboard, add one manual card:

```yaml
type: custom:flight-price-tracker-card
title: Ceny lotów
show_disabled: true
refresh_interval: 60
```

This single card dynamically contains as many chart panels as there are checks.
It creates no persistent per-check YAML cards. The list is reloaded automatically
60 seconds after a refresh finishes, or with **Odśwież**. A new check appears on
the next refresh, with an explicit empty-history message until collection produces
observations. `show_disabled` defaults to true; the viewer can also toggle it locally.
`refresh_interval` accepts 30–3600 seconds. Polling stops when the card is removed
or the browser tab is hidden and resumes on return. Unrelated HA state updates do
not trigger S3 reads. Each card keeps only its own in-memory cache; a page reload
starts a full read. A single card per dashboard is recommended.

X is the observation date in Europe/Warsaw; Y is the price in the check's currency.
Each flight departure date is a separate colored series. Legend buttons toggle
series locally. Tap, hover or focus a point to see exact local departure/arrival
strings (including next-day arrival), fare/status and UTC collection timestamp.
Unavailable/sold-out prices are hollow status markers at the baseline, **not zero
fares**, and interrupt the line. Days without observations also interrupt lines.
Themes use HA CSS variables; a narrow plot scrolls inside the card on small screens.

Reads are sequential to respect server worker limits. Every page is followed,
including empty pages with a cursor. A failed page never commits a partial update;
old chart data remain visible with a stale-data warning. List errors are separate
from an empty check list. A later refresh restarts pagination with a new cursor,
so add-on restarts do not permanently strand the card. There are no browser AWS
calls, external chart scripts, CDN requests, write services or Grafana dependencies.

Initial reads cover all available history. Subsequent reads start from the newest
known Warsaw observation date inclusively. Late backfills into older dates require
reloading the dashboard. Safety limits per check are 1,000 pages, 100,000 raw rows
per refresh and 20,000 aggregated points; exceeding a limit shows an error rather
than truncating the chart silently. Larger histories will need a later range/rollup
milestone. Switching currencies in historical data is rejected instead of mixed.

Offline browser verification (optional locally; runs in CI on amd64):

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_card_smoke.py
```

The Chromium test uses a mobile viewport and synthetic HA responses. It exercises
six-to-seven automatic panels, touch/keyboard details, series toggles, disabled
checks, incremental reads, errors and lifecycle cleanup. Real HA/tablet installation
and non-admin dashboard use still need an operational smoke test. For updates,
increment the resource URL version and reload the browser to replace cached JS.

## Local verification

Use Python 3.13 (the container runtime) or newer, plus Node.js for the UI helper tests:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
```

Tests use fake credentials, `botocore.stub.Stubber`, and local aiohttp servers.
They block botocore HTTP transport and never require live AWS. Local TCP binding
must be available to run HTTP tests.

Build the local image without publishing it:

```sh
docker build -t flight-price-tracker:local flight_price_tracker
```

GitHub Actions tests and builds on native amd64 and aarch64 runners. Pushes to
`main` (and manual runs on `main`) publish signed architecture images and, after
both succeed, `ghcr.io/krbielec/flight-price-tracker`. Pull requests test and build
without publishing. Image tags come from the manifest version, plus `latest`
and `sha-<commit>`. Bump the add-on version for subsequent HA updates; HA installs
the manifest's version tag, not `latest`.

## Install and test in Home Assistant

After the [build workflow](https://github.com/Krbielec/flight-price-tracker-ha/actions/workflows/build.yaml)
finishes, add this repository in the HA app/add-on store's **Repositories** menu:

```text
https://github.com/Krbielec/flight-price-tracker-ha
```

Refresh the store, install **Flight Price Tracker**, fill in the AWS options,
save, start, and use **Open Web UI** as an administrator. Home Assistant with
Supervisor is required. See [the add-on instructions](flight_price_tracker/DOCS.md)
for the smoke-test steps and troubleshooting. Actual HA installation and Ingress
authentication require testing on your instance.

On first publication, GitHub may create GHCR packages as private. The repository
owner must make the `flight-price-tracker` package public in its package settings
so HA can pull without registry credentials. Architecture packages can also be
made public. Workflow publishing uses the built-in `GITHUB_TOKEN`, with no AWS
credentials or extra registry password secrets.

Direct localhost access is intentionally rejected with 403. The UI and API are
intended for Home Assistant Ingress, with relative URLs preserving its base path.

## Dependencies and reference

Runtime dependencies are intentionally bounded in
`flight_price_tracker/requirements.txt`; development dependencies are in
`requirements-dev.txt`. Lower bounds track the versions verified for this stage,
and upper bounds prevent automatic major upgrades. Builds can resolve newer
versions inside these bounds, so they are not fully reproducible. Review security
updates regularly, rerun mocked tests and image checks when updating bounds, and
verify both architectures before publishing a release. The Python base image
tracks the 3.13 patch series on Debian Bookworm.

Architecture reference: [Krbielec/calendar](https://github.com/Krbielec/calendar),
reviewed at commit `795e2f6bdfcc61207f7ad341c06cefd61ffeba81`. We reuse the
add-on/Ingress layout. Calendar's HA response-service/event bridge and custom
Lovelace card are reference patterns. The user selected native Lovelace charts for
this stage; Grafana can be considered separately in the future.
