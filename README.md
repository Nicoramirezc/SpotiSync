# 🎵 Spotify Sync GUI

Aplicación de escritorio multiplataforma (Windows & Linux) que descarga y mantiene sincronizadas tus playlists de Spotify de forma automática, usando YouTube como fuente de audio. Incluye interfaz gráfica, sincronización periódica en segundo plano, icono en la bandeja del sistema e inicio automático con el sistema operativo.

> ⚠️ **Aviso legal:** esta herramienta extrae metadatos de Spotify sin usar su API oficial (vía `spotifyscraper`) y descarga el audio desde YouTube (vía `yt-dlp`). Úsala únicamente para contenido del que tengas derecho a hacer copias personales, y respeta los Términos de Servicio de Spotify/YouTube y las leyes de derechos de autor de tu país. Este proyecto es de uso personal/educativo.

---

## ✨ Características

- **Auto-Sync automático**: al agregar una playlist se activa la sincronización automática de inmediato, sin pasos extra.
- **Sincronización periódica** configurable (por defecto cada **30 segundos**).
- **Detecta cambios reales**: agrega canciones nuevas, elimina las que ya no están en la playlist (opcional) y repara archivos faltantes.
- **Icono en la bandeja del sistema**: al cerrar (✕) o minimizar (−) la ventana, la app sigue funcionando en segundo plano desde la bandeja. La única forma de cerrarla del todo es con "Salir" desde el menú de la bandeja.
- **Doble clic en el icono de la bandeja** vuelve a abrir la ventana.
- **Inicio automático con el sistema**, arrancando minimizado directo en la bandeja.
  - **Windows**: entrada en el registro (`HKCU\...\Run`) usando `run.vbs`.
  - **Linux**: archivo `.desktop` en `~/.config/autostart/`.
- **Interfaz clara**: estado de cada playlist (OK / desfase / error / nuevo), progreso de descarga en vivo, y todos los ajustes agrupados y explicados.
- **Multiplataforma**: funciona en Windows 10/11 y cualquier distribución Linux moderna (Arch, Ubuntu, Mint, Fedora, etc.).

---

## 📋 Requisitos

### Sistema operativo
- **Windows 10/11**
- **Linux** (Arch, Ubuntu, Mint, Fedora, Debian, Manjaro, etc.)

> **macOS**: no está soportado oficialmente, pero la mayor parte del código podría funcionar con pequeños ajustes (no se incluye inicio automático ni bandeja nativa).

### Python
- **Python 3.8 o superior** (recomendado 3.10+).
- Verifica tu versión con:
  ```bash
  python --version
  # o en Linux
  python3 --version
  ```

### FFmpeg (obligatorio, fuera de pip)
`yt-dlp` necesita **FFmpeg** instalado y disponible en el `PATH` para extraer y convertir el audio (mp3/m4a/flac/opus). Sin FFmpeg, las descargas fallarán silenciosamente.

#### Windows
1. Descarga un build desde [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (build "essentials" es suficiente).
2. Descomprime el `.zip` en, por ejemplo, `C:\ffmpeg`.
3. Agrega `C:\ffmpeg\bin` a la variable de entorno `PATH`.
4. Verifica con:
   ```bash
   ffmpeg -version
   ```

#### Linux
```bash
# Debian / Ubuntu / Mint
sudo apt update && sudo apt install ffmpeg

# Arch / Manjaro
sudo pacman -S ffmpeg

# Fedora
sudo dnf install ffmpeg
```
Verifica con:
```bash
ffmpeg -version
```

---

## 📦 Instalación de dependencias

### 1. Clona el repositorio

```bash
git clone https://github.com/Nicoramirezc/SpotiSync.git
cd SpotiSync
```

### 2. Dependencias del sistema

#### Windows
- **Tkinter** viene incluido por defecto con el instalador oficial de [python.org](https://www.python.org/downloads/). Si usas instalación personalizada, marca la casilla **"tcl/tk and IDLE"**.
- No se necesitan dependencias adicionales del sistema.

#### Linux
```bash
# Debian / Ubuntu / Mint
sudo apt install python3-tk python3-pip libappindicator3-1

# Arch / Manjaro
sudo pacman -S python tk libappindicator-gtk3

# Fedora
sudo dnf install python3-tkinter libappindicator-gtk3
```
> `libappindicator` es necesario para que el icono de la bandeja del sistema funcione correctamente en entornos GTK (GNOME, XFCE, Cinnamon, etc.).

### 3. Dependencias de Python (pip)

Instálalas todas de una vez con el `requirements.txt` incluido:

```bash
pip install -r requirements.txt
```

O manualmente:

```bash
pip install requests spotifyscraper yt-dlp pystray Pillow
```

> 💡 En Linux, si `pip` no está disponible, usa `python3 -m pip install -r requirements.txt`.

| Paquete | Para qué se usa |
|---|---|
| `requests` | Peticiones HTTP auxiliares |
| `spotifyscraper` | Extraer metadatos de playlists de Spotify sin necesitar API key |
| `yt-dlp` | Buscar y descargar el audio desde YouTube |
| `pystray` | Icono y menú en la bandeja del sistema |
| `Pillow` | Generar el icono de la bandeja |

---

## 📁 Estructura del proyecto

```
SpotiSync/
├── spotify_sync_gui.py        # Aplicación principal (GUI) — multiplataforma
├── run.vbs                    # Lanzador silencioso para Windows (sin consola visible)
├── requirements.txt           # Dependencias de Python
├── spotify_sync_config.json   # Se crea automáticamente (playlists + ajustes)
└── .spotify_sync_state/       # Se crea automáticamente (estado interno por playlist)
```

Los dos últimos se generan solos la primera vez que usas la app — no los crees a mano.

> **Nota para Linux:** `run.vbs` no se usa en Linux. El inicio automático se gestiona mediante un archivo `.desktop` generado dinámicamente en `~/.config/autostart/`.

---

## 🚀 Primer uso

### Windows
1. Asegúrate de tener Python, FFmpeg y las dependencias de pip instalados.
2. Ejecuta la app de cualquiera de estas formas:
   - Doble clic en `run.vbs` (no abre ventana de consola), **o**
   - Desde una terminal: `python spotify_sync_gui.py`
3. Haz clic en **➕ Agregar Playlist**, pega la URL de Spotify (`https://open.spotify.com/playlist/...`), confirma el nombre y la carpeta de destino.
4. Listo — el Auto-Sync se activa solo y empieza a descargar.

### Linux
1. Asegúrate de tener Python, FFmpeg, Tkinter, `libappindicator` y las dependencias de pip instalados.
2. Ejecuta desde la terminal:
   ```bash
   python3 spotify_sync_gui.py
   ```
3. Haz clic en **➕ Agregar Playlist**, pega la URL de Spotify, confirma el nombre y la carpeta de destino.
4. El Auto-Sync se activa automáticamente.

> **Tip:** en Linux también puedes lanzarla minimizada directamente:
> ```bash
> python3 spotify_sync_gui.py --minimized
> ```

---

## ⚙️ Ajustes disponibles

| Ajuste | Default | Descripción |
|---|---|---|
| Intervalo | **30 s** | Cada cuánto se revisan cambios en las playlists |
| Formato | mp3 | Formato de audio final (mp3, m4a, flac, opus) |
| Eliminar canciones quitadas | Activado | Si una canción se quita de la playlist en Spotify, borra también el archivo local |
| Iniciar con el sistema (minimizado) | Desactivado | Agrega la app al inicio del sistema, arrancando directo en la bandeja |

Todos los cambios se guardan automáticamente en `spotify_sync_config.json`, no hace falta reiniciar el Auto-Sync para que tomen efecto.

---

## 🖥️ Comportamiento de la ventana y la bandeja

- **✕ (cerrar)** → minimiza a la bandeja (no cierra la app).
- **− (minimizar)** → también manda la app a la bandeja en vez de dejarla en la barra de tareas.
- **Doble clic en el icono de la bandeja** → vuelve a abrir la ventana.
- **Clic derecho en el icono de la bandeja** → menú con "Abrir", "Sincronizar ahora", "Iniciar/Detener Auto-Sync" y **"Salir"**.
- La **única forma de cerrar el programa por completo** es "Salir" desde ese menú.

> En Linux, si el icono de la bandeja no aparece, asegúrate de tener instalado `libappindicator3-1` (Debian/Ubuntu) o `libappindicator-gtk3` (Arch).

---

## 🔁 Inicio automático con el sistema

### Windows
Al activar la casilla "Iniciar con Windows (minimizado)":
- Se crea una entrada en `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` que lanza `run.vbs --minimized` en cada arranque de sesión.
- La app abre directo en la bandeja, sin mostrar ventana.
- Si el Auto-Sync estaba activo, se reanuda solo.
- Si más adelante mueves o renombras la carpeta del proyecto, la app **detecta y repara la ruta automáticamente** la próxima vez que la abras manualmente.

### Linux
Al activar la casilla "Iniciar con el sistema (minimizado)":
- Se crea un archivo `SpotifySync.desktop` en `~/.config/autostart/`.
- El escritorio (GNOME, KDE, XFCE, etc.) lo ejecutará automáticamente al iniciar sesión.
- La app arranca con `--minimized`, directo en la bandeja.
- Si mueves el proyecto a otra carpeta, la app **repara la ruta** automáticamente al abrirla manualmente la siguiente vez.

---

## 🛠️ Solución de problemas

### "No se pudo agregar la playlist" / error al extraer datos de Spotify
- Verifica que la URL sea pública y tenga el formato `https://open.spotify.com/playlist/ID`.
- Reinstala/actualiza `spotifyscraper`: `pip install -U spotifyscraper` (Spotify cambia su web player de vez en cuando y la librería se actualiza para seguirle el paso).

### Las descargas fallan o quedan en 0 bytes
- Casi siempre es que falta **FFmpeg** en el `PATH`. Verifica con `ffmpeg -version` en una terminal nueva.
- Actualiza `yt-dlp` (YouTube cambia su sitio seguido y rompe versiones viejas): `pip install -U yt-dlp`.

### "Faltan dependencias" al abrir la app
- Vuelve a correr `pip install -r requirements.txt`. Si usas varias versiones de Python, confirma que estás instalando en la misma que usas para ejecutar el script.

### El icono de la bandeja no aparece / error de pystray
- Instala/actualiza: `pip install -U pystray Pillow`.
- **En Linux:** instala `libappindicator3-1` (Debian/Ubuntu) o `libappindicator-gtk3` (Arch). En algunos entornos Wayland muy restrictivos, el icono de la bandeja puede necesitar una extensión adicional (como *AppIndicator and KStatusNotifierItem Support* en GNOME).
- En algunos antivirus muy estrictos (Windows), `pystray` puede ser marcado por error; agrega una excepción si es necesario.

### El inicio automático dejó de funcionar tras mover la carpeta
- Abre la app una vez manualmente — se repara sola la ruta guardada. Como alternativa, desmarca y vuelve a marcar la casilla "Iniciar con el sistema".

### Windows Defender / SmartScreen marca `run.vbs` o el script
- Es un falso positivo común con scripts `.vbs` y ejecutables de Python sin firmar. Agrega una excepción si confías en el origen del archivo.

### En Linux la ventana se ve mal / fuentes extrañas
- Asegúrate de tener instaladas las fuentes del sistema (en Arch: `ttf-ms-fonts` o `ttf-liberation`; en Ubuntu vienen por defecto).
- Prueba cambiar el tema de ttk con `clam` si el tema por defecto de tu distribución no renderiza bien:
  ```python
  # En spotify_sync_gui.py, dentro de _setup_style(), ya está configurado
  # para probar "vista" primero y caer a "clam" automáticamente.
  ```

---

## 🔒 Privacidad

Toda la información (playlists agregadas, estado de sincronización, configuración) se guarda **localmente** en tu propia carpeta (`spotify_sync_config.json` y `.spotify_sync_state/`). La app no envía datos a ningún servidor propio; solo se comunica con Spotify (para leer metadatos públicos de la playlist) y YouTube (para buscar y descargar el audio).

---

## 🤝 Contribuciones

¿Encontraste un bug o quieres mejorar el soporte para otra distribución Linux? Abre un *issue* o un *pull request*. Toda ayuda es bienvenida.
