"""Work through a request in steps instead of answering in one breath.

The ordinary turn is a single model call: whatever comes back is the answer.
That is right for "how are you" and wrong for "compare these two proposals and
tell me which is stronger" -- the second needs the request understood, a plan,
some looking up, and only then an answer.

So this module does what a person does:

  1. Understand -- restate the task and name the context available.
  2. Plan       -- three to five concrete steps.
  3. Work       -- carry each one out, searching the web where a step needs it.
  4. Answer     -- write the reply from what was actually found.

Every stage is reported as it happens, so the work is visible rather than
appearing as a long pause. If any stage fails the caller falls back to a plain
single-call reply, so this can only ever add.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, Iterator, List, Optional

from .brain.base import Brain, Message

#: Cap the work. Each step is a model call, and past this the answer stops
#: improving while the wait keeps growing.
MAX_STEPS = 5
#: Requests shorter than this are almost never worth planning.
MIN_LENGTH = 170

#: An explicit ask to work carefully, whatever the length.
_ASKED = re.compile(
    r"\b(think(?:\s+it)?\s+through|think\s+(?:first|carefully|about\s+it)|"
    r"step\s*by\s*step|research|deep\s+dive|analys[ei]|analyz[ei]|"
    r"work\s+(?:it|this)\s+out|reason\s+through|break\s+(?:it|this)\s+down|"
    r"in\s+detail|thorough(?:ly)?|compare\s+.*\band\b)\b",
    re.IGNORECASE,
)

#: A quick exchange, even if it runs long. Planning these would be silly.
_CHATTY = re.compile(
    r"^\s*(hi|hey|hello|yo|thanks|thank\s+you|ok|okay|cool|nice|good\s+(morning|"
    r"afternoon|evening|night)|how\s+are\s+you|what'?s\s+up|bye|goodbye|"
    r"see\s+you|that'?s\s+it|never\s*mind)\b",
    re.IGNORECASE,
)


def wanted(text: str, has_attachments: bool = False) -> bool:
    """Is this request worth planning, rather than answering straight away?

    Deliberately conservative: making "hey Jarvis" cost five model calls would
    be a downgrade, so a request qualifies only if it is explicitly asked for,
    substantial, or asks something about a file that was provided.
    """
    body = str(text or "").strip()
    if not body or _CHATTY.match(body):
        return False
    if _ASKED.search(body):
        return True
    if has_attachments and ("?" in body or len(body) > 60):
        return True
    return len(body) >= MIN_LENGTH


_PLAN_PROMPT = """A request has come in. Before answering it, work out what is
actually being asked and how you will answer it.

The request:
\"\"\"
{request}
\"\"\"
{context}
Return ONLY valid JSON:
{{"understanding": "one or two sentences restating the task in your own words, naming what you have been given to work with",
  "steps": [
    {{"action": "search", "detail": "the exact search query", "why": "what this establishes"}},
    {{"action": "reason", "detail": "the specific question to work out", "why": "what this establishes"}}
  ]}}

Rules:
- Between 2 and {max_steps} steps. Every step must change the final answer;
  drop anything that is merely restating the task.
- Use "search" only for facts you cannot be confident about -- current events,
  prices, recent figures, anything dated. Use "reason" for analysis, comparison,
  judgement, or working with material already provided.
- If the request includes the material to work from, do NOT search for it.
- Order the steps so later ones can build on earlier ones.
"""

_STEP_PROMPT = """You are working through a task one step at a time.

The overall task: {understanding}

This step: {detail}
Why it matters: {why}
{found}
Answer THIS STEP only, in three sentences or fewer. Be concrete: name figures,
dates and sources where you have them. If you genuinely do not know, say so
plainly rather than guessing -- a wrong fact here becomes a wrong answer later.
"""

_FINAL_PROMPT = """You worked through a task in steps. Now write the answer.

The task: {understanding}

What you established, in order:
{findings}

Write the reply to {user} in your own warm, natural voice. Rules:
- Use only what you established above. If something could not be pinned down,
  say so rather than inventing it.
- Lead with the answer, then the reasoning that supports it.
- Name a source or a figure where you have one.
- Do not describe your process or mention steps, searching, or planning --
  {user} watched it happen. Just give the answer.
"""


def _parse_plan(raw: str, max_steps: int) -> Optional[Dict]:
    """Pull a usable plan out of the model's reply, or None."""
    if not raw:
        return None
    text = re.sub(r"```(?:json)?", "", raw.strip())
    data = None
    for candidate in (text,):
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError:
            pass
    if data is None:
        start = text.find("{")
        depth = 0
        for index in range(start if start >= 0 else len(text), len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    if not isinstance(data, dict):
        return None

    steps: List[Dict] = []
    for item in data.get("steps") or []:
        if not isinstance(item, dict):
            continue
        detail = " ".join(str(item.get("detail") or "").split())[:300]
        if not detail:
            continue
        action = str(item.get("action") or "reason").strip().lower()
        steps.append({
            "action": "search" if action.startswith("search") else "reason",
            "detail": detail,
            "why": " ".join(str(item.get("why") or "").split())[:200],
        })
        if len(steps) >= max_steps:
            break

    understanding = " ".join(str(data.get("understanding") or "").split())[:400]
    if not steps:
        return None
    return {"understanding": understanding, "steps": steps}


def plan(brain: Brain, request: str, context: str = "",
         max_steps: int = MAX_STEPS) -> Optional[Dict]:
    """Understand the request and lay out the steps. None if that didn't work."""
    note = f"\nWhat you have to work with:\n{context}\n" if context else "\n"
    prompt = _PLAN_PROMPT.format(request=request[:4000], context=note,
                                 max_steps=max_steps)
    try:
        raw = brain.reply([Message(role="user", content=prompt)],
                          json_mode=True, temperature=0.2)
    except TypeError:
        raw = brain.reply([Message(role="user", content=prompt)])
    except Exception:
        return None
    return _parse_plan(raw, max_steps)


def _run_step(brain: Brain, understanding: str, step: Dict,
              findings: List[str]) -> str:
    """Carry out one reasoning step, given everything established so far."""
    found = ""
    if findings:
        found = "\nEstablished so far:\n" + "\n".join(findings[-6:]) + "\n"
    prompt = _STEP_PROMPT.format(understanding=understanding or "(see the step)",
                                 detail=step["detail"], why=step["why"] or "-",
                                 found=found)
    try:
        return (brain.reply([Message(role="user", content=prompt)]) or "").strip()
    except Exception:
        return ""


def work(brain: Brain, request: str, user_name: str = "friend",
         context: str = "", searcher=None,
         max_results: int = 5) -> Iterator[dict]:
    """Understand, plan, work, answer -- yielding progress as it happens.

    Yields {"type": "status", "text": ...} throughout and exactly one
    {"type": "answer", "text": ...} at the end. If planning fails, yields
    {"type": "give_up"} and nothing else, so the caller can answer normally.
    """
    yield {"type": "status", "text": "Reading the request"}
    outline = plan(brain, request, context)
    if not outline:
        yield {"type": "give_up"}
        return

    understanding = outline["understanding"]
    steps = outline["steps"]
    if understanding:
        yield {"type": "status", "text": f"Understood: {understanding[:120]}"}
    yield {"type": "status",
           "text": f"Planned {len(steps)} step{'s' if len(steps) != 1 else ''}"}

    findings: List[str] = []
    for index, step in enumerate(steps, 1):
        label = step["detail"][:80]
        if step["action"] == "search" and searcher is not None:
            yield {"type": "status", "text": f"Step {index}: searching for {label}"}
            results, summary = [], ""
            try:
                from .search import format_results

                results = searcher.search(step["detail"], max_results) or []
                # Gate on the results themselves: format_results() returns a
                # readable "nothing found" line for an empty list, which would
                # otherwise be recorded as though it were a finding.
                summary = format_results(results) if results else ""
            except Exception:
                results, summary = [], ""
            if results and summary:
                findings.append(f"{index}. Searched \"{label}\":\n{summary[:1200]}")
                continue
            # Nothing came back -- fall through and reason about it instead.
            yield {"type": "status",
                   "text": f"Step {index}: nothing useful found, reasoning instead"}
        else:
            yield {"type": "status", "text": f"Step {index}: {label}"}

        answer = _run_step(brain, understanding, step, findings)
        if answer:
            findings.append(f"{index}. {step['detail']}\n{answer[:900]}")

    if not findings:
        yield {"type": "give_up"}
        return

    yield {"type": "status", "text": "Putting the answer together"}
    prompt = _FINAL_PROMPT.format(understanding=understanding or request[:300],
                                  findings="\n\n".join(findings), user=user_name)
    try:
        final = (brain.reply([Message(role="user", content=prompt)]) or "").strip()
    except Exception:
        final = ""
    if not final:
        yield {"type": "give_up"}
        return
    yield {"type": "answer", "text": final}
