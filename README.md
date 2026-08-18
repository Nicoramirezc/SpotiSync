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

En Windows puedes usar `run.vbs`

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

## ⚠️ Aviso Legal y Descargo de Responsabilidad (Disclaimer)

Este proyecto ha sido desarrollado con fines puramente **educativos y para uso estrictamente personal**. 

- **No a la piratería:** El autor de este proyecto no apoya, promueve, ni fomenta la piratería, la descarga ilegal de música ni la infracción de derechos de autor de ninguna forma.
- **Responsabilidad del usuario:** Al utilizar `Spotify Sync`, tú como usuario asumes la responsabilidad total e incondicional de tus acciones. Es tu obligación legal asegurarte de que tienes el derecho, la autorización o el amparo legal (como el derecho a la copia privada, según tu legislación local) para descargar y almacenar este contenido.
- **Términos de servicio (ToS):** Esta herramienta utiliza técnicas de scraping y herramientas de terceros (`yt-dlp`) para obtener audio de plataformas como YouTube o SoundCloud. El uso de esta herramienta puede violar los Términos de Servicio de Spotify, YouTube, SoundCloud u otras plataformas involucradas. Úsalo bajo tu propio riesgo.
- **Sin afiliación:** Este proyecto es independiente y no está afiliado, respaldado, ni patrocinado por Spotify AB, Google LLC (YouTube), SoundCloud Ltd., ni ninguna de sus empresas matrices o afiliadas.

## Limitaciones y notas técnicas

- Requiere que las playlists sean **públicas** (se leen vía scraping, no con la API oficial de Spotify ni credenciales de usuario).
- La descarga real del audio se hace vía YouTube (con SoundCloud como respaldo si YouTube falla), usando `yt-dlp`.

## Licencia

El **código fuente** de este proyecto se distribuye bajo la [Licencia MIT](LICENSE). 

El software se proporciona "tal cual" (AS IS), sin garantía de ningún tipo, explícita o implícita. Revisa el archivo `LICENSE` para más detalles.
