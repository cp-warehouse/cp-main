# Phase 1 — 첫 데이터 적재 플랜

> 목표: 기업 1개사의 실제 원문을 보관하고, 기업·제품·재무 데이터를 연결한 뒤 재실행해도 중복 없는 적재 검증.
> 현재: 네이버 기업·식별자·대표 제품·2025 연결 재무 첫 적재 및 동일 원문 재실행 검증 완료.

## 실행 기록 — 2026-10-02

| 확인 항목 | 실제 결과 |
|---|---|
| 기업 식별 | 고유번호 ZIP의 종목코드 `035420` → `NAVER`, DART `00266961` |
| 기업개황 | API 정상 응답, 법인명 `네이버(주)`, 표시명 `NAVER` |
| 첫 적재 | `company` 1행 + `company_identifier` 1행 |
| 같은 원문 재실행 | 두 테이블 추가·갱신 0행, 같은 기업 UUID 유지 |
| 변환 테스트 | 선택 필드 누락·앞자리 0·API 자료 없음·원문 해시·법인명 검증 4개 테스트 통과 |
| 2025 재무 | 사업보고서 접수번호 `20260313001021`, 연결 기준 3개 항목 적재 |
| 재무 기간 검증 | JSON 금액과 XBRL fact를 대조하여 연간 `2025-01-01~12-31`, 시점 `2025-12-31` 확인 |
| 재무 재실행·롤백 | 동일 원문 추가 0행, 잘못된 통화가 섞인 새 묶음 전체 롤백, 최종 3행 유지 |
| 대표 제품 | NAVER Corp 공식 검색 서비스 페이지 확인, `네이버 검색` 1행 적재 |
| 제품 재실행 | 같은 공식 URL 재실행 추가·갱신 0행, 같은 제품 UUID 유지 |
| 최종 행 수 | 기업 1·식별자 1·제품 1·재무 3 |
| 남은 범위 | 동시 실행 통합 테스트, 재시도·실행 로그, 다른 기업·기간 확장 |

실행 도구: `scripts/ingest_company.py`, `scripts/ingest_financial.py`, `scripts/ingest_product.py`. Python 표준 라이브러리와 컨테이너의 `psql` 활용. 계획의 psycopg 도입은 후속 적재기 확장 시 검토. 로컬 Docker 구성 전용 도구이며 원격 DB 접속 미지원.

```bash
# 최초 수집: 원문 경로와 기업 확인 결과 출력
python3 scripts/ingest_company.py discover --stock-code 035420
python3 scripts/ingest_company.py fetch --corp-code 00266961

# 위 fetch가 출력한 원문 경로 사용
python3 scripts/ingest_company.py replay <원문경로>
python3 scripts/ingest_company.py replay <원문경로> --apply

python3 -m unittest discover -s tests -v

# 2025 연결 재무 수집 후 출력된 두 원문 경로로 미리보기·적재
python3 scripts/ingest_financial.py fetch --corp-code 00266961 --year 2025
python3 scripts/ingest_financial.py replay <재무JSON경로> <XBRL경로>
python3 scripts/ingest_financial.py replay <재무JSON경로> <XBRL경로> --apply

# 기존 로컬 DB에 제품 중복 방지 인덱스 적용 후 공식 페이지 수집·적재
docker compose -f infra/postgres/compose.yaml exec -T db \
  psql -X -U company_dev -d company_analysis \
  < infra/postgres/migrations/002_product_website_unique.sql
python3 scripts/ingest_product.py fetch \
  --corp-code 00266961 --name '네이버 검색' \
  --website-url 'https://www.naver.com/' \
  --source-url 'https://www.navercorp.com/service/search'
python3 scripts/ingest_product.py replay <HTML원문경로> --apply
```

수집 원문은 `data/raw/dart/`에 Git 제외 상태로 보관. 수집 시점은 메타데이터에서 재사용. 동일·오래된 시점의 기업 원문은 현재 값 갱신 제외. 홈페이지는 API가 제공한 `www.navercorp.com` 그대로 보존하며 HTTP/HTTPS 추정 추가 없음. 기업의 과거 값은 원문에서 확인 가능하지만 DB의 변경 이력 테이블은 미구현.

재무 첫 적재 결과:

| 계정 | 금액 | 기간·기준 |
|---|---:|---|
| 영업수익 | 12,035,007,218,975 KRW | 2025-01-01~12-31, 연간·연결 |
| 영업이익 | 2,208,138,388,720 KRW | 2025-01-01~12-31, 연간·연결 |
| 자산총계 | 41,084,496,327,318 KRW | 2025-12-31, 시점·연결 |

금액은 [DART 전체 재무제표 API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020), 기간은 동일 접수번호의 [XBRL 원문](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019)에서 교차 확인. 세 계정은 이름 검색이 아니라 재무제표 구분 + XBRL 계정 ID로 선택. 첫 적재는 당기 3개 지표만 포함하며 전체 239개 응답 행을 모두 저장하지 않음.

제품은 [NAVER Corp 검색 서비스 페이지](https://www.navercorp.com/service/search)의 `네이버 검색` 설명과 `www.naver.com` 링크를 같은 HTML 원문에서 확인. `네이버페이`처럼 별도 법인이 명시된 서비스는 첫 대상에서 제외. `(company_id, website_url)` 부분 고유 인덱스로 동일 운영 기업·공식 URL의 중복 방지. 현재 모델은 운영 이력·공동 운영·URL 변경 이력 미지원.

DBeaver 확인 SQL:

```sql
SELECT c.legal_name, c.display_name, c.website_url,
       i.namespace, i.external_value, c.collected_at
FROM company c
JOIN company_identifier i USING (company_id)
WHERE i.namespace = 'dart' AND i.external_value = '00266961';

SELECT account_name, value, currency, period_start, period_end, period_basis
FROM financial_observation
ORDER BY account_name;
```

## 1. 무엇부터 적재할 것인가

첫 대상은 **네이버 1개사 제안**. 정확한 법인·DART 고유번호 확인 후 확정. 첫 성공 이후 카카오 등 3개사로 확대. 스타트업의 DART 수록·재무 제공 여부는 별도 확인 대상이며, 데이터 부재를 기업 부실로 해석하지 않는 원칙.

| 순서 | 출처·형식 | 저장 대상 | 첫 범위 |
|---|---|---|---|
| ① 기업 식별 | [DART 고유번호](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018), ZIP 내부 XML | `company_identifier` | 선택 기업의 `corp_code`, 앞자리 0 보존 |
| ② 기업 정보 | [DART 기업개황](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002), JSON | `company` | 법인명·표시명·홈페이지 |
| ③ 재무 | [DART 전체 재무제표](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020), JSON | `financial_observation` | 2025 사업보고서·연결 기준, 당기 매출·영업이익·자산총계 후보 |
| ④ 제품 | 선정 기업의 공식 제품 페이지, HTML | `product` | 운영 법인 확인 가능한 대표 제품 1개 |

- **재무 범위:** 실제 제공 여부·계정명·계정 ID 확인 후 매핑 확정. 연결 자료가 없으면 자동으로 별도 자료와 혼합하지 않고 미제공 기록.
- **제품 범위:** 공식 URL·운영 법인·제품명 확인 후 출처를 포함한 검토용 입력 파일 작성. 첫 단계는 HTML 원문 보관 + 수동 확인, 사이트 전반 자동 크롤링은 후속 작업.
- **첫 완료 단위:** ①~③의 수집→적재 검증 후 ④ 추가. 테이블마다 별도 PR을 만들지 않고 동일 기능 PR에서 진행.

## 2. 어떻게 저장할 것인가

```text
공식 API·제품 페이지
        ↓ 수집
원문 파일 + 수집 메타데이터
        ↓ 변환·검증          ← 저장한 원문으로 재실행
테이블별 적재 후보
        ↓ 트랜잭션
Docker PostgreSQL → DBeaver에서 원문과 저장 값 대조
```

| 테이블 | 필드 매핑·저장 규칙 | 다시 실행했을 때 |
|---|---|---|
| `company` | `corp_name` → `legal_name`, `stock_name` 우선·없으면 법인명 → `display_name`, `hm_url` → `website_url`, 법인 → `entity_kind=legal_entity` | DART 식별자로 기존 UUID 조회, 확인된 최신 값 갱신. 미제공 값으로 기존 값 삭제 금지 |
| `company_identifier` | `namespace=dart`, `external_value=corp_code`, 기업 UUID 연결 | `(namespace, external_value)` 기준 기존 기업 재사용. 회사명만으로 병합 금지 |
| `financial_observation` | 계정·금액·기간·통화·연결/별도·출처·원문 경로 저장 | 같은 원문·추출 버전은 건너뛰기, 변경 원문은 새 개정으로 추가 |
| `product` | 검토한 제품명·공식 URL·운영 기업 UUID·근거 URL 저장 | 운영 기업 + 정규화한 공식 제품 URL 기준 중복 방지. 이름만으로 판별 금지 |

**원문과 출처**

- 보관 위치: `data/raw/<source>/<request-hash>/<content-sha256>/` 아래 원문과 메타데이터. 기존 파일 덮어쓰기 금지.
- 메타데이터: 인증키를 뺀 요청 조건·수집 시점·응답 상태·원문 해시·추출 버전. 재시도 기록은 별도 실행 로그에 추가.
- `raw_uri`: 프로젝트 기준 상대 경로. DB 백업과 별도로 원문 디렉터리 백업 필요.
- `collected_at`: 원문을 실제 수집한 시점. 재파싱 시 현재 시간으로 변경 금지.
- API 키가 쿼리에 포함된 요청 URL·예외 메시지의 그대로 저장 금지. 로깅 시 인증정보 제거.

<details>
<summary><strong>재무 데이터 — 적재 전 확정할 매핑</strong></summary>

- 금액: `thstrm_amount`를 Decimal로 변환, `float` 사용 제외. 빈 금액은 NULL, 숫자 0은 0으로 구분. 파싱 불가 값은 오류 처리.
- 범위: 최초에는 연간 당기 값만 대상. 전기·전전기·분기 누적 값은 후속 확장.
- 계정: `account_id` 우선 매핑, 비표준 계정은 검토 후 별도 매핑. 동일 이름의 여러 후보가 있으면 임의 선택 금지.
- 기간: 자산총계는 시점 값(`instant`), 매출·영업이익은 기간 값(`annual`). `instant`의 시작일은 종료일과 동일하게 저장하는 내부 규칙.
- **현재 API 가이드에 정확한 시작·종료 날짜 필드가 없으므로 사업보고서로 확인한 기간을 근거 URL과 함께 대상 설정에 기록. 연도만 보고 1월 1일~12월 31일로 추정 금지.**
- 통화·단위: 응답 통화와 보고서 표시 단위를 대조한 뒤 통화 기본 단위로 변환. 화면의 ‘백만원’ 값을 API 값에 중복 적용하지 않도록 확인.
- `source_key`: 출처·API·기업 코드·연도·보고서 코드·연결/별도 조건을 정규화한 요청 식별자.
- `source_revision`: 접수번호 + 원문 해시. 같은 접수번호의 응답 변경도 별도 보존. 동일 원문 재실행 시 동일 값 유지.
- `source_record_key`: 재무제표 구분·계정 ID·계정명·상세·금액 필드의 조합. 충돌 시 실패 처리 후 매핑 검토, 배열 순서를 영구 식별자로 사용하지 않는 원칙.
- 정정·추출 버전이 여러 개면 단순 합산 금지. 첫 조회는 검증한 원문 개정과 추출 버전을 명시적으로 선택. ‘최신 유효 값’ 자동 선택은 후속 규칙.

</details>

## 3. 구현 순서와 완료 기준

| 작업 | 구현 내용 | 통과 기준 |
|---|---|---|
| A. 수집 확인 | 인증키 설정, 기업 ID 확인, 원문과 메타데이터 저장 | 실제 응답 필드·보고서 기간·제품 운영 법인 확인 |
| B. 변환 | 기업·재무·제품별 변환, DB에 쓰지 않는 미리보기 | 원문 값과 적재 후보 대조, 누락·오류 분리 |
| C. 적재 | 기업+식별자 원자적 저장, 재무 요청 단위 저장, 제품 중복 방지 | 실행 도중 실패하면 해당 단위 전체 롤백 |
| D. 재실행 검증 | 동일 원문 두 번 처리, 변경 원문·잘못된 값 처리 | 동일 입력의 행 수·UUID 유지, 변경 재무 이력 보존 |
| E. 공유 | 실행법·검증 SQL·실제 결과 기록, 동료 로컬 재현 | 별도 로컬 볼륨에서 같은 절차 성공 |

**현재 스키마 보완 결과와 계획**

- 제품: `(company_id, website_url)` 부분 고유 인덱스 마이그레이션 적용. URL 없는 제품은 첫 자동 적재에서 제외. URL 변경·공동 운영은 수동 검토.
- 기업: 기업 생성과 식별자 등록을 한 트랜잭션으로 처리. 동시 등록 충돌 시 전체 롤백 후 기존 식별자 재조회로 고아 기업 방지.
- 기존 `001_initial.sql`만 수정하거나 볼륨을 초기화하는 방식 제외. 새 마이그레이션에 적용·검증·복구 절차 포함.
- 원문 파일 쓰기 완료 후 DB 트랜잭션 시작. DB 실패 시 원문 유지 후 재실행. 파일 저장과 DB 저장이 단일 트랜잭션이 아니라는 한계 명시.

**실패 처리**

- HTTP 성공 여부와 DART `status`를 별도로 검사. `013`은 자료 없음, 인증 오류는 중단, 요청 한도 초과는 추가 호출 중단.
- 일시적 통신 오류·서버 오류만 제한된 재시도 적용. 최초 순차 수집, 병렬·대량 요청 제외.
- 실행 결과에 수집·삽입·갱신·건너뜀·실패 건수와 원문 위치 기록. 실패를 0건 성공으로 처리하지 않는 원칙.
- 통합 테스트는 실제 로컬 데이터와 분리한 임시 DB 사용. 테스트 fixture는 직접 만든 합성 데이터로 관리.

## 4. Git·협업·학습 기록

작업 브랜치: `feat/phase1-data-ingestion` → 목적별 커밋 → **기능 PR 1개** → 동료 리뷰 → main 병합.

구현 기본안: Python + HTTP 클라이언트 + psycopg, 버전 고정. DB는 현재 Docker Compose 재사용, 수집기는 우선 로컬 CLI 실행. ORM·스케줄러·별도 수집 컨테이너는 실제 필요 확인 후 추가.

```text
src/ingestion/         # sources · transforms · loaders · CLI
config/               # 대상 기업·기간 근거·계정 매핑, 비밀값 제외
tests/                # 합성 fixture·변환·재실행·롤백 검증
infra/postgres/migrations/  # 기존 DB 변경
data/raw/             # 수집 원문, Git 제외
docs/ingestion.md     # 계획 → 실제 결과 순차 기록
```

- 예정 커밋: `feat(ingestion): 원문 수집 및 보관` → `feat(ingestion): 변환과 중복 방지 적재` → `test(ingestion): 재실행·롤백 검증` → `docs(ingestion): 실제 적재 결과 기록`.
- Git 공유 대상: 코드·의존성·설정 예시·합성 테스트 데이터·실행법. API 키·실제 원문·DB 백업은 제외.
- 협업: 같은 레포의 코드로 각자 로컬 DB에 적재. 개발자별 localhost DB의 자동 공유·동기화 없음.
- 블로그 주제: **“기업 데이터를 INSERT하기 전에 해결한 세 가지 — 기업 식별·중복·재무 정정”**. 원문 예시 → 선택 이유 → 재실행 실험 → 한계 순서, 실제 측정 이후 결과 추가.

**다음 행동:** DBeaver에서 4개 테이블 결과 확인 → 동료 환경 재현 → 첫 적재 PR 리뷰. 이후 카카오 등 두 번째 상장 IT 기업으로 일반화 범위 확인. 루트 `.env`의 `OPENDART_API_KEY` 설정 및 실제 인증 완료. 키 값은 대화·커밋에 공유하지 않는 방식.

위 실행 기록 외 항목은 계획이며, 실제 수집·테스트 이후 결과 추가.
