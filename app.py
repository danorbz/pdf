"""
app.py – Flask web application for PDF Form Filler.

Routes:
  /                  – Upload page (always a clean start per tab)
  /upload            – POST: upload a PDF → creates a new tab session (tid)
  /page/<n>/image    – GET: rendered page image as PNG
  /add_text          – POST: add text annotation
  /add_signature     – POST: add signature annotation
  /undo              – POST: undo last annotation on current page
  /save              – POST: download the filled PDF
  /clear             – POST: close current PDF and start over
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, jsonify,
)
from PIL import Image

from pdf_engine import PDFEngine

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ---------------------------------------------------------------------------
# Per-TAB isolation: each browser tab gets its own PDFEngine and upload
# folder, keyed by a server-generated tab ID (tid).  The tid is created on
# upload and returned to the client inside the editor HTML.  The client then
# sends it back on every request via the X-Tab-Id header (for GET/POST
# fetches) or in the JSON body.  No cookies, no query-string exposure.
# ---------------------------------------------------------------------------

UPLOAD_ROOT = os.path.join(tempfile.gettempdir(), "pdf_filler_uploads")

# Startup cleanup – wipe orphaned folders from previous server runs
if os.path.isdir(UPLOAD_ROOT):
    shutil.rmtree(UPLOAD_ROOT, ignore_errors=True)
os.makedirs(UPLOAD_ROOT, exist_ok=True)

SESSION_TIMEOUT = 60 * 60  # 1 hour

_sessions_lock = threading.Lock()
_sessions: dict[str, dict] = {}
# tid → {"engine": PDFEngine, "folder": str, "last_active": float}


def _create_session() -> tuple[str, PDFEngine, str]:
    """Create a brand-new isolated session.  Returns (tid, engine, folder)."""
    tid = uuid.uuid4().hex
    folder = os.path.join(UPLOAD_ROOT, tid)
    os.makedirs(folder, exist_ok=True)
    engine = PDFEngine()
    with _sessions_lock:
        _sessions[tid] = {
            "engine": engine,
            "folder": folder,
            "last_active": time.time(),
        }
    return tid, engine, folder


def _get_session(tid: str | None) -> dict | None:
    """Look up an existing session by tid.  Returns the entry or None."""
    if not tid:
        return None
    with _sessions_lock:
        entry = _sessions.get(tid)
        if entry:
            entry["last_active"] = time.time()
        return entry


def _tid_from_request() -> str | None:
    """Extract tid from the request – header first, then JSON body."""
    tid = request.headers.get("X-Tab-Id")
    if tid:
        return tid
    if request.is_json:
        return request.get_json(silent=True, cache=True).get("tid")
    return None


def _engine_and_folder() -> tuple[PDFEngine | None, str | None]:
    """Return (engine, folder) for the current request, or (None, None)."""
    entry = _get_session(_tid_from_request())
    if entry:
        return entry["engine"], entry["folder"]
    return None, None


# ---------------------------------------------------------------------------
# Background cleanup of stale sessions
# ---------------------------------------------------------------------------

def _cleanup_loop() -> None:
    while True:
        time.sleep(300)  # every 5 minutes
        now = time.time()
        with _sessions_lock:
            expired = [t for t, s in _sessions.items()
                       if now - s["last_active"] > SESSION_TIMEOUT]
            for tid in expired:
                entry = _sessions.pop(tid, None)
                if entry:
                    entry["engine"].close()
                    if os.path.isdir(entry["folder"]):
                        shutil.rmtree(entry["folder"], ignore_errors=True)

_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_cleanup_thread.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Always show the upload page – every tab starts fresh."""
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Handle PDF upload: create a new tab session and render the editor."""
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return redirect(url_for("index"))

    tid, engine, folder = _create_session()
    filepath = os.path.join(folder, "current.pdf")
    f.save(filepath)
    engine.open(filepath)

    page_w, page_h = engine.get_page_size(0)
    return render_template(
        "editor.html",
        tid=tid,
        page_count=engine.page_count,
        current_page=0,
        page_width=page_w,
        page_height=page_h,
    )


@app.route("/editor")
def editor():
    """Re-render the editor for page navigation (tid comes via header, page via query)."""
    entry = _get_session(request.headers.get("X-Tab-Id"))
    if not entry or not entry["engine"].is_open:
        return "Session expired", 404

    engine = entry["engine"]
    page = int(request.args.get("page", 0))
    page = max(0, min(page, engine.page_count - 1))
    page_w, page_h = engine.get_page_size(page)
    return jsonify({
        "page_count": engine.page_count,
        "current_page": page,
        "page_width": page_w,
        "page_height": page_h,
    })


@app.route("/page/<int:page_num>/image")
def page_image(page_num: int):
    """Return the rendered page as a PNG."""
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return "No PDF loaded", 404

    page_num = max(0, min(page_num, engine.page_count - 1))
    zoom = 2.0
    img = engine.render_page(page_num, zoom=zoom)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/page/<int:page_num>/annotations")
def page_annotations(page_num: int):
    """Return annotation data for the page as JSON."""
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"texts": [], "signatures": []})
    return jsonify(engine.get_annotations_json(page_num))


@app.route("/signature_image/<int:ann_id>")
def signature_image(ann_id: int):
    """Return the signature PNG for a given annotation id."""
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return "No PDF loaded", 404
    for pg in range(engine.page_count):
        pa = engine.get_annotations(pg)
        for sa in pa.signatures:
            if sa.id == ann_id:
                return send_file(io.BytesIO(sa.image_bytes), mimetype="image/png")
    return "Not found", 404


@app.route("/add_text", methods=["POST"])
def add_text():
    """Add a text annotation at the given position."""
    data = request.get_json()
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"ok": False, "error": "Session expired"}), 404

    page = int(data.get("page", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    text = data.get("text", "")
    font_size = float(data.get("font_size", 12))

    if text:
        engine.add_text(page, x, y, text, font_size=font_size)
    return jsonify({"ok": True})


@app.route("/add_signature", methods=["POST"])
def add_signature():
    """Add a signature (base64 PNG) at the given position."""
    data = request.get_json()
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"ok": False, "error": "Session expired"}), 404

    page = int(data.get("page", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    width = float(data.get("width", 150))
    height = float(data.get("height", 60))
    img_b64 = data.get("image", "")

    if img_b64:
        if "," in img_b64:
            img_b64 = img_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(img_b64)
        engine.add_signature(page, x, y, width, height, img_bytes)
    return jsonify({"ok": True})


@app.route("/move", methods=["POST"])
def move():
    """Move an annotation to a new position."""
    data = request.get_json()
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"ok": False}), 404

    page = int(data.get("page", 0))
    ann_id = int(data.get("id", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    ok = engine.move_annotation(page, ann_id, x, y)
    return jsonify({"ok": ok})


@app.route("/remove", methods=["POST"])
def remove():
    """Remove a specific annotation by id."""
    data = request.get_json()
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"ok": False}), 404

    page = int(data.get("page", 0))
    ann_id = int(data.get("id", 0))
    ok = engine.remove_annotation(page, ann_id)
    return jsonify({"ok": ok})


@app.route("/undo", methods=["POST"])
def undo():
    """Remove the last annotation on the given page."""
    data = request.get_json()
    engine, _ = _engine_and_folder()
    if not engine or not engine.is_open:
        return jsonify({"ok": False}), 404

    page = int(data.get("page", 0))
    removed = engine.remove_last_annotation(page)
    return jsonify({"ok": removed})


@app.route("/save", methods=["POST"])
def save():
    """Save the annotated PDF and send it for download."""
    engine, folder = _engine_and_folder()
    if not engine or not engine.is_open:
        return "No PDF loaded", 404

    output_path = os.path.join(folder, "filled_output.pdf")
    engine.save(output_path)
    return send_file(output_path, as_attachment=True,
                     download_name="filled.pdf", mimetype="application/pdf")


@app.route("/clear", methods=["POST"])
def clear():
    """Close the current PDF session and clean up."""
    tid = _tid_from_request()
    entry = _get_session(tid)
    if entry:
        entry["engine"].close()
        with _sessions_lock:
            _sessions.pop(tid, None)
        if os.path.isdir(entry["folder"]):
            shutil.rmtree(entry["folder"], ignore_errors=True)
    return jsonify({"ok": True})


@app.route("/go_page", methods=["POST"])
def go_page():
    """Re-render the editor at a different page (form POST from JS)."""
    tid = request.form.get("tid", "")
    page = int(request.form.get("page", 0))
    entry = _get_session(tid)
    if not entry or not entry["engine"].is_open:
        return redirect(url_for("index"))

    engine = entry["engine"]
    page = max(0, min(page, engine.page_count - 1))
    page_w, page_h = engine.get_page_size(page)
    return render_template(
        "editor.html",
        tid=tid,
        page_count=engine.page_count,
        current_page=page,
        page_width=page_w,
        page_height=page_h,
    )



