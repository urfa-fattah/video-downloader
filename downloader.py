"""
downloader.py — Grabbit engine v2 (built on yt-dlp).

* Settings        persisted JSON (output folder, parallelism, speed limit, proxy, cookies…)
* Store           SQLite download history (survives restarts, interrupted jobs can resume)
* analyze()       metadata + per-quality size estimates + playlist detection
* JobManager      FIFO queue with dynamic parallelism, smart progress, cancel/retry/remove,
                  trimming, metadata/thumbnail embedding, playlist ranges, archive (skip dupes)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, download_range_func

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("VD_DATA_DIR", BASE_DIR / "data")).resolve()
WORK_DIR = DATA_DIR / "work"
TMP_DIR = DATA_DIR / "tmp"
for _d in (DATA_DIR, WORK_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "grabbit.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
ARCHIVE_PATH = DATA_DIR / "archive.txt"

DEFAULT_QUALITIES = [2160, 1440, 1080, 720, 480, 360, 240, 144]
BROWSERS = ("", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
CONTAINERS = ("mp4", "mkv")
ACTIVE = ("queued", "downloading")
MAX_HISTORY = 300

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


class Cancelled(Exception):
    """Raised inside a progress hook to abort a download."""


class DuplicateError(Exception):
    """The same link/options is already queued or downloaded."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Please enter a valid http(s) link.")
    return url


def _clean_error(exc: Exception | str) -> str:
    msg = ANSI_RE.sub("", str(exc))
    msg = re.sub(r"^\s*(ERROR:\s*)+", "", msg).strip()
    return msg[:400] or "Unknown error"


def is_instagram(url: str = "", text: str = "") -> bool:
    host = (urlparse(url).hostname or "").lower() if url else ""
    return host.endswith("instagram.com") or host == "instagr.am" or "[instagram" in text.lower()


def friendly_error(exc: Exception | str, url: str = "") -> str:
    """Clean message + a short, actionable hint when we recognise the failure."""
    msg = _clean_error(exc)
    low = msg.lower()
    hint = ""
    if "cookie" in low and any(k in low for k in (
            "could not copy", "dpapi", "decrypt", "database", "locked", "permission", "could not find")):
        hint = ("Couldn't read the browser's cookies (Chrome/Edge lock them while open and encrypt them on Windows). "
                "Fix: Settings → upload a cookies.txt file, or pick Firefox.")
    elif is_instagram(url, msg) and "no video" in low:
        hint = "This Instagram post has only photos — Grabbit downloads videos."
    elif is_instagram(url, msg) and any(k in low for k in (
            "login", "log in", "sign in", "empty media", "rate-limit", "rate limit", "not available",
            "cookies", "private", "checkpoint", "http error 401", "http error 403", "http error 429")):
        hint = ("Instagram needs you to be logged in for most videos. Settings → upload a cookies.txt exported "
                "from a browser where you're logged in to Instagram (a secondary account is safer). "
                "Private accounts only work if that account follows them.")
    elif is_instagram(url, msg) and any(k in low for k in ("unable to extract", "unsupported url", "json")):
        hint = ("Instagram changes often. Settings → Update yt-dlp, restart, retry. "
                "For share links (instagram.com/share/…), open the link in your browser and paste the final URL.")
    elif any(k in low for k in ("sign in", "log in", "login", "confirm you", "not a bot", "cookies")):
        hint = "Tip: Settings → upload cookies.txt, or choose 'Cookies from browser' (Firefox is the most reliable)."
    elif "private video" in low or "members-only" in low or "members only" in low:
        hint = "This video is not public, so it can't be downloaded."
    elif "unsupported url" in low:
        hint = "This link isn't supported or the page has no downloadable video."
    elif any(k in low for k in ("http error 403", "unable to extract", "http error 429", "nsig")):
        hint = "Tip: update yt-dlp (Settings → Update yt-dlp), then retry."
    elif ("ffmpeg" in low or "ffprobe" in low) and any(
            k in low for k in ("not found", "not installed", "no such file", "not recognized", "install")):
        hint = "Tip: install FFmpeg (see README), then restart the app."
    elif "ffmpeg exited" in low:
        hint = "FFmpeg couldn't process this stream. Try again without trim/embed options."
    elif "geo" in low and "restrict" in low:
        hint = "This video is blocked in your region."
    return f"{msg}  —  {hint}" if hint else msg


def parse_time(value) -> float | None:
    """'90', '1:30', '01:02:03' → seconds. Empty → None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if not m:
        raise ValueError(f"Invalid time '{s}'. Use seconds, mm:ss or hh:mm:ss.")
    a, b, c = m.group(1), m.group(2), m.group(3)
    if c is None:
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}")


def _is_temp(name: str) -> bool:
    return name.endswith((".part", ".ytdl", ".temp", ".tmp")) or ".part-Frag" in name


def playlist_url_from(url: str) -> str | None:
    """If a YouTube video link also carries a playlist id, return that playlist's URL."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if not (host.endswith("youtube.com") or host == "youtu.be"):
            return None
        qs = parse_qs(p.query)
        lst = (qs.get("list") or [""])[0]
        is_video = ("v" in qs and p.path == "/watch") or host == "youtu.be"
        if lst and is_video and not lst.startswith("RD"):  # RD* = endless auto "mix"
            return f"https://www.youtube.com/playlist?list={lst}"
    except Exception:  # noqa: BLE001
        pass
    return None


def open_in_file_manager(path: Path) -> None:
    target = path if path.is_dir() else path.parent
    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS = {
    "output_dir": str(Path(os.environ.get("VD_DOWNLOAD_DIR", BASE_DIR / "downloads")).resolve()),
    "max_parallel": int(os.environ.get("VD_MAX_PARALLEL", "3")),
    "speed_limit_kbps": 0,
    "proxy": "",
    "cookies_browser": "",
    "cookies_file": "",
    "embed_metadata": True,
    "embed_thumbnail": False,
    "skip_archive": False,
}


def _clean_setting(key: str, v):
    if key == "output_dir":
        s = str(v or "").strip()
        if not s:
            raise ValueError("Output folder can't be empty.")
        p = Path(os.path.expanduser(s)).resolve()
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".grabbit_write_test"
            probe.write_text("x")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"Can't write to that folder: {exc.strerror or exc}") from exc
        return str(p)
    if key == "max_parallel":
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError("Parallel downloads must be a number.") from None
        if not 1 <= n <= 8:
            raise ValueError("Parallel downloads must be between 1 and 8.")
        return n
    if key == "speed_limit_kbps":
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            raise ValueError("Speed limit must be a number (KB/s).") from None
        if not 0 <= n <= 10_000_000:
            raise ValueError("Speed limit is out of range.")
        return n
    if key == "proxy":
        s = str(v or "").strip()
        if s and not re.match(r"^(https?|socks4a?|socks5h?)://\S+$", s, re.I):
            raise ValueError("Proxy must look like http://host:port or socks5://host:port.")
        return s
    if key == "cookies_browser":
        s = str(v or "").strip().lower()
        if s not in BROWSERS:
            raise ValueError("Unknown browser for cookies.")
        return s
    if key == "cookies_file":
        s = str(v or "").strip().strip('"')
        if s and not Path(os.path.expanduser(s)).is_file():
            raise ValueError("Cookies file not found.")
        return s
    if key in ("embed_metadata", "embed_thumbnail", "skip_archive"):
        return bool(v)
    raise ValueError(f"Unknown setting: {key}")


class Settings:
    def __init__(self, path: Path = SETTINGS_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULT_SETTINGS)
        try:
            saved = json.loads(path.read_text("utf-8"))
            for k in DEFAULT_SETTINGS:
                if k in saved:
                    try:
                        self._data[k] = _clean_setting(k, saved[k])
                    except ValueError:
                        pass
        except (OSError, ValueError):
            pass

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            new = dict(self._data)
            for k, v in (patch or {}).items():
                if k in DEFAULT_SETTINGS:
                    new[k] = _clean_setting(k, v)
            self._data = new
            try:
                self._path.write_text(json.dumps(new, indent=2), "utf-8")
            except OSError:
                pass
            return dict(new)


# --------------------------------------------------------------------------- #
# Persistent history
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, path: Path = DB_PATH):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL, data TEXT NOT NULL)")
        self._db.commit()

    def save(self, job: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO jobs (id, created, data) VALUES (?,?,?)",
                             (job["id"], job["created"], json.dumps(job)))
            self._db.commit()

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._db.commit()

    def load_all(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM jobs ORDER BY created DESC LIMIT ?",
                                    (MAX_HISTORY,)).fetchall()
            self._db.execute(
                "DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY created DESC LIMIT ?)",
                (MAX_HISTORY,))
            self._db.commit()
        out = []
        for (data,) in rows:
            try:
                out.append(json.loads(data))
            except ValueError:
                continue
        return out


# --------------------------------------------------------------------------- #
# Analyze
# --------------------------------------------------------------------------- #
def _net_opts(s: dict) -> dict:
    o: dict = {}
    if s.get("proxy"):
        o["proxy"] = s["proxy"]
    if s.get("cookies_file"):
        o["cookiefile"] = os.path.expanduser(s["cookies_file"])
    elif s.get("cookies_browser"):
        o["cookiesfrombrowser"] = (s["cookies_browser"],)
    if s.get("speed_limit_kbps"):
        o["ratelimit"] = int(s["speed_limit_kbps"]) * 1024
    return o


def _fsize(f: dict):
    return f.get("filesize") or f.get("filesize_approx")


def _first_thumb(info: dict):
    thumbs = info.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None


def analyze(url: str, settings: dict) -> dict:
    url = validate_url(url)
    related = playlist_url_from(url)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": bool(related),  # video+playlist link → treat as a single video first
        "socket_timeout": 20,
        **_net_opts(settings),
    }
    opts.pop("ratelimit", None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise ValueError("Could not read this link.")

    is_playlist = info.get("_type") == "playlist" or "entries" in info
    upload = info.get("upload_date") or ""
    result = {
        "url": url,
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "thumbnail": info.get("thumbnail") or _first_thumb(info),
        "duration": info.get("duration"),
        "extractor": info.get("extractor_key") or "",
        "upload_date": f"{upload[:4]}-{upload[4:6]}-{upload[6:8]}" if len(upload) == 8 else "",
        "view_count": info.get("view_count"),
        "is_live": bool(info.get("is_live")),
        "is_playlist": bool(is_playlist),
        "playlist_url": related if (related and not is_playlist) else None,
        "qualities": [],
        "subtitle_langs": [],
        "entries": [],
    }

    if is_playlist:
        entries = [e for e in (info.get("entries") or []) if e]
        result["entry_count"] = len(entries)
        result["entries"] = [
            {"title": e.get("title") or e.get("id") or "Untitled", "duration": e.get("duration")}
            for e in entries[:50]
        ]
        if not result["thumbnail"] and entries:
            result["thumbnail"] = entries[0].get("thumbnail") or _first_thumb(entries[0])
        result["qualities"] = [{"height": h, "size": None} for h in DEFAULT_QUALITIES]
        return result

    formats = info.get("formats") or []
    audio_sizes = [_fsize(f) or 0 for f in formats
                   if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    best_audio = max(audio_sizes, default=0)
    by_h: dict[int, int | None] = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        size = _fsize(f)
        total = None
        if size:
            total = size + (0 if f.get("acodec") not in (None, "none") else best_audio)
        if h not in by_h or (total and (by_h[h] or 0) < total):
            by_h[h] = total
    result["qualities"] = [{"height": h, "size": by_h[h]} for h in sorted(by_h, reverse=True)] \
        or [{"height": h, "size": None} for h in DEFAULT_QUALITIES]
    result["audio_size"] = best_audio or None
    result["subtitle_langs"] = sorted((info.get("subtitles") or {}).keys())[:60]
    return result


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #
def normalize_options(raw: dict | None, settings: dict) -> dict:
    raw = raw or {}
    mode = raw.get("mode", "video")
    if mode not in ("video", "audio"):
        raise ValueError("Invalid mode.")
    q = str(raw.get("quality", "best")).strip().lower()
    if q != "best" and not (q.isdigit() and 100 <= int(q) <= 4320):
        raise ValueError("Invalid quality.")
    container = raw.get("container", "mp4")
    if container not in CONTAINERS:
        container = "mp4"
    audio_format = raw.get("audio_format", "mp3")
    if audio_format not in AUDIO_FORMATS:
        audio_format = "mp3"

    items = str(raw.get("playlist_items") or "").replace(" ", "")
    if items and not re.fullmatch(r"[0-9,\-:]{1,100}", items):
        raise ValueError("Playlist items must look like 1-5,8,10.")

    langs = str(raw.get("sub_langs") or "en").replace(" ", "")
    if not re.fullmatch(r"[A-Za-z0-9_,.\-*]{1,80}", langs):
        raise ValueError("Invalid subtitle language list.")

    start, end = parse_time(raw.get("trim_start")), parse_time(raw.get("trim_end"))
    if start is not None and end is not None and end <= start:
        raise ValueError("Trim end must be after trim start.")

    def flag(key):
        v = raw.get(key)
        return bool(settings.get(key)) if v is None else bool(v)

    return {
        "mode": mode,
        "quality": q,
        "container": container,
        "audio_format": audio_format,
        "playlist": bool(raw.get("playlist", False)),
        "playlist_items": items,
        "subtitles": bool(raw.get("subtitles", False)),
        "sub_langs": langs,
        "trim_start": start,
        "trim_end": end,
        "embed_metadata": flag("embed_metadata"),
        "embed_thumbnail": flag("embed_thumbnail"),
        "skip_archive": flag("skip_archive"),
    }


def _target_key(o: dict):
    return (o["mode"], o["quality"], o["playlist"],
            o["container"] if o["mode"] == "video" else o["audio_format"],
            o["trim_start"], o["trim_end"])


def _public(job: dict) -> dict:
    d = dict(job)
    d["files"] = [{"name": f["name"], "size": f["size"]} for f in job.get("files", [])]
    return d


# --------------------------------------------------------------------------- #
# Job manager
# --------------------------------------------------------------------------- #
class JobManager:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._version = 0
        self._jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._queue: list[str] = []
        self._running: set[str] = set()

        for job in store.load_all():
            if job.get("status") in ACTIVE:  # app was closed mid-download → allow resume
                job.update(status="interrupted", stage="Interrupted — press Retry to resume",
                           speed=None, eta=None)
                store.save(job)
            self._jobs[job["id"]] = job
        for p in TMP_DIR.glob("*.zip"):
            try:
                p.unlink()
            except OSError:
                pass

    # -- change notification (for the live stream) -----------------------------
    def _touch(self) -> None:
        self._version += 1
        self._cond.notify_all()

    def wait_for_change(self, last: int, timeout: float) -> int:
        with self._cond:
            if self._version == last:
                self._cond.wait(timeout)
            return self._version

    # -- queries ---------------------------------------------------------------
    def snapshot(self) -> list[dict]:
        with self._lock:
            return sorted((_public(j) for j in self._jobs.values()),
                          key=lambda j: j["created"], reverse=True)

    def raw(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def library(self) -> dict:
        with self._lock:
            done = [j for j in self._jobs.values() if j["status"] == "finished"]
            return {"count": len(done),
                    "bytes": sum(f["size"] for j in done for f in j.get("files", []))}

    # -- commands --------------------------------------------------------------
    def create(self, url: str, raw_options: dict | None, title: str = "",
               thumb: str = "", force: bool = False) -> dict:
        url = validate_url(url)
        options = normalize_options(raw_options, self.settings.get())
        with self._lock:
            if not force:
                key = _target_key(options)
                for j in self._jobs.values():
                    if j["url"] != url or _target_key(j["options"]) != key:
                        continue
                    if j["status"] in ACTIVE:
                        raise DuplicateError("This link is already in the queue with the same options.")
                    if j["status"] == "finished" and any(Path(f["path"]).exists() for f in j["files"]):
                        raise DuplicateError("You already downloaded this with the same options.")
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id, "url": url, "title": title or url, "thumb": thumb or "",
                "options": options, "status": "queued", "percent": 0.0, "speed": None,
                "eta": None, "stage": "Waiting in queue", "item": None, "files": [],
                "error": None, "created": time.time(),
            }
            self._jobs[job_id] = job
            self._cancel[job_id] = threading.Event()
            self._queue.append(job_id)
            self.store.save(job)
            self._pump()
            return _public(job)

    def pump(self) -> None:
        with self._lock:
            self._pump()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            ev = self._cancel.get(job_id)
            if not job or job["status"] not in ACTIVE:
                return False
            if ev:
                ev.set()
            if job["status"] == "queued":
                if job_id in self._queue:
                    self._queue.remove(job_id)
                job.update(status="cancelled", stage="Cancelled")
                self.store.save(job)
            self._touch()
            return True

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] not in ("error", "cancelled", "interrupted"):
                return False
            job.update(status="queued", percent=0.0, speed=None, eta=None, error=None,
                       stage="Waiting in queue", item=None, files=[])
            self._cancel[job_id] = threading.Event()
            self._queue.append(job_id)
            self.store.save(job)
            self._pump()
            return True

    def remove(self, job_id: str, delete_files: bool = False) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            self.cancel(job_id)
            self._jobs.pop(job_id, None)
            self._cancel.pop(job_id, None)
            if job_id in self._queue:
                self._queue.remove(job_id)
            self.store.delete(job_id)
            self._touch()
        shutil.rmtree(WORK_DIR / job_id, ignore_errors=True)
        if delete_files:
            out_root = Path(self.settings.get()["output_dir"])
            for f in job.get("files", []):
                p = Path(f["path"])
                try:
                    p.unlink()
                except OSError:
                    continue
                parent = p.parent
                while parent != out_root and out_root in parent.parents:
                    try:
                        parent.rmdir()  # only succeeds when empty
                    except OSError:
                        break
                    parent = parent.parent
        return True

    def make_zip(self, job_id: str) -> Path | None:
        job = self.raw(job_id)
        if not job:
            return None
        existing = [f for f in job["files"] if Path(f["path"]).is_file()]
        if not existing:
            return None
        zip_path = TMP_DIR / f"{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for f in existing:
                zf.write(f["path"], arcname=f["name"])
        return zip_path

    # -- internals -------------------------------------------------------------
    def _pump(self) -> None:
        """Start queued jobs while there is parallel capacity. Lock must be held."""
        limit = self.settings.get()["max_parallel"]
        while self._queue and len(self._running) < limit:
            jid = self._queue.pop(0)
            job = self._jobs.get(jid)
            if not job or job["status"] != "queued":
                continue
            self._running.add(jid)
            job.update(status="downloading", stage="Starting…")
            threading.Thread(target=self._run, args=(jid,), daemon=True).start()
        self._touch()

    def _update(self, jid: str, **fields) -> None:
        with self._lock:
            j = self._jobs.get(jid)
            if j is not None:
                j.update(fields)
                self._touch()

    def _persist(self, jid: str) -> None:
        with self._lock:
            j = self._jobs.get(jid)
            if j is not None:
                self.store.save(j)

    def _build_opts(self, job: dict, settings: dict, work_dir: Path, hook, pp_hook) -> dict:
        o = job["options"]
        ffmpeg = has_ffmpeg()
        playlist = o["playlist"]
        template = ("%(playlist_title|Playlist)s/%(playlist_index)s - %(title).120B [%(id)s].%(ext)s"
                    if playlist else "%(title).150B [%(id)s].%(ext)s")
        opts: dict = {
            "outtmpl": str(work_dir / template),
            "noplaylist": not playlist,
            "quiet": True, "no_warnings": True, "noprogress": True,
            "windowsfilenames": True, "continuedl": True,
            "retries": 10, "fragment_retries": 10,
            "concurrent_fragment_downloads": 4, "socket_timeout": 30,
            "progress_hooks": [hook], "postprocessor_hooks": [pp_hook],
            "ignoreerrors": bool(playlist),
            **_net_opts(settings),
        }
        pps: list[dict] = []

        if playlist and o["playlist_items"]:
            opts["playlist_items"] = o["playlist_items"]
        if playlist and o["skip_archive"]:
            opts["download_archive"] = str(ARCHIVE_PATH)

        if o["mode"] == "audio":
            opts["format"] = "bestaudio/best"
            if ffmpeg:
                pp = {"key": "FFmpegExtractAudio", "preferredcodec": o["audio_format"]}
                if o["audio_format"] in ("mp3", "m4a", "opus"):
                    pp["preferredquality"] = "192"
                pps.append(pp)
        else:
            q = o["quality"]
            if ffmpeg:
                cap = "" if q == "best" else f"[height<={q}]"
                opts["format"] = f"bv*{cap}+ba/b{cap}/b"
                opts["merge_output_format"] = o["container"]
                if o["container"] == "mp4":
                    opts["format_sort"] = ["res", "ext:mp4:m4a"]  # prefer mp4-compatible streams
                else:  # MKV holds any codec, so a lossless remux is always safe
                    pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"})
            else:
                opts["format"] = "b" if q == "best" else f"b[height<={q}]/b"

        if o["subtitles"]:
            langs = [x if re.search(r"[.*]", x) else f"{x}.*" for x in o["sub_langs"].split(",") if x]
            opts.update(writesubtitles=True, writeautomaticsub=True,
                        subtitleslangs=langs, subtitlesformat="srt/best")

        if o["trim_start"] is not None or o["trim_end"] is not None:
            if not ffmpeg:
                raise ValueError("Trimming needs FFmpeg. Install it and restart the app.")
            end = o["trim_end"] if o["trim_end"] is not None else float("inf")
            opts["download_ranges"] = download_range_func(None, [(o["trim_start"] or 0, end)])
            opts["force_keyframes_at_cuts"] = True

        if ffmpeg:
            if o["embed_metadata"]:
                pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
            if o["embed_thumbnail"]:
                opts["writethumbnail"] = True
                pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
                pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        if pps:
            opts["postprocessors"] = pps
        return opts

    def _run(self, jid: str) -> None:
        with self._lock:
            job = self._jobs.get(jid)
            cancel_ev = self._cancel.get(jid)
            job = dict(job) if job else None
        if job is None or cancel_ev is None:
            with self._lock:
                self._running.discard(jid)
                self._pump()
            return

        settings = self.settings.get()
        work_dir = WORK_DIR / jid
        out_dir = Path(settings["output_dir"])
        st = {"idx": None, "fin": 0, "item": 0.0}

        def hook(d):
            if cancel_ev.is_set():
                raise Cancelled()
            info = d.get("info_dict") or {}
            idx = info.get("playlist_index")
            count = info.get("n_entries") or info.get("playlist_count")
            if idx != st["idx"]:
                st.update(idx=idx, fin=0, item=0.0)
            n = len(info.get("requested_formats") or []) or 1  # video+audio = 2 streams
            fields: dict = {}
            if info.get("title"):
                fields["title"] = info["title"]
            if info.get("thumbnail"):
                fields["thumb"] = info["thumbnail"]
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                frac = min((d.get("downloaded_bytes") or 0) / total, 1.0) if total else 0.0
                st["item"] = max(st["item"], min(1.0, (st["fin"] + frac) / n))
                overall = ((idx - 1) + st["item"]) / count if idx and count else st["item"]
                fields.update(percent=round(min(overall, 1.0) * 100, 1), speed=d.get("speed"),
                              eta=d.get("eta"), stage="Downloading",
                              item=f"Item {idx}/{count}" if idx and count else None)
            elif status == "finished":
                st["fin"] += 1
                st["item"] = max(st["item"], min(1.0, st["fin"] / n))
                fields.update(stage="Processing…" if st["fin"] >= n else "Downloading",
                              speed=None, eta=None)
            self._update(jid, **fields)

        def pp_hook(d):
            if d.get("status") == "started":
                self._update(jid, stage="Processing…", speed=None, eta=None)

        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            opts = self._build_opts(job, settings, work_dir, hook, pp_hook)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job["url"]])
            files = self._finalize(work_dir, out_dir)
            if not files:
                if job["options"]["playlist"] and job["options"]["skip_archive"]:
                    self._update(jid, status="finished", percent=100.0, stage="Nothing new — all items already downloaded",
                                 speed=None, eta=None, files=[], finished=time.time())
                    return
                raise DownloadError("No file was produced. The video may be private, live, or unsupported.")
            self._update(jid, status="finished", percent=100.0, stage="Done", speed=None, eta=None,
                         files=files, error=None, finished=time.time())
        except Cancelled:
            shutil.rmtree(work_dir, ignore_errors=True)
            self._update(jid, status="cancelled", stage="Cancelled", speed=None, eta=None)
        except Exception as exc:  # noqa: BLE001 — every failure must reach the UI
            if cancel_ev.is_set():
                shutil.rmtree(work_dir, ignore_errors=True)
                self._update(jid, status="cancelled", stage="Cancelled", speed=None, eta=None)
            else:
                self._update(jid, status="error", stage="Failed", error=friendly_error(exc, job["url"]),
                             speed=None, eta=None)
        finally:
            self._persist(jid)
            with self._lock:
                self._running.discard(jid)
                self._pump()

    @staticmethod
    def _finalize(work_dir: Path, out_dir: Path) -> list[dict]:
        """Move finished files from the private work dir into the output folder."""
        files: list[dict] = []
        if not work_dir.exists():
            return files
        for p in sorted(work_dir.rglob("*")):
            if not p.is_file() or _is_temp(p.name):
                continue
            dest = _unique_path(out_dir / p.relative_to(work_dir))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            files.append({"name": dest.relative_to(out_dir).as_posix(),
                          "size": dest.stat().st_size, "path": str(dest)})
        shutil.rmtree(work_dir, ignore_errors=True)
        return files


# --------------------------------------------------------------------------- #
# cookies.txt import (needed for Instagram and other login-gated sites)
# --------------------------------------------------------------------------- #
COOKIES_PATH = DATA_DIR / "cookies.txt"


def import_cookies(text: str) -> dict:
    """Validate an uploaded Netscape cookies.txt, store it privately and point Settings at it."""
    from yt_dlp.cookies import YoutubeDLCookieJar

    text = (text or "").replace("\r\n", "\n").lstrip("﻿").strip()
    if not text:
        raise ValueError("The cookies file is empty.")
    if "\t" not in text:
        raise ValueError("This doesn't look like a Netscape-format cookies.txt (tab-separated lines). "
                         "Export it again from the cookies extension and pick the 'Netscape' format.")
    if not text.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        text = "# Netscape HTTP Cookie File\n" + text

    tmp = TMP_DIR / f"cookies-{uuid.uuid4().hex[:8]}.txt"
    tmp.write_text(text + "\n", "utf-8")
    try:
        jar = YoutubeDLCookieJar(str(tmp))
        jar.load(ignore_discard=True, ignore_expires=True)
        cookies = list(jar)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise ValueError(f"Couldn't read this cookies file: {_clean_error(exc)}") from exc
    if not cookies:
        tmp.unlink(missing_ok=True)
        raise ValueError("No cookies were found in that file.")

    os.replace(tmp, COOKIES_PATH)
    try:
        os.chmod(COOKIES_PATH, 0o600)  # best effort; cookies are as sensitive as a password
    except OSError:
        pass
    settings.update({"cookies_file": str(COOKIES_PATH), "cookies_browser": ""})

    ig = [c for c in cookies if "instagram.com" in (c.domain or "")]
    return {
        "ok": True,
        "count": len(cookies),
        "domains": sorted({(c.domain or "").lstrip(".") for c in cookies})[:12],
        "instagram": bool(ig),
        "instagram_logged_in": any(c.name == "sessionid" and c.value for c in ig),
        "settings": settings.get(),
    }


def remove_cookies() -> dict:
    cur = settings.get().get("cookies_file", "")
    if cur and Path(cur).resolve() == COOKIES_PATH.resolve():
        COOKIES_PATH.unlink(missing_ok=True)
    settings.update({"cookies_file": ""})
    return {"ok": True, "settings": settings.get()}


# --------------------------------------------------------------------------- #
# yt-dlp self-update
# --------------------------------------------------------------------------- #
def update_ytdlp() -> dict:
    before = yt_dlp.version.__version__
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                          capture_output=True, text=True, timeout=240)
    log = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
    after = before
    if proc.returncode == 0:
        chk = subprocess.run([sys.executable, "-c", "import yt_dlp.version as v;print(v.__version__)"],
                             capture_output=True, text=True, timeout=60)
        after = chk.stdout.strip() or before
    return {"ok": proc.returncode == 0, "before": before, "after": after,
            "restart_needed": after != before, "log": log}


settings = Settings()
store = Store()
manager = JobManager(settings, store)
