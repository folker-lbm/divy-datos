"""
Lectura directa de Copernicus Marine, sin su servidor de inicio de sesión (14 oct).

El 13–14 de octubre el servidor de acceso de Copernicus (auth.marine.copernicus.eu) dejó de responder durante más de
14 horas, mientras los datos seguían publicándose con normalidad. Los datos están en su almacenamiento en la nube
(formato ARCO/Zarr), de lectura pública; el catálogo también es público. Si el acceso normal falla, el proceso lee de ahí
los mismos productos, para que la previsión no dependa de ese servidor.

`open_arco` imita lo que usamos de `copernicusmarine.open_dataset`: variables, zona, fechas y rango de profundidad.
"""
from collections.abc import MutableMapping
from functools import lru_cache

import numpy as np
import requests
import xarray as xr
import zarr


class _HttpStore(MutableMapping):
    """Almacén Zarr de solo lectura por HTTPS (sin dependencias asíncronas)."""

    def __init__(self, url):
        self.url = url.rstrip('/')
        self.session = requests.Session()

    def __getitem__(self, key):
        import time

        for attempt in range(4):
            try:
                r = self.session.get(f'{self.url}/{key}', timeout=120)
                if r.status_code in (403, 404):
                    raise KeyError(key)
                r.raise_for_status()
                return r.content
            except KeyError:
                raise
            except Exception:  # noqa: BLE001 — fallo de red puntual: se reintenta
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __setitem__(self, key, value):
        raise NotImplementedError

    def __delitem__(self, key):
        raise NotImplementedError


@lru_cache(maxsize=None)
def _arco_url(dataset_id, part=None, service='arco-geo-series'):
    """URL del Zarr del dataset, desde el catálogo público. Por defecto, el guardado «por tiempo» (cada hora, el mapa
    entero: adecuado para recortar zonas); con `service='arco-time-series'`, el guardado «por puntos» (muchas horas de
    pocas celdas: adecuado para series largas en unos pocos puntos, como la marea de cada spot, marea.py)."""
    import copernicusmarine as cm

    info = cm.describe(dataset_id=dataset_id, disable_progress_bar=True)
    for product in info.products:
        for ds in product.datasets:
            for p in ds.versions[0].parts:
                if part and p.name != part:
                    continue
                for s in p.services:
                    if getattr(s.service_name, 'value', s.service_name) == service:
                        return s.uri
    raise RuntimeError(f'Sin ARCO para {dataset_id}')


def _slice(coord, lo, hi):
    """Corte en el orden de la coordenada (ascendente o descendente)."""
    return slice(lo, hi) if coord.values[0] <= coord.values[-1] else slice(hi, lo)


def open_arco(dataset_id, variables, minimum_latitude, maximum_latitude, minimum_longitude, maximum_longitude,
              start_datetime=None, end_datetime=None, minimum_depth=None, maximum_depth=None, dataset_part=None):
    ds = xr.open_zarr(zarr.storage.KVStore(_HttpStore(_arco_url(dataset_id, dataset_part))), consolidated=True)
    ds = ds[variables]
    ds = ds.sel(latitude=_slice(ds.latitude, minimum_latitude, maximum_latitude),
                longitude=_slice(ds.longitude, minimum_longitude, maximum_longitude))
    if start_datetime or end_datetime:
        ds = ds.sel(time=slice(np.datetime64(start_datetime) if start_datetime else None, np.datetime64(end_datetime) if end_datetime else None))
    if 'elevation' in ds.coords:
        # ARCO guarda la profundidad como elevación negativa; se devuelve como `depth` positiva, igual que el toolbox.
        ds = ds.assign_coords(depth=-ds.elevation).swap_dims({'elevation': 'depth'}).drop_vars('elevation').sortby('depth')
    if 'depth' in ds.dims and (minimum_depth is not None or maximum_depth is not None):
        ds = ds.sel(depth=slice(minimum_depth if minimum_depth is not None else 0, maximum_depth if maximum_depth is not None else 1e5))
    return ds
