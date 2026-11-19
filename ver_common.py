"""Map frame for Val d'Anniviers (Vercorin, Saint-Luc/Chandolin, Grimentz, Zinal), Valais (UTM 32N, EPSG:32632),
same map scale as the other resorts. Vercorin and the Crêt du Midi in the
middle, Sierre and the Rhône valley at the top, Grimentz near the bottom."""
from pyproj import Transformer
X0, X1 = 374500.0, 401500.0
Y0, Y1 = 5103000.0, 5132750.0
S = 0.018339746807451394
W = round((X1 - X0) * S)
H = round((Y1 - Y0) * S)
to_utm = Transformer.from_crs(4326, 32632, always_xy=True)
def mx(x): return (x - X0) * S
def my(y): return (Y1 - y) * S
def ll_to_map(lon, lat):
    x, y = to_utm.transform(lon, lat)
    return mx(x), my(y)
