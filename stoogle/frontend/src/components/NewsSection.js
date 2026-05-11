import React, { useState } from 'react';

const TABS = ['전체', '실적', '기술', '분석', '이슈'];

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
    display: 'flex', flexDirection: 'column',
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' },
  title: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  tabs: { display: 'flex', gap: '4px', flexWrap: 'wrap' },
  tab: {
    padding: '3px 10px', borderRadius: '9999px', fontSize: '12px',
    fontWeight: '600', cursor: 'pointer', border: '1px solid var(--color-border)',
    transition: 'all var(--transition)',
  },
  tabActive: { background: 'var(--color-accent)', color: '#fff', borderColor: 'var(--color-accent)' },
  tabInactive: { background: 'transparent', color: 'var(--color-text-secondary)' },
  list: { display: 'flex', flexDirection: 'column', gap: '0', overflow: 'auto', flex: 1 },
  item: {
    padding: '12px 0', borderBottom: '1px solid var(--color-border)',
    cursor: 'pointer',
  },
  itemTitle: {
    fontSize: '14px', fontWeight: '500', color: 'var(--color-text-primary)',
    lineHeight: '1.4', marginBottom: '6px',
    display: '-webkit-box', WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  itemMeta: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--color-text-muted)' },
  dot: { width: '4px', height: '4px', borderRadius: '50%', background: 'currentColor', opacity: 0.4 },
  noNews: { padding: '32px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: '14px' },
};

function sentimentBadge(s) {
  if (s === 'positive') return <span className="badge badge-negative">긍정</span>;
  if (s === 'negative') return <span className="badge badge-positive">부정</span>;
  return <span className="badge badge-neutral">중립</span>;
}

function relativeTime(dateStr) {
  const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

export default function NewsSection({ news = [] }) {
  const [tab, setTab] = useState('전체');

  const filtered = tab === '전체' ? news : news.filter((n) => n.category === tab);

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>관련 뉴스</span>
        <div style={styles.tabs}>
          {TABS.map((t) => (
            <button
              key={t}
              style={{ ...styles.tab, ...(tab === t ? styles.tabActive : styles.tabInactive) }}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.list}>
        {filtered.length === 0 ? (
          <div style={styles.noNews}>뉴스가 없습니다.</div>
        ) : (
          filtered.map((n) => (
            <div
              key={n.id}
              style={styles.item}
              onClick={() => n.url !== '#' && window.open(n.url, '_blank')}
              onMouseEnter={(e) => e.currentTarget.style.opacity = '0.75'}
              onMouseLeave={(e) => e.currentTarget.style.opacity = '1'}
            >
              <div style={styles.itemTitle}>{n.title}</div>
              <div style={styles.itemMeta}>
                {sentimentBadge(n.sentiment)}
                <span style={styles.dot} />
                <span>{n.source}</span>
                <span style={styles.dot} />
                <span>{relativeTime(n.published_at)}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
