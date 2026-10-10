# Prueba de acceso VNC

`01-vnc-access.ipynb` muestra respuestas por comando y capturas completas del escritorio. Usa VNC directo: AsyncVNC interpreta RFB y un puente TCP de loopback transporta los bytes mediante WSS. No requiere Playwright, Chromium ni un modelo.

## Ejecutar

Desde la raíz del proyecto:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-notebook.txt
.venv/bin/python -m ipykernel install --user --name frontier-vnc --display-name "Frontier VNC"
```

Abrir el notebook en un editor Jupyter compatible y seleccionar el kernel Frontier VNC. Ejecutar las celdas en orden; la sesión se cierra automáticamente después de 180 segundos. Si vence mientras inspeccionas una imagen, conectar otra vez.

La configuración se carga desde `.env.vnc`, sin mostrar valores. Las acciones de ejemplo envían Escape, hacen un clic de navegación, mueven el puntero y envían Tab. Las coordenadas del ejemplo se obtuvieron de SIGECOM y deben revisarse en la captura actual; no son una estrategia general para el agente. Escape no cerró el menú observado: comprobar siempre el resultado visual.

Para ejecutar desde terminal y guardar una copia privada con todas las salidas:

```sh
.venv/bin/python -m ipykernel install --prefix .venv --name frontier-vnc --display-name "Frontier VNC"
.venv/bin/python scripts/run_vnc_notebook.py
```

El notebook demuestra el transporte y las herramientas disponibles para un modelo visual. No implementa aún decisiones autónomas, subagentes ni las tareas completas de evaluación.

## Publicación

Guardar resultados ejecutados en `.private/notebooks/`. El notebook de `notebooks/` debe permanecer sin outputs, ya que las imágenes pueden contener datos del escritorio. El verificador de archivos públicos bloquea notebooks con outputs. Las capturas se guardan en `.private/evidencias/`.

AsyncVNC se distribuye bajo GPL; revisar sus obligaciones al distribuir el software final. Fuente: https://github.com/barneygale/asyncvnc. Este prototipo sirve para validar compatibilidad antes de decidir la dependencia definitiva.
