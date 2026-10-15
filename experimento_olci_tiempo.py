"""
Experimento (15 oct): ¿cuánto tarda leer la clorofila OLCI 300 m global spot a spot abriendo el producto una sola vez?
Decide si se puede usar en el proceso (2.800 spots fuera de Europa). Uso: python -I experimento_olci_tiempo.py
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pipeline import SAT_BOX, kind, spots_from_catalog  # noqa: E402

import copernicusmarine as cm  # noqa: E402

now = datetime.now(timezone.utc)
spots = [(s, p) for s, p in spots_from_catalog('https://joindivy.com').items() if kind(p) == 'glo'][:100]
t0 = time.time()
ds = cm.open_dataset(dataset_id='cmems_obs-oc_glo_bgc-plankton_nrt_l3-olci-300m_P1D', variables=['CHL'],
                     start_datetime=(now - timedelta(days=4)).strftime('%Y-%m-%dT00:00:00'), end_datetime=now.strftime('%Y-%m-%dT00:00:00'))
t_open = time.time() - t0
chl = ds.CHL
got = 0
t1 = time.time()
for sid, (la, lo) in spots:
    box = chl.sel(latitude=slice(la + SAT_BOX[0], la - SAT_BOX[0]), longitude=slice(lo - SAT_BOX[1], lo + SAT_BOX[1]))
    if box.latitude.size == 0:
        box = chl.sel(latitude=slice(la - SAT_BOX[0], la + SAT_BOX[0]), longitude=slice(lo - SAT_BOX[1], lo + SAT_BOX[1]))
    v = box.load().values
    got += int(np.isfinite(v).any())
per = (time.time() - t1) / len(spots)
out = {'abrir s': round(t_open, 1), 'por spot s': round(per, 2), 'spots': len(spots), 'con dato': got, 'estimado 2800 spots min': round(per * 2800 / 60, 1)}
(HERE / 'salida').mkdir(exist_ok=True)
(HERE / 'salida' / 'experimento_olci_tiempo.json').write_text(json.dumps(out, indent=1))
print(out)
