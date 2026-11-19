"""Lanza publicar_corriente.py contra producción desde el flujo Experimento, que no admite argumentos.

Uso (solo con el OK de Luka): gh workflow run experimento.yml -R folker-lbm/divy-datos -f script=publicar_corriente_produccion.py
"""
import runpy
import sys
from pathlib import Path

sys.argv = ['publicar_corriente.py', 'https://joindivy.com']
runpy.run_path(str(Path(__file__).resolve().parent / 'publicar_corriente.py'), run_name='__main__')
