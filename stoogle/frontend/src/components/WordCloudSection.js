import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import cloud from 'd3-cloud';

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
  },
  title: { fontSize: '15px', fontWeight: '600', marginBottom: '12px', color: 'var(--color-text-primary)' },
  svg: { width: '100%', display: 'block' },
};

const COLORS = ['#1a73e8', '#0d47a1', '#1565c0', '#1976d2', '#42a5f5', '#2196f3', '#5c6bc0', '#3949ab'];

export default function WordCloudSection({ keywords = [] }) {
  const svgRef = useRef(null);
  const W = 360;
  const H = 240;

  useEffect(() => {
    if (!keywords.length || !svgRef.current) return;

    const maxVal = Math.max(...keywords.map((k) => k.value));
    const sizeScale = d3.scaleLinear().domain([0, maxVal]).range([13, 44]);

    const words = keywords.map((k) => ({ text: k.text, size: sizeScale(k.value) }));

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const g = svg.append('g').attr('transform', `translate(${W / 2},${H / 2})`);

    cloud()
      .size([W, H])
      .words(words)
      .padding(6)
      .rotate(() => (Math.random() > 0.7 ? 90 : 0))
      .font('Noto Sans KR, sans-serif')
      .fontSize((d) => d.size)
      .on('end', (output) => {
        g.selectAll('text')
          .data(output)
          .enter()
          .append('text')
          .style('font-size', (d) => `${d.size}px`)
          .style('font-family', 'Noto Sans KR, sans-serif')
          .style('font-weight', (d) => (d.size > 30 ? '700' : '500'))
          .style('fill', (_, i) => COLORS[i % COLORS.length])
          .attr('text-anchor', 'middle')
          .attr('transform', (d) => `translate(${d.x},${d.y}) rotate(${d.rotate})`)
          .style('cursor', 'default')
          .style('opacity', 0)
          .text((d) => d.text)
          .transition().duration(400).delay((_, i) => i * 40)
          .style('opacity', 1);
      })
      .start();
  }, [keywords]);

  return (
    <div style={styles.card}>
      <div style={styles.title}>주요 키워드</div>
      <svg ref={svgRef} viewBox={`0 0 ${360} ${240}`} style={styles.svg} />
    </div>
  );
}
