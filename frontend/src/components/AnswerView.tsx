import { useState } from 'react'
import type { Answer, Recommendation } from '../api'
import { fmtDate } from '../api'

export function ProofTree({ proof }: { proof: any }) {
  if (!proof) return null
  return (
    <div className="proof">
      <div className="claim">{proof.claim}</div>
      <div className="rulename">{proof.rule}</div>
      {proof.children?.map((ch: any, i: number) => (
        <div key={i} className="proof">
          <div className="claim">{ch.claim}</div>
          <div className="rulename">{ch.rule}</div>
          {ch.premises.map((p: any) => (
            <div key={p.node_id} className="premise">
              <span className="what"><b>{p.statement}</b></span> <span className="muted small">({p.path})</span>
              <div className="small">You said <q>{p.source_text}</q> on {fmtDate(p.stated_at)}</div>
            </div>
          ))}
          {ch.defeated.map((p: any) => (
            <div key={p.node_id} className="premise defeated">
              <span className="what">{p.statement}</span> <span className="small muted">- {p.why}</span>
            </div>
          ))}
          {ch.suspended.map((p: any) => (
            <div key={p.node_id} className="premise suspended">
              <span className="what">{p.statement}</span> <span className="small">- {p.why}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Rec({ r }: { r: Recommendation }) {
  const [open, setOpen] = useState(false)
  return (
    <li style={{ padding: '10px 0', borderBottom: '1px solid var(--rule)' }}>
      <div className="spread">
        <div>
          <b>{r.item.name}</b>
          <div className="small muted">{r.item.description}</div>
        </div>
        <button className="btn quiet small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Hide why' : 'Why?'}</button>
      </div>
      {r.reasons.length > 0 && <div className="small" style={{ marginTop: 4 }}>{r.reasons.join(' · ')}</div>}
      {open && (
        <div style={{ marginTop: 8 }}>
          <ProofTree proof={r.proof} />
          {r.counterfactual && <div className="cf">{r.counterfactual.summary}</div>}
        </div>
      )}
    </li>
  )
}

export default function AnswerView({ a }: { a: Answer }) {
  return (
    <div className="stack">
      <div>
        <h3>Memory block sent to the model</h3>
        <p className="small muted">{a.memory_block.items.length} of {a.memory_block.candidates} relevant preferences fit the
          {' '}{a.memory_block.budget_tokens}-token budget ({a.memory_block.used_tokens} used). Superseded values never appear here.</p>
        <pre className="code">{a.memory_block.block || '(nothing relevant in memory yet)'}</pre>
      </div>
      <div>
        <h3>Suggestions</h3>
        {a.recommendations.length ? <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>{a.recommendations.map(r => <Rec key={r.item.id} r={r} />)}</ul>
          : <p className="small muted">Nothing to suggest in this area yet.</p>}
      </div>
      {a.excluded_count > 0 && (
        <details>
          <summary className="small">{a.excluded_count} option(s) left out because of things you avoid</summary>
          <ul className="small">{a.excluded.map(x => <li key={x.item.id}>{x.item.name} - {x.reasons.join(', ')}</li>)}</ul>
        </details>
      )}
      <div className="tiny muted">read path: {a.latency_ms} ms · domains {a.query.domains.join(', ') || 'any'} · context {a.query.context} · for {a.query.who}</div>
    </div>
  )
}
