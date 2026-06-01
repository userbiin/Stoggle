import React from 'react';
import { useNavigate } from 'react-router-dom';

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
  },
  title: { fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: 'var(--color-text-primary)' },
  list: { display: 'flex', flexDirection: 'column', gap: '0' },
  item: {
    padding: '12px 0',
    borderBottom: '1px solid var(--color-border)',
  },
  itemHeader: {
    display: 'flex', alignItems: 'center', gap: '10px',
    cursor: 'pointer', transition: 'opacity var(--transition)',
    marginBottom: '8px',
  },
  name: { fontSize: '14px', fontWeight: '600', color: 'var(--color-text-primary)', flex: 1 },
  ticker: { fontSize: '11px', color: 'var(--color-text-muted)', fontWeight: '400', marginLeft: '4px' },
  corr: { textAlign: 'right', flexShrink: 0, width: '56px' },
  corrValue: { fontSize: '12px', fontWeight: '700', color: 'var(--color-accent)' },
  corrBar: {
    height: '3px', borderRadius: '2px', background: 'var(--color-border)',
    marginTop: '3px', overflow: 'hidden',
  },
  corrFill: { height: '100%', borderRadius: '2px', background: 'var(--color-accent)', transition: 'width 0.5s ease' },
  reasonBox: {
    background: 'var(--color-accent-light)',
    border: '1px solid #d0e4fc',
    borderRadius: '6px',
    padding: '7px 10px',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '6px',
  },
  reasonLabel: {
    fontSize: '10px', fontWeight: '700', color: 'var(--color-accent)',
    letterSpacing: '0.04em', textTransform: 'uppercase',
    flexShrink: 0, paddingTop: '1px',
  },
  reasonText: {
    fontSize: '12px', color: '#1a3c6e', lineHeight: '1.6',
  },
};

export default function RelationList({ companies = [] }) {
  const navigate = useNavigate();

  return (
    <div style={styles.card}>
      <div style={styles.title}>연관 기업 목록</div>
      <div style={styles.list}>
        {companies.map((c) => (
          <div key={c.ticker} style={styles.item}>
            <div
              style={styles.itemHeader}
              onClick={() => navigate(`/company/${c.ticker}`)}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
            >
              <div style={styles.name}>
                {c.name}
                <span style={styles.ticker}>{c.ticker}</span>
              </div>
              <div style={styles.corr}>
                <div style={styles.corrValue}>{(c.correlation * 100).toFixed(0)}%</div>
                <div style={styles.corrBar}>
                  <div style={{ ...styles.corrFill, width: `${c.correlation * 100}%` }} />
                </div>
              </div>
            </div>
            {c.reason && (
              <div style={styles.reasonBox}>
                <span style={styles.reasonLabel}>AI</span>
                <span style={styles.reasonText}>{c.reason}</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
