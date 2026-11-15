"""
Val d'Anniviers: restaurants, huts, ski schools and ice rinks for the snow map,
from OpenStreetMap (points only, a small and quick request).

Makes anniviers_pois_landscape_part1.zip with anniviers_pois.json.
Runs on GitHub: Actions > Landscape > anniviers_pois.
"""
import json, time, urllib.parse, urllib.request, zipfile

SERVERS = ['https://overpass-api.de/api/interpreter',
           'https://overpass.private.coffee/api/interpreter',
           'https://maps.mail.ru/osm/tools/overpass/api/interpreter']
BBOX = '46.05,7.355,46.351,7.74'      # south, west, north, east (whole Val d'Anniviers map)
Q = f'''[out:json][timeout:120][bbox:{BBOX}];
(
  nw["amenity"~"^(restaurant|cafe|bar|fast_food|pub|biergarten|ski_school)$"];
  nw["tourism"~"^(alpine_hut|wilderness_hut)$"];
  nw["leisure"="ice_rink"];
);
out center tags;'''

data = None
for attempt in range(4):
    for url in SERVERS:
        try:
            print('asking', url.split('/')[2], flush=True)
            req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': Q}).encode(),
                                         headers={'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode('utf-8'))
            break
        except Exception as e:
            print('  ', e, flush=True)
    if data:
        break
    time.sleep(20)
if not data:
    raise SystemExit('Overpass did not answer')

out = []
for e in data['elements']:
    c = e.get('center') or e
    if 'lat' in c:
        out.append({'id': f"{e['type']}/{e['id']}", 'lat': c['lat'], 'lon': c['lon'], 'tags': e.get('tags', {})})
json.dump({'copyright': 'OpenStreetMap contributors, ODbL', 'features': out},
          open('anniviers_pois.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(len(out), 'points saved')
with zipfile.ZipFile('anniviers_pois_landscape_part1.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write('anniviers_pois.json')
