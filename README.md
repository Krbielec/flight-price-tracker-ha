# Flight Price Tracker — Home Assistant

Home Assistant add-on and Lovelace card for daily Ryanair fare observations collected on AWS.

See [AGENTS.md](AGENTS.md) for implementation rules and [docs/requirements.md](docs/requirements.md) for the product requirements, system architecture, contracts, and delivery plan.

Reference implementation: https://github.com/Krbielec/calendar

## Status

Specification only. The Home Assistant add-on, integration, and card are not yet implemented.

## Repositories

- This repository (`flight-price-tracker-ha`) is intended to be public. Never commit AWS credentials or Terraform state.
- `flight-price-tracker-aws` is private and owns AWS infrastructure and collector code.

## First milestone

Build a minimal HA add-on using Python/aiohttp/boto3, protected Ingress admin page, and read-only `GET /api/checks` backed by S3. Implement and test in small increments.
