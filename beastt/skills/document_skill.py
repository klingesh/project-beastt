"""Create real Office documents from a spoken/typed idea.

Examples that trigger this skill:
    "make a ppt about renewable energy"
    "create a word document on the history of cricket"
    "generate an excel sheet of monthly expenses"

The skill only detects intent and extracts the topic; the model plans the
content (docgen) and the renderers produce the file (documents).
"""

from __future__ import annotations

import re

from .base import Skill

# Which words mean which format.
_KINDS = (
    ("presentation", r"\b(ppt|power\s?point|powerpoint|presentation|slide\s?deck|slides|deck)\b"),
    ("spreadsheet", r"\b(excel|spread\s?sheet|spreadsheet|xlsx|work\s?book|csv\s?sheet)\b"),
    ("document", r"\b(word\s+document|word\s+doc|word\s+file|\bdocx\b|word|document|report|essay|letter|article)\b"),
)

_VERB = r"(?:make|create|generate|build|prepare|draft|write|do|design|put\s+together|give\s+me)"
_TRIGGER = re.compile(
    rf"\b{_VERB}\b[^.?!]*\b(ppt|power\s?point|powerpoint|presentation|slide\s?deck|slides|deck|"
    r"excel|spread\s?sheet|spreadsheet|xlsx|work\s?book|word\s+document|word\s+doc|word\s+file|"
    r"docx|document|report|essay)\b",
    re.IGNORECASE,
)

# Strip everything up to and including the topic preposition.
_TOPIC = re.compile(
    r"\b(?:about|on|for|regarding|covering|of|titled|explaining)\b\s+(.*)$", re.IGNORECASE
)
_LEAD = re.compile(
    rf"^\s*(?:hey\s+[a-z]{{4,10}}[,\s]+)?(?:please\s+)?{_VERB}\s+(?:me\s+)?(?:a|an|the)?\s*",
    re.IGNORECASE,
)
_FORMAT_WORDS = re.compile(
    r"\b(ppt|power\s?point|powerpoint|presentation|slide\s?deck|slides|deck|excel|"
    r"spread\s?sheet|spreadsheet|xlsx|work\s?book|word\s+document|word\s+doc|word\s+file|"
    r"docx|word|document|report|essay|file|sheet)\b",
    re.IGNORECASE,
)


_REPORT = re.compile(r"\breports?\b", re.IGNORECASE)


def is_report(text: str) -> bool:
    """A report is a stricter kind of Word document (fixed serif typography)."""
    return bool(_REPORT.search(text))


def detect_kind(text: str) -> str:
    for kind, pattern in _KINDS:
        if re.search(pattern, text, re.IGNORECASE):
            return kind
    return "document"


def extract_topic(text: str) -> str:
    """Pull the subject out of the request."""
    match = _TOPIC.search(text)
    topic = match.group(1) if match else _LEAD.sub("", text)
    if not match:
        topic = _FORMAT_WORDS.sub(" ", topic)
    topic = re.sub(r"[?!.]+$", "", topic)
    topic = re.sub(r"\s+", " ", topic).strip(" ,.-")
    return topic


class DocumentSkill(Skill):
    name = "documents"

    #: Human-friendly names for spoken replies.
    _LABEL = {
        "presentation": "PowerPoint presentation",
        "document": "Word document",
        "spreadsheet": "Excel spreadsheet",
    }

    def __init__(self, brain_provider, on_created=None):
        # A callable so the skill always uses the assistant's current brain.
        self._brain_provider = brain_provider
        self._on_created = on_created
        self.last_path = None
        # Holds the document currently being worked on, so follow-up
        # instructions revise it instead of starting from scratch.
        from ..workshop import Workshop

        self.workshop = Workshop()

    def matches(self, text: str) -> bool:
        if _TRIGGER.search(text):
            return True
        # While a document is open, claim instruction-shaped follow-ups.
        return self.workshop.looks_like_revision(text)

    def run(self, text: str) -> str:
        # An open document takes priority: "add a slide about costs" should edit
        # what we just made rather than trigger a brand-new file.
        if self.workshop.active and not _TRIGGER.search(text):
            if self.workshop.is_done(text):
                path = self.workshop.path
                revisions = self.workshop.revisions
                self.workshop.close()
                tail = f" after {revisions} revision{'s' if revisions != 1 else ''}" if revisions else ""
                return (
                    f"Great -- finished{tail}. It's saved at:\n{path}\n"
                    "Say \"push it to GitHub\" whenever you want it uploaded."
                )
            reply = self.workshop.revise(self._brain_provider(), text)
            if self.workshop.path:
                self.last_path = self.workshop.path
                if self._on_created:
                    try:
                        self._on_created(self.workshop.path)
                    except Exception:
                        pass
            return reply

        return self._create(text)

    def _create(self, text: str) -> str:
        from ..config import Config
        from ..docgen import plan
        from ..documents import MissingLibrary, _resolve_theme, build

        kind = detect_kind(text)
        topic = extract_topic(text)
        label = self._LABEL[kind]

        if not topic:
            return f"Sure -- what should the {label} be about?"

        print(f"[docs] Writing a {kind} about {topic!r}...")
        spec = plan(self._brain_provider(), kind, topic)
        if not spec:
            return (
                f"I couldn't put together good content for that {label}. "
                "Try describing the topic a bit more specifically?"
            )

        config = Config.load()
        if kind == "document":
            spec["is_report"] = is_report(text)

        theme = _resolve_theme(spec, config.doc_theme)
        finder = None
        if kind == "presentation" and config.images_enabled:
            from ..images import ImageFinder

            finder = ImageFinder(enabled=True)
            print("[docs] Looking for suitable images...")

        try:
            path = build(kind, spec, theme_name=theme, finder=finder)
        except MissingLibrary as exc:
            return str(exc)
        except Exception as exc:
            return f"I built the content but couldn't save the file ({exc})."

        self.last_path = path
        if self._on_created:
            try:
                self._on_created(path)
            except Exception:
                pass

        # Open a working session so the next message can refine this document.
        self.workshop.start(kind, topic, spec, theme, path)

        from ..theme import describe

        design_note = describe(theme)
        return (
            f"Here's a first draft of your {label} on {topic}.\n"
            f"{self.workshop.outline()}\n"
            f"Design: {design_note}\n"
            f"Saved to: {path}\n\n"
            "Tell me what to change -- for example \"add a slide about costs\", "
            "\"make the bullets shorter\", or \"use a warmer colour\". "
            "Say \"that's it\" when you're happy, or \"push it to GitHub\"."
        )
