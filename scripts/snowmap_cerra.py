#!/usr/bin/env python3
"""snowmap weather data from your own computer (does the GitHub run's job).

Each round it:
  1. looks at the last 10 finished CERRA requests on your Copernicus account;
     any January that snowmap doesn't have yet is downloaded, named
     cerra_snowfall_<resort>_jan_<year>.grib, kept in ./snowmap_data/ and
     uploaded to the public snowmap-data repo on GitHub
  2. shows what is running / queued at Copernicus, what is complete, what is next
  3. tops the Copernicus queue up to 3 requests - never more. It counts every
     queued or running request on the account (also ones you sent by hand)
     and sends only into free places, 20 s apart. If Copernicus refuses one,
     it sends nothing new for 6 hours.

Run:
    python3 snowmap_cerra.py            # one round
    python3 snowmap_cerra.py --watch    # a round every 5 minutes until you press Ctrl+C
    python3 snowmap_cerra.py --look     # only look: download nothing, send nothing

Needs Python 3.8+ only (no extra packages).
Keys (asked once, can be saved for next time):
  * Copernicus API key - ~/.cdsapirc ("key:" line) or CDSAPI_KEY
  * GitHub token       - GITHUB_TOKEN, `gh auth token`, or ~/.snowmap_github_token.
                         Fine-grained token for zussenbach/snowmap-data with
                         Contents: Read and write, Actions: Read-only.
"""
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "zussenbach/snowmap-data"
BRANCH = "main"
CDS = "https://cds.climate.copernicus.eu/api/retrieve/v1"
DATASET = "reanalysis-cerra-single-levels"
MAX_ACTIVE = 3          # requests the account may have queued/running at once
SEND_GAP = 20           # seconds between two new requests
COOLDOWN_H = 6          # after a refusal, send nothing new for this long
LAST_DONE = 10          # finished requests looked at each round
WATCH_EVERY = 300       # seconds between rounds with --watch
FIRST_YEAR = 2015
ACTIVE = ("accepted", "queued", "running")
DONE = ("successful", "completed")
DEAD = ("failed", "rejected", "dismissed", "deleted")
RUN_TIMES = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]

LOOK_ONLY = "--look" in sys.argv
LOCAL = Path.cwd() / "snowmap_data"


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def local(t):
    return t.astimezone().strftime("%a %d %b %H:%M") if t else "?"


def years_text(ys):
    ys = sorted(int(y) for y in ys)
    out, i = [], 0
    while i < len(ys):
        j = i
        while j + 1 < len(ys) and ys[j + 1] == ys[j] + 1:
            j += 1
        out.append(str(ys[i]) if i == j else f"{ys[i]}-{ys[j]}")
        i = j + 1
    return ", ".join(out) or "none"


def http(method, url, headers, params=None, body=None, raw=False):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers, **({"Content-Type": "application/json"} if data else {}))
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        b = r.read()
        return b if raw else (json.loads(b.decode()) if b else {})


# ------------------------------------------------------------------ keys
def cds_key():
    if os.environ.get("CDSAPI_KEY"):
        return os.environ["CDSAPI_KEY"]
    rc = Path.home() / ".cdsapirc"
    if rc.exists():
        for line in rc.read_text().splitlines():
            if line.strip().startswith("key:"):
                return line.split(":", 1)[1].strip()
    v = input("Copernicus API key (your profile page on cds.climate.copernicus.eu): ").strip()
    if v and input(f"Save it to {rc} for next time? [y/N] ").strip().lower() == "y":
        rc.write_text(f"url: https://cds.climate.copernicus.eu/api\nkey: {v}\n")
    return v


def gh_token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
        if t:
            return t
    except Exception:
        pass
    f = Path.home() / ".snowmap_github_token"
    if f.exists():
        return f.read_text().strip()
    v = input("GitHub token for zussenbach/snowmap-data (Contents: Read and write): ").strip()
    if v and input(f"Save it to {f} for next time? [y/N] ").strip().lower() == "y":
        f.write_text(v + "\n")
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return v


# ------------------------------------------------------------------ GitHub
class GitHub:
    def __init__(self, token):
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "User-Agent": "snowmap-cerra"}

    def api(self, method, path, params=None, body=None):
        return http(method, f"https://api.github.com/repos/{REPO}/{path}", self.h, params, body)

    def read(self, path, default=None):
        """(value, sha) of a JSON file in the repo."""
        try:
            d = self.api("GET", f"contents/{path}", {"ref": BRANCH})
            return json.loads(base64.b64decode(d["content"]).decode() or "null"), d["sha"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return default, None
            raise

    def write(self, path, content: bytes, message):
        for _ in range(3):
            try:
                sha = self.api("GET", f"contents/{path}", {"ref": BRANCH}).get("sha")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                sha = None
            body = {"message": message, "content": base64.b64encode(content).decode(), "branch": BRANCH,
                    "committer": {"name": "snowmap local", "email": "41898282+github-actions[bot]@users.noreply.github.com"}}
            if sha:
                body["sha"] = sha
            try:
                return self.api("PUT", f"contents/{path}", body=body)
            except urllib.error.HTTPError as e:
                if e.code not in (409, 422):
                    raise
                time.sleep(3)          # the file changed meanwhile: read it again
        raise RuntimeError(f"could not save {path} to GitHub")

    def write_json(self, path, value, message):
        self.write(path, (json.dumps(value, indent=1) + "\n").encode(), message)


# ------------------------------------------------------------------ Copernicus
class Copernicus:
    def __init__(self, key):
        self.h = {"PRIVATE-TOKEN": key, "User-Agent": "snowmap-cerra"}

    def jobs(self, status=None, limit=100):
        p = [("limit", limit)] + ([("status", s) for s in status] if status else [])
        return http("GET", f"{CDS}/jobs", self.h, p).get("jobs", [])

    def status(self, rid):
        try:
            return str(http("GET", f"{CDS}/jobs/{rid}", self.h).get("status", "")).lower()
        except urllib.error.HTTPError as e:
            return "gone" if e.code == 404 else "unknown"
        except Exception:
            return "unknown"

    def request_of(self, rid):
        return find_request(http("GET", f"{CDS}/jobs/{rid}", self.h, {"request": "true"}))

    def download(self, rid, target: Path):
        res = http("GET", f"{CDS}/jobs/{rid}/results", self.h)
        href = res["asset"]["value"]["href"]
        tmp = target.with_name(target.name + ".part")
        with urllib.request.urlopen(urllib.request.Request(href, headers={"User-Agent": "snowmap-cerra"}), timeout=900) as r, \
                open(tmp, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(target)

    def send(self, request):
        r = http("POST", f"{CDS}/processes/{DATASET}/execution", self.h, body={"inputs": request})
        return r.get("jobID") or r.get("id")


def find_request(obj):
    if isinstance(obj, dict):
        if "area" in obj and "year" in obj:
            return obj
        for v in obj.values():
            r = find_request(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_request(v)
            if r:
                return r
    return None


def which(req, resorts):
    """(resort, year) of a single-January CERRA request, else (None, year)."""
    if not req:
        return None, None
    years = req.get("year")
    years = [years] if isinstance(years, str) else (years or [])
    months = req.get("month", ["01"])
    months = [months] if isinstance(months, str) else months
    a = req.get("area") or []
    if len(years) != 1 or [str(m).zfill(2) for m in months] != ["01"] or len(a) != 4:
        return None, (str(years[0]) if years else None)
    lat, lon = (float(a[0]) + float(a[2])) / 2, (float(a[1]) + float(a[3])) / 2
    name = next((n for n, c in resorts.items() if abs(c["lat"] - lat) < 0.03 and abs(c["lon"] - lon) < 0.03), None)
    return name, str(years[0])


def request_for(conf, year):
    lat, lon = conf["lat"], conf["lon"]
    hl, ho = conf.get("half_lat", 0.25), conf.get("half_lon", 0.25)
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
        "area": [lat + hl, lon - ho, lat - hl, lon + ho],
    }


# ------------------------------------------------------------------ one round
def one_round(g, c):
    print("\n" + "=" * 70)
    print("snowmap weather data -", now().astimezone().strftime("%a %d %b %Y %H:%M"),
          "(look only)" if LOOK_ONLY else "")

    resorts, _ = g.read("resorts.json", {})
    resorts = {k: v for k, v in resorts.items() if v.get("cerra")}
    label = {k: v.get("name", k).split(" (")[0].split(" / ")[0][:22] for k, v in resorts.items()}
    tree = g.api("GET", f"git/trees/{BRANCH}", {"recursive": 1})["tree"]
    have = {k: set() for k in resorts}
    for t in tree:
        p = t["path"]
        if p.startswith("data/") and p.endswith(".grib") and "_jan_" in p and p.split("/")[1] in have:
            have[p.split("/")[1]].add(p.rsplit("_jan_", 1)[1][:4])
    pend, pend_sha = {}, {}
    for k in resorts:
        pend[k], pend_sha[k] = g.read(f"data/{k}/cerra_cache/pending_jobs.json", {})
        pend[k] = pend[k] or {}
    state, _ = g.read("data/cds_state.json", {})
    state = state or {}
    pend_changed = set()

    def save_grib(name, year, rid):
        target = LOCAL / name / f"cerra_snowfall_{name}_jan_{year}.grib"
        target.parent.mkdir(parents=True, exist_ok=True)
        c.download(rid, target)
        g.write(f"data/{name}/cerra_cache/{target.name}", target.read_bytes(),
                f"CERRA snowfall: {label[name]} January {year} (downloaded locally)")
        have[name].add(year)
        if pend[name].pop(year, None):
            pend_changed.add(name)
        state.get("failed", {}).pop(f"{name} {year}", None)
        print(f"  {label[name]:<22} {year}  downloaded -> {target}  and saved to GitHub")

    # --- 1. the last finished requests: anything snowmap doesn't have yet?
    print(f"\nLAST {LAST_DONE} FINISHED REQUESTS AT COPERNICUS")
    done = [j for j in c.jobs(["successful"]) if j.get("processID") in (None, DATASET)]
    done.sort(key=lambda j: j.get("finished") or j.get("created") or "", reverse=True)
    for j in done[:LAST_DONE]:
        rid = j.get("jobID") or j.get("id")
        try:
            name, year = which(c.request_of(rid), resorts)
        except Exception as e:
            print(f"  (could not read request {rid}: {e})")
            continue
        when = local(parse_time(j.get("finished")))
        if not name:
            print(f"  {'(not a snowmap resort)':<22} {year or '?':<5} finished {when}")
        elif year in have[name]:
            print(f"  {label[name]:<22} {year}  finished {when}  already in snowmap")
        elif LOOK_ONLY:
            print(f"  {label[name]:<22} {year}  finished {when}  NOT in snowmap yet")
        else:
            try:
                save_grib(name, year, rid)
            except Exception as e:
                print(f"  {label[name]:<22} {year}  finished {when}  download/save FAILED ({e}); next round tries again")
    if not done:
        print("  none")

    # --- 2. our own pending requests: finished ones collected, dead ones dropped
    for name in resorts:
        for year, rid in sorted(pend[name].items()):
            st = c.status(rid)
            if st in DONE and year not in have[name] and not LOOK_ONLY:
                try:
                    save_grib(name, year, rid)
                except Exception as e:
                    print(f"  {label[name]} {year}: download/save FAILED ({e}); next round tries again")
            elif st in DONE and year in have[name] and not LOOK_ONLY:
                pend[name].pop(year); pend_changed.add(name)
            elif (st in DEAD or st == "gone") and not LOOK_ONLY:
                pend[name].pop(year); pend_changed.add(name)
                fk = f"{name} {year}"
                fl = state.setdefault("failed", {}).get(fk, {"count": 0})
                fl["count"] += 1
                fl["until"] = (now() + dt.timedelta(hours=2 if fl["count"] == 1 else 24)).isoformat()
                state["failed"][fk] = fl
                print(f"  {label[name]} {year}: request {st}; asked for again after {local(parse_time(fl['until']))}")
                if st == "rejected":
                    state["cooldown_until"] = (now() + dt.timedelta(hours=COOLDOWN_H)).isoformat()

    # --- 3. what is running now (the whole account, any dataset)
    active = [j for j in c.jobs(["accepted", "running"]) if str(j.get("status", "")).lower() in ACTIVE]
    print(f"\nRUNNING / QUEUED AT COPERNICUS  ({len(active)} of {MAX_ACTIVE} places used)")
    busy = set()
    for j in sorted(active, key=lambda j: j.get("created") or ""):
        rid = j.get("jobID") or j.get("id")
        try:
            name, year = which(c.request_of(rid), resorts)
        except Exception:
            name, year = None, None
        if name:
            busy.add((name, year))
            if year not in pend[name] and not LOOK_ONLY:
                pend[name][year] = rid; pend_changed.add(name)
        st = str(j.get("status")).lower()
        since = parse_time(j.get("started") if st == "running" else j.get("created"))
        print(f"  {label.get(name, '(not a snowmap resort)'):<22} {year or '?':<5} {st:<9} since {local(since)}")
    if not active:
        print("  nothing")

    # --- 4. complete per resort and what is next
    today = dt.date.today()
    last_year = today.year if today.month >= 2 else today.year - 1
    print("\nCOMPLETE PER RESORT")
    wanted = []
    for k, conf in resorts.items():
        allyears = [str(y) for y in range(conf.get("first_year", FIRST_YEAR), last_year + 1)]
        miss = [y for y in allyears if y not in have[k]]
        print(f"  {label[k]:<22} {years_text(have[k]):<30} {'complete' if not miss else f'{len(miss)} missing'}")
        for y in miss:
            if y in pend[k] or (k, y) in busy:
                continue
            fl = state.get("failed", {}).get(f"{k} {y}")
            wait = parse_time(fl["until"]) if fl else None
            wanted.append((-float(conf.get("priority", 0)), -int(y), k, y, wait if wait and wait > now() else None))
    wanted.sort()
    ready = [w for w in wanted if not w[4]]

    print("\nNEXT TO BE ASKED FOR")
    for i, (_, _, k, y, wait) in enumerate(wanted[:10]):
        print(f"  {i + 1:>2}. {label[k]:<22} {y}" + (f"  after {local(wait)} (failed before)" if wait else ""))
    if len(wanted) > 10:
        print(f"      ... and {len(wanted) - 10} more")
    print(f"  {len(wanted)} January(s) still to ask for." if wanted else "  nothing - all complete or queued")

    # --- 5. top the queue up to 3
    cool = parse_time(state.get("cooldown_until"))
    free = max(0, MAX_ACTIVE - len(active))
    if LOOK_ONLY:
        pass
    elif cool and cool > now():
        print(f"\nCopernicus refused a request earlier: nothing new is sent before {local(cool)}.")
    elif ready and free:
        print(f"\nSENDING ({free} free place(s))")
        for n, (_, _, k, y, _) in enumerate(ready[:free]):
            if n:
                time.sleep(SEND_GAP)
            try:
                rid = c.send(request_for(resorts[k], y))
            except Exception as e:
                print(f"  {label[k]} {y}: could not be sent ({e}); nothing more sent for {COOLDOWN_H} h")
                state["cooldown_until"] = (now() + dt.timedelta(hours=COOLDOWN_H)).isoformat()
                break
            st = "accepted"
            for _ in range(6):
                time.sleep(5)
                st = c.status(rid)
                if st not in ("accepted", "unknown"):
                    break
            if st in DEAD:
                print(f"  {label[k]} {y}: refused ({st}); nothing more sent for {COOLDOWN_H} h")
                state["cooldown_until"] = (now() + dt.timedelta(hours=COOLDOWN_H)).isoformat()
                break
            pend[k][y] = rid; pend_changed.add(k)
            print(f"  {label[k]:<22} {y}  sent: request {rid} ({st})")
    elif ready:
        print("\nThe queue is full (3 of 3): new requests go in when one finishes.")

    # --- 6. save the bookkeeping to GitHub (so the GitHub run agrees, once it works again)
    if not LOOK_ONLY:
        for k in pend_changed:
            g.write_json(f"data/{k}/cerra_cache/pending_jobs.json", pend[k], f"CERRA queue: {label[k]} (local)")
        state["last_local_run"] = now().isoformat()
        g.write_json("data/cds_state.json", state, "CERRA queue state (local)")
    return len(active)


def main():
    c = Copernicus(cds_key())
    g = GitHub(gh_token())
    while True:
        try:
            one_round(g, c)
        except urllib.error.HTTPError as e:
            where = "GitHub" if "github" in e.url else "Copernicus"
            print(f"\n{where} said {e.code} {e.reason} ({e.url})")
            if where == "GitHub" and e.code in (401, 403, 404):
                print("Check the GitHub token: zussenbach/snowmap-data, Contents: Read and write.")
        except Exception as e:
            print(f"\nRound stopped: {e}")
        if "--watch" not in sys.argv:
            break
        print(f"\nNext round in {WATCH_EVERY // 60} min (Ctrl+C to stop)")
        time.sleep(WATCH_EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
