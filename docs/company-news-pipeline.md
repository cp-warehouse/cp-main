# 기업 → 뉴스 수집 구조

DART와 뉴스 수집은 독립 실행. 실행 방법은 [독립 수집기 안내](collectors.md) 참고.

| 저장 위치 | 내용 |
|---|---|
| 기존 company | 기업 기본 정보·DART metadata·원문 위치·갱신 시각 |
| 기존 company_identifier | DART 코드와 내부 기업 ID 연결 |
| news_document | URL 기준 기사·본문·출처 |
| company_news | 기업과 기사 연결·검색어·근거 |
| 로컬 체크포인트 | 실행 상태·진행 위치·최근 시도 기록 |

추가 테이블은 기사·기업 연결 2개. 기업 상세 정보 전용 테이블과 실행·대상 관리 테이블 제거.
005 수정본은 기존 기업 상세 정보를 company로 옮긴 후 불필요한 테이블 제거. 기존 기사·연결·실행 UUID 보존. 이전 실행·대상 테이블의 기록은 변경 전 DB 백업에 보관.

```bash
python3 scripts/migrate.py
python3 scripts/collect_dart.py --batch-size 1000
python3 scripts/collect_news.py --company-limit 50 --articles-per-company 3 --request-budget 200
python3 scripts/collection_status.py
```

기존 company_news_pipeline.py는 독립 수집기 호출용 호환 진입점으로 유지. sync의 --enable-news는 무시하며 news에서 직접 대상을 선택. show는 보유 기업 조회.

## 결과 조회

```bash
docker compose -f infra/postgres/compose.yaml exec -T db \
  psql -X -U company_dev -d company_analysis < scripts/query_company_news.sql
```

```sql
SELECT display_name, metadata->>'induty_code' AS industry_code, metadata_collected_at
FROM company WHERE metadata_collected_at IS NOT NULL;
```

## 검증

```bash
python3 -m unittest discover -s tests -v
docker compose -f infra/postgres/compose.yaml exec -T db \
  psql -X -U company_dev -d company_analysis < scripts/verify_company_news.sql
```

문자열 일치 연결은 후보 상태로 관련성 미확정. 본문 정제·법인 판별은 후속 작업.

## 단순화 검증 결과

- 변경 전 DB 전체 백업: `data/processed/schema-backup/before-simplify005.dump`.
- 기업 메타데이터 1,002건 company 이전 확인. 기사 161건·기업 연결 163건 유지.
- 불필요한 3개 테이블 제거 확인.
- 신규 임시 DB 적용·005 반복 적용·중복 제약 검증 통과.
- 기존 DART 원문 재적재 시 company_id 유지, 기존 기사 재적재 시 document_id 유지.
