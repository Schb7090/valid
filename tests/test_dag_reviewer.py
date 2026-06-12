"""Tests for pure functions in dag_reviewer.py"""
import json
import pytest
from helpers import make_node, make_context
from models import ArgumentGraph, ArgumentEdge
from dag_reviewer import check_axiom_ratio, build_reviewer_prompt, parse_reviewer_response


def make_graph_with_types(types):
    nodes = [make_node(f"n{i}", t) for i, t in enumerate(types)]
    return ArgumentGraph(title="Test", nodes=nodes, edges=[])


class TestCheckAxiomRatio:
    def test_empty_graph_fails(self, config):
        graph = ArgumentGraph(title="G", nodes=[], edges=[])
        ok, msg = check_axiom_ratio(graph, config)
        assert ok is False
        assert "üres" in msg

    def test_no_axioms_passes(self, config):
        graph = make_graph_with_types(["derivation", "derivation", "conclusion"])
        ok, _ = check_axiom_ratio(graph, config)
        assert ok is True

    def test_exactly_at_limit_passes(self, config):
        # 1 axiom out of 4 = 25% < 30% limit
        graph = make_graph_with_types(["axiom", "derivation", "derivation", "derivation"])
        ok, _ = check_axiom_ratio(graph, config)
        assert ok is True

    def test_over_limit_fails(self, config):
        # 2 axioms out of 4 = 50% > 30% limit
        graph = make_graph_with_types(["axiom", "axiom", "derivation", "derivation"])
        ok, msg = check_axiom_ratio(graph, config)
        assert ok is False
        assert "axióma" in msg.lower() or "axiom" in msg.lower()

    def test_all_axioms_fails(self, config):
        graph = make_graph_with_types(["axiom", "axiom", "axiom"])
        ok, _ = check_axiom_ratio(graph, config)
        assert ok is False

    def test_single_non_axiom_passes(self, config):
        graph = make_graph_with_types(["derivation"])
        ok, _ = check_axiom_ratio(graph, config)
        assert ok is True

    def test_custom_max_ratio(self):
        config = {"engine_parameters": {"max_axiom_ratio": 0.5}}
        # 2 axioms out of 4 = 50% — exactly at limit, should pass (ratio > max is the check)
        graph = make_graph_with_types(["axiom", "axiom", "derivation", "derivation"])
        ok, _ = check_axiom_ratio(graph, config)
        assert ok is True


class TestBuildReviewerPrompt:
    def test_contains_task_type(self):
        from models import ArgumentGraph, ArgumentEdge
        graph = ArgumentGraph(title="My Graph", nodes=[make_node("n1", "axiom")], edges=[])
        ctx = make_context(task_type="academic_essay", research_depth="deep")
        prompt = build_reviewer_prompt(graph, ctx)
        assert "academic_essay" in prompt

    def test_contains_graph_title(self):
        graph = ArgumentGraph(title="Unique Title XYZ", nodes=[make_node("n1")], edges=[])
        ctx = make_context()
        prompt = build_reviewer_prompt(graph, ctx)
        assert "Unique Title XYZ" in prompt

    def test_contains_node_ids(self):
        n1 = make_node("node_alpha", "axiom")
        n2 = make_node("node_beta", "derivation")
        graph = ArgumentGraph(title="G", nodes=[n1, n2], edges=[])
        ctx = make_context()
        prompt = build_reviewer_prompt(graph, ctx)
        assert "node_alpha" in prompt
        assert "node_beta" in prompt

    def test_contains_edge_info(self):
        from models import ArgumentEdge
        n1 = make_node("n1")
        n2 = make_node("n2")
        edge = ArgumentEdge(source="n1", target="n2", relation="supports")
        graph = ArgumentGraph(title="G", nodes=[n1, n2], edges=[edge])
        ctx = make_context()
        prompt = build_reviewer_prompt(graph, ctx)
        assert "n1" in prompt
        assert "n2" in prompt


class TestParseReviewerResponse:
    def test_parses_approved_true(self):
        raw = json.dumps({"is_approved": True, "critique": ""})
        approved, critique = parse_reviewer_response(raw)
        assert approved is True
        assert critique == ""

    def test_parses_approved_false_with_critique(self):
        raw = json.dumps({"is_approved": False, "critique": "Logic error at node 2."})
        approved, critique = parse_reviewer_response(raw)
        assert approved is False
        assert "Logic error" in critique

    def test_invalid_json_returns_false(self):
        approved, critique = parse_reviewer_response("not valid json {{{")
        assert approved is False
        assert "Error" in critique or "error" in critique.lower()

    def test_missing_is_approved_defaults_to_false(self):
        raw = json.dumps({"critique": "Something"})
        approved, _ = parse_reviewer_response(raw)
        assert approved is False

    def test_missing_critique_defaults_to_empty(self):
        raw = json.dumps({"is_approved": True})
        approved, critique = parse_reviewer_response(raw)
        assert approved is True
        assert critique == ""
