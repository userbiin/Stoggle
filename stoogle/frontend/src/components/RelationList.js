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
    display: 'flex', alignItems: 'center', gap: '12px',
    padding: '10px 0', borderBottom: '1px solid var(--color-border)',
    cursor: 'pointer', transition: 'opacity var(--transition)',
  },
  rank: {
    width: '22px', height: '22px', borderRadius: '50%',
    background: 'var(--color-accent-light)', color: 'var(--color-accent)',
    fontSize: '11px', fontWeight: '700', display: 'flex',
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  info: { flex: 1 },
  name: { fontSize: '14px', fontWeight: '600', color: 'var(--color-text-primary)' },
  reason: { fontSize: '12px', color: 'var(--color-text-secondary)', marginTop: '2px' },
  corr: {
    textAlign: 'right',
  },
  corrBar: {
    height: '4px', borderRadius: '2px', background: 'var(--color-border)',
    marginTop: '4px', overflow: 'hidden',
  },
  corrFill: { height: '100%', borderRadius: '2px', background: 'var(--color-accent)', transition: 'width 0.5s ease' },
  corrValue: { fontSize: '12px', fontWeight: '700', color: 'var(--color-accent)' },
};

export default function RelationList({ companies = [] }) {
  const navigate = useNavigate();

  return (
    <div style={styles.card}>
      <div style={styles.title}>연관 기업 목록</div>
      <div style={styles.list}>
        {companies.map((c, i) => (
          <div
            key={c.ticker}
            style={styles.item}
            onClick={() => navigate(`/company/${c.ticker}`)}
            onMouseEnter={(e) => e.currentTarget.style.opacity = '0.7'}
            onMouseLeave={(e) => e.currentTarget.style.opacity = '1'}
          >
            <div style={styles.rank}>{i + 1}</div>
            <div style={styles.info}>
              <div style={styles.name}>{c.name} <span style={{ fontSize: '11px', color: 'var(--color-text-muted)', fontWeight: '400' }}>{c.ticker}</span></div>
              <div style={styles.reason}>{c.reason}</div>
            </div>
            <div style={{ ...styles.corr, width: '64px' }}>
              <div style={styles.corrValue}>{(c.correlation * 100).toFixed(0)}%</div>
              <div style={styles.corrBar}>
                <div style={{ ...styles.corrFill, width: `${c.correlation * 100}%` }} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
