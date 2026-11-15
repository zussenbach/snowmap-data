"""How things stand with this project's own Copernicus requests.
  python scripts/cds_pending.py           -> prints how many are still queued/running
  python scripts/cds_pending.py --changed -> exit 0 if any has finished (or failed), else 1
"""
import json, sys
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parent.parent
conf = {}
for line in (Path.home() / ".cdsapirc").read_text().splitlines():
    if ":" in line:
        k, v = line.split(":", 1); conf[k.strip()] = v.strip()
url, key = conf["url"].rstrip("/"), conf["key"]
ids = [rid for f in ROOT.glob("data/*/cerra_cache/pending_jobs.json") for rid in json.loads(f.read_text() or "{}").values()]
active = changed = 0
for rid in ids:
    try:
        r = requests.get(f"{url}/retrieve/v1/jobs/{rid}", headers={"PRIVATE-TOKEN": key}, timeout=60)
        st = "gone" if r.status_code == 404 else str(r.json().get("status", "")).lower()
    except Exception:
        st = "unknown"
    if st in ("accepted", "queued", "running", "unknown"):
        active += 1
    else:
        changed += 1
if "--changed" in sys.argv:
    sys.exit(0 if changed else 1)
print(active + changed)
