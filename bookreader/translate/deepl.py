"""DeepL API (Free or Pro) translator. The key is entered by the user in the settings."""
from __future__ import annotations

import requests

from .base import TranslationContext, Translator


class DeepLTranslator(Translator):
    id = "deepl"
    name = "DeepL"

    def __init__(self, api_key: str, formality: str = "prefer_less"):
        self.api_key = api_key.strip()
        self.formality = formality

    @property
    def url(self) -> str:
        host = "api-free.deepl.com" if self.api_key.endswith(":fx") else "api.deepl.com"
        return f"https://{host}/v2"

    def _headers(self) -> dict:
        return {"Authorization": f"DeepL-Auth-Key {self.api_key}"}

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Brak klucza DeepL (Ustawienia tłumaczenia)."
        return True, ""

    def usage(self) -> tuple[int, int]:
        r = requests.get(f"{self.url}/usage", headers=self._headers(), timeout=10)
        r.raise_for_status()
        d = r.json()
        return d.get("character_count", 0), d.get("character_limit", 0)

    def translate(self, paragraphs: list[str], ctx: TranslationContext) -> list[str]:
        # DeepL's `context` steers the translation without being translated or billed.
        context_parts = [s for s, _ in ctx.previous[-2:]]
        names = [e for e in ctx.glossary.relevant("\n".join(paragraphs))]
        if names:
            context_parts.append("Names: " + ", ".join(e.source for e in names))
        body = {"text": paragraphs, "source_lang": ctx.source_lang.upper(), "target_lang": "PL",
                "formality": self.formality, "preserve_formatting": True,
                "split_sentences": "nonewlines", "model_type": "prefer_quality_optimized"}
        if context_parts:
            body["context"] = "\n".join(context_parts)
        r = requests.post(f"{self.url}/translate", headers=self._headers(), json=body, timeout=60)
        if r.status_code == 456:
            raise RuntimeError("Wyczerpany miesięczny limit znaków DeepL.")
        if r.status_code == 403:
            raise RuntimeError("Nieprawidłowy klucz DeepL.")
        r.raise_for_status()
        out = [t["text"] for t in r.json()["translations"]]
        # Apply glossary targets DeepL cannot know about.
        for e in names:
            if e.target and e.target != e.source:
                out = [t.replace(e.source, e.target) for t in out]
        return out
