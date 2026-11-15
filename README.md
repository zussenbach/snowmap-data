# snowmap-data

Weather and landscape data for the snowmap app. It's kept in a public repo so the GitHub Actions runs are free.

| Workflow (Actions tab) | What it does | When |
|---|---|---|
| **CERRA snowfall** | Requests January snowfall from Copernicus (at most 3 requests at a time), downloads each January when it's ready, and rebuilds `data/<resort>/<resort>_cerra_snowfall.json`. While requests are waiting, it checks every 5 minutes. | Every 3 hours on its own, or by hand |
| **Landscape** | Downloads OpenStreetMap data and terrain for one resort by running `scripts/landscape/<resort>_landscape.py`. The results go to the branch `landscape-<resort>`. | By hand, once per resort (or per part) |

- `resorts.json` lists the resorts and their centres. The weather is collected for every resort marked `"cerra": true`.
- `scripts/snowmap_cerra.py` does the same weather job from your own computer, with `--watch` or `--look`.
- `scripts/snowmap_minutes.py` reports the GitHub Actions minutes used in this repo and in the private app repo.

## Setup

1. Add the secret `CDSAPI_KEY` (your Copernicus API token) under Settings → Secrets and variables → Actions.
2. The CERRA licence must be accepted once on the Copernicus website.

## Data sources and credits

- Snowfall: Copernicus Climate Change Service (C3S), CERRA sub-daily regional reanalysis data for Europe on single levels, 1984 to present. Contains modified Copernicus Climate Change Service information. Neither the European Commission nor ECMWF is responsible for any use of this information.
- Map data: © OpenStreetMap contributors, ODbL.
- Terrain: swissALTI3D © swisstopo and other national elevation models, as named in each landscape script.
