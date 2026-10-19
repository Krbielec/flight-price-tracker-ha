# Flight Price Tracker — first test in Home Assistant

Version 0.3.0 displays S3 checks and lets administrators add, enable or disable
them. Editing existing check definitions and displaying charts are later steps.

1. Add `https://github.com/Krbielec/flight-price-tracker-ha` under **Settings →
   Apps → App store → ⋮ → Repositories** (called **Add-ons / Add-on store** in
   older Home Assistant versions).
2. Refresh the store and install **Flight Price Tracker**. Supported hardware:
   amd64 and aarch64. Home Assistant OS or another installation with Supervisor
   is required; Home Assistant Container alone has no add-on store.
3. In the add-on's **Configuration** tab, set your AWS region, existing S3 bucket,
   dedicated IAM access key ID, and secret key. Enter actual credentials only
   here; do not paste them into GitHub issues or screenshots. Password fields are
   masked, but options and HA backups must still be treated as sensitive.
4. Save, start the add-on, and select **Open Web UI** as an HA administrator.
5. Confirm that the table shows the checks from `config/checks.json`, including
   disabled checks. Use **Refresh** to reread S3. An empty configuration shows
   **No checks configured**; errors are displayed separately.

The IAM identity needs `s3:GetObject` and `s3:PutObject` only for the existing
bucket's `config/checks.json`. It does not modify observations or delete objects.
It exposes no host HTTP port; open it through Home Assistant Ingress.

When upgrading from 0.1.0, restart the add-on and reopen its panel so Ingress
provides your user identity. The add-on now requests internal HA API access to
verify administrator roles, using the server-only Supervisor token. Missing user
identity denies access; unavailable role checks deny access with a service error.

To test enabled-state changes, use **Disable** on a check, refresh, and verify it
remains disabled. Use **Enable** to restore it if desired. Disabling affects future
scheduled collection and preserves all history; it does not cancel a running job.
Only perform this test on a check you intend to change. In two tabs, make a change
in the first and try a change from the stale second tab: it should report a
conflict and require **Refresh**, rather than overwrite the first change.

To test creation, open **Add check** and use a unique lowercase ID, distinct
three-letter airport codes, valid future departure dates, and currency (PLN is the
tested default). You can uncheck **Enabled** to save a check without scheduling
future collection for it. Add it, refresh, and verify the new row and all previous
rows remain. Adding a check does not run the collector immediately.

New dates may range from today through 730 days ahead in Europe/Warsaw. A check
can cover at most 366 departure dates, inclusive. Historical checks remain readable
and can still be enabled or disabled. New IDs allow lowercase letters, digits and
single hyphens between segments, with a maximum length of 128 characters.

Reusing an ID must show a conflict instead of editing or replacing its existing
check. Test concurrent creation with two tabs: save in one, then submit the stale
form in the other. It must require a refresh. Failed saves retain your input; if a
response was lost, inspect the refreshed table before retrying. There is no delete
operation: test checks remain in S3 and can be disabled later.

If installation reports an image error, confirm the GitHub Actions workflow has
completed successfully and the `flight-price-tracker` GHCR package is public.
For startup failures, check the add-on log and required configuration fields.
For S3 failures, check bucket, region, IAM permissions, and object existence.
Do not share credentials or raw AWS debug output while troubleshooting.

Before relying on this version, verify that a non-admin account cannot open the
panel or mutate a check. In addition to `panel_admin`, the backend checks the
Ingress peer and the user's current active administrator role in HA for every
request. A 403 indicates missing identity or insufficient rights; a 503 with
`authorization_unavailable` means the internal HA API could not verify the role.
Never work around this by disabling the authorization check.
