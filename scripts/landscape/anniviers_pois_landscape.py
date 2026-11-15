"""
Val d'Anniviers: points of interest for the snow map, from OpenStreetMap
(restaurants, huts, rescue, parking, information, ski schools, ice rinks,
playgrounds, sledging / cross-country / ski-touring / snowshoe routes, snow parks).

Makes anniviers_pois_landscape_part1.zip with anniviers_pois.geojson.
Runs on GitHub: Actions > Landscape > anniviers_pois.
"""
import json, os, sys, zipfile
sys.path.insert(0, os.getcwd())
from osm_fetch import overpass, to_geojson

BBOX = (46.05, 7.355, 46.351, 7.74)     # south, west, north, east (whole Val d'Anniviers map)
Q = '''[out:json][timeout:600][bbox:{bbox}];
(
  nwr["amenity"~"^(restaurant|cafe|bar|fast_food|pub|biergarten|ice_cream|shelter|parking|ski_school|first_aid|toilets)$"];
  nwr["tourism"~"^(alpine_hut|wilderness_hut|information|viewpoint|picnic_site|hotel|guest_house|chalet)$"];
  nwr["emergency"]; nwr["healthcare"];
  nwr["leisure"~"^(ice_rink|playground|sports_centre|pitch)$"];
  nwr["sport"~"^(skiing|ice_skating|paragliding|free_flying|sledding)$"];
  nwr["piste:type"~"^(sled|nordic|skitour|hike|snow_park|playground|fatbike)$"];
  nwr["aerialway"="station"];
  nwr["shop"~"^(ski|sports)$"];
);
out geom;'''

feats, seen = [], set()
data = overpass(Q.format(bbox='{:.4f},{:.4f},{:.4f},{:.4f}'.format(*BBOX)))
for f in to_geojson(data):
    if f['id'] not in seen:
        seen.add(f['id']); feats.append(f)
out = 'anniviers_pois.geojson'
json.dump({'type': 'FeatureCollection', 'generator': 'snowmap osm_fetch (Overpass API)',
           'copyright': 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.',
           'features': feats}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'{len(feats)} features saved')
with zipfile.ZipFile('anniviers_pois_landscape_part1.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(out)
