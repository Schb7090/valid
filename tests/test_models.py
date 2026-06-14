"""Tests for Pydantic models in models.py"""
import pytest
from pydantic import ValidationError
from helpers import make_node, make_graph, make_context
from models import (
    CouncilThresholds, TaskContext, TokenUsage, ArgumentGraph,
    ArgumentNode, ArgumentEdge, FactRecord, SourceRecord
)


class TestTokenUsage:
    def test_defaults_to_zero(self):
        t = TokenUsage()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0
        assert t.total_tokens == 0

    def test_add_accumulates_correctly(self):
        t = TokenUsage()
        t.add(100, 50)
        assert t.prompt_tokens == 100
        assert t.completion_tokens == 50
        assert t.total_tokens == 150

    def test_add_multiple_times(self):
        t = TokenUsage()
        t.add(100, 50)
        t.add(200, 100)
        assert t.prompt_tokens == 300
        assert t.completion_tokens == 150
        assert t.total_tokens == 450


class TestCouncilThresholds:
    def test_default_values(self):
        c = CouncilThresholds()
        assert c.domain_expert_veto == 60
        assert c.debiaser_veto == 40
        assert c.grounding_verifier_veto == 90

    def test_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            CouncilThresholds(domain_expert_veto=101)

    def test_accepts_boundary_values(self):
        c = CouncilThresholds(domain_expert_veto=0, debiaser_veto=100)
        assert c.domain_expert_veto == 0
        assert c.debiaser_veto == 100


class TestTaskContext:
    def test_requires_user_prompt_and_task_type(self):
        with pytest.raises(ValidationError):
            TaskContext(task_type="academic_essay")  # missing user_prompt

    def test_defaults_are_sensible(self):
        ctx = make_context()
        assert ctx.research_depth == "deep"
        assert ctx.grounding_level == "strict"
        assert ctx.assembly_freedom == "strict"
        assert ctx.is_rejectable is False

    def test_invalid_task_type_rejected(self):
        with pytest.raises(ValidationError):
            TaskContext(user_prompt="p", task_type="not_a_real_type")


class TestArgumentNode:
    def test_default_grounding_status_is_ungrounded(self):
        n = make_node("n1")
        assert n.grounding_status == "ungrounded"

    def test_invalid_node_type_rejected(self):
        with pytest.raises(ValidationError):
            ArgumentNode(id="n1", section_title="T", claim="C", node_type="invalid_type")


class TestFactRecord:
    def test_basic_creation(self):
        f = FactRecord(fact_id="f1", claim_text="The sky is blue.", context="context", confidence=0.9)
        assert f.fact_id == "f1"

    def test_default_confidence_is_zero(self):
        f = FactRecord(fact_id="f1", claim_text="Some claim.")
        assert f.confidence == 0.0
