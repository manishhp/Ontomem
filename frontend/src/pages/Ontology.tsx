import { useEffect, useState } from 'react'
import { api, fmtDateTime } from '../api'
import { Empty, ErrorBox, Loading, PageHead, useApi } from '../ui'

interface TreeNode { id: string; label: string; alt_labels: string[]; children: TreeNode[] }

function Branch({ n, sel, onSel, depth, open: initOpen }: { n: TreeNode; sel: string | null; onSel: (id: string) => void; depth: number; open: boolean }) {
  const [open, setOpen] = useState(initOpen)
  const count = (x: TreeNode): number => x.children.reduce((a, c) => a + 1 + count(c), 0)
  return (
    <li>
      <span className="twisty" aria-hidden="true">{n.children.length ? (open ? '▾' : '▸') : '·'}</span>
      <button className="node" aria-current={sel === n.id} aria-expanded={n.children.length ? open : undefined}
        onClick={() => { onSel(n.id); if (n.children.length) setOpen(!open) }}>{n.label}</button>
      {n.children.length > 0 && <span className="count">{count(n)}</span>}
      {open && n.children.length > 0 && <ul>{n.children.map(c => <Branch key={c.id} n={c} sel={sel} onSel={onSel} depth={depth + 1} open={false} />)}</ul>}
    </li>
  )
}

export default function Ontology() {
  const [tab, setTab] = useState<'browse' | 'map' | 'review' | 'changes'>('browse')
  const summary = useApi<any>('/api/ontology', undefined, [tab])
  return (
    <>
      <PageHead title="Ontology">
        {summary.data ? <>Version {summary.data.version}: {summary.data.concepts} SKOS concepts in {summary.data.domains.length} domains, {summary.data.labels} labels, up to {summary.data.max_depth} levels deep.</> : 'The concept hierarchy that decides which preferences compete.'}
      </PageHead>
      <div className="tabs" role="tablist">
        {([['browse', 'Browse'], ['map', 'Try the mapper'], ['review', 'Review queue'], ['changes', 'Change log']] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'browse' && <Browse />}
      {tab === 'map' && <Mapper />}
      {tab === 'review' && <Review onChange={summary.reload} />}
      {tab === 'changes' && <Changes />}
    </>
  )
}

function Browse() {
  const tree = useApi<TreeNode[]>('/api/ontology/tree')
  const [sel, setSel] = useState<string | null>('Sushi')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<any[]>([])
  const concept = useApi<any>(sel ? `/api/ontology/concept/${sel}` : null)
  useEffect(() => {
    if (q.length < 2) { setHits([]); return }
    const id = setTimeout(() => api<any[]>('/api/ontology/search', { params: { q } }).then(setHits).catch(() => {}), 150)
    return () => clearTimeout(id)
  }, [q])
  if (tree.loading && !tree.data) return <Loading />
  return (
    <div className="grid-chat" style={{ gridTemplateColumns: 'minmax(280px, 5fr) minmax(0, 7fr)' }}>
      <section className="panel tree" aria-label="Concept tree">
        <input className="input" placeholder="Search concepts and synonyms" value={q} onChange={e => setQ(e.target.value)} aria-label="Search concepts" />
        {hits.length > 0 && (
          <ul style={{ padding: 0, margin: '8px 0' }}>
            {hits.slice(0, 8).map(h => <li key={h.id}><button className="node" onClick={() => { setSel(h.id); setQ('') }}>{h.label}</button> <span className="tiny muted">{h.path.join(' › ')}</span></li>)}
          </ul>
        )}
        <ul style={{ marginTop: 10 }}>{tree.data?.map(r => <Branch key={r.id} n={r} sel={sel} onSel={setSel} depth={0} open={r.id === 'Food'} />)}</ul>
      </section>
      <section className="panel stack">
        {!concept.data ? <Empty title="Pick a concept" /> : (
          <>
            <h2>{concept.data.label}</h2>
            <div className="small muted">{concept.data.path.join(' › ')} · <code>{concept.data.iri}</code></div>
            <dl className="kv">
              <dt>Synonyms</dt><dd>{concept.data.alt_labels.join(', ') || '–'}</dd>
              <dt>Broader</dt><dd>{concept.data.broader.map((b: string) => <button key={b} className="btn quiet small" onClick={() => setSel(b)}>{b}</button>)}</dd>
              <dt>Narrower</dt><dd>{concept.data.narrower.length ? concept.data.narrower.map((b: string) => <button key={b} className="btn quiet small" onClick={() => setSel(b)}>{b}</button>) : '–'}</dd>
              <dt>Related</dt><dd>{concept.data.related.join(', ') || '–'}</dd>
              <dt>Below it</dt><dd>{concept.data.descendants} concept(s)</dd>
            </dl>
            <div className="sunk small">
              A preference about <b>{concept.data.label}</b> is an exception to any opposite preference about
              {' '}{concept.data.path.slice(0, -1).join(', ') || 'nothing (it is a domain root)'}, and is overridden for everything below it
              by more specific statements.
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function Mapper() {
  const [q, setQ] = useState('chilli-heavy dishes')
  const [res, setRes] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  async function go() { try { setRes(await api('/api/ontology/map', { params: { q } })); setError(null) } catch (e: any) { setError(e.message) } }
  useEffect(() => { go() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="panel stack" style={{ maxWidth: 760 }}>
      <p className="muted">Type how someone might phrase a thing they like. The mapper tries an exact label, then the head word, then typo repair.</p>
      <div className="row"><input className="input" style={{ flex: 1 }} value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && go()} aria-label="Phrase" />
        <button className="btn primary" onClick={go}>Map it</button></div>
      <div className="row small">{['spicy thrillers', 'prawn curries', 'suhsi', 'learning thai', 'bubble tea', 'red-eye flights'].map(x =>
        <button key={x} className="btn quiet small" onClick={() => { setQ(x); setTimeout(() => api('/api/ontology/map', { params: { q: x } }).then(setRes), 0) }}>{x}</button>)}</div>
      <ErrorBox error={error} />
      {res && (
        <dl className="kv">
          <dt>Concept</dt><dd><b>{res.path.length ? res.path.join(' › ') : res.concept}</b></dd>
          <dt>Method</dt><dd>{res.method}{res.matched && ` (matched “${res.matched}”)`}, score {res.score}</dd>
          <dt>Review</dt><dd>{res.needs_review ? `would be queued: ${res.why}` : 'no review needed'}</dd>
        </dl>
      )}
    </section>
  )
}

function Review({ onChange }: { onChange: () => void }) {
  const props = useApi<any[]>('/api/ontology/proposals', { status: 'pending' })
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [manual, setManual] = useState<Record<string, string>>({})
  async function decide(id: string, body: any) {
    setBusy(id)
    try {
      const r = await api<any>(`/api/ontology/proposals/${id}`, { method: 'POST', body })
      setMsg(r.status === 'approved' ? `Approved. Ontology is now v${r.version}; re-keyed ${r.rekeyed} earlier statement(s).` : 'Rejected.')
      props.reload(); onChange()
    } catch (e: any) { setMsg(e.message) } finally { setBusy(null) }
  }
  if (props.loading && !props.data) return <Loading />
  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>Phrases from real conversations that the ontology does not cover, or only covers by their head word. Nothing is added without approval.</p>
      {msg && <div className="sunk small" role="status">{msg}</div>}
      {!props.data?.length && <div className="panel"><Empty title="Nothing to review">New phrases appear here when people mention things the ontology doesn’t know yet.</Empty></div>}
      {props.data?.map(p => (
        <div key={p.id} className="panel stack">
          <div className="spread"><h3>“{p.surface}”</h3><span className="small muted">heard {p.occurrences}× · {p.kind}</span></div>
          <table className="t">
            <thead><tr><th>Proposal</th><th>Why</th><th className="num">Score</th><th /></tr></thead>
            <tbody>
              {p.payload.map((o: any, i: number) => (
                <tr key={i}>
                  <td>{o.source === 'alias' ? <>synonym of <b>{o.parent}</b></> : <>new concept under <b>{o.parent}</b></>}</td>
                  <td className="small">{o.reasons.join('; ')}{o.checks && !o.checks.ok && <span style={{ color: 'var(--avoids)' }}> - fails checks</span>}</td>
                  <td className="num">{Number(o.score).toFixed(2)}</td>
                  <td><button className="btn small" disabled={!!busy || (o.checks && !o.checks.ok)} onClick={() => decide(p.id, { approve: true, choice: i })}>Approve</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row">
            <input className="input" style={{ maxWidth: 260 }} placeholder="or place under concept id, e.g. Beverage" value={manual[p.id] ?? ''}
              onChange={e => setManual({ ...manual, [p.id]: e.target.value })} aria-label="Manual parent concept" />
            <button className="btn" disabled={!manual[p.id] || !!busy} onClick={() => decide(p.id, { approve: true, parent: manual[p.id] })}>Place manually</button>
            <button className="btn quiet" disabled={!!busy} onClick={() => decide(p.id, { approve: false })}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  )
}

function Changes() {
  const log = useApi<any[]>('/api/ontology/changelog')
  if (log.loading && !log.data) return <Loading />
  return (
    <section className="panel">
      <ErrorBox error={log.error} />
      {!log.data?.length ? <Empty title="No changes yet">Approved proposals are recorded here with their version.</Empty> : (
        <table className="t"><thead><tr><th>Version</th><th>Change</th><th>By</th><th>When</th></tr></thead>
          <tbody>{[...log.data].reverse().map((c, i) => (
            <tr key={i}><td className="mono">{c.version}</td>
              <td>{c.added ? <>added <b>{c.label}</b> under {c.parent}</> : <>“{c.alias}” is now a synonym of <b>{c.of}</b></>}</td>
              <td>{c.reviewer}</td><td className="small">{fmtDateTime(c.at)}</td></tr>))}
          </tbody></table>
      )}
    </section>
  )
}
