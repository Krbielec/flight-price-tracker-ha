# Codex working agreement

Read `README.md` and `docs/requirements.md` before changing code. `docs/requirements.md` is the authoritative product and data-contract specification for this repository.

## Repository scope

- This is the **public Home Assistant repository**. It must contain the HA add-on, server-side S3 adapter, Ingress administration panel, HA integration, Lovelace card, tests, and image-building workflow.
- Never copy the AWS collector Lambda, Terraform, AWS credentials, private configuration, downloaded observation data, or Terraform state into this repository.
- The **private** `flight-price-tracker-aws` repository owns Lambda, IAM, EventBridge, SNS, CloudWatch, SQS, budgets, and S3 infrastructure.
- Follow architectural patterns from `https://github.com/Krbielec/calendar` (the `household_calendar/` add-on): `aiohttp` app, Ingress admin UI, `custom_components` HA bridge, custom Lovelace card, GitHub Actions container build. Inspect the current upstream source before copying behavior.

## Development discipline

- Work **one small milestone at a time**, explain scope, show changed files and tests, and stop for review before the next milestone.
- Inspect existing files and Git status before proposing changes. Never invent existing paths, symbols, modules, or user preferences.
- Do not deploy to AWS or Home Assistant, push to GitHub, change account credentials, or delete data without an explicit user request.
- Preserve all unrelated user changes. Prefer incremental commits after user approval.
- Add focused tests for S3 adapters, check validation, read contracts, read/write authorization, and error scenarios. Mock AWS for automated tests; never rely on live paid services in CI.
- Do not leak secrets in logs, tracebacks, browser bundles, CI output, screenshots, or git history. Do not store live credentials in plaintext examples.
- Keep dependencies pinned or bounded intentionally, with documented upgrade strategy.

## Security boundaries

- All AWS SDK calls occur on the server side in the add-on process, never in a Lovelace card or browser JavaScript.
- The Home Assistant Ingress administration panel is admin-only (`panel_admin: true`). All create/update/enable/disable operations are reachable exclusively through an authenticated, server-validated administrator path; do not rely solely on hiding sidebar links or client-side checks.
- Non-admin HA dashboard viewers, including tablet users, can read flight charts without access to the Ingress admin panel. Follow the Calendar add-on's response-returning HA service/event bridge pattern, carefully separating read-only services from mutations.
- The HA IAM user can `GetObject`/`PutObject` only `config/checks.json`, can `GetObject` `observations/*`, and can list only the `observations/` prefix. No object deletion, modification of observations, `raw/*`, or Terraform state.
- Persist configuration in S3, **not** a second authoritative HA database. Ensure updates do not silently overwrite concurrent changes or unknown check fields.

## Data and UI invariants

- `schemaVersion: 1` must be recognized, and unsupported schema versions rejected explicitly.
- A check has a stable `id`, `origin`, `destination`, `departureDateFrom`, `departureDateTo`, `currency`, `enabled`. IATA codes uppercase; dates are inclusive.
- Disabled checks retain all historical observations and may remain visible in Lovelace (the display policy should be configurable).
- Collector produces the cheapest fare **per departure date** once daily; multiple flights on the same date are a deferred requirement, not a reason to discard departure/arrival times.
- Observation record includes `checkedAt` in UTC and per-fare `departure_at`, `arrival_at`, `price`, `currency`, `sold_out`, `unavailable`.
- Naive flight timestamps are **local airport wall-clock times**; do not append `Z` or assume UTC. Clarify localization before displaying timestamps across time zones.
- One check = one distinct chart; each departure date is a separate price series, X is observation/check date and Y is fare. Hover/point detail includes flight departure and arrival time.
- Adding a seventh check automatically produces a seventh chart without manually editing dashboard YAML. Implement this as one dynamic custom Lovelace card containing as many chart panels as necessary; do not claim HA automatically creates persistent YAML cards unless separately implemented.
- Keep Grafana integration as an architectural option. Do not silently replace the user's Grafana goal with another chart library without discussion; evaluate feasibility and complexity before the chart milestone.

## Initial milestones

1. Minimal HA add-on skeleton, Ingress admin page, boto3 S3 checks read, mocked tests.
2. Admin CRUD/enable/disable with strict validation, authorization, optimistic concurrency, and tests.
3. HA read-only bridge and observation retrieval/pagination; test non-admin use.
4. Dynamic Lovelace chart card (or Grafana-backed rendering after choice), accessible from existing tablet dashboard.
5. CI image publishing/install instructions and operational smoke tests.

Consult `docs/requirements.md` for exact example payloads and acceptance criteria.
