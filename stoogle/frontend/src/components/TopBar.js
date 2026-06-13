// TopBar
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const styles = {
  bar: {
    position: 'sticky', top: 0, zIndex: 100,
    background: 'var(--color-surface)',
    borderBottom: '1px solid var(--color-border)',
    padding: '10px 20px',
    display: 'flex', alignItems: 'center', gap: '16px',
    boxShadow: 'var(--shadow-sm)',
  },
  logo: {
    fontSize: '22px', fontWeight: '700', letterSpacing: '-1px',
    background: 'linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%)',
    WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
    backgroundClip: 'text', cursor: 'pointer', flexShrink: 0,
    textDecoration: 'none',
  },
  searchBox: {
    flex: 1, maxWidth: '520px',
    display: 'flex', alignItems: 'center',
    border: '1px solid var(--color-border)', borderRadius: '9999px',
    padding: '8px 16px', gap: '8px',
    background: 'var(--color-bg-secondary)',
    transition: 'border-color var(--transition), box-shadow var(--transition)',
  },
  input: {
    flex: 1, border: 'none', outline: 'none',
    fontSize: '14px', background: 'transparent',
    color: 'var(--color-text-primary)',
  },
  btn: {
    background: 'var(--color-accent)', color: '#fff',
    borderRadius: '9999px', padding: '5px 14px',
    fontSize: '13px', fontWeight: '600',
    border: 'none', cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
};

// 상단 바
export default function TopBar({ initialQuery = '' }) {
  const [query, setQuery] = useState(initialQuery);
  const navigate = useNavigate();

  // 검색 실행
  const handleSearch = () => {
    const term = query.trim();
    if (!term) return;
    navigate(`/search?q=${encodeURIComponent(term)}`);
  };

  return (
    <div style={styles.bar}>
      <span style={styles.logo} onClick={() => navigate('/')}>Stoogle</span>
      <div style={styles.searchBox}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input
          style={styles.input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="종목명 또는 종목코드"
        />
        <button style={styles.btn} onClick={handleSearch}>검색</button>
      </div>
    </div>
  );
}
