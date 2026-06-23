# 🎵 Spotify Sync GUI

Aplicación de escritorio (Windows) que descarga y mantiene sincronizadas tus playlists de Spotify de forma automática, usando YouTube como fuente de audio. Incluye interfaz gráfica, sincronización periódica en segundo plano, icono en la bandeja del sistema e inicio automático con Windows.

> ⚠️ **Aviso legal:** esta herramienta extrae metadatos de Spotify sin usar su API oficial (vía `spotifyscraper`) y descarga el audio desde YouTube (vía `yt-dlp`). Úsala únicamente para contenido del que tengas derecho a hacer copias personales, y respeta los Términos de Servicio de Spotify/YouTube y las leyes de derechos de autor de tu país. Este proyecto es de uso personal/educativo.

---

## ✨ Características

- **Auto-Sync automático**: al agregar una playlist se activa la sincronización automática de inmediato, sin pasos extra.
- **Sincronización periódica** configurable (por defecto cada **30 segundos**).
- **Detecta cambios reales**: agrega canciones nuevas, elimina las que ya no están en la playlist (opcional) y repara archivos faltantes.
- **Icono en la bandeja del sistema**: al cerrar (✕) o minimizar (−) la ventana, la app sigue funcionando en segundo plano desde la bandeja. La única forma de cerrarla del todo es con "Salir" desde el menú de la bandeja.
- **Doble clic en el icono de la bandeja** vuelve a abrir la ventana.
- **Inicio automático con Windows**, arrancando minimizado directo en la bandeja.
- **Interfaz clara**: estado de cada playlist (OK / desfase / error / nuevo), progreso de descarga en vivo, y todos los ajustes agrupados y explicados.

---

## 📋 Requisitos

### Sistema operativo
- **Windows 10/11** (recomendado). Las funciones de bandeja del sistema e inicio automático usan el registro de Windows (`winreg`) y `wscript.exe`, por lo que esas dos funciones **no están disponibles en Linux/macOS** (el resto de la app sí podría adaptarse, pero no está pensada para eso).

### Python
- **Python 3.8 o superior** (recomendado 3.10+).
- Verifica tu versión con:
  ```bash
  python --version
  ```
- Asegúrate de que **Tkinter** esté incluido (en Windows viene incluido por defecto con el instalador oficial de [python.org](https://www.python.org/downloads/); marca la casilla "tcl/tk and IDLE" si usas instalación personalizada).

### FFmpeg (obligatorio, fuera de pip)
`yt-dlp` necesita **FFmpeg** instalado y disponible en el `PATH` del sistema para extraer y convertir el audio (mp3/m4a/flac/opus). Sin FFmpeg, las descargas fallarán silenciosamente.

1. Descarga un build para Windows desde [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (build "essentials" es suficiente).
2. Descomprime el .zip en, por ejemplo, `C:\ffmpeg`.
3. Agrega `C:\ffmpeg\bin` a la variable de entorno `PATH`.
4. Verifica con:
   ```bash
   ffmpeg -version
   ```

### Dependencias de Python (pip)

| Paquete | Para qué se usa |
|---|---|
| `requests` | Peticiones HTTP auxiliares |
| `spotifyscraper` | Extraer metadatos de playlists de Spotify sin necesitar API key |
| `yt-dlp` | Buscar y descargar el audio desde YouTube |
| `pystray` | Icono y menú en la bandeja del sistema |
| `Pillow` | Generar el icono de la bandeja |

Instálalas todas de una vez con el `requirements.txt` incluido:

```bash
pip install -r requirements.txt
```

O manualmente:

```bash
pip install requests spotifyscraper yt-dlp pystray Pillow
```

> 💡 Si `pip` no es reconocido, usa `python -m pip install ...`. Si tienes varias versiones de Python instaladas, usa `py -3 -m pip install ...` para asegurarte de instalar en la versión correcta.

---

## 📁 Estructura del proyecto

```
spotify-sync/
├── spotify_sync_gui.py        # Aplicación principal (GUI)
├── run.vbs                    # Lanzador silencioso (sin consola visible)
├── requirements.txt           # Dependencias de Python
├── spotify_sync_config.json   # Se crea automáticamente (playlists + ajustes)
└── .spotify_sync_state/       # Se crea automáticamente (estado interno por playlist)
```

Los dos últimos se generan solos la primera vez que usas la app — no los crees a mano.

---

## 🚀 Instalación y primer uso

1. Instala Python, FFmpeg y las dependencias de pip (pasos de arriba).
2. Coloca `spotify_sync_gui.py` y `run.vbs` juntos en la carpeta donde quieras que vivan tu configuración y tus descargas.
3. Ejecuta la app de cualquiera de estas formas:
   - Doble clic en `run.vbs` (no abre ventana de consola), **o**
   - Desde una terminal: `python spotify_sync_gui.py`
4. En la ventana, haz clic en **➕ Agregar Playlist**, pega la URL de Spotify (formato `https://open.spotify.com/playlist/...`), confirma el nombre y la carpeta de destino.
5. Listo — el Auto-Sync se activa solo y empieza a descargar.

---

## ⚙️ Ajustes disponibles

| Ajuste | Default | Descripción |
|---|---|---|
| Intervalo | **30 s** | Cada cuánto se revisan cambios en las playlists |
| Formato | mp3 | Formato de audio final (mp3, m4a, flac, opus) |
| Eliminar canciones quitadas | Activado | Si una canción se quita de la playlist en Spotify, borra también el archivo local |
| Iniciar con Windows (minimizado) | Desactivado | Agrega la app al inicio de Windows, arrancando directo en la bandeja |

Todos los cambios se guardan automáticamente en `spotify_sync_config.json`, no hace falta reiniciar el Auto-Sync para que tomen efecto.

---

## 🖥️ Comportamiento de la ventana y la bandeja

- **✕ (cerrar)** → minimiza a la bandeja (no cierra la app).
- **− (minimizar)** → también manda la app a la bandeja en vez de dejarla en la barra de tareas.
- **Doble clic en el icono de la bandeja** → vuelve a abrir la ventana.
- **Clic derecho en el icono de la bandeja** → menú con "Abrir", "Sincronizar ahora", "Iniciar/Detener Auto-Sync" y **"Salir"**.
- La **única forma de cerrar el programa por completo** es "Salir" desde ese menú.

---

## 🔁 Inicio automático con Windows

Al activar la casilla "Iniciar con Windows (minimizado)":
- Se crea una entrada en `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` que lanza `run.vbs --minimized` en cada arranque de sesión.
- La app abre directo en la bandeja, sin mostrar ventana.
- Si el Auto-Sync estaba activo, se reanuda solo.
- Si más adelante mueves o renombras la carpeta del proyecto, la app **detecta y repara la ruta automáticamente** la próxima vez que la abras manualmente (no necesitas tocar el registro a mano).

---

## 🛠️ Solución de problemas

**"No se pudo agregar la playlist" / error al extraer datos de Spotify**
- Verifica que la URL sea pública y tenga el formato `https://open.spotify.com/playlist/ID`.
- Reinstala/actualiza `spotifyscraper`: `pip install -U spotifyscraper` (Spotify cambia su web player de vez en cuando y la librería se actualiza para seguirle el paso).

**Las descargas fallan o quedan en 0 bytes**
- Casi siempre es que falta **FFmpeg** en el `PATH`. Verifica con `ffmpeg -version` en una terminal nueva.
- Actualiza `yt-dlp` (YouTube cambia su sitio seguido y rompe versiones viejas): `pip install -U yt-dlp`.

**"Faltan dependencias" al abrir la app**
- Vuelve a correr `pip install -r requirements.txt`. Si usas varias versiones de Python, confirma que estás instalando en la misma que usas para ejecutar el script (`python -m pip install ...`).

**El icono de la bandeja no aparece / error de pystray**
- Instala/actualiza: `pip install -U pystray Pillow`.
- En algunos antivirus muy estrictos, `pystray` puede ser marcado por error; agrega una excepción si es necesario.

**El inicio automático con Windows dejó de funcionar tras mover la carpeta**
- Abre la app una vez manualmente (doble clic) — se repara sola la ruta guardada en el registro. Como alternativa, desmarca y vuelve a marcar la casilla "Iniciar con Windows".

**Windows Defender / SmartScreen marca `run.vbs` o el script**
- Es un falso positivo común con scripts `.vbs` y ejecutables de Python sin firmar. Agrega una excepción si confías en el origen del archivo.

---

## 🔒 Privacidad

Toda la información (playlists agregadas, estado de sincronización, configuración) se guarda **localmente** en tu propia carpeta (`spotify_sync_config.json` y `.spotify_sync_state/`). La app no envía datos a ningún servidor propio; solo se comunica con Spotify (para leer metadatos públicos de la playlist) y YouTube (para buscar y descargar el audio).
