import React from 'react';
import { useNavigate } from 'react-router-dom';

const TYPE_STYLE = {
  '경쟁사':   { bg: 'var(--color-negative-bg)',  color: 'var(--color-negative)' },
  '경쟁':     { bg: 'var(--color-negative-bg)',  color: 'var(--color-negative)' },
  '공급사':   { bg: 'var(--color-accent-light)',  color: 'var(--color-accent)' },
  '납품업체': { bg: 'var(--color-accent-light)',  color: 'var(--color-accent)' },
  '공급망':   { bg: 'var(--color-accent-light)',  color: 'var(--color-accent)' },
  '협력사':   { bg: 'var(--color-positive-bg)',   color: 'var(--color-positive)' },
  '협력':     { bg: 'var(--color-positive-bg)',   color: 'var(--color-positive)' },
  '계열사':   { bg: '#f0eeff',                    color: 'var(--color-brand)' },
  '계열':     { bg: '#f0eeff',                    color: 'var(--color-brand)' },
  '고객사':   { bg: '#fff8e1',                    color: '#b7860b' },
  '유통사':   { bg: '#fff8e1',                    color: '#b7860b' },
};
const DEFAULT_TYPE = { bg: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)' };

const s = {
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)',
    padding: '20px',
    boxShadow: 'var(--shadow-sm)',
  },
  header: {
    fontSize: '15px', fontWeight: '600',
    color: 'var(--color-text-primary)',
    marginBottom: '4px',
  },
  sub: {
    fontSize: '12px', color: 'var(--color-text-muted)',
    marginBottom: '16px',
  },
  analyzing: {
    display: 'flex', alignItems: 'center', gap: '8px',
    background: '#f0eeff', border: '1px solid #c8bfff',
    borderRadius: '8px', padding: '10px 14px',
    fontSize: '12px', color: 'var(--color-brand)',
    marginBottom: '16px',
  },
  dot: {
    width: '8px', height: '8px', borderRadius: '50%',
    background: 'var(--color-brand)', flexShrink: 0,
    animation: 'pulse 1.4s ease-in-out infinite',
  },
  item: {
    padding: '12px 0',
    borderBottom: '1px solid var(--color-border)',
  },
  row: {
    display: 'flex', alignItems: 'center', gap: '10px',
    cursor: 'pointer', marginBottom: '8px',
  },
  badge: {
    fontSize: '11px', fontWeight: '700',
    padding: '2px 8px', borderRadius: '99px',
    flexShrink: 0, whiteSpace: 'nowrap',
  },
  name: {
    fontSize: '14px', fontWeight: '600',
    color: 'var(--color-text-primary)', flex: 1,
  },
  ticker: {
    fontSize: '11px', color: 'var(--color-text-muted)',
    fontWeight: '400', marginLeft: '4px',
  },
  reason: {
    background: 'var(--color-accent-light)',
    border: '1px solid #d0e4fc',
    borderRadius: '6px',
    padding: '6px 10px',
    fontSize: '12px', color: '#1a3c6e', lineHeight: '1.6',
  },
  empty: {
    padding: '24px 0', textAlign: 'center',
    fontSize: '13px', color: 'var(--color-text-muted)',
  },
};

export default function RelationList({ companies = [], isAnalyzing = false }) {
  const navigate = useNavigate();

  return (
    <div style={s.card}>
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }`}</style>
      <div style={s.header}>비즈니스 관계사</div>
      <div style={s.sub}>과거 뉴스·공시 소급 분석 기반 — 협력사 · 공급업체 · 경쟁사 등 실제 사업 관계 기업</div>

      {isAnalyzing && (
        <div style={s.analyzing}>
          <span style={s.dot} />
          기업 관계 발굴 중입니다... 잠시 후 자동 업데이트됩니다.
        </div>
      )}

      {companies.length === 0 && !isAnalyzing ? (
        <div style={s.empty}>관계사 정보가 없습니다.</div>
      ) : (
        companies.map((c) => {
          const typeStyle = TYPE_STYLE[c.relation_type] || DEFAULT_TYPE;
          return (
            <div key={c.ticker} style={s.item}>
              <div
                style={s.row}
                onClick={() => navigate(`/company/${c.ticker}`)}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
              >
                <span style={{ ...s.badge, background: typeStyle.bg, color: typeStyle.color }}>
                  {c.relation_type || '관심'}
                </span>
                <div style={s.name}>
                  {c.name}
                  <span style={s.ticker}>{c.ticker}</span>
                </div>
              </div>
              {c.reason && <div style={s.reason}>{c.reason}</div>}
            </div>
          );
        })
      )}
    </div>
  );
}
