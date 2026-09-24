"""Text from PDF statements and images, on this Mac only.

- PDFs with a text layer: pdfplumber, keeping the page layout so table columns stay
  on one line.
- Scanned pages and images (PNG, JPEG, HEIC, TIFF): Apple's on-device OCR (Vision
  framework), rebuilt into lines by position. Language correction is off so digits
  are never "corrected".
Nothing is sent anywhere; the text then goes to the local model (document_reader).
"""

import io
import sys
from dataclasses import dataclass
from statistics import median
from typing import Callable

MAX_PAGES = 20
MAX_CHARS = 40_000
MAX_PIXELS = 25_000_000   # per rendered page or image: bounds memory for hostile files
MIN_PAGE_TEXT = 20  # a page with less text than this is treated as scanned


@dataclass(frozen=True)
class Extracted:
    text: str
    method: str      # pdf-text | ocr | mixed
    pages: int
    truncated: bool


def detect(raw: bytes) -> str | None:
    """pdf | image | text (CSV and friends) | None, from the content itself."""
    head = raw[:16]
    if head.startswith(b"%PDF"):
        return "pdf"
    if (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:4] in (b"II*\x00", b"MM\x00*")
            or head[4:8] == b"ftyp" or head.startswith(b"GIF8")):
        return "image"
    try:
        sample = raw[:4096].decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return "text" if sample and all(c.isprintable() or c in "\r\n\t" for c in sample) else None


def _skew(observations) -> float:
    """The page's tilt: the median slope of the recognised text boxes' top edges."""
    slopes = []
    for obs in observations:
        try:
            tl, tr = obs.topLeft(), obs.topRight()
        except AttributeError:
            continue
        if tr.x - tl.x > 1e-6:
            slopes.append((tr.y - tl.y) / (tr.x - tl.x))
    return median(slopes) if slopes else 0.0


def _ocr_lines(observations) -> list[str]:
    """Group recognised text boxes into lines, left to right, after undoing the page's tilt
    so a slightly skewed scan doesn't split or mix rows."""
    slope = _skew(observations)
    boxes = []
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        box = obs.boundingBox()  # normalised, origin bottom-left
        cx, cy = box.origin.x + box.size.width / 2, box.origin.y + box.size.height / 2
        level = cy - slope * cx  # the row's height with the tilt removed
        boxes.append((-level, box.origin.x, box.size.width, box.size.height, str(candidates[0].string())))
    if not boxes:
        return []
    tolerance = median(b[3] for b in boxes) * 0.5
    lines: list[list[tuple]] = []
    for box in sorted(boxes, key=lambda b: (b[0], b[1])):
        if lines and abs(sum(b[0] for b in lines[-1]) / len(lines[-1]) - box[0]) <= tolerance:
            lines[-1].append(box)
        else:
            lines.append([box])
    out = []
    for line in lines:
        parts = sorted(line, key=lambda b: b[1])
        text = parts[0][4]
        for prev, cur in zip(parts, parts[1:]):
            gap = cur[1] - (prev[1] + prev[2])
            text += ("    " if gap > 0.02 else " ") + cur[4]
        out.append(text)
    return out


def _image_pixels(data) -> int:
    """Pixel count from the image header (ImageIO), without decoding it."""
    import Quartz

    source = Quartz.CGImageSourceCreateWithData(data, None)
    props = Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None) if source else None
    if not props:
        raise ValueError("this image can't be read")
    return int(props.get("PixelWidth", 0)) * int(props.get("PixelHeight", 0))


def ocr(image_bytes: bytes) -> str:
    if sys.platform != "darwin":
        raise ValueError("reading images needs macOS (Apple's on-device text recognition)")
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
    if _image_pixels(data) > 4 * MAX_PIXELS:
        raise ValueError("this image is too large to read")
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise ValueError(f"text recognition failed: {error}")
    return "\n".join(_ocr_lines(request.results() or []))


def _render_page(pdf_bytes: bytes, index: int) -> bytes:
    import pypdfium2

    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        page = document[index]
        width, height = page.get_size()  # points
        scale = min(2.5, (MAX_PIXELS / max(width * height, 1)) ** 0.5)
        if scale < 0.5:
            raise ValueError("a page in this PDF is too large to read")
        image = page.render(scale=scale).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


Progress = Callable[..., None]  # progress(stage, step=0, steps=0)


def quiet(stage: str, step: int = 0, steps: int = 0) -> None:
    """The default progress listener: nobody is watching."""


def _pdf(raw: bytes, progress: Progress = quiet) -> Extracted:
    import pdfplumber

    texts, methods = [], set()
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages = len(pdf.pages)
        read = min(pages, MAX_PAGES)
        for i, page in enumerate(pdf.pages[:MAX_PAGES]):
            progress(f"Reading page {i + 1} of {read}", i + 1, read)
            text = page.extract_text(layout=True) or ""
            if len(text.strip()) >= MIN_PAGE_TEXT:
                methods.add("pdf-text")
            else:
                text = ocr(_render_page(raw, i))
                methods.add("ocr")
            lines = [line.rstrip() for line in text.splitlines() if line.strip()]
            texts.append("--- page ---\n" + "\n".join(lines))  # no number: it would count as "printed"
    text = "\n".join(texts)
    method = methods.pop() if len(methods) == 1 else "mixed"
    return Extracted(text[:MAX_CHARS], method, pages, pages > MAX_PAGES or len(text) > MAX_CHARS)


def extract(raw: bytes, progress: Progress = quiet) -> Extracted:
    """progress(stage, step, steps) hears which page is being read."""
    kind = detect(raw)
    if kind not in ("pdf", "image"):
        raise ValueError("expected a PDF, image or CSV file")
    try:
        if kind == "pdf":
            return _pdf(raw, progress)
        progress("Recognizing the text in the image")
        text = ocr(raw)
        return Extracted(text[:MAX_CHARS], "ocr", 1, len(text) > MAX_CHARS)
    except ValueError:
        raise
    except Exception as exc:  # parser errors on damaged, encrypted or hostile files
        raise ValueError(f"this file can't be read ({type(exc).__name__}); it may be damaged or "
                         "password-protected") from None
