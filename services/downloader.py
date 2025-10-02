import threading
import uuid
import time
import shutil
import random
import re
from pathlib import Path
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:
    from backports.zoneinfo import ZoneInfo

from yt_dlp import YoutubeDL
from yt_dlp.utils import PostProcessingError

from .urltools import sanitize_youtube_url, UrlKind, InvalidYouTubeUrl

DOWNLOAD_DIR = Path("downloads").resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = Path("cookies.txt").resolve()

RETAIN_SECONDS = 5 * 60 * 60  # auto-delete per file after 5 hours
TEMP_MAX_AGE = 15 * 60        # purge orphan temp artifacts older than 15 minutes

_jobs = {}
_lock = threading.Lock()
_sweeper_started = False

def _size_fmt(num):
    if num is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"

def _now(): return time.time()

def _ist_str(ts: float | None):
    if ts is None: return None
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Kolkata"))
    return dt.strftime("%d %b %Y, %I:%M:%S %p IST")

def _rand(n=6):
    return "".join(random.choices("abcdefghjkmnpqrstuvwxyz23456789", k=n))

def _slug_two_words(title: str):
    tokens = re.findall(r"[A-Za-z0-9]+", title or "")
    tokens = tokens[:2] if tokens else ["video"]
    return "-".join(t.lower() for t in tokens)

class _YDLLogger:
    def __init__(self, job_id): self.job_id = job_id
    def debug(self, msg): DownloadManager._log(self.job_id, msg, noisy=False)
    def info(self, msg): DownloadManager._log(self.job_id, msg)
    def warning(self, msg): DownloadManager._log(self.job_id, f"WARNING: {msg}")
    def error(self, msg): DownloadManager._log(self.job_id, f"ERROR: {msg}")

class DownloadManager:
    @staticmethod
    def probe(url: str):
        clean, kind = sanitize_youtube_url(url)
        if kind not in (UrlKind.VIDEO, UrlKind.SHORT):
            raise InvalidYouTubeUrl("Only individual YouTube videos or shorts are supported.")
        with YoutubeDL(DownloadManager._base_opts(for_probe=True)) as ydl:
            info = ydl.extract_info(clean, download=False, process=False)
        if info.get("_type") in ("playlist", "multi_video", "channel"):
            raise InvalidYouTubeUrl("Playlists/channels are not supported. Enter a single video or short.")
        live_status = (info.get("live_status") or "").lower()
        if live_status in ("is_live", "is_upcoming"):
            raise InvalidYouTubeUrl("Live streams or upcoming premieres are not supported.")

        thumb = info.get("thumbnail")
        if not thumb:
            thumbs = info.get("thumbnails") or []
            if thumbs:
                thumb = sorted(thumbs, key=lambda t: t.get("height") or 0, reverse=True)[0].get("url")

        chan_name = info.get("channel") or info.get("uploader")
        chan_avatar = info.get("channel_thumbnail")
        subs = (info.get("channel_subscriber_count")
                or info.get("uploader_subscriber_count")
                or info.get("channel_follower_count"))
        if not chan_avatar:
            up_thumbs = info.get("uploader_thumbnails") or []
            if up_thumbs:
                chan_avatar = sorted(up_thumbs, key=lambda t: t.get("height") or 0, reverse=True)[0].get("url")

        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "uploader": chan_name,
            "channel_avatar": chan_avatar,
            "subscribers": subs,
            "thumbnail": thumb,
        }

    @staticmethod
    def enqueue(url: str):
        DownloadManager._ensure_sweeper()
        clean, _ = sanitize_youtube_url(url)
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH. Please install ffmpeg.")
        plan = DownloadManager._plan_format(clean)

        job_id = uuid.uuid4().hex
        with _lock:
            _jobs[job_id] = {
                "id": job_id, "url": clean, "status": "queued",
                "progress": 0.0, "eta": None, "speed": None,
                "downloaded_bytes": 0, "total_bytes": None,
                "filename": None, "filepath": None, "partial_path": None,
                "used_cookies": COOKIES_FILE.exists(),
                "created_at": _now(), "started_at": None,
                "finished_at": None, "expires_at": None, "updated_at": _now(),
                "log": [], "error": None, "vid": None, "plan": plan,
                "expired": False, "cancelled": False
            }
        threading.Thread(target=DownloadManager._worker, args=(job_id,), daemon=True).start()
        return job_id

    @staticmethod
    def cancel(job_id: str):
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return False, "Job not found."
            if job["status"] in ("finished", "expired", "error", "cancelled"):
                return False, f"Job already {job['status']}."
            job["cancelled"] = True
        return True, "Cancellation requested."

    @staticmethod
    def get(job_id):
        with _lock: return _jobs.get(job_id)

    @staticmethod
    def recent(limit: int = 20):
        with _lock:
            items = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)[:limit]
            return [{"id": j["id"], "url": j["url"], "status": j["status"],
                     "progress": round(j.get("progress") or 0.0, 1),
                     "filename": j.get("filename"), "expired": j.get("expired", False)} for j in items]

    @staticmethod
    def public_view(job):
        return {
            "ok": True, "id": job["id"], "status": job["status"],
            "progress": round(job.get("progress") or 0.0, 2), "eta": job.get("eta"),
            "speed": _size_fmt(job.get("speed")) if job.get("speed") else None,
            "downloaded": _size_fmt(job.get("downloaded_bytes")),
            "total": _size_fmt(job.get("total_bytes")), "filename": job.get("filename"),
            "used_cookies": job.get("used_cookies"), "error": job.get("error"),
            "vid": job.get("vid"), "updated_at": job.get("updated_at"),
            "started_at_ist": _ist_str(job.get("started_at")),
            "expires_at_ist": _ist_str(job.get("expires_at")),
            "expired": job.get("expired", False),
        }

    @staticmethod
    def _base_opts(job_id=None, for_probe=False, plan=None):
        opts = {
            "logger": _YDLLogger(job_id) if job_id else None,
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "restrictfilenames": True, "nocheckcertificate": True,

            "retries": 10, "fragment_retries": 10, "socket_timeout": 30.0,
            "continuedl": True, "nooverwrites": True, "nopart": False,  # allow yt-dlp .part for reliable merges
            "writesubtitles": False, "writeinfojson": False,

            "concurrent_fragments": 16,
            "http_chunk_size": 32 * 1024 * 1024,

            "paths": {"home": str(DOWNLOAD_DIR), "temp": str(DOWNLOAD_DIR)},
            "outtmpl": "%(title).200B [%(id)s].%(ext)s",

            "hls_split_discontinuity": True, "geo_bypass": True,
            "fixup": "detect_or_warn",
        }
        if COOKIES_FILE.exists(): opts["cookiefile"] = str(COOKIES_FILE)
        if for_probe: return opts

        opts["progress_hooks"] = [DownloadManager._progress_hook(job_id)]
        opts["postprocessor_hooks"] = [DownloadManager._postproc_hook(job_id)]
        opts["prefer_ffmpeg"] = True
        opts["allow_multiple_video_streams"] = False
        opts["allow_multiple_audio_streams"] = False
        if plan:
            opts["format"] = plan["format"]
            if plan.get("merge_to"): opts["merge_output_format"] = plan["merge_to"]
        else:
            opts["format"] = "bv*[vcodec~='^(avc1|h264)']+ba[acodec~='^(mp4a|aac)']/b[ext=mp4]/bv*+ba/b"
            opts["merge_output_format"] = "mp4"
        return opts

    @staticmethod
    def _plan_format(url: str):
        with YoutubeDL(DownloadManager._base_opts(for_probe=True)) as ydl:
            info = ydl.extract_info(url, download=False)
        fmts = info.get("formats") or []
        progressive = [f for f in fmts if f.get("ext") == "mp4" and f.get("acodec") != "none" and f.get("vcodec") != "none"]
        if progressive:
            prog_1080 = [f for f in progressive if (f.get("height") or 0) <= 1080]
            target = prog_1080 or progressive
            best = sorted(target, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)[0]
            return {"format": f"{best.get('format_id')}", "merge_to": None, "ext": "mp4"}
        can_mp4 = any((f.get("vcodec") or "").startswith(("avc1", "h264")) for f in fmts)
        if can_mp4:
            sel = "bv*[vcodec~='^(avc1|h264)']+ba[acodec~='^(mp4a|aac)']/" "b[ext=mp4]/bv*+ba/b"
            return {"format": sel, "merge_to": "mp4", "ext": "mp4"}
        return {"format": "bv*+ba/b", "merge_to": "mkv", "ext": "mkv"}

    @staticmethod
    def _update(job_id, **kw):
        with _lock:
            job = _jobs.get(job_id)
            if not job: return
            job.update(kw)
            job["updated_at"] = _now()

    @staticmethod
    def _log(job_id, line, noisy=True):
        if not line: return
        with _lock:
            job = _jobs.get(job_id)
            if not job: return
            if len(job["log"]) > 2000: job["log"] = job["log"][-1000:]
            if noisy and isinstance(line, str) and not line.startswith("[debug] "):
                job["log"].append(line)

    @staticmethod
    def _cancelled(job_id) -> bool:
        with _lock:
            j = _jobs.get(job_id)
            return bool(j and j.get("cancelled"))

    @staticmethod
    def _progress_hook(job_id):
        def hook(d):
            if DownloadManager._cancelled(job_id):
                raise Exception("CANCELLED")
            try:
                st = d.get("status")
                if st == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    downloaded = d.get("downloaded_bytes") or 0
                    job = DownloadManager.get(job_id)
                    if job and job.get("started_at") is None:
                        DownloadManager._update(job_id, started_at=_now())
                    DownloadManager._update(
                        job_id, status="downloading", total_bytes=total,
                        downloaded_bytes=downloaded, speed=d.get("speed"),
                        eta=d.get("eta"), progress=(downloaded / total * 100.0) if total else 0.0
                    )
                elif st == "finished":
                    cur = DownloadManager.get(job_id)
                    p = (cur.get("progress") or 95.0)
                    DownloadManager._update(job_id, status="processing", progress=max(99.0, p))
                info = d.get("info_dict") or {}
                if info.get("id"): DownloadManager._update(job_id, vid=info.get("id"))
                if d.get("filename"): DownloadManager._update(job_id, partial_path=str(Path(d["filename"]).resolve()))
            except Exception as e:
                if "CANCELLED" in str(e): raise
                DownloadManager._update(job_id, status="error", error=str(e))
        return hook

    @staticmethod
    def _postproc_hook(job_id):
        def pp(d):
            if DownloadManager._cancelled(job_id):
                raise Exception("CANCELLED")
            if d.get("status") != "finished": return
            try:
                info = d.get("info_dict") or {}
                final_fp = info.get("filepath") or info.get("_filename")
                if final_fp:
                    DownloadManager._update(job_id, filename=Path(final_fp).name, filepath=str(Path(final_fp).resolve()))
            except Exception as e:
                DownloadManager._update(job_id, status="error", error=str(e))
        return pp

    @staticmethod
    def _resolve_final_path(info):
        rds = info.get("requested_downloads")
        if isinstance(rds, list) and rds:
            for cand in reversed(rds):
                fp = cand.get("filepath") or cand.get("_filename")
                if fp and Path(fp).exists(): return Path(fp)
        for key in ("filepath", "_filename"):
            fp = info.get(key)
            if fp and Path(fp).exists(): return Path(fp)
        vid = info.get("id")
        if vid:
            candidates = sorted(DOWNLOAD_DIR.glob(f"*[{vid}].*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates: return candidates[0]
        return None

    @staticmethod
    def _rename_final(job_id: str, final: Path, title: str):
        ext = final.suffix
        base = _slug_two_words(title)
        for _ in range(6):
            new = final.with_name(f"{base}-{_rand(6)}{ext}")
            if not new.exists():
                try:
                    final.rename(new)
                    DownloadManager._update(job_id, filename=new.name, filepath=str(new.resolve()))
                except Exception:
                    pass
                return

    @staticmethod
    def _cleanup_partial(job):
        # Remove directly tracked partial files
        for key in ("partial_path", "filepath"):
            fp = job.get(key)
            if fp:
                p = Path(fp)
                try: p.unlink(missing_ok=True)
                except Exception: pass

    @staticmethod
    def _purge_job_artifacts(job):
        """Delete any temp/partial files associated with this job/video id."""
        vid = job.get("vid")
        created = job.get("created_at") or 0
        now = _now()

        def _safe_unlink(p: Path):
            try: p.unlink(missing_ok=True)
            except Exception: pass

        # Delete all files containing [vid] (if known)
        if vid:
            for p in DOWNLOAD_DIR.glob(f"*[{vid}]*"):
                if p.is_file():
                    _safe_unlink(p)

        # Delete generic temp artifacts in time window for this job
        patterns = ["*.part", "*.ytdl", "*.tmp", "*.temp", "*.frag", "*.mkv.part", "*.mp4.part"]
        for pat in patterns:
            for p in DOWNLOAD_DIR.glob(pat):
                try:
                    if not p.is_file(): continue
                    mtime = p.stat().st_mtime
                    # remove if created during/after job start or within last hour
                    if mtime >= created - 600 or (now - mtime) < (2 * 3600):
                        _safe_unlink(p)
                except Exception:
                    continue

    @staticmethod
    def _schedule_expiry(job_id):
        def expire():
            try:
                with _lock:
                    job = _jobs.get(job_id)
                if not job or job.get("expired"): return
                fp = job.get("filepath")
                if fp and Path(fp).exists():
                    try: Path(fp).unlink(missing_ok=True)
                    except Exception: pass
                DownloadManager._update(job_id, expired=True, status="expired")
            except Exception: pass
        t = threading.Timer(RETAIN_SECONDS, expire); t.daemon = True; t.start()

    @staticmethod
    def _sweeper_loop():
        while True:
            try:
                now = _now()
                # Purge old temp artifacts
                for pat in ("*.part", "*.ytdl", "*.tmp", "*.temp", "*.frag"):
                    for p in DOWNLOAD_DIR.glob(pat):
                        try:
                            if p.is_file() and (now - p.stat().st_mtime) > TEMP_MAX_AGE:
                                p.unlink(missing_ok=True)
                        except Exception:
                            continue
                # Remove stale finished files older than retention
                for p in DOWNLOAD_DIR.glob("*"):
                    try:
                        if p.is_file() and (now - p.stat().st_mtime) > RETAIN_SECONDS:
                            p.unlink(missing_ok=True)
                    except Exception:
                        continue
                time.sleep(600)
            except Exception:
                time.sleep(600)

    @staticmethod
    def _ensure_sweeper():
        global _sweeper_started
        if _sweeper_started: return
        _sweeper_started = True
        threading.Thread(target=DownloadManager._sweeper_loop, daemon=True).start()

    @staticmethod
    def _download_once(job_id, url, plan):
        with YoutubeDL(DownloadManager._base_opts(job_id=job_id, plan=plan)) as ydl:
            info = ydl.extract_info(url, download=True)
        final = DownloadManager._resolve_final_path(info or {})
        if not final or not final.exists():
            raise RuntimeError("Download finished but output file was not found.")
        title = (info or {}).get("title") or "video"
        DownloadManager._rename_final(job_id, final, title)
        return info

    @staticmethod
    def _worker(job_id):
        with _lock:
            job = _jobs.get(job_id)
            if not job: return
            url = job["url"]; plan = job.get("plan")

        if shutil.which("ffmpeg") is None:
            DownloadManager._update(job_id, status="error", error="ffmpeg not found on PATH. Please install ffmpeg.")
            return
        try:
            DownloadManager._update(job_id, status="downloading", started_at=_now())
            try:
                info = DownloadManager._download_once(job_id, url, plan)
            except Exception as e:
                msg = str(e)
                needs_fallback = isinstance(e, PostProcessingError) or \
                                 "Postprocessing" in msg or "ffmpeg" in msg or \
                                 "Invalid data found when processing input" in msg
                if DownloadManager._cancelled(job_id): raise
                if needs_fallback and plan.get("merge_to") != "mkv":
                    fallback_plan = {"format": "bv*+ba/b", "merge_to": "mkv", "ext": "mkv"}
                    DownloadManager._update(job_id, status="downloading")
                    info = DownloadManager._download_once(job_id, url, fallback_plan)
                else:
                    raise

            finished_at = _now(); expires_at = finished_at + RETAIN_SECONDS
            DownloadManager._update(job_id, status="finished", progress=100.0,
                                    eta=None, speed=None, finished_at=finished_at, expires_at=expires_at)
            DownloadManager._schedule_expiry(job_id)

        except Exception as e:
            if "CANCELLED" in str(e) or DownloadManager._cancelled(job_id):
                with _lock:
                    job = _jobs.get(job_id) or {}
                DownloadManager._cleanup_partial(job)
                DownloadManager._purge_job_artifacts(job)
                DownloadManager._update(job_id, status="cancelled", error=None, eta=None, speed=None)
            else:
                DownloadManager._update(job_id, status="error", error=str(e))
