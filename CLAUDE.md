# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the engine (requires GEMINI_API_KEY in environment)
GEMINI_API_KEY=<key> python3 engine.py

# The engine reads config.yaml on startup and before each major phase (hot-reload)
```

There is no test suite. Configuration is in `config.yaml`; edit it to tune thresholds without touching Python code.

## Architecture

**valid** (UPVS-Engine v0.9) is a 7-layer AI-powered document generation pipeline. It accepts a natural-language request and produces a fully-cited document. All LLM calls go to Google Gemini (`gemini-1.5-flash` for fast/cheap layers, `gemini-1.5-pro` for quality-critical layers). Orchestration and persistence are deterministic Python.

### Layer pipeline (`engine.py` orchestrates)

```
1. Intent Router    → intent_router.py    — classifies task type, sets research depth / grounding level
2. Planner          → planner.py          — generates an ArgumentGraph (DAG of 3–50 nodes)
2.5 DAG Reviewer    → dag_reviewer.py     — validates logic before expensive research; up to 2 retries
3. Researcher       → researcher.py       — hits external APIs, scores sources, deduplicates, stores facts
4.1 Generator       → generator.py        — produces 3 draft branches per node (conservative/critical/synthetic)
4.2 Validator       → validator.py        — V1 Grounding Protocol; removes unsupported claims
5. Logic Auditor    → logic_auditor.py    — validates DAG edge transitions; detects named fallacies
6. Assembler        → assembler.py        — topological order → smooth text → APA citations
```

The engine selects a routing tier based on researcher confidence: **Super** (≥0.85) → 1 draft, no retry; **Moderate** (0.70–0.85) → 2 drafts, 1 retry; **Weak** (<0.70) → 3 drafts, 3 retries.

### State machine (`state_manager.py` + `models.py`)

`UPVSEngineState` (Pydantic) tracks session status through: `init → routing → planning → researching → generating → auditing_logic → assembling → completed`. The full state is checkpointed to JSON per session so execution can be resumed after a crash.

`state_manager.py` owns all SQLite access. Tables:

| Table | Purpose |
|---|---|
| `api_cache` | External API responses; 7-day TTL keyed by URL+params |
| `llm_cache` | LLM outputs keyed by MD5(prompt+model); persistent |
| `sources` | Full bibliographic metadata including ORCID, DOI, impact factor |
| `facts` | Extracted claims with confidence scores |
| `fact_sources` | Many-to-many: facts ↔ sources |
| `node_facts` | DAG node ↔ fact mappings |
| `audit_log` | Every decision and error, per session |
| `pending_writes` | Async I/O queue; `SQLiteAsyncWriter` background thread drains this |

### Key algorithms

**Multi-source deduplication (researcher layer):** sources are deduplicated in three passes — exact ORCID match, normalized author+institution+email-domain hash, then fuzzy Levenshtein similarity ≥0.85.

**Confidence scoring:** `Authority(40%) + Recency(30%) + Consensus(30%)` where Authority = `Quality(60%) + ImpactFactor(20%) + CitationCount(20%)`, Recency decays 5% per year, Consensus = `min(independent_sources / 3, 1.0)`.

**V1 Grounding Protocol (validator layer):**
1. Split draft into atomic claims
2. Token-overlap prefilter (fast) — thresholds 60% high / 15% low
3. LLM entailment check (slow) — only for ambiguous middle band
4. Remove or tag `[UNSUPPORTED]` any claim that fails

**LLM JSON enforcement (`llm_utils.py`):** `enforce_pydantic_schema()` calls Gemini in JSON mode, validates with Pydantic, and on `ValidationError` feeds the error message back to the model with a temperature bump (0.5 → 0.8 → 1.0). Max 3 retries.

### Configuration (`config.yaml`)

All numeric thresholds are here: DAG complexity ranges, research depth API-call counts, council veto scores, grounding percentage targets, fallacy checklist, debias checklist. The engine re-reads the file before each layer so changes take effect mid-run without restarting.

Key kill-switch: `max_session_tokens: 50000` — the engine aborts if cumulative token usage exceeds this.

### Adding a new layer

1. Create `my_layer.py` with a function that accepts `UPVSEngineState` and returns an updated state
2. Add a new status value to `UPVSEngineState.status` in `models.py`
3. Insert the call in `engine.py` between the appropriate existing layers
4. Add relevant config keys to `config.yaml`
