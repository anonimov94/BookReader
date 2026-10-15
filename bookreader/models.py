"""Core data model shared by loaders, text processing, TTS and the player."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Chapter:
    title: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Book:
    path: str
    title: str
    author: str = ""
    language: str = ""          # ISO 639-1 code from metadata, may be empty
    chapters: list[Chapter] = field(default_factory=list)
    cover: bytes | None = None
    needs_ocr_pages: list[int] = field(default_factory=list)  # PDF pages without a text layer

    @property
    def char_count(self) -> int:
        return sum(len(p) for c in self.chapters for p in c.paragraphs)


class SegmentKind(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    PENDING = "pending"     # paragraph waiting for translation; the player waits on it


@dataclass
class Segment:
    """One unit of speech, usually a sentence.

    `start`/`end` are character offsets inside the paragraph, so the GUI can
    highlight the sentence being read in the original (display) text.
    """
    chapter: int
    paragraph: int
    start: int
    end: int
    text: str
    kind: SegmentKind = SegmentKind.TEXT
    last_in_paragraph: bool = False
