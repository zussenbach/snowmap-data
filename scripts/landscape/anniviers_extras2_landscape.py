"""
Val d'Anniviers Landscape extras, part 2 (OpenStreetMap, ODbL): mountain huts and
restaurants (with opening hours where mapped), paragliding take-offs and landings,
summit crosses / peaks with names. Makes anniviers_extras2_landscape_part1.zip with extras2.json.
Runs on GitHub: Actions > Landscape > anniviers_extras2.
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
  nw["tourism"~"^(alpine_hut|wilderness_hut)$"];
  nw["amenity"~"^(restaurant|cafe|bar|fast_food)$"];
  nw["free_flying:site"]; nw["sport"="free_flying"]; nw["free_flying:takeoff"="yes"];
  node["natural"="peak"]["name"];
);
out center tags;'''
d = overpass(Q)
feats = []
for e in d['elements']:
    c = e.get('center') or e
    if 'lat' in c: feats.append({'id': f"{e['type']}/{e['id']}", 'pt': [c['lon'], c['lat']], 'tags': e.get('tags', {})})
print(len(feats), 'features', flush=True)
json.dump({'copyright': 'OpenStreetMap contributors (ODbL)', 'osm': feats}, open('extras2.json', 'w', encoding='utf-8'), ensure_ascii=False)
with zipfile.ZipFile('anniviers_extras2_landscape_part1.zip', 'w', zipfile.ZIP_DEFLATED) as z: z.write('extras2.json')
