#!/usr/bin/env bash
# One round: Copernicus queue (save finished Januaries, top the queue up to 3),
# process what was downloaded, keep the run report, save it all to the repo.
#   scripts/cerra_cycle.sh "<resorts to process>" [<resort given by hand>]
set -u
LIST="$1"; ONE="${2:-}"
git pull -q --rebase --autostash 2>/dev/null || true   # always work with the latest resorts.json
mkdir -p data/logs
{ echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') · run ${GITHUB_RUN_ID:-local} · ${CYCLE_KIND:-${GITHUB_EVENT_NAME:-local}}"
  timeout --signal=INT 12m python -u scripts/cds_queue.py $ONE 2>&1 || echo "queue step stopped early (exit $?)"
} | tee data/logs/cerra_last.log
for r in $LIST; do
  { CERRA_PROCESS_ONLY=1 python -u scripts/cerra_snowfall.py "$r" 2>&1 || echo "$r: processing failed"; } \
    | grep -E "processed|FAILED|failed|Saved" | sed "s/^/$r: /" | tee -a data/logs/cerra_last.log || true
done
cat data/logs/cerra_last.log >> data/logs/cerra_history.log
tail -n 600 data/logs/cerra_history.log > data/logs/h.tmp && mv data/logs/h.tmp data/logs/cerra_history.log
git config user.name "snowmap bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data
if git diff --cached --quiet; then echo "nothing new"; exit 0; fi
git commit -q -m "CERRA snowfall: $(ls data/*/cerra_cache/*.grib 2>/dev/null | wc -l) Januaries downloaded · run report in data/logs"
for i in 1 2 3; do git pull -q --rebase && git push -q && break; sleep 5; done
