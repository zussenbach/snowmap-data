# ---------------------------------------------------------------------------
# OpenStreetMap download (replaces the manual overpass-turbo.eu step)
#
# Sends the two Overpass requests straight to the public Overpass API, turns
# the answer into the same GeoJSON that overpass-turbo's "Export > GeoJSON"
# gives (tags as properties, "@id", closed areas as polygons, multipolygon
# relations assembled, route relations as lines), and saves the files.
# If a server is busy it waits and tries the next one; if an area is too big
# for one request it splits it in halves.
# ---------------------------------------------------------------------------
import json, os, time, urllib.parse, urllib.request

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
