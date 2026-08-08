# jeongsan — 작업 지침

학교회계 목적사업비 정산 검증 엔진. 이 저장소에서 작업할 때 지켜야 할 것들.

## 절대 규칙

**1. 계산은 코드가 한다.**
룰 안에서 LLM 호출을 하지 않는다. 이 패키지에는 네트워크 코드가 한 줄도 없고,
앞으로도 없어야 한다. 추출(LLM)과 검증(코드)의 경계가 이 프로젝트의 전부다.

**2. `round()` 를 쓰지 않는다.**
파이썬 기본 반올림은 은행가 반올림이라 `round(100000.5) == 100000` 이다.
회계에서 기대하는 동작이 아니다. `normalize.round_won(x, mode)` 를 쓴다.
`half_up`(일반 계산) / `floor`(부가세 관행) / `ceil`.

**3. 임계값을 코드에 박지 않는다.**
계약 기준 금액, 하한율, 판정 기간은 전부 `config.Config` 에 있다.
교육청마다 다르고 개정된다. 새 룰을 쓸 때도 숫자는 Config로 뺀다.

**4. 추출기는 값을 지어내지 않는다.**
문서에 없으면 `None`. 공급가액이 안 보여도 품목을 대신 더하지 않는다.
이걸 어기면 정산서에 근거 없는 숫자가 들어간다.

## 구조

```
src/jeongsan/
  models.py      도메인 모델 (dataclass). 금액은 전부 int(원)
  config.py      임계값. 지침 개정 시 여기 + rules.yaml만 수정
  normalize.py   금액·날짜·사업자번호 정규화, round_won
  extract.py     정규식 추출 + LLM용 JSON Schema + dict→모델
  rules.py       R01~R16. @rule 데코레이터로 등록되는 순수 함수
  engine.py      validate() 진입점
  report.py      콘솔/HTML/xlsx/반납기안 산출물
  loader.py      사업 마스터 JSON·YAML 로더
  cli.py         argparse CLI

skills/jeongsan/     Claude Code 스킬 (플러그인으로도 설치 가능)
bin/jeongsan         pip 설치 없이 실행하는 래퍼
tests/               pytest. 픽스처는 tests/fixtures/
```

## 룰 추가하기

```python
@rule("R17", "검수조서 첨부 여부")
def r17_inspection(ctx: Context):
    if 조건_불충족:
        yield _f("R17", ERROR, "검수조서 누락", "2천만원 초과 계약은 검수조서가 필요합니다")
    else:
        yield _f("R17", OK, "검수조서 확인")
```

- `rules.py` 에 순수 함수로. Context를 받아 Finding을 yield한다.
- **테스트를 두 개 쓴다** — "잡아야 할 케이스"와 "통과시켜야 할 케이스".
  후자가 없으면 과잉 경고가 쌓이고, 그러면 아무도 리포트를 안 본다.
- 판단이 갈리는 룰은 `error` 가 아니라 `warning`. 자동화가 담당자를 막아서면
  사람들은 시스템을 우회한다.
- `skills/jeongsan/references/rules.md` 에 근거와 예외를 함께 적는다.

## 테스트

```bash
pip install -e ".[dev]"
pytest -q                    # 97 passed
```

룰을 고쳤으면 `tests/test_integration.py` 의 세 시나리오(정상 / 오류 다발 /
예산·기간 초과)가 그대로 통과하는지 반드시 확인한다. 이 세 개가 회귀 방지선이다.

## 하지 말 것

- 나이스·K-에듀파인 자동 연동 시도. 외부 쓰기 API가 없다.
- 실제 학교 문서를 저장소에 커밋. `.gitignore` 에 막아뒀지만 주의할 것.
- 산출물에 "정산 완료" 같은 표현. 전부 초안이고 확정은 사람이 한다.
- 의존성 추가. 코어는 표준 라이브러리로만 돌아야 한다.
  (openpyxl·pyyaml은 optional extras)
