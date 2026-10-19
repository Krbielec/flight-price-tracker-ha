# Offline airport catalogue

`airports.json` is a bundled UI catalogue, derived from the OurAirports dataset:

- Source: https://github.com/davidmegginson/ourairports-data
- Pinned commit: `b529623d2a0b09a9be78d37fc7a7a5e1e16e3451`
- CSV: https://raw.githubusercontent.com/davidmegginson/ourairports-data/b529623d2a0b09a9be78d37fc7a7a5e1e16e3451/airports.csv
- Dataset terms: https://ourairports.com/data/
- Repository license: https://github.com/davidmegginson/ourairports-data/blob/b529623d2a0b09a9be78d37fc7a7a5e1e16e3451/LICENSE (Unlicense / public domain).

The catalogue contains 8,798 large, medium and small airports with uppercase
three-letter IATA codes. Closed airports, heliports, seaplane bases, and entries
without IATA codes are excluded. It is a worldwide airport catalogue, not a
Ryanair route or schedule list. A city is the source municipality; missing
municipalities fall back to the airport name. KRK uses Kraków (from the airport
name) instead of its municipality Balice. Full airport names are retained for
search, so Warsaw Modlin can also be found by Warsaw or WMI.

The browser loads this local file through authenticated Ingress. Neither runtime
nor CI downloads the original dataset. The catalogue restricts dropdown choices;
it does not narrow the existing backend read/write syntax contract or hide checks
using codes outside the snapshot.

To update, download `airports.csv` at a reviewed upstream commit outside the
repository, then run from the repository root:

```sh
python3 scripts/build_airport_catalog.py /tmp/airports.csv flight_price_tracker/www/airports.json
```

Review the diff and update the source commit/count above. Duplicate IATA codes in
the source are rejected for manual review rather than silently picking an airport.
