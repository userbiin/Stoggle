// 백엔드 없이 UI 개발용 목 데이터

export const mockSearchResults = [
  {
    ticker: "005930",
    name: "삼성전자",
    market: "KOSPI",
    sector: "전기·전자",
    price: 72400,
    change: 1.26,
  },
  {
    ticker: "005935",
    name: "삼성전자우",
    market: "KOSPI",
    sector: "전기·전자",
    price: 60800,
    change: 0.83,
  },
];

export const mockInsight = {
  ticker: "005930",
  name: "삼성전자",
  market: "KOSPI",
  sector: "전기·전자",
  price: 72400,
  change: 1.26,
  change_amount: 900,
  market_cap: 432_000_000_000_000,
  per: 14.2,
  pbr: 1.3,
  eps: 5099,
  summary:
    "삼성전자는 HBM3E 공급 확대 및 파운드리 수율 개선으로 2분기 실적 반등이 기대됩니다. AI 서버 수요 증가로 메모리 부문 수익성이 크게 개선될 전망이며, 파운드리는 GAA 2nm 공정 양산을 앞두고 있습니다.",
  keywords: [
    { text: "HBM", value: 80 },
    { text: "파운드리", value: 65 },
    { text: "AI 반도체", value: 60 },
    { text: "메모리", value: 55 },
    { text: "GAA", value: 48 },
    { text: "엔비디아", value: 45 },
    { text: "2nm", value: 40 },
    { text: "수율", value: 38 },
    { text: "DDR5", value: 32 },
    { text: "스마트폰", value: 28 },
    { text: "갤럭시", value: 25 },
    { text: "하이닉스", value: 22 },
  ],
  price_history: generatePriceHistory(72400, 60),
};

export const mockNews = [
  {
    id: 1,
    title: "삼성전자, HBM3E 엔비디아 공급 확정… 하반기 본격 납품",
    source: "한국경제",
    published_at: "2024-03-15T09:23:00",
    url: "#",
    sentiment: "positive",
    summary: "삼성전자가 엔비디아에 HBM3E 공급 계약을 확정하며 AI 반도체 경쟁에서 입지를 강화했다.",
    category: "실적",
  },
  {
    id: 2,
    title: "삼성전자 파운드리, 2nm GAA 수율 60% 돌파",
    source: "전자신문",
    published_at: "2024-03-14T14:10:00",
    url: "#",
    sentiment: "positive",
    summary: "삼성 파운드리 2nm GAA 수율이 업계 기대치를 상회하며 TSMC와 격차를 줄이고 있다.",
    category: "기술",
  },
  {
    id: 3,
    title: "반도체 업황 불확실성 지속… 삼성전자 목표주가 하향 조정",
    source: "매일경제",
    published_at: "2024-03-13T10:45:00",
    url: "#",
    sentiment: "negative",
    summary: "글로벌 IT 수요 둔화 우려로 일부 증권사가 삼성전자 목표주가를 하향 조정했다.",
    category: "분석",
  },
  {
    id: 4,
    title: "삼성전자, 1분기 영업이익 6조 전망… 전년 대비 흑자전환",
    source: "연합뉴스",
    published_at: "2024-03-12T08:30:00",
    url: "#",
    sentiment: "positive",
    summary: "증권가는 삼성전자 1분기 영업이익을 약 6조원으로 추정하며 메모리 부문 회복을 주목하고 있다.",
    category: "실적",
  },
  {
    id: 5,
    title: "미국 반도체 수출규제 강화… 삼성전자 중국 공장 영향 우려",
    source: "조선비즈",
    published_at: "2024-03-11T16:20:00",
    url: "#",
    sentiment: "negative",
    summary: "미 상무부의 추가 수출규제로 삼성전자 중국 시안 공장의 장비 업그레이드가 제한될 수 있다.",
    category: "이슈",
  },
];

export const mockRelations = {
  nodes: [
    { id: "005930", name: "삼성전자", group: 0, size: 40 },
    { id: "000660", name: "SK하이닉스", group: 1, size: 28 },
    { id: "035420", name: "NAVER", group: 2, size: 20 },
    { id: "051910", name: "LG화학", group: 2, size: 18 },
    { id: "207940", name: "삼성바이오로직스", group: 1, size: 16 },
    { id: "035720", name: "카카오", group: 2, size: 15 },
    { id: "066570", name: "LG전자", group: 1, size: 22 },
    { id: "003550", name: "LG", group: 1, size: 14 },
    { id: "005380", name: "현대차", group: 3, size: 12 },
    { id: "000270", name: "기아", group: 3, size: 11 },
  ],
  links: [
    { source: "005930", target: "000660", value: 0.82, type: "경쟁" },
    { source: "005930", target: "066570", value: 0.75, type: "계열" },
    { source: "005930", target: "003550", value: 0.70, type: "계열" },
    { source: "005930", target: "207940", value: 0.58, type: "계열" },
    { source: "005930", target: "035420", value: 0.45, type: "협력" },
    { source: "005930", target: "035720", value: 0.40, type: "협력" },
    { source: "005930", target: "051910", value: 0.38, type: "공급망" },
    { source: "005930", target: "005380", value: 0.32, type: "협력" },
    { source: "005930", target: "000270", value: 0.28, type: "협력" },
  ],
  related_companies: [
    { ticker: "000660", name: "SK하이닉스", correlation: 0.82, reason: "메모리 반도체 직접 경쟁사" },
    { ticker: "066570", name: "LG전자", correlation: 0.75, reason: "가전·디스플레이 사업 경쟁" },
    { ticker: "003550", name: "LG", correlation: 0.70, reason: "그룹사 연관 지수 동반 움직임" },
    { ticker: "207940", name: "삼성바이오로직스", correlation: 0.58, reason: "삼성 계열사 동반 흐름" },
    { ticker: "035420", name: "NAVER", correlation: 0.45, reason: "AI 인프라 협력 파트너" },
  ],
};

export const mockImpact = [
  { ticker: "005387", name: "현대차3우B", impact: "positive", reason: "AI칩 채택 확대 수혜" },
  { ticker: "009150", name: "삼성전기", impact: "positive", reason: "부품 공급망 수혜" },
  { ticker: "028260", name: "삼성물산", impact: "positive", reason: "그룹사 호재 동반 반영" },
  { ticker: "086520", name: "에코프로", impact: "negative", reason: "반도체 투자 확대 → 배터리 투자 축소 우려" },
  { ticker: "247540", name: "에코프로비엠", impact: "negative", reason: "IT·반도체 섹터 자금 이동" },
];

// 주가 히스토리 생성 헬퍼
function generatePriceHistory(currentPrice, days) {
  const history = [];
  const now = new Date();
  let price = currentPrice * 0.85;

  for (let i = days; i >= 0; i--) {
    const date = new Date(now);
    date.setDate(date.getDate() - i);
    const dateStr = date.toISOString().slice(0, 10);

    price = price * (1 + (Math.random() - 0.48) * 0.025);
    history.push({
      date: dateStr,
      close: Math.round(price / 100) * 100,
      volume: Math.floor(Math.random() * 20_000_000) + 5_000_000,
    });
  }
  return history;
}
