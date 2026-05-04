import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { mockInsight, mockNews, mockRelations, mockImpact } from '../utils/mockData';
import TopBar from '../components/TopBar';
import PriceChart from '../components/PriceChart';
import WordCloudSection from '../components/WordCloudSection';
import NewsSection from '../components/NewsSection';
import RelationGraph from '../components/RelationGraph';
import RelationList from '../components/RelationList';
import ImpactList from '../components/ImpactList';

const USE_MOCK = process.env.REACT_APP_USE_MOCK !== 'false';

const styles = {
  page: { minHeight: '100vh', background: 'var(--color-bg-secondary)' },
  body: { maxWidth: '1100px', margin: '0 auto', padding: '24px 20px', display: 'flex', flexDirection: 'column', gap: '20px' },
  header: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '24px 28px',
    boxShadow: 'var(--shadow-sm)',
  },
  headerTop: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' },
  companyName: { fontSize: '26px', fontWeight: '700', color: 'var(--color-text-primary)' },
  tickerMeta: { fontSize: '13px', color: 'var(--color-text-secondary)', marginTop: '4px' },
  priceBlock: { textAlign: 'right' },
  price: { fontSize: '30px', fontWeight: '700' },
  changeRow: { display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' },
  stats: { display: 'flex', gap: '24px', marginTop: '20px', flexWrap: 'wrap', paddingTop: '20px', borderTop: '1px solid var(--color-border)' },
  stat: { display: 'flex', flexDirection: 'column', gap: '2px' },
  statLabel: { fontSize: '12px', color: 'var(--color-text-muted)' },
  statValue: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  summary: {
    background: 'var(--color-accent-light)', border: '1px solid #c5d8f8',
    borderRadius: 'var(--radius-md)', padding: '16px 20px', marginTop: '16px',
    fontSize: '14px', lineHeight: '1.7', color: '#1a3c6e',
  },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' },
  spinner: { padding: '80px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: '15px' },
};

const positive = { color: 'var(--color-negative)' };
const negative = { color: 'var(--color-accent)' };

function fmt(n, suffix = '') {
  if (n == null) return '-';
  if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(1) + '조' + suffix;
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(0) + '억' + suffix;
  return n.toLocaleString('ko-KR') + suffix;
}

export default function CompanyDetailPage() {
  const { ticker } = useParams();
  const [insight, setInsight] = useState(null);
  const [news, setNews] = useState([]);
  const [relations, setRelations] = useState(null);
  const [impact, setImpact] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    if (USE_MOCK) {
      setTimeout(() => {
        setInsight(mockInsight);
        setNews(mockNews);
        setRelations(mockRelations);
        setImpact(mockImpact);
        setLoading(false);
      }, 400);
      return;
    }

    Promise.all([
      axios.get(`/api/v1/insight/${ticker}`),
      axios.get(`/api/v1/news/${ticker}`),
      axios.get(`/api/v1/relations/${ticker}`),
    ]).then(([i, n, r]) => {
      setInsight(i.data);
      setNews(n.data.news || []);
      setRelations(r.data);
      setImpact(r.data.impact || []);
    }).catch(console.error).finally(() => setLoading(false));
  }, [ticker]);

  if (loading) return <><TopBar /><div style={styles.spinner}>데이터 불러오는 중...</div></>;
  if (!insight) return <><TopBar /><div style={styles.spinner}>데이터를 찾을 수 없습니다.</div></>;

  const isUp = insight.change >= 0;

  return (
    <div style={styles.page}>
      <TopBar />
      <div style={styles.body}>
        {/* 헤더 */}
        <div style={styles.header}>
          <div style={styles.headerTop}>
            <div>
              <div style={styles.companyName}>{insight.name}</div>
              <div style={styles.tickerMeta}>{insight.ticker} · {insight.market} · {insight.sector}</div>
            </div>
            <div style={styles.priceBlock}>
              <div style={{ ...styles.price, ...(isUp ? positive : negative) }}>
                {insight.price?.toLocaleString('ko-KR')}원
              </div>
              <div style={styles.changeRow}>
                <span className={`badge ${isUp ? 'badge-negative' : 'badge-positive'}`}>
                  {isUp ? '▲' : '▼'} {Math.abs(insight.change_amount)?.toLocaleString('ko-KR')}
                </span>
                <span style={{ fontSize: '14px', ...(isUp ? positive : negative), fontWeight: '600' }}>
                  {isUp ? '+' : ''}{insight.change?.toFixed(2)}%
                </span>
              </div>
            </div>
          </div>

          <div style={styles.stats}>
            {[
              { label: '시가총액', value: fmt(insight.market_cap) },
              { label: 'PER', value: insight.per != null ? insight.per + '배' : '-' },
              { label: 'PBR', value: insight.pbr != null ? insight.pbr + '배' : '-' },
              { label: 'EPS', value: insight.eps != null ? insight.eps.toLocaleString('ko-KR') + '원' : '-' },
            ].map(({ label, value }) => (
              <div key={label} style={styles.stat}>
                <span style={styles.statLabel}>{label}</span>
                <span style={styles.statValue}>{value}</span>
              </div>
            ))}
          </div>

          {insight.summary && <div style={styles.summary}>{insight.summary}</div>}
        </div>

        {/* 주가 차트 */}
        <PriceChart history={insight.price_history} name={insight.name} />

        {/* 키워드 + 뉴스 */}
        <div style={styles.grid2}>
          <WordCloudSection keywords={insight.keywords} />
          <NewsSection news={news} />
        </div>

        {/* 관계 그래프 + 관계 목록 */}
        <div style={styles.grid2}>
          <RelationGraph data={relations} centerId={ticker} />
          <RelationList companies={relations?.related_companies || []} />
        </div>

        {/* 영향 종목 */}
        <ImpactList items={impact} />
      </div>
    </div>
  );
}
