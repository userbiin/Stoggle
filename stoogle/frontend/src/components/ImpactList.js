import React from 'react';
import { useNavigate } from 'react-router-dom';

const styles = {
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)',
    padding: '20px 24px',
    boxShadow: 'var(--shadow-sm)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginBottom: '16px',
  },
  title: {
    fontSize: '15px',
    fontWeight: '600',
    color: 'var(--color-text-primary)',
  },
  chipRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '8px',
    marginBottom: '20px',
  },
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 12px',
    borderRadius: 'var(--radius-full)',
    fontSize: '13px',
    fontWeight: '600',
    cursor: 'pointer',
    border: '1px solid transparent',
    transition: 'opacity var(--transition)',
    whiteSpace: 'nowrap',
  },
  chipArrow: {
    fontSize: '10px',
    fontWeight: '700',
  },
  chipTicker: {
    fontSize: '11px',
    fontWeight: '400',
    opacity: 0.7,
  },
  divider: {
    borderTop: '1px solid var(--color-border)',
    marginBottom: '16px',
  },
  insightBox: {
    background: 'var(--color-accent-light)',
    border: '1px solid #c5d8f8',
    borderRadius: 'var(--radius-md)',
    padding: '14px 18px',
  },
  insightHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    marginBottom: '8px',
  },
  insightLabel: {
    fontSize: '12px',
    fontWeight: '700',
    color: 'var(--color-accent)',
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
  },
  insightText: {
    fontSize: '13px',
    lineHeight: '1.75',
    color: '#1a3c6e',
  },
};

function buildInsightText(items) {
  const pos = items.filter((i) => i.impact === 'positive');
  const neg = items.filter((i) => i.impact === 'negative');
  const parts = [];

  if (pos.length) {
    const names = pos.map((i) => i.name).join('·');
    const reasons = pos.map((i) => i.reason).join(' 또한 ');
    parts.push(`${names}는 ${reasons}`);
  }
  if (neg.length) {
    const names = neg.map((i) => i.name).join('·');
    const reasons = neg.map((i) => i.reason).join(' 또한 ');
    const connector = pos.length ? ' 반면 ' : '';
    parts.push(`${connector}${names}는 ${reasons}`);
  }

  return parts.join('. ') + (parts.length ? '.' : '');
}

export default function ImpactList({ items = [] }) {
  const navigate = useNavigate();

  if (!items.length) return null;

  const insightText = buildInsightText(items);

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>주가 영향 예상 종목</span>
      </div>

      <div style={styles.chipRow}>
        {items.map((item) => {
          const isPos = item.impact === 'positive';
          return (
            <span
              key={item.ticker}
              style={{
                ...styles.chip,
                background: isPos ? 'var(--color-positive-bg)' : 'var(--color-negative-bg)',
                color: isPos ? 'var(--color-positive)' : 'var(--color-negative)',
                borderColor: isPos ? '#a8d5b5' : '#f5b7b5',
              }}
              onClick={() => navigate(`/company/${item.ticker}`)}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.75')}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
              title={item.reason}
            >
              <span style={styles.chipArrow}>{isPos ? '▲' : '▼'}</span>
              {item.name}
              <span style={styles.chipTicker}>{item.ticker}</span>
            </span>
          );
        })}
      </div>

      <div style={styles.divider} />

      <div style={styles.insightBox}>
        <div style={styles.insightHeader}>
          <span style={styles.insightLabel}>AI 인사이트</span>
        </div>
        <div style={styles.insightText}>{insightText}</div>
      </div>
    </div>
  );
}
