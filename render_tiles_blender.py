"""Blender: the light (shading) of the interactive Val d'Anniviers landscape,
rendered straight down (orthographic).

    python render_tiles_blender.py cells K NJ [SIZE] [SAMPLES]   # 1 km squares K, K+NJ, ... (around pistes/lifts)
    python render_tiles_blender.py base [SIZE] [SAMPLES]         # the whole map frame, one picture

LIGHT ONLY: plain ground lit by a north-west sun with real cast shadows and the
sky's soft light (valleys and gullies darker). The 3D landscape multiplies it into
the map style's own colours, so every layer and theme stays switchable; nothing
(pistes, lifts, trees, roads) is painted in.

Terrain: swisstopo swissALTI3D at 2 m (downloaded here, 1 km tiles through
swisstopo's STAC API) for each square and 100 m around it, used as it is (no
resampling artefacts); a 10 m surround out to 5 km (from vercorin_dem5m.tif)
casts the long shadows of distant ridges, so neighbouring squares match.

Inputs in the current folder: vercorin_dem5m.tif, vercorin_fine_cells.json, ver_common.py.
Real trees (TREES=1): trees/E_N.npy, tree tops from swissSURFACE3D (see find_trees).
Out: tiles/E_N.jpg (1 km, SIZE px), heights/E_N.png (the 2 m heights of the
square, 501 x 501, decimetres in red*256+green, for the 3D landscape), or base.jpg.
"""
import json, math, os, sys, urllib.request
import numpy as np, tifffile
from PIL import Image
import bpy

sys.path.insert(0, os.getcwd())
import ver_common as J

MODE = sys.argv[1]
if MODE == 'cells':
    K, NJ = int(sys.argv[2]), int(sys.argv[3])
    SIZE = int(sys.argv[4]) if len(sys.argv) > 4 else 1024
    SAMPLES = int(sys.argv[5]) if len(sys.argv) > 5 else 32
else:
    SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 6144
    SAMPLES = int(sys.argv[3]) if len(sys.argv) > 3 else 32

t = tifffile.TiffFile('vercorin_dem5m.tif'); p = t.pages[0]
DEM = p.asarray().astype(np.float32) / 10.0
OX, OY = p.tags['ModelTiepointTag'].value[3:5]; PX = p.tags['ModelPixelScaleTag'].value[0]


def H5(e, n):
    """height (m) at UTM e, n, bilinear on the 5 m model"""
    c = (np.asarray(e, dtype=np.float64) - OX) / PX - 0.5; r = (OY - np.asarray(n, dtype=np.float64)) / PX - 0.5
    c0 = np.clip(np.floor(c).astype(int), 0, DEM.shape[1] - 2); r0 = np.clip(np.floor(r).astype(int), 0, DEM.shape[0] - 2)
    fc = np.clip(c - c0, 0, 1); fr = np.clip(r - r0, 0, 1)
    return (DEM[r0, c0] * (1 - fc) * (1 - fr) + DEM[r0, c0 + 1] * fc * (1 - fr) +
            DEM[r0 + 1, c0] * (1 - fc) * fr + DEM[r0 + 1, c0 + 1] * fc * fr)


# ---- swissALTI3D 2 m (only for the 1 km squares)
STAC = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissalti3d/items'
UA = {'User-Agent': 'snowmap-resort-builder/1.0 (personal snow map project)'}
_idx = {}
SURF = 'https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swisssurface3d-raster/items'


def alti_index(stac=STAC, tag='_2_2056_'):
    """LV95 km (E, N) -> url of the newest tile, for the map frame"""
    if tag not in _idx:
        _items = _idx[tag] = {}
        url = stac + '?bbox=7.33,46.05,7.76,46.37&limit=100'
        while url:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300))
            for it in d['features']:
                for a in it['assets'].values():
                    h = a['href']
                    if h.endswith('.tif') and tag in h:
                        # swissalti3d_2019_2588-1117_2_2056_5728.tif
                        yr, en = h.rsplit('/', 1)[-1].split('_')[1:3]
                        ek, nk = map(int, en.split('-'))
                        old = _items.get((ek, nk))
                        if not old or old[0] < yr:
                            _items[(ek, nk)] = (yr, h)
            url = next((l['href'] for l in d.get('links', []) if l.get('rel') == 'next'), None)
        print(len(_items), tag, 'tiles', flush=True)
    return _idx[tag]


def dem2(e0, n0, e1, n1, step=2, stac=STAC, tag='_2_2056_', how='cubic'):
    """swisstopo heights on the UTM grid e0..e1, n1..n0 (rows north to south), step metres; NaN where not covered"""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    from pyproj import Transformer
    tr = Transformer.from_crs(32632, 2056, always_xy=True)
    xs, ys = tr.transform([e0, e1, e0, e1], [n0, n0, n1, n1])
    idx = alti_index(stac, tag); os.makedirs('alti', exist_ok=True)
    srcs = []
    for ek in range(int(min(xs) // 1000) - 1, int(max(xs) // 1000) + 1):
        for nk in range(int(min(ys) // 1000) - 1, int(max(ys) // 1000) + 1):
            if (ek, nk) not in idx: continue
            fn = f'alti/{tag.strip("_")}_{ek}_{nk}.tif'
            if not os.path.exists(fn):
                for k in range(4):
                    try: urllib.request.urlretrieve(idx[(ek, nk)][1], fn + '.part'); os.replace(fn + '.part', fn); break
                    except Exception as ex: print('  retry', ex, flush=True)
            if os.path.exists(fn): srcs.append(fn)
    nx = int(round((e1 - e0) / step)) + 1; ny = int(round((n1 - n0) / step)) + 1
    out = np.full((ny, nx), np.nan, np.float32)
    # pixel centres on the grid points: origin half a pixel up-left of (e0, n1)
    dt = from_origin(e0 - step / 2, n1 + step / 2, step, step)
    for fn in srcs:
        with rasterio.open(fn) as s:
            a = np.full((ny, nx), np.nan, np.float32)
            reproject(rasterio.band(s, 1), a, dst_transform=dt, dst_crs='EPSG:32632', dst_nodata=np.nan,
                      resampling=getattr(Resampling, how))
            m = np.isfinite(a) & (a > -100); out[m] = a[m]
    return out


def safe_dem(*a, **k):
    try:
        return dem2(*a, **k)
    except Exception as ex:          # no network to swisstopo (e.g. a local test): 5 m only
        print('  swisstopo not reachable:', ex, flush=True); return None


def find_trees(e, n):
    """real trees of the square: tops of the canopy (swissSURFACE3D 0.5 m surface,
    taken at 1 m, minus swissALTI3D ground): local highest points at least 3 m
    tall that stand out from their surroundings (flat roofs do not). Saved as
    trees/E_N.npy uint16 (x dm, y dm from the square's south-west corner, height dm)."""
    from scipy.ndimage import gaussian_filter, maximum_filter, minimum_filter
    dsm = safe_dem(e, n, e + 1000, n + 1000, 1, SURF, '_0.5_2056_', 'max')
    dtm = safe_dem(e, n, e + 1000, n + 1000, 1, STAC, '_2_2056_', 'bilinear')
    if dsm is None or dtm is None: return 0
    chm = np.nan_to_num(dsm - dtm, nan=0).clip(0, 60)
    cs = gaussian_filter(chm, 0.9)
    peak = (cs == maximum_filter(cs, 5)) & (cs > 3) & (cs - minimum_filter(cs, 7) > 1.8)
    r, c = np.nonzero(peak)
    hgt = chm[r, c]
    a = np.stack([c * 10, (1000 - r) * 10, np.round(hgt * 10)], 1).clip(0, 65535).astype('<u2')
    os.makedirs('trees', exist_ok=True); np.save(f'trees/{e}_{n}.npy', a)
    return len(a)


def mesh(name, E, N, Z, cx, cy, skip=None):
    ny, nx = Z.shape
    verts = np.stack([E - cx, N - cy, Z], -1).reshape(-1, 3).astype(np.float32)
    ii = np.arange((ny - 1) * (nx - 1)); r, c = ii // (nx - 1), ii % (nx - 1)
    if skip is not None:
        ii = ii[~skip[r, c]]; r, c = ii // (nx - 1), ii % (nx - 1)
    a = r * nx + c
    faces = np.stack([a, a + nx, a + nx + 1, a + 1], -1).astype(np.int32)
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts)); me.vertices.foreach_set('co', verts.ravel())
    me.loops.add(faces.size); me.loops.foreach_set('vertex_index', faces.ravel())
    me.polygons.add(len(faces)); me.polygons.foreach_set('loop_start', np.arange(0, faces.size, 4, dtype=np.int32))
    me.polygons.foreach_set('loop_total', np.full(len(faces), 4, dtype=np.int32))
    me.update(); me.polygons.foreach_set('use_smooth', np.ones(len(faces), dtype=bool))
    me.materials.append(GROUND)
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    return ob


def scene():
    global GROUND
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    GROUND = bpy.data.materials.new('ground'); GROUND.use_nodes = True
    b = GROUND.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (0.55, 0.55, 0.55, 1); b.inputs['Roughness'].default_value = 0.95
    # light: north-west sun (as the map's shading) with real shadows, soft sky light
    w = bpy.data.worlds.new('sky'); sc.world = w
    if not w.node_tree: w.use_nodes = True
    bg = w.node_tree.nodes['Background']; bg.inputs['Color'].default_value = (1, 1, 1, 1); bg.inputs['Strength'].default_value = 0.30
    sun = bpy.data.lights.new('sun', 'SUN'); sun.energy = 1.7; sun.angle = math.radians(1.0)
    so = bpy.data.objects.new('sun', sun); sc.collection.objects.link(so)
    so.rotation_euler = (math.radians(56), 0, math.radians(135))
    sc.render.engine = 'CYCLES'; sc.cycles.device = 'CPU'; sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.view_settings.view_transform = 'Standard'; sc.view_settings.look = 'None'; sc.view_settings.exposure = 0.0
    sc.render.image_settings.file_format = 'PNG'; sc.render.image_settings.color_depth = '16'
    sc.render.image_settings.color_mode = 'BW'
    return sc


def ortho(sc, w, h, px_w, px_h):
    cam = bpy.data.cameras.new('cam'); cam.type = 'ORTHO'; cam.ortho_scale = max(w, h); cam.clip_end = 30000
    co = bpy.data.objects.new('cam', cam); sc.collection.objects.link(co); sc.camera = co
    co.location = (0, 0, 9000); co.rotation_euler = (0, 0, 0)
    sc.render.resolution_x = px_w; sc.render.resolution_y = px_h; sc.render.resolution_percentage = 100


def finish(png, out):
    """fixed levels for every picture (so squares match): flat sunny ground near
    white, shade and shadow darker, a gentle shoulder keeps sunlit detail"""
    a = np.asarray(Image.open(png)).astype(np.float32) / 65535
    o = np.clip(a / 0.72, 0, 1) ** 1.4
    Image.fromarray((o * 255 + 0.5).astype(np.uint8)).save(out, quality=92, optimize=True)
    os.remove(png)


if MODE == 'cells':
    allc = sorted(tuple(c) for c in json.load(open('vercorin_fine_cells.json')))
    cells = allc[K * len(allc) // NJ:(K + 1) * len(allc) // NJ]      # neighbours together: shared downloads
    os.makedirs('tiles', exist_ok=True); os.makedirs('heights', exist_ok=True)
    M2, MS = 100, 5000        # 2 m margin, 10 m shadow surround
    for i, (e, n) in enumerate(cells, 1):
        out = f'tiles/{e}_{n}.jpg'
        if os.path.exists(out): continue
        cx, cy = e + 500, n + 500
        sc = scene()
        # fine: 2 m (5 m where swissALTI3D has no data)
        es = np.arange(e - M2, e + 1000 + M2 + 1, 2.0); ns = np.arange(n + 1000 + M2, n - M2 - 1, -2.0)
        E, N = np.meshgrid(es, ns)
        Z2 = safe_dem(e - M2, n - M2, e + 1000 + M2, n + 1000 + M2)
        if Z2 is None: Z2 = np.full(E.shape, np.nan, np.float32)
        bad = ~np.isfinite(Z2)
        if bad.any(): Z2[bad] = H5(E[bad], N[bad])
        print(f'  {e}_{n}: 2 m coverage {100 * (1 - bad.mean()):.1f} %', flush=True)
        mesh('fine', E, N, Z2, cx, cy)
        # heights of the square itself for the 3D landscape (2 m, decimetres)
        k0 = M2 // 2; q = np.round(Z2[k0:k0 + 501, k0:k0 + 501] * 10).clip(0, 65535).astype(np.uint32)
        Image.fromarray(np.stack([q >> 8, q & 255, np.zeros_like(q)], -1).astype(np.uint8)).save(f'heights/{e}_{n}.png', optimize=True)
        # surround: 10 m, with a hole under the fine part
        es = np.arange(e - MS, e + 1000 + MS + 1, 10.0); ns = np.arange(n + 1000 + MS, n - MS - 1, -10.0)
        E, N = np.meshgrid(es, ns); Z = H5(E, N)
        inner = (E[:-1, :-1] >= e - M2 + 20) & (E[1:, 1:] <= e + 1000 + M2 - 20) & (N[1:, 1:] >= n - M2 + 20) & (N[:-1, :-1] <= n + 1000 + M2 - 20)
        mesh('surround', E, N, Z, cx, cy, skip=inner)
        ortho(sc, 1000, 1000, SIZE, SIZE)
        sc.render.filepath = os.path.abspath(f'tiles/{e}_{n}.png')
        bpy.ops.render.render(write_still=True)
        finish(f'tiles/{e}_{n}.png', out)
        nt = find_trees(e, n) if os.environ.get('TREES') else 0
        print(f'{i}/{len(cells)} {out}  trees {nt}', flush=True)
else:
    cx, cy = (J.X0 + J.X1) / 2, (J.Y0 + J.Y1) / 2
    w, h = J.X1 - J.X0, J.Y1 - J.Y0
    sc = scene()
    es = np.arange(J.X0 - 5000, J.X1 + 5001, 15.0); ns = np.arange(J.Y1 + 5000, J.Y0 - 5001, -15.0)
    E, N = np.meshgrid(es, ns)
    mesh('terrain', E, N, H5(E, N), cx, cy)
    ortho(sc, w, h, SIZE, int(round(SIZE * h / w)))
    sc.render.filepath = os.path.abspath('base.png')
    bpy.ops.render.render(write_still=True)
    finish('base.png', 'base.jpg')
    print('base.jpg', flush=True)
