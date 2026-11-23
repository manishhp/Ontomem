import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api, fmtDate, type Answer, type Clarification, type Trace } from '../api'
import PipelineRail from '../components/PipelineRail'
import AnswerView from '../components/AnswerView'
import { ErrorBox, PageHead } from '../ui'

interface Msg { id: number; who: 'me' | 'bot'; text: string; ts: string; trace?: Trace; answer?: Answer }

const DEMO_STEPS = [
  { label: '1 · Remember', text: 'I love spicy food.', result: 'A new preference is added to memory.' },
  { label: '2 · Revise', text: "Spicy food doesn't agree with me any more.", result: 'The new value replaces the old one; the old value stays in history.' },
  { label: '3 · Ask', text: 'Any dinner ideas?', result: 'Suggestions use your current memory, with reasons and avoided options.' },
]

export default function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Msg | null>(null)
  const [when, setWhen] = useState('')
  const [pending, setPending] = useState<Clarification[]>([])
  const [exampleLoaded, setExampleLoaded] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => { api<Clarification[]>('/api/clarifications').then(setPending).catch(() => {}) }, [])
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' }) }, [msgs])

  async function send(t: string) {
    if (!t.trim() || busy) return
    setBusy(true)
    setError(null)
    setExampleLoaded(false)
    const ts = when ? new Date(when).toISOString().slice(0, 19) : undefined
    const mine: Msg = { id: Date.now(), who: 'me', text: t, ts: ts ?? new Date().toISOString() }
    setMsgs(m => [...m, mine])
    setText('')
    try {
      const r = await api<{ trace: Trace; answer: Answer | null; reply: string }>('/api/chat', { method: 'POST', body: { text: t, ts } })
      const bot: Msg = { id: Date.now() + 1, who: 'bot', text: r.reply, ts: mine.ts, trace: r.trace, answer: r.answer ?? undefined }
      setMsgs(m => [...m, bot])
      setSelected(bot)
      setPending(r.trace.clarifications)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function answer(c: Clarification, keep: boolean) {
    await api(`/api/clarifications/${c.node_id}`, { method: 'POST', body: { keep_exception: keep } })
    setPending(await api('/api/clarifications'))
    setMsgs(m => [...m, { id: Date.now(), who: 'bot', ts: new Date().toISOString(),
      text: keep ? 'Thanks - I’ll keep that exception.' : 'Got it - I’ve updated that preference.' }])
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text) }
  }

  return (
    <>
      <PageHead title="Chat">Try the three steps below: add a preference, change it, then ask for a recommendation. OntoMem shows what it stored and how that memory shaped its answer.</PageHead>
      <div className="grid-chat">
        <section className="panel flush chat" aria-label="Conversation">
          <div className="log" ref={logRef} aria-live="polite">
            {msgs.length === 0 && (
              <div className="empty">
                <h3>Your input drives the demo</h3>
                <p className="small">Type any preference or question below, or choose a guided example to load it into the message box. Press Send to see the result.</p>
              </div>
            )}
            {msgs.map(m => (
              <div key={m.id} className={`bubble ${m.who}`}>
                {m.who === 'bot' && <strong className="message-label">Assistant result</strong>}
                <span dangerouslySetInnerHTML={{ __html: escapeBold(m.text) }} />
                {m.who === 'bot' && (m.trace || m.answer) && (
                  <div><button className="trace-link" onClick={() => setSelected(m)}>{selected?.id === m.id ? 'Showing details' : 'Show details'}</button></div>
                )}
                {m.who === 'me' && when && <span className="ts">{fmtDate(m.ts)}</span>}
              </div>
            ))}
            {pending.map(c => (
              <div key={c.node_id} className="clarify" role="group" aria-label="The assistant needs to check something">
                <p>{c.question}</p>
                <div className="row">
                  <button className="btn" onClick={() => answer(c, false)}>{c.drop_label}</button>
                  <button className="btn" onClick={() => answer(c, true)}>{c.keep_label}</button>
                </div>
              </div>
            ))}
          </div>
          <div className="demo-guide" aria-label="Guided demo steps">
            <div className="demo-guide-head"><strong>Guided demo</strong><span className="tiny muted">Choose a step to load its input</span></div>
            {DEMO_STEPS.map(step => <button key={step.label} className="demo-step" type="button" onClick={() => { setText(step.text); setExampleLoaded(true) }} disabled={busy}>
              <span className="demo-step-title">{step.label}</span><span className="demo-step-text">“{step.text}”</span><span className="demo-step-result">Expected: {step.result}</span>
            </button>)}
          </div>
          <ErrorBox error={error} />
          <form className="composer" onSubmit={(e: FormEvent) => { e.preventDefault(); send(text) }}>
            <div className="stack" style={{ flex: 1, gap: 6 }}>
              <textarea className="input" rows={1} value={text} onChange={e => { setText(e.target.value); setExampleLoaded(false) }} onKeyDown={onKey}
                placeholder="Type a preference or ask a question…" aria-label="Message" />
              <span className="tiny muted">{exampleLoaded ? 'Example loaded — press Send to run it.' : 'Input: a preference to remember, or a question to answer.'}</span>
              <label className="row tiny muted">Message date
                <input type="datetime-local" className="input" style={{ width: 'auto', padding: '2px 6px' }} value={when} onChange={e => setWhen(e.target.value)} />
                {when && <button type="button" className="btn quiet tiny" onClick={() => setWhen('')}>Use now</button>}
              </label>
            </div>
            <button className="btn primary" disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send'}</button>
          </form>
        </section>
        <section className="stack" aria-label="What happened">
          <div className="panel">
            <div className="spread" style={{ marginBottom: 10 }}>
              <h2>{selected?.answer ? 'Recommendation result' : 'Memory update result'}</h2>
              {selected?.trace && <span className="small muted">“{selected.trace.text}”</span>}
            </div>
            <p className="small muted result-hint">{selected?.answer ? 'The assistant used the relevant stored preferences to rank suggestions and explain exclusions.' : 'This shows the preference OntoMem extracted, the rule it applied, and what happened to the stored memory.'}</p>
            <PipelineRail trace={selected?.trace ?? null} />
          </div>
          {selected?.answer && (
            <div className="panel">
              <h2 style={{ marginBottom: 10 }}>What the answer used</h2>
              <AnswerView a={selected.answer} />
            </div>
          )}
        </section>
      </div>
    </>
  )
}

function escapeBold(s: string) {
  const esc = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}
