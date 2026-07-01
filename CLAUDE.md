# CLAUDE.md

This file gives Claude Code (and other AI assistants) the context needed to work effectively in this repository.

## What this is

**UPVS-Engine** (Ultra-Precision Verification and Synthesis Engine) — a Python orchestration engine that turns a free-text user prompt into a long-form, fact-grounded document (essay, report, article, etc.) with minimal hallucination. It is **not** a single LLM agent; it's a deterministic state machine (`engine.py`) that calls out to an LLM (Google Gemini) at well-defined points, while Python code owns control flow, persistence, caching, scoring, and retries.

The system, architecture rationale, and design decisions are documented in Hungarian in `UPVS_Technikai_Terv.md` (and its PDF twin `UPVS_Technikai_Terv.pdf`) — read that file first for the "why" behind any layer. Code comments and prompts throughout the codebase are also written in Hungarian; keep that convention when editing existing modules.

## Architecture — the 7 layers

The pipeline is a strict, resumable state machine driven by `UPVSEngine.run()` in `engine.py`, gated by `UPVSEngineState.status`:

1. **Intent Router** (`intent_router.py`) — classifies the user prompt into a `TaskContext` (task type, research depth, grounding level, council veto thresholds, DAG complexity, audience, constraints). Defaults to the strictest settings when ambiguous ("fail closed"). Can early-reject unanswerable/unscientific requests.
2. **Planner** (`planner.py`) — turns `TaskContext` into an `ArgumentGraph`: a DAG of `ArgumentNode`s (claims/premises/section) and `ArgumentEdge`s (supports/contrasts/extends/synthesizes).
3. **DAG Reviewer** (`dag_reviewer.py`) — pre-audits the DAG *before* expensive research calls happen. Runs a deterministic axiom-ratio check (`check_axiom_ratio`) plus an LLM logic audit. Rejects back to the Planner (max 2 retries) with structured critique.
4. **Researcher** (`researcher.py`) — fetches sources per `research_queries` (mocked via `mock_fetch_from_apis`; real integration would hit OpenAlex/PubMed/arXiv/web), scores/deduplicates sources (ORCID match, then SHA-256 identity hash, then fuzzy `difflib` matching), computes a weighted multi-factor confidence score (authority/recency/consensus), and persists `SourceRecord`/`FactRecord` rows into the SQLite fact store.
5. **Multi-Branch Generator** (`generator.py`) + **Validator** (`validator.py`) — generates up to 3 drafts per node (`conservative`/`critical`/`synthetic`, different temperatures) using a quote-first strategy (`QuoteFirstDraft`: extract literal quotes from facts, then write only from those quotes). The validator runs deterministic grounding (`verify_grounding_v1`: atomic claim split → token-overlap prefilter → LLM entailment check) plus a single strong-model pass for logic (blocking) and quality (advisory) scoring.
6. **Logical Arc Auditor** (`logic_auditor.py`) — after all nodes are drafted, audits every DAG edge for entailment, contradiction, and fallacies between the two connected sections.
7. **Output Assembler** (`assembler.py`) — topologically orders sections, smooths transitions with an "Anti-Frankenstein" LLM pass (content/facts are frozen — only connective tissue may change), replaces `[fact_id]` markers with numbered citations, and generates an APA bibliography from the fact store.

Supporting modules:
- `models.py` — all Pydantic schemas (closed taxonomies via `Literal`/`Enum`) and the top-level `UPVSEngineState`.
- `state_manager.py` — SQLite-backed persistence: LLM/API response cache, fact store tables (`sources`, `facts`, `fact_sources`, `node_facts`), audit log, and a crash-safe async write queue (`SQLiteAsyncWriter`, a background thread draining a `pending_writes` table) so disk I/O never blocks the main flow. Also handles JSON checkpointing of `UPVSEngineState` per `session_id`.
- `llm_utils.py` — the only place that talks to the Gemini API directly (`call_text`, `enforce_pydantic_schema`). `enforce_pydantic_schema` retries with escalating temperature and feeds Pydantic validation errors back into the prompt on failure.
- `config.yaml` — all tunable engine parameters (model routing per layer, veto thresholds, temperatures, complexity ranges, grounding levels, retry counts, prompt checklists). `engine.py._reload_config` re-reads this file on the fly mid-run so parameter tweaks take effect without a restart.

### Key operating principles (don't violate these when editing)

- **Autonomous, no human-in-the-loop (HITL)**: layers never pause to ask the user something mid-run; ambiguity always resolves to the *stricter* option.
- **Grounding first, not fact-check-after**: sources are fetched and validated *before* generation, and the generator is constrained to cite `[fact_id]` markers — this is intentional to avoid the "hallucinate then fix" spiral (see §2.3 of the tech plan).
- **Graceful degradation, not crashes**: a failing node gets tagged `[UNSUPPORTED]`/`[UNVERIFIED]` and the pipeline continues; only truly unexpected exceptions bubble up to set `state.status = "error"`.
- **Everything is cached and resumable**: every LLM call is hashed (`StateManager.generate_hash`) and cached in SQLite; `UPVSEngineState` checkpoints to `checkpoints/<session_id>.json` after every phase so a crashed run can resume from `state.status`.
- **3-tier confidence routing**: the number of drafts/retries per node scales with fact confidence (`>=0.85` → 1 draft/no retry, `>=0.70` → 2 drafts/1 retry, else → 3 drafts/up to `council_max_retries`). See `engine.py` phase 4 for the exact thresholds.

## Development notes

- **No test suite, no dependency manifest, no CI** currently exist in this repo. Dependencies observed in code: `pydantic`, `pyyaml` (`yaml`), `google-generativeai` (`google.generativeai`). If you add a dependency, add a `requirements.txt`.
- **LLM calls are placeholders in most layers**: `intent_router.route`, `planner.plan_dag`, `dag_reviewer.review_dag`, `researcher.evaluate_source_with_llm`, and `assembler.assemble_document`'s smoothing step all `raise NotImplementedError(...)` where a real Gemini call should go — only `generator.py` and `validator.py` (via `llm_utils.enforce_pydantic_schema`) are wired to the real API. When implementing a missing call, follow the existing pattern: build prompt → hash it → check `state_manager.get_llm_cache` → call Gemini → validate/parse → `state_manager.set_llm_cache`.
- **`GEMINI_API_KEY`** env var configures the Gemini client (falls back to a `"MOCK_API_KEY"` placeholder in `llm_utils.py`).
- Running `python engine.py` directly just prints an init message — it does not execute a full pipeline (would immediately hit a `NotImplementedError`).
- Commit messages follow a `V<major>.<minor> (V<internal-version>): <summary>` convention (see `git log`) — match this style for engine-level changes.
- Config changes belong in `config.yaml`, not hardcoded into modules — every layer reads its tunables from there via `load_config()`.
- Keep new prompts, docstrings, and inline comments in Hungarian to match the rest of the codebase, unless the user explicitly asks otherwise.
