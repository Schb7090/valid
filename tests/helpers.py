"""Shared test helper factories (not fixtures — those live in conftest.py)."""
from models import (
    ArgumentGraph, ArgumentNode, ArgumentEdge,
    TaskContext, TaskConstraints, CouncilThresholds,
)


def make_node(id: str, node_type: str = "derivation", title: str = "Section", claim: str = "A claim."):
    return ArgumentNode(id=id, section_title=title, claim=claim, node_type=node_type, research_queries=[])


def make_graph(nodes, edges=None):
    return ArgumentGraph(title="Test Graph", nodes=nodes, edges=edges or [])


def make_context(**kwargs):
    defaults = dict(user_prompt="Test prompt", task_type="analytical_report")
    defaults.update(kwargs)
    return TaskContext(**defaults)
