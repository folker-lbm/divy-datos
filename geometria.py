"""
¿Puede el modelo ver la corriente en este spot? Medidas de la costa alrededor de cada spot (T124, fase 2; 7 nov 2026).

Los modelos de corriente de Copernicus fuera de Europa tienen celdas de 1/12° (unos 9 km). Un paso entre dos islas más
estrecho que una celda no existe para el modelo: ahí la corriente del modelo no vale, aunque diga «en calma». Esto no se
interpreta a mano: se mide con la elevación del terreno y del fondo (AWS Terrain Tiles, formato Terrarium, las mismas
teselas que el relieve del mapa de la app; tierra de SRTM y fondo de ETOPO1 y GEBCO).

Por spot:
  · `orillaKm` y `orillaDir`: distancia y dirección a la orilla más cercana (36 direcciones, hasta RADIO_KM);
  · `anchuraKm`: anchura del agua frente al spot, de esa orilla a la de enfrente (2 × RADIO_KM si enfrente es mar
    abierto);
  · `abiertoPct`: qué parte de las direcciones llega a RADIO_KM sin tocar orilla (mar abierto);
  · `tierraPct`: qué parte de un cuadrado de 9 km (una celda del modelo) centrado en el spot es tierra.

Se usa una vez por spot, en local, para generar datos/corriente-spots.json (no en GitHub Actions). Necesita pillow, que
no está en requirements.txt por eso.
"""
import io
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import requests

URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
Z = 12  # unos 38 m por píxel en el ecuador
RADIO_KM = 12.0
# A este zoom el mar viene a 0 m (la batimetría de ETOPO1 y GEBCO solo está en zooms bajos) y la tierra, por encima: la
# orilla es lo que está a más de 1 m. Los arrecifes a flor de agua no se ven: cuentan como
# agua. Se probó a sacarlos de la batimetría de zoom 10 (GEBCO, ~150 m), que sí dibuja el anillo de los atolones, pero
# también ensancha la costa y daba pasos cerrados en costas abiertas (Curaçao): queda para una fuente de arrecifes.
BARRERA_M = 1.0
CELDA_KM = 9.0
_s = requests.Session()


@lru_cache(maxsize=8192)
def _tesela(x, y, z=Z):
    from PIL import Image

    for n in range(4):
        try:
            r = _s.get(URL.format(z=z, x=x, y=y), timeout=60)
            r.raise_for_status()
            a = np.asarray(Image.open(io.BytesIO(r.content)).convert('RGB'), dtype=np.float64)
            return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
        except Exception:  # noqa: BLE001
            if n == 3:
                raise


def _px(la, lo, z=Z):
    """Píxel global (x, y) de una coordenada en el zoom z."""
    n = 2 ** z * 256
    x = (lo + 180) / 360 * n
    s = math.sin(math.radians(la))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _mosaico(la, lo, km, z=Z):
    """Elevación (m) en un cuadrado de ±km alrededor del spot, metros por píxel y píxel del spot."""
    mpp = 156543.03 * math.cos(math.radians(la)) / 2 ** z
    r = int(km * 1000 / mpp) + 2
    x, y = _px(la, lo, z)
    x0, x1, y0, y1 = int(x) - r, int(x) + r, int(y) - r, int(y) + r
    tx = range(x0 // 256, x1 // 256 + 1)
    ty = range(y0 // 256, y1 // 256 + 1)
    big = np.vstack([np.hstack([_tesela(i % 2 ** z, j, z) for i in tx]) for j in ty])
    ox, oy = tx[0] * 256, ty[0] * 256
    return big[y0 - oy:y1 - oy, x0 - ox:x1 - ox], mpp, (int(y) - y0, int(x) - x0)


def mide(la, lo):
    e, mpp, (cy, cx) = _mosaico(la, lo, RADIO_KM)
    barrera = e > BARRERA_M
    # Si el punto del spot cae en tierra (coordenadas aproximadas), el agua más cercana a menos de 1 km.
    if barrera[cy, cx]:
        yy, xx = np.nonzero(~barrera)
        if not len(yy):
            return None
        d = np.hypot(yy - cy, xx - cx)
        k = int(np.argmin(d))
        if d[k] * mpp > 1000:
            return {'enTierra': True}
        cy, cx = int(yy[k]), int(xx[k])
    lim = int(RADIO_KM * 1000 / mpp)
    dist = []
    for a in range(0, 360, 10):
        dx, dy = math.sin(math.radians(a)), -math.cos(math.radians(a))
        d = lim
        for s in range(1, lim):
            y, x = int(round(cy + dy * s)), int(round(cx + dx * s))
            if not (0 <= y < e.shape[0] and 0 <= x < e.shape[1]):
                break
            if barrera[y, x]:
                d = s
                break
        dist.append(d * mpp / 1000)
    # Anchura del agua frente al spot: de la orilla más cercana a la de enfrente. En una costa abierta, enfrente no hay
    # orilla (llega al radio); en un paso o una bahía estrecha, sí. Medirla en la dirección más corta de todas daba
    # pasos falsos en costas rectas (a lo largo de la orilla siempre hay un cabo cerca).
    n = int(np.argmin(dist))
    ancho = dist[n] + dist[(n + 18) % 36]
    half = int(CELDA_KM / 2 * 1000 / mpp)
    caja = barrera[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
    return {
        'anchuraKm': round(ancho, 1),
        'orillaKm': round(dist[n], 2),
        'orillaDir': n * 10,
        'abiertoPct': round(100 * sum(d >= RADIO_KM - 0.1 for d in dist) / len(dist)),
        'tierraPct': round(100 * float(caja.mean())),
    }


def todos(spots, workers=16):
    """{sid: medidas} para {sid: (lat, lng)}."""
    def una(item):
        sid, (la, lo) = item
        try:
            return sid, mide(la, lo)
        except Exception as ex:  # noqa: BLE001
            return sid, {'error': f'{type(ex).__name__} {str(ex)[:80]}'}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(una, spots.items()))
