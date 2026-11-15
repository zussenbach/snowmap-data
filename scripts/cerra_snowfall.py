"""
CERRA hourly snowfall for every January, newest year first, for one resort.

    python scripts/cerra_snowfall.py <resort>

The resort's centre comes from resorts.json (lat, lon). Files go to
data/<resort>/: the GRIB per January (cerra_cache/), pending_jobs.json
(requests still queued at Copernicus) and <resort>_cerra_snowfall.json.

Made to run again and again (on GitHub Actions every two hours):
years already downloaded are skipped, queued requests are picked up by
their IDs, and CDS_MAX_MINUTES (environment) stops a run in time,
leaving the rest for the next run.

Needs:  pip install -U cdsapi xarray cfgrib eccodes numpy
and a Copernicus key in ~/.cdsapirc (the workflow writes it from the
CDSAPI_KEY secret).
"""
import datetime
import json
import os
import sys
from pathlib import Path

import cdsapi  # noqa: F401  (used by cds_batch)
import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cds_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
if len(sys.argv) < 2:
    sys.exit("usage: python scripts/cerra_snowfall.py <resort>   (a name from resorts.json)")
RESORT = sys.argv[1]
CONF = json.loads((ROOT / "resorts.json").read_text())[RESORT]

LATITUDE = CONF["lat"]
LONGITUDE = CONF["lon"]
HLAT, HLON = CONF.get('half_lat', 0.25), CONF.get('half_lon', 0.25)   # a wide resort can ask for a wider box
AREA = [LATITUDE + HLAT, LONGITUDE - HLON, LATITUDE - HLAT, LONGITUDE + HLON]  # N, W, S, E

# Newest January first, back to FIRST_YEAR. A January only counts once it is
# over, so before February the newest year is last year.
FIRST_YEAR = CONF.get("first_year", 2015)
_today = datetime.date.today()
LAST_YEAR = _today.year if _today.month >= 2 else _today.year - 1
YEARS = [str(y) for y in range(LAST_YEAR, FIRST_YEAR - 1, -1)]

OUT_DIR = ROOT / "data" / RESORT
DATA_DIR = OUT_DIR / "cerra_cache"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_JSON = OUT_DIR / f"{RESORT}_cerra_snowfall.json"

RUN_TIMES = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
LEAD_HOURS = ["1", "2", "3"]


# ----------------------------------------------------------------------
# STEP 1: download one GRIB file per January (cached)
# ----------------------------------------------------------------------
def download_year(year: str):
    """The CDS request for one year: (dataset, request, file to save to)."""
    target = DATA_DIR / f"cerra_snowfall_{RESORT}_jan_{year}.grib"
    request = {
        "level_type": "surface_or_atmosphere",
        "data_type": ["reanalysis"],
        "product_type": "forecast",
        "variable": ["snow_fall_water_equivalent"],  # <- check against "Show API request"
        "year": [year],
        "month": ["01"],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": RUN_TIMES,
        "leadtime_hour": LEAD_HOURS,
        "data_format": "grib",
        # CERRA's native grid is Lambert conformal, and CDS can't crop
        # that directly (the same "croppedRepresentation() not
        # implemented" error we hit with CERRA-Land). Regridding to plain
        # 0.05 degree lat/lon first (about CERRA's real 5.5 km) fixes it.
        "grid": "0.05/0.05",
        "area": AREA,
    }
    return ("reanalysis-cerra-single-levels", request, target)


# ----------------------------------------------------------------------
# STEP 2: turn accumulations into snowfall per hour
# ----------------------------------------------------------------------
def open_grib(path: Path) -> xr.Dataset:
    return xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})


def process_year(path: Path, points: list) -> dict:
    ds = open_grib(path)
    var = list(ds.data_vars)[0]
    da = ds[var]
    if "step" not in da.dims:
        raise RuntimeError(f"{path.name}: expected separate run-time and lead-time dimensions, got {da.dims}")

    lats = ds["latitude"].values
    lons = ds["longitude"].values
    if lats.ndim == 1:
        lons2d, lats2d = np.meshgrid(lons, lats)
    else:
        lats2d, lons2d = lats, lons

    if not points:
        # Small tolerance: 46.1075 + 0.25 is 46.35749999... in floating
        # point, which would otherwise drop the outer row and column of
        # grid points (110 points instead of the full 11 x 11 = 121).
        tol = 1e-3
        ys, xs = np.where(
            (lats2d >= AREA[2] - tol) & (lats2d <= AREA[0] + tol)
            & (lons2d >= AREA[1] - tol) & (lons2d <= AREA[3] + tol)
        )
        for y, x in zip(ys, xs):
            points.append({"y": int(y), "x": int(x), "lat": round(float(lats2d[y, x]), 4), "lon": round(float(lons2d[y, x]), 4)})
        print(f"  {len(points)} grid points in the area")

    iy = np.array([p["y"] for p in points])
    ix = np.array([p["x"] for p in points])
    vals = da.values[:, :, iy, ix]  # (run, lead, point), kg/m2 = mm of water
    vals = np.nan_to_num(vals, nan=0.0)

    # Accumulated from the start of each run -> per-hour amounts
    hourly = np.diff(vals, axis=1, prepend=0.0)

    # Sanity check: if values were already per-hour rather than accumulated,
    # many of these differences would come out clearly negative.
    neg = float((hourly < -0.05).mean())
    if neg > 0.02:
        print(f"  NOTE: {neg:.1%} of hourly differences are negative, so the values look "
              f"already per-hour, not accumulated. Using them as-is.")
        hourly = vals
    hourly = np.clip(hourly, 0.0, None)

    valid = ds["valid_time"].values  # (run, lead)
    out = {}
    for ri in range(valid.shape[0]):
        for li in range(valid.shape[1]):
            ts = str(valid[ri, li])
            if ts[5:7] != "01":  # the last run's 3rd hour is 1 Feb 00:00, skip it
                continue
            day, hour = str(int(ts[8:10])), str(int(ts[11:13]))
            out.setdefault(day, {})[hour] = [round(float(v), 2) for v in hourly[ri, li]]

    # 1 Jan 00:00 would need the 31 Dec 21:00 run, which isn't requested:
    # mark that one hour as missing rather than invent it.
    missing = sorted(
        (int(d), int(h)) for d in map(str, range(1, 32)) for h in map(str, range(24))
        if h not in out.get(d, {})
    )
    if missing:
        print(f"  hours without data: {missing[:5]}{' ...' if len(missing) > 5 else ''}")
    wet = hourly[hourly > 0.05]
    if wet.size:
        print(f"  wet hours: median {np.median(wet):.2f} mm/h, 99th pct {np.percentile(wet, 99):.2f}, max {wet.max():.2f}")
    return out


def main():
    # all years are queued on the CDS at once (see cds_batch.py)
    if os.environ.get("CERRA_PROCESS_ONLY"):   # GitHub: cds_queue.py does the requesting
        paths = {y: download_year(y)[2] for y in YEARS if download_year(y)[2].exists()}
    else:
        paths = cds_batch.fetch_all({y: download_year(y) for y in YEARS}, DATA_DIR)
    points, data = [], {}
    for year in YEARS:
        if year not in paths:
            continue
        try:
            path = paths[year]
            data[year] = process_year(path, points)
            print(f"[{year}] processed")
        except Exception as e:  # keep going so one failed year doesn't lose the rest
            print(f"[{year}] FAILED: {e}")

    out = {
        "variable": "snowfall water equivalent, mm per hour",
        "points": [{"lat": p["lat"], "lon": p["lon"]} for p in points],
        "rate": data,
        "years_available": sorted(data),
    }
    with open(OUTPUT_JSON, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"\nSaved {OUTPUT_JSON}: {len(points)} points, years {', '.join(sorted(data)) or 'none'}")


if __name__ == "__main__":
    main()
