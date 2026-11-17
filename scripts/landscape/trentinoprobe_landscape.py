"""Look for more Trentino LiDAR folders (one-off check)."""
import urllib.request, zipfile, re
out = []
base = 'https://siatservices.provincia.tn.it/stemdata/'
names = ['2014_lidar_dtm_asc', '2006_lidar_dtm_asc', '2008_lidar_dtm_asc', '2006_2008_lidar_dtm_asc', '2007_lidar_dtm_asc',
         '2011_lidar_dtm_asc', '2018_lidar_dtm_asc', '2019_lidar_dtm_asc', '2020_lidar_dtm_asc', '2021_lidar_dtm_asc',
         '2022_lidar_dtm_asc', '2023_lidar_dtm_asc', 'lidar_dtm_asc', '2014_lidar_dtm', '2006_lidar_dtm', 'dtm', 'lidar']
for n in names:
    url = base + n + '/'
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'snowmap-terrain/1.0'}), timeout=60)
        t = r.read().decode('utf-8', 'replace'); l = re.findall(r'href="([^"]+)"', t)
        out.append(f'{n}: {r.status}, {len(l)} links, e.g. {l[1:4]}')
    except Exception as e:
        out.append(f'{n}: {e}')
open('probe.txt', 'w').write('\n'.join(out)); print('\n'.join(out))
with zipfile.ZipFile('trentinoprobe_landscape_part1.zip', 'w') as z: z.write('probe.txt')
