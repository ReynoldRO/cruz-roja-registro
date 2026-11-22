# Cruz Roja — Registro móvil con acceso directo (versión de prueba)

En esta versión **el formulario se abre directamente sin escanear un QR**. Está diseñada para uso desde **celular** y conserva el panel administrativo con la lista completa de operarios, el historial de marcaciones, los turnos activos y el resumen de horas por mes.

## 1. Ejecutar desde Visual Studio Code (Windows)

1. Extraé el ZIP y abrí la carpeta **Registro_Cruz_Roja_Acceso_Directo** en Visual Studio Code.
2. Instalá Python 3.10 o superior, si todavía no lo tenés.
3. En la terminal de VS Code, una sola vez:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Ejecutá:

   ```powershell
   python main.py
   ```

   Alternativa: doble clic en `iniciar-windows.bat`, que configura la versión directa.

5. **Prueba desde la misma computadora:** abrí `http://localhost:8765`.
6. **Uso desde el celular:** conectá PC y teléfono al mismo Wi-Fi. Desde la terminal de Windows, ejecutá `ipconfig` y buscá la dirección IPv4 de la PC. Abrí en el navegador móvil `http://IP-DE-TU-PC:8765` (por ejemplo, `http://192.168.1.20:8765`, sustituyendo la IP). Si Windows pregunta si permite Python en redes privadas, aceptalo. No permitás acceso desde redes públicas.

**El acceso directo para móviles requiere que el teléfono esté en la misma red privada que el servidor.** Desde un navegador de escritorio ajeno al servidor se bloquea la marcación; `localhost` de la propia PC queda habilitado solo para desarrollo.

## 2. Registrar entrada y salida

1. Abrí la dirección del sistema desde el celular; el formulario aparece inmediatamente.
2. Ingresá código de operario, nombre y apellido. La hora del teléfono se muestra automáticamente.
3. El sistema consulta la base SQLite: si no hay turno abierto el botón dice **Registrar entrada**; si existe un turno abierto dice **Registrar salida**.
4. Guardá el movimiento. En la confirmación tocá **Volver al formulario** para un nuevo registro. No se permiten dos marcaciones con la misma sesión; cada regreso al inicio otorga una nueva sesión de prueba.

El sistema utiliza la marca UTC del **servidor** para calcular las horas, y guarda también la hora declarada por el celular para referencia. Los registros se comparten entre dispositivos de la misma red que acceden al servidor.

## 3. Panel administrativo

Abrí `http://localhost:8765/admin` desde la PC. La contraseña se muestra en la terminal al iniciar y se almacena en `instance/config.json` bajo `admin_password` (guardala privada).

- **Historial:** consulta de marcaciones y descarga de CSV.
- **Operarios:** lista completa de códigos, nombre, apellido y estado de turno.
- **Horas mensuales:** filtro por mes, total de horas de cada operario, recuento de turnos cerrados, búsqueda por código o nombre y detalle individual.
- **Descargas:** exportación CSV de historial y resumen mensual.

Las horas se reparten por meses en hora de Costa Rica (UTC−6), incluso si el turno cruza de un mes a otro. Los turnos que todavía están abiertos no se contabilizan hasta marcar la salida.

## 4. Conservar los registros al actualizar

Con el servidor detenido (`Ctrl + C`), respaldá toda la carpeta `instance/` de tu versión anterior y trasladala al proyecto nuevo **sin cambiar sus archivos**. Ahí están `marcaciones.sqlite3`, `config.json` y las sesiones. No arranques dos instancias del programa apuntando a la misma base SQLite simultáneamente.

## 5. Volver al flujo QR

Esta versión incluye `iniciar-con-qr-windows.bat`. Al usarlo, se desactiva el acceso directo (`MODO_PRUEBA=0`) y el formulario vuelve a requerir la ruta de escaneo del QR. Para generar el QR, entrá a `/admin` y configurá la IP local o un sitio web HTTPS accesible por los teléfonos.

## 6. Animaciones y mascota

El formulario incorpora efectos GSAP, una mascota de respaldo animada por CSS y un visor de Spline 3D que necesita Internet. La escena de Spline es una demostración, no una mascota propia de Cruz Roja; sustituila por una escena autorizada si se utilizará institucionalmente. Podés fijar una URL de escena propia en la variable `SPLINE_URL`.

## 7. Seguridad (obligatorio antes de uso institucional)

**Este es un entorno de demostración, NO para uso oficial ni Internet público.** Al omitir el QR, cualquiera en la misma red privada que tenga el enlace puede intentar registrar movimientos utilizando un código. El bloqueo por tipo de navegador no constituye un control de identidad. Antes de desplegarlo a personal real se necesitan autenticación individual, HTTPS, control institucional de acceso y presencia, registros de auditoría, respaldos y protección de datos. Mantener `MODO_PRUEBA=0` en producción.

## 8. Pruebas del proyecto

```powershell
python -m unittest discover -s tests -v
```

Las pruebas incluyen acceso directo sin QR, registro de entrada y salida, bloqueo de doble marcación, panel administrativo, exportación CSV y cálculo de horas por mes.

## 9. Publicación de demo en Render

Consultá [`GUIA_PUBLICAR_EN_RENDER.md`](GUIA_PUBLICAR_EN_RENDER.md). Este ZIP incluye `render.yaml` y una opción **MODO_DEMO_WEB=1** para probar el formulario desde celulares por Internet **sin QR**. Solo demo, nunca para personal real.
