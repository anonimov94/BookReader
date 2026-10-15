"""Local LLM translation through Ollama (http://localhost:11434)."""
from __future__ import annotations

import json
import re
from typing import Callable

import requests

from .base import TranslationContext, Translator
from .glossary import Entry, Glossary, candidate_names

HOST = "http://127.0.0.1:11434"
SUGGESTED_MODELS = [
    ("gemma3:12b", "Gemma 3 12B — bardzo dobry wielojęzyczny, ~8 GB"),
    ("SpeakLeash/bielik-11b-v2.3-instruct:Q4_K_M", "Bielik 11B — polski model, naturalna polszczyzna, ~7 GB"),
    ("qwen3:14b", "Qwen 3 14B — mocne rozumienie kontekstu, ~9 GB"),
    ("gemma3:4b", "Gemma 3 4B — szybki, słabszy, ~3 GB"),
]
_MARK = re.compile(r"\[\[(\d+)\]\]\s*")
_THINK = re.compile(r"<think>.*?</think>", re.S)

SYSTEM = (
    "Jesteś doświadczonym tłumaczem literackim z języka angielskiego na polski. "
    "Tłumaczysz książkę akapit po akapicie tak, by brzmiała jak dobrze zredagowane polskie wydanie: "
    "naturalna składnia, idiomy oddane sensem, nie słowo w słowo; zachowuj ton, rejestr, humor i rytm oryginału. "
    "Dialogi zapisuj według polskich zasad (myślnik na początku wypowiedzi). "
    "Konsekwentnie stosuj słowniczek imion i nazw oraz właściwy rodzaj gramatyczny postaci i narratora. "
    "Nie dodawaj komentarzy, przypisów ani wyjaśnień. Nie pomijaj żadnego zdania."
)


class OllamaTranslator(Translator):
    id = "ollama"

    def __init__(self, model: str = "gemma3:12b", host: str = HOST, temperature: float = 0.3):
        self.model, self.host, self.temperature = model, host, temperature

    @property
    def name(self) -> str:
        return f"Ollama · {self.model}"

    @property
    def key(self) -> str:
        return f"ollama:{self.model}"

    # ------------------------------------------------------------ server
    def running(self) -> bool:
        try:
            return requests.get(f"{self.host}/api/version", timeout=2).ok
        except requests.RequestException:
            return False

    def models(self) -> list[str]:
        r = requests.get(f"{self.host}/api/tags", timeout=5)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))

    def pull(self, model: str, progress: Callable[[str, int, int], None], cancel=None) -> None:
        """Download a model into Ollama (user-initiated from the manager)."""
        with requests.post(f"{self.host}/api/pull", json={"model": model, "stream": True}, stream=True, timeout=30) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if cancel is not None and cancel.is_set():
                    return
                if not line:
                    continue
                msg = json.loads(line)
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                progress(msg.get("status", ""), msg.get("completed", 0), msg.get("total", 0))

    def available(self) -> tuple[bool, str]:
        if not self.running():
            return False, "Ollama nie działa. Zainstaluj ją z ollama.com i uruchom."
        try:
            names = self.models()
        except requests.RequestException as e:
            return False, str(e)
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"Model {self.model} nie jest pobrany (Głosy i modele → Ollama)."
        return True, ""

    # ------------------------------------------------------------ chat
    def _chat(self, system: str, user: str, json_mode: bool = False, timeout: float = 600) -> str:
        body = {"model": self.model, "stream": False, "think": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": self.temperature, "num_ctx": 8192}}
        if json_mode:
            body["format"] = "json"
        r = requests.post(f"{self.host}/api/chat", json=body, timeout=timeout)
        if r.status_code == 400 and "think" in r.text:      # models without a thinking switch
            body.pop("think")
            r = requests.post(f"{self.host}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        return _THINK.sub("", r.json()["message"]["content"]).strip()

    def translate(self, paragraphs: list[str], ctx: TranslationContext) -> list[str]:
        joined = "\n".join(paragraphs)
        parts = [f"Książka: {ctx.book_title}" if ctx.book_title else "", ctx.glossary.describe(joined)]
        if ctx.previous:
            prev = "\n\n".join(f"EN: {s}\nPL: {t}" for s, t in ctx.previous[-2:])
            parts.append("Poprzedni fragment (tylko dla kontekstu, nie tłumacz go ponownie):\n" + prev)
        numbered = "\n\n".join(f"[[{i + 1}]] {p}" for i, p in enumerate(paragraphs))
        parts.append("Przetłumacz poniższe akapity. Każdy akapit zacznij od tego samego znacznika [[n]] "
                     "co w oryginale i zwróć wyłącznie tłumaczenia:\n\n" + numbered)
        reply = self._chat(SYSTEM, "\n\n".join(p for p in parts if p))
        result = _parse_numbered(reply, len(paragraphs))
        if result is None:
            if len(paragraphs) == 1:
                return [_MARK.sub("", reply).strip()]
            # The model merged or skipped paragraphs: fall back to one at a time.
            out, prev = [], list(ctx.previous)
            for p in paragraphs:
                sub = TranslationContext(ctx.glossary, prev, ctx.book_title, ctx.source_lang, ctx.target_lang)
                t = self.translate([p], sub)[0]
                out.append(t)
                prev = (prev + [(p, t)])[-2:]
            return out
        return result

    # ------------------------------------------------------------ glossary
    def build_glossary(self, paragraphs: list[str], existing: Glossary | None = None) -> Glossary:
        """Ask the model about narrator, genre and the most frequent names."""
        g = existing or Glossary()
        cands = candidate_names(paragraphs)
        sample = "\n".join(paragraphs)[:6000]
        names = "\n".join(f"- {n} (×{c}): …{ex}…" for n, c, ex in cands[:60])
        prompt = (
            "Przeanalizuj początek angielskiej książki i listę często występujących nazw. Zwróć JSON:\n"
            '{"narrator_person": "1" lub "3", "narrator_gender": "K"/"M"/"-", "narrator_name": "",'
            ' "genre": "gatunek po polsku", "address": "ty"/"pan"/"auto",'
            ' "entries": [{"source": "nazwa", "target": "polska forma lub pusty gdy bez zmian",'
            ' "kind": "postać"/"miejsce"/"termin"/"inne", "gender": "K"/"M"/"-"}]}\n'
            "Uwzględnij tylko prawdziwe imiona, nazwy własne i terminy specyficzne dla książki. "
            "Imiona osób zwykle zostaw bez tłumaczenia; tłumacz nazwy znaczące (np. przydomki, nazwy miejsc), "
            "jeśli polskie wydanie by je przetłumaczyło.\n\n"
            f"POCZĄTEK KSIĄŻKI:\n{sample}\n\nNAZWY:\n{names}"
        )
        data = json.loads(self._chat("Jesteś redaktorem przygotowującym tłumaczenie książki. Odpowiadasz wyłącznie JSON-em.",
                                     prompt, json_mode=True))
        for k in ("narrator_person", "narrator_gender", "narrator_name", "genre", "address"):
            if data.get(k):
                setattr(g, k, str(data[k]))
        known = {e.source for e in g.entries}
        for e in data.get("entries", []):
            src = str(e.get("source", "")).strip()
            if src and src not in known:
                g.entries.append(Entry(source=src, target=str(e.get("target") or ""),
                                       kind=str(e.get("kind") or "inne"), gender=str(e.get("gender") or "-")))
                known.add(src)
        return g


def _parse_numbered(reply: str, n: int) -> list[str] | None:
    pieces = _MARK.split(reply)
    # split -> [prefix, "1", text1, "2", text2, ...]
    found = {int(pieces[i]): pieces[i + 1].strip() for i in range(1, len(pieces) - 1, 2)}
    if sorted(found) != list(range(1, n + 1)) or not all(found.values()):
        return None
    return [found[i] for i in range(1, n + 1)]
