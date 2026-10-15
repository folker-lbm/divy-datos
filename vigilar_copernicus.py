"""
Vigilancia de los productos de Copernicus que usa el proceso (15 oct; fecha fija: el 12 de enero de 2027 se retiran
varias versiones). El proceso no fija versiones: pide siempre la más reciente, así que si Copernicus publica la nueva
con el mismo identificador, el cambio es automático. Este control avisa si a algún producto le quedan menos de
AVISO_DIAS para retirarse sin otra versión que lo sustituya (o si ha desaparecido): la ejecución falla y GitHub manda
un correo. Uso: python -I vigilar_copernicus.py
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import copernicusmarine as cm

AVISO_DIAS = 60
HERE = Path(__file__).resolve().parent
FUENTES = ('pipeline.py', 'pacioos.py', 'boyas.py')


def used_ids():
    text = ''.join((HERE / f).read_text() for f in FUENTES if (HERE / f).exists())
    return sorted(set(re.findall(r"cmems_[A-Za-z0-9_.-]+", text)))


def main():
    limit = datetime.now(timezone.utc) + timedelta(days=AVISO_DIAS)
    problems = []
    for ds_id in used_ids():
        try:
            cat = cm.describe(dataset_id=ds_id, disable_progress_bar=True)
            versions = [v for p in cat.products for d in p.datasets if d.dataset_id == ds_id for v in d.versions]
        except Exception as e:  # noqa: BLE001
            problems.append(f'{ds_id}: no se pudo consultar ({str(e)[:80]})')
            continue
        if not versions:
            problems.append(f'{ds_id}: ya no existe en el catálogo')
            continue

        def alive(v):
            dates = [getattr(pt, 'retired_date', None) for pt in v.parts]
            return any(d is None or datetime.fromisoformat(str(d).replace('Z', '+00:00')) > limit for d in dates)

        if not any(alive(v) for v in versions):
            ends = sorted({str(getattr(pt, 'retired_date', ''))[:10] for v in versions for pt in v.parts})
            problems.append(f'{ds_id}: se retira el {", ".join(ends)} y Copernicus aún no ha publicado otra versión')
        print(f'  · {ds_id}: {", ".join(v.label for v in versions)}')
    if problems:
        print('\nATENCIÓN · productos de Copernicus a punto de retirarse:\n  ' + '\n  '.join(problems))
        sys.exit(1)
    print('\nTodo bien: ningún producto se retira en los próximos', AVISO_DIAS, 'días sin sustituto.')


if __name__ == '__main__':
    main()
