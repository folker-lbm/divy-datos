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
"""
import os
import tempfile
from datetime import datetime, timedelta

import numpy as np

CMR = 'https://cmr.earthdata.nasa.gov/search/granules.json'
SHORT_NAME, VERSION = 'PACE_OCI_L3M_KD_NRT', '3.2'
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


def _granules(start, end):
    import requests

    # El catálogo (CMR) es público y rechaza credenciales: se consulta sin ellas; solo la descarga las necesita.
    r = requests.get(CMR, params={'short_name': SHORT_NAME, 'version': VERSION, 'temporal': f'{start:%Y-%m-%d}T00:00:00Z,{end:%Y-%m-%d}T23:59:59Z', 'page_size': 50}, timeout=60)
    r.raise_for_status()
    out = []
    for e in r.json()['feed']['entry']:
        if '.4km.' not in e['title']:
            continue
        href = next((l['href'] for l in e.get('links', []) if l['href'].endswith('.nc') and l['href'].startswith('https')), None)
        if href:
            out.append((e['time_start'][:10], href))
    return sorted(out)


def pace_kd(spots, end, days, box, warnings):
    """{spot_id: [{'date', 'kd', 'validFraction'}]} con la mediana de Kd490 alrededor de cada spot (`box` = medio lado)."""
    user, password = os.environ.get('EARTHDATA_USERNAME'), os.environ.get('EARTHDATA_PASSWORD')
    if not user or not password:
        warnings.append('NASA PACE: faltan EARTHDATA_USERNAME y EARTHDATA_PASSWORD (se omite)')
        return {}
    import xarray as xr

    session = _session(user, password)
    res = {sid: [] for sid in spots}
    try:
        granules = _granules(end - timedelta(days=days), end)
    except Exception as e:  # noqa: BLE001
        warnings.append(f'NASA PACE: no se pudo buscar en el catálogo ({str(e)[:100]})')
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
                        res[sid].append({'date': date, 'kd': None if kdv is None else round(kdv, 3), 'validFraction': round(valid, 2)})
        except Exception as e:  # noqa: BLE001
            warnings.append(f'NASA PACE {date}: {str(e)[:120]}')
    print(f'  · NASA PACE: {len(granules)} días')
    return res


if __name__ == '__main__':
    w = []
    print(pace_kd({'cala-reona': (37.6235, -0.6967)}, datetime.utcnow(), 3, (0.09, 0.11), w), w)
