"""Tests for pure functions in validator.py"""
import pytest
from models import FactRecord
from validator import calculate_overlap


def make_fact(claim_text):
    return FactRecord(fact_id="f1", claim_text=claim_text, confidence=1.0)


class TestCalculateOverlap:
    def test_exact_match_returns_one(self):
        claim = "the dog runs fast"
        facts = [make_fact("the dog runs fast")]
        assert calculate_overlap(claim, facts) == 1.0

    def test_no_overlap_returns_zero(self):
        claim = "zebra elephant volcano"
        facts = [make_fact("satellite orbit trajectory")]
        assert calculate_overlap(claim, facts) == 0.0

    def test_empty_claim_returns_zero(self):
        facts = [make_fact("some fact text")]
        assert calculate_overlap("", facts) == 0.0

    def test_empty_facts_list_returns_zero(self):
        assert calculate_overlap("some claim here", []) == 0.0

    def test_partial_overlap(self):
        claim = "the dog runs"  # 3 tokens
        facts = [make_fact("the dog flies")]  # shares: the, dog
        result = calculate_overlap(claim, facts)
        # overlap = 2/3
        assert abs(result - 2/3) < 0.01

    def test_case_insensitive(self):
        claim = "The Dog Runs Fast"
        facts = [make_fact("the dog runs fast")]
        assert calculate_overlap(claim, facts) == 1.0

    def test_multiple_facts_union(self):
        claim = "alpha beta gamma delta"  # 4 tokens
        facts = [
            make_fact("alpha beta"),       # contributes: alpha, beta
            make_fact("gamma epsilon"),    # contributes: gamma
        ]
        result = calculate_overlap(claim, facts)
        # overlap = {alpha, beta, gamma} ∩ {alpha, beta, gamma, delta} = 3 tokens / 4 = 0.75
        assert abs(result - 0.75) < 0.01

    def test_duplicate_tokens_in_claim_count_once(self):
        # claim_tokens is a set, so duplicates ignored
        claim = "dog dog dog"  # set = {"dog"}
        facts = [make_fact("dog")]
        assert calculate_overlap(claim, facts) == 1.0

    def test_punctuation_in_claim(self):
        # re.findall(r'\w+', ...) strips punctuation
        claim = "hello, world!"
        facts = [make_fact("hello world")]
        assert calculate_overlap(claim, facts) == 1.0
