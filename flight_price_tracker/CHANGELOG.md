# Changelog

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
