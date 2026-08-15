"""Reading what can actually be read out of an attached image.

Attaching an image to an assistant that cannot see is a trap, and the trap is the
whole design problem here. `llama3.1:8b` is not multimodal, and `Message.content`
is a plain string -- there is no path through the brain layer for an image at all.
So if a picture were simply accepted and filed, the next question about it would be
answered from the *filename*, fluently and wrongly. That is the same failure as
answering "top gainers" from a page about something else: sourced-looking, and
about the wrong thing.

So this module's job is not to describe images. It is to work out what is honestly
knowable about one and say so:

* what the file is -- format, size, dimensions,
* any text in it, if OCR is available (a screenshot is mostly text, and screenshots
  are what people paste),
* and, plainly, that the model cannot see the picture itself.

The last of those is the important one. Everything the model is told about an
attached image is framed so that describing its appearance is not an option, in
the same shape as `data.no_data_prompt` and `quotes.no_quote_prompt`.

OCR is optional. `pytesseract` also needs the Tesseract binary, which is a real
install on Windows, so its absence is a normal state and is reported as one rather
than as an error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

#: Enough of an image's text to be useful without swamping the context.
MAX_OCR_CHARS = 6000

#: Magic bytes, checked rather than trusting the extension. The same reasoning as
#: images._download: a file claiming to be a PNG and containing something else is
#: not a picture, whatever it is called.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"BM", "BMP"),
)


def is_image(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in IMAGE_SUFFIXES


def sniff(data: bytes) -> str:
    """The format from the bytes themselves, or "" if it is not an image."""
    blob = bytes(data or b"")
    for signature, label in _SIGNATURES:
        if blob.startswith(signature):
            return label
    # WEBP is "RIFF....WEBP".
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "WEBP"
    return ""


def dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) if it can be worked out, else None.

    Pillow arrives with python-pptx, so it is usually present when documents are
    enabled -- but it is not a declared dependency of this project and must not
    become one for an image to be attachable.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(bytes(data or b""))) as picture:
            return int(picture.width), int(picture.height)
    except Exception:
        return None


def _human_size(count: int) -> str:
    size = float(max(0, int(count)))
    for unit in ("bytes", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} MB"


def ocr(data: bytes) -> Tuple[str, str]:
    """(text, why_there_is_none). Never raises.

    Both halves matter. An empty result with no reason reads as "there was no
    text in the image", which is a different statement from "nothing here can
    read text" -- and the caller has to tell the user which.
    """
    try:
        import io

        from PIL import Image
    except Exception:
        return "", ("Pillow isn't installed, so I can't open the image to read "
                    "text from it (pip install pillow)")
    try:
        import pytesseract
    except Exception:
        return "", ("no OCR is installed, so I can't read any text in it "
                    "(pip install pytesseract, plus the Tesseract program)")

    try:
        with Image.open(io.BytesIO(bytes(data or b""))) as picture:
            text = pytesseract.image_to_string(picture)
    except Exception as exc:
        # Most often the Tesseract binary itself is missing, which is a different
        # problem from the Python package being absent and deserves saying.
        return "", f"OCR is installed but failed ({exc.__class__.__name__})"

    cleaned = "\n".join(line.rstrip() for line in str(text or "").splitlines())
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip(), ""


def summarise(name: str, data: bytes) -> str:
    """A short human note for the attachment chip: "PNG, 1024x576, 84.2 KB"."""
    blob = bytes(data or b"")
    parts = [sniff(blob) or (Path(name).suffix.lstrip(".").upper() or "image")]
    size = dimensions(blob)
    if size:
        parts.append(f"{size[0]}x{size[1]}")
    parts.append(_human_size(len(blob)))
    return ", ".join(parts)


def read(name: str, data: bytes, user_name: str = "you") -> Tuple[str, str, bool]:
    """(text_for_the_model, note_for_the_user, was_truncated).

    Deliberately the same shape as `readers.extract`, so the upload path treats an
    image like any other attachment -- except that what gets handed to the model is
    an instruction about what it does *not* know.
    """
    blob = bytes(data or b"")
    note = summarise(name, blob)
    text, why_not = ocr(blob)

    truncated = False
    if len(text) > MAX_OCR_CHARS:
        cut = text.rfind(" ", 0, MAX_OCR_CHARS)
        text = text[: cut if cut > MAX_OCR_CHARS // 2 else MAX_OCR_CHARS]
        truncated = True

    header = (
        f"[{user_name} attached an image: {name} ({note}).\n"
        f"You cannot see images. Do not describe it, guess at its appearance, or "
        f"infer anything from its filename -- a confident description of a picture "
        f"you have not seen is indistinguishable from having seen it, which is what "
        f"makes it worse than admitting the limit."
    )

    if text:
        body = (
            f"\nText read out of it by OCR follows. It may contain recognition "
            f"errors, and it carries no layout, so treat it as approximate and say "
            f"so if a detail matters:\n\n{text}\n]"
        )
        if truncated:
            body = body[:-1] + "\n(only the first part of the text is shown)]"
        return header + body, f"image · {note} · text read", truncated

    reason = why_not or "no text could be found in it"
    return (
        header
        + f"\nNo text is available from it either: {reason}.\n"
        f"So you know it exists and nothing about what it shows. Say that plainly "
        f"and ask {user_name} what is in it, or to paste the text.]",
        f"image · {note}" + ("" if why_not else " · no text found"),
        False,
    )
