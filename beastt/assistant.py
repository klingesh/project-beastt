"""The Assistant -- BEASTT's core. Ties brain, memory, skills, and voice together.

Turn flow for each user message:
  1. Try every skill; if one matches, answer instantly (no LLM round-trip).
  2. Otherwise, add the message to memory and ask the brain.
  3. Remember BEASTT's own reply so the conversation stays coherent.
"""

from __future__ import annotations

from typing import List, Optional

from .brain import Brain, build_brain
from .config import Config
from .memory import Memory
from .personality import system_prompt, welcome_message
from .skills import default_skills
from .skills.base import Skill


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

        # 2. Otherwise, think with the brain.
        self.memory.add_user(text)
        try:
            reply = self.brain.reply(self.memory.messages())
        except Exception as exc:
            reply = (
                "Hmm, I hit a snag trying to think that through "
                f"({exc.__class__.__name__}). Mind trying again?"
            )
        if not reply:
            reply = "I'm not quite sure how to answer that -- can you say a bit more?"

        self.memory.add_assistant(reply)
        return reply

    def reset(self) -> None:
        """Forget the current conversation (keeps the personality)."""
        self.memory.clear()
