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
    rf"\b(?P<noun>{_NOUNS})\b"
    rf"(?:\s+(?:of|on|about|showing|depicting|for|with|that\s+shows|featuring))?"
    r"\s*(?P<subject>[^\n]*)",
    re.IGNORECASE,
)

_DOCUMENT_NOUNS = (r"presentation|powerpoint|power\s*point|ppt|pptx|slides?|"
                   r"deck|document|word\s+file|docx|report|spreadsheet|excel|"
                   r"xlsx|essay")

#: A document request that happens to mention pictures is still a document
#: request -- but only when a verb actually governs the document noun.
#:
#: Merely looking for the word was too blunt and broke a real request: an image
#: prompt ending "leave clean negative space on one side for adding presentation
#: text" was rejected as a deck, so a carefully written art brief went to the
#: model, which replied with a JSON plan instead of a picture. "presentation
#: text" is not a request for a presentation.
_DOCUMENT_REQUEST = re.compile(
    rf"\b(?:{_VERBS}|prepare|write|build|put\s+together)\b"
    rf"(?:\s+(?:me|us))?"
    rf"(?:\s+(?:a|an|some|the|another))?"
    rf"[^.\n]{{0,24}}?"
    rf"\b(?P<noun>{_DOCUMENT_NOUNS})\b",
    re.IGNORECASE,
)

#: Aspect ratio, taken from what the user actually asked for rather than guessed.
_WIDE = re.compile(r"\b(wallpaper|banner|landscape|wide|header|cover|"
                   r"background|16\s*[:x]\s*9|3\s*[:x]\s*2)\b", re.IGNORECASE)
_TALL = re.compile(r"\b(poster|portrait|tall|vertical|story|reel|"
                   r"9\s*[:x]\s*16|3\s*[:x]\s*4|2\s*[:x]\s*3)\b", re.IGNORECASE)
#: 4:3 is neither 16:9 nor square, and someone who names it means it.
_CLASSIC = re.compile(r"\b4\s*[:x]\s*3\b", re.IGNORECASE)

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
    # Generous, because a considered art brief is long and every clause of it
    # matters. An earlier 300-character cap silently discarded the second half of
    # one -- including the "DSLR photography, documentary-style realism" that was
    # the whole point of it. Pollinations accepts about 1500 characters.
    return " ".join(raw.split())[:1200]


def orientation_for(text: str) -> str:
    body = text or ""
    # An explicit ratio beats a noun: "a 4:3 poster" is 4:3.
    if _CLASSIC.search(body):
        return "classic"
    if _TALL.search(body):
        return "tall"
    if _WIDE.search(body):
        return "wide"
    # Square by default: it is the shape anonymous Pollinations reliably
    # returns, and it sits well in a chat bubble.
    return "square"


class ImageSkill(Skill):
    name = "image"

    def __init__(self, config, on_created=None, progress=None,
                 brain_provider=None):
        self.config = config
        self._on_created = on_created
        self._progress_sink = progress
        #: Used to expand a bare topic into a scene. Optional: without it, short
        #: requests still work, they are just less interesting.
        self._brain_provider = brain_provider

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
        asked = directive(_ASK, body)
        if asked is None:
            return False
        # Compare where the *nouns* are, not where the matches begin. Both
        # patterns start at the same verb in "generate an image with space for
        # presentation text", so comparing match starts handed that to
        # DocumentSkill. The noun the verb reaches first is the thing being asked
        # for: "image" at 12 beats "presentation" at 33, and in "make a
        # presentation with images" it is the other way round.
        document = _DOCUMENT_REQUEST.search(body)
        if document is not None and document.start("noun") < asked.start("noun"):
            return False
        return True

    def _expanded(self, subject: str) -> str:
        """A scene description for a bare topic, or "" to use the subject as-is.

        Only for short requests. Someone who wrote a paragraph of art direction
        has already done this, and rewriting it would discard their choices.
        """
        from ..imagegen import BRIEF_PROMPT, expand_prompt

        if len(subject) >= BRIEF_PROMPT or self._brain_provider is None:
            return ""
        try:
            brain = self._brain_provider()
        except Exception:
            return ""
        self._progress("Deciding what the picture should show...")
        return expand_prompt(brain, subject)

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
        scene = self._expanded(subject)
        self._progress(f"Drawing \"{(scene or subject)[:70]}\"...")

        maker = ImageMaker(self.config, verbose=True)
        # fresh=True: asking twice should give two pictures, not the same one.
        picture = maker.make(subject, orientation=orientation, prompt=scene,
                             fresh=True)
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
        lines = [
            f"Here's \"{subject}\":",
            "",
            f"![{subject}](/api/art/{saved.path.name})",
            "",
            f"AI-generated with {saved.creator}, so it isn't a photograph.",
        ]
        if scene:
            # Show the scene it invented. Without this the only way to steer a
            # disappointing result is guesswork, and a one-word request gives no
            # clue which of the choices was the assistant's rather than yours.
            lines.append(f"I drew it as: {scene}")
            lines.append("Say \"again\" for a different take, or describe the "
                         "scene yourself to take control of it.")
        shape = self._shape_caveat(orientation, saved)
        if shape:
            lines.append(shape)
        lines.append(f"Saved to {saved.path}")
        return "\n".join(lines)

    def _shape_caveat(self, orientation: str, saved) -> str:
        """Own up when the requested shape could not be delivered.

        Anonymous Pollinations rejects non-square requests, so asking for 4:3 and
        silently receiving 1:1 would look like the instruction was ignored. Say
        which it is, and how to lift the restriction.
        """
        if orientation == "square":
            return ""
        if "Pollinations" not in (saved.creator or ""):
            return ""
        if str(getattr(self.config, "pollinations_key", "") or ""):
            return ""
        return ("Note: it's square — Pollinations only returns square images "
                "without a key. Add BEASTT_POLLINATIONS_KEY to your .env for "
                "other shapes, or BEASTT_CF_ACCOUNT/BEASTT_CF_TOKEN to use "
                "Cloudflare instead.")
