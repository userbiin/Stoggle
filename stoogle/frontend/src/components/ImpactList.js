import React from 'react';
import { useNavigate } from 'react-router-dom';

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
  },
  title: { fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: 'var(--color-text-primary)' },
  grid: {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px',
  },
  item: {
    border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
    padding: '12px 16px', cursor: 'pointer',
    transition: 'box-shadow var(--transition), border-color var(--transition)',
    display: 'flex', flexDirection: 'column', gap: '6px',
  },
  itemTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  name: { fontSize: '14px', fontWeight: '600', color: 'var(--color-text-primary)' },
  ticker: { fontSize: '11px', color: 'var(--color-text-muted)' },
  reason: { fontSize: '12px', color: 'var(--color-text-secondary)', lineHeight: '1.4' },
};

export default function ImpactList({ items = [] }) {
  const navigate = useNavigate();

  if (!items.length) return null;

  return (
    <div style={styles.card}>
      <div style={styles.title}>영향 종목</div>
      <div style={styles.grid}>
        {items.map((item) => {
          const isPos = item.impact === 'positive';
          return (
            <div
              key={item.ticker}
              style={{
                ...styles.item,
                borderColor: isPos ? '#c5d8f8' : '#f8d0cf',
                background: isPos ? '#fafcff' : '#fff9f9',
              }}
              onClick={() => navigate(`/company/${item.ticker}`)}
              onMouseEnter={(e) => e.currentTarget.style.boxShadow = 'var(--shadow-md)'}
              onMouseLeave={(e) => e.currentTarget.style.boxShadow = 'none'}
            >
              <div style={styles.itemTop}>
                <span style={styles.name}>{item.name}</span>
                <span className={`badge ${isPos ? 'badge-negative' : 'badge-positive'}`}>
                  {isPos ? '수혜' : '부정'}
                </span>
              </div>
              <div style={styles.ticker}>{item.ticker}</div>
              <div style={styles.reason}>{item.reason}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
