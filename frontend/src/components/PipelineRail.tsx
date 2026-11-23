import { useEffect, useState } from 'react'
import type { Candidate, Trace } from '../api'
import { KeyView, Pol, RuleTag } from '../ui'

const METHOD: Record<string, string> = {
  exact: 'exact label match',
  head: 'matched by its head word',
  fuzzy: 'typo-tolerant match',
  embedding: 'embedding similarity',
  unmapped: 'not in the ontology yet - queued for review',
}

function Stop({ name, state, ms, children, delay }: { name: string; state: string; ms?: number; children: React.ReactNode; delay: number }) {
  return (
    <div className={`stop ${state} arrive`} style={{ animationDelay: `${delay}ms` }}>
      <div className="stop-name">{name}{ms !== undefined && <span className="ms">{ms.toFixed(2)} ms</span>}</div>
      <div className="stop-body">{children}</div>
    </div>
  )
}

function CandidateRail({ c, timings }: { c: Candidate; timings: Record<string, number> }) {
  const r = c.raw
  const v = c.validation
  const out = c.outcome
  const d = 90
  return (
    <div className="rail">
      <Stop name="Extract" state="ok" ms={timings.extract} delay={0}>
        <div className="row"><Pol p={r.polarity} /> <b>“{r.surface}”</b></div>
        <div className="small" style={{ marginTop: 4 }}>
          who <b>{r.who}</b> · intent <b>{r.intent}</b> · context <b>{r.context}</b> · aspect <b>{r.aspect}</b>
          {r.reason && r.reason !== 'unspecified' && <> · reason <b>{r.reason}</b></>}
          {' '}· strength <b>{Number(r.strength).toFixed(2)}</b>{r.hedged && ' (hedged)'}
        </div>
        <div className="tiny muted">extractor: {r.extractor ?? 'rule-v1'}</div>
      </Stop>
      <Stop name="Map to concept" state={c.mapping.concept.startsWith('unmapped:') ? 'warn' : 'ok'} ms={timings.map} delay={d}>
        {c.mapping.path.length ? (
          <div className="path">{c.mapping.path.map((p, i) => <span key={p}>{i > 0 && ' › '}{i === c.mapping.path.length - 1 ? <b>{p}</b> : p}</span>)}</div>
        ) : <div className="path"><b>{c.mapping.concept}</b></div>}
        <div className="small">{METHOD[c.mapping.method] ?? c.mapping.method}{c.mapping.matched && c.mapping.method !== 'exact' ? ` (“${c.mapping.matched}”)` : ''}</div>
      </Stop>
      <Stop name="SHACL validation" state={c.accepted ? (c.repairs.length ? 'warn' : 'ok') : 'bad'} ms={timings.validate} delay={2 * d}>
        {v && v.conforms !== undefined && (v.conforms
          ? <span>Conforms to PreferenceShape ({v.validator}).</span>
          : <div>
              {v.violations.map((x, i) => <div key={i} className="small">✗ <b>{x.field}</b>: {x.message} {x.value !== null && x.value !== undefined && <code>{String(x.value)}</code>}</div>)}
            </div>)}
        {c.repairs.map(rp => (
          <div key={rp.attempt} className="small">repair {rp.attempt}: {rp.validation.conforms ? 'fixed ✓' : 'still invalid'}</div>
        ))}
        {!c.accepted && <div className="small"><b>Rejected</b> - nothing was written.</div>}
      </Stop>
      <Stop name="5-part slot key" state={c.key ? 'ok' : 'pending'} delay={3 * d}>
        {c.key ? <KeyView k={c.key} /> : '–'}
      </Stop>
      <Stop name="Conflict rules" state={out ? 'ok' : 'pending'} ms={timings.reason} delay={4 * d}>
        {out?.firings.map((f, i) => (
          <div key={i} className="firing"><RuleTag r={f.rule} /><span>{f.message}</span></div>
        ))}
      </Stop>
      <Stop name="Memory graph" state={out ? 'ok' : 'pending'} delay={5 * d}>
        {out && (
          <span>
            {out.decision === 'new' && <>Created preference node <code>{out.node_id}</code>.</>}
            {out.decision === 'supersede' && <>Created <code>{out.node_id}</code>; <code>{out.retired}</code> retired to history.</>}
            {out.decision === 'reinforce' && <>Reinforced <code>{out.node_id}</code> (no new node).</>}
            {out.flagged.length > 0 && <> <span className="flagged">{out.flagged.length} flagged for confirmation</span></>}
          </span>
        )}
      </Stop>
    </div>
  )
}

export default function PipelineRail({ trace }: { trace: Trace | null }) {
  const [i, setI] = useState(0)
  useEffect(() => setI(0), [trace?.turn_id])
  if (!trace) {
    return (
      <div className="empty">
        <h3>Every message goes down the line</h3>
        <p className="small">Say something you like or avoid. Each stop - extraction, concept mapping, SHACL, the slot key,
          the conflict rules - will show exactly what it decided.</p>
      </div>
    )
  }
  if (!trace.candidates.length) {
    return <div className="empty"><h3>No preference in “{trace.text}”</h3><p className="small">Requests and small talk pass straight through to the read path.</p></div>
  }
  const c = trace.candidates[Math.min(i, trace.candidates.length - 1)]
  return (
    <div key={trace.turn_id}>
      {trace.candidates.length > 1 && (
        <div className="cand-tabs" role="group" aria-label="Preferences found in this message">
          {trace.candidates.map((x, j) => (
            <button key={j} aria-pressed={j === i} onClick={() => setI(j)}>{x.raw.polarity} {x.raw.surface}</button>
          ))}
        </div>
      )}
      <CandidateRail key={`${trace.turn_id}-${i}`} c={c} timings={trace.timings_ms} />
    </div>
  )
}
