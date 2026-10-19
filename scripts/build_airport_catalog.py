"""Build an offline UI catalogue from a locally downloaded OurAirports CSV."""

import argparse
import csv
import json
from pathlib import Path
import re

# OurAirports uses the airport municipality, Balice, for KRK. Use the served
# city's name from the airport's own name so users can find Kraków directly.
CITY_OVERRIDES = {"KRK": "Kraków"}


def build_catalog(rows):
    airports = {}
    for row in rows:
        code = row.get("iata_code", "")
        if row.get("type") not in {"large_airport", "medium_airport", "small_airport"}:
            continue
        if not re.fullmatch(r"[A-Z]{3}", code):
            continue
        city = CITY_OVERRIDES.get(code) or row.get("municipality", "").strip() or row.get("name", "").strip()
        if not city:
            continue
        if code in airports:
            raise ValueError(f"Duplicate IATA code in source: {code}; review before updating the snapshot.")
        airports[code] = {"code": code, "city": city, "name": row.get("name", "").strip()}
    return [airports[code] for code in sorted(airports)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with args.source.open(encoding="utf-8-sig", newline="") as handle:
        airports = build_catalog(csv.DictReader(handle))
    # One entry per line keeps data changes reviewable without bloating the file.
    output = "[\n" + ",\n".join("  " + json.dumps(item, ensure_ascii=False) for item in airports) + "\n]\n"
    args.destination.write_text(output, encoding="utf-8")
    print(f"Wrote {len(airports)} airports")


if __name__ == "__main__":
    main()
