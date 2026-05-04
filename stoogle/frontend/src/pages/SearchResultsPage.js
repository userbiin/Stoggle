import React, { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { mockSearchResults } from '../utils/mockData';
import TopBar from '../components/TopBar';

const USE_MOCK = process.env.REACT_APP_USE_MOCK !== 'false';

const styles = {
  page: { minHeight: '100vh', background: 'var(--color-bg)' },
  body: { maxWidth: '720px', margin: '0 auto', padding: '24px 20px' },
  heading: { fontSize: '14px', color: 'var(--color-text-secondary)', marginBottom: '16px' },
  list: { display: 'flex', flexDirection: 'column', gap: '12px' },
  item: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '16px 20px', background: 'var(--color-surface)',
    border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
    cursor: 'pointer', transition: 'box-shadow var(--transition)',
  },
  itemLeft: { display: 'flex', flexDirection: 'column', gap: '4px' },
  itemName: { fontSize: '17px', fontWeight: '600', color: 'var(--color-text-primary)' },
  itemMeta: { fontSize: '13px', color: 'var(--color-text-secondary)' },
  itemRight: { textAlign: 'right' },
  price: { fontSize: '18px', fontWeight: '700', color: 'var(--color-text-primary)' },
  change: { fontSize: '13px', fontWeight: '600' },
  positive: { color: 'var(--color-negative)' },
  negative: { color: 'var(--color-accent)' },
  noResult: { padding: '60px 0', textAlign: 'center', color: 'var(--color-text-secondary)' },
  spinner: { padding: '60px 0', textAlign: 'center', color: 'var(--color-text-muted)' },
};

export default function SearchResultsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const query = searchParams.get('q') || '';
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!query) return;
    setLoading(true);

    if (USE_MOCK) {
      setTimeout(() => { setResults(mockSearchResults); setLoading(false); }, 300);
      return;
    }

    axios.get(`/api/v1/search?q=${encodeURIComponent(query)}`)
      .then((res) => setResults(res.data.results || []))
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [query]);

  const fmtPrice = (p) => p?.toLocaleString('ko-KR') + '원';
  const fmtChange = (c) => (c >= 0 ? '+' : '') + c?.toFixed(2) + '%';

  return (
    <div style={styles.page}>
      <TopBar initialQuery={query} />
      <div style={styles.body}>
        {loading ? (
          <div style={styles.spinner}>검색 중...</div>
        ) : results.length === 0 ? (
          <div style={styles.noResult}>
            <p style={{ fontSize: '16px', marginBottom: '8px' }}>"{query}"에 대한 검색 결과가 없습니다.</p>
            <p style={{ fontSize: '14px' }}>종목명 또는 6자리 종목코드로 검색해 보세요.</p>
          </div>
        ) : (
          <>
            <p style={styles.heading}>"{query}" 검색 결과 {results.length}건</p>
            <div style={styles.list}>
              {results.map((r) => (
                <div
                  key={r.ticker}
                  style={styles.item}
                  onClick={() => navigate(`/company/${r.ticker}`)}
                  onMouseEnter={(e) => e.currentTarget.style.boxShadow = 'var(--shadow-md)'}
                  onMouseLeave={(e) => e.currentTarget.style.boxShadow = 'none'}
                >
                  <div style={styles.itemLeft}>
                    <span style={styles.itemName}>{r.name}</span>
                    <span style={styles.itemMeta}>{r.ticker} · {r.market} · {r.sector}</span>
                  </div>
                  <div style={styles.itemRight}>
                    <div style={styles.price}>{fmtPrice(r.price)}</div>
                    <div style={{ ...styles.change, ...(r.change >= 0 ? styles.positive : styles.negative) }}>
                      {fmtChange(r.change)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
