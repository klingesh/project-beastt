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
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .config import Config
from .images import Picture, cache_dir
from .paths import project_root

TIMEOUT = 90          # Flux on a free tier is not fast.
_MIN_BYTES = 4_000
_MAX_BYTES = 8_000_000
_UA = {"User-Agent": "beastt-assistant/1.0 (personal assistant)"}

#: Aspect ratios that suit a 16:9 deck. Square is the safe anonymous fallback --
#: see the Pollinations note below.
SIZES: Dict[str, Tuple[int, int]] = {
    "wide": (1280, 720),
    "classic": (1024, 768),
    "square": (1024, 1024),
    "tall": (768, 1024),
}

#: Fallback direction for when a prompt cannot be expanded (see expand_prompt).
#:
#: The previous version read "professional editorial illustration, clean
#: uncluttered composition, restrained muted palette, soft studio lighting, high
#: detail, no text, no words, no letters, no captions, no watermark, no logo" --
#: about twenty-five words of style against a one-word subject like
#: "advertisements". Flux obeyed the style and ignored the subject, and the
#: instruction it followed most faithfully was to leave things out: every image
#: came back as an empty grey room with a blank frame on the wall.
#:
#: Two lessons are baked in here. Keep it shorter than the subject deserves to be,
#: and prefer concrete photographic direction over adjectives about restraint,
#: which a diffusion model renders as emptiness.
STYLE = ("detailed photograph, natural light, realistic textures, "
         "shallow depth of field")


class GenerationError(RuntimeError):
    """A backend was tried and could not produce an image."""


def art_dir() -> Path:
    """Where images a person actually asked for are kept.

    Distinct from the slide cache on purpose. The cache is keyed by prompt hash
    and is disposable; this holds files someone will want to find, open and send
    to somebody, so the names are readable and nothing here is ever pruned.
    """
    path = project_root() / "beastt_output" / "art"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    return (cleaned[:limit].strip("-") or "artwork")


def save_as_art(picture: Picture, subject: str) -> Picture:
    """Copy a generated picture into the art folder under a readable name."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = art_dir() / f"{_slug(subject)}-{stamp}{picture.path.suffix}"
    target.write_bytes(picture.path.read_bytes())
    return Picture(
        path=target,
        title=picture.title,
        creator=picture.creator,
        licence=picture.licence,
        source_url=picture.source_url,
    )


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
#: The only part of the house style worth forcing on a prompt someone wrote
#: themselves. Flux inventing lettering ruins an image whatever the art direction.
#: Kept short on purpose. A long list of "no X" phrases in a positive prompt --
#: which is all this API accepts -- pushes the model towards blankness rather than
#: away from lettering.
MINIMAL_STYLE = "no lettering"

#: Past this length a request carries its own art direction, and ours would argue
#: with it. A user asking for "photorealistic ... DSLR photography,
#: documentary-style realism" does not want "professional editorial illustration"
#: bolted on -- those are contradictory instructions, and the model splits the
#: difference into something that is neither.
DETAILED_PROMPT = 180


#: Below this, a request is a topic rather than a picture and is worth expanding.
BRIEF_PROMPT = 120

#: Deliberately example-free.
#:
#: An earlier version illustrated the point with "a billboard over a wet street at
#: dusk". The model copied it: "advertisements" and "finance" both came back as a
#: rain-slick neon street, and the billboard it dutifully included rendered as
#: garbled lettering. One concrete example in a prompt like this stops being an
#: illustration and becomes a template.
#:
#: So the guidance is structural instead. Say what a good answer contains, insist
#: the setting come from the subject's own world, and rule out the things this
#: generator cannot draw.
_EXPAND = """You write prompts for an image generator.

Turn this request into ONE vivid image prompt: {subject}

Rules:
- 50 to 80 words. A single paragraph, no line breaks, no bullet points.
- Describe a concrete scene: what is in frame, where it is, the time of day, the
  light, the camera angle, the colours, and the medium (photograph, oil painting,
  3D render, watercolour).
- Set it somewhere the subject genuinely happens, and be specific about where.
  A subject about money belongs in a dealing room, a bank hall or over a ledger;
  one about farming belongs in a field. Do not relocate it somewhere merely
  atmospheric.
- Never a night-time neon city street unless the request actually asks for one.
- Choose nothing whose whole point is writing on it: no billboards, signs,
  posters, screens full of text, book covers, packaging or shopfront names, and no
  brand names at all. This generator cannot form legible words, so anything like
  that arrives as gibberish and ruins the picture.
- Make it a photograph, and name a real lens or film look. Do not choose an oil
  painting or a 3D render unless the request asked for one -- they come back soft
  and hazy, where a photograph stays sharp.
- Daylight unless the request implies otherwise.
- Describe only what can be seen. No abstract nouns like innovation, strategy or
  growth -- those are ideas, not things a camera can point at.
- Reply with the prompt only. No preamble, no quotes, no explanation."""


def expand_prompt(brain, subject: str, allow_soft: bool = False,
                  attempts: int = 2) -> str:
    """Turn a bare topic into a scene worth rendering, or "" if that fails.

    This is the difference between the two images in the bug report. "marketing"
    plus generic style words gave Flux nothing to draw, so it drew nothing: a
    blank frame in an empty room. A model asked to invent the scene first --
    where, when, what light, what medium -- gives it something to hold on to.

    It costs one extra call, which is the same trade the deck planner makes and
    for the same reason: deciding what to make before making it.
    """
    if brain is None:
        return ""
    # A scene built entirely around a billboard cannot be repaired by dropping
    # clauses, so ask again rather than falling back to the bare topic -- that
    # fallback is what produced the empty grey rooms.
    for attempt in range(1, max(1, attempts) + 1):
        scene = _expand_once(brain, subject, allow_soft)
        if scene:
            return scene
        if attempt < attempts:
            print("[imagegen] scene was unusable; asking for another")
    return ""


def _expand_once(brain, subject: str, allow_soft: bool) -> str:
    try:
        from .brain.base import Message

        reply = brain.reply(
            [Message(role="user", content=_EXPAND.format(subject=subject))],
            temperature=0.9,
        )
    except Exception as exc:
        print(f"[imagegen] couldn't expand the prompt ({exc.__class__.__name__})")
        return ""

    text = " ".join(str(reply or "").split())
    # Models like to introduce their work; strip a leading "Here is ...:" and any
    # wrapping quotes before trusting it.
    text = re.sub(r"^(?:here(?:'s| is)[^:]{0,40}:)\s*", "", text, flags=re.I)
    text = text.strip("\"'` ")
    if len(text) < 40 or len(text) > 900:
        return ""
    return clean_scene(text, allow_soft=allow_soft)


#: Things that only exist to carry words. Asked for "advertisements", the scene
#: writer produced "giant LED screens displaying scrolling digital billboards for
#: major brands such as Sony and Honda" -- every one of them banned by the prompt
#: it had just been given.
#:
#: Which is the same lesson as the skill matching: an instruction the model may
#: ignore is not a control. Check the output instead.
_BANNED_IN_SCENE = re.compile(
    r"\b(billboards?|signage|sign\s?boards?|neon\s+signs?|marquee|"
    r"led\s+screens?|led\s+displays?|digital\s+displays?|advertisements?\s+"
    r"displaying|posters?|banners?|placards?|headlines?|newspapers?\s+"
    r"headline|logos?|brand\s+names?|labels?|price\s+tags?|number\s+plates?|"
    r"licence\s+plates?|license\s+plates?|graffiti|slogans?|lettering|"
    r"typography|captions?|subtitles?)\b",
    re.IGNORECASE,
)

#: Media that come back soft. Not banned outright -- someone may ask for a
#: painting -- but never chosen on the scene writer's own initiative.
_SOFT_MEDIUM = re.compile(
    r"\b(oil\s+painting|watercolou?r|3d\s+render|cgi|digital\s+painting|"
    r"illustration|concept\s+art|matte\s+painting|airbrush)\b",
    re.IGNORECASE,
)

#: Appended to an expanded scene, because build_prompt() -- and with it STYLE --
#: is skipped when a scene is supplied. Without this the scene went to Flux with
#: no sharpness direction at all, which is why the trading floor arrived as a
#: golden fog.
QUALITY_TAIL = "sharp focus, fine detail, natural light, photographic, no lettering"


def clean_scene(scene: str, allow_soft: bool = False) -> str:
    """Strip clauses that ask for things this generator cannot draw.

    Works clause by clause rather than rejecting the whole scene: "a trading floor
    at mid-morning, ticker machines, a massive analog clock, brass railings" only
    needs one clause removing, and throwing the description away over it would
    lose a perfectly good picture.

    Returns "" when so little survives that the remainder is no longer a scene.
    """
    text = " ".join(str(scene or "").split())
    if not text:
        return ""

    kept, dropped = [], 0
    # Split on colons and "while" as well as punctuation. Scene writers produce
    # long compound clauses -- "a Tokyo agency reflects the activity within:
    # employees browsing giant LED screens ... while outside pedestrians hurry
    # past" -- and on commas alone the banned screens took the agency and the
    # pedestrians down with them.
    for clause in re.split(r"\s*[;,:]\s*|\s+while\s+", text):
        if not clause:
            continue
        if _BANNED_IN_SCENE.search(clause):
            dropped += 1
            continue
        if not allow_soft and _SOFT_MEDIUM.search(clause):
            dropped += 1
            continue
        kept.append(clause)

    rebuilt = ", ".join(kept).strip(" ,.")
    # Losing a clause or two is a repair; losing most of them means the scene was
    # built around the thing we cannot draw, and it is better to start again.
    if not rebuilt or len(rebuilt) < 40 or dropped > len(kept):
        return ""
    if dropped:
        print(f"[imagegen] dropped {dropped} clause(s) the generator can't draw")
    return rebuilt


def build_prompt(subject: str, style: str = STYLE) -> str:
    """Turn an image request into something worth sending to Flux.

    Short subjects ("marketing", "renewable energy") get the house style, because
    two words are not art direction and the default would otherwise be whatever
    Flux feels like. Long ones are left as written, apart from the no-text rule.
    """
    cleaned = re.sub(r"\s+", " ", str(subject or "")).strip(" .,-")
    cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return ""
    if len(cleaned) >= DETAILED_PROMPT:
        return f"{cleaned}, {MINIMAL_STYLE}"
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

    # A seed is not optional. Without one the service is deterministic, so asking
    # for the same thing twice returns byte-identical images -- four requests for
    # "advertisements" produced the same picture four times over. The seed is what
    # makes "try again" mean anything.
    seed = str(random.randint(1, 10_000_000))

    attempts: List[Tuple[str, Dict[str, str]]] = []
    if key:
        attempts.append((f"{POLLINATIONS_GATEWAY}/{encoded}", {
            "model": model, "width": str(width), "height": str(height),
            "nologo": "true", "seed": seed,
        }))
    else:
        # The gateway rejects a seed and a non-square shape from anonymous
        # callers; the original host accepts both. So without a key, go there
        # first and get a varying image at the requested aspect ratio, rather
        # than a repeatable square. The gateway is kept as the fallback.
        attempts.append((f"{POLLINATIONS_DIRECT}/{encoded}", {
            "model": model, "width": str(width), "height": str(height),
            "nologo": "true", "seed": seed,
        }))
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
        # Say what did come back. A rate-limited free tier answers with an error
        # page or a stub, and "not an image" alone leaves nobody any wiser.
        kind = ""
        try:
            kind = str(resp.headers.get("Content-Type") or "")
        except Exception:
            pass
        last = (f"reply was not a usable image ({len(data)} bytes"
                + (f", {kind}" if kind else "") + ")")
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

    def make(self, subject: str, orientation: str = "wide", prompt: str = "",
             fresh: bool = False) -> Optional[Picture]:
        """Generate an image for `subject`, or None if that isn't possible.

        Pass `prompt` to send something other than the subject -- an expanded
        scene description, say. Pass `fresh` when a repeat request should produce
        a different picture: the cache exists so one deck doesn't fetch the same
        artwork twice, but a person asking again wants a new attempt, not the
        previous file handed back.
        """
        if not self.enabled or self.exhausted:
            return None
        supplied = " ".join(str(prompt or "").split())
        # A supplied scene skips build_prompt entirely, so it would otherwise
        # carry no quality direction at all.
        prompt = f"{supplied}, {QUALITY_TAIL}" if supplied else build_prompt(subject)
        if not prompt:
            return None

        width, height = SIZES.get(orientation, SIZES["wide"])
        cache_key = hashlib.sha1(
            f"{prompt}|{width}x{height}".encode("utf-8")).hexdigest()[:16]
        if not fresh and cache_key in self._made:
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

            # A fresh attempt needs its own filename. Reusing the prompt-hashed
            # one meant the second attempt silently overwrote the first, so
            # "again" replaced the picture instead of offering an alternative.
            unique = f"-{uuid.uuid4().hex[:6]}" if fresh else ""
            target = cache_dir() / f"gen-{cache_key}{unique}{_suffix(data)}"
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
