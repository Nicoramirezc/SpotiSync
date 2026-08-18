#!/usr/bin/env python3
"""
Spotify Sync GUI - Cross-platform (Windows & Linux)
- Auto-Sync se activa automáticamente al agregar una playlist
- Intervalo por defecto: 30s
- Opción de inicio automático con el sistema (minimizado a la bandeja)
- UI mejorada: más clara, agrupada y con indicadores de estado
- Código optimizado: evita errores 403 de YouTube, corrige portadas
  y metadatos, y mejora el rendimiento general.

Requiere: pip install requests spotifyscraper yt-dlp pystray Pillow mutagen (+ ffmpeg en el PATH)

Linux extra:
    sudo apt install python3-tk python3-pil.imagetk libappindicator3-1   (Debian/Ubuntu/Mint)
    sudo pacman -S python tk libappindicator-gtk3                        (Arch)
"""

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import List, Optional, Dict, Any, Callable, Tuple
import importlib.util


def _fatal_missing_deps(missing: list):
    """Si falta una dependencia obligatoria (requests/Pillow), un doble clic
    en el .py normalmente abre con pythonw.exe: sin consola, así que el
    ModuleNotFoundError no se ve en ningún lado y la ventana simplemente
    nunca aparece. Esto muestra un diálogo nativo de Tk (que sí es parte de
    la librería estándar) explicando qué falta y cómo instalarlo, en vez de
    morir en silencio."""
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Spotify Sync — Faltan dependencias",
            "No se pudo iniciar porque falta instalar:\n\n"
            f"  {', '.join(missing)}\n\n"
            "Abre una terminal en esta carpeta y ejecuta:\n\n"
            f"    pip install -r requirements.txt\n\n"
            "(o: pip install " + " ".join(missing) + ")\n\n"
            "Si ya lo instalaste, revisa que sea el mismo Python con el que "
            "abres este archivo (puede haber varias instalaciones)."
        )
        root.destroy()
    except Exception:
        pass  # Ni siquiera Tk está disponible: no hay forma de avisar en GUI.
    sys.exit(1)


try:
    import requests
    from PIL import Image, ImageDraw, ImageTk
except ImportError as _e:
    _fatal_missing_deps(["requests", "Pillow"])

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = Path(__file__).resolve().parent / "spotify_sync.log"
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("spotify_sync")

# ---------------------------------------------------------------------------
# Configuración global
# ---------------------------------------------------------------------------
PYSTRAY_AVAILABLE = (
    importlib.util.find_spec("pystray") is not None
    and importlib.util.find_spec("PIL") is not None
)
MUTAGEN_AVAILABLE = importlib.util.find_spec("mutagen") is not None

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
CONFIG_FILE = str(Path(__file__).resolve().parent / "spotify_sync_config.json")
APP_STARTUP_NAME = "SpotifySync"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# Funciones de inicio automático (Windows / Linux)
# ---------------------------------------------------------------------------
def _run_vbs_path() -> Path:
    return Path(__file__).resolve().parent / "run.vbs"


def _desktop_entry_path() -> Path:
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    return autostart_dir / f"{APP_STARTUP_NAME}.desktop"


def is_startup_enabled() -> bool:
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_READ) as key:
                try:
                    winreg.QueryValueEx(key, APP_STARTUP_NAME)
                    return True
                except FileNotFoundError:
                    return False
        except Exception:
            return False
    elif IS_LINUX:
        return _desktop_entry_path().exists()
    return False


def get_startup_command() -> Optional[str]:
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_READ) as key:
                try:
                    value, _ = winreg.QueryValueEx(key, APP_STARTUP_NAME)
                    return value
                except FileNotFoundError:
                    return None
        except Exception:
            return None
    elif IS_LINUX:
        path = _desktop_entry_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("Exec="):
                            return line.strip()[5:]
            except Exception:
                pass
        return None
    return None


def set_startup_enabled(enable: bool) -> bool:
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\Windows\CurrentVersion\Run",
                                  0, winreg.KEY_ALL_ACCESS)
            with key:
                if enable:
                    vbs = _run_vbs_path()
                    cmd = f'wscript.exe "{vbs}" --minimized'
                    winreg.SetValueEx(key, APP_STARTUP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_STARTUP_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except Exception:
            return False
    elif IS_LINUX:
        desktop_path = _desktop_entry_path()
        try:
            if enable:
                script_path = Path(__file__).resolve()
                entry = f"""[Desktop Entry]
Type=Application
Name=Spotify Sync
Exec=python3 "{script_path}" --minimized
Icon=multimedia-player
Comment=Spotify playlist sync tool
Terminal=false
X-GNOME-Autostart-enabled=true
"""
                with open(desktop_path, "w", encoding="utf-8") as f:
                    f.write(entry)
                os.chmod(desktop_path, 0o644)
            else:
                if desktop_path.exists():
                    desktop_path.unlink()
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Clase ToolTip
# ---------------------------------------------------------------------------
class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        try:
            self.tip.wm_attributes("-topmost", True)
        except Exception:
            pass
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip, text=self.text, background="#262626", foreground="#FFFFFF",
            relief="solid", borderwidth=0, padx=8, pady=4, font=("Segoe UI", 9)
        ).pack()

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------------------
# Configuración de la aplicación
# ---------------------------------------------------------------------------
class Config:
    OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "mp3")
    AUDIO_QUALITY = os.getenv("AUDIO_QUALITY", "0")   # 0 = mejor calidad VBR
    THREADS = int(os.getenv("THREADS", "4"))
    SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "30"))
    DELETE_REMOVED = os.getenv("DELETE_REMOVED", "true").lower() == "true"
    SKIP_EXISTING = os.getenv("SKIP_EXISTING", "true").lower() == "true"
    SKIP_EXPLICIT = os.getenv("SKIP_EXPLICIT", "false").lower() == "true"
    MIN_POPULARITY = int(os.getenv("MIN_POPULARITY", "0"))
    BLOCKED_ARTISTS = [a.strip().lower() for a in os.getenv("BLOCKED_ARTISTS", "").split(",") if a.strip()]
    STATE_DIR = os.getenv("STATE_DIR", ".spotify_sync_state")
    YTDLP_EXTRA = os.getenv("YTDLP_EXTRA", "")


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------
@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    explicit: bool
    popularity: int
    spotify_url: str = ""
    album_artist: str = ""
    track_number: int = 0
    disc_number: int = 1
    release_date: str = ""
    cover_url: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.name} - {', '.join(self.artists)}"

    @property
    def legacy_display_name(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"

    @staticmethod
    def _sanitize(name: str) -> str:
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        return name.strip('. ')[:120]

    @property
    def safe_filename(self) -> str:
        return self._sanitize(self.name) or self.id

    @property
    def legacy_safe_filename(self) -> str:
        return self._sanitize(self.display_name)

    @property
    def legacy_safe_filename_v0(self) -> str:
        return self._sanitize(self.legacy_display_name)

    @property
    def search_query(self) -> str:
        return f"{self.legacy_display_name} official audio"


@dataclass
class Playlist:
    id: str
    name: str
    owner: str
    tracks: List[Track]
    total_tracks: int
    spotify_url: str
    snapshot_id: str = ""
    cover_url: str = ""


# ---------------------------------------------------------------------------
# Extractor de Spotify
# ---------------------------------------------------------------------------
class SpotifyExtractor:
    def __init__(self):
        self._has_scraper = self._check_scraper()

    @staticmethod
    def _check_scraper() -> bool:
        try:
            import spotify_scraper
            return True
        except ImportError:
            return False

    def _get_attr(self, d, obj, attr, default=None):
        if obj is not None and hasattr(obj, attr):
            return getattr(obj, attr, default)
        if isinstance(d, dict) and attr in d:
            return d.get(attr, default)
        return default

    def _extract_cover_url(self, source) -> str:
        if source is None:
            return ''
        images = self._get_attr(source, None, 'images', None)
        if images is None and isinstance(source, dict):
            images = source.get('images')
        if images:
            try:
                best = None
                best_w = -1
                for img in images:
                    url = img.get('url') if isinstance(img, dict) else getattr(img, 'url', None)
                    width = img.get('width') if isinstance(img, dict) else getattr(img, 'width', 0)
                    width = width or 0
                    if url and width >= best_w:
                        best, best_w = url, width
                if best:
                    return best
            except Exception:
                pass
        for attr in ('cover_url', 'image', 'cover', 'thumbnail'):
            val = self._get_attr(source, None, attr, None)
            if isinstance(val, str) and val:
                return val
        return ''

    def get_playlist(self, playlist_id: str) -> Playlist:
        if not self._has_scraper:
            raise RuntimeError(f"No se pudo extraer la playlist {playlist_id}. Instala spotifyscraper: pip install spotifyscraper")

        try:
            from spotify_scraper import SpotifyClient
            with SpotifyClient() as client:
                pl = client.get_playlist(playlist_id, max_tracks=10000)
                pl_dict = pl.to_dict() if hasattr(pl, 'to_dict') else pl

                name = self._get_attr(pl_dict, pl, 'name', 'Playlist')
                playlist_cover = self._extract_cover_url(pl_dict) or self._extract_cover_url(pl)
                owner = ''
                if hasattr(pl, 'owner') and pl.owner:
                    owner = self._get_attr(pl.owner, None, 'name', '') or self._get_attr(pl.owner, None, 'display_name', '')
                elif isinstance(pl_dict, dict) and 'owner' in pl_dict:
                    owner = pl_dict['owner'].get('name', '')

                tracks_data = []
                if hasattr(pl, 'tracks') and pl.tracks:
                    tracks_data = pl.tracks
                elif isinstance(pl_dict, dict) and 'tracks' in pl_dict:
                    tracks_data = pl_dict['tracks']

                tracks = []
                for i, pt in enumerate(tracks_data):
                    try:
                        track_dict, track_obj = self._extract_track_data(pt)
                        track_id = self._get_attr(track_dict, track_obj, 'id', '') or f"track_{i}"
                        track_name = self._get_attr(track_dict, track_obj, 'name', 'Unknown')
                        artists = self._extract_artists(self._get_attr(track_dict, track_obj, 'artists', []))
                        album, cover_url, release_date = self._extract_album_info(track_dict, track_obj)
                        track_number = self._get_attr(track_dict, track_obj, 'track_number', 0) or 0
                        disc_number = self._get_attr(track_dict, track_obj, 'disc_number', 1) or 1
                        duration_ms = self._get_attr(track_dict, track_obj, 'duration_ms', 0) or 0
                        explicit = bool(self._get_attr(track_dict, track_obj, 'explicit', False))
                        popularity = self._get_attr(track_dict, track_obj, 'popularity', 50) or 50

                        tracks.append(Track(
                            id=track_id,
                            name=track_name,
                            artists=artists if artists else ["Unknown Artist"],
                            album=album,
                            duration_ms=duration_ms,
                            explicit=explicit,
                            popularity=popularity,
                            album_artist=artists[0] if artists else "",
                            track_number=int(track_number) if str(track_number).isdigit() else 0,
                            disc_number=int(disc_number) if str(disc_number).isdigit() else 1,
                            release_date=str(release_date or ''),
                            cover_url=cover_url,
                            spotify_url=f"https://open.spotify.com/track/{track_id}"
                        ))
                    except Exception:
                        continue

                if not playlist_cover and tracks:
                    playlist_cover = tracks[0].cover_url

                return Playlist(
                    id=playlist_id,
                    name=name,
                    owner=owner,
                    tracks=tracks,
                    total_tracks=len(tracks),
                    spotify_url=f"https://open.spotify.com/playlist/{playlist_id}",
                    snapshot_id=self._get_attr(pl_dict, pl, 'snapshot_id', f"scraper_{int(time.time())}"),
                    cover_url=playlist_cover,
                )
        except Exception as e:
            logger.exception("Error extrayendo playlist %s", playlist_id)
            raise RuntimeError(f"Error extrayendo playlist {playlist_id}: {e}")

    def _extract_track_data(self, pt) -> Tuple[Optional[dict], Optional[object]]:
        if hasattr(pt, 'track') and pt.track:
            track_obj = pt.track
            track_dict = track_obj.to_dict() if hasattr(track_obj, 'to_dict') else track_obj
            return track_dict, track_obj
        elif isinstance(pt, dict) and 'track' in pt:
            return pt['track'], None
        else:
            return pt.to_dict() if hasattr(pt, 'to_dict') else pt, pt

    def _extract_artists(self, artists_data) -> List[str]:
        artists = []
        if artists_data:
            for a in artists_data:
                if hasattr(a, 'name'):
                    artists.append(a.name)
                elif isinstance(a, dict):
                    artists.append(a.get('name', 'Unknown'))
                elif isinstance(a, str):
                    artists.append(a)
        return artists

    def _extract_album_info(self, track_dict, track_obj) -> Tuple[str, str, str]:
        album = ''
        cover_url = ''
        release_date = ''
        album_data = self._get_attr(track_dict, track_obj, 'album', None)
        if album_data:
            if hasattr(album_data, 'name'):
                album = album_data.name
            elif isinstance(album_data, dict):
                album = album_data.get('name', '')
            cover_url = self._extract_cover_url(album_data)
            release_date = self._get_attr(album_data, None, 'release_date', '') or ''
        # Si no hay portada del álbum, intentar con el track
        if not cover_url:
            cover_url = self._extract_cover_url(track_dict) or self._extract_cover_url(track_obj)
        return album, cover_url, release_date

    @staticmethod
    def extract_playlist_id(url: str) -> str:
        for p in [
            r"spotify:playlist:([a-zA-Z0-9]+)",
            r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)",
            r"spotify\.com/playlist/([a-zA-Z0-9]+)",
        ]:
            m = re.search(p, url)
            if m:
                return m.group(1)
        raise ValueError(f"URL inválida: {url}")


# ---------------------------------------------------------------------------
# Utilidades de normalización y puntuación
# ---------------------------------------------------------------------------
def _normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_MISMATCH_KEYWORDS = [
    "live", "en vivo", "cover", "reaction", "karaoke", "8d audio",
    "nightcore", "sped up", "slowed", "type beat", "tutorial",
    "instrumental", "remix", "parody", "parodia", "reversed",
]
_OFFICIAL_CHANNEL_HINTS = ["topic", "vevo", "official"]


def _score_youtube_candidate(candidate: dict, track: Track) -> float:
    title = candidate.get("title") or ""
    channel = (candidate.get("channel") or candidate.get("uploader") or "")
    duration = candidate.get("duration")

    norm_title = _normalize_text(title)
    norm_track_name = _normalize_text(track.name)

    score = SequenceMatcher(None, norm_title, norm_track_name).ratio() * 0.5

    primary_artist = (track.artists[0] if track.artists else "").lower()
    if primary_artist:
        if primary_artist in title.lower():
            score += 0.15
        if primary_artist in channel.lower():
            score += 0.15

    if any(hint in channel.lower() for hint in _OFFICIAL_CHANNEL_HINTS):
        score += 0.15

    if duration and track.duration_ms:
        target_s = track.duration_ms / 1000.0
        diff = abs(duration - target_s)
        if diff <= 3:
            score += 0.3
        elif diff <= 8:
            score += 0.15
        elif diff <= 15:
            score += 0.0
        else:
            score -= 0.4

    title_lower = title.lower()
    track_name_lower = track.name.lower()
    for kw in _MISMATCH_KEYWORDS:
        if kw in title_lower and kw not in track_name_lower:
            score -= 0.35
            break

    return score


# ---------------------------------------------------------------------------
# Descargador
# ---------------------------------------------------------------------------
class Downloader:
    ALWAYS_JUNK_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".webp",
        ".part", ".ytdl", ".temp", ".tmp",
    }
    RAW_CONTAINER_EXTENSIONS = {
        ".webm", ".m4a", ".opus", ".ogg", ".aac", ".wav", ".flac", ".mp4", ".mkv", ".3gp"
    }
    AUDIO_EXTENSIONS = [".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"]
    MIN_MATCH_SCORE = 0.35

    def __init__(self, output_dir: Path, config: Config, error_cb: Optional[Callable[[str], None]] = None):
        self.output_dir = output_dir
        self.config = config
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._error_cb = error_cb
        self._name_registry: Dict[str, str] = {}
        self._cover_cache: Dict[str, Optional[bytes]] = {}

    def _junk_extensions(self) -> set:
        final_ext = f".{self.config.OUTPUT_FORMAT.lower().strip()}"
        return self.ALWAYS_JUNK_EXTENSIONS | (self.RAW_CONTAINER_EXTENSIONS - {final_ext})

    def _cookies_args(self) -> List[str]:
        cookies = Path(__file__).resolve().parent / "cookies.txt"
        if cookies.exists():
            return ["--cookies", str(cookies)]
        return []

    def _notify_error(self, stderr_bytes: bytes):
        if self._error_cb is None:
            return
        try:
            text = stderr_bytes.decode("utf-8", errors="ignore").strip()
            last_line = text.splitlines()[-1] if text else "yt-dlp error"
            if len(last_line) > 120:
                last_line = last_line[-120:]
            self._error_cb(last_line)
        except Exception:
            pass

    def _run_ytdlp(self, args: List[str], timeout: int = 300) -> Tuple[bool, bytes, bytes]:
        cmd = ["yt-dlp"] + args
        kwargs = {
            "capture_output": True,
            "timeout": timeout,
        }
        if IS_WINDOWS:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            result = subprocess.run(cmd, **kwargs)
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            logger.exception("Error ejecutando yt-dlp: %s", e)
            return False, b"", str(e).encode()

    def _search_best(self, track: Track, search_prefix: str, query: str, count: int = 5) -> Optional[dict]:
        if search_prefix.startswith("yt"):
            args = ["--flat-playlist", "-J", "--no-warnings", "--quiet", "--socket-timeout", "15",
                    *self._cookies_args(), f"{search_prefix}{count}:{query}"]
        else:
            args = ["--flat-playlist", "-J", "--no-warnings", "--quiet", "--socket-timeout", "15",
                    f"{search_prefix}{count}:{query}"]

        ok, stdout, _ = self._run_ytdlp(args, timeout=60)
        if not ok or not stdout:
            return None
        try:
            data = json.loads(stdout.decode("utf-8", errors="ignore"))
            entries = data.get("entries") or []
            if not entries:
                return None
            scored = [(_score_youtube_candidate(e, track), e) for e in entries if e]
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best = scored[0]
            logger.info(
                "Búsqueda '%s' (%s): mejor candidato '%s' [canal=%s dur=%s] score=%.2f",
                query, search_prefix, best.get("title"), best.get("channel") or best.get("uploader"),
                best.get("duration"), best_score,
            )
            if best_score < self.MIN_MATCH_SCORE:
                return None
            return best
        except Exception:
            logger.exception("Error parseando búsqueda para '%s'", track.display_name)
            return None

    def _download_cover(self, url: str) -> Optional[bytes]:
        """Descarga y procesa la portada: la convierte a JPEG cuadrado de
        tamaño moderado (500x500) para evitar estiramientos y reducir peso."""
        if not url:
            return None
        if url in self._cover_cache:
            return self._cover_cache[url]
        data = None
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200 and resp.content:
                img = Image.open(BytesIO(resp.content))
                img = img.convert("RGB")
                # Redimensionar manteniendo proporción y recortar a cuadrado
                img.thumbnail((500, 500), Image.Resampling.LANCZOS)
                width, height = img.size
                size = min(width, height)
                left = (width - size) / 2
                top = (height - size) / 2
                right = (width + size) / 2
                bottom = (height + size) / 2
                img = img.crop((left, top, right, bottom))
                # Guardar como JPEG en memoria
                output = BytesIO()
                img.save(output, format="JPEG", quality=90)
                data = output.getvalue()
        except Exception as e:
            logger.warning("No se pudo descargar/procesar carátula %s: %s", url, e)
        self._cover_cache[url] = data
        return data

    def _apply_metadata(self, path: Path, track: Track):
        if not MUTAGEN_AVAILABLE:
            logger.warning("mutagen no está instalado, no se pueden aplicar metadatos.")
            return
        try:
            ext = path.suffix.lower()
            cover_bytes = self._download_cover(track.cover_url) if track.cover_url else None
            artist_str = ", ".join(track.artists) if track.artists else ""

            if ext == ".mp3":
                self._apply_id3(path, track, artist_str, cover_bytes)
            elif ext in (".m4a", ".mp4"):
                self._apply_mp4(path, track, artist_str, cover_bytes)
            elif ext == ".flac":
                self._apply_flac(path, track, artist_str, cover_bytes)
            elif ext in (".ogg", ".opus"):
                self._apply_ogg(path, track, artist_str, cover_bytes)
            else:
                logger.info("Formato %s sin soporte de metadatos explícito", ext)
            logger.info("Metadatos aplicados a %s", path)
        except Exception:
            logger.exception("No se pudieron aplicar metadatos de Spotify a %s", path)

    def _apply_id3(self, path: Path, track: Track, artist_str: str, cover_bytes: Optional[bytes]):
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC, APIC

        # Creamos un objeto ID3 completamente nuevo para sobreescribir
        # cualquier metadato anterior y evitar conflictos.
        tags = ID3()
        tags["TIT2"] = TIT2(encoding=3, text=track.name)
        tags["TPE1"] = TPE1(encoding=3, text=artist_str)
        if track.album:
            tags["TALB"] = TALB(encoding=3, text=track.album)
        if track.album_artist:
            tags["TPE2"] = TPE2(encoding=3, text=track.album_artist)
        if track.track_number:
            tags["TRCK"] = TRCK(encoding=3, text=str(track.track_number))
        if track.disc_number:
            tags["TPOS"] = TPOS(encoding=3, text=str(track.disc_number))
        if track.release_date:
            tags["TDRC"] = TDRC(encoding=3, text=track.release_date)
        if cover_bytes:
            tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes)
        tags.save(str(path), v2_version=3)

    def _apply_mp4(self, path: Path, track: Track, artist_str: str, cover_bytes: Optional[bytes]):
        from mutagen.mp4 import MP4, MP4Cover

        tags = MP4(str(path))
        tags.clear()
        tags["\xa9nam"] = [track.name]
        tags["\xa9ART"] = [artist_str]
        if track.album:
            tags["\xa9alb"] = [track.album]
        if track.album_artist:
            tags["aART"] = [track.album_artist]
        if track.track_number:
            tags["trkn"] = [(track.track_number, 0)]
        if track.disc_number:
            tags["disk"] = [(track.disc_number, 0)]
        if track.release_date:
            tags["\xa9day"] = [track.release_date]
        if cover_bytes:
            tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
        tags.save()

    def _apply_flac(self, path: Path, track: Track, artist_str: str, cover_bytes: Optional[bytes]):
        from mutagen.flac import FLAC, Picture

        tags = FLAC(str(path))
        tags.clear()
        tags["title"] = track.name
        tags["artist"] = artist_str
        if track.album:
            tags["album"] = track.album
        if track.album_artist:
            tags["albumartist"] = track.album_artist
        if track.track_number:
            tags["tracknumber"] = str(track.track_number)
        if track.disc_number:
            tags["discnumber"] = str(track.disc_number)
        if track.release_date:
            tags["date"] = track.release_date
        if cover_bytes:
            tags.clear_pictures()
            pic = Picture()
            pic.data = cover_bytes
            pic.type = 3
            pic.mime = "image/jpeg"
            tags.add_picture(pic)
        tags.save()

    def _apply_ogg(self, path: Path, track: Track, artist_str: str, cover_bytes: Optional[bytes]):
        if path.suffix.lower() == ".opus":
            from mutagen.oggopus import OggOpus as OggFile
        else:
            from mutagen.oggvorbis import OggVorbis as OggFile
        from mutagen.flac import Picture

        tags = OggFile(str(path))
        tags.clear()
        tags["title"] = track.name
        tags["artist"] = artist_str
        if track.album:
            tags["album"] = track.album
        if track.album_artist:
            tags["albumartist"] = track.album_artist
        if track.track_number:
            tags["tracknumber"] = str(track.track_number)
        if track.release_date:
            tags["date"] = track.release_date
        if cover_bytes:
            pic = Picture()
            pic.data = cover_bytes
            pic.type = 3
            pic.mime = "image/jpeg"
            tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        tags.save()

    def _attempt(self, track: Track, target: str, filename_base: str, is_youtube: bool) -> Optional[Path]:
        output_template = str(self.output_dir / f"{filename_base}.%(ext)s")

        args = [
            "-x",                      # Extraer audio
            "--audio-format", self.config.OUTPUT_FORMAT,   # mp3 por defecto
            "--audio-quality", self.config.AUDIO_QUALITY,
            "-o", output_template,
            "--format", "bestaudio/best",   # Solo audio
            "--no-playlist",
            "--no-embed-metadata",          # No incrustar metadatos de yt-dlp
            "--no-embed-thumbnail",         # No incrustar miniatura de yt-dlp
            "--no-write-thumbnail",         # No descargar miniatura
            "--no-mtime",                   # No modificar fecha de archivo por yt-dlp
        ]
        if is_youtube:
            args += self._cookies_args()
            args += [
                "--extractor-args", "youtube:player_client=android",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ]
            args += ["--sponsorblock-remove", "all"]

        # Evitar que ffmpeg copie metadatos del contenedor original durante la conversión
        args += [
            "--postprocessor-args", "ffmpeg:-map_metadata -1",
        ]

        args += [
            "--concurrent-fragments", str(min(self.config.THREADS, 4)),
            "--no-keep-video",
            "--no-continue",
            "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "3",
            "--sleep-requests", "1", "--sleep-interval", "1",
            "--no-overwrites", "--ignore-errors", "--no-progress",
            "--no-warnings", "--quiet",
            target,
        ]
        if self.config.YTDLP_EXTRA:
            args.extend(self.config.YTDLP_EXTRA.split())

        ok, _, stderr = self._run_ytdlp(args, timeout=300)
        final_path = self.output_dir / f"{filename_base}.{self.config.OUTPUT_FORMAT}"

        # Si el archivo de audio existe y tiene tamaño, lo consideramos éxito
        # aunque yt-dlp haya devuelto un aviso (por ejemplo, 403 al intentar la miniatura)
        if final_path.exists() and final_path.stat().st_size > 0:
            self._apply_metadata(final_path, track)
            if not ok:
                logger.info(
                    "yt-dlp terminó con aviso para '%s', pero el audio existe y se procesó correctamente.",
                    track.display_name
                )
            return final_path

        if not ok:
            self._notify_error(stderr)
            logger.warning(
                "yt-dlp falló para '%s' [target='%s']: %s",
                track.display_name, target, stderr.decode("utf-8", errors="ignore")[-300:]
            )
            return None
        return None

    def download(self, track: Track) -> Optional[Path]:
        existing = self.find_existing(track)
        if existing and self.config.SKIP_EXISTING:
            # No aplicamos metadatos aquí para no alterar el orden de la playlist
            return existing

        filename_base = self.resolve_filename(track)

        youtube_queries = [
            track.search_query,
            track.legacy_display_name,
            f"{track.name} {track.artists[0] if track.artists else ''} audio",
        ]
        for i, q in enumerate(youtube_queries):
            candidate = self._search_best(track, "ytsearch", q, count=5)
            if candidate and candidate.get("id"):
                url = f"https://www.youtube.com/watch?v={candidate['id']}"
                result = self._attempt(track, url, filename_base, is_youtube=True)
                if result:
                    return result
            if i < len(youtube_queries) - 1:
                time.sleep(1)

        # Último recurso YouTube
        result = self._attempt(track, f"ytsearch1:{track.search_query}", filename_base, is_youtube=True)
        if result:
            return result

        # Respaldo SoundCloud
        if self._error_cb:
            self._error_cb(f"YouTube no disponible para '{track.display_name}', probando SoundCloud...")
        logger.info("YouTube agotado para '%s', probando SoundCloud como respaldo", track.display_name)

        sc_query = f"{track.legacy_display_name} audio"
        sc_candidate = self._search_best(track, "scsearch", sc_query, count=5)
        if sc_candidate:
            sc_url = sc_candidate.get("webpage_url") or sc_candidate.get("url")
            if sc_url:
                result = self._attempt(track, sc_url, filename_base, is_youtube=False)
                if result:
                    return result
        return self._attempt(track, f"scsearch1:{sc_query}", filename_base, is_youtube=False)

    def reapply_metadata(self, track: Track) -> Optional[Path]:
        """
        Vuelve a incrustar los metadatos y la portada de Spotify en un archivo
        ya descargado, sin descargar el audio de nuevo.
        """
        path = self.find_existing(track)
        if path and path.exists():
            self._apply_metadata(path, track)
            return path
        return None

    def resolve_filename(self, track: Track) -> str:
        base = track.safe_filename
        key = base.lower()
        owner = self._name_registry.get(key)
        if owner is None or owner == track.id:
            self._name_registry[key] = track.id
            return base
        artist = track.artists[0] if track.artists else ""
        disambiguated = f"{base} ({artist})" if artist else f"{base} ({track.id[:6]})"
        disambiguated = Track._sanitize(disambiguated) or f"{base} ({track.id[:6]})"
        self._name_registry[f"{disambiguated.lower()}"] = track.id
        return disambiguated

    def find_existing(self, track: Track) -> Optional[Path]:
        # Búsqueda flexible: probamos con varios nombres base y extensiones
        names = [track.safe_filename, track.legacy_safe_filename, track.legacy_safe_filename_v0]
        for name in names:
            if not name:
                continue
            # Buscar cualquier archivo que comience con el nombre y tenga extensión de audio
            for ext in self.AUDIO_EXTENSIONS:
                candidate = self.output_dir / f"{name}{ext}"
                if candidate.exists():
                    logger.info(f"Archivo encontrado: {candidate}")
                    return candidate
                # También probar con mayúsculas
                candidate_upper = self.output_dir / f"{name}{ext.upper()}"
                if candidate_upper.exists():
                    logger.info(f"Archivo encontrado: {candidate_upper}")
                    return candidate_upper
            # Búsqueda con glob para capturar variaciones
            try:
                for file in self.output_dir.glob(f"{name}.*"):
                    if file.suffix.lower() in self.AUDIO_EXTENSIONS:
                        logger.info(f"Archivo encontrado (glob): {file}")
                        return file
            except Exception:
                pass
        logger.warning(f"No se encontró archivo para '{track.display_name}' (buscado: {names})")
        return None

    def cleanup_track_junk(self, track: Track, filename_base: Optional[str] = None):
        names = {filename_base, track.safe_filename, track.legacy_safe_filename, track.legacy_safe_filename_v0}
        prefixes = tuple(f"{n}." for n in names if n)
        junk_exts = self._junk_extensions()
        try:
            for f in self.output_dir.iterdir():
                if not f.is_file() or not f.name.startswith(prefixes):
                    continue
                if f.suffix.lower() in junk_exts:
                    try:
                        f.unlink()
                        logger.info("Limpieza: eliminado archivo huérfano %s", f.name)
                    except OSError as e:
                        logger.warning("No se pudo borrar huérfano %s: %s", f.name, e)
        except Exception as e:
            logger.warning("Error listando %s para limpieza: %s", self.output_dir, e)


# ---------------------------------------------------------------------------
# Funciones de limpieza y reordenamiento
# ---------------------------------------------------------------------------
def cleanup_orphan_files(output_dir: Path, config: Config) -> int:
    final_ext = f".{config.OUTPUT_FORMAT.lower().strip()}"
    junk_exts = Downloader.ALWAYS_JUNK_EXTENSIONS | (Downloader.RAW_CONTAINER_EXTENSIONS - {final_ext})
    removed = 0
    try:
        for f in output_dir.iterdir():
            if not f.is_file():
                continue
            # Eliminar por extensión basura
            if f.suffix.lower() in junk_exts:
                try:
                    f.unlink()
                    removed += 1
                    continue
                except OSError as e:
                    logger.warning("No se pudo borrar huérfano %s: %s", f.name, e)
            # Eliminar temporales con doble extensión (.temp.mp3, .part.mp4, etc.)
            if ".temp." in f.name or ".part." in f.name:
                try:
                    f.unlink()
                    removed += 1
                except OSError as e:
                    logger.warning("No se pudo borrar temporal %s: %s", f.name, e)
    except Exception as e:
        logger.warning("Error en barrido de huérfanos de %s: %s", output_dir, e)
    if removed:
        logger.info("Barrido de %s: %d archivo(s) huérfano(s) eliminado(s)", output_dir, removed)
    return removed


def _set_windows_creation_time(path: Path, dt: datetime):
    try:
        import ctypes
        from ctypes import wintypes

        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80

        handle = ctypes.windll.kernel32.CreateFileW(
            str(path), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
        )
        if handle == -1 or handle == 0:
            return

        EPOCH_AS_FILETIME = 116444736000000000
        HUNDREDS_OF_NS = 10_000_000
        ticks = int(dt.timestamp() * HUNDREDS_OF_NS) + EPOCH_AS_FILETIME
        creation_time = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

        ctypes.windll.kernel32.SetFileTime(handle, ctypes.byref(creation_time), None, None)
        ctypes.windll.kernel32.CloseHandle(handle)
    except Exception as e:
        logger.warning("No se pudo ajustar fecha de creación de %s: %s", path, e)


def reorder_files_by_playlist(tracks: List[Track], files_map: Dict[str, str], output_dir: Path) -> int:
    ordered_paths: List[Path] = []
    for t in tracks:
        fname = files_map.get(t.id)
        if not fname:
            continue
        p = output_dir / fname
        if p.exists():
            ordered_paths.append(p)

    if not ordered_paths:
        return 0

    base_ts = time.time() - len(ordered_paths) - 5
    updated = 0
    for i, path in enumerate(ordered_paths):
        ts = base_ts + i
        try:
            os.utime(path, (ts, ts))
            if IS_WINDOWS:
                _set_windows_creation_time(path, datetime.fromtimestamp(ts))
            updated += 1
        except Exception as e:
            logger.warning("No se pudo reordenar fecha de %s: %s", path, e)

    if updated:
        logger.info("Reordenadas %d fecha(s) de archivo según orden de playlist en %s", updated, output_dir)
    return updated


# ---------------------------------------------------------------------------
# Motor de sincronización
# ---------------------------------------------------------------------------
class SyncEngine:
    def __init__(self, config: Config, progress_cb=None, status_cb=None,
                 playlists_source: Optional[Callable[[], List[dict]]] = None,
                 ui_update_cb: Optional[Callable[[str, dict], None]] = None):
        self.config = config
        self.spotify = SpotifyExtractor()
        self.state_dir = Path(config.STATE_DIR)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.progress_cb = progress_cb
        self.status_cb = status_cb
        self.ui_update_cb = ui_update_cb
        self._daemon_running = False
        self._daemon_thread = None
        self._playlists_source = playlists_source
        self._stop_event: Optional[threading.Event] = None

    def _state_file(self, playlist_id: str) -> Path:
        return self.state_dir / f"{playlist_id}.json"

    def _load_state(self, playlist_id: str) -> dict:
        path = self._state_file(playlist_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("No se pudo leer estado de %s: %s", playlist_id, e)
        return {"track_ids": [], "files": {}, "snapshot_id": ""}

    def _save_state(self, playlist_id: str, state: dict):
        try:
            with open(self._state_file(playlist_id), "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("No se pudo guardar estado de %s: %s", playlist_id, e)

    def _filter_track(self, track: Track) -> bool:
        if self.config.SKIP_EXPLICIT and track.explicit:
            return False
        if track.popularity < self.config.MIN_POPULARITY:
            return False
        for blocked in self.config.BLOCKED_ARTISTS:
            if any(blocked in a.lower() for a in track.artists):
                return False
        return True

    def sync(self, playlist_url: str, output_dir: Path, force: bool = False,
             stop_event: Optional[threading.Event] = None) -> dict:
        playlist_id = self.spotify.extract_playlist_id(playlist_url)
        state = self._load_state(playlist_id)

        try:
            playlist = self.spotify.get_playlist(playlist_id)
        except Exception as e:
            return {"error": str(e), "playlist_id": playlist_id}

        tracks = [t for t in playlist.tracks if self._filter_track(t)]
        skipped = len(playlist.tracks) - len(tracks)
        current_ids = {t.id for t in tracks}

        prev_ids = set(state.get("track_ids", []))
        prev_files = dict(state.get("files", {}))

        new_ids = current_ids - prev_ids
        removed_ids = prev_ids - current_ids

        prev_order_raw = state.get("track_order", [])
        prev_order = [tid for tid in prev_order_raw if tid in current_ids]
        current_order = [t.id for t in tracks]
        order_changed = current_order != prev_order

        missing_files = []
        for tid, fname in prev_files.items():
            if tid in current_ids and not (output_dir / fname).exists():
                missing_files.append(tid)

        downloader = Downloader(output_dir, self.config,
                                error_cb=lambda msg: self.status_cb("", msg) if self.status_cb else None)
        # Solo detectamos archivos faltantes; no aplicamos metadatos aquí
        for track in tracks:
            if track.id in current_ids and not downloader.find_existing(track):
                if track.id not in missing_files:
                    missing_files.append(track.id)

        has_changes = bool(new_ids or removed_ids or missing_files or force or order_changed)

        if self.status_cb:
            self.status_cb("", f"Playlist: {playlist.name} | Tracks: {len(tracks)} | "
                               f"Nuevas: {len(new_ids)} | Eliminar: {len(removed_ids)} | "
                               f"Faltan: {len(missing_files)} | Orden cambió: {'sí' if order_changed else 'no'}")

        if not has_changes:
            # No hay cambios: no se tocan metadatos ni se reordena
            self._save_state(playlist_id, {
                "playlist_name": playlist.name,
                "last_sync": datetime.now().isoformat(),
                "snapshot_id": playlist.snapshot_id,
                "track_ids": list(current_ids),
                "track_order": current_order,
                "files": {tid: fname for tid, fname in prev_files.items()
                          if tid in current_ids and (output_dir / fname).exists()}
            })
            return {"status": "no_changes", "playlist_name": playlist.name, "total_tracks": len(tracks),
                    "cover_url": playlist.cover_url}

        files_map = {}
        # Construimos el mapa de archivos existentes sin aplicar metadatos
        for track in tracks:
            existing = downloader.find_existing(track)
            if existing:
                files_map[track.id] = str(existing.name)

        for tid, fname in prev_files.items():
            if tid in current_ids and (output_dir / fname).exists():
                files_map[tid] = fname

        # Refuerzo: revisar de nuevo en disco para no redescargar nada que ya existe
        for track in tracks:
            if track.id not in files_map:
                existing = downloader.find_existing(track)
                if existing:
                    files_map[track.id] = str(existing.name)

        # Solo se descargan pistas que no tienen archivo local
        tracks_to_download = [
            t for t in tracks
            if t.id not in files_map or not (output_dir / files_map[t.id]).exists()
        ]
        total_to_download = len(tracks_to_download)

        downloaded = 0
        failed = 0
        completed = 0
        progress_lock = threading.Lock()
        cancelled = False
        failed_tracks = []

        def _should_abort() -> bool:
            if stop_event is not None and stop_event.is_set():
                return True
            if self._playlists_source is not None:
                active_urls = {p["url"] for p in self._playlists_source()}
                if playlist_url not in active_urls:
                    return True
            return False

        def _download_one(track: Track):
            if _should_abort():
                return track, None, True
            result = downloader.download(track)
            return track, result, False

        max_workers = max(1, min(self.config.THREADS, total_to_download)) if total_to_download else 1
        if total_to_download:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dl") as executor:
                futures = {executor.submit(_download_one, t): t for t in tracks_to_download}
                for future in as_completed(futures):
                    track, result, skipped = future.result()
                    with progress_lock:
                        completed += 1
                        if self.progress_cb:
                            self.progress_cb(completed, total_to_download, track.display_name)
                    if skipped:
                        cancelled = True
                        continue
                    if result:
                        files_map[track.id] = str(result.name)
                        downloaded += 1
                    else:
                        failed += 1
                        failed_tracks.append(track.display_name)

        if cancelled and self.status_cb:
            self.status_cb("", "Sincronización cancelada o playlist eliminada")

        deleted = 0
        if self.config.DELETE_REMOVED:
            for rid in removed_ids:
                if rid in prev_files:
                    file_path = output_dir / prev_files[rid]
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted += 1
                        except Exception as e:
                            logger.warning("No se pudo borrar %s: %s", file_path, e)
                    if rid in files_map:
                        del files_map[rid]

        # Guardar estado y reordenar (el orden se restaura aquí)
        self._save_state(playlist_id, {
            "playlist_name": playlist.name,
            "last_sync": datetime.now().isoformat(),
            "snapshot_id": playlist.snapshot_id,
            "track_ids": list(current_ids),
            "track_order": current_order,
            "files": {tid: fname for tid, fname in files_map.items() if tid in current_ids}
        })

        reordered = reorder_files_by_playlist(tracks, files_map, output_dir)
        cleanup_orphan_files(output_dir, self.config)

        return {
            "playlist_name": playlist.name,
            "total_tracks": len(tracks),
            "downloaded": downloaded,
            "failed": failed,
            "deleted": deleted,
            "skipped_filters": skipped,
            "already_have": len(tracks) - total_to_download,
            "reordered": reordered,
            "output_dir": str(output_dir),
            "failed_tracks": failed_tracks,
            "cover_url": playlist.cover_url,
        }

    def fix_metadata_for_playlist(self, playlist_url: str, output_dir: Path,
                                  stop_event: Optional[threading.Event] = None) -> dict:
        """
        Corrige los metadatos y portadas de todas las canciones ya descargadas
        de una playlist. No descarga audio, solo reescribe los tags.
        Al final reordena las fechas de modificación para mantener el orden de la playlist.
        """
        playlist_id = self.spotify.extract_playlist_id(playlist_url)
        try:
            playlist = self.spotify.get_playlist(playlist_id)
        except Exception as e:
            return {"error": str(e), "playlist_id": playlist_id}

        tracks = [t for t in playlist.tracks if self._filter_track(t)]
        downloader = Downloader(output_dir, self.config)

        updated = 0
        missing = 0
        errors = 0
        files_map = {}  # Para reordenar después

        for i, track in enumerate(tracks):
            if stop_event and stop_event.is_set():
                break
            try:
                path = downloader.reapply_metadata(track)
                if path:
                    updated += 1
                    files_map[track.id] = str(path.name)
                    logger.info(f"Metadatos corregidos para: {track.display_name}")
                    if self.progress_cb:
                        self.progress_cb(i + 1, len(tracks), f"Metadatos: {track.display_name}")
                else:
                    missing += 1
                    logger.warning(f"Archivo no encontrado para: {track.display_name}")
            except Exception:
                errors += 1
                logger.exception("Error corrigiendo metadatos de %s", track.display_name)

        # Restaurar el orden de la playlist (fechas de modificación)
        reordered = reorder_files_by_playlist(tracks, files_map, output_dir)

        result = {
            "total_tracks": len(tracks),
            "updated": updated,
            "missing": missing,
            "errors": errors,
            "reordered": reordered,
        }
        logger.info(f"Resumen de corrección de metadatos: {result}")
        return result

    def get_missing_tracks(self, playlist_url: str, output_dir: Path) -> dict:
        """
        Devuelve:
        - missing: lista de dicts con posición y nombre de pistas faltantes.
        - collisions: lista de dicts con archivo y pistas (posiciones y nombres) que comparten archivo.
        - orphans: lista de nombres de archivo en la carpeta que no están en el estado.
        - total_tracks: número total de pistas en Spotify.
        - unique_files: número de archivos únicos asignados.
        """
        playlist_id = self.spotify.extract_playlist_id(playlist_url)
        state = self._load_state(playlist_id)
        files_map = state.get("files", {})

        try:
            playlist = self.spotify.get_playlist(playlist_id)
        except Exception as e:
            raise RuntimeError(f"No se pudo obtener la playlist: {e}")

        tracks = [t for t in playlist.tracks if self._filter_track(t)]

        missing = []
        used_names = {}  # filename -> list of (track, position)
        assigned_files = set()   # archivos referenciados en el estado

        for idx, track in enumerate(tracks, start=1):
            fname = files_map.get(track.id)
            if not fname:
                missing.append({"position": idx, "name": track.display_name})
            else:
                file_path = output_dir / fname
                if not file_path.exists():
                    missing.append({"position": idx, "name": track.display_name})
                else:
                    assigned_files.add(fname)
                    used_names.setdefault(fname, []).append({"position": idx, "track": track})

        # Detectar colisiones (mismo archivo para varias pistas)
        collisions = []
        for fname, track_list in used_names.items():
            if len(track_list) > 1:
                details = []
                for item in track_list:
                    pos = item["position"]
                    name = item["track"].display_name
                    details.append(f"#{pos}: {name}")
                collisions.append({
                    "file": fname,
                    "tracks": details
                })

        # Detectar archivos huérfanos: existen en la carpeta pero no están en files_map
        orphans = []
        if output_dir.exists():
            audio_exts = [
                ".mp3", ".m4a", ".flac", ".opus",
                ".ogg", ".wav", ".aac", ".mp4",
            ]
            for file in output_dir.iterdir():
                if file.is_file() and file.suffix.lower() in audio_exts:
                    if ".temp." in file.name or ".part." in file.name:
                        continue
                    if file.name not in assigned_files:
                        orphans.append(file.name)

        return {
            "missing": missing,
            "collisions": collisions,
            "orphans": orphans,
            "total_tracks": len(tracks),
            "unique_files": len(used_names),
        }

    def start_daemon(self, playlists: List[dict]):
        self._daemon_running = True
        self._stop_event = threading.Event()
        self._daemon_thread = threading.Thread(target=self._daemon_loop, args=(playlists,), daemon=True)
        self._daemon_thread.start()

    def _daemon_loop(self, playlists: List[dict]):
        while self._daemon_running:
            current_playlists = self._playlists_source() if self._playlists_source else playlists
            for pl in current_playlists:
                if not self._daemon_running or self._stop_event.is_set():
                    break
                try:
                    if self.status_cb:
                        self.status_cb(pl.get("name", ""), "Verificando cambios...")
                    out = Path(pl["output"])
                    out.mkdir(parents=True, exist_ok=True)
                    result = self.sync(pl["url"], out, stop_event=self._stop_event)
                    if self.ui_update_cb:
                        self.ui_update_cb(pl["url"], result)
                    if self.status_cb:
                        if "error" in result:
                            self.status_cb(pl.get("name", ""), f"Error: {result['error'][:50]}")
                        elif result.get("status") == "no_changes":
                            self.status_cb(pl.get("name", ""), "Sin cambios")
                        else:
                            self.status_cb(pl.get("name", ""), f"+{result['downloaded']} -{result['deleted']} "
                                                               f"fail:{result['failed']} have:{result['already_have']} "
                                                               f"orden:{result.get('reordered', 0)}")
                except Exception as e:
                    if self.status_cb:
                        self.status_cb(pl.get("name", ""), f"Error: {str(e)[:50]}")
            if self._daemon_running and not self._stop_event.is_set():
                if self.status_cb:
                    self.status_cb("", "Esperando...")
                slept = 0
                while slept < self.config.SYNC_INTERVAL and self._daemon_running:
                    if self._stop_event.wait(1.0):
                        break
                    slept += 1

    def stop_daemon(self):
        self._daemon_running = False
        if self._stop_event is not None:
            self._stop_event.set()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)
        self._stop_event = None

    def is_daemon_running(self) -> bool:
        return self._daemon_running


# ---------------------------------------------------------------------------
# Interfaz gráfica
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Paleta e interfaz gráfica — minimalista, plana, un solo tema (clam) para
# que los colores se apliquen igual en Windows y Linux y el render sea liviano.
# ---------------------------------------------------------------------------
COLOR_BG = "#121212"
COLOR_SURFACE = "#181818"
COLOR_SURFACE_ALT = "#282828"
COLOR_SURFACE_HOVER = "#2A2A2A"
COLOR_BORDER = "#2A2A2A"
COLOR_ACCENT = "#1DB954"
COLOR_ACCENT_HOVER = "#1ED760"
COLOR_TEXT = "#FFFFFF"
COLOR_TEXT_MUTED = "#B3B3B3"
COLOR_TEXT_FAINT = "#6A6A6A"
COLOR_OK = "#1DB954"
COLOR_WARN = "#F2A93B"
COLOR_ERROR = "#F15E6C"
COLOR_INFO = "#509BF5"
COLOR_IDLE = "#6A6A6A"

FONT_FAMILY = "Segoe UI" if IS_WINDOWS else "Noto Sans"


class SpotifySyncApp:
    def __init__(self, root: tk.Tk, start_minimized: bool = False):
        self.root = root
        self.root.title("Spotify Sync")
        self.root.geometry("900x640")
        self.root.minsize(760, 540)

        self.config = Config()
        self.engine = SyncEngine(self.config, progress_cb=self.on_progress, status_cb=self.on_status)
        self.playlists: List[dict] = []
        self.settings: Dict[str, Any] = {
            "interval": Config.SYNC_INTERVAL,
            "format": Config.OUTPUT_FORMAT,
            "delete_removed": Config.DELETE_REMOVED,
            "auto_sync_enabled": False,
            "ytdlp_extra": Config.YTDLP_EXTRA,
        }
        self.tray_icon = None
        self._pending = []
        self._save_config_job = None
        self._queue_poll_job = None
        self._playlist_totals: Dict[str, int] = {}   # URL → total de pistas en vivo
        self._thumb_photos: Dict[str, ImageTk.PhotoImage] = {}  # cover_url → imagen viva (evita que el GC la borre)
        self._thumb_requested: set = set()            # cover_url ya en descarga/descargados esta sesión
        self._placeholder_thumb: Optional[ImageTk.PhotoImage] = None

        self._setup_style()
        self.load_config()
        self._repair_startup_entry()
        self.build_ui()
        self.check_dependencies()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self._process_queue()

        if self.settings.get("auto_sync_enabled") and self.playlists:
            self.root.after(400, self.start_autosync)

        if start_minimized:
            self.root.after(700, lambda: self.hide_to_tray(silent=True))

    # -- Estilo -------------------------------------------------------------
    def _setup_style(self):
        """Un único tema (clam) aplicado igual en todas las plataformas: menos
        capas nativas que redibujar que un tema del sistema, y control total
        de color para un look plano y consistente."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT,
                         font=(FONT_FAMILY, 10), borderwidth=0, relief="flat")
        style.configure("TFrame", background=COLOR_BG, borderwidth=0)
        style.configure("Surface.TFrame", background=COLOR_SURFACE, borderwidth=0)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_MUTED,
                         font=(FONT_FAMILY, 9))
        style.configure("Caption.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_FAINT,
                         font=(FONT_FAMILY, 8))
        style.configure("Title.TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                         font=(FONT_FAMILY, 22, "bold"))
        style.configure("Subtitle.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_MUTED,
                         font=(FONT_FAMILY, 10))
        # Spotify no encierra secciones en cajas con borde: separa con
        # espacio y una cabecera en mayúsculas apagada. Sin relief/borde.
        style.configure("Section.TLabelframe", background=COLOR_BG, borderwidth=0, relief="flat")
        style.configure("Section.TLabelframe.Label", background=COLOR_BG,
                         foreground=COLOR_TEXT_FAINT, font=(FONT_FAMILY, 8, "bold"))

        style.configure("TButton", background=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                         borderwidth=0, focusthickness=0, padding=(14, 8), relief="flat",
                         lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_SURFACE_ALT, font=(FONT_FAMILY, 9))
        style.map("TButton", background=[("active", COLOR_SURFACE_HOVER)],
                  lightcolor=[("active", COLOR_SURFACE_HOVER)], darkcolor=[("active", COLOR_SURFACE_HOVER)])

        style.configure("TMenubutton", background=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                         borderwidth=0, padding=(14, 8), font=(FONT_FAMILY, 9), arrowcolor=COLOR_TEXT,
                         relief="flat", lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_SURFACE_ALT)
        style.map("TMenubutton", background=[("active", COLOR_SURFACE_HOVER)])

        # Botón "píldora" verde: el CTA principal, como el de Spotify.
        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="#000000",
                         borderwidth=0, focusthickness=0, padding=(18, 9), relief="flat",
                         lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT, bordercolor=COLOR_ACCENT,
                         font=(FONT_FAMILY, 9, "bold"))
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT_HOVER)],
                  lightcolor=[("active", COLOR_ACCENT_HOVER)], darkcolor=[("active", COLOR_ACCENT_HOVER)])

        style.configure("Stop.TButton", background=COLOR_SURFACE_ALT, foreground=COLOR_WARN,
                         borderwidth=0, focusthickness=0, padding=(18, 9), relief="flat",
                         lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_SURFACE_ALT, font=(FONT_FAMILY, 9, "bold"))
        style.map("Stop.TButton", background=[("active", COLOR_SURFACE_HOVER)],
                  lightcolor=[("active", COLOR_SURFACE_HOVER)], darkcolor=[("active", COLOR_SURFACE_HOVER)])

        # Filas más altas para que quepa la portada de la playlist.
        style.configure("Treeview", background=COLOR_SURFACE, fieldbackground=COLOR_SURFACE,
                         foreground=COLOR_TEXT, borderwidth=0, rowheight=48, relief="flat",
                         lightcolor=COLOR_SURFACE, darkcolor=COLOR_SURFACE, bordercolor=COLOR_SURFACE,
                         font=(FONT_FAMILY, 10))
        style.configure("Treeview.Heading", background=COLOR_BG, foreground=COLOR_TEXT_FAINT,
                         borderwidth=0, font=(FONT_FAMILY, 8, "bold"), relief="flat",
                         lightcolor=COLOR_BG, darkcolor=COLOR_BG, bordercolor=COLOR_BG)
        style.map("Treeview", background=[("selected", "#2A3B2F")],
                  foreground=[("selected", COLOR_TEXT)])
        style.map("Treeview.Heading", background=[("active", COLOR_BG)])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        # El tema "clam" dibuja un bisel 3D (borde claro arriba/izq, oscuro
        # abajo/der) en estos widgets salvo que se anulen lightcolor/darkcolor
        # explícitamente. Ese bisel es justo el "borde blanco" en modo oscuro.
        style.configure("Thin.Horizontal.TProgressbar", troughcolor=COLOR_SURFACE_ALT,
                         background=COLOR_ACCENT, borderwidth=0, thickness=4,
                         lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT,
                         bordercolor=COLOR_SURFACE_ALT, troughrelief="flat", relief="flat")

        style.configure("TCheckbutton", background=COLOR_BG, foreground=COLOR_TEXT,
                         focuscolor=COLOR_BG, font=(FONT_FAMILY, 9))
        style.map("TCheckbutton", background=[("active", COLOR_BG)])

        style.configure("TSpinbox", fieldbackground=COLOR_SURFACE_ALT, background=COLOR_SURFACE_ALT,
                         foreground=COLOR_TEXT, arrowsize=12, borderwidth=0, relief="flat",
                         lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_SURFACE_ALT, insertcolor=COLOR_TEXT)
        style.map("TSpinbox", fieldbackground=[("readonly", COLOR_SURFACE_ALT)],
                  lightcolor=[("focus", COLOR_SURFACE_ALT)], darkcolor=[("focus", COLOR_SURFACE_ALT)])

        style.configure("TCombobox", fieldbackground=COLOR_SURFACE_ALT, background=COLOR_SURFACE_ALT,
                         foreground=COLOR_TEXT, arrowsize=12, borderwidth=0, relief="flat",
                         lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_SURFACE_ALT)
        style.map("TCombobox", fieldbackground=[("readonly", COLOR_SURFACE_ALT)],
                   foreground=[("readonly", COLOR_TEXT)],
                   lightcolor=[("focus", COLOR_SURFACE_ALT), ("!focus", COLOR_SURFACE_ALT)],
                   darkcolor=[("focus", COLOR_SURFACE_ALT), ("!focus", COLOR_SURFACE_ALT)])

        style.configure("TScrollbar", background=COLOR_SURFACE_ALT, troughcolor=COLOR_BG,
                         borderwidth=0, arrowsize=12, relief="flat",
                         lightcolor=COLOR_SURFACE_ALT, darkcolor=COLOR_SURFACE_ALT,
                         bordercolor=COLOR_BG)
        style.map("TScrollbar", background=[("active", COLOR_SURFACE_HOVER)])

        self.root.configure(bg=COLOR_BG, highlightthickness=0, bd=0)
        self.root.option_add("*TCombobox*Listbox.background", COLOR_SURFACE_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT)
        self.root.option_add("*Dialog.msg.font", (FONT_FAMILY, 9))

        # En Windows, quita el marco/título claro por defecto (era el "borde
        # blanco" que se veía en modo oscuro) y lo pinta a juego.
        self.root.after(10, self._apply_windows_dark_titlebar)

    def _apply_windows_dark_titlebar(self):
        """Windows dibuja la barra de título y el borde de la ventana con el
        tema claro del sistema salvo que se le pida explícitamente lo
        contrario vía DWM. Esto es lo que causaba los bordes/título blancos
        alrededor de una app por lo demás oscura. Requiere Windows 10 1809+
        (modo oscuro) / Windows 11 22H2+ (color de borde y título)."""
        if not IS_WINDOWS:
            return
        try:
            import ctypes

            def to_colorref(hex_color: str) -> int:
                hex_color = hex_color.lstrip('#')
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                return r | (g << 8) | (b << 16)

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            dwmapi = ctypes.windll.dwmapi

            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_BORDER_COLOR = 34
            DWMWA_CAPTION_COLOR = 35
            DWMWA_TEXT_COLOR = 36

            dark = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                          ctypes.byref(dark), ctypes.sizeof(dark))

            border = ctypes.c_int(to_colorref(COLOR_BORDER))
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR,
                                          ctypes.byref(border), ctypes.sizeof(border))

            caption = ctypes.c_int(to_colorref(COLOR_BG))
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR,
                                          ctypes.byref(caption), ctypes.sizeof(caption))

            text_color = ctypes.c_int(to_colorref(COLOR_TEXT))
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_TEXT_COLOR,
                                          ctypes.byref(text_color), ctypes.sizeof(text_color))
        except Exception:
            # Versión de Windows sin soporte (pre-1809) u otra falla no
            # crítica: la app sigue funcionando con la barra de título nativa.
            logger.debug("No se pudo oscurecer la barra de título de Windows", exc_info=True)

    @staticmethod
    def _binary_available(cmd: List[str]) -> bool:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
        if IS_WINDOWS:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            subprocess.run(cmd, **kwargs)
            return True
        except Exception:
            return False

    def check_dependencies(self):
        missing = []
        if importlib.util.find_spec("spotify_scraper") is None:
            missing.append("spotifyscraper")
        if not self._binary_available(["yt-dlp", "--version"]):
            missing.append("yt-dlp")
        if not self._binary_available(["ffmpeg", "-version"]):
            missing.append("ffmpeg")
        if not PYSTRAY_AVAILABLE:
            missing.append("pystray")
        if missing:
            extra_msg = ""
            if IS_WINDOWS and "ffmpeg" in missing:
                extra_msg += ("\n\nFFmpeg no encontrado. Descarga el .exe desde:\n"
                              "https://github.com/BtbN/FFmpeg-Builds/releases\n"
                              "y pon ffmpeg.exe junto a este script, o instala con:\n"
                              "winget install Gyan.FFmpeg")
            if IS_LINUX:
                extra_msg = ("\n\nEn Linux, asegúrate de tener también:\n"
                             "  sudo apt install python3-tk libappindicator3-1 ffmpeg   (Debian/Ubuntu/Mint)\n"
                             "  sudo pacman -S python tk libappindicator-gtk3 ffmpeg      (Arch)")
            messagebox.showwarning(
                "Dependencias faltantes",
                f"Faltan los siguientes paquetes: {', '.join(missing)}\n\n"
                f"Instálalos con:\npip install {' '.join([p for p in missing if p != 'ffmpeg'])}{extra_msg}"
            )

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.playlists = data.get("playlists", [])
                    self.settings.update(data.get("settings", {}))
            except Exception as e:
                logger.warning("No se pudo cargar %s: %s", CONFIG_FILE, e)
                self.playlists = []

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"playlists": self.playlists, "settings": self.settings}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("No se pudo guardar %s: %s", CONFIG_FILE, e)

    # -- Layout ---------------------------------------------------------
    def build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        self._build_header(outer)
        self._build_toolbar(outer)
        self._build_progress(outer)
        self._build_playlist_tree(outer)
        self._build_settings(outer)
        self._build_statusbar(outer)

        self.refresh_list()

    def _build_header(self, parent):
        hdr = ttk.Frame(parent)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        hdr.columnconfigure(0, weight=1)

        title_box = ttk.Frame(hdr)
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="Spotify Sync", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Tus playlists, siempre descargadas y al día",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        status_box = ttk.Frame(hdr)
        status_box.grid(row=0, column=1, sticky="e")
        self.autosync_dot = tk.Label(status_box, text="●", font=(FONT_FAMILY, 12),
                                      fg=COLOR_IDLE, bg=COLOR_BG, bd=0)
        self.autosync_dot.pack(side="left", padx=(0, 6))
        self.autosync_label = ttk.Label(status_box, text="Auto-Sync detenido", style="Muted.TLabel")
        self.autosync_label.pack(side="left")

    def _build_toolbar(self, parent):
        tb = ttk.Frame(parent)
        tb.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        primary = ttk.Frame(tb)
        primary.pack(side="left")
        b_add = ttk.Button(primary, text="＋ Agregar", command=self.add_playlist)
        b_add.pack(side="left", padx=(0, 6))
        ToolTip(b_add, "Agrega una playlist por URL. El Auto-Sync se activa al instante.")

        b_sync = ttk.Button(primary, text="⟲ Sincronizar", command=self.sync_now)
        b_sync.pack(side="left", padx=6)
        ToolTip(b_sync, "Sincroniza la playlist seleccionada (o todas si no hay selección)")

        b_del = ttk.Button(primary, text="Quitar", command=self.remove_playlist)
        b_del.pack(side="left", padx=6)
        ToolTip(b_del, "Quita la playlist seleccionada de la lista (no borra archivos)")

        tools = ttk.Menubutton(primary, text="Más ⌄")
        tools_menu = tk.Menu(tools, tearoff=0, bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT,
                              activebackground=COLOR_ACCENT, activeforeground="#0A0A0A",
                              bd=0)
        tools_menu.add_command(label="⬇ Forzar descarga completa", command=self.force_download)
        tools_menu.add_command(label="🛠 Corregir metadatos", command=self.fix_metadata_now)
        tools_menu.add_command(label="🔍 Ver faltantes / huérfanos", command=self.show_missing_tracks)
        tools_menu.add_separator()
        tools_menu.add_command(label="⟳ Actualizar yt-dlp", command=self.update_ytdlp)
        tools.configure(menu=tools_menu)
        tools.pack(side="left", padx=6)
        ToolTip(tools, "Diagnóstico y mantenimiento: metadatos, archivos faltantes, actualizar yt-dlp")

        self.btn_daemon = ttk.Button(tb, text="▶ Iniciar Auto-Sync", style="Accent.TButton",
                                      command=self.toggle_daemon)
        self.btn_daemon.pack(side="right")
        ToolTip(self.btn_daemon, "Activa/desactiva la sincronización automática periódica")

    def _build_progress(self, parent):
        pf = ttk.Frame(parent)
        pf.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        pf.columnconfigure(0, weight=1)
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(pf, variable=self.progress_var, maximum=100,
                         style="Thin.Horizontal.TProgressbar").grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.progress_label = ttk.Label(pf, text="Listo", width=40, anchor="e", style="Muted.TLabel")
        self.progress_label.grid(row=0, column=1, sticky="e")

    def _build_playlist_tree(self, parent):
        lf = ttk.Frame(parent, style="Surface.TFrame")
        lf.grid(row=3, column=0, sticky="nsew", pady=(0, 12))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        # "tree headings": la columna #0 (icono + texto) hace de portada+nombre,
        # igual que una fila de playlist en el propio Spotify.
        cols = ("tracks", "local", "status", "last")
        self.tree = ttk.Treeview(lf, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Nombre", anchor="w")
        self.tree.column("#0", width=260, minwidth=180, anchor="w", stretch=True)
        headings = {
            "tracks": ("En Spotify", 90, "center"),
            "local": ("Locales", 90, "center"),
            "status": ("Estado", 140, "center"),
            "last": ("Último sync", 140, "center"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        scroll = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.tag_configure("row_ok", foreground=COLOR_OK)
        self.tree.tag_configure("row_warn", foreground=COLOR_WARN)
        self.tree.tag_configure("row_error", foreground=COLOR_ERROR)
        self.tree.tag_configure("row_new", foreground=COLOR_INFO)
        self.tree.tag_configure("row_hover", background=COLOR_SURFACE_HOVER)

        ttk.Label(parent, text="Doble clic en una playlist para sincronizarla de inmediato.",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 10))
        self.tree.bind("<Double-1>", lambda e: self.sync_now())
        self.tree.bind("<Motion>", self._on_tree_hover)
        self.tree.bind("<Leave>", lambda e: self._clear_tree_hover())
        self._hovered_row = None

    def _on_tree_hover(self, event):
        row = self.tree.identify_row(event.y)
        if row == self._hovered_row:
            return
        self._clear_tree_hover()
        if row:
            current_tags = list(self.tree.item(row, "tags"))
            if "row_hover" not in current_tags:
                self.tree.item(row, tags=current_tags + ["row_hover"])
            self._hovered_row = row

    def _clear_tree_hover(self):
        if self._hovered_row and self.tree.exists(self._hovered_row):
            current_tags = [t for t in self.tree.item(self._hovered_row, "tags") if t != "row_hover"]
            self.tree.item(self._hovered_row, tags=current_tags)
        self._hovered_row = None

    def _build_settings(self, parent):
        wrap = ttk.Frame(parent)
        wrap.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        wrap.columnconfigure(0, weight=1)
        wrap.columnconfigure(1, weight=1)

        sync_box = ttk.LabelFrame(wrap, text="SINCRONIZACIÓN", style="Section.TLabelframe", padding=12)
        sync_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        row1 = ttk.Frame(sync_box)
        row1.pack(fill="x")
        ttk.Label(row1, text="Cada").pack(side="left")
        self.interval_var = tk.StringVar(value=str(self.settings.get("interval", 30)))
        spin = ttk.Spinbox(row1, from_=10, to=3600, textvariable=self.interval_var, width=6)
        spin.pack(side="left", padx=6)
        ttk.Label(row1, text="s   ·   Formato").pack(side="left")
        self.format_var = tk.StringVar(value=self.settings.get("format", "mp3"))
        combo = ttk.Combobox(row1, textvariable=self.format_var, values=["mp3", "m4a", "flac", "opus"],
                              width=7, state="readonly")
        combo.pack(side="left", padx=6)
        ToolTip(spin, "Cada cuánto tiempo se revisan cambios en las playlists (por defecto 30s)")

        self.delete_var = tk.BooleanVar(value=self.settings.get("delete_removed", True))
        chk_del = ttk.Checkbutton(sync_box, text="Eliminar canciones quitadas de la playlist",
                                   variable=self.delete_var, command=self._on_settings_changed)
        chk_del.pack(anchor="w", pady=(8, 0))
        ToolTip(chk_del, "Si una canción se quita de Spotify, también se borra el archivo local")

        self.interval_var.trace_add("write", lambda *a: self._on_settings_changed())
        self.format_var.trace_add("write", lambda *a: self._on_settings_changed())

        startup_box = ttk.LabelFrame(wrap, text="INICIO CON EL SISTEMA", style="Section.TLabelframe", padding=12)
        startup_box.grid(row=0, column=1, sticky="nsew")

        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        startup_text = "Iniciar con Windows (minimizado)" if IS_WINDOWS else "Iniciar con el sistema (minimizado)"
        chk_startup = ttk.Checkbutton(startup_box, text=startup_text,
                                       variable=self.startup_var, command=self.on_toggle_startup)
        chk_startup.pack(anchor="w")
        ToolTip(chk_startup, "La app arrancará junto con el sistema, directo en la bandeja del sistema")

        ttk.Label(startup_box, text="Reanuda el Auto-Sync automáticamente si estaba activo.",
                  style="Muted.TLabel", wraplength=240, justify="left").pack(anchor="w", pady=(6, 0))
        if not (IS_WINDOWS or IS_LINUX):
            chk_startup.configure(state="disabled")

    def _build_statusbar(self, parent):
        # Discreto a propósito: es solo el último evento, en una línea, sin
        # marco ni separador. El detalle completo de cada sincronización
        # siempre queda en spotify_sync.log — esto es apenas una pista visual.
        self.status_var = tk.StringVar(value="")
        bar = ttk.Frame(parent)
        bar.grid(row=6, column=0, sticky="ew", pady=(2, 0))
        ttk.Label(bar, textvariable=self.status_var, style="Caption.TLabel",
                  anchor="w").pack(side="left", fill="x", expand=True)

    def _set_status(self, text: str):
        """Actualiza la pista de estado discreta bajo la lista y deja el
        registro completo únicamente en el archivo .log (no en la UI)."""
        logger.debug(text)
        clipped = text if len(text) <= 110 else text[:107] + "…"
        self.status_var.set(clipped)

    def _on_settings_changed(self):
        try:
            self.settings["interval"] = max(10, int(self.interval_var.get()))
        except (ValueError, tk.TclError):
            pass
        self.settings["format"] = self.format_var.get()
        self.settings["delete_removed"] = bool(self.delete_var.get())
        if self.engine.is_daemon_running():
            self.config.SYNC_INTERVAL = self.settings["interval"]
            self.config.OUTPUT_FORMAT = self.settings["format"]
            self.config.DELETE_REMOVED = self.settings["delete_removed"]
        self._schedule_save_config()

    def _schedule_save_config(self, delay_ms: int = 600):
        """Debounce disk writes: coalesce bursts of changes (e.g. every keystroke
        in the interval spinbox) into a single save after the user pauses."""
        if self._save_config_job is not None:
            self.root.after_cancel(self._save_config_job)
        self._save_config_job = self.root.after(delay_ms, self._flush_config_save)

    def _flush_config_save(self):
        self._save_config_job = None
        self.save_config()

    def _apply_settings_to_config(self):
        try:
            self.config.SYNC_INTERVAL = max(10, int(self.interval_var.get()))
        except (ValueError, tk.TclError):
            self.config.SYNC_INTERVAL = 30
        self.config.OUTPUT_FORMAT = self.format_var.get()
        self.config.DELETE_REMOVED = self.delete_var.get()

    def _repair_startup_entry(self):
        if not self.settings.get("start_with_windows"):
            return
        if IS_WINDOWS:
            expected_cmd = f'wscript.exe "{_run_vbs_path()}" --minimized'
            if get_startup_command() != expected_cmd:
                set_startup_enabled(True)
        elif IS_LINUX:
            expected_cmd = f'python3 "{Path(__file__).resolve()}" --minimized'
            current = get_startup_command()
            if current != expected_cmd:
                set_startup_enabled(True)

    def on_toggle_startup(self):
        enable = self.startup_var.get()
        ok = set_startup_enabled(enable)
        if not ok:
            messagebox.showerror("No disponible", "No se pudo modificar el inicio automático.")
            self.startup_var.set(not enable)
            return
        self.settings["start_with_windows"] = enable
        self.save_config()
        self._set_status("✅ Se iniciará minimizado con el sistema" if enable else "Inicio automático desactivado")

    def _update_autosync_indicator(self, running: bool):
        if running:
            self.autosync_dot.configure(fg=COLOR_OK)
            self.autosync_label.configure(text="Auto-Sync activo")
            self.btn_daemon.configure(text="⏸ Detener Auto-Sync", style="Stop.TButton")
        else:
            self.autosync_dot.configure(fg=COLOR_IDLE)
            self.autosync_label.configure(text="Auto-Sync detenido")
            self.btn_daemon.configure(text="▶ Iniciar Auto-Sync", style="Accent.TButton")

    def refresh_list(self):
        self._hovered_row = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        for i, pl in enumerate(self.playlists):
            st = self._load_state_info(pl)
            status = st.get("status", "Pendiente")
            tag = "row_ok"
            if "Error" in status:
                tag = "row_error"
            elif "Desfase" in status:
                tag = "row_warn"
            elif "Nuevo" in status:
                tag = "row_new"
            self.tree.insert("", "end", iid=str(i), text=pl.get("name", "Sin nombre"),
                              image=self._thumb_for(pl.get("cover_url", "")),
                              values=(
                                  st.get("tracks", "-"),
                                  st.get("local", "-"),
                                  status,
                                  st.get("last", "Nunca")
                              ), tags=(tag,))
            self._request_thumb(str(i), pl.get("cover_url", ""))

    # -- Portadas de playlist -------------------------------------------
    def _placeholder(self) -> ImageTk.PhotoImage:
        """Ícono neutro mientras se descarga (o si falla) la portada real."""
        if self._placeholder_thumb is None:
            size = 36
            img = Image.new("RGBA", (size, size), COLOR_SURFACE_ALT)
            draw = ImageDraw.Draw(img)
            # Una nota musical simple dibujada a mano: no depende de fuentes
            # del sistema, así que se ve igual en Windows y Linux.
            draw.ellipse((9, 21, 17, 29), fill=COLOR_TEXT_FAINT)
            draw.ellipse((21, 17, 29, 25), fill=COLOR_TEXT_FAINT)
            draw.rectangle((16, 9, 18, 25), fill=COLOR_TEXT_FAINT)
            draw.rectangle((28, 8, 30, 21), fill=COLOR_TEXT_FAINT)
            draw.line((16, 10, 30, 8), fill=COLOR_TEXT_FAINT, width=2)
            self._placeholder_thumb = ImageTk.PhotoImage(img)
        return self._placeholder_thumb

    def _thumb_for(self, cover_url: str) -> ImageTk.PhotoImage:
        return self._thumb_photos.get(cover_url) or self._placeholder()

    def _thumb_cache_path(self, cover_url: str) -> Path:
        cache_dir = Path(self.config.STATE_DIR) / "cover_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha1(cover_url.encode("utf-8")).hexdigest()
        return cache_dir / f"{h}.png"

    def _request_thumb(self, row_iid: str, cover_url: str):
        """Descarga (o lee de caché en disco) la portada de una playlist en
        segundo plano y actualiza esa fila cuando esté lista, sin bloquear la UI."""
        if not cover_url or cover_url in self._thumb_photos or cover_url in self._thumb_requested:
            return
        self._thumb_requested.add(cover_url)

        def work():
            try:
                cache_path = self._thumb_cache_path(cover_url)
                if cache_path.exists():
                    img = Image.open(cache_path).convert("RGBA")
                else:
                    resp = requests.get(cover_url, timeout=8)
                    resp.raise_for_status()
                    img = Image.open(BytesIO(resp.content)).convert("RGBA")
                    size = 128
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
                    img.save(cache_path, "PNG")
                thumb = img.resize((36, 36), Image.Resampling.LANCZOS)

                def apply():
                    photo = ImageTk.PhotoImage(thumb)
                    self._thumb_photos[cover_url] = photo
                    if self.tree.exists(row_iid):
                        self.tree.item(row_iid, image=photo)

                self._safe(apply)
            except Exception:
                logger.debug("No se pudo obtener la portada %s", cover_url, exc_info=True)

        threading.Thread(target=work, daemon=True).start()

    _AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".mp4"}

    def _load_state_info(self, pl: dict) -> dict:
        try:
            extractor = SpotifyExtractor()
            pid = extractor.extract_playlist_id(pl["url"])
            sp = Path(self.config.STATE_DIR) / f"{pid}.json"
            out = Path(pl.get("output", "./"))

            local_count = 0
            if out.exists():
                with os.scandir(out) as entries:
                    for entry in entries:
                        if not entry.is_file():
                            continue
                        name = entry.name
                        suffix = os.path.splitext(name)[1].lower()
                        if suffix in self._AUDIO_SUFFIXES and ".temp." not in name and ".part." not in name:
                            local_count += 1

            state_data = None
            if sp.exists():
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                except Exception:
                    state_data = None

            track_count = self._playlist_totals.get(pl["url"])
            if track_count is None:
                track_count = len(state_data.get("track_ids", [])) if state_data else 0

            status = f"OK ({local_count}/{track_count})"
            if local_count != track_count:
                status = f"Desfase ({local_count}/{track_count})"

            last = "Nunca"
            if state_data and state_data.get("last_sync"):
                last = state_data["last_sync"][:16].replace("T", " ")

            return {"tracks": track_count, "local": local_count, "last": last, "status": status}
        except Exception:
            return {"tracks": "-", "local": "-", "last": "Nunca", "status": "Error"}

    def add_playlist(self):
        url = simpledialog.askstring("Agregar Playlist", "URL de Spotify:", parent=self.root)
        if not url:
            return
        try:
            extractor = SpotifyExtractor()
            pid = extractor.extract_playlist_id(url)
            pl = extractor.get_playlist(pid)
            name = simpledialog.askstring("Nombre", "Nombre:", parent=self.root, initialvalue=pl.name)
            output = simpledialog.askstring("Carpeta", "Carpeta de salida:", parent=self.root, initialvalue=f"./{name or pl.name}")
            new_entry = {
                "url": url,
                "name": name or pl.name,
                "output": output or f"./{name or pl.name}",
                "cover_url": pl.cover_url,
            }
            self.playlists.append(new_entry)
            self.save_config()
            self.refresh_list()
            self._set_status(f"✅ Agregado: {name or pl.name} ({pl.total_tracks} tracks)")

            if not self.engine.is_daemon_running():
                self.start_autosync()
            else:
                self._run_sync(new_entry, False)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo agregar:\n{e}")

    def remove_playlist(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona una playlist.")
            return
        idx = int(sel[0])
        if messagebox.askyesno("Confirmar", f"Eliminar '{self.playlists[idx].get('name')}'?\n(Los archivos NO se borrarán)"):
            self.playlists.pop(idx)
            self.save_config()
            self.refresh_list()

    def sync_now(self):
        sel = self.tree.selection()
        if sel:
            self._run_sync(self.playlists[int(sel[0])], False)
        else:
            for pl in self.playlists:
                self._run_sync(pl, False)

    def force_download(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona una playlist.")
            return
        pl = self.playlists[int(sel[0])]
        if not messagebox.askyesno("Forzar", f"Borrar estado de '{pl.get('name')}' y re-verificar todos los archivos?\n(Los archivos existentes se conservan)"):
            return

        def run():
            try:
                extractor = SpotifyExtractor()
                pid = extractor.extract_playlist_id(pl["url"])
                sp = Path(self.config.STATE_DIR) / f"{pid}.json"
                if sp.exists():
                    sp.unlink()
                self._run_sync(pl, True)
            except Exception as e:
                err = str(e)
                self._safe(lambda: self._set_status(f"Error: {err}"))
        threading.Thread(target=run, daemon=True).start()

    def update_ytdlp(self):
        self._set_status("⟳ Actualizando yt-dlp...")

        def run():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    last_line = next((l for l in reversed(result.stdout.strip().splitlines()) if l.strip()), "OK")
                    self._safe(lambda: self._set_status(f"✅ yt-dlp actualizado: {last_line[:80]}"))
                    logger.info("yt-dlp actualizado correctamente: %s", last_line)
                else:
                    err = (result.stderr or result.stdout).strip().splitlines()
                    last = err[-1] if err else "error desconocido"
                    self._safe(lambda: self._set_status(f"❌ No se pudo actualizar yt-dlp: {last[:80]}"))
                    logger.warning("Fallo actualizando yt-dlp: %s", last)
            except Exception as e:
                err = str(e)
                self._safe(lambda: self._set_status(f"❌ Error actualizando yt-dlp: {err}"))
                logger.exception("Excepción actualizando yt-dlp")
        threading.Thread(target=run, daemon=True).start()

    def fix_metadata_now(self):
        """Corrige los metadatos de la playlist seleccionada."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona una playlist.")
            return

        pl = self.playlists[int(sel[0])]

        def run():
            self._safe(lambda: self._set_status(f"🛠 Corrigiendo metadatos de {pl.get('name', '')}..."))
            try:
                out = Path(pl["output"])
                out.mkdir(parents=True, exist_ok=True)
                result = self.engine.fix_metadata_for_playlist(pl["url"], out)
                self._safe(lambda: self._set_status(
                    f"[{pl.get('name', '')}] Metadatos corregidos: {result.get('updated', 0)} | "
                    f"sin archivo: {result.get('missing', 0)} | errores: {result.get('errors', 0)}"
                ))
                self._safe(self.refresh_list)
                # Mostrar resumen en un cuadro de diálogo
                self._safe(lambda: messagebox.showinfo(
                    "Corrección completada",
                    f"Playlist: {pl.get('name', '')}\n\n"
                    f"✅ Actualizados: {result.get('updated', 0)}\n"
                    f"⚠️ Sin archivo: {result.get('missing', 0)}\n"
                    f"❌ Errores: {result.get('errors', 0)}\n"
                    f"🔄 Reordenados: {result.get('reordered', 0)}\n\n"
                    "Revisa el log para más detalles."
                ))
            except Exception as e:
                err = str(e)
                self._safe(lambda: self._set_status(f"Error corrigiendo metadatos: {err}"))
                self._safe(lambda: messagebox.showerror("Error", f"No se pudo corregir metadatos:\n{err}"))

        threading.Thread(target=run, daemon=True).start()

    def show_missing_tracks(self):
        """Muestra un diagnóstico completo: faltantes, colisiones y huérfanos."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona una playlist.")
            return

        pl = self.playlists[int(sel[0])]

        def run():
            try:
                out = Path(pl["output"])
                result = self.engine.get_missing_tracks(pl["url"], out)

                missing = result.get("missing", [])
                collisions = result.get("collisions", [])
                orphans = result.get("orphans", [])
                total = result.get("total_tracks", 0)
                unique = result.get("unique_files", 0)

                message = f"Total canciones en Spotify: {total}\n"
                message += f"Archivos únicos asignados: {unique}\n"
                if orphans:
                    message += f"Archivos huérfanos en carpeta: {len(orphans)}\n"
                message += "\n"

                if missing:
                    message += f"❌ Faltan ({len(missing)}):\n"
                    for item in missing:
                        message += f"• #{item['position']}: {item['name']}\n"
                    message += "\n\n"
                if collisions:
                    message += f"⚠️ Colisiones (comparten archivo) ({len(collisions)}):\n"
                    for col in collisions:
                        message += f"📄 {col['file']}\n"
                        for track_info in col['tracks']:
                            message += f"   - {track_info}\n"
                        message += "\n"
                if orphans:
                    message += "🗑 Huérfanos (no están en la playlist):\n"
                    for name in orphans:
                        message += f"• {name}\n"
                    message += "\n"
                if not missing and not collisions and not orphans:
                    message += "✅ Todas las canciones tienen archivo único y están descargadas."

                self._safe(lambda: messagebox.showinfo("Diagnóstico de descargas", message))
            except Exception as e:
                err = str(e)
                self._safe(lambda: messagebox.showerror("Error", f"No se pudo analizar:\n{err}"))

        threading.Thread(target=run, daemon=True).start()

    def _run_sync(self, pl: dict, force: bool):
        def run():
            self._safe(lambda: self._set_status(f"🔄 Sincronizando: {pl.get('name', '')}..."))
            try:
                out = Path(pl["output"])
                out.mkdir(parents=True, exist_ok=True)
                result = self.engine.sync(pl["url"], out, force=force)
                self._handle_result(pl.get("name", ""), pl["url"], result)
            except Exception as e:
                self._handle_result(pl.get("name", ""), pl["url"], {"error": str(e)})
        threading.Thread(target=run, daemon=True).start()

    def _handle_result(self, name: str, url: str, result: dict):
        if "error" in result:
            msg = f"❌ Error: {result['error'][:60]}"
        elif result.get("status") == "no_changes":
            msg = f"✅ Sin cambios ({result.get('total_tracks', 0)} tracks)"
        else:
            msg = (f"⬇ +{result.get('downloaded', 0)} descargados, -{result.get('deleted', 0)} eliminados, "
                   f"{result.get('failed', 0)} fallos, {result.get('already_have', 0)} ya tenías, "
                   f"{result.get('reordered', 0)} reordenados")
        self._safe(lambda: self._set_status(f"[{name}] {msg}" if name else msg))
        self._safe(lambda: self.progress_var.set(0))
        self._safe(lambda: self.progress_label.configure(text="Listo"))

        # Actualizar total en caché si hay datos reales
        if "total_tracks" in result and not result.get("error"):
            self._safe(lambda: self._playlist_totals.update({url: result["total_tracks"]}))

        self._safe(lambda: self._backfill_cover_url(url, result))
        self._safe(self.refresh_list)

        # Mostrar popup con fallos solo si hay fallos y no es auto-sync
        failed = result.get("failed_tracks", [])
        if failed:
            list_text = "\n".join(f"• {t}" for t in failed)
            self._safe(lambda: messagebox.showwarning(
                "Descargas fallidas",
                f"Las siguientes canciones no se pudieron descargar:\n\n{list_text}"
            ))

    def start_autosync(self):
        if not self.playlists:
            messagebox.showwarning("Atención", "Agrega al menos una playlist para iniciar el Auto-Sync.")
            return
        self._apply_settings_to_config()
        self.engine = SyncEngine(
            self.config,
            progress_cb=self.on_progress,
            status_cb=self.on_status,
            playlists_source=lambda: list(self.playlists),
            ui_update_cb=self._on_auto_sync_result
        )
        self.engine.start_daemon(self.playlists)
        self._update_autosync_indicator(True)
        self.settings["auto_sync_enabled"] = True
        self.save_config()
        self._set_status(f"🟢 Auto-sync activo (cada {self.config.SYNC_INTERVAL}s)")

    def _on_auto_sync_result(self, url: str, result: dict):
        if "total_tracks" in result and not result.get("error"):
            self._safe(lambda: self._playlist_totals.update({url: result["total_tracks"]}))
        self._safe(lambda: self._backfill_cover_url(url, result))
        self._safe(self.refresh_list)

    def _backfill_cover_url(self, url: str, result: dict):
        """Playlists agregadas antes de esta versión no tienen cover_url
        guardada. En cuanto una sincronización la trae, se guarda una sola
        vez para no tener que volver a pedirla."""
        cover_url = result.get("cover_url")
        if not cover_url:
            return
        for pl in self.playlists:
            if pl.get("url") == url and not pl.get("cover_url"):
                pl["cover_url"] = cover_url
                self._schedule_save_config()
                break

    def stop_autosync(self):
        self.engine.stop_daemon()
        self._update_autosync_indicator(False)
        self.settings["auto_sync_enabled"] = False
        self.save_config()
        self._set_status("⏸ Auto-sync detenido")

    def toggle_daemon(self):
        if self.engine.is_daemon_running():
            self.stop_autosync()
        else:
            self.start_autosync()

    def on_progress(self, current: int, total: int, track_name: str):
        self._safe(lambda: self._update_progress(current, total, track_name))

    def _update_progress(self, current: int, total: int, track_name: str):
        if total > 0:
            self.progress_var.set((current / total) * 100)
            self.progress_label.configure(text=f"{current}/{total}: {track_name[:40]}")
        else:
            self.progress_var.set(0)
            self.progress_label.configure(text=track_name[:50])

    def on_status(self, name: str, message: str):
        text = f"[{name}] {message}" if name else message
        self._safe(lambda: self._set_status(text))

    def _safe(self, func):
        self._pending.append(func)

    def _process_queue(self):
        had_items = bool(self._pending)
        while self._pending:
            func = self._pending.pop(0)
            try:
                func()
            except Exception:
                pass
        # Busy: keep the tick fast so status/progress feel instant.
        # Idle: back off to cut needless wake-ups while nothing changes.
        next_delay = 80 if had_items else 250
        self._queue_poll_job = self.root.after(next_delay, self._process_queue)

    def on_close(self):
        if PYSTRAY_AVAILABLE:
            self.hide_to_tray()
        else:
            self.exit_app()

    def _on_unmap(self, event):
        if event.widget is not self.root:
            return
        if PYSTRAY_AVAILABLE and self.root.state() == "iconic":
            self.root.after(10, self.hide_to_tray)

    def hide_to_tray(self, silent: bool = False):
        if not PYSTRAY_AVAILABLE:
            if silent:
                self.root.iconify()
            else:
                extra = ""
                if IS_LINUX:
                    extra = "\n\nEn Linux instala: sudo apt install libappindicator3-1"
                messagebox.showwarning("Bandeja", f"pystray no instalado. pip install pystray Pillow{extra}")
            return
        self.root.withdraw()
        self._setup_tray()

    def show_from_tray(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.deiconify()
        self.root.lift()

    def _setup_tray(self):
        if self.tray_icon:
            return
        import pystray
        from PIL import Image, ImageDraw

        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(29, 185, 84, 255))
        draw.ellipse([18, 38, 28, 48], fill=(255, 255, 255, 255))
        draw.rectangle([26, 18, 29, 42], fill=(255, 255, 255, 255))
        draw.rectangle([29, 18, 42, 21], fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", lambda: self.root.after(0, self.show_from_tray), default=True),
            pystray.MenuItem("Sincronizar ahora", lambda: self.root.after(0, self.sync_now)),
            pystray.MenuItem(
                lambda item: "Detener Auto-Sync" if self.engine.is_daemon_running() else "Iniciar Auto-Sync",
                lambda: self.root.after(0, self.toggle_daemon)
            ),
            pystray.MenuItem("Salir", lambda: self.root.after(0, self.exit_app))
        )
        self.tray_icon = pystray.Icon("spotify_sync", img, "Spotify Sync", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def exit_app(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self.engine.stop_daemon()
        self.root.destroy()
        sys.exit(0)


def main():
    start_minimized = any(arg.lower() in ("--minimized", "-m") for arg in sys.argv[1:])
    root = tk.Tk()
    app = SpotifySyncApp(root, start_minimized=start_minimized)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Cualquier otro fallo al arrancar (no solo dependencias faltantes)
        # queda en el .log Y se muestra en pantalla — nunca en silencio.
        logger.exception("Fallo no controlado al iniciar Spotify Sync")
        try:
            _err_root = tk.Tk()
            _err_root.withdraw()
            messagebox.showerror(
                "Spotify Sync — Error al iniciar",
                "Ocurrió un error inesperado al iniciar la aplicación.\n\n"
                f"Detalle: {sys.exc_info()[1]}\n\n"
                f"El registro completo quedó en:\n{LOG_FILE}"
            )
            _err_root.destroy()
        except Exception:
            pass
        sys.exit(1)