import React, { useState } from 'react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';

const PERIODS = [
  { label: '1M', days: 21 },
  { label: '3M', days: 63 },
  { label: '6M', days: 126 },
  { label: '전체', days: Infinity },
];

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' },
  title: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  tabs: { display: 'flex', gap: '4px' },
  tab: {
    padding: '4px 10px', borderRadius: 'var(--radius-sm)',
    fontSize: '12px', fontWeight: '600', cursor: 'pointer',
    border: '1px solid transparent', transition: 'all var(--transition)',
  },
  tabActive: {
    background: 'var(--color-accent)', color: '#fff',
    borderColor: 'var(--color-accent)',
  },
  tabInactive: {
    background: 'transparent', color: 'var(--color-text-secondary)',
    borderColor: 'var(--color-border)',
  },
};

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{
      background: '#fff', border: '1px solid var(--color-border)',
      borderRadius: 'var(--radius-sm)', padding: '8px 12px',
      fontSize: '13px', boxShadow: 'var(--shadow-sm)',
    }}>
      <div style={{ color: 'var(--color-text-secondary)', marginBottom: '4px' }}>{d.date}</div>
      <div style={{ fontWeight: '700', fontSize: '15px' }}>{d.close?.toLocaleString('ko-KR')}원</div>
      <div style={{ color: 'var(--color-text-muted)', fontSize: '12px' }}>
        거래량 {(d.volume / 1e6).toFixed(1)}M
      </div>
    </div>
  );
};

export default function PriceChart({ history = [], name = '' }) {
  const [period, setPeriod] = useState('3M');

  const selectedDays = PERIODS.find((p) => p.label === period)?.days ?? Infinity;
  const data = history.slice(-selectedDays);

  const prices = data.map((d) => d.close);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const firstClose = data[0]?.close ?? 0;
  const lastClose = data[data.length - 1]?.close ?? 0;
  const isUp = lastClose >= firstClose;

  const color = isUp ? '#c5221f' : '#1a73e8';

  const tickFormatter = (val) => {
    if (val >= 1e4) return (val / 1e4).toFixed(0) + '만';
    return val?.toLocaleString('ko-KR');
  };

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>{name} 주가 추이</span>
        <div style={styles.tabs}>
          {PERIODS.map((p) => (
            <button
              key={p.label}
              style={{ ...styles.tab, ...(period === p.label ? styles.tabActive : styles.tabInactive) }}
              onClick={() => setPeriod(p.label)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.18} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="date" tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
            tickLine={false} axisLine={false}
            tickFormatter={(v) => v?.slice(5)}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[minPrice * 0.98, maxPrice * 1.02]}
            tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
            tickLine={false} axisLine={false}
            tickFormatter={tickFormatter}
            width={52}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={firstClose} stroke={color} strokeDasharray="4 4" strokeOpacity={0.5} />
          <Area
            type="monotone" dataKey="close"
            stroke={color} strokeWidth={2}
            fill="url(#priceGrad)" dot={false} activeDot={{ r: 4, fill: color }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
