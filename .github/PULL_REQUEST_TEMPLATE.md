## 작업 내용

### 프로젝트 명칭 정리
- `Stoogle` 표기를 `Stoogle`로 전면 수정
- 프론트 문구, HTML 타이틀, 문서/컨텍스트 파일의 프로젝트명 정리

### Redis cache 연결
- Redis 기반 캐싱 계층 정리
- 캐시 키 구조:
  - `stoogle:registry` / `Stoogle:registry`: 종목 레지스트리
  - `stoogle:price:{ticker}` / `Stoogle:price:{ticker}`: 현재가
  - `stoogle:history:{ticker}` / `Stoogle:history:{ticker}`: 주가 히스토리
  - `stoogle:news:{ticker}` / `Stoogle:news:{ticker}`: 뉴스 목록
- Redis 미실행 시에도 API 실패 없이 외부 데이터 소스로 fallback 유지
- Redis Docker 실행 후 검색/뉴스/주가/히스토리 캐시 키 생성 확인

### KRX 주가 수집 및 검색 안정화
- `pykrx` 기반 종목 레지스트리, 현재가, 주가 히스토리 조회 로직 보강
- `pykrx` 응답이 list가 아니라 `DataFrame`/`Series`로 들어오는 경우에도 6자리 종목코드와 종목명을 안전하게 정규화
- KRX 조회 실패 시 주요 종목 fallback 레지스트리를 사용해 검색 API 500 방지
- `search?q=NAVER`, `search?q=카카오`, `search?q=삼성전자`, `search?q=SK하이닉스` 등 검색 API 정상 응답 확인

### 메인 주가 표시 ↔ 현재 주가 연동 리팩토링
- 기업 상세 화면의 헤더 주가와 차트 최신 종가가 서로 다른 값으로 표시되던 문제 수정
- `/api/v1/insight/{ticker}` 응답에서 헤더 가격은 `price_history`의 최신 거래일 종가를 우선 사용하도록 변경
- 등락 금액/등락률도 최신 히스토리 종가와 직전 종가 기준으로 재계산되도록 보정

### Celery scheduler 정리
- `fetch_top200_prices`: 장중 60초 주기로 KOSPI200 현재가 Redis 캐싱
- `update_price_history`: 장 마감 후 주가 히스토리 캐싱
- `crawl_all_news`: 1시간 주기로 뉴스 강제 크롤링 및 캐싱
- `prefetch_news_for_major_stocks`: beat schedule에 등록되어 있으나 함수가 없던 문제 해결, 주요 30개 종목 뉴스 사전 수집 태스크 추가
- KOSPI200 구성 종목 로딩 시 `DataFrame` truth-value 오류 방지

### 기업 관계 도출기 및 D3 시각화
- Pearson 상관계수 기반 기업 관계 도출 로직을 Redis 캐시가 적용된 주가 히스토리 기반으로 변경
- 데이터가 없을 때 임의 상관계수를 만들어내던 fallback 제거, 공통 거래일이 부족한 후보 제외
- D3 관계 그래프 범례에 `관심` 관계 타입 색상 추가

### feat/1, feat/2 branch 1차 병합
- `feat/2` 내용을 `feat/1`에 no-rebase 방식으로 1차 병합
- 병합된 주요 내용:
  - 뉴스 요약 에이전트 추가
  - 관련도 판별 에이전트 추가
  - pgvector 기반 중복 제거/색인 모듈 추가
  - `NewsVector` ORM 모델 및 pgvector 의존성 추가


## 관련 이슈

closes #

## 테스트 / 확인 사항

- [x] `python -m compileall Stoogle/backend`
- [x] Redis Docker 실행 후 `Stoogle:*` 캐시 키 생성 확인
- [x] `/api/v1/search?q=카카오` 200 OK
- [x] `/api/v1/search?q=SK하이닉스` 200 OK
- [x] `/api/v1/news/{ticker}` 200 OK
- [x] `/api/v1/relations/{ticker}` 200 OK
- [x] `/api/v1/insight/{ticker}` 200 OK
- [x] 프론트 기업 상세 화면에서 주가 헤더/차트 최신 종가 불일치 원인 확인 및 백엔드 응답 보정

## 체크리스트

- [x] 로컬에서 백엔드 API 동작 확인
- [x] Redis cache 연결 확인
- [x] 기존 검색/뉴스/관계/인사이트 API 500 오류 수정
- [ ] CLAUDE/EXAONE/DART API key 입력 후 LLM/DART 파이프라인 검증
- [ ] PostgreSQL/Supabase 영구 저장 플로우 E2E 검증

## 참고 사항

- Redis 미실행 시에도 API는 외부 데이터 직접 조회 또는 fallback으로 응답, 반복 요청 성능을 위해 Redis 실행 필요
- 현재 핵심 주가/관계 API는 Redis + pykrx 중심
- `summary_agent.py`, `relevance_agent.py`, `dedup_indexer.py`는 구현되어 있으나 기존 뉴스 API 파이프라인에는 미연결
- API key 미입력 상태에서는 LLM 요약/영향 종목/관련도 판별은 fallback 또는 빈 결과로 동작
