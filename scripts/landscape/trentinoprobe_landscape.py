"""Look at how the Trentino LiDAR terrain server is organised (one-off check)."""
import urllib.request, zipfile, re
out = []
for url in ['https://siatservices.provincia.tn.it/stemdata/',
            'https://siatservices.provincia.tn.it/stemdata/2014_lidar_dtm_asc/']:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'snowmap-terrain/1.0'}), timeout=120)
        t = r.read().decode('utf-8', 'replace')
        names = re.findall(r'href="([^"]+)"', t)
        out.append(f'== {url} {r.status} {len(t)} bytes, {len(names)} links\n' + '\n'.join(names[:60]) + '\n...\n' + '\n'.join(names[-20:]))
    except Exception as e:
        out.append(f'== {url} ERROR {e}')
open('probe.txt', 'w').write('\n'.join(out))
print('\n'.join(out)[:4000])
with zipfile.ZipFile('trentinoprobe_landscape_part1.zip', 'w') as z: z.write('probe.txt')
