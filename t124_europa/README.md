# T124 en Europa (10–11 nov 2026)

Lo habitual de la corriente (`hab`) y su confianza (`conf`) de los spots europeos con los mismos modelos regionales que
la previsión (MEDSEA, IBI, noroeste europeo, Báltico y mar Negro), para que el motor compare con lo habitual en todo el
mundo con un solo camino. Informe completo: `docs/T124-EUROPA.md` del repositorio de la app (rama `t124-europa`).

Orden (en local; los datos pesados van a /Volumes/MacExt/Divy-cache/T124/europa):
1. `habitual_europa.py --solo med` (y `ibi`, `nws`, `bal,blk`): un año de corriente a ~10 m en la celda de la previsión.
2. `combinar.py`: une los modelos (media de MEDSEA e IBI donde la app usa los dos).
3. `corr_europa.py`: confianza en la malla regional y `datos/corriente-spots.json` con los spots europeos sustituidos.
   Con el tope de Europa (p90 de lo habitual de 1,9 km/h como mucho, 17 spots de marea fuerte) y `celda` como confianza
   baja en las calas (Luka, 11 nov, tras la validación crítica).
   Y `rojo: 3.7` (2 nudos) en esos mismos spots (Luka, 11 nov, tras la revalidación): el motor de la 1.2 lo lee si
   existe. Quitar el campo (o `hab`) vuelve atrás solo con datos.
4. `medir.mjs`, `conf.mjs` y `anual.py`: efecto en el color (previsiones reales y un año del factor de corriente).
5. `medir-revalida.mjs` (dos motores con dos corr, Europa y resto del mundo, también la vuelta atrás) y
   `calma-revalida.mjs` (dos «tranquila» en el repunte): efecto de la calma que caduca y de `rojo`.

`datos/corriente-spots.json` lo publica `publicar_corriente.py` como siempre (solo con el OK de Luka). Orden: primero
estos datos (y comprobar en staging que `/api/catalogo/es`, `/med` y `/eu` traen el `hab` nuevo), después la API y la 1.2.
