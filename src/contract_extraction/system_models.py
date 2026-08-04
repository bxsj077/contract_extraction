from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ProjectFiles:
    project_code: str
    folder: str
    forward_pdfs: list[str] = field(default_factory=list)
    backward_pdfs: list[str] = field(default_factory=list)
    extra_pdfs: list[str] = field(default_factory=list)
    status: str = "待处理"
    issues: list[str] = field(default_factory=list)

    @property
    def forward_pdf(self) -> str | None:
        return self.forward_pdfs[0] if self.forward_pdfs else None

    @property
    def backward_pdf(self) -> str | None:
        return self.backward_pdfs[0] if self.backward_pdfs else None


@dataclass(slots=True)
class EvidenceRef:
    evidence_id: str
    project_code: str
    direction: str
    field_name: str
    value: Any
    file_name: str
    page: int | str
    quote: str
    method: str = ""
    confidence: float | str = ""
    needs_review: bool = False


@dataclass(slots=True)
class EquipmentItem:
    category: str = "其他"
    standard_name: str = ""
    original_name: str = ""
    brand: str = ""
    model: str = ""
    unit: str = ""
    quantity: float | None = None
    technical_parameters: dict[str, Any] = field(default_factory=dict)
    direction: str = ""
    evidence_id: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class TimePlan:
    duration_value: int | None = None
    duration_unit: str = ""
    start_condition_type: str = "没有明确"
    start_condition_text: str = ""
    start_date: str | None = None
    finish_date: str | None = None
    completion_node: str = "项目完工"
    fixed_deadline: str | None = None
    milestones: dict[str, str] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(slots=True)
class ScopeItem:
    scope_item: str
    responsibility: str
    object: str
    scope_limit: str
    acceptance_requirement: str
    direction: str
    original_text: str
    evidence_id: str
    confidence: float


@dataclass(slots=True)
class ContractStructured:
    project_code: str
    direction: str
    contract_number: str = ""
    contract_name: str = ""
    party_a: str = ""
    party_b: str = ""
    amount_yuan: float | None = None
    sign_date: str | None = None
    effective_date: str | None = None
    contract_type: str = "无法确定"
    equipment: list[EquipmentItem] = field(default_factory=list)
    time_plan: TimePlan = field(default_factory=TimePlan)
    scopes: list[ScopeItem] = field(default_factory=list)
    key_clauses: dict[str, str] = field(default_factory=dict)
    evidence: list[EvidenceRef] = field(default_factory=list)
    parse_metadata: dict[str, Any] = field(default_factory=dict)
    review_issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Difference:
    category: str
    status: str
    risk_level: str
    rule_id: str
    title: str
    description: str
    forward: dict[str, Any] = field(default_factory=dict)
    backward: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    needs_review: bool = False


@dataclass(slots=True)
class ProjectReviewResult:
    project_code: str
    status: str
    risk_level: str
    forward: ContractStructured | None
    backward: ContractStructured | None
    equipment_differences: list[Difference] = field(default_factory=list)
    schedule_differences: list[Difference] = field(default_factory=list)
    scope_differences: list[Difference] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    review_issues: list[dict[str, Any]] = field(default_factory=list)
    rule_version: str = "2026.08-v1"
    processed_at: str = ""
    backward_contracts: list[ContractStructured] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
