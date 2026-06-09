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
  subtitle: {
    fontSize: '12px',
    color: 'var(--color-text-muted)',
    marginLeft: '4px',
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
  chipConfidence: {
    fontSize: '10px',
    fontWeight: '400',
    opacity: 0.55,
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
  emptyState: {
    textAlign: 'center',
    padding: '24px 0 8px',
    color: 'var(--color-text-muted)',
    fontSize: '13px',
  },
};

function buildInsightText(items) {
  const up = items.filter((i) => i.direction === 'up');
  const down = items.filter((i) => i.direction === 'down');
  const parts = [];
  if (up.length) {
    const names = up.map((i) => i.name).join('·');
    const reasons = up.map((i) => i.reason).join(' 또한 ');
    parts.push(`${names}는 ${reasons}`);
  }
  if (down.length) {
    const names = down.map((i) => i.name).join('·');
    const reasons = down.map((i) => i.reason).join(' 또한 ');
    const connector = up.length ? ' 반면 ' : '';
    parts.push(`${connector}${names}는 ${reasons}`);
  }
  return parts.join('. ') + (parts.length ? '.' : '');
}

export default function AnalysisImpacts({ items = [] }) {
  const navigate = useNavigate();
  const filtered = items.filter((i) => i.direction !== 'neutral');

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>AI 영향 예측 종목</span>
        <span style={styles.subtitle}>최근 뉴스 분석 기반 자동 예측</span>
      </div>

      {filtered.length === 0 ? (
        <div style={styles.emptyState}>현재 뉴스 기반 영향 예측 종목이 없습니다</div>
      ) : (
        <>
          <div style={styles.chipRow}>
            {filtered.map((item) => {
              const isUp = item.direction === 'up';
              return (
                <span
                  key={item.ticker}
                  style={{
                    ...styles.chip,
                    background: isUp ? 'var(--color-positive-bg)' : 'var(--color-negative-bg)',
                    color: isUp ? 'var(--color-positive)' : 'var(--color-negative)',
                    borderColor: isUp ? '#a8d5b5' : '#f5b7b5',
                  }}
                  onClick={() => navigate(`/company/${item.ticker}`)}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.75')}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
                  title={item.reason}
                >
                  <span style={styles.chipArrow}>{isUp ? '▲' : '▼'}</span>
                  {item.name}
                  <span style={styles.chipTicker}>{item.ticker}</span>
                  <span style={styles.chipConfidence}>
                    {Math.round((item.confidence ?? 0) * 100)}%
                  </span>
                </span>
              );
            })}
          </div>

          <div style={styles.divider} />

          <div style={styles.insightBox}>
            <div style={styles.insightHeader}>
              <span style={styles.insightLabel}>AI 인사이트</span>
            </div>
            <div style={styles.insightText}>{buildInsightText(filtered)}</div>
          </div>
        </>
      )}
    </div>
  );
}
