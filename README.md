# Divy · proceso de datos marinos

Previsión de oleaje, viento, corriente, temperatura y visibilidad para los spots de buceo de [Divy](https://joindivy.com). Repositorio público para ejecutarlo en GitHub Actions; la app y la API están en un repositorio privado (`folker-lbm/divy`), que lo incluye como submódulo en `herramientas/datos/`.

- **Spots:** sin la app al lado, `pipeline.py` pide la lista (id y coordenadas) a la API de Divy. La sube el repositorio de la app cuando cambia el catálogo (`--subir-spots`).
- **Secretos del repositorio:** `COPERNICUSMARINE_SERVICE_USERNAME`, `COPERNICUSMARINE_SERVICE_PASSWORD`, `DIVY_FORECAST_TOKEN`, `EARTHDATA_USERNAME` y `EARTHDATA_PASSWORD`. Nunca en el código.
- **Lanzamiento:** el temporizador del Worker de Divy lo lanza a su hora (`workflow_dispatch`); las tareas programadas de GitHub quedan de respaldo.


- **`pipeline.py`:** genera la previsión real que usa la app para todos los spots del catálogo y la publica en la API (`/api/prevision`). Desde la versión 0.8 se ejecuta sola en GitHub Actions (`.github/workflows/prevision.yml`): modo `completo` (Copernicus + Open-Meteo, una vez al día, unos 25 min) y modo `open-meteo` (cada 6 h, recombina con la última parte de Copernicus). Uso local: `.venv/bin/python -I pipeline.py --modo completo [--publicar http://localhost:8787]` (con `DIVY_FORECAST_TOKEN` igual al `FORECAST_TOKEN` de `api/.dev.vars`). Sale a `salida/prevision.json` y `salida/copernicus.json`. Ver `VERSION-0.4.md`, `VERSION-0.5.md` y `VERSION-0.8.md`.
- **Satélite (3 nov):** se piden los últimos 7 días (`SAT_DIAS`, los mismos que acepta la app para la visibilidad). Fuentes por orden: alta resolución de Copernicus (100 m), Copernicus OLCI o multisensor, NASA PACE y, de relleno, los tres VIIRS de la NASA y la NOAA (`pace.py`, misma cuenta de Earthdata). Vigilancia: si el último día publicado de un producto tiene más de 3 días (`SAT_PARADO_DIAS`), la ejecución completa publica igual, deja el aviso y termina en rojo para que GitHub avise. Origen: el 30 oct Copernicus dejó de publicar el color del océano global y del Atlántico (incidencia OC-1116) y nadie se enteró.
- **Marea (T124, en prueba; 7 nov):** `marea.py` descarga la marea de Copernicus SMOC (FES2014) de todos los spots y calcula pleamar, bajamar, repuntes, sentido y vivas o muertas. Se activa con `DIVY_MAREA=1`; con `DIVY_SUMAR_MAREA=1`, `cGlo` pasa a ser la corriente global más la marea. Apagado por defecto: no se publica sin el OK de Luka. `datos/corriente-spots.json` guarda, por spot, la confianza calculada de la corriente (`geometria.py`, `propuesta.py`) y lo habitual de un año (`habitual.py`). Informe: `docs/T124-MAREA-FASE2.md` del repositorio de la app.
- **Entorno:** `requirements.txt` (Python 3.9+).
- **El resto:** pruebas de fuentes para `INVESTIGACION-DATOS.md` (`corrientes.py`, `visibilidad.py`, `backtest_visibilidad.py`, `perfil.py`).

- `.venv/`: entorno de Python con `copernicusmarine` (y `xarray`, `netCDF4`, `h5netcdf`).
- Credenciales: las guarda Luka con `.venv/bin/copernicusmarine login` en `~/.copernicusmarine/`. Nunca se copian aquí.
- `perfil.py`: perfil vertical de temperatura y gradiente máximo (termoclina) a partir de un NetCDF del modelo MEDSEA 3D.
- `salida/`: NetCDF descargados el 13 de octubre de 2026 (temperatura 3D, temperatura de superficie observada y Kd490 de Cabo de Palos).
- `med_phy.json`: descripción del producto MEDSEA_ANALYSISFORECAST_PHY_006_013.

Ejemplo (temperatura 3D, 72 h):

```bash
.venv/bin/copernicusmarine subset --dataset-id cmems_mod_med_phy-tem_anfc_4.2km-3D_PT1H-m --variable thetao \
  --minimum-longitude -0.70 --maximum-longitude -0.62 --minimum-latitude 37.61 --maximum-latitude 37.69 \
  --minimum-depth 1 --maximum-depth 60 --start-datetime 2026-10-13T00:00:00 --end-datetime 2026-10-16T00:00:00 \
  --output-directory salida --output-filename cabo_palos_temp_3d.nc
.venv/bin/python -I perfil.py salida/cabo_palos_temp_3d.nc
```

Aviso del servicio: la versión `202511` de varios datasets se retira el 12 de enero de 2027. El pipeline pide la versión por defecto (la más reciente), así que solo hay que intervenir si cambian los identificadores: revisar en noviembre de 2026, cuando Copernicus publique las nuevas.
