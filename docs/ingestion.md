# 기업 수집·적재 실행 안내

> 대상: 대형 IT 기업 + Series A 이상 투자 이력이 확인된 기업. 기업 수 제한 없음.
> 현재: 기존 7개 법인 + 업스테이지·리벨리온 조직 정보, 투자 발표 근거 추가.

## 실행

```bash
# 저장소 루트에서 실행. 기존 DB·볼륨 유지
python3 scripts/migrate.py
python3 scripts/ingest_phase1.py --apply

# DB 변경 없는 수집·변환 확인
python3 scripts/ingest_phase1.py

# 특정 기업만 실행
python3 scripts/ingest_phase1.py --apply --target official_domain:upstage.ai

# 실패·중단 기업만 다시 실행. 최초 실행과 같은 config/year 사용
python3 scripts/ingest_phase1.py --apply --retry-run <run_id>
```

API 키: 루트 `.env`의 `OPENDART_API_KEY`. 원문: `data/raw/`, Git 제외.
성공 건수는 **처리한 기업 수**, 재무 처리 건수는 **검증·재처리한 레코드 수**. 신규 INSERT 건수와 다른 지표.

## 자동화와 실패 처리

```text
기업 설정 → 원문 수집 → 검증 → DB 적재 → 기업별 실행 결과
                               실패 → 다른 기업 계속 처리 → 실패 기업 재실행
```

- 대상 설정: `config/phase1_targets.json`. `enabled: false`로 제외, 기업 추가 시 코드 변경 불필요
- 일시적 네트워크·HTTP 500/502/503/504: 최대 3회 요청, 1·2초 대기
- HTTP 429: 자동 재시도 제외. DART 인증·호출 한도 응답: 해당 배치의 추가 수집 중단
- 중복 실행: 같은 로컬 저장소의 파일 잠금. 여러 호스트 간 분산 실행 미지원
- 오류 기업: 실패 단계·오류 유형 기록, 다른 기업 계속 처리. 기업·제품까지 저장한 후 재무 실패한 경우 부분 적재 가능
- 강제 종료: 실행 중 상태가 남을 수 있으며 다음 정상 실행에서 `interrupted`로 변경
- GitHub Actions: `.github/workflows/ingestion.yml`의 수동 실행 진입점 준비. `cp-ingestion` 전용 self-hosted runner와 `CP_INGESTION_DIR` 설정 필요
- **현재 정기 스케줄·runner 등록은 미설정.** 코드 변경은 로컬 상태이며 원격 workflow 실행도 아직 불가. 노트북이 꺼지면 로컬 수집 불가
- 공식 공시 원문 보조 추출: 설정한 접수번호만 처리. 새 공시 발견·기간 매핑 자동 갱신 미구현

## DB 구조 변경과 히스토리

| 테이블 | 저장 내용 | 확인 질문 |
|---|---|---|
| `schema_migration` | SQL 파일명·SHA256·적용 시각 | 어떤 DB 구조 변경을 적용했는가? |
| `ingestion_run` | 실행 ID·시작/종료·전체 상태·설정 해시 | 이번 실행은 성공했는가? |
| `ingestion_result` | 기업별 상태·원문 경로·실패 단계·오류 유형 | 어느 기업을 다시 수집해야 하는가? |
| `entity_history` | 기업·제품의 변경 전후 JSON | 값이 무엇에서 무엇으로 바뀌었는가? |
| `funding_evidence` | 투자 단계·발표일·공식 URL·원문 위치 | Series A 이상이라는 근거는 무엇인가? |

마이그레이션: `infra/postgres/migrations/` SQL을 순서대로 적용. 적용 기록과 SQL 변경을 같은 트랜잭션에 저장, 중복 적용 방지, 적용 후 SQL 변경 시 체크섬 오류. 새 변경은 새 번호 파일로 추가.

기존 002·003은 이미 수동 적용된 DB와 새 DB 모두에서 재실행 가능한 SQL. 첫 도입 때 다시 확인·적용한 시각으로 기록. 최초 초기화 SQL 001은 Docker 초기화 담당이며 마이그레이션 실행기는 기존 DB가 준비된 상태를 전제.

기업·제품 이력은 도입 시점의 `baseline`부터 보존. 그 이전 변경 내역 복원 불가. 수집 시각만 바뀌면 이력 추가 제외, 실제 필드 변경·삭제 시 기록. 재무는 기존 출처 개정별 저장 유지. 원문 파일은 DB 백업과 별도 백업 필요.

```sql
SELECT run_id, started_at, status FROM ingestion_run ORDER BY started_at DESC;
SELECT label, status, details->>'stage' AS failed_stage, error_type
FROM ingestion_result WHERE run_id = '<run_id>';
SELECT entity_table, operation, before_data, after_data
FROM entity_history ORDER BY history_id DESC LIMIT 20;
```

## Series A 이상 기업 확장

기업명을 검색해 임의로 투자 단계를 분류하지 않고 **공식 발표 확인 → 대상 설정 → 원문 저장 → 근거 적재** 순서.

| 신규 대상 | 확인한 투자 발표 | 저장 기준 |
|---|---|---|
| 업스테이지 | [2025-08-20 Series B bridge](https://www.upstage.ai/news/upstage-series-b-bridge) | 해당 날짜의 투자 사실, 최신 단계 단정 제외 |
| 리벨리온 | [2026-03-30 pre-IPO](https://rebellions.ai/newsroom/rebellions-closes-400-million-pre-ipo-and-launches-rebelrack-and-rebelpod-to-accelerate-global-expansion/) | 공식 발표의 단계·발표일 |

현재 신규 두 기업은 공식 도메인 식별자 + `organization`으로 등록. 법인명·DART 코드가 검증된 상태가 아니므로 법인·연결재무 추정 연결 제외. DART 미등록 기업이라는 뜻도 아님. 이후 DART 대조 시 기존 UUID에 식별자 추가 필요; 다른 기업처럼 중복 등록 금지.

투자 출처 선정과 발표일 확인은 사람이 검토한 설정값, 크롤러는 해당 페이지의 명칭·단계 문구와 해시를 검증. 모든 Series A 이상 기업을 발견하는 크롤러는 아직 미구현. 신규 기업 제품·재무는 별도 출처 확인 후 추가.

## 검증과 현재 제한

```bash
python3 -m unittest discover -s tests -v
docker compose -f infra/postgres/compose.yaml exec -T db \
  psql -X -U company_dev -d company_analysis < scripts/verify_ingestion_history.sql
```

기존 재무 21건: API·XBRL 9건 + 공시 XML 표 12건. 라인플러스는 2025-04-01~2026-03-31로 다른 기업의 달력연도와 구분. 표 추출기는 검토한 문서 구조용이며 임의 문서에 대한 범용 추출기 아님.

원문 불변 저장·재처리 가능성과 스케줄 운영은 별개. 현재는 수동 실행으로 실제 결과를 검증한 단계. 정기 운영 전 실행 주기·호스트·실패 알림 설정 필요.

참고: [PostgreSQL 트리거](https://www.postgresql.org/docs/18/plpgsql-trigger.html), [OpenDART 전체 재무제표](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020).
