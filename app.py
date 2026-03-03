"""
app.py – Flask web application for PDF Form Filler.

Routes:
  /                  – Main page (upload or editor)
  /upload            – POST: upload a PDF
  /page/<n>/image    – GET: rendered page image as PNG
  /add_text          – POST: add text annotation
  /add_signature     – POST: add signature annotation
  /undo              – POST: undo last annotation on current page
  /save              – GET: download the filled PDF
  /clear             – POST: close current PDF and start over
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, session, jsonify,
)
from PIL import Image

from pdf_engine import PDFEngine

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Store uploads in a temp directory
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "pdf_filler_uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Global engine instance (single-user app)
engine = PDFEngine()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Show upload form or editor depending on state."""
    page = int(request.args.get("page", 0))
    if engine.is_open:
        page = max(0, min(page, engine.page_count - 1))
        page_w, page_h = engine.get_page_size(page)
        return render_template(
            "editor.html",
            page_count=engine.page_count,
            current_page=page,
            page_width=page_w,
            page_height=page_h,
        )
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Handle PDF file upload."""
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return redirect(url_for("index"))

    filepath = os.path.join(UPLOAD_FOLDER, "current.pdf")
    f.save(filepath)
    engine.close()
    engine.open(filepath)
    return redirect(url_for("index"))


@app.route("/page/<int:page_num>/image")
def page_image(page_num: int):
    """Return the rendered page as a clean PNG (no annotations baked in)."""
    if not engine.is_open:
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
    if not engine.is_open:
        return jsonify({"texts": [], "signatures": []})
    return jsonify(engine.get_annotations_json(page_num))


@app.route("/signature_image/<int:ann_id>")
def signature_image(ann_id: int):
    """Return the signature PNG for a given annotation id."""
    if not engine.is_open:
        return "No PDF loaded", 404
    # Search all pages for the signature
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
    page = int(data.get("page", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    text = data.get("text", "")
    font_size = float(data.get("font_size", 12))

    if text and engine.is_open:
        engine.add_text(page, x, y, text, font_size=font_size)
    return jsonify({"ok": True})


@app.route("/add_signature", methods=["POST"])
def add_signature():
    """Add a signature at the given position.

    Expects JSON with base64-encoded PNG data from the signature pad.
    """
    data = request.get_json()
    page = int(data.get("page", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    width = float(data.get("width", 150))
    height = float(data.get("height", 60))
    img_b64 = data.get("image", "")

    if img_b64 and engine.is_open:
        # Strip data URL prefix if present
        if "," in img_b64:
            img_b64 = img_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(img_b64)
        engine.add_signature(page, x, y, width, height, img_bytes)
    return jsonify({"ok": True})


@app.route("/move", methods=["POST"])
def move():
    """Move an annotation to a new position."""
    data = request.get_json()
    page = int(data.get("page", 0))
    ann_id = int(data.get("id", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    ok = engine.move_annotation(page, ann_id, x, y) if engine.is_open else False
    return jsonify({"ok": ok})


@app.route("/remove", methods=["POST"])
def remove():
    """Remove a specific annotation by id."""
    data = request.get_json()
    page = int(data.get("page", 0))
    ann_id = int(data.get("id", 0))
    ok = engine.remove_annotation(page, ann_id) if engine.is_open else False
    return jsonify({"ok": ok})


@app.route("/undo", methods=["POST"])
def undo():
    """Remove the last annotation on the given page."""
    data = request.get_json()
    page = int(data.get("page", 0))
    removed = engine.remove_last_annotation(page) if engine.is_open else False
    return jsonify({"ok": removed})


@app.route("/save")
def save():
    """Save the annotated PDF and send it for download."""
    if not engine.is_open:
        return "No PDF loaded", 404

    output_path = os.path.join(UPLOAD_FOLDER, "filled_output.pdf")
    engine.save(output_path)
    return send_file(output_path, as_attachment=True,
                     download_name="filled.pdf", mimetype="application/pdf")


@app.route("/clear", methods=["POST"])
def clear():
    """Close the current PDF and go back to upload."""
    engine.close()
    return redirect(url_for("index"))



