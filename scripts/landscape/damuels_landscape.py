"""
Damüls-Mellau (Bregenzerwald, Vorarlberg): landscape for the snow map
(OpenStreetMap features + terrain), all in one go.

Makes:
  * damuels_skiarea.geojson, damuels_buildings.geojson
                         - pistes, lifts, roads, water, names / buildings,
                           forest, rock, glaciers (OpenStreetMap, ODbL),
                           downloaded from the Overpass API
  * damuels_dem5m.tif    - the whole map area at 5 m (heights in decimetres)
  * relief_2m/E_N.png    - shaded relief at 2 m, one 1 km x 1 km tile for each
                           square around the pistes and lifts

Sources (all downloaded by this script, nothing to click):
  * BEV ALS-DTM, Austria's 1 m laser-scan terrain model (Bundesamt für Eich-
    und Vermessungswesen, CC BY 4.0), read straight from its cloud-optimised
    50 km tiles: only the needed parts are downloaded
  * Copernicus GLO-30 (30 m) for anything outside Austria

Runs on GitHub (Actions > Landscape > damuels) or on the PC
(needs GDAL:  conda install -c conda-forge gdal):
    python damuels_landscape.py
It makes damuels_landscape_part*.zip. If it stops, run it again: everything
already downloaded or made is skipped.
"""
import json, os, sys, time, urllib.parse, urllib.request, zipfile
import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif')
gdal.SetConfigOption('GDAL_HTTP_TIMEOUT', '120')
gdal.SetConfigOption('GDAL_HTTP_MAX_RETRY', '5')

# Austria, 1 m bare earth, 50 km tiles in ETRS89-LAEA (EPSG:3035)
BEV = '/vsicurl/https://data.bev.gv.at/download/ALS/DTM/20240915/ALS_DTM_CRS3035RES50000mN{n}E{e}.tif'
GLO = ['/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N47_00_E{0:03d}_00_DEM/'
       'Copernicus_DSM_COG_10_N47_00_E{0:03d}_00_DEM.tif'.format(e) for e in (9, 10)]
DEM_BOUNDS = (552500, 5228500, 581500, 5253000)   # UTM 32N, map frame + 1 km


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



OVERPASS = ['https://overpass-api.de/api/interpreter',
            'https://overpass.private.coffee/api/interpreter',
            'https://maps.mail.ru/osm/tools/overpass/api/interpreter']
UA = 'snowmap-resort-builder/1.0 (personal snow map project)'

Q_SKI = '''[out:json][timeout:600][bbox:{bbox}];
(
  way["piste:type"]; relation["piste:type"];
  way["aerialway"]; node["aerialway"];
  way["highway"]; way["railway"]; way["waterway"];
  node["place"]; node["natural"~"^(peak|saddle)$"];
);
out geom;'''
Q_LAND = '''[out:json][timeout:600][bbox:{bbox}];
(
  way["building"]; relation["building"];
  way["natural"~"^(wood|glacier|water|bare_rock|scree)$"];
  relation["natural"~"^(wood|glacier|water|bare_rock|scree)$"];
  way["landuse"="forest"]; relation["landuse"="forest"];
);
out geom;'''

# tags that make a closed way an area (as overpass-turbo / osmtogeojson decide)
AREA_KEYS = {'building', 'landuse', 'natural', 'amenity', 'leisure', 'place', 'shop', 'tourism', 'man_made',
             'boundary', 'military', 'aeroway', 'historic', 'office', 'craft', 'ruins', 'area:highway',
             'building:part', 'water', 'glacier', 'wetland'}
LINE_VALUES = {('natural', 'coastline'), ('natural', 'cliff'), ('natural', 'ridge'), ('natural', 'arete'),
               ('natural', 'tree_row'), ('man_made', 'embankment'), ('man_made', 'pipeline'), ('barrier', 'fence')}


def _post(url, query):
    req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': query}).encode(),
                                 headers={'User-Agent': UA, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode('utf-8'))


def overpass(query):
    last = None
    for attempt in range(6):
        for url in OVERPASS:
            try:
                print(f'   asking {url.split("/")[2]} ...', flush=True)
                data = _post(url, query)
                if data.get('remark') and 'error' in data['remark'].lower():
                    raise RuntimeError(data['remark'][:200])
                return data
            except Exception as e:
                last = e
                code = getattr(e, 'code', None)
                print(f'   {url.split("/")[2]}: {e}')
                if code in (429, 504, 503):
                    time.sleep(30)
        time.sleep(30 * (attempt + 1))
    raise RuntimeError(f'Overpass did not answer: {last}')


def _pts(geom):
    return [[round(p['lon'], 7), round(p['lat'], 7)] for p in geom if p]


def _is_area(tags, pts):
    if len(pts) < 4 or pts[0] != pts[-1]:
        return False
    if tags.get('area') == 'no':
        return False
    if tags.get('area') == 'yes':
        return True
    return any(k in AREA_KEYS and (k, v) not in LINE_VALUES for k, v in tags.items())


def _join(chains):
    """join way pieces end to end (reversing where needed) into longer chains"""
    chains = [c[:] for c in chains if len(c) > 1]
    out = []
    while chains:
        cur = chains.pop(0)
        grew = True
        while grew and cur[0] != cur[-1]:
            grew = False
            for i, c in enumerate(chains):
                if c[0] == cur[-1]: cur += c[1:]
                elif c[-1] == cur[-1]: cur += c[::-1][1:]
                elif c[-1] == cur[0]: cur = c + cur[1:]
                elif c[0] == cur[0]: cur = c[::-1] + cur[1:]
                else: continue
                chains.pop(i); grew = True; break
        out.append(cur)
    return out


def _inside(pt, ring):
    x, y, n, ins = pt[0], pt[1], len(ring), False
    for i in range(n - 1):
        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ins = not ins
    return ins


def to_geojson(data):
    feats = []
    for el in data.get('elements', []):
        tags = el.get('tags') or {}
        if not tags:
            continue
        oid = f"{el['type']}/{el['id']}"
        geom = None
        if el['type'] == 'node':
            geom = {'type': 'Point', 'coordinates': [round(el['lon'], 7), round(el['lat'], 7)]}
        elif el['type'] == 'way':
            pts = _pts(el.get('geometry') or [])
            if len(pts) < 2: continue
            geom = {'type': 'Polygon', 'coordinates': [pts]} if _is_area(tags, pts) else {'type': 'LineString', 'coordinates': pts}
        elif el['type'] == 'relation':
            mem = [m for m in el.get('members', []) if m.get('type') == 'way' and m.get('geometry')]
            if tags.get('type') in ('multipolygon', 'boundary'):
                outer = [r for r in _join([_pts(m['geometry']) for m in mem if m.get('role') != 'inner']) if len(r) >= 4 and r[0] == r[-1]]
                inner = [r for r in _join([_pts(m['geometry']) for m in mem if m.get('role') == 'inner']) if len(r) >= 4 and r[0] == r[-1]]
                if not outer: continue
                polys = [[o] for o in outer]
                for r in inner:
                    for p in polys:
                        if _inside(r[0], p[0]): p.append(r); break
                geom = {'type': 'Polygon', 'coordinates': polys[0]} if len(polys) == 1 else {'type': 'MultiPolygon', 'coordinates': polys}
            else:
                lines = _join([_pts(m['geometry']) for m in mem])
                if not lines: continue
                geom = {'type': 'LineString', 'coordinates': lines[0]} if len(lines) == 1 else {'type': 'MultiLineString', 'coordinates': lines}
        if geom:
            feats.append({'type': 'Feature', 'id': oid, 'properties': dict({'@id': oid}, **tags), 'geometry': geom})
    return feats


def fetch_osm(bbox, out_ski, out_land):
    """bbox = (south, west, north, east) in degrees"""
    for query, out in ((Q_SKI, out_ski), (Q_LAND, out_land)):
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            print(f'{out}: already downloaded'); continue
        print(f'OpenStreetMap -> {out}')
        feats, seen = [], set()
        s, w, n, e = bbox
        parts = [(s, w, n, e)]
        while parts:
            b = parts.pop(0)
            try:
                data = overpass(query.format(bbox=f'{b[0]:.4f},{b[1]:.4f},{b[2]:.4f},{b[3]:.4f}'))
            except RuntimeError as err:
                if (b[3] - b[1]) < 0.05:
                    raise
                print('   too much at once - splitting the area in half')
                m = (b[1] + b[3]) / 2
                parts += [(b[0], b[1], b[2], m), (b[0], m, b[2], b[3])]
                continue
            for f in to_geojson(data):
                if f['id'] not in seen:
                    seen.add(f['id']); feats.append(f)
            time.sleep(5)
        json.dump({'type': 'FeatureCollection', 'generator': 'snowmap osm_fetch (Overpass API)',
                   'copyright': 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.',
                   'features': feats}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
        print(f'   {len(feats)} features saved')


# south, west, north, east of the map frame (+ a margin)
fetch_osm((47.20, 9.69, 47.43, 10.09), 'damuels_skiarea.geojson', 'damuels_buildings.geojson')

# ---------- 0. which 1 km squares are near the pistes and lifts ----------
if not os.path.exists('damuels_skiarea.geojson'):
    sys.exit('The OpenStreetMap download did not work - run the script again.')
src_sr = osr.SpatialReference(); src_sr.ImportFromEPSG(4326); src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
utm_sr = osr.SpatialReference(); utm_sr.ImportFromEPSG(32632); utm_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
to_utm = osr.CoordinateTransformation(src_sr, utm_sr)
cells = set()


def mark(x, y):
    for dx in (-500, 0, 500):
        for dy in (-500, 0, 500):
            e, n = int((x + dx) // 1000 * 1000), int((y + dy) // 1000 * 1000)
            if DEM_BOUNDS[0] <= e < DEM_BOUNDS[2] - 1000 and DEM_BOUNDS[1] <= n < DEM_BOUNDS[3] - 1000:
                cells.add((e, n))


def walk(coords):
    pts = [to_utm.TransformPoint(c[0], c[1])[:2] for c in coords]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] or pts):
        k = max(1, int(np.hypot(x1 - x0, y1 - y0) // 150))
        for i in range(k + 1):
            mark(x0 + (x1 - x0) * i / k, y0 + (y1 - y0) * i / k)


def lines(g):
    t, c = g['type'], g['coordinates']
    if t == 'LineString': yield c
    elif t in ('MultiLineString', 'Polygon'): yield from c
    elif t == 'MultiPolygon':
        for p in c: yield from p


for f in json.load(open('damuels_skiarea.geojson', encoding='utf-8'))['features']:
    p = f.get('properties') or {}
    if (p.get('piste:type') or p.get('aerialway')) and f.get('geometry'):
        for c in lines(f['geometry']):
            walk(c)
CELLS = sorted(cells)
print(f'{len(CELLS)} squares of 1 km around the pistes and lifts')

# ---------- 1. the terrain sources (read in place, nothing to unpack) ----------
_l = osr.SpatialReference(); _l.ImportFromEPSG(3035); _l.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
to_laea = osr.CoordinateTransformation(utm_sr, _l)
corners = [to_laea.TransformPoint(x, y)[:2] for x in (DEM_BOUNDS[0], DEM_BOUNDS[2]) for y in (DEM_BOUNDS[1], DEM_BOUNDS[3])]
tiles = sorted({(int(e // 50000 * 50000), int(n // 50000 * 50000))
                for e in np.linspace(min(c[0] for c in corners), max(c[0] for c in corners), 8)
                for n in np.linspace(min(c[1] for c in corners), max(c[1] for c in corners), 8)})
bev = []
for e, n in tiles:
    url = BEV.format(e=e, n=n)
    try:
        gdal.Open(url); bev.append(url); print('BEV terrain tile', f'E{e} N{n}', 'ok')
    except Exception as ex:
        print('BEV terrain tile', f'E{e} N{n}', 'not available:', str(ex)[:120])
bev_vrt = gdal.BuildVRT('/vsimem/bev.vrt', bev) if bev else None
glo_vrt = gdal.BuildVRT('/vsimem/glo.vrt', GLO)


# ---------- 2. read everything onto one grid ----------
def warp(src, bounds, res, alg):
    """src on the output grid as float array, NaN where there is no data"""
    if src is None:
        return None
    ds = gdal.Warp('/vsimem/w.tif', src, dstSRS='EPSG:32632', outputBounds=bounds, xRes=res, yRes=res,
                   resampleAlg=alg, outputType=gdal.GDT_Float32, dstNodata=-32768, multithread=True)
    a = ds.ReadAsArray().astype(np.float64); ds = None
    a[(a < 300) | (a > 5000)] = np.nan          # no data / fill values (nothing here is below 300 m)
    return a


def fill(a, b):
    if b is None:
        return a
    m = np.isnan(a); a[m] = b[m]; return a


if not os.path.exists('damuels_dem5m.tif'):
    print('whole area at 5 m (from the 1 m model) ...')
    dem = warp(bev_vrt, DEM_BOUNDS, 5, 'average')
    if dem is None:
        dem = np.full((round((DEM_BOUNDS[3] - DEM_BOUNDS[1]) / 5), round((DEM_BOUNDS[2] - DEM_BOUNDS[0]) / 5)), np.nan)
    if np.isnan(dem).any():
        print(f'  {np.isnan(dem).mean() * 100:.1f}% outside Austria: filling from Copernicus GLO-30')
        dem = fill(dem, warp(glo_vrt, DEM_BOUNDS, 5, 'cubicspline'))
    print(f'  {np.isnan(dem).mean() * 100:.2f}% still empty')
    dem = np.nan_to_num(dem, nan=0)
    out = gdal.GetDriverByName('GTiff').Create('damuels_dem5m.tif', dem.shape[1], dem.shape[0], 1, gdal.GDT_UInt16,
                                               options=['COMPRESS=DEFLATE', 'PREDICTOR=2', 'ZLEVEL=9'])
    out.SetGeoTransform((DEM_BOUNDS[0], 5, 0, DEM_BOUNDS[3], 0, -5))
    sr = osr.SpatialReference(); sr.ImportFromEPSG(32632); out.SetProjection(sr.ExportToWkt())
    b = out.GetRasterBand(1); b.WriteArray(np.round(dem * 10).astype(np.uint16)); b.SetNoDataValue(0)
    out = None
    print('  done')


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
dem5 = gdal.Open('damuels_dem5m.tif')
B = 8   # metres of border so tile edges shade correctly
skipped = 0
for k, (e, n) in enumerate(CELLS, 1):
    out = f'relief_2m/{e}_{n}.png'
    if os.path.exists(out) or os.path.exists(out + '.skip'):
        continue
    bounds = (e - B, n - B, e + 1000 + B, n + 1000 + B)
    a = warp(bev_vrt, bounds, 2, 'average')
    if a is None or np.isnan(a).mean() > 0.5:          # outside Austria: the 5 m map covers it
        open(out + '.skip', 'w').close(); skipped += 1; continue
    ds = gdal.Warp('/vsimem/c.tif', dem5, outputBounds=bounds, xRes=2, yRes=2, resampleAlg='cubicspline',
                   outputType=gdal.GDT_Float32)
    a = fill(a, ds.ReadAsArray().astype(np.float64) / 10.0); ds = None
    img = shade(a, 2.0)[B // 2:-B // 2, B // 2:-B // 2]
    mem = gdal.GetDriverByName('MEM').Create('', img.shape[1], img.shape[0], 1, gdal.GDT_Byte)
    mem.GetRasterBand(1).WriteArray(img)
    gdal.GetDriverByName('PNG').CreateCopy(out, mem, options=['ZLEVEL=9']); mem = None
    print(f'  2 m tile {k}/{len(CELLS)}: {out}')
print(f'  ({skipped} squares outside Austria left to the 5 m map)')
json.dump(CELLS, open('damuels_fine_cells.json', 'w'))

# ---------- 4. pack for upload in parts of at most ~25 MB ----------
files = ['damuels_skiarea.geojson', 'damuels_buildings.geojson', 'damuels_dem5m.tif', 'damuels_fine_cells.json'] + \
    sorted('relief_2m/' + f for f in os.listdir('relief_2m') if f.endswith('.png'))
part, size, z = 1, 0, None
for f in files:
    s = os.path.getsize(f) * (0.15 if f.endswith('.geojson') else 1)     # map data packs down a lot
    if z is None or size + s > 25e6:
        if z: z.close()
        z = zipfile.ZipFile(f'damuels_landscape_part{part}.zip', 'w', zipfile.ZIP_DEFLATED); part += 1; size = 0
    z.write(f); size += s
z.close()
print('\nDone. Upload these files:', ', '.join(f'damuels_landscape_part{i}.zip' for i in range(1, part)))
