# Flight Price Tracker — Home Assistant

Home Assistant add-on for adding, viewing and enabling/disabling flight monitoring
checks stored in S3. Daily Ryanair fare collection runs separately in AWS.

## Current scope

Version 0.3.1 adds sortable administration columns, muted enable/disable button
colors, and airport dropdowns with city names and IATA codes. The panel supports
creating checks and enabling/disabling existing checks. Server-side boto3 reads and conditionally updates
`config/checks.json`. Editing existing check definitions, observation reads, the HA
dashboard integration, and charts remain later milestones.

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
`s3:PutObject` on the single `config/checks.json` object. No AWS deployment or IAM
changes are performed by the add-on. It never writes or deletes observations and
does not create a local config database.

The manifest enables `panel_admin: true`, exposes only Ingress port 8099 internally,
and publishes no host port. The server first checks the actual Ingress proxy peer
`172.30.32.2`; forwarded headers cannot bypass it. It then reads the Supervisor-set
`X-Remote-User-Id` and uses the internal HA WebSocket API (`config/auth/list`) to
verify the account is active and is an owner or member of `system-admin`.
This check covers the page, reads, and writes and runs on every request, without
caching permissions. Browser-supplied role headers are ignored.

`homeassistant_api: true` grants the add-on access to HA Core. Its
`SUPERVISOR_TOKEN` stays on the server and is used only for the role lookup; it is
never returned to the browser. Missing identity or a non-admin account receives
403; an unavailable or malformed HA authorization response receives 503, with no
S3 operation. Older Ingress sessions without identity must be closed and reopened;
there is no fallback to trusting the proxy alone. The future non-admin dashboard
bridge will be a separate read-only interface.

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

## Local verification

Use Python 3.13 (the container runtime) or newer, plus Node.js for the UI helper tests:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node tests/admin_helpers.test.mjs
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
Lovelace card are reference patterns for later stages. Grafana versus native chart
rendering remains a separate decision before the chart milestone.
