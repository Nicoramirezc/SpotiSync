# Spotify Sync

Descarga y mantiene sincronizadas tus playlists de Spotify como archivos de
audio locales (MP3/M4A/FLAC/Opus), con portadas y metadatos correctos.
Detecta canciones nuevas, eliminadas y reordenadas automáticamente, en
segundo plano, con una interfaz de escritorio simple.

![Estado](https://img.shields.io/badge/plataforma-Windows%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)

## Características

- **Auto-Sync**: revisa tus playlists cada X segundos (configurable) y
  descarga lo nuevo, borra lo que quitaste de Spotify y reordena los
  archivos según el orden actual de la playlist.
- **Metadatos y portada correctos**: título, artista, álbum, número de
  pista y carátula se incrustan en el archivo de audio.
- **Multi-formato**: mp3, m4a, flac u opus.
- **Corrección de metadatos** y **detección de archivos faltantes/huérfanos**
  para playlists que ya tenías descargadas por fuera de la app.
- **Inicio con el sistema** (minimizado a la bandeja) tanto en Windows como
  en Linux.
- **Multiplataforma**: Windows y Linux (X11/GTK).

## Requisitos

- Python 3.9 o superior.
- [ffmpeg](https://ffmpeg.org/) instalado y accesible en el `PATH`.
- Las dependencias de `requirements.txt` (ver más abajo).

## Instalación

```bash
git clone https://github.com/tu-usuario/spotify-sync.git
cd spotify-sync
pip install -r requirements.txt
```

### ffmpeg

- **Windows**: `winget install Gyan.FFmpeg`, o descarga el `.exe` desde
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) y
  colócalo junto a `spotify_sync_gui.py`.
- **Linux (Debian/Ubuntu/Mint)**: `sudo apt install ffmpeg`
- **Linux (Arch)**: `sudo pacman -S ffmpeg`

### Extras de Linux (interfaz gráfica y bandeja del sistema)

```bash
# Debian/Ubuntu/Mint
sudo apt install python3-tk python3-pil.imagetk libappindicator3-1

# Arch
sudo pacman -S python tk libappindicator-gtk3
```

## Uso

```bash
python spotify_sync_gui.py
```

En Windows también puedes usar `run.bat` (doble clic) — a diferencia de
`run.vbs`, este sí muestra una consola, así que si algo falla verás el
error en pantalla en lugar de que la ventana simplemente no aparezca.

1. Clic en **+ Agregar**, pega la URL de una playlist pública de Spotify.
2. El Auto-Sync se activa solo al agregar la primera playlist.
3. Ajusta el intervalo de sincronización, el formato de salida y si quieres
   que se borren localmente las canciones que quites de la playlist, desde
   la sección **Sincronización**.
4. Activa **Iniciar con el sistema (minimizado)** si quieres que arranque
   solo, minimizado a la bandeja, cuando enciendas el equipo.

### Menú "Más"

- **Forzar descarga completa**: descarta el estado guardado de una playlist
  y vuelve a verificar todos sus archivos.
- **Corregir metadatos**: re-escribe título/artista/álbum/portada de los
  archivos ya descargados sin volver a descargarlos.
- **Ver faltantes / huérfanos**: lista canciones de la playlist sin archivo
  local, y archivos locales que ya no están en la playlist.
- **Actualizar yt-dlp**: actualiza yt-dlp a la última versión.

## ¿Por qué no abre al hacer doble clic en el `.py`?

Casi siempre es por una de estas dos razones:

1. **Falta una dependencia** (`pip install -r requirements.txt` no se
   corrió, o se corrió con un Python distinto al que usa el doble clic).
   Si te pasa, ahora la propia app te avisa con un cuadro de diálogo
   explicando qué falta — antes fallaba en silencio porque Windows suele
   abrir los `.py` con `pythonw.exe`, que no tiene consola donde mostrar
   el error.
2. **Windows asocia `.py` con `pythonw.exe`** en vez de `python.exe`, así
   que si hay un error que la app no logra atrapar, no ves nada.

**No necesitas compilar un `.exe` para que esto funcione.** Opciones, de
más simple a más "a prueba de todo":

- **Doble clic normal en `spotify_sync_gui.py`**: debería bastar si Python
  está instalado y las dependencias también. Es el caso normal.
- **`run.bat`** (incluido): abre una consola visible y hace `pause` si algo
  falla, para que puedas leer el error. Bueno para diagnosticar.
- **Revisa `spotify_sync.log`**: todo error queda registrado ahí, se vea o
  no en pantalla.
- **Empaquetarlo como `.exe`** (opcional, no obligatorio): útil solo si vas
  a compartir la app con alguien que no tiene Python instalado. Con
  [PyInstaller](https://pyinstaller.org/):

  ```bash
  pip install pyinstaller
  pyinstaller --onefile --windowed --name "SpotifySync" spotify_sync_gui.py
  ```

  El ejecutable queda en `dist/SpotifySync.exe`. Nota: `ffmpeg.exe` y
  `spotify_sync_config.json` no se empaquetan solos — cópialos junto al
  `.exe` en `dist/`, o ajusta las rutas si prefieres que sean fijas.

### ¿Y `run.vbs`?

`run.vbs` **no es para uso manual**. La app lo usa internamente solo para
la opción **"Iniciar con el sistema (minimizado)"**: crea una entrada en
el Registro de Windows que ejecuta
`wscript.exe run.vbs --minimized`, que a su vez lanza
`pythonw spotify_sync_gui.py --minimized` sin ninguna ventana de consola
(por eso usa `.vbs` y no llama a Python directo). Si no usas esa opción,
puedes ignorar `run.vbs` por completo.

## Estructura del proyecto

```
spotify_sync_gui.py       # App completa: extractor, motor de sync, GUI
requirements.txt          # Dependencias de Python
run.bat                   # Lanzador manual para Windows (con consola)
run.vbs                   # Lanzador silencioso, solo para autoinicio
spotify_sync_config.json  # Se crea solo: playlists y ajustes guardados
spotify_sync.log          # Se crea solo: registro completo de la app
.spotify_sync_state/      # Se crea solo: estado interno por playlist
```

## Variables de entorno (opcional)

La mayoría de los ajustes se controlan desde la interfaz, pero también se
pueden fijar por variable de entorno (útil para correr sin GUI/en servidor):

| Variable          | Default                | Descripción                                   |
|-------------------|-------------------------|------------------------------------------------|
| `OUTPUT_FORMAT`   | `mp3`                  | `mp3`, `m4a`, `flac` u `opus`                  |
| `SYNC_INTERVAL`   | `30`                   | Segundos entre revisiones del Auto-Sync        |
| `DELETE_REMOVED`  | `true`                 | Borra localmente lo quitado de la playlist     |
| `SKIP_EXPLICIT`   | `false`                | Omite canciones marcadas como explícitas       |
| `MIN_POPULARITY`  | `0`                    | Popularidad mínima (0-100) para descargar      |
| `BLOCKED_ARTISTS` | *(vacío)*               | Lista de artistas a excluir, separados por coma|
| `STATE_DIR`       | `.spotify_sync_state`  | Carpeta donde se guarda el estado interno      |

## Limitaciones y notas

- Requiere que las playlists sean **públicas** (se leen vía scraping, no
  con la API oficial de Spotify ni credenciales de usuario).
- La descarga real del audio se hace vía YouTube (con SoundCloud como
  respaldo si YouTube falla), usando `yt-dlp`. Úsalo bajo tu propia
  responsabilidad y respetando los términos de servicio de cada plataforma
  y el derecho de autor del contenido.
- Este proyecto no está afiliado a Spotify, YouTube ni SoundCloud.

## Licencia

Añade aquí la licencia que prefieras (por ejemplo, MIT) creando un archivo
`LICENSE` en la raíz del repositorio.
