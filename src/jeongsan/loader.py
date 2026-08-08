"""사업 마스터(Project) 로더 — JSON / YAML."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from .models import BudgetLine, PriorSpend, Project
from .normalize import normalize_biz_no, parse_date, parse_money

__all__ = ["project_from_dict", "load_project"]


def project_from_dict(d: Dict[str, Any]) -> Project:
    lines = [BudgetLine(name=ln["name"], allocated=parse_money(ln["allocated"]) or 0)
             for ln in d.get("lines", [])]
    prior = []
    for p in d.get("prior", []):
        prior.append(PriorSpend(
            spend_date=parse_date(p.get("date") or p.get("spend_date")),
            vendor_name=p.get("vendor") or p.get("vendor_name") or "",
            biz_no=normalize_biz_no(p.get("biz_no")) or "",
            amount=parse_money(p.get("amount")) or 0,
            line=p.get("line") or "",
            method=p.get("method") or "",
        ))
    return Project(
        name=d.get("name", ""),
        grant=parse_money(d.get("grant")) or 0,
        start=parse_date(d.get("start")),
        end=parse_date(d.get("end")),
        lines=lines, prior=prior,
        purpose=d.get("purpose", ""),
        grant_no=d.get("grant_no"),
    )


def load_project(path: str) -> Project:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if path.endswith((".yaml", ".yml")):
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    return project_from_dict(data)
