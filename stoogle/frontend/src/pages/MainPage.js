// MainPage
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const styles = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--color-bg)',
    padding: '20px',
  },
  logo: {
    fontSize: '56px',
    fontWeight: '700',
    letterSpacing: '-2px',
    marginBottom: '36px',
    background: 'linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    backgroundClip: 'text',
  },
  tagline: {
    fontSize: '16px',
    color: 'var(--color-text-secondary)',
    marginTop: '-28px',
    marginBottom: '36px',
    letterSpacing: '0.5px',
  },
  searchBox: {
    display: 'flex',
    alignItems: 'center',
    width: '100%',
    maxWidth: '580px',
    border: '1px solid var(--color-border)',
    borderRadius: '9999px',
    padding: '12px 20px',
    boxShadow: 'var(--shadow-sm)',
    background: 'var(--color-surface)',
    gap: '12px',
    transition: 'box-shadow var(--transition), border-color var(--transition)',
  },
  searchBoxFocused: {
    boxShadow: 'var(--shadow-md)',
    borderColor: 'var(--color-accent)',
    outline: 'none',
  },
  input: {
    flex: 1,
    border: 'none',
    outline: 'none',
    fontSize: '16px',
    color: 'var(--color-text-primary)',
    background: 'transparent',
  },
  searchBtn: {
    background: 'var(--color-accent)',
    color: '#fff',
    border: 'none',
    borderRadius: '9999px',
    padding: '8px 20px',
    fontSize: '14px',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'background var(--transition)',
    whiteSpace: 'nowrap',
  },
};

// 메인 페이지
export default function MainPage() {
  const [query, setQuery] = useState('');
  const [focused, setFocused] = useState(false);
  const navigate = useNavigate();

  // 검색 실행
  const handleSearch = (q) => {
    const term = (q || query).trim();
    if (!term) return;
    navigate(`/search?q=${encodeURIComponent(term)}`);
  };

  // 키 입력
  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch();
  };

  return (
    <div style={styles.page}>
      <div style={styles.logo}>Stoogle</div>
      <p style={styles.tagline}>주식 전용 인사이트 검색엔진</p>

      <div style={{ ...styles.searchBox, ...(focused ? styles.searchBoxFocused : {}) }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input
          style={styles.input}
          type="text"
          placeholder="종목명 또는 종목코드 입력"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          autoFocus
        />
        <button style={styles.searchBtn} onClick={() => handleSearch()}>
          검색
        </button>
      </div>


    </div>
  );
}
