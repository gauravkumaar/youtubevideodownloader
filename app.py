from pathlib import Path
from flask import (
    Flask, render_template, request, jsonify, url_for,
    send_from_directory, abort
)
from services.downloader import DownloadManager
from services.urltools import sanitize_youtube_url, UrlKind, InvalidYouTubeUrl

app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/api/probe", methods=["POST"])
def api_probe():
    data = request.get_json(silent=True) or request.form
    raw = (data.get("url") or "").strip()
    try:
        clean, kind = sanitize_youtube_url(raw)
        if kind not in (UrlKind.VIDEO, UrlKind.SHORT):
            return jsonify({"ok": False, "error": "Only individual YouTube videos or shorts are supported."}), 400
        meta = DownloadManager.probe(clean)
        return jsonify({"ok": True, "meta": meta, "clean_url": clean})
    except InvalidYouTubeUrl as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Probe failed: {e}"}), 500

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or request.form
    raw = (data.get("url") or "").strip()
    try:
        clean, kind = sanitize_youtube_url(raw)
        if kind not in (UrlKind.VIDEO, UrlKind.SHORT):
            return jsonify({"ok": False, "error": "Only individual YouTube videos or shorts are supported."}), 400
        job_id = DownloadManager.enqueue(clean)
        return jsonify({"ok": True, "job_id": job_id})
    except InvalidYouTubeUrl as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Start failed: {e}"}), 500

@app.route("/api/progress/<job_id>", methods=["GET"])
def api_progress(job_id):
    job = DownloadManager.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    payload = DownloadManager.public_view(job)
    if payload.get("filename"):
        payload["download_url"] = url_for("fetch_file", job_id=job["id"], _external=False)
    return jsonify(payload)

@app.route("/api/cancel/<job_id>", methods=["POST"])
def api_cancel(job_id):
    ok, msg = DownloadManager.cancel(job_id)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

@app.route("/file/<job_id>", methods=["GET"])
def fetch_file(job_id):
    job = DownloadManager.get(job_id)
    if not job or job["status"] != "finished":
        abort(404)
    fp = Path(job["filepath"])
    if not fp.exists():
        abort(404)
    return send_from_directory(str(fp.parent), fp.name, as_attachment=True)

@app.route("/api/recent", methods=["GET"])
def api_recent():
    return jsonify({"ok": True, "jobs": DownloadManager.recent()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
