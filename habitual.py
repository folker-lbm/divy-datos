"""
Corriente habitual de cada spot (T124, fase 2; 7 nov 2026): un año completo, hora a hora, de la misma corriente que
enseña la app fuera de Europa, para comparar cada hora con lo normal en ese spot. Sale de los datos, sin etiquetar
ningún spot a mano.

Qué corriente: la global de Copernicus a unos 10 m (GLOBAL_ANALYSISFORECAST_PHY_001_024, cada 6 h, interpolada a horas,
en la celda de mar más cercana, como pipeline.py) más la marea de SMOC (FES2014, cada hora, en la celda que elige
marea.py). Es lo que da `cGlo` con DIVY_SUMAR_MAREA.

Por qué un año: la marea se repite con ciclos de unos 15 días (vivas y muertas) y de un año (las vivas más fuertes, en
los equinoccios). Un año entero los recoge todos, también los cambios de estación de la corriente general; una semana o
la ventana de la previsión no sirven (una semana de vivas pondría lo «normal» muy arriba). La variación de la marea de
un año a otro (el ciclo de 18,6 años de la Luna) mueve la amplitud unos pocos puntos: se comprueba en estable.py de
docs/T124. No se usan las componentes armónicas de FES2014 porque SMOC no las publica y las de AVISO piden registro y
otra descarga; con un año de la serie horaria sale lo mismo para este fin.

Por spot, percentiles (50, 75, 90, 95 y 98) de `total` (general + marea), `marea` (sola) y `general` (sola), en km/h.
Se calcula una vez por spot (y al añadir spots): datos/corriente-spots.json.
"""
import numpy as np

from marea import DS_UV, _abrir

DS_GLO = 'cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i'
PCTS = (50, 75, 90, 95, 98)
VECINAS = 2
MIN_HORAS = 24 * 300  # al menos 300 días: nunca una semana ni la ventana de la previsión


def _sel(ds, t0, t1, depth_m=None):
    ds = ds.sel(time=slice(np.datetime64(t0.replace(tzinfo=None)), np.datetime64(t1.replace(tzinfo=None))))
    if 'elevation' in ds.dims:
        k = int(np.argmin(np.abs(ds.elevation.values + (depth_m or 0))))
        ds = ds.isel(elevation=k)
    if 'depth' in ds.dims:
        k = int(np.argmin(np.abs(ds.depth.values - (depth_m or 0))))
        ds = ds.isel(depth=k)
    return ds


def calcula(spots, celdas_marea, t0, t1, workers=16):
    """{sid: {'total': {p: kmh}, 'marea': {…}, 'general': {…}, 'horas': n, 'celdaGeneral': (i, j)}}.
    `spots`: {sid: (lat, lng)}; `celdas_marea`: {sid: (i, j)} de marea.py; t0, t1: datetime UTC."""
    import dask
    import xarray as xr

    uv = _sel(_abrir(DS_UV)[['utide', 'vtide']], t0, t1)
    glo = _sel(_abrir(DS_GLO)[['uo', 'vo']], t0, t1, depth_m=10)
    lat, lon = glo.latitude.values, glo.longitude.values
    dlat, dlon = float(lat[1] - lat[0]), float(lon[1] - lon[0])
    # Celda de mar a 10 m más cercana a cada spot (la misma regla que pipeline.py: la más cercana con dato).
    cerca = {}
    for sid, (la, lo) in spots.items():
        i0, j0 = int(round((la - lat[0]) / dlat)), int(round(((lo - lon[0]) % 360) / dlon)) % len(lon)
        cerca[sid] = [(i, j % len(lon)) for i in range(i0 - VECINAS, i0 + VECINAS + 1) if 0 <= i < len(lat) for j in range(j0 - VECINAS, j0 + VECINAS + 1)]
    todas = sorted({c for cs in cerca.values() for c in cs})
    with dask.config.set(scheduler='threads', num_workers=workers):
        m0 = glo.uo.isel(time=0).isel(latitude=xr.DataArray([c[0] for c in todas], dims='p'), longitude=xr.DataArray([c[1] for c in todas], dims='p')).values
    mar = {c for c, v in zip(todas, m0) if np.isfinite(v)}
    from marea import _km
    celda_g = {}
    for sid, cs in cerca.items():
        ok = [c for c in cs if c in mar]
        if ok:
            celda_g[sid] = min(ok, key=lambda c: _km(spots[sid], (float(lat[c[0]]), float(lon[c[1]]))))
    ug = sorted(set(celda_g.values()))
    um = sorted(set(celdas_marea.values()))
    with dask.config.set(scheduler='threads', num_workers=workers):
        g = glo.isel(latitude=xr.DataArray([c[0] for c in ug], dims='p'), longitude=xr.DataArray([c[1] for c in ug], dims='p'))
        GU, GV = g.uo.values.astype('float32'), g.vo.values.astype('float32')
        m = uv.isel(latitude=xr.DataArray([c[0] for c in um], dims='p'), longitude=xr.DataArray([c[1] for c in um], dims='p'))
        TU, TV = m.utide.values.astype('float32'), m.vtide.values.astype('float32')
    tg = g.time.values.astype('datetime64[s]').astype(np.int64)
    tm = m.time.values.astype('datetime64[s]').astype(np.int64)
    pg, pm = {c: k for k, c in enumerate(ug)}, {c: k for k, c in enumerate(um)}
    pc = lambda x: {p: round(float(np.percentile(x, p)), 2) for p in PCTS}  # noqa: E731
    out = {}
    for sid, cm in celdas_marea.items():
        tu, tv = TU[:, pm[cm]], TV[:, pm[cm]]
        if sid in celda_g:
            k = pg[celda_g[sid]]
            gu, gv = np.interp(tm, tg, GU[:, k]), np.interp(tm, tg, GV[:, k])  # 6 h → horas, como pipeline.py
        else:
            gu = gv = np.zeros_like(tu)
        tot, mr, ge = np.hypot(gu + tu, gv + tv) * 3.6, np.hypot(tu, tv) * 3.6, np.hypot(gu, gv) * 3.6
        ok = np.isfinite(tot) & np.isfinite(mr)
        if ok.sum() < MIN_HORAS:
            continue
        out[sid] = {'total': pc(tot[ok]), 'marea': pc(mr[ok]), 'general': pc(ge[ok]), 'horas': int(ok.sum()),
                    'celdaGeneral': celda_g.get(sid)}
    return out
