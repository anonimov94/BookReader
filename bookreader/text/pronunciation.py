"""User pronunciation rules: "how it is written" -> "how the narrator should say it"."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from ..paths import PRONUNCIATION_FILE


@dataclass
class Rule:
    find: str
    say: str
    whole_word: bool = True
    case_sensitive: bool = False
    regex: bool = False

    def compile(self) -> re.Pattern | None:
        if not self.find:
            return None
        body = self.find if self.regex else re.escape(self.find)
        if self.whole_word:
            body = rf"(?<!\w){body}(?!\w)"
        try:
            return re.compile(body, 0 if self.case_sensitive else re.I)
        except re.error:
            return None


class PronunciationDict:
    def __init__(self, rules: list[Rule] | None = None):
        self.rules = rules or []
        # Regex rules may use \1 back-references; plain rules take the replacement literally.
        self._compiled = [(r.compile(), r.say if r.regex else r.say.replace("\\", "\\\\")) for r in self.rules]

    def apply(self, text: str) -> str:
        for pattern, say in self._compiled:
            if pattern is not None:
                try:
                    text = pattern.sub(say, text)
                except re.error:       # bad back-reference in a user rule: skip it
                    pass
        return text

    @staticmethod
    def rules_from(raw: list[dict]) -> list[Rule]:
        known = Rule.__dataclass_fields__
        return [Rule(**{k: v for k, v in d.items() if k in known}) for d in raw]

    @staticmethod
    def rules_to(rules: list[Rule]) -> list[dict]:
        return [asdict(r) for r in rules]


def load_global_rules(path: str = PRONUNCIATION_FILE) -> list[Rule]:
    try:
        with open(path, encoding="utf-8") as f:
            return PronunciationDict.rules_from(json.load(f))
    except (OSError, ValueError):
        return []


def save_global_rules(rules: list[Rule], path: str = PRONUNCIATION_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(PronunciationDict.rules_to(rules), f, ensure_ascii=False, indent=1)
