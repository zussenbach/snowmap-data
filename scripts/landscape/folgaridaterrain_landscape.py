"""
Folgarida–Campiglio (Trentino): close-up terrain for the snow map, the same as
the other resorts.
Makes:
  * folgarida_dem5m_s.tif, folgarida_dem5m_n.tif - the whole map area at 5 m
        (heights in decimetres), ETRS89 / UTM 32N, in two halves (GitHub's 100 MB limit)
  * relief_2m/E_N.png - shaded relief at 2 m, one 1 km x 1 km tile for each square
        around the pistes and lifts (folgarida_fine_cells.json, from the
        OpenStreetMap pistes and lifts)
Sources: Provincia autonoma di Trento, LiDAR PAT 2014 (+2018) DTM 0.5 m, CC BY 4.0
(500 m sheets, ESRI ASCII grids, siatservices.provincia.tn.it/stemdata/2014_lidar_dtm_asc/),
and Copernicus GLO-30 outside Trentino.
Runs on GitHub (Actions > Landscape > folgaridaterrain). Makes folgaridaterrain_landscape_part*.zip.
"""
import json, os, re, sys, time, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from osgeo import gdal, osr
gdal.UseExceptions()
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif')
gdal.SetConfigOption('GDAL_HTTP_TIMEOUT', '120')
gdal.SetConfigOption('GDAL_HTTP_MAX_RETRY', '5')

P = 'folgarida'
LIDAR = 'https://siatservices.provincia.tn.it/stemdata/2014_lidar_dtm_asc/'
DEM_BOUNDS = (616000, 5107000, 654500, 5141000)    # UTM 32N: the map frame + 1 km
GLO = ['/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N46_00_E{0:03d}_00_DEM/'
       'Copernicus_DSM_COG_10_N46_00_E{0:03d}_00_DEM.tif'.format(e) for e in (10, 11)]
UA = {'User-Agent': 'snowmap-terrain/1.0 (personal snow map project)'}
HERE = os.path.dirname(os.path.abspath(__file__))
CELLS = [tuple(c) for c in json.load(open(os.path.join(HERE, 'folgarida_fine_cells.json')))]
print(f'{len(CELLS)} squares of 1 km around the pistes and lifts')
SR = osr.SpatialReference(); SR.ImportFromEPSG(25832)


def fetch(url, path, tries=6):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
                data = r.read()
            if data[:5] in (b'<?xml', b'<!DOC', b'<html'):
                return False
            open(path + '.part', 'wb').write(data); os.replace(path + '.part', path)
            return True
        except Exception as e:
            if '404' in str(e):
                return False
            print(f'   retry {i + 1}: {e}'); time.sleep(5 * (i + 1))
    return False


# ---------- the list of 500 m LiDAR sheets ----------
html = urllib.request.urlopen(urllib.request.Request(LIDAR, headers=UA), timeout=300).read().decode('utf-8', 'replace')
SHEETS = {}
for name in set(re.findall(r'href="(5h(\d{4})(\d{5})_DTM\.asc)"', html)):
    fn, e, n = name
    SHEETS[(int(e) * 100, int(n) * 100)] = fn
print(f'{len(SHEETS)} LiDAR sheets listed')
# where does a sheet's name sit on the sheet (corner or centre)? read one header
k0 = next(iter(sorted(SHEETS)))
os.makedirs('lidar', exist_ok=True)
fetch(LIDAR + SHEETS[k0], 'lidar/' + SHEETS[k0])
ds = gdal.Open('lidar/' + SHEETS[k0]); gt = ds.GetGeoTransform()
SHEET_DX = gt[0] - k0[0]                          # left edge relative to the name
SHEET_DY = gt[3] + gt[5] * ds.RasterYSize - k0[1]   # bottom edge relative to the name
print(f'sheet {SHEETS[k0]}: {ds.RasterXSize} x {ds.RasterYSize} cells of {gt[1]} m, '
      f'left/bottom at name {SHEET_DX:+.0f}/{SHEET_DY:+.0f} m')
ds = None


def sheets_for(b):
    """local paths of the LiDAR sheets touching bounds b, downloaded (8 at a time)"""
    want = [fn for (e, n), fn in SHEETS.items()
            if e + SHEET_DX < b[2] and e + SHEET_DX + 500 > b[0] and n + SHEET_DY < b[3] and n + SHEET_DY + 500 > b[1]]
    with ThreadPoolExecutor(8) as ex:
        ok = list(ex.map(lambda fn: fetch(LIDAR + fn, 'lidar/' + fn), want))
    return ['lidar/' + fn for fn, o in zip(want, ok) if o]


def warp(paths, bounds, res, alg, src_srs='EPSG:25832'):
    if not paths:
        return None
    vrt = gdal.BuildVRT('/vsimem/s.vrt', paths, outputSRS=src_srs) if src_srs else gdal.BuildVRT('/vsimem/s.vrt', paths)
    ds = gdal.Warp('/vsimem/w.tif', vrt, dstSRS='EPSG:25832', outputBounds=bounds, xRes=res, yRes=res,
                   resampleAlg=alg, outputType=gdal.GDT_Float32, dstNodata=-32768, multithread=True)
    a = ds.ReadAsArray().astype(np.float64); ds = None; vrt = None
    a[(a < 150) | (a > 4500)] = np.nan            # no data / fill values
    return a


def fill(a, b):
    if b is None:
        return a
    m = np.isnan(a); a[m] = b[m]; return a


def tidy(keep_from_e):
    """drop downloaded sheets west of what is still needed (disk space)"""
    for f in os.listdir('lidar'):
        m = re.match(r'5h(\d{4})\d{5}_DTM\.asc', f)
        if m and int(m.group(1)) * 100 + 1000 < keep_from_e:
            os.remove('lidar/' + f)


# ---------- 1. the whole area at 5 m, in 5 km strips, saved as two halves ----------
HALVES = [(f'{P}_dem5m_s.tif', DEM_BOUNDS[1], (DEM_BOUNDS[1] + DEM_BOUNDS[3]) // 2),
          (f'{P}_dem5m_n.tif', (DEM_BOUNDS[1] + DEM_BOUNDS[3]) // 2, DEM_BOUNDS[3])]
for name, y0, y1 in HALVES:
    if os.path.exists(name):
        continue
    W = (DEM_BOUNDS[2] - DEM_BOUNDS[0]) // 5
    dem = np.full(((y1 - y0) // 5, W), np.nan)
    for x in range(DEM_BOUNDS[0], DEM_BOUNDS[2], 5000):
        b = (x, y0, min(x + 5000, DEM_BOUNDS[2]), y1)
        paths = sheets_for(b)
        a = warp(paths, b, 5, 'average')
        if a is not None:
            c = (x - DEM_BOUNDS[0]) // 5
            dem[:, c:c + a.shape[1]] = a
        print(f'  {name}: {x}-{b[2]} with {len(paths)} sheets', flush=True)
        for p in paths:                       # 5 m done: free the disk (2 m tiles fetch again what they need)
            os.remove(p)
    if np.isnan(dem).any():
        print(f'  {np.isnan(dem).mean() * 100:.1f}% without LiDAR: filling from Copernicus GLO-30')
        dem = fill(dem, warp(GLO, (DEM_BOUNDS[0], y0, DEM_BOUNDS[2], y1), 5, 'cubicspline', src_srs=None))
    dem = np.nan_to_num(dem, nan=0)
    out = gdal.GetDriverByName('GTiff').Create(name, dem.shape[1], dem.shape[0], 1, gdal.GDT_UInt16,
                                               options=['COMPRESS=DEFLATE', 'PREDICTOR=2', 'ZLEVEL=9'])
    out.SetGeoTransform((DEM_BOUNDS[0], 5, 0, y1, 0, -5)); out.SetProjection(SR.ExportToWkt())
    bd = out.GetRasterBand(1); bd.WriteArray(np.round(dem * 10).astype(np.uint16)); bd.SetNoDataValue(0)
    out = None; dem = None
    print('  saved', name, round(os.path.getsize(name) / 1e6, 1), 'MB', flush=True)


# ---------- 2. 2 m shaded relief tiles (the same shading as the map) ----------
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
B = 8
skipped = 0
for k, (e, n) in enumerate(CELLS, 1):
    out = f'relief_2m/{e}_{n}.png'
    if os.path.exists(out) or os.path.exists(out + '.skip'):
        continue
    bounds = (e - B, n - B, e + 1000 + B, n + 1000 + B)
    a = warp(sheets_for(bounds), bounds, 2, 'average')
    if a is None or np.isnan(a).mean() > 0.5:          # no LiDAR here: the 5 m map covers it
        open(out + '.skip', 'w').close(); skipped += 1; continue
    ds = gdal.Warp('/vsimem/c.tif', dem5, outputBounds=bounds, xRes=2, yRes=2, resampleAlg='cubicspline',
                   outputType=gdal.GDT_Float32)
    a = fill(a, ds.ReadAsArray().astype(np.float64) / 10.0); ds = None
    img = shade(a, 2.0)[B // 2:-B // 2, B // 2:-B // 2]
    mem = gdal.GetDriverByName('MEM').Create('', img.shape[1], img.shape[0], 1, gdal.GDT_Byte)
    mem.GetRasterBand(1).WriteArray(img)
    gdal.GetDriverByName('PNG').CreateCopy(out, mem, options=['ZLEVEL=9']); mem = None
    tidy(e)
    print(f'  2 m tile {k}/{len(CELLS)}: {out}', flush=True)
print(f'  ({skipped} squares without LiDAR left to the 5 m map)')

# ---------- 3. pack for upload ----------
files = [h[0] for h in HALVES] + sorted('relief_2m/' + f for f in os.listdir('relief_2m') if f.endswith('.png'))
part, size, z = 1, 0, None
for f in files:
    s = os.path.getsize(f)
    if z is None or size + s > 25e6:
        if z: z.close()
        z = zipfile.ZipFile(f'folgaridaterrain_landscape_part{part}.zip', 'w', zipfile.ZIP_DEFLATED); part += 1; size = 0
    z.write(f); size += s
z.close()
print('\nDone:', ', '.join(f'folgaridaterrain_landscape_part{i}.zip' for i in range(1, part)))
