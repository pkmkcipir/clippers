"""Tests for ai/caption_generator.py.

The LLM-backend tests make REAL network calls (api.anthropic.com is
reachable from this sandbox) using deliberately invalid credentials --
this exercises the actual error response shape and fallback behavior
rather than a guessed/mocked one. No valid API key is used or required;
these tests only verify graceful degradation.
Run with: pytest tests/test_caption_generator.py -v
"""
import pytest

from ai.caption_generator import (
    HeuristicCaptionGenerator, LLMCaptionGenerator, extract_keywords, CaptionResult,
)

TRANSCRIPT_ID = (
    "Halo semua, hari ini kita bahas sesuatu yang gila banget soal investasi saham. "
    "Ternyata banyak orang pemula rugi karena gak paham resiko investasi saham. "
    "Jadi ceritanya begini, aku dulu juga gak percaya kalau investasi saham bisa untung besar. "
    "Tapi setelah belajar strategi investasi yang benar, hasilnya bikin kaget parah. "
    "Nah ini bagian pentingnya, jangan sampai kelewat kalau mau mulai investasi saham."
)

TRANSCRIPT_EN = (
    "Hey everyone, today we are talking about something insane about productivity hacks. "
    "You wont believe how many people waste hours every single day on useless meetings. "
    "So heres the thing, I never used to believe that a simple morning routine could change everything. "
    "But after trying this method for thirty days, the results were honestly shocking. "
    "This is the important part so dont skip it if you want to actually get more done."
)


class TestKeywordExtraction:
    def test_extracts_topical_words_not_generic_openers(self):
        keywords = extract_keywords(TRANSCRIPT_ID, language="id")
        assert "investasi" in keywords
        assert "saham" in keywords
        # Generic transcript-opener filler should not crowd out real topic words.
        assert "halo" not in keywords
        assert "hari" not in keywords

    def test_respects_top_n(self):
        keywords = extract_keywords(TRANSCRIPT_ID, language="id", top_n=3)
        assert len(keywords) <= 3

    def test_empty_text_returns_empty_list(self):
        assert extract_keywords("", language="id") == []


class TestHeuristicCaptionGenerator:
    def setup_method(self):
        self.gen = HeuristicCaptionGenerator()

    def test_generates_all_required_fields(self):
        result = self.gen.generate(TRANSCRIPT_ID, language="id")
        assert result.title
        assert result.caption
        assert result.description
        assert result.hashtags
        assert result.keywords
        assert result.source == "heuristic"

    def test_title_uses_hook_sentence_when_present(self):
        result = self.gen.generate(TRANSCRIPT_ID, language="id")
        # TRANSCRIPT_ID's first sentence contains the hook word "gila".
        assert "gila" in result.title.lower()

    def test_is_deterministic(self):
        r1 = self.gen.generate(TRANSCRIPT_ID, language="id")
        r2 = self.gen.generate(TRANSCRIPT_ID, language="id")
        assert r1.title == r2.title
        assert r1.hashtags == r2.hashtags

    def test_different_transcripts_can_yield_different_titles(self):
        r1 = self.gen.generate(TRANSCRIPT_ID, language="id")
        r2 = self.gen.generate(TRANSCRIPT_EN, language="en")
        assert r1.title != r2.title

    def test_caption_and_description_respect_length_limits(self):
        long_text = TRANSCRIPT_ID * 5
        result = self.gen.generate(long_text, language="id")
        assert len(result.caption) <= 154  # 150 + a few chars of "..." tolerance
        assert len(result.description) <= 404

    def test_empty_transcript_still_returns_a_usable_title(self):
        result = self.gen.generate("", language="id")
        assert result.title
        result_en = self.gen.generate("", language="en")
        assert result_en.title

    def test_hashtags_are_valid_hashtag_strings(self):
        result = self.gen.generate(TRANSCRIPT_ID, language="id")
        for tag in result.hashtags:
            assert tag.isalnum() or tag.isalpha()
            assert " " not in tag
            assert "#" not in tag

    def test_hashtags_str_and_keywords_str_formatting(self):
        result = CaptionResult(hashtags=["foo", "bar"], keywords=["alpha", "beta"])
        assert result.hashtags_str() == "#foo #bar"
        assert result.keywords_str() == "alpha, beta"


class TestLLMCaptionGeneratorFallback:
    """No valid API key is available in this environment -- these tests
    verify the fallback path degrades gracefully, using real network
    calls against real error responses rather than mocks."""

    def test_no_api_key_falls_back_without_network_call(self):
        gen = LLMCaptionGenerator(provider="anthropic", api_key="")
        result = gen.generate(TRANSCRIPT_ID, language="id")
        assert result.source == "heuristic"
        assert result.title

    def test_invalid_anthropic_key_falls_back_gracefully(self):
        gen = LLMCaptionGenerator(provider="anthropic", api_key="sk-ant-invalid-fake-key-00000")
        result = gen.generate(TRANSCRIPT_ID, language="id", timeout=15.0)
        assert result.source == "heuristic"
        assert result.title
        assert result.hashtags

    def test_unreachable_openai_endpoint_falls_back_gracefully(self):
        gen = LLMCaptionGenerator(provider="openai", api_key="sk-fake-openai-key")
        result = gen.generate(TRANSCRIPT_EN, language="en", timeout=15.0)
        assert result.source == "heuristic"
        assert result.title

    def test_invalid_provider_string_defaults_to_anthropic(self):
        gen = LLMCaptionGenerator(provider="not-a-real-provider", api_key="x")
        assert gen.provider == "anthropic"
