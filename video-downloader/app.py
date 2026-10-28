"""
app.py — Grabbit server (Flask). Runs on 127.0.0.1 only.

Start with:  python app.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import webbrowser
from pathlib import Path
from threading import Timer

try:
    import yt_dlp
    from flask import Flask, Response, abort, jsonify, render_template, request, send_file
except ModuleNotFoundError as exc:
    sys.exit(
        f"\n[!] Missing Python package: {exc.name}\n"
        "    Install everything with:\n\n"
        "        python -m pip install -r requirements.txt\n\n"
        "    Then run again:  python app.py\n"
    )

import downloader as dl

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # API payloads are tiny (cookies.txt is the largest)


@app.before_request
def _local_only():
    """Block DNS-rebinding: only answer requests addressed to localhost."""
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        abort(403)


@app.errorhandler(413)
def _too_big(_exc):
    return jsonify({"error": "That file is too large. Export cookies only for the site you need "
                             "(e.g. instagram.com), not for every site."}), 413


def _err(message: str, code: int = 400, **extra):
    return jsonify({"error": message, **extra}), code


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/info")
def info():
    s = dl.settings.get()
    try:
        free = shutil.disk_usage(s["output_dir"]).free
    except OSError:
        free = None
    return jsonify({
        "ffmpeg": dl.has_ffmpeg(),
        "yt_dlp_version": yt_dlp.version.__version__,
        "settings": s,
        "library": dl.manager.library(),
        "disk_free": free,
    })


@app.get("/api/settings")
def get_settings():
    return jsonify(dl.settings.get())


@app.post("/api/settings")
def save_settings():
    try:
        new = dl.settings.update(_body())
    except ValueError as exc:
        return _err(str(exc))
    dl.manager.pump()  # a higher parallel limit may free queued jobs immediately
    return jsonify(new)


@app.post("/api/cookies")
def upload_cookies():
    # Custom header forces a CORS preflight, so a random website can't overwrite your cookies.
    if request.headers.get("X-Grabbit") != "1":
        abort(403)
    try:
        return jsonify(dl.import_cookies(request.get_data(as_text=True)))
    except ValueError as exc:
        return _err(str(exc))


@app.delete("/api/cookies")
def delete_cookies():
    return jsonify(dl.remove_cookies())


@app.post("/api/analyze")
def analyze():
    url = str(_body().get("url", ""))
    try:
        return jsonify(dl.analyze(url, dl.settings.get()))
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _err(dl.friendly_error(exc, url), 422)


@app.post("/api/download")
def download():
    data = _body()
    try:
        job = dl.manager.create(
            url=data.get("url", ""),
            raw_options=data.get("options"),
            title=str(data.get("title") or "")[:200],
            thumb=str(data.get("thumb") or "")[:500],
            force=bool(data.get("force", False)),
        )
        return jsonify(job), 201
    except dl.DuplicateError as exc:
        return _err(str(exc), 409, duplicate=True)
    except ValueError as exc:
        return _err(str(exc))


@app.post("/api/batch")
def batch():
    data = _body()
    urls = data.get("urls")
    if not isinstance(urls, list) or not urls:
        return _err("Paste at least one link.")
    if len(urls) > 50:
        return _err("Batch is limited to 50 links at a time.")
    created, errors = 0, []
    for u in urls:
        try:
            dl.manager.create(url=str(u), raw_options=data.get("options"), force=bool(data.get("force")))
            created += 1
        except dl.DuplicateError:
            errors.append({"url": str(u), "error": "Duplicate — skipped"})
        except ValueError as exc:
            errors.append({"url": str(u), "error": str(exc)})
    return jsonify({"created": created, "errors": errors}), (201 if created else 400)


@app.get("/api/jobs")
def jobs():
    return jsonify(dl.manager.snapshot())


@app.get("/api/stream")
def stream():
    """Server-Sent Events: pushes the full job list whenever anything changes."""
    def gen():
        ver = -1
        while True:
            new = dl.manager.wait_for_change(ver, 15)
            if new != ver:
                ver = new
                yield f"data: {json.dumps(dl.manager.snapshot())}\n\n"
                time.sleep(0.25)  # coalesce bursts of progress updates
            else:
                yield ": keepalive\n\n"
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    return jsonify({"ok": dl.manager.cancel(job_id)})


@app.post("/api/jobs/<job_id>/retry")
def retry(job_id):
    return jsonify({"ok": dl.manager.retry(job_id)})


@app.delete("/api/jobs/<job_id>")
def remove(job_id):
    return jsonify({"ok": dl.manager.remove(job_id, delete_files=request.args.get("files") == "1")})


@app.get("/files/<job_id>/<int:idx>")
def serve_file(job_id, idx):
    job = dl.manager.raw(job_id)
    if not job or not 0 <= idx < len(job["files"]):
        abort(404)
    path = Path(job["files"][idx]["path"])
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/files/<job_id>.zip")
def serve_zip(job_id):
    path = dl.manager.make_zip(job_id)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"grabbit-{job_id}.zip")


@app.post("/api/open-folder")
def open_folder():
    job_id = _body().get("job_id")
    target = Path(dl.settings.get()["output_dir"])
    if job_id:
        job = dl.manager.raw(str(job_id))
        if job and job["files"]:
            target = Path(job["files"][0]["path"]).parent
    try:
        dl.open_in_file_manager(target)
        return jsonify({"ok": True, "path": str(target)})
    except Exception as exc:  # noqa: BLE001
        return _err(f"Couldn't open the folder: {exc}", 500)


@app.post("/api/update-ytdlp")
def update_ytdlp():
    try:
        return jsonify(dl.update_ytdlp())
    except Exception as exc:  # noqa: BLE001
        return _err(f"Update failed: {dl._clean_error(exc)}", 500)


def _open_browser(port: int):
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    s = dl.settings.get()
    print(f"\n  Grabbit running      →  http://127.0.0.1:{port}")
    print(f"  Saving files to      →  {s['output_dir']}")
    print(f"  FFmpeg detected      →  {'yes' if dl.has_ffmpeg() else 'NO (no HD merge / MP3 / trim)'}")
    print(f"  yt-dlp version       →  {yt_dlp.version.__version__}\n")
    if os.environ.get("VD_NO_BROWSER") != "1":
        Timer(1.0, _open_browser, args=(port,)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
