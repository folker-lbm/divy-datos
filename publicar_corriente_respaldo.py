"""Arreglo 1 de T144: publica la corriente con la regla de respaldo (publicar_corriente.py --respaldo) en pruebas.
El flujo Experimento solo acepta el nombre de un script, sin opciones: este lo lanza con --respaldo contra divy-staging.
Para producción se usa «python -I publicar_corriente.py https://joindivy.com --respaldo», con el OK de Luka.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publicar_corriente  # noqa: E402

if __name__ == '__main__':
    sys.argv = [sys.argv[0], publicar_corriente.STAGING, '--respaldo']
    publicar_corriente.main()
