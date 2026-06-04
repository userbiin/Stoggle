import React from 'react';
import { useNavigate } from 'react-router-dom';

const s = {
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)',
    padding: '20px 24px',
    boxShadow: 'var(--shadow-sm)',
  },
  header: {
    fontSize: '15px', fontWeight: '600',
    color: 'var(--color-text-primary)',
    marginBottom: '4px',
  },
  sub: {
    fontSize: '12px', color: 'var(--color-text-muted)',
    marginBottom: '20px',
  },
  group: {
    marginBottom: '20px',
  },
  newsRow: {
    display: 'flex', alignItems: 'flex-start', gap: '8px',
    background: '#f8f9fa',
    border: '1px solid var(--color-border)',
    borderRadius: '8px',
    padding: '10px 14px',
    marginBottom: '10px',
  },
  newsIcon: {
    fontSize: '13px', flexShrink: 0, marginTop: '1px',
  },
  newsTitle: {
    fontSize: '13px', fontWeight: '500',
    color: 'var(--color-text-primary)', lineHeight: '1.5',
  },
  item: {
    display: 'flex', alignItems: 'flex-start', gap: '10px',
    padding: '10px 12px',
    borderRadius: '8px',
    marginBottom: '6px',
    cursor: 'pointer',
    transition: 'opacity var(--transition)',
  },
  dirBadge: {
    fontSize: '11px', fontWeight: '700',
    padding: '3px 8px', borderRadius: '99px',
    flexShrink: 0, whiteSpace: 'nowrap', marginTop: '1px',
  },
  nameBlock: {
    flex: 1, minWidth: 0,
  },
  name: {
    fontSize: '14px', fontWeight: '600',
    color: 'var(--color-text-primary)',
  },
  ticker: {
    fontSize: '11px', color: 'var(--color-text-muted)',
    fontWeight: '400', marginLeft: '4px',
  },
  reason: {
    fontSize: '12px', lineHeight: '1.6',
    marginTop: '3px',
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  },
  divider: {
    borderTop: '1px solid var(--color-border)',
    margin: '4px 0 16px',
  },
  empty: {
    padding: '24px 0', textAlign: 'center',
    fontSize: '13px', color: 'var(--color-text-muted)',
  },
};

function groupByNews(items) {
  const NO_NEWS = '__no_news__';
  const map = {};
  for (const item of items) {
    const key = item.trigger_news || NO_NEWS;
    if (!map[key]) map[key] = [];
    map[key].push(item);
  }
  return Object.entries(map).sort(([a], [b]) => {
    if (a === NO_NEWS) return 1;
    if (b === NO_NEWS) return -1;
    return 0;
  });
}

export default function ImpactList({ items = [] }) {
  const navigate = useNavigate();

  if (!items.length) return null;

  const groups = groupByNews(items);
  const hasTrigger = groups.some(([key]) => key !== '__no_news__');

  return (
    <div style={s.card}>
      <div style={s.header}>뉴스 연동 종목</div>
      <div style={s.sub}>
        {hasTrigger
          ? '최신 뉴스 기준 — 함께 주가 변동 가능성이 있는 기업 (순서는 중요도와 무관)'
          : '최근 뉴스 기반 — 함께 주가 변동 가능성이 있는 기업 (순서는 중요도와 무관)'}
      </div>

      {groups.map(([newsKey, groupItems]) => {
        const showNews = newsKey !== '__no_news__';
        return (
          <div key={newsKey} style={s.group}>
            {showNews && (
              <div style={s.newsRow}>
                <span style={s.newsIcon}>📰</span>
                <span style={s.newsTitle}>{newsKey}</span>
              </div>
            )}
            {!showNews && hasTrigger && <div style={s.divider} />}

            {groupItems.map((item) => {
              const isPos = item.impact === 'positive';
              const bg = isPos ? 'var(--color-positive-bg)' : 'var(--color-negative-bg)';
              const color = isPos ? 'var(--color-positive)' : 'var(--color-negative)';
              return (
                <div
                  key={item.ticker}
                  style={{ ...s.item, background: bg }}
                  onClick={() => navigate(`/company/${item.ticker}`)}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.75')}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
                >
                  <span style={{ ...s.dirBadge, background: color, color: '#fff' }}>
                    {isPos ? '▲' : '▼'}
                  </span>
                  <div style={s.nameBlock}>
                    <div style={s.name}>
                      {item.name}
                      <span style={s.ticker}>{item.ticker}</span>
                    </div>
                    {item.reason && (
                      <div style={{ ...s.reason, color }}>
                        {item.reason}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
