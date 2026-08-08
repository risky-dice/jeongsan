"""검증 임계값 설정.

교육청 지침이 개정되면 **코드가 아니라 이 값만** 바꾼다.
YAML/JSON 파일로 오버라이드할 수 있다.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict

__all__ = ["Config", "DEFAULT", "load_config"]


@dataclass
class Config:
    # --- 계약방법 판정 (지방자치단체 계약 기준) ---
    #: 1인 견적 수의계약 한도
    single_quote_max: int = 20_000_000
    #: 2인 이상 견적제출 수의계약 한도 (초과 시 입찰)
    multi_quote_max: int = 100_000_000
    #: 여성기업·장애인기업·사회적경제기업 1인 견적 특례 한도
    special_single_max: int = 50_000_000

    # --- 낙찰하한율 (수의계약) ---
    lower_rate_under: float = 0.90   # 추정가격 2천만원 이하
    lower_rate_over: float = 0.88    # 추정가격 2천만원 초과
    lower_rate_books: float = 0.90   # 도서

    # --- 세율 ---
    vat_rate: float = 0.10
    #: 부가세 원 단위 처리: floor(절사, 관행) / half_up / ceil
    vat_rounding: str = "floor"

    # --- 이상 징후 ---
    #: 동일 업체 분할수의 의심 판정 기간(일)
    split_watch_days: int = 30

    # --- 통계목 판정 ---
    #: 이 단가를 넘으면 자산취득비 해당 여부 확인 경고
    asset_unit_price_threshold: int = 500_000

    # --- 허용 오차 ---
    #: 원 단위 절사 등으로 생기는 허용 차액
    tolerance: int = 1

    # --- 신뢰도 임계값 ---
    conf_auto: float = 0.95    # 이상이면 자동 확정
    conf_review: float = 0.70  # 미만이면 수기 입력 필수

    def method_for(self, estimated: int, special_entity: bool = False) -> Dict[str, Any]:
        """추정가격에 대해 적정 계약방법을 판정한다.

        Returns dict(method, min_quotes, lower_rate).
        min_quotes 가 None 이면 입찰 대상.
        """
        limit = self.special_single_max if special_entity else self.single_quote_max
        if estimated <= limit:
            return {
                "method": "수의계약(1인 견적)",
                "min_quotes": 1,
                "lower_rate": (self.lower_rate_under
                               if estimated <= self.single_quote_max
                               else self.lower_rate_over),
            }
        if estimated <= self.multi_quote_max:
            return {
                "method": "수의계약(2인 이상 견적)",
                "min_quotes": 2,
                "lower_rate": self.lower_rate_over,
            }
        return {"method": "입찰(일반/제한)", "min_quotes": None, "lower_rate": None}

    # -- 직렬화 -------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"알 수 없는 설정 항목: {sorted(unknown)}")
        return cls(**d)


DEFAULT = Config()


def load_config(path: str | None = None) -> Config:
    """YAML 또는 JSON 설정 파일을 읽는다. 경로가 없으면 기본값."""
    if not path:
        return Config()
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    data: Dict[str, Any]
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("YAML 설정을 쓰려면 pyyaml이 필요합니다.") from exc
        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw)
    return Config.from_dict(data)
