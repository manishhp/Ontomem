import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape, { type Core } from 'cytoscape'
import { api, fmtDate, fmtDateTime } from '../api'
import { ErrorBox, KeyView, Loading, PageHead, Pol, RuleTag, useApi } from '../ui'

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

function style(): any[] {
  const pol = { likes: css('--likes'), dislikes: css('--dislikes'), avoids: css('--avoids'), neutral: css('--neutral') }
  return [
    { selector: 'node', style: { label: 'data(label)', 'font-family': 'Atkinson Hyperlegible, sans-serif', 'font-size': 15, color: css('--ink'), 'text-background-color': css('--panel'), 'text-background-opacity': 0.85, 'text-background-padding': '2px',
      'text-valign': 'bottom', 'text-margin-y': 4, 'text-wrap': 'wrap', 'text-max-width': 130, 'background-color': css('--panel'),
      'border-width': 2, 'border-color': css('--rule-strong'), width: 22, height: 22 } },
    { selector: 'node[kind="user"]', style: { 'background-color': css('--ink'), 'border-color': css('--ink'), width: 34, height: 34, color: css('--ink'), 'font-weight': 700 } },
    { selector: 'node[kind="role"]', style: { 'background-color': css('--accent'), 'border-color': css('--accent'), width: 24, height: 24, shape: 'round-rectangle' } },
    { selector: 'node[kind="concept"]', style: { shape: 'round-rectangle', width: 'label', height: 18, padding: 6, 'text-valign': 'center', 'text-margin-y': 0,
      'background-color': css('--sunk'), 'border-width': 1, 'border-color': css('--rule-strong'), 'font-size': 14, 'text-background-opacity': 0 } },
    { selector: 'node[kind="concept"][?ancestor]', style: { color: css('--ink-3'), 'border-style': 'dashed' } },
    { selector: 'node[kind="unmapped"]', style: { shape: 'round-rectangle', width: 'label', height: 18, padding: 6, 'text-valign': 'center', 'text-margin-y': 0,
      'background-color': css('--dislikes-soft'), 'border-color': css('--dislikes'), 'border-style': 'dashed', 'font-size': 12, 'text-background-opacity': 0 } },
    ...Object.entries(pol).map(([k, c]) => ({ selector: `node[kind="preference"][polarity="${k}"]`, style: { 'background-color': c, 'border-color': c } })),
    { selector: 'node[kind="preference"][polarity="avoids"]', style: { shape: 'diamond', width: 26, height: 26 } },
    { selector: 'node[kind="preference"][status="retired"]', style: { opacity: 0.35, 'border-style': 'dashed', 'background-opacity': 0.25 } },
    { selector: 'node[kind="preference"][?needs_confirmation]', style: { 'border-color': css('--flag'), 'border-width': 4 } },
    { selector: 'node:selected', style: { 'overlay-color': css('--accent'), 'overlay-opacity': 0.15, 'overlay-padding': 6 } },
    { selector: 'edge', style: { width: 1.4, 'line-color': css('--rule-strong'), 'curve-style': 'bezier', 'target-arrow-shape': 'none' } },
    { selector: 'edge[kind="broader"]', style: { 'line-style': 'dashed', 'target-arrow-shape': 'triangle', 'target-arrow-color': css('--rule-strong'), 'arrow-scale': 0.7 } },
    { selector: 'edge[kind="supersededBy"]', style: { 'line-color': css('--dislikes'), 'target-arrow-shape': 'triangle', 'target-arrow-color': css('--dislikes'), width: 2,
      label: 'superseded by', 'font-size': 9, color: css('--dislikes'), 'text-rotation': 'autorotate', 'text-background-color': css('--panel'), 'text-background-opacity': 1 } },
    { selector: 'edge[kind="exceptionTo"]', style: { 'line-color': css('--likes'), 'target-arrow-shape': 'triangle', 'target-arrow-color': css('--likes'), width: 2,
      label: 'exception to', 'font-size': 9, color: css('--likes'), 'text-rotation': 'autorotate', 'text-background-color': css('--panel'), 'text-background-opacity': 1 } },
    { selector: 'edge[kind="refines"]', style: { 'line-color': css('--accent'), 'line-style': 'dotted', 'target-arrow-shape': 'triangle', 'target-arrow-color': css('--accent') } },
  ]
}

export default function MemoryGraph() {
  const mem = useApi<any>('/api/memory')
  const [t, setT] = useState(1000)
  const [history, setHistory] = useState(true)
  const [ancestors, setAncestors] = useState(false)
  const [graph, setGraph] = useState<any>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)

  const range = useMemo(() => {
    const first = mem.data?.first_event ? new Date(mem.data.first_event).getTime() : null
    const last = mem.data?.last_event ? new Date(mem.data.last_event).getTime() : null
    return first && last ? { first, last } : null
  }, [mem.data])
  const asOf = range && t < 1000 ? new Date(range.first + (range.last - range.first) * t / 1000) : null

  useEffect(() => {
    const id = setTimeout(() => {
      api('/api/memory/graph', { params: { as_of: asOf ? toLocalIso(asOf) : undefined, history, ancestors } })
        .then(g => setGraph({ ...g, full: ancestors })).catch(e => setError(e.message))
    }, 120)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, history, ancestors, mem.data])

  useEffect(() => {
    if (!box.current || !graph) return
    const els = [...graph.nodes.map((n: any) => ({ data: n })), ...graph.edges.map((e: any) => ({ data: e }))]
    if (!cy.current) {
      cy.current = cytoscape({ container: box.current, style: style(), wheelSensitivity: 0.25, minZoom: 0.2, maxZoom: 3 })
      cy.current.on('tap', 'node[kind="preference"]', e => setSelected(e.target.data('node_id')))
    }
    const c = cy.current
    c.elements().remove()
    c.add(els)
    const ring: Record<string, number> = { user: 4, role: 3, preference: 2, concept: 1, unmapped: 1 }
    const layout = graph.full
      ? { name: 'cose', animate: false, nodeRepulsion: () => 14000, idealEdgeLength: () => 85, padding: 24, nodeDimensionsIncludeLabels: true, randomize: false }
      // you at the centre, preference nodes on the inner ring, the concepts they are about on the outer ring
      : { name: 'concentric', animate: false, padding: 30, minNodeSpacing: 22, spacingFactor: 0.9, levelWidth: () => 1, startAngle: -Math.PI / 2,
          concentric: (n: any) => ring[n.data('kind')] ?? 1, nodeDimensionsIncludeLabels: false }
    c.layout(layout as any).run()
  }, [graph])

  useEffect(() => () => { cy.current?.destroy(); cy.current = null }, [])

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    api(`/api/memory/node/${selected}`).then(setDetail).catch(e => setError(e.message))
  }, [selected])

  return (
    <>
      <PageHead title="Memory graph">
        Each dot is a reified preference node, linked to you (or the person it is about) and to its SKOS concept. Drag the
        slider to see what the memory believed at any earlier moment.
      </PageHead>
      <ErrorBox error={error ?? mem.error} />
      <div className="grid-chat" style={{ gridTemplateColumns: 'minmax(0, 8fr) minmax(300px, 4fr)' }}>
        <section className="panel flush">
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--rule)' }} className="stack">
            <div className="time-ctl">
              <label htmlFor="asof" className="small"><b>{asOf ? `As of ${fmtDateTime(asOf.toISOString())}` : 'Now'}</b></label>
              <input id="asof" type="range" min={0} max={1000} value={t} onChange={e => setT(+e.target.value)} disabled={!range} />
              <label className="row small"><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> show retired values</label>
              <label className="row small"><input type="checkbox" checked={ancestors} onChange={e => setAncestors(e.target.checked)} /> full SKOS hierarchy</label>
            </div>
            <div className="legend">
              <span><i style={{ background: 'var(--likes)' }} />likes</span>
              <span><i style={{ background: 'var(--dislikes)' }} />dislikes</span>
              <span><i style={{ background: 'var(--avoids)', borderRadius: 1, transform: 'rotate(45deg) scale(.85)' }} />avoids</span>
              <span><i style={{ background: 'transparent', border: '2px dashed var(--ink-3)' }} />retired</span>
              <span><i style={{ background: 'transparent', border: '3px solid var(--flag)' }} />needs confirmation</span>
              <span><i style={{ background: 'var(--sunk)', borderRadius: 3, border: '1px solid var(--rule-strong)' }} />SKOS concept</span>
            </div>
          </div>
          <div className="graph-wrap">
            {mem.loading && !graph && <Loading what="Loading memory" />}
            <div ref={box} className="cy" role="img" aria-label="Memory graph. Use the side panel or the slot timeline for a text view." />
          </div>
        </section>
        <aside className="panel stack" aria-label="Selected preference">
          {!detail && <div className="empty"><h3>Pick a preference</h3><p className="small">Click a coloured node to see its slot key, provenance, history and RDF.</p>
            {mem.data && <div className="stat-line" style={{ justifyContent: 'center' }}>
              <div><b>{mem.data.stats.active}</b><span>active</span></div>
              <div><b>{mem.data.stats.retired}</b><span>retired</span></div>
              <div><b>{mem.data.stats.needs_confirmation}</b><span>to confirm</span></div>
            </div>}</div>}
          {detail && <NodeDetail d={detail} onPick={setSelected} />}
        </aside>
      </div>
    </>
  )
}

function NodeDetail({ d, onPick }: { d: any; onPick: (id: string) => void }) {
  const n = d.node
  return (
    <>
      <div className="spread"><h2 className={n.status === 'retired' ? 'retired' : ''}>{n.label}</h2><Pol p={n.polarity} /></div>
      {n.needs_confirmation && <span className="flagged">Needs confirmation (rule R3)</span>}
      <KeyView k={n.key} />
      <dl className="kv">
        <dt>You said</dt><dd>“{n.source_text}”</dd>
        <dt>When</dt><dd>{fmtDate(n.stated_at)}{n.valid_to && ` - retired ${fmtDate(n.valid_to)}`}</dd>
        <dt>Concept</dt><dd>{n.path.join(' › ') || n.key.what}</dd>
        <dt>Strength</dt><dd>{n.strength} (confidence now {n.confidence})</dd>
        <dt>Evidence</dt><dd>{n.evidence.length} statement(s)</dd>
        <dt>Created by</dt><dd><RuleTag r={n.created_by_rule} /> {n.rule_name}</dd>
        <dt>Reason</dt><dd>{n.reason}</dd>
      </dl>
      {d.chain.length > 1 && (
        <div>
          <h3>This slot over time</h3>
          <ol className="small" style={{ paddingLeft: 18 }}>
            {d.chain.map((c: any) => (
              <li key={c.id}><button className="btn quiet small" onClick={() => onPick(c.id)}>
                <span className={c.status === 'retired' ? 'retired' : ''}>{c.polarity}</span></button> {fmtDate(c.valid_from)} - “{c.source_text}”</li>
            ))}
          </ol>
        </div>
      )}
      {(d.broader.length > 0 || d.narrower.length > 0) && (
        <div className="small">
          {d.broader.map((b: any) => <div key={b.id}>Narrower than <b>{b.polarity} {b.label}</b> - {b.polarity !== n.polarity ? 'exception (R2)' : 'refinement'}</div>)}
          {d.narrower.map((b: any) => <div key={b.id}>Broader than <b>{b.polarity} {b.label}</b></div>)}
        </div>
      )}
      <details>
        <summary className="small">RDF (Turtle) for this node</summary>
        <pre className="code">{d.turtle}</pre>
      </details>
    </>
  )
}

function toLocalIso(d: Date) {
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
