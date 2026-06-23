#!/usr/bin/env python3
"""
Spotify Sync GUI - Cross-platform (Windows & Linux)
- Auto-Sync se activa automaticamente al agregar una playlist
- Intervalo por defecto: 30s
- Opcion de inicio automatico con el sistema (minimizado a la bandeja)
- UI mejorada: mas clara, agrupada y con indicadores de estado
Requiere: pip install requests spotifyscraper yt-dlp pystray Pillow (+ ffmpeg en el PATH)

Linux extra:
    sudo apt install python3-tk python3-pil.imagetk libappindicator3-1   (Debian/Ubuntu/Mint)
    sudo pacman -S python tk libappindicator-gtk3                        (Arch)
"""

import gc
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import List, Optional, Dict, Any, Callable

import importlib.util

import requests

# Solo comprobamos que esten instalados (sin importarlos todavia) para no cargar
# pystray/Pillow en memoria hasta el momento en que realmente se minimice a la
# bandeja. Esto reduce el consumo de RAM en reposo si el usuario nunca usa la bandeja.
PYSTRAY_AVAILABLE = (
    importlib.util.find_spec("pystray") is not None
    and importlib.util.find_spec("PIL") is not None
)

# Windows-only flag
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
CONFIG_FILE = "spotify_sync_config.json"
APP_STARTUP_NAME = "SpotifySync"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# Inicio automatico con el sistema (Windows: registro | Linux: .desktop)
# ---------------------------------------------------------------------------

def _run_vbs_path() -> Path:
    return Path(__file__).resolve().parent / "run.vbs"


def _desktop_entry_path() -> Path:
    """Ruta al archivo .desktop para autostart en Linux."""
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    return autostart_dir / f"{APP_STARTUP_NAME}.desktop"


def is_startup_enabled() -> bool:
    """Indica si el inicio automatico esta activo actualmente."""
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\Windows\CurrentVersion\Run",
                                  0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, APP_STARTUP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False
    elif IS_LINUX:
        return _desktop_entry_path().exists()
    return False


def get_startup_command() -> Optional[str]:
    """Devuelve el comando guardado actualmente (o None)."""
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\Windows\CurrentVersion\Run",
                                  0, winreg.KEY_READ)
            try:
                value, _ = winreg.QueryValueEx(key, APP_STARTUP_NAME)
                return value
            except FileNotFoundError:
                return None
            finally:
                winreg.CloseKey(key)
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
    """Activa/desactiva el inicio automatico. True si tuvo exito."""
    if IS_WINDOWS:
        try:
            import winreg
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                      r"Software\Microsoft\Windows\CurrentVersion\Run",
                                      0, winreg.KEY_ALL_ACCESS)
            except FileNotFoundError:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                        r"Software\Microsoft\Windows\CurrentVersion\Run")
            try:
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
            finally:
                winreg.CloseKey(key)
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


class ToolTip:
    """Tooltip simple para botones/checkboxes, mejora la claridad de la UI."""
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


class Config:
    OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "mp3")
    AUDIO_QUALITY = os.getenv("AUDIO_QUALITY", "0")
    THREADS = int(os.getenv("THREADS", "4"))
    SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "30"))
    DELETE_REMOVED = os.getenv("DELETE_REMOVED", "true").lower() == "true"
    SKIP_EXISTING = os.getenv("SKIP_EXISTING", "true").lower() == "true"
    SKIP_EXPLICIT = os.getenv("SKIP_EXPLICIT", "false").lower() == "true"
    MIN_POPULARITY = int(os.getenv("MIN_POPULARITY", "0"))
    BLOCKED_ARTISTS = [a.strip().lower() for a in os.getenv("BLOCKED_ARTISTS", "").split(",") if a.strip()]
    STATE_DIR = os.getenv("STATE_DIR", ".spotify_sync_state")
    YTDLP_EXTRA = os.getenv("YTDLP_EXTRA", "")


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

    @property
    def display_name(self) -> str:
        return f"{self.name} - {', '.join(self.artists)}"

    @property
    def legacy_display_name(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"

    @property
    def safe_filename(self) -> str:
        name = self.display_name
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        return name.strip('. ')[:120]

    @property
    def legacy_safe_filename(self) -> str:
        name = self.legacy_display_name
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        return name.strip('. ')[:120]

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


class SpotifyExtractor:
    def __init__(self):
        self._has_scraper = self._check_scraper()

    def _check_scraper(self) -> bool:
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

    def get_playlist(self, playlist_id: str) -> Playlist:
        if self._has_scraper:
            try:
                from spotify_scraper import SpotifyClient
                with SpotifyClient() as client:
                    pl = client.get_playlist(playlist_id, max_tracks=10000)
                    pl_dict = pl.to_dict() if hasattr(pl, 'to_dict') else pl
                    name = self._get_attr(pl_dict, pl, 'name', 'Playlist')
                    owner = ''
                    if hasattr(pl, 'owner') and pl.owner:
                        owner = self._get_attr(pl.owner, None, 'name', '') or self._get_attr(pl.owner, None, 'display_name', '')
                    elif isinstance(pl_dict, dict) and 'owner' in pl_dict:
                        owner = pl_dict['owner'].get('name', '')

                    tracks = []
                    tracks_data = []
                    if hasattr(pl, 'tracks') and pl.tracks:
                        tracks_data = pl.tracks
                    elif isinstance(pl_dict, dict) and 'tracks' in pl_dict:
                        tracks_data = pl_dict['tracks']

                    for i, pt in enumerate(tracks_data):
                        try:
                            if hasattr(pt, 'track') and pt.track:
                                track_obj = pt.track
                                track_dict = track_obj.to_dict() if hasattr(track_obj, 'to_dict') else track_obj
                            elif isinstance(pt, dict) and 'track' in pt:
                                track_dict = pt['track']
                                track_obj = None
                            else:
                                track_obj = pt
                                track_dict = pt.to_dict() if hasattr(pt, 'to_dict') else pt

                            track_id = self._get_attr(track_dict, track_obj, 'id', '')
                            if not track_id and hasattr(track_obj, 'uri'):
                                track_id = track_obj.uri.split(':')[-1] if track_obj.uri else ''
                            if not track_id:
                                track_id = f"track_{i}"

                            track_name = self._get_attr(track_dict, track_obj, 'name', 'Unknown')
                            artists = []
                            artists_data = self._get_attr(track_dict, track_obj, 'artists', [])
                            if artists_data:
                                for a in artists_data:
                                    if hasattr(a, 'name'):
                                        artists.append(a.name)
                                    elif isinstance(a, dict):
                                        artists.append(a.get('name', 'Unknown'))
                                    elif isinstance(a, str):
                                        artists.append(a)

                            album = ''
                            album_data = self._get_attr(track_dict, track_obj, 'album', None)
                            if album_data:
                                if hasattr(album_data, 'name'):
                                    album = album_data.name
                                elif isinstance(album_data, dict):
                                    album = album_data.get('name', '')

                            duration_ms = 0
                            if hasattr(track_obj, 'duration_ms'):
                                duration_ms = track_obj.duration_ms
                            elif isinstance(track_dict, dict):
                                duration_ms = track_dict.get('duration_ms', 0)

                            explicit = False
                            if hasattr(track_obj, 'explicit'):
                                explicit = bool(track_obj.explicit)
                            elif isinstance(track_dict, dict):
                                explicit = bool(track_dict.get('explicit', False))

                            popularity = 50
                            if hasattr(track_obj, 'popularity'):
                                popularity = track_obj.popularity
                            elif isinstance(track_dict, dict):
                                popularity = track_dict.get('popularity', 50)

                            tracks.append(Track(
                                id=track_id, name=track_name,
                                artists=artists if artists else ["Unknown Artist"],
                                album=album, duration_ms=duration_ms,
                                explicit=explicit, popularity=popularity,
                                spotify_url=f"https://open.spotify.com/track/{track_id}"
                            ))
                        except Exception:
                            continue

                    return Playlist(
                        id=playlist_id, name=name, owner=owner,
                        tracks=tracks, total_tracks=len(tracks),
                        spotify_url=f"https://open.spotify.com/playlist/{playlist_id}",
                        snapshot_id=self._get_attr(pl_dict, pl, 'snapshot_id', f"scraper_{int(time.time())}")
                    )
            except Exception:
                pass

        raise RuntimeError(f"No se pudo extraer la playlist {playlist_id}. Instala spotifyscraper: pip install spotifyscraper")

    def extract_playlist_id(self, url: str) -> str:
        for p in [r"spotify:playlist:([a-zA-Z0-9]+)", r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", r"spotify\.com/playlist/([a-zA-Z0-9]+)"]:
            m = re.search(p, url)
            if m:
                return m.group(1)
        raise ValueError(f"URL invalida: {url}")


class Downloader:
    def __init__(self, output_dir: Path, config: Config):
        self.output_dir = output_dir
        self.config = config
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = threading.Event()

    def _force_kill(self, pid: int):
        """Mata el proceso y todos sus hijos de forma cross-platform."""
        if IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass

    def cancel(self):
        """Fuerza la terminacion del proceso yt-dlp actual (y subprocesos)."""
        self._cancelled.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._force_kill(proc.pid)

    def find_existing(self, track: Track) -> Optional[Path]:
        safe = track.safe_filename
        for ext in [".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"]:
            candidate = self.output_dir / f"{safe}{ext}"
            if candidate.exists():
                return candidate

        legacy = track.legacy_safe_filename
        for ext in [".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"]:
            candidate = self.output_dir / f"{legacy}{ext}"
            if candidate.exists():
                return candidate
        return None

    def download(self, track: Track) -> Optional[Path]:
        existing = self.find_existing(track)
        if existing and self.config.SKIP_EXISTING:
            return existing

        self._cancelled.clear()

        output_template = str(self.output_dir / f"{track.safe_filename}.%(ext)s")

        cmd = [
            "yt-dlp", "-x", "--audio-format", self.config.OUTPUT_FORMAT,
            "--audio-quality", self.config.AUDIO_QUALITY, "-o", output_template,
            "--embed-metadata", "--embed-thumbnail", "--add-metadata",
            "--parse-metadata", "%(title)s:%(meta_title)s",
            "--parse-metadata", "%(uploader)s:%(meta_artist)s",
            "--sponsorblock-remove", "all",
            "--concurrent-fragments", str(self.config.THREADS),
            "--no-overwrites", "--ignore-errors", "--no-progress",
            "--no-warnings", "--quiet",
            "--default-search", "ytsearch1",
            f"ytsearch1:{track.search_query}",
        ]
        if self.config.YTDLP_EXTRA:
            cmd.extend(self.config.YTDLP_EXTRA.split())

        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if IS_WINDOWS:
                kwargs["creationflags"] = CREATE_NO_WINDOW
            else:
                kwargs["preexec_fn"] = os.setsid

            self._proc = subprocess.Popen(cmd, **kwargs)
            pid = self._proc.pid

            return_code = None
            try:
                while self._proc.poll() is None:
                    if self._cancelled.is_set():
                        self._force_kill(pid)
                        return None
                    time.sleep(0.2)
                return_code = self._proc.returncode
            finally:
                self._proc = None

            if return_code != 0:
                return self._retry(track)
            downloaded = self.find_existing(track)
            if downloaded:
                return downloaded
            return None
        except Exception:
            return None

    def _retry(self, track: Track) -> Optional[Path]:
        queries = [
            f"{track.legacy_display_name}",
            f"{track.name} {track.artists[0] if track.artists else ''} audio",
        ]
        for q in queries:
            if self._cancelled.is_set():
                return None

            output_template = str(self.output_dir / f"{track.safe_filename}.%(ext)s")
            cmd = [
                "yt-dlp", "-x", "--audio-format", self.config.OUTPUT_FORMAT,
                "--audio-quality", self.config.AUDIO_QUALITY, "-o", output_template,
                "--embed-metadata", "--no-overwrites", "--ignore-errors",
                "--no-warnings", "--quiet", "--default-search", "ytsearch1",
                f"ytsearch1:{q}",
            ]
            try:
                kwargs = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if IS_WINDOWS:
                    kwargs["creationflags"] = CREATE_NO_WINDOW
                else:
                    kwargs["preexec_fn"] = os.setsid

                self._proc = subprocess.Popen(cmd, **kwargs)
                pid = self._proc.pid

                return_code = None
                try:
                    while self._proc.poll() is None:
                        if self._cancelled.is_set():
                            self._force_kill(pid)
                            return None
                        time.sleep(0.2)
                    return_code = self._proc.returncode
                finally:
                    self._proc = None

                if return_code == 0:
                    existing = self.find_existing(track)
                    if existing:
                        return existing
            except Exception:
                pass
            if self._cancelled.is_set():
                return None
            time.sleep(1)
        return None


class SyncEngine:
    def __init__(self, config: Config, progress_cb=None, status_cb=None, playlists_source: Optional[Callable[[], List[dict]]] = None):
        self.config = config
        self.spotify = SpotifyExtractor()
        self.state_dir = Path(config.STATE_DIR)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.progress_cb = progress_cb
        self.status_cb = status_cb
        self._daemon_running = False
        self._daemon_thread = None
        self._playlists_source = playlists_source
        self._stop_event: Optional[threading.Event] = None
        self._current_downloader: Optional[Downloader] = None
        self._downloader_lock = threading.Lock()

    def _state_file(self, playlist_id: str) -> Path:
        return self.state_dir / f"{playlist_id}.json"

    def _load_state(self, playlist_id: str) -> dict:
        path = self._state_file(playlist_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"track_ids": [], "files": {}, "snapshot_id": ""}

    def _save_state(self, playlist_id: str, state: dict):
        try:
            with open(self._state_file(playlist_id), "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _filter_track(self, track: Track) -> bool:
        if self.config.SKIP_EXPLICIT and track.explicit:
            return False
        if track.popularity < self.config.MIN_POPULARITY:
            return False
        for blocked in self.config.BLOCKED_ARTISTS:
            if any(blocked in a.lower() for a in track.artists):
                return False
        return True

    def sync(self, playlist_url: str, output_dir: Path, force: bool = False, stop_event: Optional[threading.Event] = None) -> dict:
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

        missing_files = []
        for tid, fname in prev_files.items():
            if tid in current_ids and not (output_dir / fname).exists():
                missing_files.append(tid)

        downloader = Downloader(output_dir, self.config)
        for track in tracks:
            if track.id in current_ids and not downloader.find_existing(track):
                if track.id not in missing_files:
                    missing_files.append(track.id)

        has_changes = bool(new_ids or removed_ids or missing_files or force)

        if self.status_cb:
            self.status_cb("", f"Playlist: {playlist.name} | Tracks: {len(tracks)} | Nuevas: {len(new_ids)} | Eliminar: {len(removed_ids)} | Faltan: {len(missing_files)}")

        if not has_changes:
            self._save_state(playlist_id, {
                "playlist_name": playlist.name,
                "last_sync": datetime.now().isoformat(),
                "snapshot_id": playlist.snapshot_id,
                "track_ids": list(current_ids),
                "files": {tid: fname for tid, fname in prev_files.items() if tid in current_ids and (output_dir / fname).exists()}
            })
            return {"status": "no_changes", "playlist_name": playlist.name, "total_tracks": len(tracks)}

        files_map = {}
        for track in tracks:
            existing = downloader.find_existing(track)
            if existing:
                files_map[track.id] = str(existing.name)

        for tid, fname in prev_files.items():
            if tid in current_ids and (output_dir / fname).exists():
                files_map[tid] = fname

        tracks_to_download = [t for t in tracks if t.id in new_ids or t.id not in files_map]
        total_to_download = len(tracks_to_download)

        downloaded = 0
        failed = 0

        with self._downloader_lock:
            self._current_downloader = downloader
        try:
            for i, track in enumerate(tracks_to_download):
                if stop_event and stop_event.is_set():
                    if self.status_cb:
                        self.status_cb("", "Cancelado por el usuario")
                    break

                if self._playlists_source:
                    active_playlists = self._playlists_source()
                    active_ids = {self.spotify.extract_playlist_id(p["url"]) for p in active_playlists}
                    if playlist_id not in active_ids:
                        if self.status_cb:
                            self.status_cb("", "Playlist eliminada, deteniendo sincronizacion")
                        break

                if self.progress_cb:
                    self.progress_cb(i + 1, total_to_download, track.display_name)

                result = downloader.download(track)
                if result:
                    files_map[track.id] = str(result.name)
                    downloaded += 1
                else:
                    failed += 1

                if stop_event and stop_event.wait(1.5):
                    break
        finally:
            with self._downloader_lock:
                self._current_downloader = None

        deleted = 0
        if self.config.DELETE_REMOVED:
            for rid in removed_ids:
                if rid in prev_files:
                    file_path = output_dir / prev_files[rid]
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted += 1
                        except Exception:
                            pass
                    if rid in files_map:
                        del files_map[rid]

        self._save_state(playlist_id, {
            "playlist_name": playlist.name,
            "last_sync": datetime.now().isoformat(),
            "snapshot_id": playlist.snapshot_id,
            "track_ids": list(current_ids),
            "files": {tid: fname for tid, fname in files_map.items() if tid in current_ids}
        })

        gc.collect()

        return {
            "playlist_name": playlist.name,
            "total_tracks": len(tracks),
            "downloaded": downloaded,
            "failed": failed,
            "deleted": deleted,
            "skipped_filters": skipped,
            "already_have": len(tracks) - total_to_download,
            "output_dir": str(output_dir)
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
                if not self._daemon_running or (self._stop_event and self._stop_event.is_set()):
                    break
                try:
                    if self.status_cb:
                        self.status_cb(pl.get("name", ""), "Verificando cambios...")
                    out = Path(pl["output"])
                    out.mkdir(parents=True, exist_ok=True)
                    result = self.sync(pl["url"], out, stop_event=self._stop_event)
                    if self.status_cb:
                        if "error" in result:
                            self.status_cb(pl.get("name", ""), f"Error: {result['error'][:50]}")
                        elif result.get("status") == "no_changes":
                            self.status_cb(pl.get("name", ""), "Sin cambios")
                        else:
                            self.status_cb(pl.get("name", ""), f"+{result['downloaded']} -{result['deleted']} fail:{result['failed']} have:{result['already_have']}")
                except Exception as e:
                    if self.status_cb:
                        self.status_cb(pl.get("name", ""), f"Error: {str(e)[:50]}")
            if self._daemon_running and (self._stop_event and not self._stop_event.is_set()):
                if self.status_cb:
                    self.status_cb("", "Esperando...")
                slept = 0
                while slept < self.config.SYNC_INTERVAL and self._daemon_running:
                    if self._stop_event and self._stop_event.wait(1.0):
                        break
                    slept += 1

    def stop_daemon(self):
        self._daemon_running = False
        if self._stop_event:
            self._stop_event.set()
        with self._downloader_lock:
            dl = self._current_downloader
        if dl:
            dl.cancel()
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5)
        self._stop_event = None

    def is_daemon_running(self) -> bool:
        return self._daemon_running


# ---------------------------------------------------------------------------
# Colores / estilo de la interfaz
# ---------------------------------------------------------------------------
COLOR_BG_HEADER = "#121212"
COLOR_ACCENT = "#1DB954"
COLOR_TEXT_LIGHT = "#FFFFFF"
COLOR_TEXT_MUTED = "#A0A0A0"
COLOR_OK = "#1DB954"
COLOR_WARN = "#F2A93B"
COLOR_ERROR = "#E5484D"
COLOR_INFO = "#3D91F4"
COLOR_IDLE = "#8A8A8A"


class SpotifySyncApp:
    def __init__(self, root: tk.Tk, start_minimized: bool = False):
        self.root = root
        self.root.title("Spotify Sync")
        self.root.geometry("900x680")
        self.root.minsize(780, 580)

        self.config = Config()
        self.engine = SyncEngine(self.config, progress_cb=self.on_progress, status_cb=self.on_status)
        self.playlists: List[dict] = []
        self.settings: Dict[str, Any] = {
            "interval": Config.SYNC_INTERVAL,
            "format": Config.OUTPUT_FORMAT,
            "delete_removed": Config.DELETE_REMOVED,
            "auto_sync_enabled": False,
        }
        self.tray_icon = None
        self._pending = []

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

    def _setup_style(self):
        style = ttk.Style()
        for theme in ("vista", "clam"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Toolbar.TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))

    def check_dependencies(self):
        missing = []
        if importlib.util.find_spec("spotify_scraper") is None:
            missing.append("spotifyscraper")
        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "check": True,
            }
            if IS_WINDOWS:
                kwargs["creationflags"] = CREATE_NO_WINDOW
            subprocess.run(["yt-dlp", "--version"], **kwargs)
        except Exception:
            missing.append("yt-dlp")
        if not PYSTRAY_AVAILABLE:
            missing.append("pystray")
        if missing:
            extra_msg = ""
            if IS_LINUX:
                extra_msg = ("\n\nEn Linux, asegurate de tener tambien:\n"
                             "  sudo apt install python3-tk libappindicator3-1   (Debian/Ubuntu/Mint)\n"
                             "  sudo pacman -S python tk libappindicator-gtk3      (Arch)")
            messagebox.showwarning(
                "Dependencias faltantes",
                f"Faltan los siguientes paquetes: {', '.join(missing)}\n\n"
                f"Instalalos con:\npip install {' '.join(missing)}{extra_msg}"
            )

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.playlists = data.get("playlists", [])
                    self.settings.update(data.get("settings", {}))
            except Exception:
                self.playlists = []

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"playlists": self.playlists, "settings": self.settings}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def build_ui(self):
        self.root.configure(bg=COLOR_BG_HEADER)
        main = ttk.Frame(self.root, padding="14")
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_header(main)
        self._build_toolbar(main)
        self._build_playlist_tree(main)
        self._build_progress(main)
        self._build_settings(main)
        self._build_statusbar(main)

        self.refresh_list()

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=COLOR_BG_HEADER, padx=14, pady=10)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        hdr.columnconfigure(1, weight=1)

        title_box = tk.Frame(hdr, bg=COLOR_BG_HEADER)
        title_box.grid(row=0, column=0, sticky="w")
        tk.Label(title_box, text="🎵 Spotify Sync", font=("Segoe UI", 16, "bold"),
                 fg=COLOR_TEXT_LIGHT, bg=COLOR_BG_HEADER).pack(anchor="w")
        tk.Label(title_box, text="Descarga y mantiene tus playlists sincronizadas automaticamente",
                 font=("Segoe UI", 9), fg=COLOR_TEXT_MUTED, bg=COLOR_BG_HEADER).pack(anchor="w")

        status_box = tk.Frame(hdr, bg=COLOR_BG_HEADER)
        status_box.grid(row=0, column=2, sticky="e")
        self.autosync_dot = tk.Label(status_box, text="●", font=("Segoe UI", 13),
                                      fg=COLOR_IDLE, bg=COLOR_BG_HEADER)
        self.autosync_dot.pack(side="left", padx=(0, 6))
        self.autosync_label = tk.Label(status_box, text="Auto-Sync: Detenido", font=("Segoe UI", 10, "bold"),
                                        fg=COLOR_TEXT_LIGHT, bg=COLOR_BG_HEADER)
        self.autosync_label.pack(side="left")

    def _build_toolbar(self, parent):
        tb = ttk.Frame(parent)
        tb.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        b_add = ttk.Button(tb, text="➕ Agregar Playlist", style="Toolbar.TButton", command=self.add_playlist)
        b_add.pack(side="left", padx=(0, 4))
        ToolTip(b_add, "Agrega una playlist por URL. El Auto-Sync se activa al instante.")

        b_del = ttk.Button(tb, text="🗑 Eliminar", style="Toolbar.TButton", command=self.remove_playlist)
        b_del.pack(side="left", padx=4)
        ToolTip(b_del, "Quita la playlist seleccionada de la lista (no borra archivos)")

        b_sync = ttk.Button(tb, text="🔄 Sincronizar Ahora", style="Toolbar.TButton", command=self.sync_now)
        b_sync.pack(side="left", padx=4)
        ToolTip(b_sync, "Sincroniza la playlist seleccionada (o todas si no hay seleccion)")

        b_force = ttk.Button(tb, text="⬇ Forzar Descarga", style="Toolbar.TButton", command=self.force_download)
        b_force.pack(side="left", padx=4)
        ToolTip(b_force, "Reescanea toda la playlist seleccionada, conservando archivos existentes")

        self.btn_daemon = ttk.Button(tb, text="▶ Iniciar Auto-Sync", style="Primary.TButton", command=self.toggle_daemon)
        self.btn_daemon.pack(side="right")
        ToolTip(self.btn_daemon, "Activa/desactiva la sincronizacion automatica periodica")

    def _build_playlist_tree(self, parent):
        lf = ttk.LabelFrame(parent, text="Tus Playlists", padding="6")
        lf.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        cols = ("name", "tracks", "local", "status", "last")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="browse")
        headings = {
            "name": ("Nombre", 260, "w"),
            "tracks": ("En Spotify", 90, "center"),
            "local": ("Locales", 90, "center"),
            "status": ("Estado", 150, "center"),
            "last": ("Ultimo Sync", 150, "center"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.tag_configure("row_ok", foreground=COLOR_OK)
        self.tree.tag_configure("row_warn", foreground=COLOR_WARN)
        self.tree.tag_configure("row_error", foreground=COLOR_ERROR)
        self.tree.tag_configure("row_new", foreground=COLOR_INFO)

        hint = ttk.Label(lf, text="Tip: doble clic en una playlist para sincronizarla de inmediato.",
                          font=("Segoe UI", 8))
        hint.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.tree.bind("<Double-1>", lambda e: self.sync_now())

    def _build_progress(self, parent):
        pf = ttk.Frame(parent)
        pf.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        pf.columnconfigure(0, weight=1)
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(pf, variable=self.progress_var, maximum=100).grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress_label = ttk.Label(pf, text="Listo", width=42, anchor="e")
        self.progress_label.grid(row=0, column=1, sticky="e")

    def _build_settings(self, parent):
        wrap = ttk.Frame(parent)
        wrap.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        wrap.columnconfigure(0, weight=3)
        wrap.columnconfigure(1, weight=2)

        cf = ttk.LabelFrame(wrap, text="Sincronizacion automatica", padding="10")
        cf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ttk.Label(cf, text="Intervalo:").grid(row=0, column=0, sticky="w")
        self.interval_var = tk.StringVar(value=str(self.settings.get("interval", 30)))
        spin = ttk.Spinbox(cf, from_=10, to=3600, textvariable=self.interval_var, width=7)
        spin.grid(row=0, column=1, sticky="w", padx=(6, 4))
        ttk.Label(cf, text="segundos").grid(row=0, column=2, sticky="w")
        ToolTip(spin, "Cada cuanto tiempo se revisan cambios en las playlists (por defecto 30s)")

        ttk.Label(cf, text="Formato:").grid(row=0, column=3, sticky="w", padx=(18, 0))
        self.format_var = tk.StringVar(value=self.settings.get("format", "mp3"))
        combo = ttk.Combobox(cf, textvariable=self.format_var, values=["mp3", "m4a", "flac", "opus"],
                              width=8, state="readonly")
        combo.grid(row=0, column=4, sticky="w", padx=(6, 0))

        self.delete_var = tk.BooleanVar(value=self.settings.get("delete_removed", True))
        chk_del = ttk.Checkbutton(cf, text="Eliminar canciones que se quitaron de la playlist",
                                   variable=self.delete_var, command=self._on_settings_changed)
        chk_del.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ToolTip(chk_del, "Si una cancion se quita de Spotify, tambien se borra el archivo local")

        self.interval_var.trace_add("write", lambda *a: self._on_settings_changed())
        self.format_var.trace_add("write", lambda *a: self._on_settings_changed())

        sf = ttk.LabelFrame(wrap, text="Inicio con el sistema", padding="10")
        sf.grid(row=0, column=1, sticky="nsew")

        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        startup_text = "Iniciar con Windows (minimizado)" if IS_WINDOWS else "Iniciar con el sistema (minimizado)"
        chk_startup = ttk.Checkbutton(sf, text=startup_text,
                                       variable=self.startup_var, command=self.on_toggle_startup)
        chk_startup.grid(row=0, column=0, sticky="w")
        ToolTip(chk_startup, "La app arrancara junto con el sistema, directo en la bandeja del sistema")

        ttk.Label(sf, text="Reanuda el Auto-Sync automaticamente si estaba activo.",
                  font=("Segoe UI", 8), foreground="#666666", wraplength=220, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        if not (IS_WINDOWS or IS_LINUX):
            chk_startup.configure(state="disabled")

    def _build_statusbar(self, parent):
        self.status_var = tk.StringVar(value="Listo")
        bar = ttk.Frame(parent, relief="sunken")
        bar.grid(row=5, column=0, sticky="ew")
        ttk.Label(bar, textvariable=self.status_var, anchor="w", padding=(6, 4)).pack(side="left", fill="x", expand=True)

    def _on_settings_changed(self):
        try:
            self.settings["interval"] = max(10, int(self.interval_var.get()))
        except (ValueError, tk.TclError):
            pass
        self.settings["format"] = self.format_var.get()
        self.settings["delete_removed"] = bool(self.delete_var.get())
        self.save_config()
        if self.engine.is_daemon_running():
            self.config.SYNC_INTERVAL = self.settings["interval"]
            self.config.OUTPUT_FORMAT = self.settings["format"]
            self.config.DELETE_REMOVED = self.settings["delete_removed"]

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
            messagebox.showerror("No disponible",
                                  "No se pudo modificar el inicio automatico.")
            self.startup_var.set(not enable)
            return
        self.settings["start_with_windows"] = enable
        self.save_config()
        self.status_var.set("✅ Se iniciara minimizado con el sistema" if enable
                             else "Inicio automatico desactivado")

    def _update_autosync_indicator(self, running: bool):
        if running:
            self.autosync_dot.configure(fg=COLOR_OK)
            self.autosync_label.configure(text="Auto-Sync: Activo")
            self.btn_daemon.configure(text="⏸ Detener Auto-Sync")
        else:
            self.autosync_dot.configure(fg=COLOR_IDLE)
            self.autosync_label.configure(text="Auto-Sync: Detenido")
            self.btn_daemon.configure(text="▶ Iniciar Auto-Sync")

    def refresh_list(self):
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
            self.tree.insert("", "end", iid=str(i), values=(
                pl.get("name", "Sin nombre"),
                st.get("tracks", "-"),
                st.get("local", "-"),
                status,
                st.get("last", "Nunca")
            ), tags=(tag,))

    def _load_state_info(self, pl: dict) -> dict:
        try:
            extractor = SpotifyExtractor()
            pid = extractor.extract_playlist_id(pl["url"])
            sp = Path(self.config.STATE_DIR) / f"{pid}.json"
            out = Path(pl.get("output", "./"))
            local_count = 0
            if out.exists():
                for ext in ["*.mp3", "*.m4a", "*.flac", "*.opus"]:
                    local_count += len(list(out.glob(ext)))
            if sp.exists():
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    track_count = len(data.get("track_ids", []))
                    status = f"OK ({local_count}/{track_count})"
                    if local_count != track_count:
                        status = f"Desfase ({local_count}/{track_count})"
                    return {
                        "tracks": track_count,
                        "local": local_count,
                        "last": data.get("last_sync", "Nunca")[:16].replace("T", " ") if data.get("last_sync") else "Nunca",
                        "status": status
                    }
            return {"tracks": "-", "local": local_count, "last": "Nunca", "status": f"Nuevo ({local_count})"}
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
            new_entry = {"url": url, "name": name or pl.name, "output": output or f"./{name or pl.name}"}
            self.playlists.append(new_entry)
            self.save_config()
            self.refresh_list()
            self.status_var.set(f"✅ Agregado: {name or pl.name} ({pl.total_tracks} tracks)")

            if not self.engine.is_daemon_running():
                self.start_autosync()
            else:
                self._run_sync(new_entry, False)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo agregar:\n{e}")

    def remove_playlist(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Atencion", "Selecciona una playlist.")
            return
        idx = int(sel[0])
        if messagebox.askyesno("Confirmar", f"Eliminar '{self.playlists[idx].get('name')}'?\n(Los archivos NO se borraran)"):
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
            messagebox.showwarning("Atencion", "Selecciona una playlist.")
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
                self._safe(lambda: self.status_var.set(f"Error: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _run_sync(self, pl: dict, force: bool):
        def run():
            self._safe(lambda: self.status_var.set(f"🔄 Sincronizando: {pl.get('name', '')}..."))
            try:
                out = Path(pl["output"])
                out.mkdir(parents=True, exist_ok=True)
                result = self.engine.sync(pl["url"], out, force=force)
                self._handle_result(pl.get("name", ""), result)
            except Exception as e:
                self._handle_result(pl.get("name", ""), {"error": str(e)})
        threading.Thread(target=run, daemon=True).start()

    def _handle_result(self, name: str, result: dict):
        if "error" in result:
            msg = f"❌ Error: {result['error'][:60]}"
        elif result.get("status") == "no_changes":
            msg = f"✅ Sin cambios ({result.get('total_tracks', 0)} tracks)"
        else:
            msg = f"⬇ +{result.get('downloaded', 0)} descargados, -{result.get('deleted', 0)} eliminados, {result.get('failed', 0)} fallos, {result.get('already_have', 0)} ya tenias"
        self._safe(lambda: self.status_var.set(f"[{name}] {msg}" if name else msg))
        self._safe(lambda: self.progress_var.set(0))
        self._safe(lambda: self.progress_label.configure(text="Listo"))
        self._safe(self.refresh_list)

    def start_autosync(self):
        if not self.playlists:
            messagebox.showwarning("Atencion", "Agrega al menos una playlist para iniciar el Auto-Sync.")
            return
        self._apply_settings_to_config()
        self.engine = SyncEngine(
            self.config,
            progress_cb=self.on_progress,
            status_cb=self.on_status,
            playlists_source=lambda: list(self.playlists)
        )
        self.engine.start_daemon(self.playlists)
        self._update_autosync_indicator(True)
        self.settings["auto_sync_enabled"] = True
        self.save_config()
        self.status_var.set(f"🟢 Auto-sync activo (cada {self.config.SYNC_INTERVAL}s)")

    def stop_autosync(self):
        self.engine.stop_daemon()
        self._update_autosync_indicator(False)
        self.settings["auto_sync_enabled"] = False
        self.save_config()
        self.status_var.set("⏸ Auto-sync detenido")

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
        self._safe(lambda: self.status_var.set(text))

    def _safe(self, func):
        self._pending.append(func)

    def _process_queue(self):
        while self._pending:
            func = self._pending.pop(0)
            try:
                func()
            except Exception:
                pass
        self.root.after(100, self._process_queue)

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
    main()