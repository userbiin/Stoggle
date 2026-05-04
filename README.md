# Stoogle — 주식 종목 인사이트 플랫폼

최종 update : 2026-04-30

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
- Redis 기반 종목 레지스트리/현재가/주가 히스토리/뉴스 캐싱 구현
- Pearson 상관계수 기반 기업 관계 도출 및 D3 관계 그래프 구현
- 네이버 금융 뉴스 크롤링 및 간단 감성/카테고리 분류 구현
- CLAUDE API 기반 요약/영향 종목 추론 구현, API 키 없을 때 fallback 처리
- Redis 캐시 서비스와 Celery 자동화 태스크 골격 구현
- SQLAlchemy ORM 모델과 pgvector 뉴스 벡터 모델 정의 완료

---

## 추가 검토 사항

- 현재 frontend -> mock data 로 실행 중 (실데이터 API `REACT_APP_USE_MOCK=false` 호출 필요)
- 아직 Supabase 구현 X
- 에이전트 연결 필요 

---

## 빠른 실행

```bash
cd Stoogle/frontend
npm install
npm start
```

```bash
cd Stoogle/backend
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
