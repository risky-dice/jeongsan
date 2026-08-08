"""검증 진입점."""
from __future__ import annotations

from typing import Iterable, Optional

from .config import Config
from .models import Draft, Project, Quote, Result, TaxInvoice
from .rules import Context, run_all

__all__ = ["validate", "Context"]


def validate(project: Project,
             draft: Optional[Draft] = None,
             quote: Optional[Quote] = None,
             tax: Optional[TaxInvoice] = None,
             *,
             line: Optional[str] = None,
             config: Optional[Config] = None,
             only: Optional[Iterable[str]] = None,
             skip: Optional[Iterable[str]] = None) -> Result:
    """집행 건 하나를 16개 룰로 검증한다.

    Parameters
    ----------
    project : 교부 사업 정보 (교부액·기간·세부항목·기집행 내역)
    draft / quote / tax : 기안 / 견적서 / 세금계산서. 최소 하나는 있어야 한다.
    line : 이번 건을 달 세부항목명. 생략하면 사업에 항목이 하나일 때만 자동 선택.
    """
    if draft is None and quote is None and tax is None:
        raise ValueError("기안·견적서·세금계산서 중 최소 하나는 필요합니다.")

    if line is None:
        line = project.lines[0].name if len(project.lines) == 1 else ""

    ctx = Context(project=project, draft=draft, quote=quote, tax=tax,
                  config=config or Config(), line_name=line)

    findings = run_all(ctx, only=only, skip=skip)

    this_amount = ctx.this_amount
    prior_total = project.prior_total
    total_spent = prior_total + this_amount

    return Result(
        findings=findings,
        this_amount=this_amount,
        prior_total=prior_total,
        total_spent=total_spent,
        remaining=project.grant - total_spent,
        line_name=line,
        line_allocated=project.line_allocated(line) if line else None,
        line_spent=(project.prior_line_total(line) + this_amount) if line else 0,
    )
