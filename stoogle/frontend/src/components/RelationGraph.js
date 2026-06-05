import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { useNavigate } from 'react-router-dom';

const TYPE_COLORS = {
  경쟁: '#c5221f',
  계열: '#1a73e8',
  협력: '#137333',
  공급망: '#e37400',
  관심: '#5f6368',
};

const styles = {
  card: {
    background: 'var(--color-surface)', border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)', padding: '20px', boxShadow: 'var(--shadow-sm)',
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' },
  title: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  legend: { display: 'flex', gap: '10px', flexWrap: 'wrap' },
  legendItem: { display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--color-text-secondary)' },
  dot: { width: '8px', height: '8px', borderRadius: '50%' },
  svg: { width: '100%', cursor: 'grab' },
};

const analyzingBox = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
  height: '320px', gap: '12px',
  color: 'var(--color-text-muted)', fontSize: '13px', textAlign: 'center',
};

export default function RelationGraph({ data, centerId, isAnalyzing = false }) {
  const svgRef = useRef(null);
  const navigate = useNavigate();
  const W = 400;
  const H = 320;

  useEffect(() => {
    if (!data || !svgRef.current) return;

    const nodes = data.nodes.map((n) => ({ ...n }));
    const links = data.links.map((l) => ({ ...l }));

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const g = svg.append('g');

    svg.call(
      d3.zoom().scaleExtent([0.5, 3]).on('zoom', (event) => {
        g.attr('transform', event.transform);
      })
    );

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(80))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collision', d3.forceCollide().radius((d) => d.size / 2 + 10));

    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .enter().append('line')
      .attr('stroke', (d) => TYPE_COLORS[d.type] || '#ccc')
      .attr('stroke-width', (d) => Math.max(1, d.value * 3))
      .attr('stroke-opacity', 0.5);

    const nodeG = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .style('cursor', 'pointer')
      .call(
        d3.drag()
          .on('start', (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on('end', (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null; d.fy = null;
          })
      )
      .on('click', (_, d) => {
        if (d.id !== centerId) navigate(`/company/${d.id}`);
      });

    nodeG.append('circle')
      .attr('r', (d) => d.size / 2)
      .attr('fill', (d) => d.id === centerId ? '#1a73e8' : '#e8f0fe')
      .attr('stroke', (d) => d.id === centerId ? '#0d47a1' : '#1a73e8')
      .attr('stroke-width', 2);

    nodeG.append('text')
      .text((d) => d.name)
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => d.size / 2 + 12)
      .style('font-size', '10px')
      .style('fill', 'var(--color-text-secondary)')
      .style('pointer-events', 'none');

    sim.on('tick', () => {
      link
        .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
      nodeG.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    return () => sim.stop();
  }, [data, centerId, navigate]);

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>연관 기업 관계도</span>
        {!isAnalyzing && (
          <div style={styles.legend}>
            {Object.entries(TYPE_COLORS).map(([type, color]) => (
              <div key={type} style={styles.legendItem}>
                <div style={{ ...styles.dot, background: color }} />
                <span>{type}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {isAnalyzing ? (
        <div style={analyzingBox}>
          <span style={{ fontSize: '28px' }}>⏳</span>
          <div>
            <div style={{ fontWeight: '600', color: 'var(--color-text-primary)', marginBottom: '4px' }}>
              과거 뉴스·공시를 분석 중입니다
            </div>
            <div>LLM이 비즈니스 관계를 발굴하고 있습니다.<br />잠시 후 새로고침하면 관계도를 확인할 수 있습니다.</div>
          </div>
        </div>
      ) : (
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={styles.svg} />
      )}
    </div>
  );
}
