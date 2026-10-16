# Flight Price Tracker — Home Assistant

Home Assistant add-on for viewing flight monitoring checks stored in S3. Daily
Ryanair fare collection runs separately in AWS.

## Current scope

The first milestone provides a Python/aiohttp add-on, an Ingress panel showing
checks, and server-side boto3 reads of `config/checks.json`. There are no write
endpoints, observation reads, HA integration, Lovelace card, or charts yet.

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

AWS calls run only on the server. For this stage, `s3:GetObject` on the single
`config/checks.json` object is sufficient. No AWS deployment or IAM changes are
performed by the add-on. It does not write to S3 or create a local config database.

The manifest enables `panel_admin: true`, exposes only Ingress port 8099 internally,
and publishes no host port. The server accepts only the actual Ingress proxy peer
`172.30.32.2`; forwarded headers cannot bypass that check. Home Assistant handles
Ingress authentication. **The peer check does not prove an administrator role**,
and `panel_admin` controls panel visibility. This stage has only read operations;
independent server-side administrator authorization is required before adding any
mutations. Non-admin dashboard access will use a separate read-only HA bridge in
a later milestone.

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

## Local verification

Use Python 3.13 (the container runtime) or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
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
