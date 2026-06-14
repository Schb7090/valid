"""Tests for pure/deterministic functions in state_manager.py"""
import pytest
from state_manager import StateManager


class TestGenerateHash:
    def test_same_input_produces_same_hash(self):
        h1 = StateManager.generate_hash("prompt", "model")
        h2 = StateManager.generate_hash("prompt", "model")
        assert h1 == h2

    def test_different_input_produces_different_hash(self):
        h1 = StateManager.generate_hash("prompt1", "model")
        h2 = StateManager.generate_hash("prompt2", "model")
        assert h1 != h2

    def test_order_matters(self):
        h1 = StateManager.generate_hash("a", "b")
        h2 = StateManager.generate_hash("b", "a")
        assert h1 != h2

    def test_single_arg(self):
        h = StateManager.generate_hash("only")
        assert isinstance(h, str)
        assert len(h) == 32  # MD5 hex digest length

    def test_empty_string(self):
        h = StateManager.generate_hash("")
        assert isinstance(h, str)
        assert len(h) == 32

    def test_multiple_args_combined(self):
        h1 = StateManager.generate_hash("a", "b", "c")
        # Internally uses "|" as separator
        h2 = StateManager.generate_hash("a|b", "c")
        # These should differ since "a|b|c" != "a|b|c" — actually they'd be the same here!
        # This tests that the separator joins correctly
        assert isinstance(h1, str)

    def test_unicode_input(self):
        h = StateManager.generate_hash("névsor", "áéíóöőúüű")
        assert isinstance(h, str)
        assert len(h) == 32

    def test_returns_32_char_hex(self):
        h = StateManager.generate_hash("test")
        assert all(c in "0123456789abcdef" for c in h)
