# Changelog

## 0.6.0

- Queue the collector Lambda for a new enabled check only after the conditional S3
  config write succeeds, passing only checkId; disabled checks, edits and toggles
  do not request collection.
- Report queue acceptance separately from successful creation, preserving the
  created check on invoke failure. Bound SDK calls and do not retry invocation.
- Add collector_function_name and document the additional exact-function IAM
  permission required in the private infrastructure repository.
- Test Lambda calls using botocore stubs and verify creation/trigger boundaries
  and browser feedback, without contacting live AWS.

- Add admin-only editing of departure dates and enabled state; keep ID, route and
  currency immutable and preserve all historical observations and unknown fields.
- Require the editor's original ETag and a conditional S3 write; reject stale or
  concurrent changes without silently rebasing a draft after list refresh.
- Retain form values after errors, provide explicit reload/cancel, and lock all
  mutation controls during saves. Editing does not depend on the airport catalogue.
- Validate changed dates against the Warsaw date and 730-day/366-date limits while
  allowing unchanged historical endpoints and existing legacy IDs.
- Add mocked-S3/authorization/validation tests and an offline Chromium admin test.
- Existing HA configuration and Lovelace resources require no changes.

## 0.5.1

- Fit the Y axis to visible available fares with padded bounds, including constant
  and zero fares, instead of forcing a zero baseline.
- Label each observation day on X, preserving gaps and using horizontal scrolling
  for longer histories. Keep touch hit testing accurate after scrolling.
- Show local departure HH:MM from the newest observation in each legend button;
  retain full historical times in point details.

## 0.5.0

- Add one native Lovelace card with dynamic chart panels for all checks; new checks
  appear on automatic refresh without per-check YAML edits.
- Read through HA services only, with sequential pagination, incremental in-memory
  refresh, stale/error states and optional disabled-check visibility.
- Plot per-departure-date series with latest daily observations, deterministic ties,
  gaps for unavailable prices and touch/keyboard details preserving local times.
- Install managed card assets under HA www without changing unrelated files or
  dashboard configuration. Document one-time resource/card registration.
- Add offline aggregation, lifecycle and mobile Chromium tests to CI.
- Include timestamps in add-on logs.

## 0.4.0

- Read S3 observations with bounded, signed pagination and an immutable-object cache.
- Filter by inclusive Warsaw observation dates for incremental refresh.
- Preserve local flight times and normalize unavailable fares to null prices.
- Expose read-only HA response services for authenticated users, including non-admins.
- Install the managed custom integration without modifying HA YAML or unrelated files.
- Keep all writes behind the existing administrator-only Ingress path.

## 0.3.1

- Sort administration data columns by clicking their headings, with direction indicators.
- Preserve local sorting after refreshes and enabled-state changes.
- Use muted green/red Enable/Disable buttons with keyboard focus styles.
- Bundle an offline OurAirports catalogue with searchable city/IATA dropdowns.
- Display city/code labels while keeping the S3 airport-code contract unchanged.

## 0.3.0

- Add new monitoring checks through an administrator-only Ingress form.
- Validate unique slug IDs, routes, currencies and inclusive future date ranges.
- Protect creation with the current config ETag and conditional S3 writes.
- Preserve existing checks and extension fields; retain form input on failures.

## 0.2.0

- Enable/disable existing checks from the Ingress panel.
- Verify active administrator roles against Home Assistant for every request.
- Protect S3 updates with browser ETags and conditional PutObject writes.
- Preserve other checks, compatible unknown fields, and observation history.
- Reject malformed configurations and show conflicts or uncertain write outcomes.

## 0.1.0

- Initial Python/aiohttp add-on and read-only Ingress panel.
- Server-side, validated S3 reads of `config/checks.json`.
- Mocked tests and automatic amd64/aarch64 image builds on GitHub Actions.
