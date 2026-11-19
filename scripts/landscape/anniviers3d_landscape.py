"""
Val d'Anniviers: swisstopo swissBUILDINGS3D 3.0 (3D buildings with real roofs, free,
CC BY 4.0 "© swisstopo") for the 3D model pilot.

Makes anniviers3d_landscape_part*.zip with buildings_<tile>.geojsonl: one line per
building surface (roof / wall / floor) as a 3D polygon, Swiss LV95 + LN02 heights.
Runs on GitHub (Actions > Landscape > anniviers3d).
"""
import json, os, subprocess, urllib.request, zipfile

STAC = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissbuildings3d_3_0/items'
LL_BOX = (7.36, 46.06, 7.73, 46.35)        # the Val d'Anniviers map frame, west south east north
UA = {'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'}

items, url = [], STAC + '?bbox=' + ','.join(map(str, LL_BOX)) + '&limit=100'
while url:
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300))
    items += d['features']
    url = next((l['href'] for l in d.get('links', []) if l.get('rel') == 'next'), None)
print(len(items), 'items')
for it in items[:2]:
    print(it['id'], {k: (a.get('type'), a['href'].rsplit('/', 1)[-1]) for k, a in it['assets'].items()})

os.makedirs('dl', exist_ok=True)
outs = []
for it in items:
    # newest file in the File Geodatabase format (3D surfaces); fall back to anything zipped
    assets = [a for a in it['assets'].values() if a['href'].endswith('.gdb.zip')] or \
             [a for a in it['assets'].values() if a['href'].endswith('.zip')]
    if not assets:
        print('  no usable asset in', it['id']); continue
    a = sorted(assets, key=lambda a: a['href'])[-1]
    fn = 'dl/' + a['href'].rsplit('/', 1)[-1]
    if not os.path.exists(fn):
        print('  downloading', fn, flush=True)
        urllib.request.urlretrieve(a['href'], fn)
    with zipfile.ZipFile(fn) as z:
        z.extractall('dl/x_' + it['id'])
    src = next((os.path.join(r, n) for r, ds, fs in os.walk('dl/x_' + it['id']) for n in ds + fs
                if n.endswith('.gdb')), None)
    if not src:
        print('  no .gdb inside', fn); continue
    layers = subprocess.run(['ogrinfo', '-ro', '-q', src], capture_output=True, text=True).stdout
    print('  layers:', ' | '.join(l.strip() for l in layers.splitlines()[:12]))
    out = f'buildings_{it["id"]}.geojsonl'
    # every layer with 3D surfaces, cut to the frame (in degrees, -spat_srs)
    for line in layers.splitlines():
        if ':' not in line: continue
        name = line.split(':', 1)[1].strip().split(' ')[0]
        subprocess.run(['ogr2ogr', '-f', 'GeoJSONSeq', '-append' if os.path.exists(out) else '-overwrite',
                        out, src, name, '-spat', *map(str, LL_BOX), '-spat_srs', 'EPSG:4326',
                        '-nlt', 'MULTIPOLYGON25D', '-dim', 'XYZ', '-lco', 'RS=NO'], check=False)
    if os.path.exists(out):
        outs.append(out); print('  ->', out, round(os.path.getsize(out) / 1e6, 1), 'MB', flush=True)

part, size, z = 1, 0, None
for f in outs:
    s = os.path.getsize(f)
    if z is None or size + s > 60e6:
        if z: z.close()
        z = zipfile.ZipFile(f'anniviers3d_landscape_part{part}.zip', 'w', zipfile.ZIP_DEFLATED); part += 1; size = 0
    z.write(f); size += s
if z: z.close()
print('done', part - 1, 'parts')
