"""Gross-error check of stadiums.csv coordinates against the Open-Meteo geocoding API (same free service, no key).
For each stadium the nearest geocoder hit for its city name must be within 30 km. Writes coord_check.csv.
This detects wrong-hemisphere / wrong-city / typo-in-integer-degree errors only (it is a city-centre check)."""
import math
import pandas as pd
import lib

URL = "https://geocoding-api.open-meteo.com/v1/search"


def hav_km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def main():
    s = lib.load_stadiums()
    om = lib.OpenMeteo(min_interval_s=0.3)
    rows = []
    for sid, r in s.iterrows():
        resp = om.get("geocode", URL, {"name": r.city_check, "count": 20, "language": "en", "format": "json"})
        hits = resp.get("results") or []
        if not hits:
            rows.append(dict(stadium_id=sid, city=r.city_check, nearest_hit_km=None, status="no geocoder hit"))
            continue
        best = min(hits, key=lambda h: hav_km(r.lat, r.lon, h["latitude"], h["longitude"]))
        d = hav_km(r.lat, r.lon, best["latitude"], best["longitude"])
        rows.append(dict(stadium_id=sid, city=r.city_check, hit=f"{best['name']}, {best.get('admin1', '')}, {best.get('country', '')}",
                         nearest_hit_km=round(d, 1), status="ok" if d <= 30 else "CHECK"))
    out = pd.DataFrame(rows)
    out.to_csv(lib.HERE / "coord_check.csv", index=False)
    print(out.status.value_counts().to_dict())
    print(out[out.status != "ok"].to_string())


if __name__ == "__main__":
    main()
