"""Per-book glossary: consistent names, characters' gender, narrator and style.

Polish needs grammatical gender that English hides ("I said" -> "powiedziałem"/"powiedziałam"),
so the glossary records the narrator and each character's gender for the translator.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

GENDERS = {"K": "kobieta", "M": "mężczyzna", "-": "nieokreślona / nie dotyczy"}


@dataclass
class Entry:
    source: str
    target: str = ""            # empty = keep the original spelling
    kind: str = "postać"        # postać / miejsce / termin / inne
    gender: str = "-"
    note: str = ""


@dataclass
class Glossary:
    narrator_person: str = "3"          # "1" first-person narrator, "3" third-person
    narrator_gender: str = "-"
    narrator_name: str = ""
    genre: str = ""                     # e.g. "fantasy", "kryminał", "literatura faktu"
    address: str = "auto"               # how characters address each other: auto / ty / pan
    style_notes: str = ""
    entries: list[Entry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Glossary":
        if not d:
            return cls()
        known = cls.__dataclass_fields__
        g = cls(**{k: v for k, v in d.items() if k in known and k != "entries"})
        ek = Entry.__dataclass_fields__
        g.entries = [Entry(**{k: v for k, v in e.items() if k in ek}) for e in d.get("entries", [])]
        return g

    def relevant(self, text: str, limit: int = 40) -> list[Entry]:
        """Entries mentioned in `text` (keeps prompts short)."""
        low = text.lower()
        return [e for e in self.entries if e.source and e.source.lower() in low][:limit]

    def describe(self, text: str) -> str:
        """Glossary as instructions for an LLM translator (Polish)."""
        lines = []
        if self.narrator_person == "1":
            g = {"K": "kobietą (formy żeńskie: zrobiłam, byłam)", "M": "mężczyzną (formy męskie: zrobiłem, byłem)"}
            who = f" ({self.narrator_name})" if self.narrator_name else ""
            lines.append(f"Narracja pierwszoosobowa; narrator{who} jest " + g.get(self.narrator_gender, "o nieznanej płci — wnioskuj z kontekstu") + ".")
        else:
            lines.append("Narracja trzecioosobowa.")
        if self.genre:
            lines.append(f"Gatunek: {self.genre}.")
        if self.address == "ty":
            lines.append("Postacie zwracają się do siebie na „ty”.")
        elif self.address == "pan":
            lines.append("Postacie zwracają się do siebie formalnie („pan/pani”).")
        if self.style_notes:
            lines.append(self.style_notes)
        rel = self.relevant(text)
        if rel:
            lines.append("Słowniczek (stosuj konsekwentnie):")
            for e in rel:
                tgt = e.target or e.source
                extra = []
                if e.kind == "postać" and e.gender in ("K", "M"):
                    extra.append(GENDERS[e.gender])
                if e.note:
                    extra.append(e.note)
                lines.append(f"- {e.source} → {tgt} ({e.kind}{', ' + ', '.join(extra) if extra else ''})")
        return "\n".join(lines)


_CAP_PHRASE = re.compile(r"(?<![.!?„\"«]\s)(?<!^)\b([A-Z][a-z’']+(?:\s+[A-Z][a-z’']+){0,2})\b")
_STOP = {"I", "The", "A", "An", "He", "She", "It", "They", "We", "You", "But", "And", "Then", "When", "What",
         "Mr", "Mrs", "Ms", "Dr", "Sir", "Lady", "Lord", "Chapter", "Part", "Yes", "No", "Oh", "If", "In",
         "On", "At", "As", "So", "This", "That", "There", "His", "Her", "My", "Your", "Our", "Their", "God",
         "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def candidate_names(paragraphs: list[str], limit: int = 80) -> list[tuple[str, int, str]]:
    """Frequent capitalised words/phrases not at the start of a sentence: (name, count, example)."""
    counts: Counter[str] = Counter()
    example: dict[str, str] = {}
    for p in paragraphs:
        for m in _CAP_PHRASE.finditer(p):
            name = m.group(1).strip()
            if name.split()[0] in _STOP:
                continue
            counts[name] += 1
            if name not in example:
                a = max(0, m.start() - 80)
                example[name] = p[a:m.end() + 80]
    return [(n, c, example[n]) for n, c in counts.most_common(limit) if c >= 2]
