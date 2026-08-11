"""The Assistant -- BEASTT's core. Ties brain, memory, skills, and voice together.

Turn flow for each user message:
  1. Try every skill; if one matches, answer instantly (no LLM round-trip).
  2. Otherwise, add the message to memory and ask the brain.
  3. Remember BEASTT's own reply so the conversation stays coherent.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional

from .brain import Brain, build_brain
from .brain.base import Message
from .config import Config
from .longterm import LongTermMemory
from .memory import Memory
from .personality import system_prompt, welcome_message
from .reflect import extract_facts
from .search import WebSearch, extract_query, format_results, is_news, needs_search
from .skills import default_skills
from .skills.base import Skill
from .skills.maintenance_skill import MaintenanceSkill
from .skills.memory_skill import MemorySkill

#: Llama-3 style templates wrap a reply in role headers. When the template is
#: applied loosely the role name leaks out as the first line of the content, so
#: the answer arrives reading "assistant\n\nHello...". Matched only when the word
#: stands alone or is followed by a colon, so a reply that legitimately begins
#: "Assistant roles vary..." is left alone.
_ROLE_LEAK = re.compile(r"^\s*assistant\s*(?::|\n|$)", re.IGNORECASE)


_LEAK_WORD = "assistant"


def memory_instruction(user_name: str) -> str:
    """How remembered facts are introduced to the model.

    A module-level function rather than an inline f-string so its wording can be
    asserted directly. The previous version said only "use them naturally when
    relevant", which reads as a promise that the facts *were* relevant -- and the
    recall code was padding the list to a quota, so usually they were not. Handed a
    list of facts about a person, a model finds a use for them: a request to draw a
    picture came back recommending a friend for artistic advice.

    So this says plainly that most replies need none of it, and names the specific
    failure rather than gesturing at it.
    """
    return (
        f"[Background on {user_name} from previous conversations. It is here in "
        f"case it helps. Most replies will not need any of it.\n"
        f"Do not steer the answer towards it. Do not recite it or mention having "
        f"notes. Do not bring up a person who has nothing to do with what was "
        f"asked.]"
    )


def _strip_role_leak(text: str) -> str:
    match = _ROLE_LEAK.match(text or "")
    return text[match.end():].lstrip() if match else text


def _leak_decided(held: str) -> bool:
    """Can we already tell whether `held` opens with a leaked role header?

    Streaming has to hold text back until this is True, so the aim is to decide
    as early as possible. Almost every reply is settled by its first chunk --
    "Renewable " cannot become "assistant", so it goes straight to the screen.
    Only text that is still a possible prefix of the word is held.
    """
    probe = (held or "").lstrip()
    if not probe:
        return False
    lowered = probe.lower()
    if len(lowered) < len(_LEAK_WORD):
        # Too short to be the word yet: undecided only if it could still become it.
        return not _LEAK_WORD.startswith(lowered)
    if not lowered.startswith(_LEAK_WORD):
        return True
    # We have "assistant..."; one more character settles whether it's the role
    # header or a real word like "assistants".
    return len(lowered) > len(_LEAK_WORD)


_SEARCH_INSTRUCTION = (
    "[You just searched the web for the user in real time. Use the results below "
    "to answer their most recent message in your own warm, natural, conversational "
    "voice -- like a friend catching them up. Give a concise summary with the key "
    "facts (a few sentences), and you may mention a source name. If the results are "
    "empty or don't actually answer the question, say honestly that you couldn't "
    "find anything reliable. Never invent details that aren't in the results.]\n\n"
)


class Assistant:
    def __init__(
        self,
        config: Optional[Config] = None,
        brain: Optional[Brain] = None,
        skills: Optional[List[Skill]] = None,
        verbose: bool = True,
        model_id: Optional[str] = None,
    ):
        self.config = config or Config.load()
        from . import providers

        self.model_id = model_id or providers.default_model_id(self.config)
        self.brain = brain or build_brain(
            self.config, verbose=verbose, model_id=self.model_id
        )
        self.skills = skills if skills is not None else default_skills(self.config)
        self.memory = Memory(
            system_prompt=system_prompt(
                self.config.name, self.config.user_name,
                can_draw=bool(getattr(self.config, "imagegen_enabled", False)),
            ),
            max_messages=self.config.max_history_messages,
        )
        self.search = WebSearch() if self.config.search_enabled else None
        self._verbose = verbose
        #: Set while a streaming turn is in flight, so a slow skill can report
        #: its progress outwards. None means nobody is listening.
        self._status_sink = None

        # Long-term memory: facts that persist across sessions.
        self.longterm = (
            LongTermMemory(self.config.memory_path)
            if self.config.longterm_enabled
            else None
        )
        if self.longterm is not None:
            # Let the user manage memory directly ("remember that ...").
            self.skills.insert(0, MemorySkill(self.longterm, self.config.user_name))
            if verbose and len(self.longterm):
                print(f"[memory] Recalling {len(self.longterm)} things about you.")

        # Document creation, and GitHub for getting those files off the machine.
        self.last_document = None
        # Set by the CLI when input is coming from the microphone.
        self.voice_mode = False

        # Drawing a picture on request. Inserted before the document skills so
        # that they end up ahead of it: "a presentation with images" is a deck,
        # and whichever skill is checked first decides that.
        if getattr(self.config, "imagegen_enabled", False):
            from .skills.image_skill import ImageSkill

            self.skills.insert(
                0,
                ImageSkill(
                    self.config,
                    on_created=self._remember_document,
                    progress=self._emit_status,
                    brain_provider=lambda: self.brain,
                ),
            )

        if self.config.documents_enabled:
            from .skills.document_skill import DocumentSkill
            from .skills.github_skill import GitHubSkill

            self.skills.insert(
                0,
                DocumentSkill(
                    brain_provider=lambda: self.brain,
                    on_created=self._remember_document,
                    progress=self._emit_status,
                ),
            )
            self.skills.insert(
                0, GitHubSkill(self.config, last_file_provider=lambda: self.last_document)
            )

        # Looking after itself: diagnose, repair, and update.
        self.skills.insert(0, MaintenanceSkill(self.config))

        # Watching the trading bot, if one is configured. Read-only.
        #
        # Inserted last, so it is checked FIRST. Its patterns all require an
        # explicit reference to the bot or to trades, which makes it the more
        # specific of the two: MaintenanceSkill matches a bare "any updates?",
        # and so answered "any update on my bot" with BEASTT's own git revision.
        # Specific before general.
        if getattr(self.config, "bot_status_repo", ""):
            from .skills.trading_skill import TradingBotSkill

            self.skills.insert(0, TradingBotSkill(self.config))

        # Reading documents out of repositories (or locally).
        if self.config.documents_enabled:
            from .skills.read_skill import ReadSkill

            self.skills.insert(
                0, ReadSkill(self.config, brain_provider=lambda: self.brain)
            )

        # Coding: write files, scaffold projects, run commands, manage clones.
        if self.config.code_enabled:
            from .skills.code_skill import CodeSkill
            from .skills.shell_skill import ShellSkill

            self.skills.insert(
                0,
                CodeSkill(
                    brain_provider=lambda: self.brain,
                    on_created=self._remember_document,
                ),
            )
            self.skills.insert(
                0,
                ShellSkill(
                    self.config,
                    voice_mode_provider=lambda: self.voice_mode,
                ),
            )

    # --- lifecycle --------------------------------------------------------
    def welcome(self) -> str:
        """The greeting BEASTT says when it wakes up."""
        return welcome_message(self.config.user_name)

    # --- which model is thinking ------------------------------------------
    def model_label(self) -> str:
        """A short human description of the model currently in use."""
        from . import providers

        return providers.describe(self.model_id)

    def set_model(self, model_id: str) -> Optional[str]:
        """Switch the model doing the thinking. Returns None on success.

        The conversation is untouched -- only the brain behind it changes, so you
        can start a document on the local model and finish it on a faster hosted
        one. Skills hold `brain_provider=lambda: self.brain`, so they follow the
        switch automatically and don't need rebuilding.

        On failure a short reason is returned instead of switching, because
        moving to an unreachable model would break every following turn.
        """
        from . import providers

        provider_id, model = providers.split_model_id(model_id)
        provider = providers.get(provider_id)
        if provider is None or not model:
            return "I don't recognise that model."

        brain = providers.build(self.config, model_id)
        if brain is None:
            return (
                f"There's no API key for {provider.label} yet. "
                f"Add {provider.env_var} to your .env and restart me."
            )
        if not brain.is_available():
            if provider.is_local:
                return (
                    f"Ollama doesn't have '{model}'. Run: ollama pull {model}"
                )
            why = getattr(brain, "last_error", "")
            return (
                f"{provider.label} didn't accept that request"
                + (f" ({why})" if why else "")
                + f". Check {provider.env_var} in your .env is valid."
            )

        self.brain = brain
        self.model_id = providers.join_model_id(provider_id, model)
        return None

    # --- conversation -----------------------------------------------------
    def _skill_answer(self, text: str) -> Optional[str]:
        for skill in self.skills:
            try:
                if skill.matches(text):
                    return skill.run(text)
            except Exception as exc:
                # Log and fall through: one broken skill shouldn't end the turn.
                from .selfheal import record

                record(exc, context=f"the {skill.name} skill")
                print(f"[skill] {skill.name} failed: {exc.__class__.__name__}: {exc}")
                continue
        return None

    def _context_for(self, text: str) -> List[Message]:
        """Conversation + attachments + recalled facts, ready for the brain.

        Everything except the web search, which is kept out so the streaming
        path can announce it before it happens rather than after.
        """
        messages = self.memory.messages()
        messages = self._augment_with_attachments(messages)
        return self._augment_with_memories(text, messages)

    def respond(self, text: str) -> str:
        """Return BEASTT's reply to a single user message."""
        text = text.strip()
        if not text:
            return "I'm listening -- go ahead."

        # 1. Fast path: a skill can handle it directly. Note the truthy check:
        #    a skill returning "" means it had nothing to say, and treating that
        #    as an answer produced a blank turn that was also saved to memory.
        skill_reply = self._skill_answer(text)
        if skill_reply:
            self.memory.add_user(text)
            self.memory.add_assistant(skill_reply)
            return skill_reply

        # 2. Otherwise, think with the brain -- augmenting with a live web
        #    search first if the question needs current information.
        self.memory.add_user(text)
        messages = self._context_for(text)
        series, data_problem = self._data_attempt(text)
        if series:
            messages = self._augment_with_data(series, messages)
        elif data_problem:
            messages = self._augment_with_data_gap(data_problem, messages)
        if self._should_search(text, series):
            messages = self._augment_with_search(text, messages)

        try:
            reply = self.brain.reply(messages)
        except Exception as exc:
            from .selfheal import record

            record(exc, context="thinking about a reply")
            reply = (
                "Hmm, I hit a snag trying to think that through "
                f"({exc.__class__.__name__}). Say \"fix yourself\" and I'll "
                "check what's wrong."
            )
        reply = _strip_role_leak(reply)
        if not reply:
            reply = "I'm not quite sure how to answer that -- can you say a bit more?"

        self.memory.add_assistant(reply)
        return reply

    # --- working through a request in steps -------------------------------
    def _should_deliberate(self, text: str) -> bool:
        if not getattr(self.config, "deliberate", False):
            return False
        from . import deliberate

        return deliberate.wanted(text, bool(getattr(self, "_attachment_text", "")))

    def _deliberate(self, text: str) -> Iterator[dict]:
        """Understand, plan and work through a request, reporting each stage.

        Attachments and remembered facts are handed over as context, so the plan
        is made knowing what material is already available -- that is what stops
        it searching the web for a document the user just uploaded.
        """
        from . import deliberate

        context_parts = []
        attached = getattr(self, "_attachment_text", "")
        if attached:
            names = ", ".join(getattr(self, "_attachment_names", [])) or "a file"
            context_parts.append(
                f"Files attached to this conversation ({names}) -- their text "
                f"follows, so do not search for it:\n{attached[:6000]}"
            )
        if self.longterm is not None and len(self.longterm):
            facts = self.longterm.relevant(text, limit=self.config.memory_recall_limit)
            if facts:
                context_parts.append("Things you remember about "
                                     f"{self.config.user_name}:\n"
                                     + "\n".join(f"- {f}" for f in facts))

        return deliberate.work(
            self.brain, text,
            user_name=self.config.user_name,
            context="\n\n".join(context_parts),
            searcher=self.search,
            max_results=self.config.search_max_results,
            config=self.config,
        )

    def _emit_status(self, message: str) -> None:
        """Report a step from inside a skill, when someone is streaming."""
        sink = self._status_sink
        if sink and message:
            try:
                sink(str(message))
            except Exception:
                pass

    def _skills_with_progress(self, text: str, holder: dict) -> Iterator[dict]:
        """Run the skills, yielding their progress as it happens.

        A skill's run() is an ordinary blocking call, so the only way to show
        what it is doing is to let it push messages onto a queue while this
        generator drains them. Building a deck is now one model call per slide,
        which is far too long to leave the interface silent.
        """
        import queue
        import threading

        sink = queue.Queue()

        def work():
            try:
                holder["reply"] = self._skill_answer(text)
            finally:
                sink.put(None)          # sentinel: the skill has finished

        self._status_sink = sink.put
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        try:
            while True:
                message = sink.get()
                if message is None:
                    break
                yield {"type": "status", "text": str(message)[:160]}
        finally:
            self._status_sink = None
            worker.join(timeout=2)

    def respond_stream(self, text: str) -> Iterator[dict]:
        """Answer, emitting events as the reply is produced.

        Yields dicts with a `type`:
          status  -- work in progress, e.g. a web search, safe to show and discard
          chunk   -- a fragment of the reply, in order
          done    -- carries the complete `reply`, and is always last

        A skill answers instantly, so its reply arrives as a single chunk: the
        caller never needs to know which path was taken. Memory is updated the
        same way `respond()` does it, so the two can be used interchangeably.
        """
        text = text.strip()
        if not text:
            nudge = "I'm listening -- go ahead."
            yield {"type": "chunk", "text": nudge}
            yield {"type": "done", "reply": nudge}
            return

        holder: dict = {}
        yield from self._skills_with_progress(text, holder)
        skill_reply = holder.get("reply")
        if skill_reply:
            self.memory.add_user(text)
            self.memory.add_assistant(skill_reply)
            yield {"type": "chunk", "text": skill_reply}
            yield {"type": "done", "reply": skill_reply}
            return

        self.memory.add_user(text)

        # A substantial request is worked through in steps rather than answered
        # in one breath. Chatty or short messages skip this entirely.
        if self._should_deliberate(text):
            answered = None
            for event in self._deliberate(text):
                if event.get("type") == "answer":
                    answered = event["text"]
                elif event.get("type") == "give_up":
                    answered = None
                    break
                else:
                    yield event
            if answered:
                self.memory.add_assistant(answered)
                yield {"type": "chunk", "text": answered}
                yield {"type": "done", "reply": answered}
                return
            # Planning didn't work out; fall through to a plain reply.

        messages = self._context_for(text)
        series, data_problem = self._data_attempt(text)
        if series:
            from . import data

            yield {"type": "status", "text": f"Looking up {data.describe(series)}"}
            messages = self._augment_with_data(series, messages)
        elif data_problem:
            # Say it on screen. A failed lookup used to be invisible, which is
            # how an invented figure got to pass itself off as a fetched one.
            yield {"type": "status",
                   "text": f"Couldn't fetch that figure — {data_problem}"}
            messages = self._augment_with_data_gap(data_problem, messages)
        if self._should_search(text, series):
            yield {"type": "status", "text": f"Searching the web for “{extract_query(text)}”"}
            messages = self._augment_with_search(text, messages)

        yield {"type": "status", "text": f"Thinking with {self.model_label()}"}

        parts: List[str] = []
        held, opened, failure = "", False, None
        try:
            for chunk in self.brain.stream(messages):
                if not chunk:
                    continue
                if not opened:
                    # Hold the opening back only while a leaked role header is
                    # still possible -- it has to be removed before it reaches
                    # the screen, and it can arrive split across chunks. For a
                    # normal reply this releases on the very first chunk.
                    held += chunk
                    if not _leak_decided(held):
                        continue
                    opened = True
                    cleaned, held = _strip_role_leak(held), ""
                    if not cleaned:
                        continue
                    parts.append(cleaned)
                    yield {"type": "chunk", "text": cleaned}
                    continue
                parts.append(chunk)
                yield {"type": "chunk", "text": chunk}
        except Exception as exc:
            failure = exc

        # A reply shorter than the guard threshold is still sitting in `held`.
        if held:
            cleaned = _strip_role_leak(held)
            if cleaned:
                parts.append(cleaned)
                yield {"type": "chunk", "text": cleaned}

        if failure is not None:
            from .selfheal import record

            record(failure, context="thinking about a reply")
            # Keep whatever arrived before the failure -- a truncated answer is
            # more use than replacing it with an apology.
            if not parts:
                excuse = (
                    "Hmm, I hit a snag trying to think that through "
                    f"({failure.__class__.__name__}). Say \"fix yourself\" and I'll "
                    "check what's wrong."
                )
                parts.append(excuse)
                yield {"type": "chunk", "text": excuse}

        reply = "".join(parts).strip()
        if not reply:
            reply = "I'm not quite sure how to answer that -- can you say a bit more?"
            yield {"type": "chunk", "text": reply}

        self.memory.add_assistant(reply)
        yield {"type": "done", "reply": reply}

    # --- published figures ------------------------------------------------
    def _data_attempt(self, text: str):
        """(series, problem): the figures for this question, or why there are none.

        Asked about inflation or GDP, a model answers from training data --
        confidently, without a source, and out of date. Fetching the published
        series instead replaces a guess with a citation.

        Returning the *reason* matters as much as returning the data. An earlier
        version swallowed every failure and returned an empty list, so when the
        World Bank was unreachable the model answered from memory and described
        that as having "just pulled up the latest data". A wrong number is bad; a
        wrong number wearing the costume of a real one is worse, because there is
        nothing in the reply to doubt.

        The empty problem string is reserved for "this was never a data question",
        which is the only case that should pass silently.
        """
        if not getattr(self.config, "data_enabled", True):
            return [], ""
        try:
            from . import data

            if not data.wanted(self.config, text):
                return [], ""
            problems: List[str] = []
            series = data.lookup(self.config, text, problems=problems)
        except Exception as exc:
            print(f"[data] lookup failed: {exc.__class__.__name__}: {exc}")
            return [], f"the lookup itself failed ({exc.__class__.__name__})"

        if series:
            if self._verbose:
                print(f"[data] {data.describe(series)}")
            return series, ""
        return [], (problems[0] if problems else "the source returned nothing")

    def _augment_with_data(self, series, messages):
        from . import data

        block = data.as_prompt(series)
        if not block:
            return messages
        return [*messages, Message(role="system", content=block)]

    def _augment_with_data_gap(self, reason: str, messages):
        """Tell the model it has no figures, so it cannot pretend otherwise."""
        from . import data

        note = data.no_data_prompt(reason, self.config.user_name)
        return [*messages, Message(role="system", content=note)]

    def _should_search(self, text: str, series) -> bool:
        """Whether to search as well, given what the data sources returned.

        With an official figure in hand, a general web search is a liability
        rather than a help: the top results for "US inflation" are blog posts
        quoting last year's number, and putting those beside the real series
        just invites the model to average them. News is the exception -- if the
        question asks what is happening, the figure alone doesn't answer it.
        """
        if self.search is None or not needs_search(text):
            return False
        return not series or is_news(text)

    def _augment_with_search(self, text: str, messages):
        """Run a live web search and append the results as context for the brain."""
        query = extract_query(text)
        if self._verbose:
            print(f"[search] Looking that up: {query!r}")
        try:
            if is_news(text):
                results = self.search.news(query, self.config.search_max_results)
            else:
                results = self.search.search(query, self.config.search_max_results)
        except Exception as exc:
            if self._verbose:
                print(f"[search] Search error: {exc}")
            results = []

        context = format_results(results)
        note = Message(role="system", content=_SEARCH_INSTRUCTION + context)
        return [*messages, note]

    def set_attachments(self, text: str, names=None) -> None:
        """Provide file contents as background context for this conversation.

        Used by the web interface when files are attached to a chat, so questions
        can be answered about them without re-uploading each turn.
        """
        self._attachment_text = text or ""
        self._attachment_names = list(names or [])

    def _augment_with_attachments(self, messages):
        if not getattr(self, "_attachment_text", ""):
            return messages
        names = ", ".join(self._attachment_names) or "the attached file(s)"
        note = Message(
            role="system",
            content=(
                f"[{self.config.user_name} has attached these files to this "
                f"conversation: {names}. Their contents follow. Use them to answer "
                f"questions, and say so if something isn't in them.]\n\n"
                f"{self._attachment_text}"
            ),
        )
        return [messages[0], note, *messages[1:]]

    def _augment_with_memories(self, text: str, messages):
        """Prepend what BEASTT remembers about the user, when relevant."""
        if self.longterm is None or not len(self.longterm):
            return messages
        facts = self.longterm.relevant(text, limit=self.config.memory_recall_limit)
        if not facts:
            return messages
        listing = "\n".join(f"- {f}" for f in facts)
        note = Message(
            role="system",
            content=memory_instruction(self.config.user_name) + "\n" + listing,
        )
        # Insert right after the persona so it reads as background knowledge.
        return [messages[0], note, *messages[1:]]

    def remember_session(self) -> int:
        """Reflect on this conversation and save durable facts. Returns count added."""
        if self.longterm is None:
            return 0
        transcript = [m for m in self.memory.messages() if m.role != "system"]
        if len(transcript) < 2:
            return 0
        if self._verbose:
            print("[memory] Thinking about what to remember...")
        facts = extract_facts(self.brain, transcript, self.config.user_name)
        added = 0
        for fact in facts:
            if self.longterm.add(fact):
                added += 1
        if self._verbose:
            if added:
                print(f"[memory] Remembered {added} new thing(s) about you.")
            else:
                print("[memory] Nothing new to remember this time.")
        return added

    def _remember_document(self, path) -> None:
        """Track the newest generated file so "push it to GitHub" knows the target."""
        self.last_document = path

    def reset(self) -> None:
        """Forget the current conversation (keeps the personality)."""
        self.memory.clear()
