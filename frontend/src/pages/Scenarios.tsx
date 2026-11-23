import { useState } from 'react'
import { api, fmtDate } from '../api'
import PipelineRail from '../components/PipelineRail'
import AnswerView from '../components/AnswerView'
import { ErrorBox, Loading, PageHead, Pol, RuleTag, useApi } from '../ui'

export default function Scenarios() {
  const list = useApi<any[]>('/api/scenarios')
  const [run, setRun] = useState<any>(null)
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function play(id: string) {
    setBusy(id)
    setError(null)
    try { setRun(await api(`/api/scenarios/${id}/play`, { method: 'POST' })); setStep(0) } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }

  if (list.loading && !list.data) return <Loading />
  const s = run?.steps[step]
  return (
    <>
      <PageHead title="Scenarios">Short scripted conversations, one per rule. Each runs in a fresh sandbox memory, so your own memory is untouched.</PageHead>
      <ErrorBox error={error ?? list.error} />
      <div className="grid-chat" style={{ gridTemplateColumns: 'minmax(260px, 4fr) minmax(0, 8fr)' }}>
        <nav className="panel" aria-label="Scenarios">
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {list.data?.map(sc => (
              <li key={sc.id} style={{ borderBottom: '1px solid var(--rule)', padding: '10px 0' }}>
                <div className="spread">
                  <b>{sc.title}</b>
                  {/^R\d/.test(sc.rule) ? <RuleTag r={sc.rule} /> : <span className="chip">{sc.rule}</span>}
                </div>
                <p className="small muted" style={{ margin: '4px 0 8px' }}>{sc.summary}</p>
                <button className="btn" onClick={() => play(sc.id)} disabled={!!busy} aria-pressed={run?.scenario.id === sc.id}>
                  {busy === sc.id ? 'Playing…' : run?.scenario.id === sc.id ? 'Play again' : 'Play'}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <section className="stack">
          {!run ? <div className="panel empty"><h3>Pick a scenario</h3><p className="small">You will step through each message and see which rule fires.</p></div> : (
            <>
              <div className="panel">
                <div className="spread" style={{ marginBottom: 12 }}>
                  <h2>{run.scenario.title}</h2>
                  <div className="row">
                    <button className="btn" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>Previous</button>
                    <span className="small muted">Message {step + 1} of {run.steps.length}</span>
                    <button className="btn primary" onClick={() => setStep(Math.min(run.steps.length - 1, step + 1))} disabled={step === run.steps.length - 1}>Next message</button>
                  </div>
                </div>
                <div className="bubble me" style={{ maxWidth: '100%', marginLeft: 'auto', width: 'fit-content' }}>{s.text}<span className="ts">{fmtDate(s.ts)}</span></div>
                <div style={{ marginTop: 16 }}><PipelineRail trace={s.trace} /></div>
              </div>
              {s.answer && <div className="panel"><h2 style={{ marginBottom: 10 }}>Answer</h2><p>{s.answer.reply.replace(/\*\*/g, '')}</p><AnswerView a={s.answer} /></div>}
              {step === run.steps.length - 1 && (
                <div className="panel">
                  <h3>Final memory</h3>
                  <ul className="small" style={{ paddingLeft: 18 }}>
                    {run.memory.active.map((n: any) => (
                      <li key={n.id}>{n.key.who !== 'self' && <b>{n.key.who}: </b>}<Pol p={n.polarity} /> {n.label}
                        {n.key.intent !== 'standing' && ` (${n.key.intent})`}{n.key.context !== 'any' && ` (${n.key.context})`}
                        {n.key.aspect !== 'general' && ` (${n.key.aspect})`}{n.needs_confirmation && <> <span className="flagged">to confirm</span></>}</li>
                    ))}
                    {run.memory.history.map((n: any) => <li key={n.id} className="retired">{n.polarity} {n.label} - retired {fmtDate(n.valid_to)}</li>)}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  )
}
