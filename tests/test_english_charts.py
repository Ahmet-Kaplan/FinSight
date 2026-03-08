"""Tests for English-only chart enforcement: CJK detection, ASCII filename sanitization,
and language-conditional prompt instructions."""

import os
import re
import pytest

from src.utils.chart_utils import contains_cjk, sanitize_chart_filename


# ---------------------------------------------------------------------------
# contains_cjk()
# ---------------------------------------------------------------------------

class TestContainsCjk:
    def test_pure_ascii(self):
        assert contains_cjk('revenue_growth_2025.png') is False

    def test_pure_english_sentence(self):
        assert contains_cjk('The quick brown fox jumps over the lazy dog') is False

    def test_chinese_characters(self):
        assert contains_cjk('ASML收入趋势图') is True

    def test_single_cjk_char(self):
        assert contains_cjk('chart_名.png') is True

    def test_japanese_kanji(self):
        # Japanese kanji fall within CJK Unified Ideographs
        assert contains_cjk('東京タワー') is True

    def test_empty_string(self):
        assert contains_cjk('') is False

    def test_numbers_and_punctuation(self):
        assert contains_cjk('123-456_789.png') is False

    def test_mixed_with_cjk(self):
        assert contains_cjk('revenue_收入_trend.png') is True

    def test_korean_hangul_not_detected(self):
        # Hangul is not in CJK Unified Ideographs range — this is expected behavior
        # (our detector focuses on ideographs, not syllabary)
        # Hangul Syllables: U+AC00-U+D7AF — outside our CJK range
        assert contains_cjk('한국어') is False

    def test_cjk_extension_a(self):
        # U+3400 is in CJK Extension A
        assert contains_cjk('\u3400') is True


# ---------------------------------------------------------------------------
# sanitize_chart_filename(ascii_only=True)
# ---------------------------------------------------------------------------

class TestSanitizeAsciiOnly:
    def test_ascii_passthrough(self):
        result = sanitize_chart_filename('revenue_trend.png', ascii_only=True)
        assert result == 'revenue_trend.png'

    def test_strips_cjk_when_ascii_only(self):
        result = sanitize_chart_filename('ASML收入趋势图.png', ascii_only=True)
        assert result == 'ASML.png'
        assert not contains_cjk(result)

    def test_strips_all_non_ascii(self):
        result = sanitize_chart_filename('café_latté.png', ascii_only=True)
        # accented chars stripped, only ASCII remain
        assert 'caf' in result
        assert 'é' not in result

    def test_preserves_cjk_when_not_ascii_only(self):
        result = sanitize_chart_filename('ASML收入趋势图.png', ascii_only=False)
        assert 'ASML' in result
        assert contains_cjk(result)

    def test_empty_after_strip_returns_chart(self):
        result = sanitize_chart_filename('收入趋势图', ascii_only=True)
        assert result == 'chart'

    def test_spaces_replaced_with_underscores(self):
        result = sanitize_chart_filename('revenue growth chart.png', ascii_only=True)
        assert result == 'revenue_growth_chart.png'
        assert ' ' not in result

    def test_max_length_respected(self):
        long_name = 'a' * 100 + '.png'
        result = sanitize_chart_filename(long_name, max_length=20, ascii_only=True)
        assert len(result) <= 20

    def test_special_chars_stripped_ascii_mode(self):
        result = sanitize_chart_filename('chart@#$%^&*().png', ascii_only=True)
        assert result == 'chart.png'

    def test_hyphens_and_underscores_preserved(self):
        result = sanitize_chart_filename('my-chart_v2.png', ascii_only=True)
        assert result == 'my-chart_v2.png'


# ---------------------------------------------------------------------------
# Prompt YAML enforcement
# ---------------------------------------------------------------------------

class TestPromptLanguageConditional:
    """Verify that prompt templates use language-conditional filename instructions."""

    @pytest.fixture
    def financial_prompts_path(self):
        return os.path.join(
            os.path.dirname(__file__), '..', 'src', 'agents',
            'data_analyzer', 'prompts', 'financial_prompts.yaml',
        )

    @pytest.fixture
    def general_prompts_path(self):
        return os.path.join(
            os.path.dirname(__file__), '..', 'src', 'agents',
            'data_analyzer', 'prompts', 'general_prompts.yaml',
        )

    def test_financial_prompts_no_hardcoded_chinese_filenames(self, financial_prompts_path):
        """financial_prompts.yaml must NOT contain 'Save charts using Chinese names'."""
        if not os.path.exists(financial_prompts_path):
            pytest.skip('financial_prompts.yaml not found')
        with open(financial_prompts_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'Save charts using Chinese names' not in content, (
            'financial_prompts.yaml still has hardcoded Chinese filename instruction'
        )

    def test_general_prompts_no_hardcoded_chinese_filenames(self, general_prompts_path):
        """general_prompts.yaml must NOT contain 'Save filenames in Chinese'."""
        if not os.path.exists(general_prompts_path):
            pytest.skip('general_prompts.yaml not found')
        with open(general_prompts_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'Save filenames in Chinese' not in content, (
            'general_prompts.yaml still has hardcoded Chinese filename instruction'
        )

    def test_financial_prompts_uses_target_language_placeholder(self, financial_prompts_path):
        """financial_prompts.yaml should reference {target_language} for filenames."""
        if not os.path.exists(financial_prompts_path):
            pytest.skip('financial_prompts.yaml not found')
        with open(financial_prompts_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'target_language' in content, (
            'financial_prompts.yaml should reference target_language for filename instructions'
        )

    def test_general_prompts_uses_target_language_placeholder(self, general_prompts_path):
        """general_prompts.yaml should reference {target_language} for filenames."""
        if not os.path.exists(general_prompts_path):
            pytest.skip('general_prompts.yaml not found')
        with open(general_prompts_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'target_language' in content, (
            'general_prompts.yaml should reference target_language for filename instructions'
        )
