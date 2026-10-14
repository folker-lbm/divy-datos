"""
Boyas: medidas reales cerca de cada spot (FUENTES-DATOS.md, paso 3; aprobado por Luka el 13 oct).

Fuente GLOBAL: Copernicus Marine In Situ (INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030, parte «latest»), que reúne las
boyas de todo el mundo, incluidas las de Puertos del Estado. Por la regla «global salvo que lo local sea mucho mejor» se
usa esta y no la de Puertos del Estado directamente.

Para cada spot: la boya fondeada (tipo MO) más cercana, a 25 km como mucho, con su última altura de ola (VHM0) de las
últimas 12 h y, si la tiene, la temperatura en superficie. La app la compara con la previsión de esa hora.
Necesita el acceso normal de Copernicus (`read_dataframe`); si falla, se omite con un aviso.
"""
import math
from datetime import timedelta

MAX_KM = 25
HOURS = 12


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(d))


def buoys(spots, now, warnings):
    """{spot_id: {'id', 'km', 'time', 'hs', 'temp'}} con la boya más cercana y su última medida."""
    try:
        import copernicusmarine as cm

        lats = [p[0] for p in spots.values()]
        lons = [p[1] for p in spots.values()]
        df = cm.read_dataframe(
            dataset_id='cmems_obs-ins_glo_phybgcwav_mynrt_na_irr', dataset_part='latest',
            minimum_latitude=min(lats) - 0.3, maximum_latitude=max(lats) + 0.3,
            minimum_longitude=min(lons) - 0.3, maximum_longitude=max(lons) + 0.3,
            start_datetime=(now - timedelta(hours=HOURS)).strftime('%Y-%m-%dT%H:00:00'), end_datetime=now.strftime('%Y-%m-%dT%H:00:00'),
            variables=['VHM0', 'TEMP'], disable_progress_bar=True,
        )
    except Exception as e:  # noqa: BLE001
        warnings.append(f'Boyas (Copernicus In Situ): {type(e).__name__} {str(e)[:120]}')
        return {}
    # Solo boyas fondeadas (MO) y medidas buenas (QC 1 = buena).
    df = df[(df['platform_type'] == 'MO') & (df['value_qc'] == 1)]
    latest = {}
    for (pid, var), g in df.groupby(['platform_id', 'variable']):
        if var == 'TEMP':
            g = g[g['depth'].abs() <= 2]
        if g.empty:
            continue
        row = g.sort_values('time').iloc[-1]
        b = latest.setdefault(pid, {'pos': (float(row['latitude']), float(row['longitude']))})
        b[var] = (row['time'], float(row['value']))
    with_waves = {pid: b for pid, b in latest.items() if 'VHM0' in b}
    out = {}
    for sid, pos in spots.items():
        best = min(((pid, b, _km(pos, b['pos'])) for pid, b in with_waves.items()), key=lambda x: x[2], default=None)
        if best is None or best[2] > MAX_KM:
            continue
        pid, b, d = best
        t, hs = b['VHM0']
        out[sid] = {'id': pid, 'km': round(d, 1), 'time': str(t), 'hs': round(hs, 2), **({'temp': round(b['TEMP'][1], 1)} if 'TEMP' in b else {})}
    print(f'  · boyas: {len(with_waves)} con oleaje, {len(out)} spots con boya a ≤ {MAX_KM} km')
    return out
