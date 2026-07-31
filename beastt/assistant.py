"""The Assistant -- BEASTT's core. Ties brain, memory, skills, and voice together.

Turn flow for each user message:
  1. Try every skill; if one matches, answer instantly (no LLM round-trip).
  2. Otherwise, add the message to memory and ask the brain.
  3. Remember BEASTT's own reply so the conversation stays coherent.
"""

from __future__ import annotations

from typing import List, Optional

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
    ):
        self.config = config or Config.load()
        self.brain = brain or build_brain(self.config, verbose=verbose)
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

    # --- lifecycle --------------------------------------------------------
    def welcome(self) -> str:
        """The greeting BEASTT says when it wakes up."""
        return welcome_message(self.config.user_name)

    # --- conversation -----------------------------------------------------
    def _skill_answer(self, text: str) -> Optional[str]:
        for skill in self.skills:
            try:
                if skill.matches(text):
                    return skill.run(text)
            except Exception:
                continue
        return None

    def respond(self, text: str) -> str:
        """Return BEASTT's reply to a single user message."""
        text = text.strip()
        if not text:
            return "I'm listening -- go ahead."

        # 1. Fast path: a skill can handle it directly.
        skill_reply = self._skill_answer(text)
        if skill_reply is not None:
            self.memory.add_user(text)
            self.memory.add_assistant(skill_reply)
            return skill_reply

        # 2. Otherwise, think with the brain -- augmenting with a live web
        #    search first if the question needs current information.
        self.memory.add_user(text)
        messages = self.memory.messages()
        messages = self._augment_with_memories(text, messages)
        if self.search is not None and needs_search(text):
            messages = self._augment_with_search(text, messages)

        try:
            reply = self.brain.reply(messages)
        except Exception as exc:
            reply = (
                "Hmm, I hit a snag trying to think that through "
                f"({exc.__class__.__name__}). Mind trying again?"
            )
        if not reply:
            reply = "I'm not quite sure how to answer that -- can you say a bit more?"

        self.memory.add_assistant(reply)
        return reply

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
