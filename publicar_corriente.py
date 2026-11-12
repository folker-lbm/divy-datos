"""T124: publica la corriente habitual y la confianza de cada spot (datos/corriente-spots.json) en la API de Divy, una
clave «corriente-<región>» por región. La API la une al catálogo de esa región («corr» en cada spot de /api/catalogo).

Uso:    python -I publicar_corriente.py                 (pruebas, divy-staging; así lo lanza el flujo Experimento)
        python -I publicar_corriente.py https://joindivy.com   (producción: solo con el OK de Luka)
        python -I publicar_corriente.py [base] --respaldo      (regla de respaldo, ver abajo)

Respaldo (arreglo 1 de T144, 12 nov): si la marea fuera de Europa no se puede encender (o hay que apagarla), lo habitual
de esos spots no vale, porque se calculó con la marea. Con --respaldo se publica sin «hab» (ni «rojo») en todos los
spots cuya corriente habitual no sale de un modelo regional europeo (los que no llevan «rojo»), y el motor de la 1.2
vuelve solo a la regla fija de siempre en ellos. Los europeos con modelo regional se quedan como están. Para deshacerlo,
se vuelve a publicar sin --respaldo.
Necesita DIVY_FORECAST_TOKEN. Deja un resumen en salida/corriente.json.

Solo se publica una región si su corriente ha cambiado (Luka, 9 nov): cada publicación cambia la fecha del catálogo y
la app vuelve a descargarlo. Lo habitual se recalcula una vez al año, no con cada previsión.
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# El flujo lo ejecuta con «python -I», que no mira la carpeta del script: pipeline.py está en la misma.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import api_request  # noqa: E402

STAGING = 'https://divy-staging.divy-api.workers.dev'
DATOS = Path(__file__).parent / 'datos/corriente-spots.json'


def respaldo(c):
    """Corriente de un spot para la regla de respaldo: sin lo habitual si no sale de un modelo regional europeo."""
    if 'rojo' in c:
        return c
    return {k: v for k, v in c.items() if k not in ('hab', 'rojo')}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    base = args[0] if args else STAGING
    corr = json.loads(DATOS.read_text())
    if '--respaldo' in sys.argv:
        corr = {sid: respaldo(c) for sid, c in corr.items()}
        print(f'Respaldo: sin lo habitual en {sum(1 for c in corr.values() if "hab" not in c)} spots; '
              f'con él (modelo regional europeo) en {sum(1 for c in corr.values() if "hab" in c)}')
    # La región de cada spot, como la tiene la API (la misma lista con la que trabaja el proceso de la previsión).
    region = {s['id']: s.get('region') or 'es' for s in json.loads(api_request(base, 'spots'))['spots']}
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    por_region = {}
    for sid, c in corr.items():
        if sid in region:
            # Solo lo que lee la app: sin motivos vacíos ni «marea» falsa, para que pese menos.
            d = {k: v for k, v in c.items() if v not in ([], False, None)}
            por_region.setdefault(region[sid], {})[sid] = d
    resumen = {'base': base, 'generatedAt': stamp, 'respaldo': '--respaldo' in sys.argv, 'regiones': {}}
    for r, spots in sorted(por_region.items()):
        body = json.dumps({'generatedAt': stamp, 'region': r, 'spots': spots}, ensure_ascii=False, separators=(',', ':')).encode()
        try:
            antes = json.loads(api_request(base, f'corriente-{r}')).get('spots')
        except Exception:  # noqa: BLE001 — aún no publicada
            antes = None
        if antes == spots:
            print(f'  · «corriente-{r}»: sin cambios, no se publica')
            resumen['regiones'][r] = {'corriente': len(spots), 'sinCambios': True}
            continue
        api_request(base, f'corriente-{r}', 'PUT', body)
        # Comprobación: el catálogo que descarga la app ya la lleva.
        req = urllib.request.Request(f'{base}/api/catalogo/{r}', headers={'User-Agent': 'divy-t124'})
        cat = json.loads(urllib.request.urlopen(req, timeout=120).read())
        con = sum(1 for s in cat.get('spots', []) if 'corr' in s)
        resumen['regiones'][r] = {'corriente': len(spots), 'kb': len(body) // 1024, 'catalogo': len(cat.get('spots', [])),
                                   'conCorr': con, 'fecha': cat.get('generatedAt')}
        print(f'  · «corriente-{r}»: {len(spots)} spots ({len(body) // 1024} KB); catálogo con corr: {con}/{len(cat.get("spots", []))}')
    sin = [sid for sid in corr if sid not in region]
    resumen['sinRegion'] = len(sin)
    print(f'{len(corr) - len(sin)} spots publicados; {len(sin)} sin región en la API')
    Path('salida').mkdir(exist_ok=True)
    Path('salida/corriente.json').write_text(json.dumps(resumen, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
