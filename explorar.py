"""Exploración de fuentes nuevas (FUENTES-DATOS.md): turbidez HR 100 m y boyas In Situ de Copernicus.
Solo imprime estructura y unos valores de ejemplo; no publica nada. Se ejecuta en GitHub Actions (explorar-datos.yml)."""
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import copernicusmarine as cm

now = datetime.now(timezone.utc)
t0 = (now - timedelta(days=5)).strftime('%Y-%m-%dT00:00:00')
t1 = now.strftime('%Y-%m-%dT%H:00:00')
SPOTS = {'cala-reona': (37.6235, -0.6967), 'islas-hormigas': (39.1023, 1.5907), 'illa-de-ons': (42.38, -8.93)}


def step(title, fn):
    print(f'\n=== {title}')
    try:
        fn()
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def hr():
    for ds_id, bbox in [('cmems_obs_oc_med_bgc_tur-spm-chl_nrt_l3-hr-mosaic_P1D-m', (37, 40, -1.5, 2)),
                        ('cmems_obs_oc_ibi_bgc_tur-spm-chl_nrt_l3-hr-mosaic_P1D-m', (42, 43, -9.5, -8.5))]:
        ds = cm.open_dataset(dataset_id=ds_id, variables=['TUR'], minimum_latitude=bbox[0], maximum_latitude=bbox[1],
                             minimum_longitude=bbox[2], maximum_longitude=bbox[3], start_datetime=t0, end_datetime=t1)
        print(ds_id, dict(ds.sizes), str(ds.time.values[:3]))
        for sid, (la, lo) in SPOTS.items():
            if not (bbox[0] <= la <= bbox[1] and bbox[2] <= lo <= bbox[3]):
                continue
            box = ds.TUR.sel(latitude=slice(la - 0.02, la + 0.02), longitude=slice(lo - 0.025, lo + 0.025))
            if box.latitude.size == 0:
                box = ds.TUR.sel(latitude=slice(la + 0.02, la - 0.02), longitude=slice(lo - 0.025, lo + 0.025))
            v = box.load()
            for k, t in enumerate(v.time.values):
                a = v.isel(time=k).values
                valid = float(np.isfinite(a).mean()) if a.size else 0
                print(' ', sid, str(t)[:10], a.shape, round(valid, 2), round(float(np.nanmedian(a)), 2) if valid > 0 else None)


def insitu():
    for part in ['latest']:
        ds = cm.open_dataset(dataset_id='cmems_obs-ins_glo_phybgcwav_mynrt_na_irr', dataset_part=part,
                             minimum_latitude=35.5, maximum_latitude=44, minimum_longitude=-10, maximum_longitude=4.5,
                             start_datetime=(now - timedelta(days=2)).strftime('%Y-%m-%dT00:00:00'), end_datetime=t1)
        print(part, dict(ds.sizes))
        print('vars', list(ds.data_vars)[:60])
        print('coords', list(ds.coords))
        print(ds)


def pace():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pace import pace_kd
    w = []
    print(pace_kd(SPOTS, now, 4, (0.09, 0.11), w), w)


def insitu_arco():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from arco import _arco_url, _HttpStore
    import xarray as xr, zarr
    for part in ['latest']:
        url = _arco_url('cmems_obs-ins_glo_phybgcwav_mynrt_na_irr', part)
        print(part, url)
        ds = xr.open_zarr(zarr.storage.KVStore(_HttpStore(url)), consolidated=True)
        print(dict(ds.sizes)); print(list(ds.coords)); print(list(ds.data_vars)[:80])


step('NASA PACE', pace)
step('In Situ global (boyas) por ARCO', insitu_arco)
