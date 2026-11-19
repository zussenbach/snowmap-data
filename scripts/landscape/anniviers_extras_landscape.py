"""
Val d'Anniviers Landscape extras (3D view made precise), all free and open data:

  * extras.json  - from OpenStreetMap (ODbL): ski-tour routes, lit pistes, viewpoints,
                   churches, hotels, car parks, bus stops and bus lines, lift details;
                   weather stations: SLF IMIS (snow stations) and MeteoSwiss (if reachable)
  * photo/E_N.jpg - swisstopo SWISSIMAGE aerial photo of each 1 km square around the pistes,
                   1 m a pixel (1024 x 1024), UTM 32N, north up ("(c) swisstopo")
  * photo_base.jpg - the whole map frame, 6144 px wide

Makes anniviers_extras_landscape_part*.zip. Runs on GitHub: Actions > Landscape > anniviers_extras.
"""
import json, os, time, urllib.parse, urllib.request, zipfile
from osgeo import gdal
gdal.UseExceptions()
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif')
gdal.SetConfigOption('GDAL_HTTP_MAX_RETRY', '5'); gdal.SetConfigOption('GDAL_HTTP_TIMEOUT', '120')

UA = {'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'}
BBOX = '46.05,7.355,46.351,7.74'        # south, west, north, east
X0, X1, Y0, Y1 = 374500.0, 401500.0, 5103000.0, 5132750.0
SERVERS = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter',
           'https://maps.mail.ru/osm/tools/overpass/api/interpreter']


def overpass(q):
    for attempt in range(4):
        for url in SERVERS:
            try:
                print('asking', url.split('/')[2], flush=True)
                req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(), headers=UA)
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read().decode('utf-8'))
            except Exception as e:
                print('  ', e, flush=True)
        time.sleep(30)
    raise SystemExit('Overpass did not answer')


def get_json(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120))
    except Exception as e:
        print('  not reachable:', url, e, flush=True); return None


# ---- OpenStreetMap
Q = f'''[out:json][timeout:300][bbox:{BBOX}];
(
  way["piste:type"="skitour"]; relation["piste:type"="skitour"];
  way["piste:type"="downhill"]["lit"="yes"];
  way["route"="ski"]; relation["route"="ski"];
  node["tourism"="viewpoint"];
  nw["amenity"="place_of_worship"];
  nw["tourism"~"^(hotel|guest_house|chalet|apartment|hostel)$"];
  nw["amenity"="parking"];
  node["highway"="bus_stop"]; node["public_transport"="platform"]["bus"="yes"];
  relation["route"="bus"];
  way["aerialway"];
  node["man_made"~"^(tower|mast)$"]["tower:type"="communication"];
);
out geom tags;'''
d = overpass(Q)
feats = []
for e in d['elements']:
    t = e.get('tags', {}); f = {'id': f"{e['type']}/{e['id']}", 'tags': t}
    if e['type'] == 'node':
        f['pt'] = [e['lon'], e['lat']]
    elif e['type'] == 'way' and 'geometry' in e:
        f['line'] = [[g['lon'], g['lat']] for g in e['geometry']]
    elif e['type'] == 'relation':
        f['lines'] = [[[g['lon'], g['lat']] for g in m['geometry']] for m in e.get('members', []) if m.get('type') == 'way' and 'geometry' in m]
        if 'bounds' in e: f['pt'] = [(e['bounds']['minlon'] + e['bounds']['maxlon']) / 2, (e['bounds']['minlat'] + e['bounds']['maxlat']) / 2]
    if 'center' in e: f['pt'] = [e['center']['lon'], e['center']['lat']]
    feats.append(f)
print(len(feats), 'OpenStreetMap features', flush=True)

# ---- weather stations (positions and names only)
stations = []
s = get_json('https://measurement-api.slf.ch/public/api/imis/stations')
for st in (s or []):
    try:
        stations.append({'net': 'SLF IMIS', 'code': st.get('code'), 'name': st.get('label') or st.get('name'),
                         'lon': float(st['lon']), 'lat': float(st['lat']), 'elev': st.get('elevation')})
    except Exception:
        pass
m = get_json('https://data.geo.admin.ch/ch.meteoschweiz.messnetz-automatisch/ch.meteoschweiz.messnetz-automatisch_en.json')
for f in ((m or {}).get('features') or []):
    p = f.get('properties', {}); g = f.get('geometry', {})
    try:
        lon, lat = g['coordinates'][:2]
        if abs(lon) > 180:   # LV95 / LV03
            from osgeo import osr
            a = osr.SpatialReference(); a.ImportFromEPSG(2056 if lon > 2e6 else 21781)
            b = osr.SpatialReference(); b.ImportFromEPSG(4326); b.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            lon, lat = osr.CoordinateTransformation(a, b).TransformPoint(lon, lat)[:2]
        stations.append({'net': 'MeteoSwiss', 'code': f.get('id'), 'name': p.get('station_name') or p.get('name'),
                         'lon': lon, 'lat': lat, 'elev': p.get('altitude')})
    except Exception:
        pass
stations = [s for s in stations if 7.355 <= s['lon'] <= 7.74 and 46.05 <= s['lat'] <= 46.351]
print(len(stations), 'weather stations in the frame', flush=True)
json.dump({'copyright': 'OpenStreetMap contributors (ODbL); stations: SLF, MeteoSwiss', 'osm': feats, 'stations': stations},
          open('extras.json', 'w', encoding='utf-8'), ensure_ascii=False)

# ---- SWISSIMAGE (aerial photo), read straight from swisstopo's cloud tiles at 1 m
STAC = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissimage-dop10/items'
items, url = {}, STAC + '?bbox=7.33,46.05,7.76,46.37&limit=100'
while url:
    dd = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300))
    for it in dd['features']:
        for a in it['assets'].values():
            h = a['href']
            if h.endswith('.tif') and '_0.1_2056' in h:
                yr, en = h.rsplit('/', 1)[-1].split('_')[1:3]
                k = tuple(map(int, en.split('-')))
                if k not in items or items[k][0] < yr: items[k] = (yr, h)
    url = next((l['href'] for l in dd.get('links', []) if l.get('rel') == 'next'), None)
print(len(items), 'SWISSIMAGE tiles', flush=True)
vrt = gdal.BuildVRT('swissimage.vrt', ['/vsicurl/' + h for _, h in items.values()])
vrt = None
cells = json.load(open('../scripts/landscape/vercorin_fine_cells.json')) if os.path.exists('../scripts/landscape/vercorin_fine_cells.json') else []
os.makedirs('photo', exist_ok=True)
for i, (e, n) in enumerate(cells, 1):
    out = f'photo/{e}_{n}.jpg'
    if os.path.exists(out): continue
    for k in range(3):
        try:
            gdal.Warp('cell.tif', 'swissimage.vrt', dstSRS='EPSG:32632', outputBounds=(e, n, e + 1000, n + 1000),
                      width=1024, height=1024, resampleAlg='average', multithread=True)
            gdal.Translate(out, 'cell.tif', format='JPEG', bandList=[1, 2, 3], creationOptions=['QUALITY=88'])
            break
        except Exception as ex:
            print('  retry', ex, flush=True)
    print(f'{i}/{len(cells)} {out}', flush=True)
gdal.Warp('base.tif', 'swissimage.vrt', dstSRS='EPSG:32632', outputBounds=(X0, Y0, X1, Y1),
          width=6144, height=int(round(6144 * (Y1 - Y0) / (X1 - X0))), resampleAlg='average', multithread=True)
gdal.Translate('photo_base.jpg', 'base.tif', format='JPEG', bandList=[1, 2, 3], creationOptions=['QUALITY=85'])

files = ['extras.json', 'photo_base.jpg'] + sorted('photo/' + f for f in os.listdir('photo') if f.endswith('.jpg'))
part, size, z = 1, 0, None
for f in files:
    s_ = os.path.getsize(f)
    if z is None or size + s_ > 90e6:
        if z: z.close()
        z = zipfile.ZipFile(f'anniviers_extras_landscape_part{part}.zip', 'w', zipfile.ZIP_STORED); part += 1; size = 0
    z.write(f); size += s_
z.close()
print('done', part - 1, 'parts')
