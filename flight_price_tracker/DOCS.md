# Flight Price Tracker — first test in Home Assistant

This version only reads and displays monitoring checks from S3. It cannot change
checks or display price charts.

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

For this version, the IAM identity only needs `s3:GetObject` for the existing
bucket's `config/checks.json`. The add-on performs no writes or deletions. It
exposes no host HTTP port; open it through Home Assistant Ingress.

If installation reports an image error, confirm the GitHub Actions workflow has
completed successfully and the `flight-price-tracker` GHCR package is public.
For startup failures, check the add-on log and required configuration fields.
For S3 failures, check bucket, region, IAM permissions, and object existence.
Do not share credentials or raw AWS debug output while troubleshooting.

Before relying on this version, verify that a non-admin account cannot open the
admin panel. `panel_admin` controls sidebar visibility; proxy checks alone do not
prove an administrator role. Independent authorization must be added before any
future mutation endpoints.
