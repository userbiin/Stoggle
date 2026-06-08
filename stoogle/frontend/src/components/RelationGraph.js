import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { useNavigate } from 'react-router-dom';

const TYPE_COLORS = {
  경쟁:   '#e53935',
  계열사: '#1a73e8',
  계열:   '#1a73e8',
  협력:   '#2e7d32',
  공급망: '#f57c00',
  관심:   '#9e9e9e',
};

const TYPE_FILL = {
  경쟁:   '#fdecea',
  계열사: '#e8f0fe',
  계열:   '#e8f0fe',
  협력:   '#e8f5e9',
  공급망: '#fff3e0',
  관심:   '#f5f5f5',
};

const LEGEND_TYPES = ['경쟁', '계열사', '협력', '공급망', '관심'];

const styles = {
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius-md)',
    padding: '20px',
    boxShadow: 'var(--shadow-sm)',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '12px',
    flexWrap: 'wrap',
    gap: '8px',
  },
  title: { fontSize: '15px', fontWeight: '600', color: 'var(--color-text-primary)' },
  legend: { display: 'flex', gap: '10px', flexWrap: 'wrap' },
  legendItem: {
    display: 'flex', alignItems: 'center', gap: '4px',
    fontSize: '11px', color: 'var(--color-text-secondary)',
  },
  legendDot: { width: '7px', height: '7px', borderRadius: '50%', flexShrink: 0 },
  graphBg: {
    borderRadius: '10px',
    overflow: 'hidden',
    background: '#f6f7fb',
    border: '1px solid #eceef3',
  },
  svg: { width: '100%', cursor: 'grab' },
};

export default function RelationGraph({ data, centerId }) {
  const svgRef = useRef(null);
  const navigate = useNavigate();
  const W = 420;
  const H = 380;

  useEffect(() => {
    if (!data || !svgRef.current) return;

    const nodes = (data.nodes || []).map((n) => ({ ...n }));
    const links = (data.links || []).map((l) => ({ ...l }));

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    if (nodes.length === 0) return;

    // ── Defs ─────────────────────────────────────────────────────────────
    const defs = svg.append('defs');

    const shadowFilter = defs.append('filter')
      .attr('id', 'node-shadow')
      .attr('x', '-40%').attr('y', '-40%')
      .attr('width', '180%').attr('height', '180%');
    shadowFilter.append('feDropShadow')
      .attr('dx', 0).attr('dy', 2)
      .attr('stdDeviation', 3)
      .attr('flood-color', '#00000018');

    // Node radius helper
    const getR = (d) => d.id === centerId ? 22 : Math.min(17, Math.max(12, d.size / 2));

    // Build relation type lookup: nodeId → relationType
    const nodeRelType = {};
    links.forEach((l) => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      if (s === centerId) nodeRelType[t] = l.type;
      if (t === centerId) nodeRelType[s] = l.type;
    });

    // ── Zoom ──────────────────────────────────────────────────────────────
    const g = svg.append('g');
    svg.call(
      d3.zoom().scaleExtent([0.4, 3]).on('zoom', (event) => {
        g.attr('transform', event.transform);
      })
    );

    // ── Simulation ────────────────────────────────────────────────────────
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(115))
      .force('charge', d3.forceManyBody().strength(-320))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collision', d3.forceCollide().radius((d) => getR(d) + 14));

    // ── Links ─────────────────────────────────────────────────────────────
    const link = g.append('g')
      .selectAll('path')
      .data(links)
      .enter().append('path')
      .attr('fill', 'none')
      .attr('stroke', (d) => TYPE_COLORS[d.type] || '#ccc')
      .attr('stroke-width', (d) => Math.max(1.2, (d.value || 0.3) * 3))
      .attr('stroke-opacity', 0.45)
      .attr('stroke-linecap', 'round');

    // ── Nodes ─────────────────────────────────────────────────────────────
    const nodeG = g.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .style('cursor', (d) => d.id === centerId ? 'default' : 'pointer')
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
      })
      .on('mouseenter', function (_, d) {
        if (d.id === centerId) return;
        d3.select(this).select('circle')
          .transition().duration(120)
          .attr('r', getR(d) + 3)
          .attr('stroke-width', 2.5);
      })
      .on('mouseleave', function (_, d) {
        if (d.id === centerId) return;
        d3.select(this).select('circle')
          .transition().duration(120)
          .attr('r', getR(d))
          .attr('stroke-width', 1.5);
      });

    nodeG.append('circle')
      .attr('r', getR)
      .attr('fill', (d) => d.id === centerId ? '#534AB7' : (TYPE_FILL[nodeRelType[d.id]] || '#f5f5f5'))
      .attr('stroke', (d) => d.id === centerId ? '#3730a3' : (TYPE_COLORS[nodeRelType[d.id]] || '#bbb'))
      .attr('stroke-width', (d) => d.id === centerId ? 2 : 1.5)
      .attr('filter', 'url(#node-shadow)');

    // Center node: label inside circle
    nodeG.filter((d) => d.id === centerId)
      .append('text')
      .text((d) => d.name.length > 4 ? d.name.slice(0, 4) : d.name)
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .style('font-size', '9px')
      .style('font-weight', '700')
      .style('fill', '#fff')
      .style('pointer-events', 'none');

    // Other nodes: label below circle
    nodeG.filter((d) => d.id !== centerId)
      .append('text')
      .text((d) => d.name)
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => getR(d) + 12)
      .style('font-size', '10px')
      .style('font-weight', '500')
      .style('fill', '#5f6368')
      .style('pointer-events', 'none');

    // ── Tick ──────────────────────────────────────────────────────────────
    sim.on('tick', () => {
      link.attr('d', (d) => {
        const sx = d.source.x, sy = d.source.y;
        const tx = d.target.x, ty = d.target.y;
        const mx = (sx + tx) / 2;
        const my = (sy + ty) / 2;
        const dx = tx - sx, dy = ty - sy;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const off = len * 0.12;
        const cx = mx + (-dy / len) * off;
        const cy = my + (dx / len) * off;
        return `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`;
      });
      nodeG.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    return () => sim.stop();
  }, [data, centerId, navigate]);

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>연관 기업 관계도</span>
        <div style={styles.legend}>
          {LEGEND_TYPES.map((type) => (
            <div key={type} style={styles.legendItem}>
              <div style={{ ...styles.legendDot, background: TYPE_COLORS[type] }} />
              <span>{type}</span>
            </div>
          ))}
        </div>
      </div>
      <div style={styles.graphBg}>
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={styles.svg} />
      </div>
    </div>
  );
}
