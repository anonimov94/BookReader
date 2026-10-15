"""Translator interface and the context passed with every batch."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .glossary import Glossary


@dataclass
class TranslationContext:
    glossary: Glossary = field(default_factory=Glossary)
    previous: list[tuple[str, str]] = field(default_factory=list)   # (source, translation) just before the batch
    book_title: str = ""
    source_lang: str = "en"
    target_lang: str = "pl"


class Translator(ABC):
    id: str = ""
    name: str = ""

    @property
    def key(self) -> str:
        """Identifies translator + model in the translation cache."""
        return self.id

    @abstractmethod
    def translate(self, paragraphs: list[str], ctx: TranslationContext) -> list[str]:
        """Translate paragraphs keeping a 1:1 correspondence."""

    def available(self) -> tuple[bool, str]:
        """(ready, human-readable reason when not)."""
        return True, ""
