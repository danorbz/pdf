"""
pdf_engine.py – Back-end logic for loading, rendering, annotating, and saving PDFs.

Uses pypdf for reading/writing, reportlab for overlaying text & images,
and pdf2image (poppler) for rendering pages to preview images.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from pdf2image import convert_from_path


# ---------------------------------------------------------------------------
# Data classes that represent things the user has placed on a page
# ---------------------------------------------------------------------------

@dataclass
class TextAnnotation:
    """A piece of text the user wants to burn into a PDF page."""
    id: int   # unique id within the page
    x: float  # position in PDF points (from left)
    y: float  # position in PDF points (from top – will be flipped for save)
    text: str
    font_size: float = 12.0
    font_name: str = "Helvetica"
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # RGB 0-1


@dataclass
class SignatureAnnotation:
    """A signature image the user wants to place on a PDF page."""
    id: int   # unique id within the page
    x: float
    y: float
    width: float
    height: float
    image_bytes: bytes  # PNG image data


@dataclass
class PageAnnotations:
    """All annotations for a single page."""
    texts: List[TextAnnotation] = field(default_factory=list)
    signatures: List[SignatureAnnotation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class PDFEngine:
    """Handles PDF document operations: open, render, annotate, save."""

    def __init__(self) -> None:
        self._reader: Optional[PdfReader] = None
        self._path: Optional[str] = None
        self._annotations: Dict[int, PageAnnotations] = {}
        self._next_id: int = 0  # global annotation counter

    # -- File operations -----------------------------------------------------

    def open(self, path: str) -> None:
        """Open a PDF file."""
        self._reader = PdfReader(path)
        self._path = path
        self._annotations.clear()

    @property
    def is_open(self) -> bool:
        return self._reader is not None

    @property
    def page_count(self) -> int:
        return len(self._reader.pages) if self._reader else 0

    def close(self) -> None:
        self._reader = None
        self._path = None
        self._annotations.clear()

    # -- Rendering -----------------------------------------------------------

    def render_page(self, page_num: int, zoom: float = 2.0) -> Image.Image:
        """Render a page to a PIL Image for display in the GUI.

        *zoom* controls resolution; 2.0 ≈ 144 DPI.
        """
        if not self._reader or not self._path:
            raise RuntimeError("No document is open.")

        dpi = int(72 * zoom)
        images = convert_from_path(
            self._path,
            dpi=dpi,
            first_page=page_num + 1,
            last_page=page_num + 1,
        )
        if not images:
            raise RuntimeError(f"Failed to render page {page_num}")
        return images[0]

    def get_page_size(self, page_num: int) -> Tuple[float, float]:
        """Return (width, height) of a page in PDF points."""
        if not self._reader:
            raise RuntimeError("No document is open.")
        page = self._reader.pages[page_num]
        box = page.mediabox
        return float(box.width), float(box.height)

    # -- Annotation management -----------------------------------------------

    def _ensure_page(self, page_num: int) -> PageAnnotations:
        if page_num not in self._annotations:
            self._annotations[page_num] = PageAnnotations()
        return self._annotations[page_num]

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def add_text(self, page_num: int, x: float, y: float, text: str,
                 font_size: float = 12.0,
                 color: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> TextAnnotation:
        ann = TextAnnotation(id=self._new_id(), x=x, y=y, text=text, font_size=font_size, color=color)
        self._ensure_page(page_num).texts.append(ann)
        return ann

    def add_signature(self, page_num: int, x: float, y: float,
                      width: float, height: float,
                      image_bytes: bytes) -> SignatureAnnotation:
        ann = SignatureAnnotation(id=self._new_id(), x=x, y=y, width=width, height=height,
                                 image_bytes=image_bytes)
        self._ensure_page(page_num).signatures.append(ann)
        return ann

    def remove_last_annotation(self, page_num: int) -> bool:
        """Undo the last annotation on *page_num*."""
        pa = self._annotations.get(page_num)
        if not pa:
            return False
        if pa.signatures:
            pa.signatures.pop()
            return True
        if pa.texts:
            pa.texts.pop()
            return True
        return False

    def get_annotations(self, page_num: int) -> PageAnnotations:
        return self._annotations.get(page_num, PageAnnotations())

    def move_annotation(self, page_num: int, ann_id: int, x: float, y: float) -> bool:
        """Move any annotation (text or signature) to a new position."""
        pa = self._annotations.get(page_num)
        if not pa:
            return False
        for ta in pa.texts:
            if ta.id == ann_id:
                ta.x = x
                ta.y = y
                return True
        for sa in pa.signatures:
            if sa.id == ann_id:
                sa.x = x
                sa.y = y
                return True
        return False

    def remove_annotation(self, page_num: int, ann_id: int) -> bool:
        """Remove a specific annotation by id."""
        pa = self._annotations.get(page_num)
        if not pa:
            return False
        for i, ta in enumerate(pa.texts):
            if ta.id == ann_id:
                pa.texts.pop(i)
                return True
        for i, sa in enumerate(pa.signatures):
            if sa.id == ann_id:
                pa.signatures.pop(i)
                return True
        return False

    def get_annotations_json(self, page_num: int) -> dict:
        """Return annotations as a JSON-serialisable dict."""
        pa = self.get_annotations(page_num)
        return {
            "texts": [
                {"id": t.id, "x": t.x, "y": t.y, "text": t.text,
                 "font_size": t.font_size}
                for t in pa.texts
            ],
            "signatures": [
                {"id": s.id, "x": s.x, "y": s.y,
                 "width": s.width, "height": s.height}
                for s in pa.signatures
            ],
        }

    # -- Save ----------------------------------------------------------------

    def _build_overlay_pdf(self, page_num: int,
                           page_width: float, page_height: float) -> Optional[bytes]:
        """Create a single-page transparent PDF overlay with annotations via ReportLab."""
        pa = self._annotations.get(page_num)
        if not pa or (not pa.texts and not pa.signatures):
            return None

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(page_width, page_height))

        # Texts – user coords are top-left; ReportLab is bottom-left
        for ta in pa.texts:
            c.setFont(ta.font_name, ta.font_size)
            c.setFillColorRGB(*ta.color)
            pdf_y = page_height - ta.y
            c.drawString(ta.x, pdf_y, ta.text)

        # Signatures
        for sa in pa.signatures:
            img = Image.open(io.BytesIO(sa.image_bytes))
            img_reader = ImageReader(img)
            pdf_y = page_height - sa.y - sa.height
            c.drawImage(img_reader, sa.x, pdf_y,
                        width=sa.width, height=sa.height,
                        mask='auto', preserveAspectRatio=False)

        c.save()
        return buf.getvalue()

    def save(self, output_path: str) -> None:
        """Burn all annotations into the PDF and save to *output_path*."""
        if not self._reader:
            raise RuntimeError("No document is open.")

        writer = PdfWriter()

        for i, page in enumerate(self._reader.pages):
            pw, ph = self.get_page_size(i)
            overlay_bytes = self._build_overlay_pdf(i, pw, ph)

            if overlay_bytes:
                overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
                page.merge_page(overlay_reader.pages[0])

            writer.add_page(page)

        with open(output_path, "wb") as f:
            writer.write(f)
