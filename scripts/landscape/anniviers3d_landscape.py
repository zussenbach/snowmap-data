"""
Val d'Anniviers: swisstopo swissBUILDINGS3D 3.0 (3D buildings with real roofs, free,
CC BY 4.0 "© swisstopo") for the 3D model pilot.

Reads each building's solid (Building_solid, a 3D triangle surface) and keeps its
triangles, as roof (facing up) or wall (steep), without the floor. Swiss LV95 + heights.
Makes anniviers3d_landscape_part*.zip with buildings_<tile>.npz:
  tri  float32 (n, 3, 3)  triangle corners, metres, relative to the tile's origin (e0, n0, 0)
  bid  int32   (n,)       building number within the tile
  roof uint8   (n,)       1 = roof, 0 = wall
  org  float64 (2,)       e0, n0
Runs on GitHub (Actions > Landscape > anniviers3d).
"""
import json, os, shutil, urllib.request, zipfile
import numpy as np
from osgeo import ogr, osr
ogr.UseExceptions()

STAC = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissbuildings3d_3_0/items'
LL_BOX = (7.36, 46.06, 7.73, 46.35)        # the Val d'Anniviers map frame, west south east north
UA = {'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'}
# the frame in LV95 (a little generous)
t = osr.CoordinateTransformation(osr.SpatialReference(), osr.SpatialReference())
s4326 = osr.SpatialReference(); s4326.ImportFromEPSG(4326); s4326.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
s2056 = osr.SpatialReference(); s2056.ImportFromEPSG(2056)
tr = osr.CoordinateTransformation(s4326, s2056)
corners = [tr.TransformPoint(x, y)[:2] for x in LL_BOX[0::2] for y in LL_BOX[1::2]]
EB = (min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners))
print('frame LV95', [round(v) for v in EB])

items, url = [], STAC + '?bbox=' + ','.join(map(str, LL_BOX)) + '&limit=100'
while url:
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300))
    items += d['features']
    url = next((l['href'] for l in d.get('links', []) if l.get('rel') == 'next'), None)
print(len(items), 'tiles')

os.makedirs('dl', exist_ok=True)
outs, nb_all = [], 0
for k, it in enumerate(items, 1):
    a = next((a for a in it['assets'].values() if a['href'].endswith('.gdb.zip')), None)
    if not a:
        continue
    fn = 'dl/' + a['href'].rsplit('/', 1)[-1]
    for tries in range(4):
        try:
            urllib.request.urlretrieve(a['href'], fn); break
        except Exception as e:
            print('  retry', e)
    xd = 'dl/x'
    shutil.rmtree(xd, ignore_errors=True)
    with zipfile.ZipFile(fn) as z:
        z.extractall(xd)
    os.remove(fn)
    src = next((os.path.join(r, n) for r, ds, fs in os.walk(xd) for n in ds if n.endswith('.gdb')), None)
    ds = ogr.Open(src)
    lyr = ds.GetLayerByName('Building_solid')
    lyr.SetSpatialFilterRect(*EB)
    tris, bids, nb = [], [], 0
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        pts = []
        for i in range(g.GetGeometryCount()):          # each triangle of the TIN
            ring = g.GetGeometryRef(i).GetGeometryRef(0)
            p = ring.GetPoints()
            if len(p) >= 3:
                pts.append(p[:3])
        if pts:
            tris += pts; bids += [nb] * len(pts); nb += 1
    ds = None
    shutil.rmtree(xd, ignore_errors=True)
    if not tris:
        print(f'  {k}/{len(items)} {it["id"]}: no buildings in the frame'); continue
    T = np.array(tris, dtype=np.float64)
    org = np.array([np.floor(T[:, :, 0].min()), np.floor(T[:, :, 1].min())])
    T[:, :, 0] -= org[0]; T[:, :, 1] -= org[1]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-9)
    keep = nz > -0.3                                    # drop the floors (facing down)
    roof = (nz > 0.3).astype(np.uint8)
    out = f'buildings_{it["id"]}.npz'
    np.savez_compressed(out, tri=T[keep].astype(np.float32), bid=np.array(bids, np.int32)[keep], roof=roof[keep], org=org)
    outs.append(out); nb_all += nb
    print(f'  {k}/{len(items)} {it["id"]}: {nb} buildings, {int(keep.sum())} triangles', flush=True)
print(nb_all, 'buildings in all')

part, size, z = 1, 0, None
for f in outs:
    s = os.path.getsize(f)
    if z is None or size + s > 60e6:
        if z: z.close()
        z = zipfile.ZipFile(f'anniviers3d_landscape_part{part}.zip', 'w', zipfile.ZIP_STORED); part += 1; size = 0
    z.write(f); size += s
if z: z.close()
print('done', part - 1, 'parts')
