"""
Submit every year's Copernicus (CDS) request at once, then download each
one as soon as the server has it ready.

Used by the folgarida_*.py scripts - keep this file in the same folder.

Why: CDS runs requests in a queue. Asking for one year, waiting until it
is done and only then asking for the next means a single stuck year holds
up all the others. Here the years go in batches of 3 (Copernicus lets one
user have about 3 requests queued per dataset): the script sends 3 years,
checks each one is accepted, watches them, downloads each as it finishes,
and only when all 3 are downloaded sends the next 3. Meanwhile the
script downloads every year as soon as it is ready.

Request IDs from the Copernicus website can be given on the command line:
    python folgarida_cerra_snowfall.py 2015=<id> 2016=<id> 2017=<id>

If you stop the script (Ctrl+C, closing the window, a lost connection) just
run it again: the request IDs are saved in pending_jobs.json in the cache
folder, so it picks up the SAME requests instead of queueing new ones, and
years already downloaded are skipped.

Needs a recent cdsapi:   pip install -U cdsapi
"""
import json
import os
import sys
import time
from pathlib import Path

import cdsapi

POLL_SECONDS = 30
ACCEPT_WAIT = 3     # seconds between checks that a new request was accepted
ACCEPT_CHECKS = 10  # ... checked up to this many times
BATCH = 3           # years sent together; the next batch goes once all of these are downloaded
RETRY_WAIT = 900    # with nothing of ours queued, wait this long (15 min) after a rejection
SEND_GAP = 10       # pause between two accepted requests
MAX_TRIES = 36      # give a year up after this many rejections (about 3 hours)


def _ready(remote):
    """True when the result can be downloaded, False while queued/running;
    raises if the server says the request failed."""
    r = getattr(remote, "results_ready", None)
    if isinstance(r, bool):
        return r
    status = str(getattr(remote, "status", "")).lower()
    if status in ("successful", "completed"):
        return True
    if status in ("accepted", "queued", "running", ""):
        return False
    raise RuntimeError(f"request {status}")


def fetch_all(jobs, cache_dir):
    """jobs: {year: (dataset, request, target_path)}; target None/existing = cached.
    Returns {year: Path} for every year whose file is on disk at the end."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(exist_ok=True)
    pending_file = cache_dir / "pending_jobs.json"
    try:
        pending = json.loads(pending_file.read_text())
    except Exception:
        pending = {}

    def save_pending():
        pending_file.write_text(json.dumps(pending, indent=1))

    # request IDs typed on the command line (YEAR=ID) are picked up like saved ones,
    # e.g.  python folgarida_cerra_snowfall.py 2015=1a2b3c... 2016=4d5e6f...
    for arg in sys.argv[1:]:
        if "=" in arg:
            y, rid = arg.split("=", 1)
            if y.strip() in jobs and rid.strip():
                pending[y.strip()] = rid.strip()
                print(f"[{y.strip()}] using request {rid.strip()} from the command line")

    todo = {y: j for y, j in jobs.items() if not Path(j[2]).exists()}
    for y, j in jobs.items():
        if y not in todo:
            print(f"[{y}] already downloaded, skipping")
            pending.pop(y, None)

    if not todo:
        save_pending()
        return {y: Path(j[2]) for y, j in jobs.items() if Path(j[2]).exists()}

    try:  # give up on a dead connection in minutes, not hours
        client = cdsapi.Client(wait_until_complete=False, timeout=120, retry_max=10, sleep_max=60)
    except TypeError:
        client = cdsapi.Client(wait_until_complete=False)
    inner = getattr(client, "client", None)  # the new CDS client behind cdsapi
    if inner is None or not hasattr(inner, "get_remote"):
        print("Your cdsapi is too old to queue requests in parallel - run:  pip install -U cdsapi\n"
              "Falling back to one year at a time.")
        client = cdsapi.Client()
        for y, (ds, req, target) in todo.items():
            try:
                print(f"[{y}] requesting ...")
                client.retrieve(ds, req, str(target))
            except Exception as e:
                print(f"[{y}] FAILED: {e}")
        return {y: Path(j[2]) for y, j in jobs.items() if Path(j[2]).exists()}

    # Requests are sent one by one: send a year, check the server ACCEPTED it
    # (Copernicus rejects a request when too many of yours are already
    # queued for that dataset), then send the next. If one is rejected, the
    # script waits and sends that same year again later, while downloading
    # whatever is ready in the meantime.
    waiting = list(todo)            # years not yet accepted by the server
    remotes = {}                    # years accepted and queued/running
    failed = {}
    tries = {}
    last = None

    def capped(err):
        t = str(err).lower()
        return "maximum number" in t or "queued requests" in t or "too many" in t or "rejected" in t

    def accepted(r):
        """Wait a few seconds for the server's verdict on a new request."""
        for _ in range(ACCEPT_CHECKS):
            st = str(getattr(r, "status", "")).lower()
            if st in ("accepted", "running", "successful", "completed", "queued"):
                return True, st
            if st in ("rejected", "failed", "dismissed", "deleted"):
                return False, st
            time.sleep(ACCEPT_WAIT)
        return True, st  # no verdict yet: treat as queued, the download loop will see the outcome

    # reconnect to requests accepted on an earlier run
    for y in list(waiting):
        if y in pending:
            try:
                remotes[y] = inner.get_remote(pending[y])
                waiting.remove(y)
                print(f"[{y}] reconnected to request {pending[y]}")
            except Exception:
                pending.pop(y, None)
    save_pending()

    # On GitHub Actions each run is time-limited: stop in good time and let
    # the next run carry on from pending_jobs.json.
    max_min = float(os.environ.get("CDS_MAX_MINUTES") or 0)
    deadline = time.time() + max_min * 60 if max_min else None

    next_send = 0.0   # time when the next request may be sent
    while waiting or remotes:
        if deadline and time.time() > deadline:
            print(f"\nTime limit ({max_min:g} min) reached - {len(remotes)} request(s) still queued, "
                  f"{len(waiting)} not sent yet. The next run carries on.")
            break
        # 1. send years one at a time, each only after the previous was accepted.
        #    After a rejection nothing more is sent until one of the queued
        #    requests has finished (so the server isn't sent request after request).
        # Strict batches: only when the whole previous batch has been
        # downloaded is the next one (up to BATCH years) sent.
        sending = not remotes
        while sending and waiting and time.time() >= next_send and len(remotes) < BATCH:
            y = waiting[0]
            ds, req, target = todo[y]
            try:
                r = client.retrieve(ds, req)
                ok, st = accepted(r)
            except Exception as e:
                ok, st = False, str(e)
            if ok:
                remotes[y] = r
                pending[y] = r.request_id
                waiting.pop(0)
                save_pending()
                print(f"[{y}] accepted (request {r.request_id}, {st})")
                next_send = time.time() + SEND_GAP
                time.sleep(SEND_GAP)
                continue
            tries[y] = tries.get(y, 0) + 1
            if tries[y] > MAX_TRIES:
                failed[y] = f"rejected {MAX_TRIES} times ({st})"
                print(f"[{y}] giving up after {MAX_TRIES} tries: {st}")
                waiting.pop(0)
                continue
            if capped(st) or st == "rejected":
                print(f"[{y}] refused - server queue full; nothing more is sent this run. "
                      "See https://cds.climate.copernicus.eu/requests and run the script again later.")
                waiting.clear()
                break
            next_send = time.time() + RETRY_WAIT
            when = time.strftime("%H:%M", time.localtime(next_send))
            if capped(st) or st == "rejected":
                print(f"[{y}] rejected - server queue full; sending it again at {when} (try {tries[y]})")
                if tries[y] == 2 and not remotes:
                    print("    Tip: requests from earlier runs may still be queued under your account.\n"
                          "    See https://cds.climate.copernicus.eu/requests and delete any you don't need.")
            else:
                print(f"[{y}] could not be sent ({st}); trying again at {when}")
            break

        # 2. download whatever is ready
        for y in list(remotes):
            target = Path(jobs[y][2])
            try:
                if not _ready(remotes[y]):
                    continue
                tmp = target.with_name(target.name + ".part")
                remotes[y].download(str(tmp))
                tmp.replace(target)
                print(f"[{y}] downloaded -> {target}")
            except Exception as e:
                if capped(e):
                    # refused after all: not sent again in this run (run the script later)
                    failed[y] = "refused by the server (queue full)"
                    waiting.clear()
                    print(f"[{y}] refused (server queue full) - nothing more is sent this run; run the script again later")
                else:
                    failed[y] = str(e)
                    print(f"[{y}] FAILED: {e}")
            remotes.pop(y)
            pending.pop(y, None)
            save_pending()

        if waiting or remotes:
            state = ", ".join(f"{y}: {getattr(r, 'status', '?')}" for y, r in sorted(remotes.items()))
            state += f" | {len(waiting)} not queued yet" if waiting else ""
            if state != last:
                print(f"  in progress - {state}")
                last = state
            time.sleep(POLL_SECONDS)

    if failed:
        print("\nThese years failed (often just not published yet): " + ", ".join(sorted(failed)))
    return {y: Path(j[2]) for y, j in jobs.items() if Path(j[2]).exists()}
