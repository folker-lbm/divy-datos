"""
NASA PACE (instrumento OCI): Kd490 diario global de 4 km en tiempo casi real (FUENTES-DATOS.md; Luka, 13 oct).

Es la fuente GLOBAL de turbidez: sirve fuera de Europa y como relleno en Europa los días en que el satélite de
Copernicus no tiene dato (PACE pasa a otra hora y puede encontrar otras nubes). En la costa europea es más gruesa que
Copernicus OLCI (300 m–1 km) y que la alta resolución (100 m), así que en la prioridad va detrás (ver prioridad en
FUENTES-DATOS.md).

Producto: PACE_OCI_L3M_KD_NRT (versión 3.2), un fichero NetCDF por día (variable Kd por longitud de onda; se usa 490 nm),
1–2 días de retraso.
Descarga con cuenta gratuita de NASA Earthdata: variables EARTHDATA_USERNAME y EARTHDATA_PASSWORD (en GitHub, como
secretos). Sin ellas, se omite con un aviso y la previsión sigue igual.

VIIRS (3 nov): el 30 oct Copernicus dejó de publicar el color del océano global y del Atlántico (incidencia OC-1116) y
fuera del Mediterráneo solo quedó PACE. Con la misma cuenta se leen también los Kd490 diarios de 4 km de los tres VIIRS
de la NASA y la NOAA (SNPP, NOAA-20 y NOAA-21, `VIIRS*_L3m_KD_NRT`, ~6 MB por día y satélite, 1 día de retraso): pasan
a otra hora que PACE y rellenan más días nublados. Van como fuente propia («viirs»), detrás de PACE.
"""
import os
import tempfile
from datetime import datetime, timedelta

import numpy as np

CMR = 'https://cmr.earthdata.nasa.gov/search/granules.json'
SHORT_NAME, VERSION = 'PACE_OCI_L3M_KD_NRT', '3.2'
# Producto de la NASA: (nombre corto, versión o None, texto que debe llevar el fichero de 4 km diario).
PACE = ('NASA PACE', [(SHORT_NAME, VERSION, '.4km.')])
VIIRS = ('NASA VIIRS', [(n, None, '.DAY.KD.Kd_490.4km.') for n in ('VIIRSN_L3m_KD_NRT', 'VIIRSJ1_L3m_KD_NRT', 'VIIRSJ2_L3m_KD_NRT')])
URS = 'urs.earthdata.nasa.gov'


def _session(user, password):
    import requests

    class Session(requests.Session):
        # Las descargas redirigen a URS (inicio de sesión de NASA): solo a él se le mandan las credenciales.
        def rebuild_auth(self, prepared, response):
            if 'Authorization' in prepared.headers and URS not in prepared.url and URS not in response.request.url:
                del prepared.headers['Authorization']

    s = Session()
    s.auth = (user, password)
    s.headers['User-Agent'] = 'divy-pipeline (joindivy.com)'
    return s


def _granules(start, end, short_name=SHORT_NAME, version=VERSION, title='.4km.'):
    import requests

    # El catálogo (CMR) es público y rechaza credenciales: se consulta sin ellas; solo la descarga las necesita.
    params = {'short_name': short_name, 'temporal': f'{start:%Y-%m-%d}T00:00:00Z,{end:%Y-%m-%d}T23:59:59Z', 'page_size': 50}
    if version:
        params['version'] = version
    r = requests.get(CMR, params=params, timeout=60)
    r.raise_for_status()
    out = []
    for e in r.json()['feed']['entry']:
        if title not in e['title']:
            continue
        href = next((l['href'] for l in e.get('links', []) if l['href'].endswith('.nc') and l['href'].startswith('https')), None)
        if href:
            out.append((e['time_start'][:10], href))
    return sorted(out)


def pace_kd(spots, end, days, box, warnings, product=PACE):
    """{spot_id: [{'date', 'kd', 'validFraction'}]} con la mediana de Kd490 alrededor de cada spot (`box` = medio lado).
    Con varios satélites (VIIRS), por día se queda la medida con más superficie sin nubes."""
    label, collections = product
    user, password = os.environ.get('EARTHDATA_USERNAME'), os.environ.get('EARTHDATA_PASSWORD')
    if not user or not password:
        warnings.append(f'{label}: faltan EARTHDATA_USERNAME y EARTHDATA_PASSWORD (se omite)')
        return {}
    import xarray as xr

    session = _session(user, password)
    best = {sid: {} for sid in spots}
    granules = []
    for short_name, version, title in collections:
        try:
            granules += _granules(end - timedelta(days=days), end, short_name, version, title)
        except Exception as e:  # noqa: BLE001
            warnings.append(f'{label}: no se pudo buscar {short_name} en el catálogo ({str(e)[:100]})')
    if not granules:
        return {}
    for date, url in granules:
        try:
            with tempfile.NamedTemporaryFile(suffix='.nc') as tmp:
                with session.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(1 << 20):
                        tmp.write(chunk)
                tmp.flush()
                with xr.open_dataset(tmp.name) as ds:
                    # v3.2: Kd hiperespectral (dimensión `wavelength`); se usa la banda más cercana a 490 nm.
                    kd = ds['Kd'].sel(wavelength=490, method='nearest') if 'Kd' in ds else ds['Kd_490']
                    for sid, (la, lo) in spots.items():
                        sub = kd.sel(lat=slice(la + box[0], la - box[0]), lon=slice(lo - box[1], lo + box[1]))  # lat descendente
                        vals = sub.values
                        valid = float(np.isfinite(vals).mean()) if vals.size else 0.0
                        kdv = float(np.nanmedian(vals)) if valid >= 0.2 else None
                        row = {'date': date, 'kd': None if kdv is None else round(kdv, 3), 'validFraction': round(valid, 2)}
                        if date not in best[sid] or row['validFraction'] > best[sid][date]['validFraction']:
                            best[sid][date] = row
        except Exception as e:  # noqa: BLE001
            warnings.append(f'{label} {date}: {str(e)[:120]}')
    days_ok = sorted({d for rows in best.values() for d in rows})
    print(f'  · {label}: {len(granules)} ficheros, {len(days_ok)} días (último {days_ok[-1] if days_ok else "ninguno"})')
    return {sid: [rows[d] for d in sorted(rows)] for sid, rows in best.items()}


if __name__ == '__main__':
    w = []
    print(pace_kd({'cala-reona': (37.6235, -0.6967)}, datetime.utcnow(), 3, (0.09, 0.11), w), w)
    print(pace_kd({'cala-reona': (37.6235, -0.6967)}, datetime.utcnow(), 3, (0.09, 0.11), w, VIIRS), w)
