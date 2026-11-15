"""
One polite queue for the whole Copernicus account (all resorts together).

Each run (GitHub Actions, every 3 hours):
  1. asks the server which of the account's requests are still queued or
     running - every dataset, including requests made by hand on the website;
     if the server can't be asked, nothing new is sent this run
  2. checks this project's own requests (data/<resort>/cerra_cache/pending_jobs.json):
     finished ones are downloaded, refused or failed ones are dropped
     (and NOT sent again straight away)
  3. only then adds new requests, one at a time, always the nearest January
     first and going down from there (all resorts together), until the account has 3 requests in the queue -
     so a new one goes in only when an earlier one has finished
  4. after any refusal ("queue full" or similar) it sends nothing more for
     COOLDOWN_HOURS (kept in data/cds_state.json)

Needs: pip install -U cdsapi requests ; the key in ~/.cdsapirc
"""
import datetime
import json
import os
import sys
import time
from pathlib import Path

import cdsapi
import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "cds_state.json"
MAX_ACTIVE = 3          # requests the account may have queued/running at once
COOLDOWN_HOURS = 6      # after a refusal, send nothing new for this long
SEND_GAP = 20           # seconds between two new requests
VERDICT_CHECKS = 6      # after sending, check the server's verdict this many times ...
VERDICT_WAIT = 5        # ... this many seconds apart
DATASET = "reanalysis-cerra-single-levels"
ACTIVE = ("accepted", "queued", "running")
DONE = ("successful", "completed")
DEAD = ("failed", "rejected", "dismissed", "deleted")

HARVEST_DAYS = int(os.environ.get("CERRA_HARVEST_DAYS", "3"))

RUN_TIMES = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def cdsapirc():
    conf = {}
    for line in (Path.home() / ".cdsapirc").read_text().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            conf[k.strip()] = v.strip()
    return conf["url"].rstrip("/"), conf["key"]


def request_for(resort, conf, year):
    lat, lon = conf["lat"], conf["lon"]
    return {
        "level_type": "surface_or_atmosphere",
        "data_type": ["reanalysis"],
        "product_type": "forecast",
        "variable": ["snow_fall_water_equivalent"],
        "year": [year],
        "month": ["01"],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": RUN_TIMES,
        "leadtime_hour": ["1", "2", "3"],
        "data_format": "grib",
        "grid": "0.05/0.05",
        "area": [lat + conf.get("half_lat", 0.25), lon - conf.get("half_lon", 0.25), lat - conf.get("half_lat", 0.25), lon + conf.get("half_lon", 0.25)],
    }


def account_active(url, key):
    """IDs of the account's requests that are queued or running (any dataset),
    or None if the server could not be asked."""
    try:
        r = requests.get(f"{url}/retrieve/v1/jobs",
                         params=[("status", s) for s in ("accepted", "running")] + [("limit", 100)],
                         headers={"PRIVATE-TOKEN": key}, timeout=60)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        return [j.get("jobID") or j.get("id") for j in jobs if str(j.get("status", "")).lower() in ACTIVE]
    except Exception as e:
        print(f"Could not ask the server for the account's queue ({e}); sending nothing new this run.")
        return None


def status_of(url, key, rid):
    """'accepted' / 'running' / 'successful' / 'failed' / ... or 'gone' if deleted."""
    try:
        r = requests.get(f"{url}/retrieve/v1/jobs/{rid}", headers={"PRIVATE-TOKEN": key}, timeout=60)
        if r.status_code == 404:
            return "gone"
        r.raise_for_status()
        return str(r.json().get("status", "")).lower()
    except Exception as e:
        print(f"   (could not check request {rid}: {e})")
        return "unknown"


def find_request(obj):
    """The request inside a job's details (the dict holding 'area' and 'year')."""
    if isinstance(obj, dict):
        if "area" in obj and "year" in obj:
            return obj
        for v in obj.values():
            r = find_request(v)
            if r: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_request(v)
            if r: return r
    return None


def harvest(url, key, all_resorts, days=HARVEST_DAYS):
    """Every finished CERRA January of the last few days - also ones asked for
    by hand on the website - that belongs to a resort and isn't saved yet gets
    downloaded, so nothing finished is ever missed."""
    H = {"PRIVATE-TOKEN": key}
    try:
        r = requests.get(f"{url}/retrieve/v1/jobs", params=[("status", "successful"), ("limit", 100)], headers=H, timeout=60)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    except Exception as e:
        print(f"(could not list the finished requests: {e})")
        return
    since = now() - datetime.timedelta(days=days)
    found = 0
    for j in jobs:
        if j.get("processID") not in (None, DATASET):
            continue
        t = j.get("finished") or j.get("updated") or j.get("created") or ""
        try:
            if datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) < since:
                continue
        except Exception:
            pass
        rid = j.get("jobID") or j.get("id")
        try:
            d = requests.get(f"{url}/retrieve/v1/jobs/{rid}", params={"request": "true"}, headers=H, timeout=60)
            d.raise_for_status()
            req = find_request(d.json())
        except Exception as e:
            print(f"   (could not read request {rid}: {e})")
            continue
        if not req:
            continue
        months = req.get("month", ["01"]); months = [months] if isinstance(months, str) else months
        years = req.get("year"); years = [years] if isinstance(years, str) else (years or [])
        if [str(m).zfill(2) for m in months] != ["01"] or len(years) != 1:
            continue
        a = req.get("area") or []
        if len(a) != 4:
            continue
        lat, lon = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
        name = next((n for n, c in all_resorts.items() if abs(c["lat"] - lat) < 0.03 and abs(c["lon"] - lon) < 0.03), None)
        if not name:
            continue
        year = str(years[0])
        cache = ROOT / "data" / name / "cerra_cache"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"cerra_snowfall_{name}_jan_{year}.grib"
        if target.exists():
            continue
        try:
            res = requests.get(f"{url}/retrieve/v1/jobs/{rid}/results", headers=H, timeout=60)
            res.raise_for_status()
            tmp = target.with_name(target.name + ".part")
            with requests.get(res.json()["asset"]["value"]["href"], stream=True, timeout=600) as dl:
                dl.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in dl.iter_content(1 << 20): fh.write(chunk)
            tmp.replace(target)
            found += 1
            print(f"[{name} {year}] finished request {rid} found and downloaded")
            pend = cache / "pending_jobs.json"
            p = load_json(pend, {})
            if p.pop(year, None):
                pend.write_text(json.dumps(p, indent=1))
        except Exception as e:
            print(f"[{name} {year}] finished request {rid}: download failed ({e})")
    print(f"Checked the finished requests of the last {days} days: {found} new January(s) saved.")


def main():
    url, key = cdsapirc()
    all_resorts = load_json(ROOT / "resorts.json", {})
    harvest(url, key, all_resorts)
    resorts = {k: v for k, v in all_resorts.items() if v.get("cerra")}
    if len(sys.argv) > 1:
        resorts = {k: v for k, v in resorts.items() if k in sys.argv[1:]} or resorts
    state = load_json(STATE, {})
    today = datetime.date.today()
    last_year = today.year if today.month >= 2 else today.year - 1

    try:
        client = cdsapi.Client(wait_until_complete=False, timeout=120, retry_max=3, sleep_max=30)
    except TypeError:
        client = cdsapi.Client(wait_until_complete=False)
    inner = getattr(client, "client", None)

    # --- 2. our own requests: download finished ones, drop dead ones
    refused = False
    wanted = []            # (resort, year, target) still to get, in order
    for name, conf in resorts.items():
        cache = ROOT / "data" / name / "cerra_cache"
        cache.mkdir(parents=True, exist_ok=True)
        pend_file = cache / "pending_jobs.json"
        pending = load_json(pend_file, {})
        for year in sorted(pending, reverse=True):
            rid = pending[year]
            target = cache / f"cerra_snowfall_{name}_jan_{year}.grib"
            st = status_of(url, key, rid)
            if st in DONE:
                try:
                    tmp = target.with_name(target.name + ".part")
                    if inner is not None and hasattr(inner, "get_remote"):
                        inner.get_remote(rid).download(str(tmp))
                    else:
                        res = requests.get(f"{url}/retrieve/v1/jobs/{rid}/results", headers={"PRIVATE-TOKEN": key}, timeout=60)
                        res.raise_for_status()
                        with requests.get(res.json()["asset"]["value"]["href"], stream=True, timeout=600) as dl:
                            dl.raise_for_status()
                            with open(tmp, "wb") as fh:
                                for chunk in dl.iter_content(1 << 20): fh.write(chunk)
                    tmp.replace(target)
                    print(f"[{name} {year}] downloaded")
                    pending.pop(year)
                    state.get("failed", {}).pop(f"{name} {year}", None)
                except Exception as e:
                    print(f"[{name} {year}] finished but the download failed ({e}); trying again next run")
            elif st in DEAD or st == "gone":
                pending.pop(year)
                # a January whose request failed at Copernicus is asked for again
                # later: after 2 h the first time, after 24 h if it fails again
                fk = f"{name} {year}"
                fl = state.setdefault("failed", {}).get(fk, {"count": 0})
                fl["count"] += 1
                fl["until"] = (now() + datetime.timedelta(hours=2 if fl["count"] == 1 else 24)).isoformat()
                state["failed"][fk] = fl
                print(f"[{name} {year}] request {rid} is {st}: dropped; asked for again after {fl['until'][:16]} UTC")
                if st == "rejected":
                    refused = True
            else:
                print(f"[{name} {year}] request {rid}: {st}")
        pend_file.write_text(json.dumps(pending, indent=1))
        first = conf.get("first_year", 2015)
        for y in range(last_year, first - 1, -1):
            year = str(y)
            target = cache / f"cerra_snowfall_{name}_jan_{year}.grib"
            fl = state.get("failed", {}).get(f"{name} {year}")
            if fl and datetime.datetime.fromisoformat(fl["until"]) > now():
                continue                      # failed recently: wait (see above)
            if not target.exists() and year not in pending:
                wanted.append((name, year, target))

    # always the nearest January first, going down from there; for the same
    # year the resorts go in resorts.json order (e.g. belpiano 2025, damuels 2025, belpiano 2024 ...)
    # a resort with "priority" in resorts.json goes first (higher first), then
    # as before: nearest January first
    wanted.sort(key=lambda w: (-float(resorts[w[0]].get("priority", 0)), -int(w[1])))

    # --- 1 + 3. ask the server, then fill the free places only
    if refused:
        state["cooldown_until"] = (now() + datetime.timedelta(hours=COOLDOWN_HOURS)).isoformat()
    cool = state.get("cooldown_until")
    if cool and datetime.datetime.fromisoformat(cool) > now():
        print(f"Cooling down after a refusal: nothing new is sent before {cool[:16]} UTC.")
        wanted = []
    active = account_active(url, key) if wanted else []
    if active is None:
        wanted = []
    elif wanted:
        print(f"The account has {len(active)} request(s) queued or running "
              f"(at most {MAX_ACTIVE}); {len(wanted)} January(s) still to request.")
    free = max(0, MAX_ACTIVE - len(active or []))
    sent = 0
    for name, year, target in wanted:
        if sent >= free:
            break
        if sent:
            time.sleep(SEND_GAP)
        try:
            r = client.retrieve(DATASET, request_for(name, resorts[name], year))
            rid = r.request_id
        except Exception as e:
            print(f"[{name} {year}] could not be sent ({e}); stopping for this run")
            state["cooldown_until"] = (now() + datetime.timedelta(hours=COOLDOWN_HOURS)).isoformat()
            break
        st = "accepted"
        for _ in range(VERDICT_CHECKS):
            time.sleep(VERDICT_WAIT)
            st = status_of(url, key, rid)
            if st not in ("accepted", "unknown"):
                break
        if st in DEAD:
            print(f"[{name} {year}] refused by the server ({st}). Nothing more is sent for {COOLDOWN_HOURS} h.")
            state["cooldown_until"] = (now() + datetime.timedelta(hours=COOLDOWN_HOURS)).isoformat()
            break
        pend_file = ROOT / "data" / name / "cerra_cache" / "pending_jobs.json"
        pending = load_json(pend_file, {})
        pending[year] = rid
        pend_file.write_text(json.dumps(pending, indent=1))
        print(f"[{name} {year}] sent: request {rid} ({st})")
        sent += 1
    if wanted and active is not None and not sent and free == 0:
        print("The queue is full: new requests wait until one of the queued ones has finished.")
    state["last_run"] = now().isoformat()
    STATE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    main()
