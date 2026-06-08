import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import cloud from 'd3-cloud';

// 중요도에 따른 색상 팔레트 (진할수록 중요)
const COLOR_BY_RANK = [
  '#1a56db', // 1~3위: 진한 블루
  '#1a56db',
  '#1a56db',
  '#2563eb', // 4~6위
  '#2563eb',
  '#2563eb',
  '#3b82f6', // 7~9위
  '#3b82f6',
  '#3b82f6',
  '#60a5fa', // 10~12위: 중간 블루
  '#60a5fa',
  '#60a5fa',
  '#93c5fd', // 13위~: 연한 블루
  '#93c5fd',
  '#bfdbfe',
];

const styles = {
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)',
    padding: '20px',
    boxShadow: 'var(--shadow-sm)',
    position: 'relative',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    marginBottom: '4px',
  },
  title: {
    fontSize: '15px',
    fontWeight: '600',
    color: 'var(--color-text-primary)',
  },
  count: {
    fontSize: '12px',
    color: 'var(--color-text-secondary)',
    fontWeight: '400',
  },
  svgWrapper: {
    position: 'relative',
    width: '100%',
  },
  svg: {
    width: '100%',
    display: 'block',
  },
  empty: {
    height: '200px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'var(--color-text-secondary)',
    fontSize: '13px',
  },
  tooltip: {
    position: 'absolute',
    background: 'rgba(15, 23, 42, 0.9)',
    color: '#f8fafc',
    padding: '5px 10px',
    borderRadius: '6px',
    fontSize: '12px',
    fontWeight: '500',
    pointerEvents: 'none',
    whiteSpace: 'nowrap',
    zIndex: 10,
    transition: 'opacity 0.15s',
    boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
  },
};

export default function WordCloudSection({ keywords = [] }) {
  const svgRef = useRef(null);
  const wrapperRef = useRef(null);
  const [tooltip, setTooltip] = useState({ visible: false, text: '', x: 0, y: 0 });
  const W = 360;
  const H = 280;

  useEffect(() => {
    if (!keywords.length || !svgRef.current) return;

    // sqrt 스케일: 큰 단어의 독점을 줄이고 더 고른 분포
    const maxVal = Math.max(...keywords.map((k) => k.value));
    const sizeScale = d3
      .scaleSqrt()
      .domain([0, maxVal])
      .range([13, 46])
      .clamp(true);

    // 중요도 순 정렬 유지 (color rank용)
    const sorted = [...keywords].sort((a, b) => b.value - a.value);
    const rankMap = new Map(sorted.map((k, i) => [k.text, i]));

    const words = keywords.map((k) => ({
      text: k.text,
      size: sizeScale(k.value),
      value: k.value,
    }));

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    // 배경 subtle gradient
    const defs = svg.append('defs');
    const grad = defs
      .append('radialGradient')
      .attr('id', 'wc-bg')
      .attr('cx', '50%').attr('cy', '50%')
      .attr('r', '50%');
    grad.append('stop').attr('offset', '0%').attr('stop-color', '#eff6ff').attr('stop-opacity', 0.6);
    grad.append('stop').attr('offset', '100%').attr('stop-color', '#ffffff').attr('stop-opacity', 0);

    svg.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', 'url(#wc-bg)')
      .attr('rx', 8);

    const g = svg.append('g').attr('transform', `translate(${W / 2},${H / 2})`);

    cloud()
      .size([W, H])
      .words(words)
      .padding(5)
      .rotate(0)          // ← 한글 세로 회전 완전 제거
      .font('"Noto Sans KR", "Apple SD Gothic Neo", sans-serif')
      .fontWeight((d) => {
        const rank = rankMap.get(d.text) ?? 99;
        return rank < 3 ? '800' : rank < 6 ? '700' : '500';
      })
      .fontSize((d) => d.size)
      .on('end', (output) => {
        const textNodes = g
          .selectAll('text')
          .data(output)
          .enter()
          .append('text')
          .style('font-size', (d) => `${d.size}px`)
          .style('font-family', '"Noto Sans KR", "Apple SD Gothic Neo", sans-serif')
          .style('font-weight', (d) => {
            const rank = rankMap.get(d.text) ?? 99;
            return rank < 3 ? '800' : rank < 6 ? '700' : '500';
          })
          .style('fill', (d) => {
            const rank = rankMap.get(d.text) ?? 14;
            return COLOR_BY_RANK[Math.min(rank, COLOR_BY_RANK.length - 1)];
          })
          .style('letter-spacing', (d) => (d.size > 30 ? '0.5px' : '0'))
          .attr('text-anchor', 'middle')
          .attr('transform', (d) => `translate(${d.x},${d.y})`)
          .style('cursor', 'default')
          .style('opacity', 0)
          .text((d) => d.text)
          // hover 인터랙션
          .on('mouseenter', function (event, d) {
            d3.select(this)
              .transition().duration(150)
              .style('opacity', 1)
              .attr('transform', (d) => `translate(${d.x},${d.y}) scale(1.12)`);

            const rect = wrapperRef.current?.getBoundingClientRect();
            const evRect = event.target.getBoundingClientRect();
            setTooltip({
              visible: true,
              text: `${d.text} (${d.value})`,
              x: evRect.left - (rect?.left ?? 0) + evRect.width / 2,
              y: evRect.top - (rect?.top ?? 0) - 8,
            });
          })
          .on('mouseleave', function () {
            d3.select(this)
              .transition().duration(150)
              .style('opacity', 1)
              .attr('transform', (d) => `translate(${d.x},${d.y}) scale(1)`);
            setTooltip((t) => ({ ...t, visible: false }));
          });

        // 순차 등장 애니메이션 (크기 + opacity)
        textNodes
          .transition()
          .duration(500)
          .delay((_, i) => i * 35)
          .ease(d3.easeCubicOut)
          .style('opacity', 1)
          .attr('transform', (d) => `translate(${d.x},${d.y}) scale(1)`);
      })
      .start();
  }, [keywords]);

  if (!keywords.length) {
    return (
      <div style={styles.card}>
        <div style={styles.header}>
          <span style={styles.title}>주요 키워드</span>
        </div>
        <div style={styles.empty}>키워드 데이터가 없습니다</div>
      </div>
    );
  }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>주요 키워드</span>
        <span style={styles.count}>{keywords.length}개</span>
      </div>
      <div ref={wrapperRef} style={styles.svgWrapper}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          style={styles.svg}
        />
        {tooltip.visible && (
          <div
            style={{
              ...styles.tooltip,
              left: tooltip.x,
              top: tooltip.y,
              transform: 'translateX(-50%) translateY(-100%)',
              opacity: tooltip.visible ? 1 : 0,
            }}
          >
            {tooltip.text}
          </div>
        )}
      </div>
    </div>
  );
}