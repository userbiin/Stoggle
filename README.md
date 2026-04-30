# stoggle — 주식 종목 인사이트 플랫폼

> 어떤 기업을 검색해도 동일한 품질의 주가·뉴스·관계도 인사이트를 제공하는 "주식 전용 구글"

상세 문서는 [stoggle/README.md](./stoggle/README.md)를 기준으로 관리합니다.

검토 기준일: 2026-04-30

---

## 현재 진행 상황

- React 18 기반 프론트엔드 3개 화면 구현 완료
  - 검색 홈 `/`
  - 검색 결과 `/search?q={query}`
  - 기업 상세 `/company/:ticker`
- 기업 상세 화면 구성 완료
  - 주가 차트, 키워드 워드 클라우드, 뉴스 목록, D3 관계 그래프, 연관 기업 목록, 영향 종목
- FastAPI 백엔드 API 구현 완료
  - `/api/v1/search`
  - `/api/v1/insight/{ticker}`
  - `/api/v1/news/{ticker}`
  - `/api/v1/relations/{ticker}`
  - `/health`
- pykrx 기반 종목 검색/주가/시총 조회 서비스 구현
- 네이버 금융 뉴스 크롤링 및 간단 감성/카테고리 분류 구현
- OpenAI API 기반 요약/영향 종목 추론 구현, API 키 없을 때 fallback 처리
- Redis 캐시 서비스와 Celery 자동화 태스크 골격 구현
- SQLAlchemy ORM 모델 정의 완료

---

## 주요 주의점

- 프론트엔드는 기본적으로 mock 데이터를 사용합니다. 실데이터 API 호출은 `REACT_APP_USE_MOCK=false`로 실행해야 합니다.
- Supabase는 현재 SDK/Auth/Storage가 아니라 `DATABASE_URL`의 PostgreSQL 후보로만 잡혀 있습니다.
- 로컬 DB는 루트 `docker-compose.yml`로 PostgreSQL 16 + pgvector를 실행할 수 있습니다.
- SQLAlchemy 모델은 정의되어 있지만 라우터/서비스의 영구 저장 로직에는 아직 연결되어 있지 않습니다.
- Celery beat schedule에 등록된 `tasks.prefetch_news_for_major_stocks`는 현재 함수 정의가 없습니다.
- Supabase/PostgreSQL을 실제로 사용하려면 PostgreSQL 드라이버 의존성 추가가 필요합니다.

---

## 빠른 실행

```bash
cd stoggle/frontend
npm install
npm start
```

```bash
cd stoggle/backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python models/db_models.py
uvicorn main:app --reload --port 8000
```

```bash
# 선택: 로컬 PostgreSQL 16 + pgvector
docker-compose up -d
```

더 자세한 실행 방법, API 명세, 환경변수, 검토 결과, 남은 작업은 [stoggle/README.md](./stoggle/README.md)에 정리되어 있습니다.
