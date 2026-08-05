"""The Assistant -- BEASTT's core. Ties brain, memory, skills, and voice together.

Turn flow for each user message:
  1. Try every skill; if one matches, answer instantly (no LLM round-trip).
  2. Otherwise, add the message to memory and ask the brain.
  3. Remember BEASTT's own reply so the conversation stays coherent.
"""

from __future__ import annotations

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
            system_prompt=system_prompt(self.config.name, self.config.user_name),
            max_messages=self.config.max_history_messages,
        )
        self.search = WebSearch() if self.config.search_enabled else None
        self._verbose = verbose

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
        if self.config.documents_enabled:
            from .skills.document_skill import DocumentSkill
            from .skills.github_skill import GitHubSkill

            self.skills.insert(
                0,
                DocumentSkill(
                    brain_provider=lambda: self.brain,
                    on_created=self._remember_document,
                ),
            )
            self.skills.insert(
                0, GitHubSkill(self.config, last_file_provider=lambda: self.last_document)
            )

        # Looking after itself: diagnose, repair, and update.
        self.skills.insert(0, MaintenanceSkill(self.config))

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
        if self.search is not None and needs_search(text):
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
        if not reply:
            reply = "I'm not quite sure how to answer that -- can you say a bit more?"

        self.memory.add_assistant(reply)
        return reply

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

        skill_reply = self._skill_answer(text)
        if skill_reply:
            self.memory.add_user(text)
            self.memory.add_assistant(skill_reply)
            yield {"type": "chunk", "text": skill_reply}
            yield {"type": "done", "reply": skill_reply}
            return

        self.memory.add_user(text)
        messages = self._context_for(text)
        if self.search is not None and needs_search(text):
            yield {"type": "status", "text": f"Searching the web for “{extract_query(text)}”"}
            messages = self._augment_with_search(text, messages)

        yield {"type": "status", "text": f"Thinking with {self.model_label()}"}

        parts: List[str] = []
        try:
            for chunk in self.brain.stream(messages):
                if not chunk:
                    continue
                parts.append(chunk)
                yield {"type": "chunk", "text": chunk}
        except Exception as exc:
            from .selfheal import record

            record(exc, context="thinking about a reply")
            # Keep whatever arrived before the failure -- a truncated answer is
            # more use than replacing it with an apology.
            if not parts:
                excuse = (
                    "Hmm, I hit a snag trying to think that through "
                    f"({exc.__class__.__name__}). Say \"fix yourself\" and I'll "
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
            content=(
                f"[Things you remember about {self.config.user_name} from previous "
                f"conversations. Use them naturally when relevant -- don't recite them "
                f"or mention that you have notes.]\n{listing}"
            ),
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
