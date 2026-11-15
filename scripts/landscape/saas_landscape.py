"""
Saastal: pistes and lifts from OpenStreetMap (the same source as the other
resorts), plus the buildings at the lift stations. Only the ski data - the
Saas terrain and the rest of the map stay as they are.

Makes saas_skiarea.geojson (pistes, lifts) and saas_buildings.geojson
(buildings near the lifts), packed as saas_landscape_part1.zip.
"""
import json, os, sys, zipfile
sys.path.insert(0, os.getcwd())   # osm_fetch.py is copied next to the work folder
import osm_fetch as O

BBOX = '45.9400,7.6800,46.3550,8.0750'     # south, west, north, east (the Saas map frame + a margin)
Q = '''[out:json][timeout:600][bbox:{bbox}];
( way["piste:type"]; relation["piste:type"]; way["aerialway"]; node["aerialway"]; )->.ski;
.ski out geom;
way["aerialway"]->.lifts;
way["building"](around.lifts:60);
out geom;'''
data = O.overpass(Q.format(bbox=BBOX))
feats = O.to_geojson(data)
ski = [f for f in feats if not f['properties'].get('building')]
bld = [f for f in feats if f['properties'].get('building')]
for name, fs in (('saas_skiarea.geojson', ski), ('saas_buildings.geojson', bld)):
    json.dump({'type': 'FeatureCollection', 'generator': 'snowmap osm_fetch (Overpass API)',
               'copyright': 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.',
               'features': fs}, open(name, 'w', encoding='utf-8'), ensure_ascii=False)
    print(name, len(fs), 'features')
with zipfile.ZipFile('saas_landscape_part1.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write('saas_skiarea.geojson'); z.write('saas_buildings.geojson')
print('Done.')
