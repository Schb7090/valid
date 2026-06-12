import sys
import os
from unittest.mock import MagicMock
import pytest

# Stub out google.generativeai before any source module imports it
google_mock = MagicMock()
sys.modules.setdefault('google', google_mock)
sys.modules.setdefault('google.generativeai', google_mock)

# Add the project root so tests can import source modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import ArgumentGraph, ArgumentEdge
from helpers import make_node, make_graph, make_context


@pytest.fixture
def simple_graph():
    n1 = make_node("n1", "axiom")
    n2 = make_node("n2", "derivation")
    n3 = make_node("n3", "derivation")
    edge = ArgumentEdge(source="n1", target="n2", relation="supports")
    return ArgumentGraph(title="G", nodes=[n1, n2, n3], edges=[edge])


@pytest.fixture
def config():
    return {"engine_parameters": {"max_axiom_ratio": 0.3}}
