"""
Marea de Copernicus por spot (T124, fase 2; 7 nov 2026). En prueba: no se publica sin el OK de Luka.

La corriente global que usamos fuera de Europa (GLOBAL_ANALYSISFORECAST_PHY_001_024, cada 6 h a 10 m) no lleva la
marea. Copernicus la publica aparte en el mismo producto (SMOC), calculada con el modelo de marea FES2014:
  · corriente de marea: `cmems_mod_glo_phy_anfc_merged-uv_PT1H-i`, variables `utide` y `vtide` (superficie, cada hora);
  · altura de la marea: `cmems_mod_glo_phy_anfc_merged-sl_PT1H-i`, variable `ocean_tide`.
Ver docs/T124 en el repositorio de la app (fase 1) y FUENTES-DATOS.md.

Qué se calcula en cada spot:
  · la corriente de marea hora a hora (km/h y hacia dónde va), para sumarla a la del modelo global o enseñarla aparte;
  · pleamares y bajamares (de la altura);
  · repuntes: cuándo la corriente de marea para y cambia de sentido (de la corriente, no de la altura: en los canales el
    repunte va desplazado una o dos horas respecto a la pleamar o la bajamar), y hacia dónde va después;
  · si la marea es de vaivén (va y vuelve, con repuntes) o giratoria (cambia de dirección sin pararse, típico de mar
    abierto): en las giratorias no hay repunte que dar;
  · mareas vivas, muertas o intermedias por día, con la fase de la Luna (cálculo astronómico, sin descargas), y la
    corriente de marea más fuerte de cada día según el modelo.

Lo que no sabe: la fuerza dentro de un canal estrecho (celdas de 9 km). Da el ritmo y el sentido de la zona.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np

DS_UV = 'cmems_mod_glo_phy_anfc_merged-uv_PT1H-i'
DS_SL = 'cmems_mod_glo_phy_anfc_merged-sl_PT1H-i'
ZONA = 3.0  # lado (°) de cada zona de descarga, como las zonas del global en pipeline.py
PAD = 0.15
# Celda del modelo: entre las celdas de mar a como mucho la más cercana + CELDA_TOL km (y nunca a más de CELDA_MAX km),
# la de marea más fuerte. En Cauldron (Komodo) las dos celdas de mar están empatadas a 5,2 km: una en el estrecho
# (3,3 km/h) y otra en mar abierto (0,2 km/h); la más cercana «a secas» podía caer en la de mar abierto.
CELDA_TOL = 3.0
CELDA_MAX = 15.0
# Marea de vaivén si el eje menor de la elipse es menos de esta fracción del mayor; si no, giratoria (sin repuntes).
VAIVEN_MAX = 0.5
# Un repunte solo cuenta si a esa hora la corriente de marea es menos de esta fracción de la más fuerte del día.
REPUNTE_FRAC = 0.35
# Corriente de marea tan floja que no merece la pena hablar de ritmo (km/h de máximo en toda la ventana).
FLOJA_KMH = 0.3


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(d))


def zonas(spots):
    """Grupos de spots cercanos (cajas de ZONA grados) para descargar la marea por zonas."""
    out = []
    for sid, (la, lo) in sorted(spots.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        for z in out:
            b = z['box']
            nb = (min(b[0], la), max(b[1], la), min(b[2], lo), max(b[3], lo))
            if nb[1] - nb[0] <= ZONA and nb[3] - nb[2] <= ZONA:
                z['box'], z['ids'] = nb, z['ids'] + [sid]
                break
        else:
            out.append({'box': (la, la, lo, lo), 'ids': [sid]})
    for z in out:
        b = z['box']
        z['bounds'] = dict(minimum_latitude=b[0] - PAD, maximum_latitude=b[1] + PAD, minimum_longitude=b[2] - PAD, maximum_longitude=b[3] + PAD)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Luna: fase por cálculo astronómico de baja precisión (longitudes eclípticas del Sol y de la Luna; error de pocas horas).

def _elongacion(t):
    """Grados de la Luna respecto al Sol (0 = nueva, 90 = cuarto creciente, 180 = llena, 270 = cuarto menguante)."""
    d = (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    r = math.radians
    g = r(357.529 + 0.98560028 * d)
    ls = 280.459 + 0.98564736 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)
    lm0 = 218.316 + 13.176396 * d
    mm = r(134.963 + 13.064993 * d)
    dd = r(lm0 - ls)  # elongación media
    lm = lm0 + 6.289 * math.sin(mm) + 1.274 * math.sin(2 * dd - mm) + 0.658 * math.sin(2 * dd) - 0.186 * math.sin(g) - 0.114 * math.sin(2 * r(93.272 + 13.229350 * d))
    return (lm - ls) % 360


def fases_luna(t0, t1):
    """Lunas nuevas, llenas y cuartos entre t0 y t1 (hora UTC, redondeada a la hora), buscando cada cruce de 90°."""
    out = []
    t = t0 - timedelta(days=8)
    prev = _elongacion(t)
    nombres = {0: 'nueva', 1: 'creciente', 2: 'llena', 3: 'menguante'}
    while t < t1 + timedelta(days=8):
        t2 = t + timedelta(hours=1)
        e = _elongacion(t2)
        if int(prev // 90) != int(e // 90):
            out.append((t2, nombres[int(e // 90) % 4]))
        prev, t = e, t2
    return out


# Las mareas más fuertes llegan uno o dos días después de la luna nueva o llena («edad de la marea»), y las más flojas
# uno o dos días después de los cuartos. Se cuentan de 1 día antes a 3 días después de cada fase.
VIVAS_DESDE, VIVAS_HASTA = -1.0, 3.0


def vivas_muertas(dia, fases):
    """'vivas', 'muertas' o 'intermedia' para el día (fecha UTC, a mediodía)."""
    t = datetime(dia.year, dia.month, dia.day, 12, tzinfo=timezone.utc)
    for tf, nombre in fases:
        dias = (t - tf).total_seconds() / 86400
        if VIVAS_DESDE <= dias <= VIVAS_HASTA:
            return 'vivas' if nombre in ('nueva', 'llena') else 'muertas'
    return 'intermedia'


# ---------------------------------------------------------------------------------------------------------------------

def _celda(speed_max, lat, lon, pos):
    """(i, j, km) de la celda de mar elegida: la de marea más fuerte entre las casi tan cercanas como la más cercana."""
    cand = []
    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            if np.isfinite(speed_max[i, j]):
                cand.append((_km(pos, (float(la), float(lo))), i, j))
    if not cand:
        return None
    dmin = min(c[0] for c in cand)
    if dmin > CELDA_MAX:
        return None
    cerca = [c for c in cand if c[0] <= min(dmin + CELDA_TOL, CELDA_MAX)]
    d, i, j = max(cerca, key=lambda c: speed_max[c[1], c[2]])
    return i, j, d


def _hora(t):
    return t.strftime('%Y-%m-%dT%H:%M')


def _redondea(t, minutos=10):
    s = minutos * 60
    return datetime.fromtimestamp(round(t.timestamp() / s) * s, tz=timezone.utc)


def _extremos(times, h):
    """Pleamares y bajamares: máximos y mínimos locales de la altura, con el instante afinado por una parábola."""
    out = []
    for k in range(1, len(h) - 1):
        a, b, c = h[k - 1], h[k], h[k + 1]
        if not all(np.isfinite([a, b, c])):
            continue
        if (b > a and b >= c) or (b < a and b <= c):
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if den else 0.0
            off = max(-0.5, min(0.5, off))
            hv = b - 0.25 * (a - c) * off
            out.append((times[k] + timedelta(hours=off), 'pleamar' if b > a else 'bajamar', float(hv)))
    # Quita oscilaciones de menos de 3 cm entre extremos seguidos (ruido en mareas casi planas).
    limpio = []
    for e in out:
        if limpio and abs(e[2] - limpio[-1][2]) < 0.03:
            if e[1] == limpio[-1][1]:
                continue
            limpio.pop()
            continue
        if limpio and e[1] == limpio[-1][1]:
            if (e[1] == 'pleamar') == (e[2] > limpio[-1][2]):
                limpio[-1] = e
            continue
        limpio.append(e)
    return limpio


def _rumbo(u, v):
    """Grados desde el norte hacia los que va la corriente."""
    return (math.degrees(math.atan2(u, v)) + 360) % 360


def analiza(times, u, v, h, fases):
    """Ritmo de la marea en un spot. `times`: horas (datetime UTC); `u`, `v`: corriente de marea (m/s, hacia el este y
    hacia el norte); `h`: altura de la marea (m) o None."""
    ok = np.isfinite(u) & np.isfinite(v)
    if ok.sum() < 24:
        return None
    sp = np.hypot(u, v) * 3.6
    # Elipse de la marea: el eje mayor es la dirección en la que va y vuelve (análisis de componentes principales).
    uu, vv = u[ok] - u[ok].mean(), v[ok] - v[ok].mean()
    cov = np.cov(np.vstack([uu, vv]))
    w, vec = np.linalg.eigh(cov)
    mayor, menor = math.sqrt(max(w[1], 0)), math.sqrt(max(w[0], 0))
    ex, ey = vec[0, 1], vec[1, 1]  # componentes este y norte del eje mayor
    eje = _rumbo(ex, ey) % 180
    vaiven = mayor > 0 and menor / mayor < VAIVEN_MAX
    s = u * ex + v * ey  # componente a lo largo del eje
    res = {
        'maxKmh': round(float(np.nanmax(sp)), 1),
        'eje': round(eje),
        'tipo': 'floja' if np.nanmax(sp) < FLOJA_KMH else ('vaiven' if vaiven else 'giratoria'),
    }
    # Por día: corriente de marea más fuerte y vivas o muertas.
    dias = {}
    for t, x in zip(times, sp):
        if np.isfinite(x):
            k = t.date()
            dias[k] = max(dias.get(k, 0.0), float(x))
    res['dias'] = [{'d': k.isoformat(), 'fase': vivas_muertas(k, fases), 'maxKmh': round(x, 1)} for k, x in sorted(dias.items())]
    eventos = []
    if res['tipo'] == 'vaiven':
        for k in range(len(s) - 1):
            a, b = s[k], s[k + 1]
            if not (np.isfinite(a) and np.isfinite(b)) or (a > 0) == (b > 0) or a == b:
                continue
            f = a / (a - b)
            t = times[k] + timedelta(hours=float(f))
            # Solo si de verdad para: corriente baja respecto a la más fuerte de ese día.
            dmax = dias.get(times[k].date(), 0) or 1
            if min(sp[k], sp[k + 1]) > REPUNTE_FRAC * dmax:
                continue
            # Hacia dónde va después: el sentido del eje que toma la corriente.
            sign = 1 if b > 0 else -1
            eventos.append({'t': _hora(_redondea(t)), 'tipo': 'repunte', 'hacia': round(_rumbo(sign * ex, sign * ey))})
    if h is not None:
        for t, tipo, hv in _extremos(times, h):
            eventos.append({'t': _hora(_redondea(t)), 'tipo': tipo, 'm': round(hv, 2)})
    eventos.sort(key=lambda e: e['t'])
    res['eventos'] = eventos
    return res


def compacta(info, desde, hasta):
    """Ritmo de la marea en pocos bytes (con las claves largas, la previsión crecía un 45 %):
      · `t`: 'v' vaivén, 'g' giratoria o 'f' floja;
      · `e`: eventos entre `desde` y `hasta` (horas 'AAAA-MM-DDTHH:00'), cada uno [hora UTC 'MMDDHHMM', tipo, valor]:
        'r' repunte (valor: rumbo hacia el que va después), 'p' pleamar o 'b' bajamar (valor: altura en m);
      · `d0` y `f`: primer día UTC y la fase de cada día desde él: 'v' vivas, 'm' muertas, 'i' intermedia;
      · `max`: corriente de marea más fuerte (km/h)."""
    ev = [[e['t'][5:].replace('-', '').replace('T', '').replace(':', ''), e['tipo'][0], e.get('hacia', e.get('m'))]
          for e in info['eventos'] if desde <= e['t'] <= hasta[:13] + ':59']
    dias = [x for x in info['dias'] if desde[:10] <= x['d'] <= hasta[:10]]
    return {'t': info['tipo'][0], 'e': ev, 'd0': dias[0]['d'] if dias else desde[:10], 'f': ''.join(x['fase'][0] for x in dias),
            'max': info['maxKmh']}


def recorta(c, desde, hasta, base=None):
    """El resumen compacto (`compacta`) recortado a las horas de `desde` a `hasta` ('AAAA-MM-DDTHH:00'). Con `base`
    (datetime UTC, el inicio de la ventana de la previsión), cada evento lleva la hora en minutos desde `base` en lugar
    de 'MMDDHHMM' (comprime mejor)."""
    from datetime import date, datetime, timezone

    def completa(m):  # 'MMDDHHMM' → 'AAAA-MM-DDTHH:MM', con el año de la ventana (que puede cruzar el fin de año)
        y = int(desde[:4]) + (1 if m[:2] < desde[5:7] else 0)
        return f'{y}-{m[:2]}-{m[2:4]}T{m[4:6]}:{m[6:]}'
    ev = [e for e in c.get('e', []) if desde <= completa(e[0]) <= hasta[:13] + ':59']
    d0 = date.fromisoformat(c.get('d0', desde[:10]))
    i = max(0, (date.fromisoformat(desde[:10]) - d0).days)
    n = (date.fromisoformat(hasta[:10]) - date.fromisoformat(desde[:10])).days + 1
    if base is not None:
        ev = [[int((datetime.fromisoformat(completa(e[0])).replace(tzinfo=timezone.utc) - base).total_seconds() // 60), e[1], e[2]] for e in ev]
    return {**c, 'e': ev, 'd0': max(d0, date.fromisoformat(desde[:10])).isoformat(), 'f': c.get('f', '')[i:i + n]}


VECINAS = 2  # celdas a cada lado del spot donde se busca la celda de mar (±2 celdas de 1/12°, unos ±18 km)


def _abrir(ds_id):
    """El dataset guardado «por puntos» (muchas horas de pocas celdas) en la nube pública de Copernicus, sin inicio de
    sesión. Para la marea de cada spot es mucho más ligero que el guardado «por horas», que trae medio mundo en cada hora:
    con este, cada trozo es medio año de 16 × 8 celdas (unos 150 KB) y solo se piden los trozos con algún spot."""
    import xarray as xr
    import zarr

    from arco import _arco_url, _HttpStore

    return xr.open_zarr(zarr.storage.KVStore(_HttpStore(_arco_url(ds_id, service='arco-time-series'))), consolidated=True)


def _puntos(ds, var, t0, t1, I, J):
    """Serie (horas × puntos) de `var` en las celdas (I[k], J[k]) entre t0 y t1. Devuelve (horas, valores)."""
    import xarray as xr

    da = ds[var].sel(time=slice(np.datetime64(t0.replace(tzinfo=None)), np.datetime64(t1.replace(tzinfo=None))))
    for d in ('depth', 'elevation'):
        if d in da.dims:
            da = da.isel({d: 0})
    sel = da.isel(latitude=xr.DataArray(I, dims='p'), longitude=xr.DataArray(J, dims='p'))
    vals = sel.values.astype('float32')
    times = [datetime.fromtimestamp(int(x.astype('datetime64[s]').astype(np.int64)), tz=timezone.utc) for x in sel.time.values]
    return times, vals


def descargar(spots, t0, t1, warnings, opener=None):
    """Marea de cada spot entre t0 y t1 (datetime UTC). Devuelve {sid: {'horas': {hora: (kmh, rumbo, u, v)}, 'marea': {...}}}.
    Lee de la nube pública de Copernicus (sin inicio de sesión): solo las celdas cercanas a cada spot. `opener` no se usa
    (compatibilidad)."""
    import time

    import dask

    t_ini = time.time()
    fases = fases_luna(t0, t1)
    out = {}
    try:
        uv, sl = _abrir(DS_UV), _abrir(DS_SL)
        lat, lon = uv.latitude.values, uv.longitude.values
        dlat, dlon = float(lat[1] - lat[0]), float(lon[1] - lon[0])
        cerca = {}  # spot -> [(i, j)]
        for sid, (la, lo) in spots.items():
            i0 = int(round((la - lat[0]) / dlat))
            j0 = int(round((((lo - lon[0]) % 360)) / dlon)) % len(lon)
            cerca[sid] = [(i, j % len(lon)) for i in range(i0 - VECINAS, i0 + VECINAS + 1) if 0 <= i < len(lat)
                          for j in range(j0 - VECINAS, j0 + VECINAS + 1)]
        celdas = sorted({c for cs in cerca.values() for c in cs})
        pos = {c: k for k, c in enumerate(celdas)}
        I = np.array([c[0] for c in celdas])
        J = np.array([c[1] for c in celdas])
        with dask.config.set(scheduler='threads', num_workers=16):
            times, U = _puntos(uv, 'utide', t0, t1, I, J)
            _, V = _puntos(uv, 'vtide', t0, t1, I, J)
            # La corriente general del mismo producto (GLO12, superficie, cada hora), solo para saber dónde tiene mar el
            # modelo general: su costa es más gruesa que la de la marea (en Cauldron, la celda con marea es tierra para él).
            _, UO = _puntos(uv, 'uo', t0, t1, I, J)
            try:
                ht, H = _puntos(sl, 'ocean_tide', t0, t1, I, J)
                if ht != times:
                    raise ValueError('horas distintas en la altura y en la corriente')
            except Exception as e:  # noqa: BLE001 — sin altura: se sigue con la corriente (sin pleamar ni bajamar)
                warnings.append(f'Marea, altura: {type(e).__name__} {str(e)[:160]}')
                H = None
    except Exception as e:  # noqa: BLE001
        warnings.append(f'Marea: {type(e).__name__} {str(e)[:200]}')
        return out
    smax_all = np.nanmax(np.hypot(U, V), axis=0) if len(times) else None
    for sid, cs in cerca.items():
        try:
            ks = [pos[c] for c in cs]
            sm = np.array([smax_all[k] for k in ks])
            best = None
            dists = [_km(spots[sid], (float(lat[c[0]]), float(lon[c[1]]))) for c in cs]
            mar = [n for n in range(len(cs)) if np.isfinite(sm[n])]
            if not mar:
                continue
            dmin = min(dists[n] for n in mar)
            if dmin > CELDA_MAX:
                continue
            cand = [n for n in mar if dists[n] <= min(dmin + CELDA_TOL, CELDA_MAX)]
            best = max(cand, key=lambda n: sm[n])
            k = ks[best]
            u, v = U[:, k].astype(float), V[:, k].astype(float)
            h = None
            if H is not None:
                hk = k if np.isfinite(H[0, k]) else None
                if hk is None:
                    hm = [n for n in range(len(cs)) if np.isfinite(H[0, ks[n]])]
                    hk = ks[min(hm, key=lambda n: dists[n])] if hm else None
                h = H[:, hk].astype(float) if hk is not None else None
            info = analiza(times, u, v, h, fases)
            if info is None:
                continue
            info['celdaKm'] = round(dists[best], 1)
            # ¿Ve el modelo el mar de aquí? Parte de las 9 celdas alrededor del spot que son mar en el modelo, y cuánto
            # cambia la marea entre las celdas de mar cercanas (a CELDA_MAX km): si una corre mucho y otra casi nada,
            # entre ellas hay un paso o una costa que el modelo no resuelve (Cauldron: 3,3 y 0,2 km/h).
            c0 = cs[len(cs) // 2]
            nueve = [n for n, c in enumerate(cs) if abs(c[0] - c0[0]) <= 1 and abs(c[1] - c0[1]) <= 1]
            uo_mar = [n for n in range(len(cs)) if np.isfinite(UO[0, ks[n]])]
            info['marPct'] = round(100 * sum(n in uo_mar for n in nueve) / len(nueve))
            if uo_mar:
                nu = min(uo_mar, key=lambda n: dists[n])
                info['celdaGeneralKm'] = round(dists[nu], 1)
                celda_uo = (int(I[ks[nu]]), int(J[ks[nu]]))
            else:
                celda_uo = None
            cer = [sm[n] * 3.6 for n in mar if dists[n] <= CELDA_MAX]
            info['vecinasKmh'] = [round(float(min(cer)), 1), round(float(max(cer)), 1)]
            horas = {}
            for t, a, b in zip(times, u, v):
                if np.isfinite(a) and np.isfinite(b):
                    horas[t.strftime('%Y-%m-%dT%H:00')] = (round(math.hypot(a, b) * 3.6, 2), round(_rumbo(a, b)), float(a), float(b))
            out[sid] = {'horas': horas, 'marea': info, 'celda': (int(I[k]), int(J[k])), 'celdaGeneral': celda_uo}
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Marea ({sid}): {type(e).__name__} {str(e)[:120]}')
    print(f'  · marea descargada: {len(out)} spots en {time.time() - t_ini:.0f} s', flush=True)
    return out
