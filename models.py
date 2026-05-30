from pydantic import BaseModel, Field
from typing import Literal, List, Dict, Optional, Any
from enum import Enum

# --- 0. ZÁRT TAXONÓMIÁK (Enum) ---

TASK_TYPES = Literal[
    "academic_essay",
    "analytical_report",
    "business_document",
    "journalistic_article",
    "creative_writing",
    "code_generation",
    "translation_rewrite",
    "brainstorm_ideation",
    "summarization",
]

RESEARCH_DEPTH = Literal["deep", "standard", "shallow", "none"]
GROUNDING_LEVEL = Literal["strict", "moderate", "none"]
ASSEMBLY_FREEDOM = Literal["strict", "moderate", "flexible"]
DAG_COMPLEXITY = Literal["micro", "small", "medium", "large"]
TARGET_AUDIENCE = Literal["academic", "professional", "general", "casual"]

# --- 1. INTENT & CONTEXT ---

class EvaluationRubric(BaseModel):
    name: str
    criteria: Dict[str, str]

class CouncilThresholds(BaseModel):
    """Ágens-szintű vétóküszöbök a Delphi Tanácshoz."""
    domain_expert_veto: int = Field(default=60, ge=0, le=100)
    debiaser_veto: int = Field(default=40, ge=0, le=100)
    grounding_verifier_veto: int = Field(default=90, ge=0, le=100)
    max_variance: int = Field(default=15, ge=0, le=50)

class TaskConstraints(BaseModel):
    """Strukturált kinyerés a felhasználó kéréséből."""
    language: str = "hu"
    length_pages: Optional[int] = None
    length_words: Optional[int] = None
    format: Optional[str] = None
    deadline_priority: Literal["quality_first", "balanced", "speed_first"] = "quality_first"
    citation_style: str = "APA"

class TaskContext(BaseModel):
    user_prompt: str
    task_type: TASK_TYPES
    research_depth: RESEARCH_DEPTH = "deep"
    council_thresholds: CouncilThresholds = Field(default_factory=CouncilThresholds)
    grounding_level: GROUNDING_LEVEL = "strict"
    assembly_freedom: ASSEMBLY_FREEDOM = "strict"
    dag_complexity: DAG_COMPLEXITY = "medium"
    target_audience: TARGET_AUDIENCE = "professional"
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    is_rejectable: bool = False
    reject_reason: Optional[str] = None
    rubric: Optional[EvaluationRubric] = None
    few_shot_examples: List[str] = Field(default_factory=list, max_length=3)
    reasoning: str = ""

# --- 2. DAG & PLANNER ---

class ArgumentNode(BaseModel):
    id: str
    section_title: str
    claim: str
    premises: List[str] = Field(default_factory=list)
    node_type: Literal["axiom", "derivation", "synthesis", "conclusion"]
    research_queries: List[str] = Field(default_factory=list)
    grounding_status: Literal["grounded", "partial", "ungrounded"] = "ungrounded"

class ArgumentEdge(BaseModel):
    source: str
    target: str
    relation: Literal["supports", "contrasts", "extends", "synthesizes"]

class ArgumentGraph(BaseModel):
    title: str
    nodes: List[ArgumentNode]
    edges: List[ArgumentEdge]

# --- 3. RESEARCH & FACTS ---

class SourceRecord(BaseModel):
    source_id: str
    title: str
    authors: Optional[str] = None
    author_id: Optional[str] = None
    institution: Optional[str] = None
    email_domain: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    source_type: Literal["journal", "preprint", "book", "web", "other"]
    quality: int = Field(ge=1, le=100, default=50)

class FactRecord(BaseModel):
    fact_id: str
    source_ids: List[str] = Field(default_factory=list)
    claim_text: str
    context: Optional[str] = None
    confidence: float = 0.0

# --- 4. COUNCIL & EVALUATION ---

class GroundingResult(BaseModel):
    total_claims: int
    grounded_claims: int
    misattributed_claims: List[str]
    ungrounded_claims: List[str]
    grounding_ratio: float
    verdict: Literal["pass", "weak", "fail"]

class EdgeAuditResult(BaseModel):
    edge_source: str
    edge_target: str
    entailment_score: int
    detected_fallacies: List[str]
    relation_correct: bool
    reasoning: str
    verdict: Literal["pass", "weak", "fail"]

class SectionEvaluation(BaseModel):
    agent_name: str
    thought_process: str
    scores: Dict[str, int]
    veto_raised: bool = False
    conclusion: str

class DelphiDraftResult(BaseModel):
    draft_id: str
    draft_text: str
    branch_type: Literal["conservative", "critical", "synthetic"]
    evaluations: List[SectionEvaluation]
    average_score: float
    is_vetoed: bool

# --- 5. TOKEN TRACKING & COSTS ---

class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    
    def add(self, prompt: int, completion: int):
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += (prompt + completion)

# --- 6. STATE MANAGEMENT ---

class UPVSEngineState(BaseModel):
    session_id: str
    status: Literal[
        "init", "routing", "planning", "reviewing_dag",
        "researching", "generating", "council_voting",
        "auditing_logic", "assembling", "completed", "error"
    ]
    task_context: Optional[TaskContext] = None
    argument_graph: Optional[ArgumentGraph] = None
    fact_store_db_path: str = "upvs_cache.db"

    completed_nodes: List[str] = Field(default_factory=list)
    section_drafts: Dict[str, List[DelphiDraftResult]] = Field(default_factory=dict)
    final_sections: Dict[str, str] = Field(default_factory=dict)

    audit_trail: List[Dict[str, Any]] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
