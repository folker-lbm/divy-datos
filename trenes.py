"""
T139 (en sombra): oleaje por trenes con la exposición de T126. No lo usa la previsión publicada; lo usa
experimento_t139.py, que lo calcula y lo guarda aparte para compararlo con lo actual.

Hoy el motor toma el oleaje TOTAL de Copernicus (altura, dirección media y periodo de pico) y lo multiplica por 1, 0,6
o 0,25 según la dirección media caiga dentro del sector abierto del spot, a menos de 30° o fuera. Con mar de viento de
un lado y mar de fondo de otro, la dirección media no es la de ninguno de los dos. Aquí:

1. Cada tren por separado: mar de viento (`VHM0_WW`, `VMDR_WW`, `VTM01_WW`) y los dos mares de fondo (`_SW1`, `_SW2`),
   cada uno con su altura, su dirección (de dónde viene) y su periodo medio, en la misma celda de mar que usa la previsión.
2. La exposición de T126, rayo a rayo (72 rayos de 30 km cada 5°; `s`, camino de mar de fondo con difracción por
   Fresnel 8; `f`, km de agua; docs/T126-METODO.md). Un tren llega al spot con la fracción de su energía que viene por
   rayos con camino abierto, repartida con la dispersión direccional del tipo de mar (el abanico de dirección):
   D(θ) ∝ cos^(2s)((θ − θ0)/2) (Mitsuyasu et al. 1975; Goda 2000, s_max: 10 para el mar de viento, 25 para el mar de
   fondo de periodo corto y 75 para el mar de fondo largo y lejano). Altura en el spot = altura del tren × √(fracción).
3. El mar de viento es el mayor de dos: el del modelo que entra por los rayos abiertos y el que el viento de la
   previsión forma sobre el agua que hay delante (fetch de la dirección del viento, media de ±15°; SPM 1984, como T126).
4. El mar en la entrada es la suma de energías de los tres: √(Hw² + H1² + H2²).
5. Entradas desde costa: el periodo largo baja el umbral. A igual altura, el flujo de energía en aguas profundas crece
   con el periodo (P ∝ H²·T), así que la altura equivalente a la de un mar de T_REF es H·√(T/T_REF), solo hacia arriba
   y con tope K_MAX. Se usa el periodo medio del tren que más energía mete en la entrada. En barco, sin cambio.
6. Viento y rachas cuentan si el viento sopla desde una dirección con agua suficiente para formar mar de 0,25 m con
   40 km/h (el criterio con el que T126 da una dirección por abrigada del viento), en lugar de «dentro del sector».
"""
import gzip
import json
import math
from pathlib import Path

G = 9.81
PASO = 5  # grados entre rayos (T126)
N_RAYOS = 72
S_VIENTO, S_FONDO_CORTO, S_FONDO_LARGO = 10, 25, 75  # Goda 2000: s_max por tipo de mar
T_FONDO_LARGO = 10.0  # s (periodo medio): desde aquí, mar de fondo largo y lejano (s_max 75)
T_REF = 8.0  # s (periodo medio): referencia del umbral de mar de la entrada desde costa
K_MAX = 1.5  # tope del factor del periodo (el umbral de 0,5 m nunca baja de 0,33 m)
VIENTO_REF, MAR_REF = 40.0, 0.25  # km/h y m: criterio de T126 para que el viento cuente
RAYOS = Path(__file__).resolve().parent / 'datos' / 't139-rayos.json.gz'


def ua(u):
    """SPM 1984, ec. 3-28: viento ajustado (m/s)."""
    return 0.71 * u ** 1.23


def hm0_viento(u_kmh, f_km):
    """Mar de viento limitado por el fetch (SPM 1984, ec. 3-33 y 3-34), en m."""
    if u_kmh is None or f_km is None or u_kmh <= 0 or f_km <= 0:
        return 0.0
    u = ua(u_kmh / 3.6)
    return min(1.6e-3 * u * math.sqrt(f_km * 1000 / G), 0.2433 * u * u / G)


def cargar_rayos(path=RAYOS):
    """{id: {"f": [72 km], "s": "0/1 x 72"}} de T126 (mundo, 19 nov) y de España (mismo método, 27 nov)."""
    with gzip.open(path, 'rt') as fh:
        return json.load(fh)


def _idx(deg):
    return int(round((deg % 360) / PASO)) % N_RAYOS


def fetch_dir(rayo, deg):
    """Fetch de una dirección: media de los 7 rayos en ±15° (T126)."""
    i = _idx(deg)
    return sum(rayo['f'][(i + k) % N_RAYOS] for k in range(-3, 4)) / 7


def s_de(tipo, tm):
    """Dispersión del tren: s_max de Goda según el tipo de mar."""
    if tipo == 'ww':
        return S_VIENTO
    return S_FONDO_LARGO if tm is not None and tm >= T_FONDO_LARGO else S_FONDO_CORTO


_PESOS = {}


def _pesos(s):
    """Pesos de la dispersión direccional en los rayos a ±90° (cos^(2s) de la mitad del ángulo), normalizados."""
    if s not in _PESOS:
        w = [math.cos(math.radians(k * PASO) / 2) ** (2 * s) for k in range(-18, 19)]
        tot = sum(w)
        _PESOS[s] = [x / tot for x in w]
    return _PESOS[s]


def fraccion(rayo, deg, s):
    """Fracción de la energía de un tren que viene de `deg` y llega por rayos con camino abierto."""
    i, w = _idx(deg), _pesos(s)
    return sum(w[k + 18] for k in range(-18, 19) if rayo['s'][(i + k) % N_RAYOS] == '1')


def viento_cuenta(rayo, wdir):
    """¿Cuenta el viento? Sí si sobre el agua de su dirección se forma mar de 0,25 m con 40 km/h (T126)."""
    return hm0_viento(VIENTO_REF, fetch_dir(rayo, wdir)) >= MAR_REF


def entrada(rayo, trenes, viento=None):
    """Mar en la entrada. `trenes`: {'ww': (hs, dir, tm), 'sw1': ..., 'sw2': ...} (cualquiera puede faltar o ser None).
    `viento`: (km/h, dirección) de la previsión. Devuelve (altura en m, periodo medio del tren dominante o None,
    detalle {tren: [altura en la entrada, periodo]}; el mar de viento formado delante sale como 'local')."""
    det = {}
    for k in ('ww', 'sw1', 'sw2'):
        t = trenes.get(k)
        if not t or t[0] is None or t[1] is None or t[0] <= 0:
            continue
        hs, d, tm = t
        det[k] = [hs * math.sqrt(fraccion(rayo, d, s_de(k, tm))), tm]
    if viento and viento[0] is not None and viento[1] is not None:
        local = hm0_viento(viento[0], fetch_dir(rayo, viento[1]))
        if local > det.get('ww', [0])[0]:
            det.pop('ww', None)
            det['local'] = [local, None]  # sin periodo: el mar corto que se forma delante no baja el umbral
    h = math.sqrt(sum(v[0] ** 2 for v in det.values()))
    dom = max(det.values(), key=lambda v: v[0]) if det else None
    return h, (dom[1] if dom else None), det


def factor_periodo(tm, barco):
    """Factor por el que se multiplica la altura de la entrada desde costa (equivale a dividir el umbral por él)."""
    if barco or tm is None or tm <= T_REF:
        return 1.0
    return min(K_MAX, math.sqrt(tm / T_REF))
