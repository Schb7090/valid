"""Tests for pure functions in researcher.py"""
import pytest
from researcher import normalize_author, normalize_institution, truncate_context


class TestNormalizeAuthor:
    def test_removes_dr_title(self):
        assert "john smith" in normalize_author("Dr. John Smith")

    def test_removes_prof_title(self):
        result = normalize_author("Prof. Anna Jones")
        assert "anna jones" in result
        assert "prof" not in result

    def test_removes_phd_suffix(self):
        result = normalize_author("Jane Doe PhD")
        assert "phd" not in result
        assert "jane doe" in result

    def test_removes_multiple_titles(self):
        result = normalize_author("Dr. Prof. John")
        assert "dr" not in result
        assert "prof" not in result

    def test_lowercases_output(self):
        result = normalize_author("JOHN SMITH")
        assert result == result.lower()

    def test_strips_punctuation(self):
        result = normalize_author("Smith, J.")
        assert "," not in result
        assert "." not in result

    def test_empty_string_returns_empty(self):
        assert normalize_author("") == ""

    def test_collapses_whitespace(self):
        result = normalize_author("John    Smith")
        assert result == "john smith"


class TestNormalizeInstitution:
    def test_mit_expands(self):
        result = normalize_institution("MIT")
        assert result == "massachusetts institute of technology"

    def test_mit_with_dots_expands(self):
        result = normalize_institution("M.I.T")
        assert result == "massachusetts institute of technology"

    def test_ucla_expands(self):
        result = normalize_institution("UCLA")
        assert result == "university of california los angeles"

    def test_stanford_expands(self):
        result = normalize_institution("Stanford")
        assert result == "stanford university"

    def test_unknown_institution_lowercased(self):
        result = normalize_institution("Oxford University")
        assert result == "oxford university"

    def test_empty_string_returns_empty(self):
        assert normalize_institution("") == ""

    def test_removes_punctuation(self):
        result = normalize_institution("Harvard.")
        assert "." not in result

    def test_collapses_whitespace(self):
        result = normalize_institution("Harvard  University")
        assert "  " not in result


class TestTruncateContext:
    def test_short_text_returned_as_is(self):
        text = "short text"
        assert truncate_context(text, "query") == text

    def test_long_text_truncated_to_max_words(self):
        words = ["word"] * 1000
        text = " ".join(words)
        result = truncate_context(text, "query", max_words=100)
        assert len(result.split()) == 100

    def test_selects_window_with_most_query_terms(self):
        # Put query terms only in the last 100 words
        filler = ["filler"] * 500
        target = ["alpha"] * 100  # these are in the query
        text = " ".join(filler + target)
        result = truncate_context(text, "alpha beta", max_words=100)
        # The result should contain "alpha" since that's where query terms are dense
        assert "alpha" in result

    def test_exact_max_words_returns_unchanged(self):
        words = ["word"] * 500
        text = " ".join(words)
        result = truncate_context(text, "query", max_words=500)
        assert result == text

    def test_empty_query_still_returns_window(self):
        words = ["word"] * 1000
        text = " ".join(words)
        result = truncate_context(text, "", max_words=100)
        assert len(result.split()) == 100
