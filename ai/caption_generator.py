"""AI Caption Generator (spec: viral title, caption, SEO keywords,
hashtags, description). Two backends behind one interface:

- HeuristicCaptionGenerator: fully offline, no API key, no network call.
  Keyword frequency + the same hook-phrase signal ai/viral_scorer.py uses
  for highlight detection, assembled with a handful of title templates.
  Honest ceiling: this is pattern-matching, not understanding -- it won't
  write anything genuinely clever, but it's real, editable scaffolding
  rather than a blank text box, and it always works offline.
- LLMCaptionGenerator: calls the user's own Anthropic or OpenAI API key
  for genuinely higher-quality output. Never bundles or ships a key --
  the person supplies their own in Settings. Any failure (network, auth,
  rate limit, malformed response) falls back to the heuristic generator,
  so a bad or missing key degrades gracefully instead of blocking the
  workflow.
"""
from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

from ai.viral_scorer import HOOK_WORDS
from utils.logger import get_logger

log = get_logger("caption_generator")

STOPWORDS = {
    "id": {
        "yang", "di", "ke", "dari", "ini", "itu", "dan", "atau", "dengan", "untuk", "pada",
        "adalah", "akan", "telah", "sudah", "saya", "kamu", "dia", "mereka", "kita", "kami",
        "nya", "mu", "ku", "tidak", "bukan", "juga", "saja", "hanya", "masih", "lagi",
        "sangat", "sekali", "banget", "jadi", "karena", "kalau", "jika", "agar", "supaya",
        "tapi", "tetapi", "namun", "sebab", "oleh", "dalam", "luar", "atas", "bawah",
        "antara", "semua", "setiap", "beberapa", "banyak", "sedikit", "lebih", "paling",
        "kurang", "ada", "punya", "bisa", "dapat", "harus", "mau", "ingin", "coba", "mari",
        "ayo", "oke", "gak", "nggak", "enggak", "si", "deh", "dong", "kok", "sih", "nih",
        "tuh", "loh", "apa", "siapa", "kapan", "dimana", "mengapa", "kenapa", "bagaimana",
        "gimana", "kita", "aku", "kau", "yg", "ga", "udah", "emang", "gitu", "begitu",
        "halo", "hai", "hari", "bahas", "sesuatu", "ceritanya", "begini", "kalian", "tau",
        "banget", "nah", "bagian", "penting", "kelewat", "mulai", "soal",
    },
    "en": {
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "can", "this", "that", "these", "those", "i",
        "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
        "your", "his", "its", "our", "their", "to", "of", "in", "on", "at", "by", "for",
        "with", "about", "against", "between", "into", "through", "during", "before",
        "after", "above", "below", "from", "up", "down", "out", "off", "over", "under",
        "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
        "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no",
        "nor", "not", "only", "own", "same", "so", "than", "too", "very", "just", "now",
        "hey", "everyone", "today", "talking", "something", "guys", "thing", "things",
        "heres", "here's", "wont", "won't", "single", "every", "actually", "honestly",
    },
}

TITLE_TEMPLATES = {
    "id": [
        "{kw} yang Bikin Semua Orang Kaget",
        "Ternyata Ini Rahasia {kw}",
        "{kw}? Kamu Harus Lihat Ini",
        "Momen {kw} yang Bikin Viral",
        "Gak Nyangka, {kw} Bisa Begini",
    ],
    "en": [
        "This {kw} Moment Went Viral",
        "The Truth About {kw}",
        "You Won't Believe This {kw}",
        "{kw}: What Nobody Tells You",
        "Wait Until You See This {kw}",
    ],
}


@dataclass
class CaptionResult:
    title: str = ""
    caption: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source: str = "heuristic"  # "heuristic" | "llm"

    def hashtags_str(self) -> str:
        return " ".join(f"#{h}" for h in self.hashtags)

    def keywords_str(self) -> str:
        return ", ".join(self.keywords)


def extract_keywords(text: str, language: str = "id", top_n: int = 8) -> list[str]:
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", text.lower())
    stop = STOPWORDS.get(language, STOPWORDS["en"])
    filtered = [w for w in words if w not in stop and len(w) >= 4]
    counts = Counter(filtered)
    return [w for w, _ in counts.most_common(top_n)]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _find_hook_sentence(sentences: list[str], language: str) -> str | None:
    words = HOOK_WORDS.get(language, []) + HOOK_WORDS.get("en", [])
    for s in sentences:
        low = s.lower()
        if any(w in low for w in words):
            return s
    return None


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].rstrip(",.;: ") + "..."


class HeuristicCaptionGenerator:
    def generate(self, transcript_text: str, language: str = "id") -> CaptionResult:
        transcript_text = (transcript_text or "").strip()
        sentences = _split_sentences(transcript_text)
        keywords = extract_keywords(transcript_text, language)

        hook = _find_hook_sentence(sentences, language)
        if hook:
            title = _truncate(hook, 70)
        elif keywords:
            templates = TITLE_TEMPLATES.get(language, TITLE_TEMPLATES["en"])
            # Deterministic (hash-based) template choice, not random.choice
            # -- the same transcript should always yield the same title,
            # which keeps this reproducible and testable, not flaky.
            idx = sum(ord(c) for c in transcript_text[:20]) % len(templates) if transcript_text else 0
            title = templates[idx].format(kw=keywords[0].capitalize())
        elif sentences:
            title = _truncate(sentences[0], 70)
        else:
            title = "Klip Baru" if language == "id" else "New Clip"

        caption = _truncate(" ".join(sentences[:2]) if sentences else transcript_text, 150)
        description = _truncate(" ".join(sentences[:4]) if sentences else transcript_text, 400)

        hashtags = [tag for tag in (re.sub(r"[^a-z0-9]", "", kw) for kw in keywords[:6]) if tag]

        return CaptionResult(title=title, caption=caption, description=description,
                              hashtags=hashtags, keywords=keywords, source="heuristic")


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text)
    return json.loads(text)


class LLMCaptionGenerator:
    """Calls the user's own API key. Falls back to HeuristicCaptionGenerator
    on any failure -- see module docstring."""

    ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
    OPENAI_URL = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODELS = {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini"}

    def __init__(self, provider: str = "anthropic", api_key: str = "", model: str = ""):
        self.provider = provider if provider in ("anthropic", "openai") else "anthropic"
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODELS[self.provider]
        self._fallback = HeuristicCaptionGenerator()

    def generate(self, transcript_text: str, language: str = "id", timeout: float = 20.0) -> CaptionResult:
        if not self.api_key:
            log.info("No LLM API key configured; using heuristic caption generator")
            return self._fallback.generate(transcript_text, language)
        try:
            data = (self._call_anthropic(transcript_text, language, timeout) if self.provider == "anthropic"
                    else self._call_openai(transcript_text, language, timeout))
            return CaptionResult(
                title=str(data.get("title", ""))[:100], caption=str(data.get("caption", ""))[:300],
                description=str(data.get("description", ""))[:800],
                hashtags=[str(h).lstrip("#") for h in data.get("hashtags", [])][:10],
                keywords=[str(k) for k in data.get("keywords", [])][:10], source="llm",
            )
        except Exception as exc:
            log.warning("LLM caption generation failed (%s: %s), falling back to heuristic",
                        type(exc).__name__, exc)
            return self._fallback.generate(transcript_text, language)

    @staticmethod
    def _prompt(transcript_text: str, language: str) -> tuple[str, str]:
        lang_name = "Bahasa Indonesia" if language == "id" else "English"
        system = (
            f"You write short-form video metadata in {lang_name}. Given a clip transcript, "
            f"respond with ONLY a JSON object with keys: title (punchy, under 70 chars), "
            f"caption (under 150 chars), description (under 400 chars), "
            f"hashtags (array of 5-8 lowercase words without #), keywords (array of 5-8 SEO keywords). "
            f"No markdown formatting, no explanation -- output only the JSON object."
        )
        user = f"Transcript:\n{transcript_text[:2000]}"
        return system, user

    def _call_anthropic(self, transcript_text: str, language: str, timeout: float) -> dict:
        system, user = self._prompt(transcript_text, language)
        payload = json.dumps({
            "model": self.model, "max_tokens": 500, "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(self.ANTHROPIC_URL, data=payload, method="POST", headers={
            "content-type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        return _parse_json_response(text)

    def _call_openai(self, transcript_text: str, language: str, timeout: float) -> dict:
        system, user = self._prompt(transcript_text, language)
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 500,
        }).encode("utf-8")
        req = urllib.request.Request(self.OPENAI_URL, data=payload, method="POST", headers={
            "content-type": "application/json", "authorization": f"Bearer {self.api_key}",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        return _parse_json_response(text)


def get_generator(settings):
    """Factory reading config.settings.Settings so callers don't need to
    know about the two backend classes."""
    backend = getattr(settings, "caption_backend", "heuristic")
    if backend == "llm":
        return LLMCaptionGenerator(
            provider=getattr(settings, "caption_llm_provider", "anthropic"),
            api_key=getattr(settings, "caption_llm_api_key", ""),
            model=getattr(settings, "caption_llm_model", ""),
        )
    return HeuristicCaptionGenerator()
