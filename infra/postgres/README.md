# PostgreSQL · 실행과 데이터 보존

목적: 팀원마다 같은 DB 실행 설정 사용, 컨테이너 재생성 후 데이터 유지, 별도 DB에 백업 복구.

## 구성 이해

| 구성 | 역할 | 이번 설정 |
|---|---|---|
| 이미지 | DB 프로그램과 실행 환경 패키지 | 공식 PostgreSQL 18 Bookworm 이미지 |
| 컨테이너 | 이미지로 실행한 DB 환경 | Compose의 db 서비스 |
| Compose | 이미지·포트·저장소를 함께 정의한 실행 설정 | compose.yaml |
| Named volume | 컨테이너와 수명이 분리된 저장소 | cp-main-pg18-data |
| Bind mount | 내 폴더를 컨테이너에 연결 | init 폴더의 SQL을 읽기 전용 연결 |
| 백업 | 삭제·손상 후 복구할 별도 복사본 | pg_dump 파일 + 수집 원문 |

```text
로컬 수집 코드 / DB 클라이언트
       │ 127.0.0.1:55432
       ▼
PostgreSQL 컨테이너 :5432
       ├─ /var/lib/postgresql → Named volume → 실제 DB 데이터
       └─ /docker-entrypoint-initdb.d ← ./init → 최초 생성 SQL
```

macOS의 Named volume은 Docker Desktop Linux VM 내부 보관. 일반 프로젝트 폴더와 다른 위치. 이미지 재사용으로 충분한 범위이므로 별도 Dockerfile 불필요.

## 최초 실행

준비: Docker 엔진 실행, Docker Compose 설치. 저장소 루트에서 시작.

```bash
cd infra/postgres
cp .env.example .env
# 편집기에서 .env의 DB_PASSWORD 입력 — 이미 .env가 있으면 복사 생략
docker volume create cp-main-pg18-data
docker compose config --quiet
docker compose up -d --wait
docker compose ps
docker compose exec db psql -U company_dev -d company_analysis -c '\dt'
```

성공 기준: db healthy, public 스키마에 company·company_identifier·product·financial_observation 4개 테이블.
DB_VOLUME_NAME 변경 시 해당 이름의 볼륨도 별도 생성. 새 이름의 빈 볼륨을 연결하면 기존 데이터가 보이지 않으므로 데이터 위치 확인 필요.

Docker Desktop: Containers → cp-main-local → db → Logs에서 기동 로그, Volumes에서 cp-main-pg18-data 확인.

| DB 클라이언트 연결 | 값 |
|---|---|
| 종류 / Host / Port | PostgreSQL / 127.0.0.1 / 55432 |
| Database / User | company_analysis / company_dev |
| Password | .env의 DB_PASSWORD |

향후 같은 Compose 네트워크의 앱 컨테이너에서는 `db:5432`로 연결. 현재 계정은 로컬 개발용 관리자이며 실제 서비스용 최소 권한 계정은 후속 구성.

## 데이터 보존과 초기화

| 작업 | 동작 |
|---|---|
| `docker compose stop` → `start` | 같은 컨테이너 중지·시작, 데이터 유지 |
| `docker compose down` → `up -d --wait` | 컨테이너 재생성, 같은 볼륨 재연결로 데이터 유지 |
| Docker Desktop 종료·재부팅 | 저장 데이터 유지, 엔진 시작 후 DB 상태 확인 |
| 볼륨 직접 삭제·Docker 데이터 초기화 | 데이터 손실 가능, 별도 백업 필요 |

외부 볼륨은 Compose의 `down -v` 삭제 대상에서도 제외. Docker의 직접 볼륨 삭제나 정리 명령까지 차단하는 기능은 아님. 일상 종료에는 stop 또는 down 사용.

`restart: unless-stopped`는 엔진 실행 중 컨테이너 재시작 정책. 직접 중지한 컨테이너나 Docker Desktop 자체의 실행 기능은 아님.

초기 SQL은 **빈 데이터 디렉터리의 첫 실행에서만 적용**. 이후 SQL 파일 수정·컨테이너 재시작만으로 기존 스키마가 바뀌지 않으므로 후속 변경은 마이그레이션 필요. 기존 DB 비밀번호도 .env 변경만으로 바뀌지 않는 구조.

기존 로컬 DB의 마이그레이션은 중복 여부를 먼저 확인한 뒤 개별 SQL 적용.

```bash
docker compose exec -T db psql -X -U company_dev -d company_analysis \
  -c "SELECT company_id,website_url,count(*) FROM product WHERE website_url IS NOT NULL GROUP BY company_id,website_url HAVING count(*)>1"
docker compose exec -T db psql -X -U company_dev -d company_analysis \
  < migrations/002_product_website_unique.sql
```

볼륨 보존과 데이터 변경 이력은 별개. 기업·제품은 현재 값만 저장. 재무 정정은 새로운 source_revision 행으로 보존. 테이블별 의미와 한계는 [설계 기록](../../docs/phase-1.md) 참고.

## 백업·복구

현재 폴더: infra/postgres. 백업 명령이 성공한 경우에만 최종 .dump 파일 생성.

```bash
mkdir -p backups
backup_path="backups/company_analysis_$(date +%Y%m%d_%H%M%S).dump"
if docker compose exec -T db pg_dump -U company_dev -d company_analysis -Fc > "$backup_path.partial"; then
  mv "$backup_path.partial" "$backup_path"
  echo "Backup saved: $backup_path"
else
  echo 'Backup failed: .partial 파일은 복구용 백업으로 사용 금지' >&2
fi
```

이어서 정상 백업을 새 DB에 복구하는 예시. 생성·복구 단계가 실패하면 다음 명령 진행 중단. 같은 이름의 DB가 있다면 새 이름 사용.

```bash
docker compose exec db createdb -U company_dev company_analysis_restore_check
docker compose exec -T db pg_restore -U company_dev -d company_analysis_restore_check --exit-on-error < "$backup_path"
docker compose exec db psql -U company_dev -d company_analysis_restore_check -c '\dt'
```

테이블 목록뿐 아니라 행 수와 대표 값까지 비교해야 복구 확인 완료. 수집 원문 `data/raw/`는 pg_dump에 포함되지 않으므로 별도 백업. 중요한 백업은 다른 디스크·저장소에도 보관.

## 자동 검증

저장소 루트에서 실행. 추가 Python 패키지 없이 Python 3.10 이상과 실행 중인 Docker 필요.

```bash
python3 scripts/verify_postgres.py
```

고유 테스트 프로젝트·볼륨·임의 로컬 포트 생성 → 4개 테이블과 제약조건 확인 → 중지·재생성 후 데이터 비교 → 백업·별도 DB 복구 → 전체 테스트 행 비교.
실제 수집 데이터를 사용하지 않는 합성 테스트. 종료 시 해당 실행에서 생성한 테스트 컨테이너·볼륨만 정리. 테스트용 비밀번호는 실행 중 무작위 생성, 파일 저장·로그 출력 없음.

검증 결과와 실행 환경은 [검증 기록](../../docs/verification.md) 참고.

## 공식 근거와 버전

- [Named volume과 데이터 보존](https://docs.docker.com/engine/storage/volumes/)
- [Bind mount](https://docs.docker.com/engine/storage/bind-mounts/)
- [Compose 외부 볼륨](https://docs.docker.com/reference/compose-file/volumes/#external)
- [PostgreSQL 공식 이미지 — 초기화와 18 이상 볼륨 경로](https://github.com/docker-library/docs/blob/master/postgres/README.md)
- [PostgreSQL 백업·복구](https://www.postgresql.org/docs/18/backup-dump.html)

PostgreSQL 18 이미지의 볼륨 경로는 `/var/lib/postgresql`. Compose에는 검증한 이미지 digest 고정. 이미지 업데이트 시 digest 갱신과 재검증 필요. 다른 메이저 버전으로 변경 시 단순 태그 교체가 아닌 업그레이드 절차 검토 필요.

healthcheck는 TCP 연결 사용. 최초 초기화용 임시 서버가 아닌 실제 DB 서버의 준비 상태 확인 목적.
