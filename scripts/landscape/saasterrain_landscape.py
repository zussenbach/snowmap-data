"""
Saastal: close-up terrain for the snow map, the same as the other resorts.
Makes:
  * saas_dem5m_s.tif, saas_dem5m_n.tif - the whole map area at 5 m (heights in
                         decimetres), Swiss LV95, in two halves (GitHub's 100 MB limit)
  * relief_2m/E_N.png  - shaded relief at 2 m, one 1 km x 1 km tile for each square
                         around the pistes and lifts (saas_fine_cells.json, worked out
                         from the OpenStreetMap pistes and lifts)
Sources: swissALTI3D (swisstopo, 2 m, open data) and Copernicus GLO-30 for Italy.
Runs on GitHub (Actions > Landscape > saasterrain). Makes saasterrain_landscape_part*.zip.
"""
import json, os, sys, time, urllib.parse, urllib.request, zipfile
import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif')
gdal.SetConfigOption('GDAL_HTTP_TIMEOUT', '120')
gdal.SetConfigOption('GDAL_HTTP_MAX_RETRY', '5')

# Switzerland: swissALTI3D 2 m, 1 km tiles (EPSG:2056), listed by swisstopo's STAC API
STAC = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissalti3d/items?bbox={bbox}&limit=100'
GLO = ['/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N46_00_E{0:03d}_00_DEM/'
       'Copernicus_DSM_COG_10_N46_00_E{0:03d}_00_DEM.tif'.format(e) for e in (7, 8)] + \
      ['/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N45_00_E{0:03d}_00_DEM/'
       'Copernicus_DSM_COG_10_N45_00_E{0:03d}_00_DEM.tif'.format(e) for e in (7, 8)]
DEM_BOUNDS = (2619000, 1087000, 2650000, 1134000)   # Swiss LV95 (EPSG:2056), the Saas map frame + 1 km
LL_BOX = (7.66, 45.93, 8.10, 46.37)                 # the same in degrees (west, south, east, north)
P = 'saas'
print(P, DEM_BOUNDS, LL_BOX)


def fetch(url, path, tries=6):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'snowmap-terrain/1.0'})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            if data[:5] in (b'<?xml', b'<Serv', b'<!DOC', b'<html', b'<ows:'):
                print('   server says:', data[:300]); return False
            open(path + '.part', 'wb').write(data); os.replace(path + '.part', path)
            return True
        except Exception as e:
            print(f'   retry {i + 1}: {e}'); time.sleep(5 * (i + 1))
    return False



UA = 'snowmap-resort-builder/1.0 (personal snow map project)'
CELLS = [tuple(c) for c in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saas_fine_cells.json')))]
print(f'{len(CELLS)} squares of 1 km around the pistes and lifts')

# ---------- 1. the terrain sources ----------
# every swissALTI3D 2 m tile in the area (newest survey of each), downloaded once
os.makedirs('alti2m', exist_ok=True)
best = {}
url = STAC.format(bbox=','.join(f'{v:.4f}' for v in LL_BOX))
while url:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    page = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
    for it in page.get('features', []):
        for a in it.get('assets', {}).values():
            h = a.get('href', '')
            if h.endswith('.tif') and abs(float(a.get('eo:gsd', 0) or 0) - 2.0) < 0.01:
                name = h.rsplit('/', 1)[1]                  # swissalti3d_2019_2607-1125_2_2056_5728.tif
                tile, year = name.split('_')[2], name.split('_')[1]
                if tile not in best or year > best[tile][0]:
                    best[tile] = (year, h)
    url = next((l['href'] for l in page.get('links', []) if l.get('rel') == 'next'), None)
print(f'swissALTI3D: {len(best)} tiles of 1 km')
alti = []
for k, (tile, (year, h)) in enumerate(sorted(best.items()), 1):
    p = f'alti2m/{tile}.tif'
    if fetch(h, p):
        alti.append(p)
    if k % 100 == 0: print(f'  {k}/{len(best)}')
bev_vrt = gdal.BuildVRT('/vsimem/alti.vrt', alti) if alti else None
glo_vrt = gdal.BuildVRT('/vsimem/glo.vrt', GLO)


# ---------- 2. read everything onto one grid ----------
def warp(src, bounds, res, alg):
    """src on the output grid as float array, NaN where there is no data"""
    if src is None:
        return None
    ds = gdal.Warp('/vsimem/w.tif', src, dstSRS='EPSG:2056', outputBounds=bounds, xRes=res, yRes=res,
                   resampleAlg=alg, outputType=gdal.GDT_Float32, dstNodata=-32768, multithread=True)
    a = ds.ReadAsArray().astype(np.float64); ds = None
    a[(a < 300) | (a > 5000)] = np.nan          # no data / fill values (nothing here is below 300 m)
    return a


def fill(a, b):
    if b is None:
        return a
    m = np.isnan(a); a[m] = b[m]; return a


HALVES = [(f'{P}_dem5m_s.tif', DEM_BOUNDS[1], (DEM_BOUNDS[1] + DEM_BOUNDS[3]) // 2),
          (f'{P}_dem5m_n.tif', (DEM_BOUNDS[1] + DEM_BOUNDS[3]) // 2, DEM_BOUNDS[3])]
# the 5 m model is too big for one GitHub file (100 MB), so it is saved as a south and a north half
for name, y0, y1 in HALVES:
    if os.path.exists(name):
        continue
    b = (DEM_BOUNDS[0], y0, DEM_BOUNDS[2], y1)
    print(f'{name}: 5 m (from the 2 m model) ...')
    dem = warp(bev_vrt, b, 5, 'average')
    if dem is None:
        dem = np.full((round((y1 - y0) / 5), round((DEM_BOUNDS[2] - DEM_BOUNDS[0]) / 5)), np.nan)
    if np.isnan(dem).any():
        print(f'  {np.isnan(dem).mean() * 100:.1f}% without swissALTI3D: filling from Copernicus GLO-30')
        dem = fill(dem, warp(glo_vrt, b, 5, 'cubicspline'))
    print(f'  {np.isnan(dem).mean() * 100:.2f}% still empty')
    dem = np.nan_to_num(dem, nan=0)
    out = gdal.GetDriverByName('GTiff').Create(name, dem.shape[1], dem.shape[0], 1, gdal.GDT_UInt16,
                                               options=['COMPRESS=DEFLATE', 'PREDICTOR=2', 'ZLEVEL=9'])
    out.SetGeoTransform((DEM_BOUNDS[0], 5, 0, y1, 0, -5))
    sr = osr.SpatialReference(); sr.ImportFromEPSG(2056); out.SetProjection(sr.ExportToWkt())
    bd = out.GetRasterBand(1); bd.WriteArray(np.round(dem * 10).astype(np.uint16)); bd.SetNoDataValue(0)
    out = None; dem = None
    print('  done', round(os.path.getsize(name) / 1e6, 1), 'MB')


# ---------- 3. 2 m shaded relief tiles (the same shading as the map) ----------
def shade(dem, px):
    d = dem.astype(np.float64)
    p = np.pad(d, 1, mode='edge')       # very light smoothing against laser noise
    d = sum(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9.0
    dzdy, dzdx = np.gradient(d * 1.6, px)
    dzdy = -dzdy
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    az, alt = np.radians(315), np.radians(45)
    s = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - np.pi / 2 + aspect)
    v = np.clip(0.08 + 0.92 * s / np.sin(alt), 0, 1) ** 1.25
    return (255 * (0.42 + 0.58 * v)).astype(np.uint8)


os.makedirs('relief_2m', exist_ok=True)
dem5 = gdal.BuildVRT('/vsimem/dem5.vrt', [h[0] for h in HALVES])
B = 8   # metres of border so tile edges shade correctly
skipped = 0
for k, (e, n) in enumerate(CELLS, 1):
    out = f'relief_2m/{e}_{n}.png'
    if os.path.exists(out) or os.path.exists(out + '.skip'):
        continue
    bounds = (e - B, n - B, e + 1000 + B, n + 1000 + B)
    a = warp(bev_vrt, bounds, 2, 'average')
    if a is None or np.isnan(a).mean() > 0.5:          # no swissALTI3D here: the 5 m map covers it
        open(out + '.skip', 'w').close(); skipped += 1; continue
    ds = gdal.Warp('/vsimem/c.tif', dem5, outputBounds=bounds, xRes=2, yRes=2, resampleAlg='cubicspline',
                   outputType=gdal.GDT_Float32)
    a = fill(a, ds.ReadAsArray().astype(np.float64) / 10.0); ds = None
    img = shade(a, 2.0)[B // 2:-B // 2, B // 2:-B // 2]
    mem = gdal.GetDriverByName('MEM').Create('', img.shape[1], img.shape[0], 1, gdal.GDT_Byte)
    mem.GetRasterBand(1).WriteArray(img)
    gdal.GetDriverByName('PNG').CreateCopy(out, mem, options=['ZLEVEL=9']); mem = None
    print(f'  2 m tile {k}/{len(CELLS)}: {out}')
print(f'  ({skipped} squares without swissALTI3D left to the 5 m map)')

# ---------- 4. pack for upload in parts of at most ~25 MB ----------
files = [h[0] for h in HALVES] + \
    sorted('relief_2m/' + f for f in os.listdir('relief_2m') if f.endswith('.png'))
part, size, z = 1, 0, None
for f in files:
    s = os.path.getsize(f) * (0.15 if f.endswith('.geojson') else 1)     # map data packs down a lot
    if z is None or size + s > 25e6:
        if z: z.close()
        z = zipfile.ZipFile(f'saasterrain_landscape_part{part}.zip', 'w', zipfile.ZIP_DEFLATED); part += 1; size = 0
    z.write(f); size += s
z.close()
print('\nDone. Upload these files:', ', '.join(f'saasterrain_landscape_part{i}.zip' for i in range(1, part)))
