"""Text from PDFs and images, on this Mac (pdfplumber; Apple Vision OCR)."""

import sys
from pathlib import Path

import pytest

from glassfolio.extract import detect, extract

DOCS = Path(__file__).parent.parent / "evals" / "documents"
mac = pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision OCR is macOS-only")


def test_detects_by_content_not_name():
    assert detect((DOCS / "statement.pdf").read_bytes()) == "pdf"
    assert detect((DOCS / "statement.png").read_bytes()) == "image"
    assert detect(b"Symbol,Quantity\nAAA,1\n") == "text"
    assert detect(b"\x00\x01binary junk") is None


def test_pdf_text_layer_keeps_rows_together():
    out = extract((DOCS / "statement.pdf").read_bytes())
    assert out.method == "pdf-text" and out.pages == 1
    nvda = next(line for line in out.text.splitlines() if "NVDA" in line)
    assert "12" in nvda and "$110.00" in nvda and "$1,320.00" in nvda


@mac
def test_image_is_read_with_ocr_row_by_row():
    out = extract((DOCS / "statement.png").read_bytes())
    assert out.method == "ocr"
    qqq = next(line for line in out.text.splitlines() if "QQQ" in line)
    assert "$52.00" in qqq and "$5,200.00" in qqq
    assert "11,350.00" in out.text


@mac
def test_scanned_pdf_falls_back_to_ocr():
    out = extract((DOCS / "statement_scanned.pdf").read_bytes())
    assert out.method == "ocr" and "GFOF" in out.text and "$1,050.00" in out.text


def test_unreadable_input_is_a_clear_error():
    with pytest.raises(ValueError, match="PDF, image"):
        extract(b"\x00\x01binary junk")


class _P:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Box:
    def __init__(self, x, y, w, h):
        self.origin, self.size = _P(x, y), type("S", (), {"width": w, "height": h})()


class _Obs:
    """A fake Vision observation: text in a box on a page tilted by `slope` (dy/dx)."""
    def __init__(self, text, x, y, w=0.08, h=0.012, slope=0.0):
        self.text, self.slope = text, slope
        y = y + slope * x
        self.box = _Box(x, y, w, h)
        self.tl, self.tr = _P(x, y + h), _P(x + w, y + h + slope * w)

    def topCandidates_(self, n):
        return [type("C", (), {"string": lambda _self: self.text})()]

    def boundingBox(self):
        return self.box

    def topLeft(self):
        return self.tl

    def topRight(self):
        return self.tr


def test_ocr_lines_survive_a_skewed_scan():
    from glassfolio.extract import _ocr_lines
    slope = 0.026  # about 1.5 degrees
    rows = [("NVDA", "12", "$1,320.00"), ("QQQ", "100", "$5,200.00"), ("GFOF", "50", "$1,050.00")]
    obs = [_Obs(t, x, 0.8 - i * 0.02, slope=slope)
           for i, row in enumerate(rows) for t, x in zip(row, (0.05, 0.4, 0.8))]
    lines = _ocr_lines(obs)
    assert [l.split() for l in lines] == [list(r) for r in rows]
