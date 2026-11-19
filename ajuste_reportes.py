"""T149, capa 3 de CORRIENTE-A-ESCALA-DE-SPOT.md: ajuste de la corriente de marea de un spot con sus reportes.

El modelo da el ritmo de la zona; los reportes corrigen el spot con dos números:
  · dt (minutos): cuánto va retrasada la corriente del spot respecto al modelo (positivo: el spot va después);
  · k: cuánto se amplifica (o se queda en menos) la corriente en el spot respecto al modelo.
Corriente ajustada: c_spot(t) = k · c_modelo(t − dt).

Un reporte es (hora de entrada, nivel 0–3: calma, suave, moderada, fuerte). Se supone que el buceador llama a la
corriente por su velocidad media durante la inmersión con los cortes de la regla fija de la app (0,5, 1 y 2 km/h) y que
se equivoca a veces: el nivel sale de log(velocidad) con un error normal de desviación SIGMA (modelo ordinal). El ajuste
busca el dt y el k más probables con una «previa» que tira hacia el modelo sin tocar (dt = 0, k = 1): con pocos
reportes casi no se mueve, y con muchos manda lo que dicen los buceadores.

T149 (19 nov 2026). En sombra: no cambia nada de lo que se publica sin DIVY_AJUSTE_MODO=aplicar y el OK de Luka.
Validación: /Volumes/MacExt/Divy-cache/T149-reportes/INFORME.md (estaciones oficiales de NOAA, reportes simulados).

Uso:
  python ajuste_reportes.py calcular <reportes.json> <salida.json>
      Ajuste por spot con los reportes reales. reportes.json: [{spotId, diveTime (ms), createdAt (ms), current (0–3), author}].
  En pipeline.py, con DIVY_AJUSTE_REPORTES=<salida.json>: en sombra (por defecto) guarda en salida/ajuste-sombra.json qué
  cambiaría en la marea de cada spot; con DIVY_AJUSTE_MODO=aplicar, además la cambia antes de sumarla a la global (cMar).

Antes de encenderlo (DIVY_AJUSTE_MODO=aplicar): lo habitual del spot (corr.hab, habitual.py) se calculó con la marea sin
ajustar. Un desfase no cambia los percentiles, pero una fuerza k sí cambia la parte de marea: hay que recalcular lo
habitual de los spots con k distinto de 1, o el color comparará una corriente ajustada con un habitual sin ajustar."""
import math

import numpy as np
from scipy.special import ndtr

CORTES = np.log([0.5, 1.0, 2.0])  # km/h: calma | suave | moderada | fuerte (regla fija de la app, REGLAS 2)
SIGMA = 0.75  # error del buceador al juzgar la corriente más el del modelo en ese spot, en log(km/h)
RARO = 0.10  # parte de reportes que el modelo no puede explicar (otro lado del bajo, un remolino, un error al apuntar)
_DT = DT = np.arange(-150, 151, 10)  # minutos (la velocidad se repite cada ~6 h: más allá de ±3 h el desfase es ambiguo)
_K = K = np.exp(np.linspace(math.log(0.25), math.log(4.0), 41))
PREVIA_DT = 60.0  # minutos
PREVIA_LOGK = 0.6
DURACION = 45  # minutos de inmersión que se promedian
SUELO = 0.05  # km/h, para el logaritmo
# Desfase: apagado por defecto. En la validación, con reportes repartidos al azar, empeora la hora de la parada en 1 de
# cada 4–5 spots; la fuerza sola no la toca y es lo que más baja la «calma falsa» (INFORME.md de T149).
CON_DESFASE = False
# Los reportes solo pueden subir la corriente del spot, nunca bajarla (k ≥ 1). En los spots de marea los centros bucean
# en la parada: sus «calma» enseñarían al ajuste que el spot es tranquilo y subiría la calma falsa (validación de T149,
# escenario «parada»). Coherente con «nunca en calma como certeza» (REGLAS, 3 ter punto 3).
SOLO_SUBIR = True
PUERTA_SD_DT = 40.0  # minutos: incertidumbre máxima del desfase para aplicarlo


def prob_niveles(v):
    """P(nivel | velocidad v km/h) para los cuatro niveles. v: array (...); devuelve (..., 4)."""
    lv = np.log(np.maximum(v, SUELO))
    z = (CORTES - lv[..., None]) / SIGMA
    cdf = ndtr(z)
    p = np.concatenate([cdf[..., :1], np.diff(cdf, axis=-1), 1 - cdf[..., -1:]], axis=-1)
    return (1 - RARO) * p + RARO / 4


def velocidad_buceo(serie, t_min, dt=0.0):
    """Velocidad media del modelo (km/h) durante la inmersión que empieza en t_min (minutos desde el origen de la serie,
    serie cada 10 min), con la serie desplazada dt minutos (c(t − dt))."""
    t = np.asarray(t_min, float)[:, None] + np.arange(0, DURACION + 1, 15)[None, :] - dt
    x = np.arange(len(serie)) * 10.0
    return np.interp(t, x, serie).mean(axis=1)


def ajustar(serie, t_rep, nivel, previa=True, con_desfase=True, solo_subir=SOLO_SUBIR):
    """(dt, k, info) más probables. serie: velocidad del modelo cada 10 min (km/h); t_rep: minutos de entrada de cada
    reporte; nivel: 0–3. Devuelve también la confianza: cuánto se concentra la probabilidad en dt."""
    t_rep = np.asarray(t_rep, float); nivel = np.asarray(nivel, int)
    if not len(t_rep):
        return 0.0, 1.0, {'n': 0}
    DT = _DT if con_desfase else np.array([0])
    K = _K[_K >= 1] if solo_subir else _K
    V = np.stack([velocidad_buceo(serie, t_rep, d) for d in DT])  # (DT, N)
    ll = np.zeros((len(DT), len(K)))
    for j, k in enumerate(K):
        p = prob_niveles(V * k)  # (DT, N, 4)
        ll[:, j] = np.log(np.take_along_axis(p, nivel[None, :, None], axis=2)[..., 0]).sum(axis=1)
    if previa:
        ll += -0.5 * (DT[:, None] / PREVIA_DT) ** 2 - 0.5 * (np.log(K)[None, :] / PREVIA_LOGK) ** 2
    w = np.exp(ll - ll.max()); w /= w.sum()
    pdt, pk = w.sum(axis=1), w.sum(axis=0)
    m_dt = float((pdt * DT).sum()); m_lk = float((pk * np.log(K)).sum())
    sd_dt = float(np.sqrt((pdt * (DT - m_dt) ** 2).sum()))
    sd_lk = float(np.sqrt((pk * (np.log(K) - m_lk) ** 2).sum()))
    info = {'n': int(len(t_rep)), 'sd_dt': round(sd_dt), 'sd_logk': round(sd_lk, 2)}
    # Puerta: el desfase solo se aplica si está bien determinado (no es casi tan probable lo contrario); si no, se queda
    # el del modelo. La fuerza se aplica siempre, con su media (ya encogida hacia 1 por la previa).
    usa_dt = sd_dt <= PUERTA_SD_DT and abs(m_dt) >= sd_dt
    info['usa_dt'] = bool(usa_dt)
    return (round(m_dt / 10) * 10.0 if usa_dt else 0.0), float(math.exp(m_lk)), info


def loglik(serie, t_rep, nivel, dt, k):
    """Log-probabilidad de unos reportes con la corriente del modelo ajustada (dt, k)."""
    p = prob_niveles(velocidad_buceo(serie, np.asarray(t_rep, float), dt) * k)
    return float(np.log(p[np.arange(len(nivel)), np.asarray(nivel, int)]).sum())


def ajustar_validado(serie, t_rep, nivel, pliegues=5, ganancia_min=0.05, n_min=8, semilla=0, con_desfase=CON_DESFASE, solo_subir=SOLO_SUBIR):
    """Ajuste con su propia comprobación, la que puede hacer el servidor sin saber la verdad: validación cruzada sobre los
    reportes. Se ajusta con 4/5 de los reportes y se mide cuánto mejor predice el 1/5 restante que el modelo sin tocar.
    Solo si gana de media más de `ganancia_min` (log-probabilidad por reporte) y hay al menos `n_min` reportes se usa el
    ajuste con todos; si no, el modelo se queda como está (dt = 0, k = 1). Devuelve (dt, k, info)."""
    t_rep = np.asarray(t_rep, float); nivel = np.asarray(nivel, int); n = len(t_rep)
    if n < n_min:
        return 0.0, 1.0, {'n': n, 'aplicado': False, 'motivo': 'pocos'}
    orden = np.random.default_rng(semilla).permutation(n)
    gan = []
    for f in range(pliegues):
        te = orden[f::pliegues]; tr = np.setdiff1d(orden, te)
        d, k, _ = ajustar(serie, t_rep[tr], nivel[tr], con_desfase=con_desfase, solo_subir=solo_subir)
        gan.append(loglik(serie, t_rep[te], nivel[te], d, k) - loglik(serie, t_rep[te], nivel[te], 0.0, 1.0))
    g = sum(gan) / n
    d, k, info = ajustar(serie, t_rep, nivel, con_desfase=con_desfase, solo_subir=solo_subir)
    info.update(ganancia=round(g, 3), aplicado=bool(g > ganancia_min))
    return (d, k, info) if g > ganancia_min else (0.0, 1.0, {**info, 'motivo': 'no mejora'})


# Reportes que hacen falta para intentar el ajuste (la validación cruzada decide después si se aplica).
N_MIN = 8
AUTORES_MIN = 3  # personas distintas: un solo buceador muy activo no recalibra un spot


def aplicar(u, v, dt_min, k):
    """Corriente horaria (u, v) del modelo con el ajuste: k · c(t − dt), interpolando entre horas."""
    h = np.arange(len(u), dtype=float)
    t = h - dt_min / 60.0
    return k * np.interp(t, h, u), k * np.interp(t, h, v)


def _rumbo(u, v):
    return (math.degrees(math.atan2(u, v)) + 360) % 360


def aplicar_a_mareas(mareas, ajustes, aplicar_de_verdad=False):
    """Ajuste de cada spot sobre la salida de marea.descargar ({sid: {'horas': {hora: (kmh, rumbo, u, v)}}}).
    En sombra (por defecto) no toca `mareas` y devuelve, por spot, cómo quedaría; con aplicar_de_verdad, la cambia."""
    resumen = {}
    for sid, a in ajustes.items():
        m = mareas.get(sid)
        if not m or not a.get('aplicado'):
            continue
        hs = sorted(m['horas'])
        u = np.array([m['horas'][h][2] for h in hs], float)
        v = np.array([m['horas'][h][3] for h in hs], float)
        ua, va = aplicar(u, v, a['dt'], a['k'])
        antes = np.hypot(u, v) * 3.6
        despues = np.hypot(ua, va) * 3.6
        resumen[sid] = {'dt': a['dt'], 'k': a['k'], 'horas': len(hs), 'max_antes': round(float(antes.max()), 2),
                        'max_despues': round(float(despues.max()), 2),
                        'cambio_medio_kmh': round(float(np.mean(np.abs(despues - antes))), 2)}
        if aplicar_de_verdad:
            for h, a1, b1 in zip(hs, ua, va):
                m['horas'][h] = (round(math.hypot(a1, b1) * 3.6, 2), round(_rumbo(a1, b1)), float(a1), float(b1))
    return resumen


def serie_10min(horas):
    """{hora ISO: (kmh, …)} → (origen datetime, velocidad cada 10 min en km/h)."""
    from datetime import datetime, timezone

    hs = sorted(horas)
    t0 = datetime.fromisoformat(hs[0]).replace(tzinfo=timezone.utc)
    idx = [int((datetime.fromisoformat(h).replace(tzinfo=timezone.utc) - t0).total_seconds() // 3600) for h in hs]
    v = np.array([horas[h][0] for h in hs], float)
    full = np.interp(np.arange(idx[-1] + 1), idx, v)
    return t0, np.interp(np.arange(idx[-1] * 6 + 1) / 6, np.arange(idx[-1] + 1), full)


def hora_por_defecto(r):
    """La app 1.2 propone como hora de la inmersión «hace 40 minutos» (store.tsx). Si se publica sin tocarla, la hora es la
    de publicar menos 40 min, que puede no ser la del buceo (en producción, 4 de 8 reportes el 19 nov): para el ajuste,
    que depende de la hora, no vale. Necesita `createdAt` (ms) en el reporte; sin él, se da por buena."""
    c = r.get('createdAt')
    return c is not None and 39 * 60_000 <= c - r['diveTime'] <= 42 * 60_000


def calcular(reportes, coords, descargar=None):
    """Ajuste por spot. reportes: [{spotId, diveTime, current, author}]; coords: {sid: (lat, lng)}.
    Devuelve {sid: {dt, k, n, autores, aplicado, motivo?, ganancia?, sd_dt?}} para todos los spots con algún reporte."""
    from datetime import datetime, timedelta, timezone

    if descargar is None:
        from marea import descargar
    por = {}
    for r in reportes:
        if r.get('current') is None or r.get('spotId') not in coords:
            continue
        if hora_por_defecto(r):
            continue
        por.setdefault(r['spotId'], []).append(r)
    out = {}
    listos = {}
    for sid, rs in por.items():
        autores = len({r['author'] for r in rs})
        out[sid] = {'dt': 0.0, 'k': 1.0, 'n': len(rs), 'autores': autores, 'aplicado': False}
        if len(rs) < N_MIN or autores < AUTORES_MIN:
            out[sid]['motivo'] = 'pocos'
        else:
            listos[sid] = rs
    if not listos:
        return out
    t_min = min(r['diveTime'] for rs in listos.values() for r in rs) / 1000
    t_max = max(r['diveTime'] for rs in listos.values() for r in rs) / 1000
    t0 = datetime.fromtimestamp(t_min, tz=timezone.utc) - timedelta(days=1)
    t1 = datetime.fromtimestamp(t_max, tz=timezone.utc) + timedelta(days=1)
    warnings = []
    mareas = descargar({sid: coords[sid] for sid in listos}, t0, t1, warnings)
    for sid, rs in listos.items():
        if sid not in mareas:
            out[sid]['motivo'] = 'sin marea'
            continue
        origen, serie = serie_10min(mareas[sid]['horas'])
        t_rep = [(r['diveTime'] / 1000 - origen.timestamp()) / 60 for r in rs]
        d, k, info = ajustar_validado(serie, t_rep, [r['current'] for r in rs])
        out[sid].update(dt=d, k=round(k, 3), **{x: info[x] for x in ('aplicado', 'ganancia', 'sd_dt', 'motivo') if x in info})
    if warnings:
        print('avisos:', warnings[:5])
    return out


def _coords():
    """Posición de cada spot del catálogo (mundo y España), como los usa el proceso de datos."""
    import glob
    import json
    import os

    raiz = os.environ.get('DIVY_SPOTS_RAIZ') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'spots')
    c = {}
    for f in glob.glob(os.path.join(raiz, 'mundo', '*.json')) + [os.path.join(raiz, '..', '..', 'app', 'src', 'data', 'spots.json')]:
        if os.path.exists(f):
            for s in json.load(open(f)):
                c[s['id']] = (s['coord']['lat'], s['coord']['lng'])
    return c


if __name__ == '__main__':
    import json
    import sys

    if len(sys.argv) != 4 or sys.argv[1] != 'calcular':
        sys.exit(__doc__)
    res = calcular(json.load(open(sys.argv[2])), _coords())
    json.dump(res, open(sys.argv[3], 'w'), indent=1)
    aplic = [s for s, a in res.items() if a['aplicado']]
    print(f'{len(res)} spots con reportes de corriente; ajuste aplicable en {len(aplic)}: {aplic[:10]}')
