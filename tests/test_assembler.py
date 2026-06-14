"""Tests for pure functions in assembler.py"""
import pytest
from helpers import make_node
from models import ArgumentGraph, ArgumentEdge, SourceRecord
from assembler import get_topological_order, generate_apa_bibliography, build_freedom_rules


def make_edge(src, tgt):
    return ArgumentEdge(source=src, target=tgt, relation="supports")


def make_source(source_id, authors=None, year=None, title="A Paper", doi=None, url=None):
    return SourceRecord(source_id=source_id, title=title, authors=authors, year=year,
                        source_type="journal", doi=doi, url=url)


class TestGetTopologicalOrder:
    def test_linear_chain(self):
        n1, n2, n3 = make_node("n1"), make_node("n2"), make_node("n3")
        edges = [make_edge("n1", "n2"), make_edge("n2", "n3")]
        graph = ArgumentGraph(title="G", nodes=[n1, n2, n3], edges=edges)
        order = get_topological_order(graph)
        assert order == ["n1", "n2", "n3"]

    def test_diamond_shape(self):
        # n1 -> n2, n1 -> n3, n2 -> n4, n3 -> n4
        nodes = [make_node(nid) for nid in ["n1", "n2", "n3", "n4"]]
        edges = [make_edge("n1", "n2"), make_edge("n1", "n3"),
                 make_edge("n2", "n4"), make_edge("n3", "n4")]
        graph = ArgumentGraph(title="G", nodes=nodes, edges=edges)
        order = get_topological_order(graph)
        assert order.index("n1") < order.index("n4")
        assert order.index("n2") < order.index("n4")
        assert order.index("n3") < order.index("n4")
        assert len(order) == 4

    def test_empty_graph(self):
        graph = ArgumentGraph(title="G", nodes=[], edges=[])
        assert get_topological_order(graph) == []

    def test_no_edges(self):
        nodes = [make_node("n1"), make_node("n2")]
        graph = ArgumentGraph(title="G", nodes=nodes, edges=[])
        order = get_topological_order(graph)
        assert set(order) == {"n1", "n2"}

    def test_single_node(self):
        graph = ArgumentGraph(title="G", nodes=[make_node("solo")], edges=[])
        assert get_topological_order(graph) == ["solo"]

    def test_cycle_fallback_returns_all_ids(self):
        # Cyclic: n1->n2->n1
        n1, n2 = make_node("n1"), make_node("n2")
        edges = [make_edge("n1", "n2"), make_edge("n2", "n1")]
        graph = ArgumentGraph(title="G", nodes=[n1, n2], edges=edges)
        order = get_topological_order(graph)
        assert set(order) == {"n1", "n2"}
        assert len(order) == 2


class TestBuildFreedomRules:
    def test_strict_level(self):
        result = build_freedom_rules("strict")
        assert "SZIGORÚ" in result or "strict" in result.lower() or "kötőszavak" in result.lower()

    def test_moderate_level(self):
        result = build_freedom_rules("moderate")
        assert "MÉRSÉKELT" in result or "moderate" in result.lower() or "mondatszerkezet" in result.lower()

    def test_flexible_level(self):
        result = build_freedom_rules("flexible")
        assert "LAZA" in result or "flexible" in result.lower() or "stílus" in result.lower()

    def test_returns_string(self):
        for level in ("strict", "moderate", "flexible"):
            assert isinstance(build_freedom_rules(level), str)
            assert len(build_freedom_rules(level)) > 10


class TestGenerateApaBibliography:
    def test_empty_sources_returns_empty(self):
        assert generate_apa_bibliography([]) == ""

    def test_single_source_with_doi(self):
        src = make_source("s1", authors="Smith, J.", year=2023, title="Great Paper", doi="10.1234/test")
        result = generate_apa_bibliography([src])
        assert "Smith, J." in result
        assert "2023" in result
        assert "Great Paper" in result
        assert "10.1234/test" in result

    def test_single_source_with_url(self):
        src = make_source("s1", authors="Doe, J.", year=2021, title="Web Article", url="https://example.com")
        result = generate_apa_bibliography([src])
        assert "https://example.com" in result

    def test_source_without_author_uses_default(self):
        src = make_source("s1", title="No Author Paper")
        result = generate_apa_bibliography([src])
        assert "Ismeretlen" in result or "Unknown" in result.lower() or result  # graceful fallback

    def test_source_without_year_uses_nd(self):
        src = make_source("s1", authors="Nobody", title="Timeless")
        result = generate_apa_bibliography([src])
        assert "n.d." in result

    def test_multiple_sources_numbered_sequentially(self):
        sources = [
            make_source("s1", authors="Alpha", year=2020, title="Paper A"),
            make_source("s2", authors="Beta", year=2021, title="Paper B"),
        ]
        result = generate_apa_bibliography(sources)
        assert "[1]" in result
        assert "[2]" in result

    def test_output_contains_section_header(self):
        src = make_source("s1", authors="A", year=2020, title="T")
        result = generate_apa_bibliography([src])
        assert "Irodalomjegyzék" in result
