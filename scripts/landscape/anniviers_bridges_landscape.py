"""
Val d'Anniviers Landscape: bridges, tunnels and fences (OpenStreetMap, ODbL).
Roads, tracks and railways on bridges or in tunnels (with their layer), and fences,
guard rails and walls as lines. Makes anniviers_bridges_landscape_part1.zip with bridges.json.
Runs on GitHub: Actions > Landscape > anniviers_bridges.
"""
import json, time, urllib.parse, urllib.request, zipfile
UA = {'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'}
BBOX = '46.05,7.355,46.351,7.74'
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

Q = f'''[out:json][timeout:300][bbox:{BBOX}];
(
  way["highway"]["bridge"]["bridge"!="no"];
  way["railway"]["bridge"]["bridge"!="no"];
  way["highway"]["tunnel"]["tunnel"!="no"];
  way["railway"]["tunnel"]["tunnel"!="no"];
  way["man_made"="bridge"];
  way["barrier"~"^(fence|guard_rail|wall|retaining_wall|city_wall|hedge|handrail)$"];
);
out geom tags;'''
d = overpass(Q)
feats = []
for e in d['elements']:
    g = e.get('geometry')
    if g and len(g) >= 2:
        feats.append({'id': f"way/{e['id']}", 'line': [[round(p['lon'], 7), round(p['lat'], 7)] for p in g], 'tags': e.get('tags', {})})
print(len(feats), 'features', flush=True)
json.dump({'copyright': 'OpenStreetMap contributors (ODbL)', 'osm': feats}, open('bridges.json', 'w', encoding='utf-8'), ensure_ascii=False)
with zipfile.ZipFile('anniviers_bridges_landscape_part1.zip', 'w', zipfile.ZIP_DEFLATED) as z: z.write('bridges.json')
