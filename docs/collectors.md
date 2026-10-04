# 독립 DART / 다음 뉴스 수집기

실행 코드는 `scripts/collect_dart.py`, `scripts/collect_news.py`, 공통 코드는 `scripts/collection_common.py`, 상태 조회는 `scripts/collection_status.py`에 위치. 테스트는 `tests/test_collectors.py`에 위치. 기존 수집기·Compose·마이그레이션·설정은 유지.
추가 테이블은 `news_document`, `company_news` 2개. DART 상세 정보는 `company.metadata`·`metadata_raw_uri`·`metadata_collected_at`에 저장.

- DART: `company`, `company_identifier` 생성/갱신. 뉴스 호출 없음.
- 뉴스: 기존 company를 읽고 `news_document`, `company_news` 사용. 기업 생성·DART 호출 없음.
- 배치 상태: `data/processed/collectors/batches/<배치ID>.json`에 원자적으로 저장. Git 제외 경로.
- 실행 환경: 기존 PostgreSQL 컨테이너 + 로컬 Python 3. 별도 패키지 설치 불필요.

모든 명령은 저장소 루트에서 실행. 기존 DB 컨테이너가 실행 필요. DART만 루트 `.env`의 `OPENDART_API_KEY`를 사용. 구조 단순화 최초 적용 시 `python3 scripts/migrate.py` 실행 필요.

## 1. DART 기업 정보 1,000개씩 저장

```bash
python3 scripts/collect_dart.py --batch-size 1000
```

DART 전체 코드 목록을 다운로드해 로컬에 캐시하지만, DB에는 선택한 최대 1,000개 기업개황만 저장.
목록 캐시는 24시간 재사용. 기본적으로 `company.metadata_collected_at`이 있는 DART 기업은 제외.
미완료 배치에 배정된 기업도 다음 새 배치에서 제외. 같은 명령을 다시 실행하면 다음 미수집 기업을 선택.
목록은 코드순으로 처리. 폐업·과거 기업이나 자료 없는 기업 포함 가능. 실패 건은 체크포인트에 기록.

상장기업으로 한정하려면:

```bash
python3 scripts/collect_dart.py --batch-size 1000 --listed-only
```

특정 기업을 등록하거나 기존 정보를 갱신하려면:

```bash
python3 scripts/collect_dart.py --batch-size 2 \
  --corp-code 00126380 --corp-code 00258801
```

명시한 코드는 기존 메타데이터 유무와 관계없이 갱신 대상.
기업·식별자·개황을 한 트랜잭션에 저장하고 기존 수집기와 같은 DART 식별자 잠금을 사용.
이름 기준 병합 제외. 같은 DART 코드는 같은 company_id 재사용.
도메인으로만 등록된 조직과 DART 법인의 자동 병합은 미지원.

기본 간격은 DART API 작업 사이 최소 1초, 50회마다 추가 5~10초 휴식.
일시적 오류는 기존 DART HTTP 모듈이 최대 3회 재시도하므로 50회는 논리 API 호출 기준.
인증/호출 한도 오류는 전체 배치를 중단. 일부 기업 오류는 기록하고 다음 기업을 처리.
뉴스 수집 자동 활성화 없음.

## 2. 보유 기업에서 뉴스 별도 수집

먼저 회사 ID와 보유 기업을 확인.

```bash
python3 scripts/collection_status.py --companies 20
```

DB에 보유한 기업 중 최근 뉴스 수집 시도가 오래된 50개에서, 기업별 최대 3건:

```bash
python3 scripts/collect_news.py \
  --company-limit 50 --articles-per-company 3 \
  --request-budget 200 \
  --pause-every 50 --pause-min 5 --pause-max 10
```

- DART 수집 시 활성화 플래그 설정 불필요. 뉴스 명령에서 지정 범위 선택.
- 특정 기업만 수집할 경우 회사 ID 또는 DART 코드 명시 필요.
- 기본 요청 간격 최소 2초, 뉴스 요청 50회마다 5~10초 추가 휴식.
- 동시에 한 요청만 처리. 뉴스 자체 재시도는 없고 403/429면 중단.
- 검색 요청·본문 요청 모두 예산에 포함. 리디렉션 시 내부 추가 통신 발생 가능.
- `--request-budget`은 실행별 상한. 일일 한도와 별개이며 재실행 시 새 예산 적용.
- 기존 기사 URL은 본문 재다운로드 없이 재사용. 같은 기업-기사 연결도 중복 생성 없음.
- 검색은 기본 1페이지. 기사 후보 확대 시 `--search-pages 3` 등 지정.
- 회사별 기사 수는 상한. 결과 부족·실패·요청 예산에 따라 실제 수집 건수 감소 가능.

특정 회사만 수집:

```bash
python3 scripts/collect_news.py --corp-code 00126380 \
  --company-limit 1 --articles-per-company 3 --request-budget 4

# --company-id에는 status에서 확인한 UUID 사용; 여러 번 지정 가능
python3 scripts/collect_news.py --company-id '<company UUID>' \
  --company-limit 1 --articles-per-company 3
```

`--corp-code`는 DB 조회 조건으로만 사용. DART 호출 없음. DB에 없는 기업은 오류로 처리.
기업별 기사 연결은 문자열 일치 후보. 제보 안내·관련 기사·계열사 언급에 의한 오탐 존재. 확정 기업 정보와 구분 필요.

## 3. 중단/실패한 작업 재개

배치 ID는 실행 시작·종료 시 출력. 최근 30개 체크포인트 조회:

```bash
python3 scripts/collection_status.py
```

```bash
python3 scripts/collect_dart.py --resume '<DART 배치ID>'
python3 scripts/collect_news.py --resume '<뉴스 배치ID>' --request-budget 100
```

완료 기업 제외, 실패·중단·미처리 기업만 처리.
뉴스는 저장된 검색 URL·완료 기사 목록 사용. 검색 직후 요청 예산이 소진돼도 검색 재호출 없이 재개.
재개 시 대상/기사 수/검색 페이지는 최초 배치 설정을 사용. 간격·휴식·요청 예산만 이번 실행 인자로 조절.
체크포인트는 미완료 작업의 기준으로 보존 필요. DB 저장 직후 중단돼도 다음 실행의 URL/기업 중복 방지로 재처리 가능.
DART·뉴스별 잠금으로 동일 수집기 중복 실행 방지. 두 수집기는 독립 실행 가능.
기존 scripts 수집기와 파일 잠금은 미공유. 여러 뉴스 수집기의 동시 실행 주의 필요.

## 검증 명령

```bash
python3 -m unittest discover -s tests -v
```

기존 원문·로컬 결과는 경로와 내용 유지. 뉴스 신규 원문은 `data/raw/collectors/news/`, DART는 기존 `data/raw/dart/`에 보관.
체크포인트·원문·잠금 파일의 기존 `data/.../collectors/` 경로 유지. 이전 배치 ID로 재개 가능.

## 이번 검증 결과

- 기존 테스트 33개 + 신규 테스트 7개 통과.
- 1,100개 합성 목록에서 1,000개 선택 및 완료/예약 기업을 제외한 다음 배치 선택 검증.
- 실제 DART 기업 2개를 기존 테이블에 저장. 각 DART 식별자 1개 유지 확인.
- 실제 뉴스 검색 요청 1회 후 예산으로 중단 → 동일 체크포인트로 재개 → 검색 재호출 없이 본문 1회로 완료.
- DB에 별도 collector 스키마가 없음을 조회로 확인.
- 해당 검증에서 실제 1,000개 기업 전체 수집은 미실행.

## 구조 단순화

수정 005에서 기존 기업 상세 정보는 company로 이전. 기사·연결 데이터 유지. 실행 상태와 수집 순서는 로컬 체크포인트로 관리. 이전 체크섬이 일치하는 005만 자동 재적용하며 다른 마이그레이션의 체크섬 검증은 유지.
