"""Find and cache openly-licensed images for slides.

Uses the Openverse API, which needs no API key and can be filtered to licences
that permit commercial use and modification. Everything it returns still carries
attribution requirements (most results are CC BY or CC BY-SA), so each download
records its creator, licence, and source URL -- the renderer puts these in the
speaker notes and on a credits slide. Never strip that attribution.

If the network is unavailable or nothing suitable is found, callers fall back to
drawing an abstract graphic instead, so a deck never fails because of images.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests

from .paths import project_root

API = "https://api.openverse.org/v1/images/"
_UA = {"User-Agent": "beastt-assistant/1.0 (personal assistant)"}

# Licences that allow reuse and modification. "nd" (no derivatives) is excluded
# because slides crop and overlay images.
_ALLOWED = {"cc0", "pdm", "by", "by-sa"}

_MIN_BYTES = 8_000        # skip tiny thumbnails
_MAX_BYTES = 6_000_000    # keep decks a sensible size


def cache_dir() -> Path:
    path = project_root() / "beastt_output" / ".image_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Picture:
    path: Path
    title: str
    creator: str
    licence: str
    source_url: str

    def credit(self) -> str:
        """Attribution line -- required by the CC licences these images use."""
        bits = [self.title or "Untitled"]
        if self.creator:
            bits.append(f"by {self.creator}")
        licence = self.licence.upper().replace("_", " ")
        bits.append(f"({licence})")
        if self.source_url:
            bits.append(self.source_url)
        return " ".join(bits)


def _clean_query(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", str(text or ""))
    words = [w for w in text.split() if len(w) > 2]
    return " ".join(words[:6])


class ImageFinder:
    """Searches Openverse and caches results on disk."""

    def __init__(self, timeout: int = 20, enabled: bool = True):
        self.timeout = timeout
        self.enabled = enabled
        self._seen: set = set()      # avoid repeating the same picture in one deck
        self.credits: List[Picture] = []
        self.failed = False

    def find(self, query: str, orientation: str = "wide") -> Optional[Picture]:
        """Return a cached, attributed image for `query`, or None."""
        if not self.enabled or self.failed:
            return None
        query = _clean_query(query)
        if not query:
            return None

        try:
            resp = requests.get(
                API,
                params={
                    "q": query,
                    "license_type": "commercial,modification",
                    "page_size": 8,
                    "mature": "false",
                    "aspect_ratio": "wide" if orientation == "wide" else "square",
                },
                headers=_UA,
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get("results") or []
        except Exception as exc:
            # One network failure disables further attempts for this deck, so a
            # slow connection doesn't stall every slide.
            print(f"[images] Search unavailable ({exc.__class__.__name__}); using graphics.")
            self.failed = True
            return None

        for item in results:
            licence = str(item.get("license") or "").lower()
            if licence not in _ALLOWED:
                continue
            url = item.get("url")
            identifier = item.get("id")
            if not url or identifier in self._seen:
                continue
            picture = self._download(item, url)
            if picture:
                self._seen.add(identifier)
                self.credits.append(picture)
                return picture
        return None

    def _download(self, item: dict, url: str) -> Optional[Picture]:
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        suffix = ".jpg"
        if url.lower().endswith(".png"):
            suffix = ".png"
        target = cache_dir() / f"{name}{suffix}"

        if not target.exists():
            try:
                resp = requests.get(url, headers=_UA, timeout=self.timeout, stream=True)
                if resp.status_code != 200:
                    return None
                data = resp.content
                if not (_MIN_BYTES <= len(data) <= _MAX_BYTES):
                    return None
                # Confirm it really is an image before writing it into a deck.
                if not (data[:3] == b"\xff\xd8\xff" or data[:8].startswith(b"\x89PNG")):
                    return None
                target.write_bytes(data)
            except Exception:
                return None

        return Picture(
            path=target,
            title=str(item.get("title") or "").strip()[:80],
            creator=str(item.get("creator") or "").strip()[:60],
            licence=f"CC {item.get('license', '')} {item.get('license_version', '')}".strip(),
            source_url=str(item.get("foreign_landing_url") or item.get("url") or "")[:120],
        )



class PictureSource:
    """One place a slide asks for a picture, however it has to be obtained.

    Photographs first, generated artwork second. That order is deliberate: a
    real photograph of a real subject is more credible than an invented one, and
    Openverse answers in a second where Flux takes tens of them. Generation
    exists to cover what photography cannot -- "the three phases of an AI
    rollout" has never been photographed, and that slide used to fall back to an
    abstract shape.

    Set BEASTT_IMAGE_PREFER=generated to reverse it, which suits a deck about
    concepts rather than places.

    Exposes the same `find()` and `credits` as ImageFinder, so the renderers do
    not need to know which happened.
    """

    def __init__(self, config=None, verbose: bool = False):
        from .config import Config

        self.config = config or Config.load()
        self.verbose = verbose

        self.finder: Optional[ImageFinder] = None
        if getattr(self.config, "images_enabled", False):
            self.finder = ImageFinder(enabled=True)

        self.maker = None
        if getattr(self.config, "imagegen_enabled", False):
            from .imagegen import ImageMaker

            self.maker = ImageMaker(self.config, verbose=verbose)

        self.prefer = str(getattr(self.config, "image_prefer", "photo") or "photo").lower()

    @property
    def enabled(self) -> bool:
        return self.finder is not None or self.maker is not None

    def _photo(self, query: str, orientation: str) -> Optional[Picture]:
        if self.finder is None:
            return None
        return self.finder.find(query, orientation=orientation)

    def _generated(self, query: str, orientation: str) -> Optional[Picture]:
        if self.maker is None:
            return None
        return self.maker.make(query, orientation=orientation)

    def find(self, query: str, orientation: str = "wide") -> Optional[Picture]:
        order = ((self._generated, self._photo) if self.prefer == "generated"
                 else (self._photo, self._generated))
        for attempt in order:
            try:
                picture = attempt(query, orientation)
            except Exception as exc:
                if self.verbose:
                    print(f"[images] {attempt.__name__} failed: "
                          f"{exc.__class__.__name__}: {exc}")
                continue
            if picture is not None:
                return picture
        return None

    @property
    def credits(self) -> List[Picture]:
        """Every picture used, in the order it was used.

        Generated images appear here too, licensed "AI-generated" -- the credits
        slide is exactly where a reader should learn which images were invented.
        """
        collected: List[Picture] = []
        if self.finder is not None:
            collected.extend(self.finder.credits)
        if self.maker is not None:
            collected.extend(self.maker.credits)
        return collected
