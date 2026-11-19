"""Corriente de marea de STOFS-2D-Global por spot (T149, 19 nov 2026). En prueba: no se publica sin el OK de Luka.

STOFS-2D-Global (NOAA) es un modelo de marea y temporal de todo el mundo, con una malla que se afina en la costa (cientos
de metros) en lugar de las celdas de 9 km de la marea de Copernicus. En el piloto de Filipinas y en las estaciones
oficiales de NOAA da la misma hora que Copernicus, pero más fuerza donde la costa la concentra y la mitad de «calma falsa»
(ver Divy-cache/T149-reportes/INFORME.md).

No se descarga cada día: de seis meses de STOFS se sacaron una vez las constantes de marea de la corriente de cada spot
(datos/marea-stofs.json, análisis armónico con UTide). Con ellas se predice la marea de cualquier hora con numpy, sin
dependencias nuevas. Lo que no es marea (viento, temporales) no entra: eso lo pone la corriente global, como hasta ahora.

Se usa con DIVY_MAREA_FUENTE=stofs (pipeline.py y marea.py): en los spots que tienen constantes, la corriente de marea
de Copernicus se cambia por esta; la altura (pleamar y bajamar) sigue siendo la de Copernicus. Sin la variable, todo
sigue como antes. Las constantes se recalculan una vez al año, como lo habitual (habitual.py).
"""
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

RUTA = Path(__file__).resolve().parent / 'datos' / 'marea-stofs.json'
# Solo se usan las constantes que predicen bien la serie que no entró en el ajuste (los últimos 14 días): correlación a lo
# largo del eje de la marea de al menos CORR_MIN. Donde no, se queda la marea de Copernicus.
CORR_MIN = 0.8

_cache = {}


def cargar(ruta=RUTA):
    if ruta not in _cache:
        _cache[ruta] = json.loads(Path(ruta).read_text()) if Path(ruta).exists() else None
    return _cache[ruta]


def usable(r):
    res = r.get('reserva') or {}
    return res.get('corr_eje') is not None and res['corr_eje'] >= CORR_MIN


def predecir(datos, sid, times):
    """(u, v) de marea (m/s, hacia el este y hacia el norte) en el spot a las horas `times` (datetime UTC), o None si el
    spot no tiene constantes usables o las horas se salen de la tabla."""
    r = datos['spots'].get(sid)
    if r is None or not usable(r):
        return None
    tb = datos['tabla']
    banda = min(tb['bandas'], key=lambda b: abs(b - r['lat']))
    E0 = np.array(tb['E'][str(banda)])  # (muestras, constituyentes, 2)
    E0 = E0[..., 0] + 1j * E0[..., 1]
    frq = np.array(datos['frq_cph'])
    c = np.array(r['c'])
    ap, am = c[:, 0] + 1j * c[:, 1], c[:, 2] + 1j * c[:, 3]
    d0 = datetime.combine(date.fromisoformat(tb['desde']), datetime.min.time(), tzinfo=timezone.utc)
    paso_h = tb['paso_dias'] * 24
    h = np.array([(t - d0).total_seconds() / 3600 for t in times])
    k = np.floor(h / paso_h + 0.5).astype(int)  # la muestra más cercana
    if (k < 0).any() or (k >= len(E0)).any():
        return None
    E = E0[k] * np.exp(2j * math.pi * frq[None, :] * (h - k * paso_h)[:, None])
    w = np.einsum('tc,c->t', E, ap) + np.einsum('tc,c->t', np.conj(E), am)  # sin @: numpy 2.0 con Accelerate da avisos falsos
    return w.real, w.imag
