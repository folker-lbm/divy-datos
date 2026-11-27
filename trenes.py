"""
T139 (en sombra): oleaje por trenes con la exposición de T126. No lo usa la previsión publicada; lo usa
experimento_t139.py, que lo calcula y lo guarda aparte para compararlo con lo actual.

Hoy el motor toma el oleaje TOTAL (altura, dirección media y periodo de pico) y lo multiplica por 1, 0,6 o 0,25 según la
dirección media caiga dentro del sector abierto del spot, a menos de 30° o fuera (y 0,6 con lo que solo abriga el
arrecife). Con mar de viento de un lado y mar de fondo de otro, la dirección media no es la de ninguno. Aquí:

1. Cada tren por separado: mar de viento (`VHM0_WW`, `VMDR_WW`, `VTM01_WW`) y los dos mares de fondo (`_SW1`, `_SW2`),
   cada uno con su altura, su dirección (de dónde viene) y su periodo medio, en la misma celda de mar de Copernicus que
   usa la previsión. La energía que las particiones no recogen se reparte entre ellas en proporción a la suya.
2. La exposición de T126, rayo a rayo (72 rayos de 30 km cada 5°; `s`, camino de mar de fondo con difracción por
   Fresnel 8; `f`, km de agua; docs/T126-METODO.md). Un tren llega al spot con la fracción de su energía que viene por
   rayos con camino abierto, repartida con la dispersión direccional de su tipo de mar (el abanico):
   D(θ) ∝ cos^(2s)((θ − θ0)/2). Los s_max de Goda (2000) son 10 (mar de viento), 25 (mar de fondo corto) y 75 (largo),
   pero valen en la frecuencia de pico; el tren entero (una partición) está más abierto porque s cae lejos del pico
   (Mitsuyasu et al. 1975). Integrado en frecuencia, la misma energía fuera de ±30° la da un s de 5, 12 y 28
   (revisión independiente del 27 nov), y eso es lo que se usa. Con T139_ABANICO=pico, los s_max tal cual.
   Altura en el spot = altura del tren × √(fracción) × arrecife, nunca menos de SUELO.
3. El mar de viento es el mayor de dos: el del modelo que entra por los rayos y el que el viento de la previsión forma
   sobre el agua que hay delante (fetch de su dirección, media de ±15°; SPM 1984, como T126), sin pasar del del modelo.
4. El mar en la entrada es la suma de energías de los tres: √(Hw² + H1² + H2²). Se publica como factor sobre el total.
5. Entradas desde costa: el periodo largo baja el umbral (no cambia la altura que se enseña). La altura de rotura crece
   como H0^0,8 · T^0,4 (Komar y Gaughan 1972), así que a igual altura en mar abierto un mar de periodo T rompe como uno
   de T_REF con la altura multiplicada por (T/T_REF)^0,4 ≈ √(T/T_REF); el umbral se divide por √(T/T_REF), solo hacia
   abajo y con tope K_MAX. T es el periodo medio (Tm01) del tren que más energía mete; T_REF = 8 s de Tm01 son unos
   10 s de periodo de pico. En barco, sin cambio.
6. Viento y rachas cuentan si el viento sopla desde una dirección con agua suficiente para formar mar de 0,25 m con
   40 km/h (el criterio con el que T126 da una dirección por abrigada del viento), en lugar de «dentro del sector».
7. Barco (propuesta para decidir): con el oleaje total por encima del umbral difícil de barco, el mar en la entrada no
   baja del umbral bueno de barco, porque la travesía hasta el punto abrigado también cuenta (Berlengas con 3 m).
"""
import gzip
import json
import math
import os
from pathlib import Path

G = 9.81
PASO = 5  # grados entre rayos (T126)
N_RAYOS = 72
if os.environ.get('T139_ABANICO') == 'pico':
    S_VIENTO, S_FONDO_CORTO, S_FONDO_LARGO = 10, 25, 75  # Goda 2000: s_max en la frecuencia de pico
else:
    S_VIENTO, S_FONDO_CORTO, S_FONDO_LARGO = 5, 12, 28  # el mismo abanico integrado en frecuencia
T_FONDO_LARGO = 10.0  # s (periodo medio): desde aquí, mar de fondo largo (aproximación; Goda usa el peralte)
T_REF = 8.0  # s (periodo medio, unos 10 s de pico): referencia del umbral de mar de la entrada desde costa
K_MAX = 1.5  # tope del factor del periodo (el umbral de 0,5 m nunca baja de 0,33 m)
VIENTO_REF, MAR_REF = 40.0, 0.25  # km/h y m: criterio de T126 para que el viento cuente
# Suelo de la altura de cada tren en la entrada: el «abrigado» del motor de hoy (0,25). El rayo recto no ve la
# refracción ni las reflexiones, que meten mar de fondo detrás de las puntas (Honolua Bay con el mar del norte, prueba del
# 27 nov); con el suelo, ningún tren llega más flojo de lo que hoy llega el oleaje a un spot abrigado.
SUELO = float(os.environ.get('T139_SUELO', 0.25))
ARRECIFE = 0.6  # el «algo expuesto» del motor de hoy para lo que solo abriga el arrecife (T126, opción D)
BARCO_DIFICIL, BARCO_BUENO = 1.5, 0.8  # umbrales de mar de barco del motor (thresholdsFor)
TOPE_BARCO = os.environ.get('T139_TOPE_BARCO', '1') != '0'
RAYOS = Path(__file__).resolve().parent / 'datos' / 't139-rayos.json.gz'
# Spots que no usan los trenes (siguen con el sector): en tierra de OSM, con la posición aproximada o movida más de
# 0,5 km, o con la coordenada redondeada (prueba y revisión del 27 nov).
EXCLUIDOS = Path(__file__).resolve().parent / 'datos' / 't139-excluidos.json'
SIN_ARRECIFE = object()


def ua(u):
    """SPM 1984, ec. 3-28: viento ajustado (m/s)."""
    return 0.71 * u ** 1.23


def hm0_viento(u_kmh, f_km):
    """Mar de viento limitado por el fetch (SPM 1984, ec. 3-33 y 3-34), en m."""
    if u_kmh is None or f_km is None or u_kmh <= 0 or f_km <= 0:
        return 0.0
    u = ua(u_kmh / 3.6)
    return min(1.6e-3 * u * math.sqrt(f_km * 1000 / G), 0.2433 * u * u / G)


def cargar_rayos(path=RAYOS, excluidos=EXCLUIDOS):
    """{id: {"f": [72 km], "s": "0/1 x 72"}} de T126 (mundo, 19 nov) y de España (mismo método, 27 nov), sin los excluidos."""
    with gzip.open(path, 'rt') as fh:
        rayos = json.load(fh)
    fuera = json.loads(Path(excluidos).read_text())['spots'] if excluidos and Path(excluidos).exists() else {}
    return {k: v for k, v in rayos.items() if k not in fuera}


def _idx(deg):
    return int(round((deg % 360) / PASO)) % N_RAYOS


def fetch_dir(rayo, deg):
    """Fetch de una dirección: media de los 7 rayos en ±15° (T126)."""
    i = _idx(deg)
    return sum(rayo['f'][(i + k) % N_RAYOS] for k in range(-3, 4)) / 7


def s_de(tipo, tm):
    """Dispersión del tren según el tipo de mar."""
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


def distancia_sector(d, sector):
    """Distancia angular (°) de una dirección al sector [desde, hasta]; 0 si está dentro (synthesis.ts)."""
    desde, hasta = sector
    if hasta - desde >= 360:
        return 0
    n = lambda a: a % 360  # noqa: E731
    ancho = n(hasta - desde)
    if n(d - desde) <= ancho:
        return 0
    return min(n(desde - d), n(d - n(desde + ancho)))


def factor_arrecife(d, sector, arrecife):
    """Como el motor de hoy (T126, opción D): una dirección dentro del sector que solo abriga el arrecife (fuera de
    `reefOpen`, o todo el sector con `reefOpen: null`) llega al 60 %. Los rayos solo ven la tierra, no los arrecifes."""
    if arrecife is SIN_ARRECIFE or sector is None or distancia_sector(d, sector) > 0:
        return 1.0
    return ARRECIFE if arrecife is None or distancia_sector(d, arrecife) > 0 else 1.0


def viento_cuenta(rayo, wdir):
    """¿Cuenta el viento? Sí si sobre el agua de su dirección se forma mar de 0,25 m con 40 km/h (T126)."""
    return hm0_viento(VIENTO_REF, fetch_dir(rayo, wdir)) >= MAR_REF


def entrada(rayo, trenes, viento=None, total=None, sector=None, arrecife=SIN_ARRECIFE, barco=False):
    """Mar en la entrada. `trenes`: {'ww': (hs, dir, tm), 'sw1': ..., 'sw2': ...} (cualquiera puede faltar o ser None).
    `viento`: (km/h, dirección) de la previsión. `total`: altura del oleaje total (VHM0) en la celda. `sector` y
    `arrecife`: `open` y `reefOpen` del catálogo (sin `reefOpen`, SIN_ARRECIFE). `barco`: entrada en barco.
    Devuelve (altura en m, periodo medio del tren dominante o None, detalle {tren: [altura en la entrada, periodo]}; el
    mar de viento formado delante sale como 'local').

    Correcciones de la prueba y de la revisión del 27 nov:
    - Las particiones no siempre suman toda la energía del total (en el global, la mediana es el 96 % de la altura y el
      10 % peor, el 86 %). La que falta se reparte entre los trenes en proporción a la suya, para que un spot abierto a
      todo reciba el total, como hoy, y no salga más verde solo por eso.
    - El mar de viento que se forma delante no puede ser mayor que el del modelo en la celda de mar abierto (el SPM no
      limita por duración; con fetch de 30 km daba más que el modelo).
    - El suelo va después del arrecife: ningún tren llega por debajo del 25 %, como hoy un spot abrigado."""
    crudos = {k: t for k in ('ww', 'sw1', 'sw2') if (t := trenes.get(k)) and t[0] is not None and t[1] is not None and t[0] > 0}
    suma = math.sqrt(sum(t[0] ** 2 for t in crudos.values()))
    escala = total / suma if total and suma > 0 else 1.0
    det = {}
    for k, (hs, d, tm) in crudos.items():
        f = math.sqrt(fraccion(rayo, d, s_de(k, tm))) * factor_arrecife(d, sector, arrecife)
        det[k] = [hs * escala * max(SUELO, f), tm]
    if viento and viento[0] is not None and viento[1] is not None:
        tope = crudos['ww'][0] * escala if 'ww' in crudos else 0.0
        local = min(hm0_viento(viento[0], fetch_dir(rayo, viento[1])), tope)
        if local > det.get('ww', [0])[0]:
            det.pop('ww', None)
            det['local'] = [local, None]  # sin periodo: el mar corto que se forma delante no baja el umbral
    h = math.sqrt(sum(v[0] ** 2 for v in det.values()))
    dom = max(det.values(), key=lambda v: v[0]) if det else None
    if barco and TOPE_BARCO and total is not None and total > BARCO_DIFICIL:
        h = max(h, BARCO_BUENO)
    return h, (dom[1] if dom else None), det


def factor_periodo(tm, barco):
    """Factor por el que se divide el umbral de mar de la entrada desde costa (1 si no cambia)."""
    if barco or tm is None or tm <= T_REF:
        return 1.0
    return min(K_MAX, math.sqrt(tm / T_REF))


def misma_fuente(sombra_tag, sombra_km, celdas_publicadas):
    """¿Viene el oleaje publicado de la misma celda de Copernicus que la sombra? `celdas_publicadas`: `seaCellKm` de la
    previsión. La previsión usa el primero de MEDSEA, IBI, regional (también CARICOOS) y global que tenga celda a 10 km
    o menos; si es otro modelo, u otra celda, los trenes no se aplican (CARICOOS en Puerto Rico y las Islas Vírgenes)."""
    for tag in ('med', 'ibi', 'reg', 'glo'):
        km = (celdas_publicadas or {}).get('wav' + tag.capitalize())
        if km is not None and km <= 10:
            return tag == sombra_tag and abs(km - sombra_km) <= 0.15
    return False
