# CP — 기업 분석 서비스

IT 기업의 사업·재무·채용·기술 정보를 여러 출처에서 수집하고, 출처와 시점을 연결해 기업을 조사할 수 있도록 돕는 서비스.

주요 대상은 국내 IT 기업과 Series A 이상 투자 이력이 확인된 스타트업. 개발자의 기업 탐색과 지원 준비에 필요한 정보 제공이 목표.

## 해결하려는 문제

- 기업 홈페이지·공시·채용 공고·기술 블로그에 흩어진 정보
- 출처마다 다른 기업명과 식별자
- 현재 정보만으로 파악하기 어려운 사업·채용 변화
- 근거와 확인 시점이 불분명한 분석 결과

목표 기능: 기업별 정보 조회, 시점별 변화 확인, 원문 근거 연결, 확인된 사실과 AI 해석의 구분.

## 현재 단계 — Phase 1

| 구분 | 범위 |
|---|---|
| 이번 구현 | 기업·제품·재무 수집, 마이그레이션 적용 기록, 실행·변경 이력, 실패 기업 재실행 |
| 첫 적재 대상 | 기업 기본 정보·외부 식별자·제품·재무 수치 |
| 다음 작업 | Series A 이상 대상 확대, 신규 기업 법인 대조, 정기 실행 호스트 설정 |
| 후속 목표 | 채용·기술·사건·시장 데이터, 검색·관계 분석·AI 조사 |

NAVER·카카오·라인플러스·쿠팡·우아한형제들·당근마켓·비바리퍼블리카의 기업·DART 식별자·대표 제품을 적재. 재무는 전체 재무제표 API와 공시 원문 XML을 함께 사용해 7개 법인의 자산총계·영업수익·영업이익 총 21행을 적재. 사용자 화면·AI 분석 기능은 미구현. 실행법은 [첫 적재 기록](docs/ingestion.md) 참고.

업스테이지·리벨리온은 공식 투자 발표를 근거로 조직 정보와 투자 이력 추가. 총 기업·조직 9개이며, 기업별 수집 결과와 기업·제품 변경 전후 값 기록. 정기 스케줄은 아직 미설정.

## 시작하기

필수 환경: Docker Engine + Docker Compose. macOS·Windows에서는 Docker Desktop 활용 가능.

```bash
git clone https://github.com/cp-warehouse/cp-main.git
cd cp-main/infra/postgres
cp .env.example .env
# .env의 DB_PASSWORD에 로컬 개발용 비밀번호 입력
docker volume create cp-main-pg18-data
docker compose up -d --wait
docker compose exec db psql -U company_dev -d company_analysis -c '\dt'
```

처음 한 번 `.env` 준비 후 실행. 연결값·중지·백업·복구는 [DB 실행 안내](infra/postgres/README.md) 참고.

## 데이터 수집 사용법

수집기는 Python 3 표준 라이브러리와 Docker의 PostgreSQL CLI 사용. 현재 파일 잠금 방식은 macOS·Linux 대상이며 Windows에서는 WSL 등 Linux 환경 필요.

아래 명령은 **저장소 루트 `cp-main/`**에서 실행.

```bash
# 최초 한 번: 설정 예시 복사 후 발급받은 키 입력
# 기존 .env가 있으면 복사 생략
cp .env.example .env
# .env의 OPENDART_API_KEY 설정 — 실제 키는 Git 커밋 제외

# DB 준비와 구조 변경 적용 — 기존 볼륨·데이터 유지
docker compose -f infra/postgres/compose.yaml up -d --wait
python3 scripts/migrate.py

# 수집·검증만 실행 — 원문 파일 저장, DB 변경 없음
python3 scripts/ingest_phase1.py

# 전체 기업 수집·검증·DB 적재
python3 scripts/ingest_phase1.py --apply

# 특정 기업만 수집
python3 scripts/ingest_phase1.py --apply --target dart:00266961
python3 scripts/ingest_phase1.py --apply --target official_domain:upstage.ai

# 실패·중단 기업만 재실행: 출력 또는 ingestion_run에서 run_id 확인
python3 scripts/ingest_phase1.py --apply --retry-run <run_id>
```

재시도는 최초 실행과 동일한 설정 파일·사업연도 필요. 기본 재무 요청 연도는 `2025`; 다른 연도 사용 시 `--year` 지정 및 공시 원문 설정 재검토 필요.

대상 추가·제외는 [기업 설정](config/phase1_targets.json)에서 관리. DART 기업은 `corp_code`, 공식 홈페이지 기반 기업은 `official_company` 사용. `enabled: false`로 수집 제외. 투자 단계는 공식 발표일·출처와 함께 기록하며 최신 단계 추정 제외.

## 자동화 범위와 기록 확인

```text
명령 실행 → 기업 목록 → API·공식 페이지 → 원문 보관 → 검증·적재 → 실행 결과
```

**현재는 명령 한 번으로 실행하는 배치 자동화. 정기 스케줄은 미설정.** 새 기업 자동 발견과 새로운 공시의 추출 설정 자동 갱신은 후속 개발 범위.

| 실행 방식 | 준비 사항 | 현재 상태 |
|---|---|---|
| 로컬 CLI | Python·Docker·API 키 | 실제 실행 검증 완료 |
| macOS 정기 실행 | `launchd`에 CLI와 실행 시간 등록, Mac·Docker 실행 | 미등록 |
| GitHub Actions | `cp-ingestion` 라벨의 전용 self-hosted runner, 검토한 체크아웃 경로 `CP_INGESTION_DIR`, Docker·환경변수 준비 | 수동 실행 workflow 제공, runner·시간 설정 미등록 |
| 서버 정기 실행 | 서버 DB·수집기·스케줄러 배치 | 미구현 |

Actions의 [Collect company data](.github/workflows/ingestion.yml)는 전용 runner에 준비한 체크아웃을 실행하는 방식. 원격 코드를 자동 배포하거나 노트북을 자동으로 켜는 기능은 미포함. 노트북이 꺼져 있어도 수집하려면 상시 실행 서버 필요.

| 기록 | 테이블 | 저장 내용 |
|---|---|---|
| 구조 변경 | `schema_migration` | 적용 SQL·체크섬·시각 |
| 수집 실행 | `ingestion_run`, `ingestion_result` | 실행별·기업별 결과와 실패 단계 |
| 데이터 변경 | `entity_history` | 기업·제품 변경 전후 값, 동일 내용 중복 제외 |
| 투자 근거 | `funding_evidence` | 발표 단계·날짜·URL·원문 경로 |

```sql
-- DBeaver에서 실행
SELECT run_id, started_at, status FROM ingestion_run ORDER BY started_at DESC;
SELECT run_id, label, status, details->>'stage' AS failed_stage, error_type
FROM ingestion_result ORDER BY started_at DESC;
SELECT entity_table, operation, before_data, after_data
FROM entity_history ORDER BY history_id DESC LIMIT 20;
```

일시적 네트워크·서버 오류는 최대 3회 요청. 기업별 오류는 기록 후 다음 기업 처리, DART 인증·호출 한도 오류는 배치 중단. 일부 단계가 이미 저장된 기업은 재실행으로 나머지 단계 처리. 원문 파일과 DB 백업은 각각 보관 필요.

## 검증

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_postgres.py
docker compose -f infra/postgres/compose.yaml exec -T db \
  psql -X -U company_dev -d company_analysis < scripts/verify_ingestion_history.sql
```

`verify_postgres.py`는 임시 컨테이너·볼륨에서 초기 스키마·재생성·백업 복구 검증. 이력 검증 SQL은 적용된 DB에서 합성 데이터를 트랜잭션 안에서 확인한 뒤 롤백.

## 설계와 개발 기록

- [Phase 1 — 조사 과정과 설계 결정](docs/phase-1.md)
- [첫 데이터 적재 플랜 — 범위·매핑·중복 처리·검증](docs/ingestion.md)
- [실행 검증 기록](docs/verification.md)
- [커밋·PR·리뷰 규칙](CONTRIBUTING.md)

개발 흐름: 문제 정의 → 자료 조사 → 선택 근거 → 구현 → 검증 → 회고.
노션의 조사·토론을 바탕으로, 코드에 적용한 결정과 실제 결과를 GitHub에 기록.
