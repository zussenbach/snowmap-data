#!/usr/bin/env python3
"""GitHub Actions minutes used by snowmap - every run, this month.

    python3 snowmap_minutes.py              # this month
    python3 snowmap_minutes.py --month 2026-11

GitHub bills each job rounded UP to a whole minute; a run that never started
(e.g. blocked by billing) costs nothing. Runs in PUBLIC repos are free.

Needs Python 3.8+ only. GitHub token: GITHUB_TOKEN, `gh auth token`, or
~/.snowmap_github_token (needs Actions: Read-only on both repos).
"""
import datetime as dt
import json
import math
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPOS = ["zussenbach/snowmap", "zussenbach/snowmap-data"]   # private (counts) and public (free)
FREE_MINUTES = 2000                # included each month in a Free plan (private repos)
PRICE = 0.006                      # US$ per Linux minute beyond that (approximate)


def token():
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
    sys.exit("No GitHub token: set GITHUB_TOKEN or save it in ~/.snowmap_github_token")


H = {"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json", "User-Agent": "snowmap-minutes"}


def get(url, **params):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60) as r:
        return json.loads(r.read().decode())


def t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def main():
    if "--month" in sys.argv:
        y, m = map(int, sys.argv[sys.argv.index("--month") + 1].split("-"))
    else:
        today = dt.date.today()
        y, m = today.year, today.month
    start = dt.date(y, m, 1)
    end = dt.date(y + (m == 12), m % 12 + 1, 1)

    total_paid_kind = 0
    for repo in REPOS:
        info = get(f"https://api.github.com/repos/{repo}")
        public = not info.get("private")
        print(f"\n{repo}  ({'public - free' if public else 'private - counts'})   {start:%B %Y}")
        print(f"  {'started (local time)':<20} {'workflow':<16} {'trigger':<18} {'result':<10} min")
        runs, page = [], 1
        while True:
            d = get(f"https://api.github.com/repos/{repo}/actions/runs", per_page=100, page=page,
                    created=f"{start.isoformat()}..{(end - dt.timedelta(days=1)).isoformat()}")
            runs += d["workflow_runs"]
            if len(d["workflow_runs"]) < 100:
                break
            page += 1
        repo_min, by_wf = 0, {}
        for r in sorted(runs, key=lambda r: r["created_at"]):
            mins = 0
            jobs = get(f"https://api.github.com/repos/{repo}/actions/runs/{r['id']}/jobs", per_page=100)["jobs"]
            for j in jobs:
                a, b = t(j.get("started_at")), t(j.get("completed_at"))
                if j.get("steps") and a and b and b > a:          # job really ran
                    mins += math.ceil((b - a).total_seconds() / 60)
                elif j.get("steps") and a and not b:               # still running
                    mins += math.ceil((dt.datetime.now(dt.timezone.utc) - a).total_seconds() / 60)
            repo_min += mins
            by_wf[r["name"]] = by_wf.get(r["name"], 0) + mins
            res = r["conclusion"] or r["status"]
            print(f"  {t(r['created_at']).astimezone():%d %b %H:%M}         {r['name'][:16]:<16} "
                  f"{r['event'][:18]:<18} {res:<10} {mins:>4}")
        print(f"  {'-' * 70}")
        for wf, mn in sorted(by_wf.items(), key=lambda x: -x[1]):
            print(f"  {wf:<36} {mn:>6} min")
        print(f"  {'TOTAL':<36} {repo_min:>6} min")
        if not public:
            total_paid_kind += repo_min

    over = max(0, total_paid_kind - FREE_MINUTES)
    print(f"\nPrivate-repo minutes this month: {total_paid_kind} of {FREE_MINUTES} included"
          + (f"  ->  {over} paid minutes, about ${over * PRICE:.2f}" if over else f"  ({FREE_MINUTES - total_paid_kind} left)"))
    print("(Counted from these repos only. GitHub's own figure: Settings > Billing and licensing > Usage.)")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub said {e.code} {e.reason} - check the token can read Actions on {', '.join(REPOS)}")
