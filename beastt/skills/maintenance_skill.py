"""Self-diagnosis, self-repair, and self-update, driven by conversation.

Examples:
    "check yourself"            / "run a health check"
    "fix yourself"              / "fix any problems"
    "what went wrong"           / "show me your errors"
    "are you up to date"        / "check for updates"
    "update yourself"
    "roll back the last update"

Repairs are deterministic (install a package, start Ollama, pull the model, reset
a corrupt file). Nothing here lets the model rewrite its own source: when a real
code change is needed, it reports the fault instead.
"""

from __future__ import annotations

import re

from .base import Skill

_DIAGNOSE = re.compile(
    r"\b(?:health\s*check|check\s+(?:yourself|your\s+health|everything|your\s+setup)|"
    r"diagnose|self[\s-]?check|are\s+you\s+(?:ok|okay|healthy|working|alright)|"
    # "run a diagnostic" is at least as natural as "run diagnostics", and the
    # missing article was enough to send it to the model instead.
    r"is\s+everything\s+(?:ok|okay|working|fine)|run\s+(?:a\s+)?diagnostics?)\b",
    re.IGNORECASE,
)
_REPAIR = re.compile(
    r"\b(?:fix|repair|heal|mend|sort\s+out|resolve)\b[^.?!]*"
    r"\b(?:yourself|your\s+self|it|issues?|problems?|errors?|everything|that)\b"
    r"|^\s*(?:fix|repair|heal)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_ERRORS = re.compile(
    # "what's wrong" was missing alongside "what went wrong" -- the present tense
    # is the one someone actually types when something is wrong now.
    r"\b(?:what\s+went\s+wrong|what(?:'?s| is)\s+wrong|"
    r"show\s+(?:me\s+)?(?:your\s+)?(?:errors?|logs?|failures?)|"
    r"any\s+errors?|last\s+error|recent\s+errors?|error\s+log)\b",
    re.IGNORECASE,
)
_CLEAR_ERRORS = re.compile(
    r"\b(?:clear|forget|reset|wipe)\b[^.?!]*\b(?:errors?|error\s+log|logs?)\b",
    re.IGNORECASE,
)
_CHECK_UPDATE = re.compile(
    r"\b(?:are\s+you\s+up\s*[\s-]?to[\s-]?date|check\s+for\s+updates?|"
    # "any updates?" on its own asks about BEASTT. "any update ON my bot" asks
    # about something else entirely, and used to be answered with BEASTT's own
    # git revision -- a confident reply to a question nobody asked.
    r"any\s+updates?\b(?!\s+(?:on|for|about|to|regarding|from|with)\b)|"
    r"is\s+there\s+(?:an\s+)?update|new\s+version)\b",
    re.IGNORECASE,
)
_DO_UPDATE = re.compile(
    r"\b(?:update|upgrade)\b[^.?!]*\b(?:yourself|your\s*self|your\s+code|"
    r"everything|now)\b|^\s*(?:update|upgrade)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_UPDATE_MODEL = re.compile(
    r"\b(?:update|upgrade|refresh)\b[^.?!]*\b(?:model|brain|llm)\b", re.IGNORECASE
)
_ROLLBACK = re.compile(
    r"\b(?:roll\s*back|revert|undo)\b[^.?!]*\b(?:update|change|version)?\b",
    re.IGNORECASE,
)


class MaintenanceSkill(Skill):
    name = "maintenance"

    def __init__(self, config):
        self.config = config
        self._last_findings = None

    def matches(self, text: str) -> bool:
        return bool(
            _CLEAR_ERRORS.search(text)
            or _ERRORS.search(text)
            or _DIAGNOSE.search(text)
            or _REPAIR.search(text)
            or _UPDATE_MODEL.search(text)
            or _CHECK_UPDATE.search(text)
            or _DO_UPDATE.search(text)
            or (_ROLLBACK.search(text) and re.search(r"\bupdate|version\b", text, re.I))
        )

    def run(self, text: str) -> str:
        from .. import selfheal, selfupdate

        # --- errors ------------------------------------------------------
        if _CLEAR_ERRORS.search(text):
            count = selfheal.clear_errors()
            return (
                f"Cleared {count} logged error(s)." if count
                else "There was nothing in the error log."
            )

        if _ERRORS.search(text):
            errors = selfheal.recent_errors(5)
            if not errors:
                return "Nothing has gone wrong that I've noticed. Clean log."
            lines = [f"The last {len(errors)} problem(s) I hit:"]
            for entry in errors:
                where = f" during {entry['context']}" if entry.get("context") else ""
                lines.append(
                    f"  - {entry['when']}{where}: {entry['type']} -- {entry['message']}"
                )
            lines.append(
                "\nSay \"fix yourself\" and I'll repair anything I can, or "
                "\"check yourself\" for a full health check."
            )
            return "\n".join(lines)

        # --- updates -----------------------------------------------------
        if _UPDATE_MODEL.search(text):
            print("[update] Pulling the latest model build...")
            return selfupdate.model_update(self.config.model)

        if _ROLLBACK.search(text) and re.search(r"\bupdate|version\b", text, re.I):
            return selfupdate.rollback()

        if _CHECK_UPDATE.search(text) and not _DO_UPDATE.search(text):
            return selfupdate.check()

        if _DO_UPDATE.search(text):
            print("[update] Fetching the latest code...")
            report = selfupdate.update()
            # A fresh copy may need packages the old one didn't.
            findings = selfheal.diagnose(self.config)
            missing = [f for f in findings if f.fixable]
            if missing:
                report += (
                    f"\n\nAlso worth doing: {len(missing)} thing(s) need attention. "
                    "Say \"fix yourself\" and I'll handle them."
                )
            return report

        # --- diagnose / repair -------------------------------------------
        if _REPAIR.search(text):
            print("[heal] Checking myself over...")
            findings = selfheal.diagnose(self.config)
            problems = [f for f in findings if not f.ok]
            if not problems:
                return "I checked everything and there's nothing to fix -- all good."
            result = selfheal.repair(findings)
            # Re-check so the reply reflects reality rather than hope.
            after = selfheal.diagnose(self.config)
            remaining = [f for f in after if not f.ok]
            tail = (
                "\n\nEverything checks out now."
                if not remaining
                else "\n\nStill outstanding:\n"
                + "\n".join(f"  - {f.name}: {f.detail}" for f in remaining)
            )
            if remaining and any(not f.fixable for f in remaining):
                tail += (
                    "\n\nThose need a person -- tell me what you'd like to do, or "
                    "say \"what went wrong\" for the details."
                )
            return result + tail

        # Default: a health check.
        print("[heal] Running a health check...")
        findings = selfheal.diagnose(self.config)
        self._last_findings = findings
        summary = selfheal.summarise(findings)
        if any(f.fixable for f in findings):
            summary += "\n\nSay \"fix yourself\" and I'll sort out what I can."
        return summary
