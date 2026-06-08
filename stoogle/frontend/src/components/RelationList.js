import React from 'react';
import { useNavigate } from 'react-router-dom';

// 백엔드 relation_type 값과 매핑
const RELATION_TYPE_STYLE = {
  '공급망': { bg: '#fff3e0', color: '#e37400', label: '공급망' },
  '협력':   { bg: '#e8f5e9', color: '#137333', label: '협력' },
  '경쟁':   { bg: '#fce8e6', color: '#c5221f', label: '경쟁' },
  '계열사': { bg: '#e8f0fe', color: '#1a73e8', label: '계열사' },
  '계열':   { bg: '#e8f0fe', color: '#1a73e8', label: '계열사' }, // 구버전 호환
  '관심':   { bg: '#f1f3f4', color: '#5f6368', label: '관심' },
};

const DEFAULT_RELATION_STYLE = { bg: '#f1f3f4', color: '#5f6368', label: '관계사' };

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
  nameRow: { display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' },
  name: { fontSize: '14px', fontWeight: '600', color: 'var(--color-text-primary)' },
  tickerText: { fontSize: '11px', color: 'var(--color-text-muted)', fontWeight: '400' },
  reason: { fontSize: '12px', color: 'var(--color-text-secondary)', marginTop: '2px' },
  badge: {
    display: 'inline-block',
    padding: '1px 7px',
    borderRadius: '9999px',
    fontSize: '11px',
    fontWeight: '600',
    lineHeight: '1.6',
    flexShrink: 0,
  },
  empty: { padding: '24px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: '13px' },
};

// reason 앞에 붙는 "[supplier]" 같은 내부 태그 제거
function cleanReason(reason) {
  if (!reason) return '';
  return reason.replace(/^\[.*?\]\s*/, '');
}

export default function RelationList({ companies = [] }) {
  const navigate = useNavigate();

  return (
    <div style={styles.card}>
      <div style={styles.title}>연관 기업 목록</div>
      {companies.length === 0 ? (
        <div style={styles.empty}>연관 기업 정보가 없습니다.</div>
      ) : (
        <div style={styles.list}>
          {companies.map((c, i) => {
            const relStyle = RELATION_TYPE_STYLE[c.relation_type] || DEFAULT_RELATION_STYLE;
            return (
              <div
                key={c.ticker}
                style={styles.item}
                onClick={() => navigate(`/company/${c.ticker}`)}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
              >
                <div style={styles.rank}>{i + 1}</div>
                <div style={styles.info}>
                  <div style={styles.nameRow}>
                    <span style={styles.name}>{c.name}</span>
                    <span style={styles.tickerText}>{c.ticker}</span>
                    <span
                      style={{
                        ...styles.badge,
                        background: relStyle.bg,
                        color: relStyle.color,
                      }}
                    >
                      {relStyle.label}
                    </span>
                  </div>
                  <div style={styles.reason}>{cleanReason(c.reason)}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}