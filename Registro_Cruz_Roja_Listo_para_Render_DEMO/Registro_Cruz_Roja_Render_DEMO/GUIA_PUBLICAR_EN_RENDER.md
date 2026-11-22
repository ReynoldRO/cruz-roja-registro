# Publicar la demo móvil de Cruz Roja en Render

**Solo para mostrar/probar con datos inventados.** El enlace público sin QR no verifica a la persona ni su ubicación. Alguien con el enlace puede enviar marcaciones bajo el código de otra persona. No es un sistema listo para uso oficial.

## Método rápido: GitHub + Render Blueprint

1. Creá un repositorio **privado** vacío en GitHub (por ejemplo `cruz-roja-registro-demo`).
2. Descomprimí este ZIP. **Subí el contenido de la carpeta** al repositorio, no el ZIP ni la carpeta `instance` con archivos privados. `main.py`, `requirements.txt` y `render.yaml` deben estar en la raíz del repositorio.
3. Entrá a https://dashboard.render.com → **New → Blueprint**; vinculá tu cuenta de GitHub y elegí el repositorio. Revisá y aprobá la creación del servicio de prueba.
4. Render usa `render.yaml` para instalar dependencias y ejecutar `python main.py`. Cuando termine, te mostrará una URL HTTPS similar a `https://registro-cruz-roja-demo.onrender.com` (la tuya puede ser diferente).
5. Abrí el enlace desde un **celular** para ver directamente el formulario. Desde una computadora abrí `https://TU-SITIO.onrender.com/admin` para administración.
6. En Render > tu servicio > **Environment**, consultá el valor generado para `ADMIN_PASSWORD`; también se muestra al arrancar en Logs. No compartás esa contraseña.

## Método manual: New → Web Service

Si no usás Blueprint, configurá un servicio **Python 3** desde GitHub:

- Build Command: `pip install -r requirements.txt`
- Start Command: `python main.py`
- Instance: Free, solo para demo
- Env vars: `MODO_PRUEBA=0`, `MODO_DEMO_WEB=1`, `COOKIE_SECURE=1`
- Definí `ADMIN_PASSWORD` y `COOKIE_SECRET` con valores secretos fuertes en la sección Environment; **no los agregués al código ni a GitHub**.
- No hace falta configurar `PORT`: Render lo proporciona y la aplicación lo lee.

## Aviso fundamental sobre base de datos

**Render Free usa archivos temporales**: la base SQLite (`instance/marcaciones.sqlite3`), los operarios y el historial pueden desaparecer en reinicios, inactividad y despliegues. NO lo usés para contabilizar horas laborales reales.

Para guardar datos permanentemente podés elegir **Web Service pagado + Persistent Disk** y establecer:

- Disk mount path: `/opt/render/project/src/storage`
- Environment variable: `INSTANCE_DIR=/opt/render/project/src/storage`

Además, debés hacer respaldos periódicos de ese disco. Alternativamente se puede migrar de SQLite a una base de datos administrada y persistente, pero requiere cambios de código.

## Advertencia de seguridad

La demostración usa un enlace abierto y un código de operario escrito libremente. **No hay autenticación individual**: la detección del celular por User-Agent no es una garantía. Antes de utilizarlo realmente para Cruz Roja necesitás autenticación de personal, permisos, auditoría, backups, HTTPS, revisión de seguridad y privacidad, y un método confiable para acreditar presencia en el lugar. Para volver al QR deshabilitá `MODO_DEMO_WEB` (`0`) y habilitá el flujo QR; el QR por sí solo tampoco garantiza la identidad.

## Comprobar localmente

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

El modo local mantiene acceso directo mediante `MODO_PRUEBA=1` (valor por defecto). Si ejecutás esta versión local usando las variables de Render, solo celulares podrán abrir el formulario directamente.
