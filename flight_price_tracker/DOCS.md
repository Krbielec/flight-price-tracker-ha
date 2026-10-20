# Flight Price Tracker — first test in Home Assistant

Version 0.6.0 lets administrators create checks, edit departure dates and enabled
state, and enable/disable collection. ID, route and currency remain fixed. It also
provides read-only HA services and a dynamic Lovelace chart card.

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
bucket's `config/checks.json`, plus `s3:GetObject` for `observations/*` and
`s3:ListBucket` limited to `observations/` prefixes. It does not modify observations
or delete objects.
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

Click a table column heading to sort ascending, then click again for descending.
The direction arrow should update, and your sorting should remain after Refresh
or an enable/disable operation. Airport columns display and sort by city/code
labels. Enable is muted green and Disable is muted red; labels remain visible.

Origin and destination are dropdowns. Search by city or code, then choose a label
such as **Valencia - VLC**. Only `VLC` is saved, not the display label. Filtering
must not silently clear an existing selection. The bundled list covers active
IATA-coded airports worldwide; it is not a Ryanair route list. Existing codes
outside this snapshot remain visible as raw codes and can still be toggled.

To test creation, open **Add check** and use a unique lowercase ID, distinct
airports from the dropdowns, valid future departure dates, and currency (PLN is the
tested default). You can uncheck **Enabled** to save a check without scheduling
future collection for it. Add it, refresh, and verify the new row and all previous
rows remain. A new enabled check requests an initial collection after saving; a disabled check
does not. Wait for the collector to finish and the dashboard to refresh.

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

## Enable the read-only HA integration when you next update

You can update directly to this version without installing 0.3.1 separately.
Start the add-on to install its managed integration files under
`/config/custom_components/flight_price_tracker`. Add the following top-level
entry to your existing HA `configuration.yaml`, merging it with other settings:

```yaml
flight_price_tracker:
```

Restart Home Assistant Core once. This is manual; the add-on does not edit your
YAML or restart HA. Do not edit the integration files managed by the add-on.
Existing unmarked directories and symlinks are refused for safety; check the
add-on log if the integration could not be installed. Other files are preserved.

Test `flight_price_tracker.get_checks` in Developer Tools → Actions. Then call
`flight_price_tracker.get_observations` with your `check_id` and `page_size: 100`.
Pass the returned `nextCursor` as `cursor` with identical parameters until it is
null. An empty page with a cursor is not the end. Cursors expire across add-on
restarts. For incremental refresh, use `checked_from` with the most recent known
observation date (Europe/Warsaw), inclusively. `checked_to` is also supported.

The response includes local flight departure/arrival strings and a Warsaw
`checkedDate`. Unavailable/sold-out fares use null prices, not invented zeros.
Multiple collection runs remain in the response; graph aggregation is a later
stage. An S3 or bridge failure must show an error, never empty successful history.

The same read actions are intended for authenticated non-admin dashboard users.
Verify read access from an authenticated viewer session when you test the future
dashboard; the Ingress admin panel stays administrator-only. There are no write
services or write event commands. Read calls without an active user context fail.
No AWS credentials belong in dashboard configuration. The native Lovelace card was selected for this stage; Grafana remains a future option.


## Add the dynamic chart card

Start the add-on to install `/config/www/flight-price-tracker/`. If HA's `www`
directory was just created for the first time, restart HA Core once to enable
`/local/` files. The integration above must already be enabled.

In **Settings → Dashboards → Resources**, add a **JavaScript module**:

```text
/local/flight-price-tracker/flight-price-tracker-card.js?v=0.5.1
```

Add one manual card to the existing dashboard:

```yaml
type: custom:flight-price-tracker-card
title: Ceny lotów
show_disabled: true
refresh_interval: 60
```

One card renders one chart per check. After adding a check in the admin panel,
wait for the next automatic refresh (60 seconds after the previous read finishes)
or press **Odśwież**: a new panel must appear without any YAML change. Checks with
no observations show an empty-history message. A viewer can hide disabled checks
locally. No viewer operation changes S3 configuration.

Tap a point for fare, local departure and arrival times, including next-day dates.
Legend buttons hide/show individual departure-date series and display the local
departure time from the latest observation, for example `2027-02-14 08:35`.
An unknown time in the latest observation is shown as an em dash.
The Y axis fits visible available prices with padding instead of always starting
at zero. For example, prices 190–550 produce bounds 160–580. Each observation day
has its own X label; longer histories scroll horizontally to avoid overlapping dates. Missing fares break
lines and appear as hollow status markers, not zero prices. The X axis is the
Warsaw observation date. Multiple daily reads use the latest run per departure date.

Test on the tablet and a non-admin dashboard session. The chart must work while
the Ingress admin panel remains inaccessible to that user. Try a temporary add-on
stop: reads must show an error/stale-data warning and recover after restart and
refresh. Pagination cursors are restarted automatically on the next attempt.

The first read loads full history; later reads reread the newest observation day
inclusively. Reload the dashboard after older historical backfills. Bounds per
check: 1,000 pages, 100,000 raw rows per refresh, 20,000 aggregated points. A bound
or mixed-currency failure shows an error instead of a truncated/mixed chart.

Card files are managed only in their dedicated directory; an existing unmanaged
directory is refused. Dashboard resources and YAML are not edited automatically.
For later upgrades, change the resource URL's version and reload the browser.


## Edit check dates and collection state

From 0.5.1, update/start the add-on and reopen its Ingress panel. No Core restart,
configuration.yaml edit or Lovelace resource/YAML change is needed for this upgrade.

Click **Edit** beside a check. Change **Departure from**, **Departure to** and/or
**Enabled**, then **Save changes**. ID, route and currency are shown as fixed text;
a different route or currency needs a new check. Existing observations remain
available in the chart, including flight dates removed from the current window.
Edits affect subsequent scheduled collection; creating a new enabled check also
requests an initial collection, as described below.

Changed dates must be today through 730 days ahead (Europe/Warsaw), inclusive, and
a changed range may cover at most 366 days. An unchanged endpoint may stay in the
past; unchanged historical windows can still have their enabled state changed.

Test a check you intend to modify: save a date range, Refresh, and confirm the new
values and prior observations remain. Try two tabs: open Edit in both, save in
one, then try to save the stale editor. It must report a conflict. Refresh does
not silently rebase the draft; **Reload values** explicitly discards it and loads
the current values/version. Failed requests retain inputs. If a response was lost,
inspect the refreshed values before trying again because the write may have succeeded.
**Cancel editing** closes the form without writing. Other mutation buttons are
locked while saving. Confirm that a non-admin still cannot open or use this panel.


## Enable initial collection after creating a check

The collector already supports a `checkId` event. In add-on options, verify
`collector_function_name` (default `flight-price-tracker-collector`) matches the
function in the configured AWS region/account. Existing options without this field
use the default; no new AWS key is required.

In the private AWS infrastructure repository, extend the HA IAM user's policy with
`lambda:InvokeFunction` on **that one function ARN**, keeping all S3 restrictions.
See the policy template in the repository README. This permission change needs
separate deployment by the infrastructure owner; installing the add-on does not
change AWS permissions. No Lambda/collector code change is needed for the inspected
`checkId` contract. No AWS deployment or paid invocation is performed by tests.

Create one enabled check: the panel should say **Initial collection queued**.
This confirms AWS accepted the request, not that collection finished. Observations
appear after processing and the next card refresh. If permission is missing, the
check is still saved and the panel reports that collection was denied. Fix IAM;
do not create the check again. The next daily scheduled run still processes active
checks. There is no manual recheck/retry action in this version.

A disabled new check does not request initial collection. Editing, enabling an
existing check, stale/duplicate creation requests and failed S3 writes never request
collection. An invoke failure does not roll back the check, and the add-on does
not automatically retry the invoke. Lost replies/process interruptions can leave
collection uncertain; the config write and Lambda request are not one transaction.
