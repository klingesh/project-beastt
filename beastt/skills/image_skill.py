"""Making a picture because someone asked for one.

`imagegen` could already generate artwork, but only slide rendering ever called
it. Asked "can you create an image on renewable energy resources", BEASTT replied
"I'm not a graphics tool" and suggested Canva -- while carrying a working Flux
backend it had no way to reach. This skill is that missing route.

The reply embeds the image as Markdown so the chat window can show it inline,
and states that it was generated rather than photographed. That disclosure is not
optional politeness: an image that looks like a photograph and isn't will end up
in somebody's coursework.
"""

from __future__ import annotations

import re
from typing import Optional

from .base import Skill
from .intent import directive

#: The nouns that mean "a picture", and the verbs that mean "make me one".
_NOUNS = (r"image|picture|photo|photograph|illustration|artwork|graphic|"
          r"drawing|painting|render|visual|logo|poster|banner|wallpaper|"
          r"thumbnail|icon|diagram|sketch")
_VERBS = (r"create|generate|make|draw|paint|design|produce|render|give\s+me|"
          r"show\s+me|do")

_ASK = re.compile(
    # "create an image of X", "draw me a picture showing X", "generate artwork X"
    rf"\b(?:{_VERBS})\b"
    rf"(?:\s+(?:me|us))?"
    rf"(?:\s+(?:a|an|some|the))?"
    rf"[^.\n]{{0,24}}?"
    rf"\b(?:{_NOUNS})\b"
    rf"(?:\s+(?:of|on|about|showing|depicting|for|with|that\s+shows|featuring))?"
    r"\s*(?P<subject>[^\n]*)",
    re.IGNORECASE,
)

#: A request that mentions a document is a document request that happens to
#: mention pictures. "Make a presentation with images about solar" belongs to
#: DocumentSkill, and matching it here would quietly replace a deck with a JPEG.
_DOCUMENT = re.compile(
    r"\b(presentation|powerpoint|power\s*point|ppt|pptx|slide|slides|deck|"
    r"document|word\s+file|docx|report|spreadsheet|excel|xlsx|essay)\b",
    re.IGNORECASE,
)

#: Aspect ratio, taken from the word the user chose rather than guessed.
_WIDE = re.compile(r"\b(wallpaper|banner|landscape|wide|header|cover|"
                   r"background|16\s*[:x]\s*9)\b", re.IGNORECASE)
_TALL = re.compile(r"\b(poster|portrait|tall|vertical|story|reel|"
                   r"9\s*[:x]\s*16)\b", re.IGNORECASE)

#: Trailing politeness that is not part of the subject.
_TRAILING = re.compile(
    r"\s*(?:please|for\s+me|thanks|thank\s+you|now|asap|quickly)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _subject(text: str) -> str:
    """The thing to draw, pulled out of the request."""
    found = directive(_ASK, text)
    if found is None:
        return ""
    raw = found.group("subject") or ""
    raw = _TRAILING.sub("", raw).strip(" \t\"'`.,:;-—")
    # "resources" style tails are fine; a bare preposition left over is not.
    raw = re.sub(r"^(?:of|on|about|for|with|showing|depicting)\s+", "", raw,
                 flags=re.IGNORECASE)
    return " ".join(raw.split())[:300]


def orientation_for(text: str) -> str:
    if _WIDE.search(text or ""):
        return "wide"
    if _TALL.search(text or ""):
        return "tall"
    # Square by default: it is the shape anonymous Pollinations reliably
    # returns, and it sits well in a chat bubble.
    return "square"


class ImageSkill(Skill):
    name = "image"

    def __init__(self, config, on_created=None, progress=None):
        self.config = config
        self._on_created = on_created
        self._progress_sink = progress

    def _progress(self, message: str) -> None:
        if self._progress_sink:
            try:
                self._progress_sink(message)
            except Exception:
                pass

    def matches(self, text: str) -> bool:
        if not getattr(self.config, "imagegen_enabled", False):
            return False
        body = text or ""
        if _DOCUMENT.search(body):
            return False
        return directive(_ASK, body) is not None

    def run(self, text: str) -> str:
        from ..imagegen import ImageMaker, art_dir, available, save_as_art

        subject = _subject(text)
        if not subject:
            return ("I can draw that -- what should be in it? Tell me the "
                    "subject, like \"an image of a wind farm at sunrise\".")

        if not available(self.config):
            return ("Image generation is switched off, so I can't make that "
                    "right now. Set BEASTT_IMAGE_GEN=on in your .env, or add "
                    "BEASTT_CF_ACCOUNT and BEASTT_CF_TOKEN for Cloudflare.")

        orientation = orientation_for(text)
        self._progress(f"Drawing \"{subject[:60]}\"...")

        maker = ImageMaker(self.config, verbose=True)
        picture = maker.make(subject, orientation=orientation)
        if picture is None:
            return (f"I tried to draw \"{subject}\" but the image service "
                    "wouldn't answer. Worth another go in a minute -- the free "
                    "backends are busy sometimes.")

        try:
            saved = save_as_art(picture, subject)
        except Exception as exc:
            print(f"[image] couldn't move into {art_dir()}: {exc}")
            saved = picture

        if self._on_created:
            try:
                self._on_created(saved.path)
            except Exception:
                pass

        # Markdown so the chat can render it inline; the path so it can be found
        # on disk; the attribution so nobody mistakes it for a photograph.
        return (
            f"Here's \"{subject}\":\n\n"
            f"![{subject}](/api/art/{saved.path.name})\n\n"
            f"AI-generated with {saved.creator}, so it isn't a photograph.\n"
            f"Saved to {saved.path}"
        )
