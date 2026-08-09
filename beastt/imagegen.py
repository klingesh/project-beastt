"""Making pictures that don't exist yet.

`images.py` finds real photographs, which is right when the subject is real -- a
retail storefront, a wind farm, a city. It is useless for "the three phases of an
AI rollout", because nobody has photographed that. Until now those slides fell
back to an abstract generated shape, which is where decks start to look plain.

So: generate the image instead. Two backends, and the order matters.

  * **Pollinations** -- no key, no signup, no card. Runs Flux. This is the
    default because it works on a fresh install.
  * **Cloudflare Workers AI** -- FLUX-1-schnell, roughly 170 images a day on the
    free allocation. Needs an account id and a token, and is the more dependable
    of the two once set up.

Everything returns an `images.Picture`, so slides consume a generated image
through exactly the same path as a photograph -- including the credits slide.
That is deliberate: a generated image is labelled "AI-generated" and attributed
to the model that made it, so a deck never passes invented imagery off as a
photograph.
"""

from __future__ import annotations

import base64
import hashlib
import random
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .config import Config
from .images import Picture, cache_dir

TIMEOUT = 90          # Flux on a free tier is not fast.
_MIN_BYTES = 4_000
_MAX_BYTES = 8_000_000
_UA = {"User-Agent": "beastt-assistant/1.0 (personal assistant)"}

#: Aspect ratios that suit a 16:9 deck. Square is the safe anonymous fallback --
#: see the Pollinations note below.
SIZES: Dict[str, Tuple[int, int]] = {
    "wide": (1280, 720),
    "square": (1024, 1024),
    "tall": (768, 1024),
}

#: Flux renders text as convincing gibberish, which looks worse on a slide than
#: no text at all. Ruling it out in the prompt is the only control we have.
STYLE = ("professional editorial illustration, clean uncluttered composition, "
         "restrained muted palette, soft studio lighting, high detail, "
         "no text, no words, no letters, no captions, no watermark, no logo")


class GenerationError(RuntimeError):
    """A backend was tried and could not produce an image."""


@dataclass(frozen=True)
class Generator:
    id: str
    label: str
    #: Config attributes that must be non-empty. Empty means no key needed.
    key_fields: Tuple[str, ...] = ()
    env_vars: Tuple[str, ...] = ()
    signup: str = ""
    blurb: str = ""
    model_label: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.key_fields)


GENERATORS: Tuple[Generator, ...] = (
    Generator(
        id="pollinations",
        label="Pollinations",
        signup="https://enter.pollinations.ai",
        blurb=("Free Flux image generation with no key and no signup. "
               "Works immediately; a key lifts the anonymous restrictions."),
        model_label="Flux",
    ),
    Generator(
        id="cloudflare",
        label="Cloudflare Workers AI",
        key_fields=("cloudflare_account", "cloudflare_token"),
        env_vars=("BEASTT_CF_ACCOUNT", "BEASTT_CF_TOKEN"),
        signup="https://dash.cloudflare.com/profile/api-tokens",
        blurb=("FLUX-1-schnell, about 170 images a day free. Needs an account "
               "id and a token, and is the steadier of the two."),
        model_label="FLUX-1-schnell",
    ),
)

_BY_ID: Dict[str, Generator] = {g.id: g for g in GENERATORS}


def get_generator(generator_id: str) -> Optional[Generator]:
    return _BY_ID.get(generator_id)


def is_configured(config: Config, generator: Generator) -> bool:
    if not generator.needs_key:
        return True
    return all(str(getattr(config, field, "") or "") for field in generator.key_fields)


def available(config: Config) -> List[Generator]:
    """Usable backends, best first.

    Cloudflare goes first when it is set up: it is a documented, authenticated
    API with a published allowance, where the anonymous Pollinations tier can
    change without notice.
    """
    ready = [g for g in GENERATORS if is_configured(config, g)]
    ready.sort(key=lambda g: 0 if g.needs_key else 1)
    return ready


def catalogue(config: Config) -> List[Dict]:
    rows = []
    for generator in GENERATORS:
        ready = is_configured(config, generator)
        rows.append({
            "id": generator.id,
            "label": generator.label,
            "blurb": generator.blurb,
            "configured": ready,
            "needs_key": generator.needs_key,
            "env_vars": list(generator.env_vars),
            "signup": generator.signup,
            "status": "ready" if ready else "no key",
        })
    return rows


# --- prompt shaping ---------------------------------------------------------
def build_prompt(subject: str, style: str = STYLE) -> str:
    """Turn a slide's image query into something worth sending to Flux."""
    cleaned = re.sub(r"\s+", " ", str(subject or "")).strip(" .,-")
    cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return ""
    return f"{cleaned}, {style}" if style else cleaned


def _valid_image(data: bytes) -> bool:
    if not (_MIN_BYTES <= len(data) <= _MAX_BYTES):
        return False
    return data[:3] == b"\xff\xd8\xff" or data[:8].startswith(b"\x89PNG")


def _suffix(data: bytes) -> str:
    return ".png" if data[:8].startswith(b"\x89PNG") else ".jpg"


# --- Pollinations -----------------------------------------------------------
#: The supported public gateway.
POLLINATIONS_GATEWAY = "https://gen.pollinations.ai/image"
#: The original endpoint. Kept as a fallback because the gateway restricts
#: anonymous requests in ways the older host does not -- see below.
POLLINATIONS_DIRECT = "https://image.pollinations.ai/prompt"


def pollinations_generate(config: Config, prompt: str, width: int, height: int,
                          model: str = "flux", timeout: int = TIMEOUT) -> bytes:
    """Ask Pollinations for an image, working around the anonymous limits.

    Anonymous requests to the gateway are documented to reject a `seed` and any
    non-square aspect ratio with a 401, while an authenticated request accepts
    both. So we ask for what we actually want only when we have a key, fall back
    to a square (which is the combination known to work without one), and keep
    the older host as a last resort since it never had the restriction.

    A square image is not a loss here: the slide renderer places pictures into a
    fixed band and crops, so aspect ratio is a preference rather than a
    requirement.
    """
    encoded = urllib.parse.quote(prompt[:1500], safe="")
    key = str(getattr(config, "pollinations_key", "") or "")
    headers = dict(_UA)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    attempts: List[Tuple[str, Dict[str, str]]] = []
    if key:
        attempts.append((f"{POLLINATIONS_GATEWAY}/{encoded}", {
            "model": model, "width": str(width), "height": str(height),
            "nologo": "true", "seed": str(random.randint(1, 10_000_000)),
        }))
    else:
        side = max(width, height)
        attempts.append((f"{POLLINATIONS_GATEWAY}/{encoded}", {
            "model": model, "width": str(side), "height": str(side),
        }))
    attempts.append((f"{POLLINATIONS_DIRECT}/{encoded}", {
        "model": model, "width": str(width), "height": str(height),
        "nologo": "true",
    }))

    last = ""
    for url, params in attempts:
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=timeout)
        except Exception as exc:
            last = f"{exc.__class__.__name__}"
            continue
        if resp.status_code >= 400:
            last = f"HTTP {resp.status_code}"
            continue
        data = resp.content or b""
        if _valid_image(data):
            return data
        last = "reply was not an image"
    raise GenerationError(last or "no image returned")


# --- Cloudflare Workers AI --------------------------------------------------
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def cloudflare_generate(config: Config, prompt: str, steps: int = 4,
                        timeout: int = TIMEOUT) -> bytes:
    """Run FLUX-1-schnell on Workers AI. Returns decoded image bytes."""
    account = str(getattr(config, "cloudflare_account", "") or "")
    token = str(getattr(config, "cloudflare_token", "") or "")
    if not (account and token):
        raise GenerationError("no Cloudflare account id or token set")

    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/ai/run/{CF_MODEL}")
    try:
        resp = requests.post(
            url,
            json={"prompt": prompt[:2000], "steps": max(1, min(8, steps))},
            headers={"Authorization": f"Bearer {token}", **_UA},
            timeout=timeout,
        )
    except Exception as exc:
        raise GenerationError(f"could not reach Cloudflare ({exc.__class__.__name__})")

    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            errors = body.get("errors") if isinstance(body, dict) else None
            if errors:
                detail = str(errors[0].get("message") or "")
        except Exception:
            pass
        raise GenerationError(f"HTTP {resp.status_code}{': ' + detail if detail else ''}")

    try:
        body = resp.json()
    except Exception:
        raise GenerationError("reply was not JSON")
    if not isinstance(body, dict) or not body.get("success", True):
        raise GenerationError("Cloudflare reported failure")

    encoded = (body.get("result") or {}).get("image")
    if not encoded:
        raise GenerationError("no image in the reply")
    try:
        data = base64.b64decode(encoded)
    except Exception:
        raise GenerationError("image was not valid base64")
    if not _valid_image(data):
        raise GenerationError("decoded bytes were not an image")
    return data


# --- the maker --------------------------------------------------------------
class ImageMaker:
    """Generates and caches slide artwork, trying each usable backend in turn."""

    def __init__(self, config: Optional[Config] = None, timeout: int = TIMEOUT,
                 enabled: bool = True, verbose: bool = False):
        self.config = config or Config.load()
        self.timeout = timeout
        self.enabled = enabled and bool(getattr(self.config, "imagegen_enabled", False))
        self.verbose = verbose
        self.credits: List[Picture] = []
        #: Once every backend has failed, stop trying: a deck should not pay a
        #: 90-second timeout per slide to learn the same thing ten times over.
        self.exhausted = False
        self._made: Dict[str, Picture] = {}

    def backends(self) -> List[Generator]:
        return available(self.config) if self.enabled else []

    def make(self, subject: str, orientation: str = "wide") -> Optional[Picture]:
        """Generate an image for `subject`, or None if that isn't possible."""
        if not self.enabled or self.exhausted:
            return None
        prompt = build_prompt(subject)
        if not prompt:
            return None

        width, height = SIZES.get(orientation, SIZES["wide"])
        cache_key = hashlib.sha1(
            f"{prompt}|{width}x{height}".encode("utf-8")).hexdigest()[:16]
        if cache_key in self._made:
            return self._made[cache_key]

        backends = self.backends()
        if not backends:
            self.exhausted = True
            return None

        reasons = []
        for generator in backends:
            try:
                if generator.id == "cloudflare":
                    data = cloudflare_generate(self.config, prompt,
                                               timeout=self.timeout)
                else:
                    data = pollinations_generate(self.config, prompt, width,
                                                 height, timeout=self.timeout)
            except GenerationError as exc:
                reasons.append(f"{generator.label}: {exc}")
                continue
            except Exception as exc:
                reasons.append(f"{generator.label}: {exc.__class__.__name__}")
                continue

            target = cache_dir() / f"gen-{cache_key}{_suffix(data)}"
            try:
                target.write_bytes(data)
            except Exception as exc:
                reasons.append(f"{generator.label}: could not save ({exc})")
                continue

            picture = Picture(
                path=target,
                title=str(subject or "").strip()[:80],
                # Attribution is the point: the credits slide will now say this
                # picture was generated rather than photographed.
                creator=f"{generator.model_label or 'AI'} via {generator.label}",
                licence="AI-generated",
                source_url="",
            )
            self._made[cache_key] = picture
            self.credits.append(picture)
            if self.verbose:
                print(f"[imagegen] {generator.label} made \"{subject[:40]}\"")
            return picture

        # Every backend refused. Say why once, then stop asking.
        self.exhausted = True
        print("[imagegen] Couldn't generate artwork; using graphics instead."
              + (f" ({'; '.join(reasons[:2])})" if reasons else ""))
        return None
