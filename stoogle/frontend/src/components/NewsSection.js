import React, { useState } from 'react';

const PAGE_SIZE = 5;

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
    display: 'flex', flexDirection: 'column',
  },
  header: { marginBottom: '12px' },
  title: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  list: { display: 'flex', flexDirection: 'column', flex: 1 },
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
  pagination: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--color-border)',
  },
  navBtn: {
    display: 'flex', alignItems: 'center', gap: '4px',
    padding: '5px 12px', borderRadius: 'var(--radius-sm)',
    fontSize: '13px', fontWeight: '600', cursor: 'pointer',
    border: '1px solid var(--color-border)',
    background: 'var(--color-surface)', color: 'var(--color-text-secondary)',
    transition: 'all var(--transition)',
  },
  navBtnDisabled: {
    opacity: 0.35, cursor: 'default',
    border: '1px solid var(--color-border)',
    background: 'var(--color-surface)', color: 'var(--color-text-muted)',
  },
  pageInfo: { fontSize: '13px', color: 'var(--color-text-muted)' },
  noNews: { padding: '32px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: '14px', flex: 1 },
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

const TABS = [
  { key: 'all',      label: '전체' },
  { key: 'positive', label: '긍정' },
  { key: 'neutral',  label: '중립' },
  { key: 'negative', label: '부정' },
];

function tabActiveClass(key) {
  if (key === 'positive') return 'news-tab-btn active-positive';
  if (key === 'negative') return 'news-tab-btn active-negative';
  return 'news-tab-btn active';
}

export default function NewsSection({ news = [] }) {
  const [tab, setTab] = useState('all');
  const [page, setPage] = useState(0);

  const filtered = tab === 'all' ? news : news.filter((n) => n.sentiment === tab);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageNews = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const canPrev = page > 0;
  const canNext = page < totalPages - 1;

  function handleTabChange(key) {
    setTab(key);
    setPage(0);
  }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>관련 뉴스</span>
      </div>

      <div className="news-tabs">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            className={tab === key ? tabActiveClass(key) : 'news-tab-btn'}
            onClick={() => handleTabChange(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={styles.list}>
        {pageNews.length === 0 ? (
          <div style={styles.noNews}>뉴스가 없습니다.</div>
        ) : (
          pageNews.map((n) => (
            <div
              key={n.id ?? n.url}
              style={styles.item}
              onClick={() => n.url !== '#' && window.open(n.url, '_blank')}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.75')}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
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

      {filtered.length > PAGE_SIZE && (
        <div style={styles.pagination}>
          <button
            style={canPrev ? styles.navBtn : { ...styles.navBtn, ...styles.navBtnDisabled }}
            onClick={() => canPrev && setPage((p) => p - 1)}
            disabled={!canPrev}
          >
            ‹ 이전
          </button>
          <span style={styles.pageInfo}>
            {page + 1} / {totalPages}
          </span>
          <button
            style={canNext ? styles.navBtn : { ...styles.navBtn, ...styles.navBtnDisabled }}
            onClick={() => canNext && setPage((p) => p + 1)}
            disabled={!canNext}
          >
            다음 ›
          </button>
        </div>
      )}
    </div>
  );
}
