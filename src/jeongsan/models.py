"""학교회계 목적사업비 정산 — 도메인 모델.

모든 금액은 정수(원). 소수·부동소수점 금액은 쓰지 않는다.
모든 날짜는 datetime.date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

__all__ = [
    "Item", "Vendor", "Quote", "Draft", "TaxInvoice",
    "PriorSpend", "BudgetLine", "Project", "Finding", "Result",
    "ERROR", "WARNING", "INFO", "OK", "SEVERITY_ORDER",
]

ERROR = "error"
WARNING = "warning"
INFO = "info"
OK = "ok"

SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2, OK: 3}


@dataclass
class Item:
    """견적서·거래명세서의 품목 한 줄."""
    name: str
    spec: str = ""
    unit: str = ""
    qty: float = 0
    unit_price: int = 0
    amount: int = 0
    confidence: float = 1.0

    @property
    def computed(self) -> int:
        """수량 × 단가 (검산용). 0.5는 올림 — 은행가 반올림을 쓰지 않는다."""
        from .normalize import round_won
        return round_won(self.qty * self.unit_price, "half_up")


@dataclass
class Vendor:
    name: Optional[str] = None
    biz_no: Optional[str] = None
    ceo: Optional[str] = None
    contact: Optional[str] = None
    #: 여성기업·장애인기업·사회적경제기업 여부 (수의계약 특례 판정)
    special_entity: bool = False


@dataclass
class Quote:
    """견적서."""
    vendor: Vendor = field(default_factory=Vendor)
    quote_date: Optional[date] = None
    valid_days: Optional[int] = None
    items: List[Item] = field(default_factory=list)
    supply: Optional[int] = None          # 공급가액
    vat: Optional[int] = None             # 부가세
    total: Optional[int] = None           # 합계금액
    tax_free: bool = False                # 면세
    confidence: Dict[str, float] = field(default_factory=dict)
    source: Optional[str] = None          # 원본 파일 경로 등

    @property
    def items_sum(self) -> int:
        return sum(i.amount for i in self.items)


@dataclass
class Draft:
    """기안(품의서)."""
    doc_no: Optional[str] = None
    title: Optional[str] = None
    project: Optional[str] = None
    budget_code: Optional[str] = None     # 예산과목 (통계목 포함 문자열)
    method: Optional[str] = None          # 기재된 계약방법
    quote_count: Optional[int] = None     # 기재된 견적 인원수
    drafter: Optional[str] = None
    draft_date: Optional[date] = None
    amount: Optional[int] = None          # 소요예산 (부가세 포함)
    estimated: Optional[int] = None       # 추정가격 (부가세 제외)
    due_date: Optional[date] = None       # 납품기한
    vendor: Vendor = field(default_factory=Vendor)
    confidence: Dict[str, float] = field(default_factory=dict)
    source: Optional[str] = None


@dataclass
class TaxInvoice:
    """세금계산서 / 거래명세서."""
    approval_no: Optional[str] = None
    write_date: Optional[date] = None
    vendor: Vendor = field(default_factory=Vendor)
    supply: Optional[int] = None
    vat: Optional[int] = None
    total: Optional[int] = None
    confidence: Dict[str, float] = field(default_factory=dict)
    source: Optional[str] = None


@dataclass
class PriorSpend:
    """같은 사업의 기집행 내역 (예산 잔액·분할수의 판정용)."""
    spend_date: Optional[date]
    vendor_name: str = ""
    biz_no: str = ""
    amount: int = 0
    line: str = ""            # 세부항목명
    method: str = ""


@dataclass
class BudgetLine:
    name: str
    allocated: int


@dataclass
class Project:
    """교부받은 목적사업 한 건."""
    name: str
    grant: int                                     # 교부액
    start: Optional[date] = None                   # 집행 시작
    end: Optional[date] = None                     # 집행 종료
    lines: List[BudgetLine] = field(default_factory=list)
    prior: List[PriorSpend] = field(default_factory=list)
    purpose: str = ""                              # 사업계획서 목적 (R16 참고용)
    grant_no: Optional[str] = None

    def line_allocated(self, name: str) -> Optional[int]:
        for ln in self.lines:
            if ln.name == name:
                return ln.allocated
        return None

    @property
    def prior_total(self) -> int:
        return sum(p.amount for p in self.prior)

    def prior_line_total(self, name: str) -> int:
        return sum(p.amount for p in self.prior if p.line == name)


@dataclass
class Finding:
    """검증 룰 하나의 판정 결과."""
    code: str
    severity: str
    title: str
    detail: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.severity == ERROR

    def __str__(self) -> str:  # pragma: no cover - 표시용
        mark = {ERROR: "✕", WARNING: "!", INFO: "i", OK: "✓"}[self.severity]
        s = f"{mark} {self.code} {self.title}"
        return s + (f"\n      {self.detail}" if self.detail else "")


@dataclass
class Result:
    """검증 전체 결과."""
    findings: List[Finding] = field(default_factory=list)
    this_amount: int = 0          # 이번 집행 건 금액
    prior_total: int = 0
    total_spent: int = 0          # 기집행 + 이번 건
    remaining: int = 0            # 교부 잔액 (반납 대상)
    line_name: str = ""
    line_allocated: Optional[int] = None
    line_spent: int = 0

    # -- 편의 접근자 -------------------------------------------------
    def by_severity(self, sev: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == sev]

    @property
    def errors(self) -> List[Finding]:
        return self.by_severity(ERROR)

    @property
    def warnings(self) -> List[Finding]:
        return self.by_severity(WARNING)

    @property
    def ok(self) -> bool:
        """확정 가능 여부 — error가 하나도 없어야 True."""
        return not self.errors

    @property
    def sorted_findings(self) -> List[Finding]:
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.code))

    def summary(self) -> Dict[str, int]:
        return {
            "error": len(self.by_severity(ERROR)),
            "warning": len(self.by_severity(WARNING)),
            "info": len(self.by_severity(INFO)),
            "ok": len(self.by_severity(OK)),
        }
