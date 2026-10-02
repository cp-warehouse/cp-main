# CP — 기업 분석 서비스

IT 기업의 사업·재무·채용·기술 정보를 여러 출처에서 수집하고, 출처와 시점을 연결해 기업을 조사할 수 있도록 돕는 서비스.

주요 대상은 국내 IT 기업과 Series A~C 단계의 스타트업. 개발자의 기업 탐색과 지원 준비에 필요한 정보 제공이 목표.

## 해결하려는 문제

- 기업 홈페이지·공시·채용 공고·기술 블로그에 흩어진 정보
- 출처마다 다른 기업명과 식별자
- 현재 정보만으로 파악하기 어려운 사업·채용 변화
- 근거와 확인 시점이 불분명한 분석 결과

목표 기능: 기업별 정보 조회, 시점별 변화 확인, 원문 근거 연결, 확인된 사실과 AI 해석의 구분.

## 현재 단계 — Phase 1

| 구분 | 범위 |
|---|---|
| 이번 구현 | Docker PostgreSQL, 초기 4개 테이블, 보존·복구 검증, DART 기업 수집·재실행 적재 |
| 첫 적재 대상 | 기업 기본 정보·외부 식별자·제품·재무 수치 |
| 다음 작업 | 재무·제품 수집과 매핑, 정정 처리, 롤백·동시 실행 검증 |
| 후속 목표 | 채용·기술·사건·시장 데이터, 검색·관계 분석·AI 조사 |

네이버 기업·DART 식별자 각 1행 적재 및 동일 원문 재실행 검증 완료. 재무·제품 수집기·사용자 화면·AI 분석 기능은 미구현. 실행법은 [첫 적재 기록](docs/ingestion.md) 참고.

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

## 설계와 개발 기록

- [Phase 1 — 조사 과정과 설계 결정](docs/phase-1.md)
- [첫 데이터 적재 플랜 — 범위·매핑·중복 처리·검증](docs/ingestion.md)
- [실행 검증 기록](docs/verification.md)
- [커밋·PR·리뷰 규칙](CONTRIBUTING.md)

개발 흐름: 문제 정의 → 자료 조사 → 선택 근거 → 구현 → 검증 → 회고.
노션의 조사·토론을 바탕으로, 코드에 적용한 결정과 실제 결과를 GitHub에 기록.
