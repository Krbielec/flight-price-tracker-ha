# Changelog

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
