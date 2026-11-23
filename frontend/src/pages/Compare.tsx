import { useState } from 'react'
import { api } from '../api'
import { ErrorBox, PageHead } from '../ui'

const PRESETS: Record<string, { label: string; turns: string[]; query: string }> = {
  wife: { label: 'Someone else’s preference', query: 'Any dinner ideas?',
    turns: ['I love mushrooms.', 'My wife hates mushrooms.'] },
  paris: { label: 'Past trip vs future plan', query: 'Any trip ideas?',
    turns: ['We loved our Paris trip last year!', "Honestly I can't stand Paris these days.", 'Planning to go back to Paris next summer.'] },
  paraphrase: { label: 'Same thing, new words', query: 'Any dinner ideas?',
    turns: ['I love spicy food.', 'Anything spicy is off the table now.'] },
  context: { label: 'Work vs home', query: 'What should I get for a work lunch?',
    turns: ['At home, I avoid coffee.', 'At work I love coffee.'] },
  price: { label: 'Liking vs price', query: 'Suggest something for lunch.',
    turns: ['I love sushi.', 'Sushi is way too expensive though.'] },
}

export default function Compare() {
  const [preset, setPreset] = useState('wife')
  const [text, setText] = useState(PRESETS.wife.turns.join('\n'))
  const [query, setQuery] = useState(PRESETS.wife.query)
  const [baseline, setBaseline] = useState('S1')
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function pick(k: string) {
    setPreset(k)
    setText(PRESETS[k].turns.join('\n'))
    setQuery(PRESETS[k].query)
    setRes(null)
  }

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const turns = text.split('\n').map(t => t.trim()).filter(Boolean).map((t, i) => ({ text: t, ts: `2026-0${Math.min(9, i + 1)}-01T19:00:00` }))
      setRes(await api('/api/compare', { method: 'POST', body: { turns, baseline, query } }))
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <PageHead title="Compare">Run the same conversation through OntoMem and a baseline memory. Differences are highlighted at every step.</PageHead>
      <div className="panel stack" style={{ marginBottom: 18 }}>
        <div className="row" role="group" aria-label="Example conversations">
          {Object.entries(PRESETS).map(([k, p]) => <button key={k} className="btn" aria-pressed={preset === k} onClick={() => pick(k)}
            style={preset === k ? { borderColor: 'var(--ink)' } : undefined}>{p.label}</button>)}
        </div>
        <div className="grid-2">
          <label className="field">Conversation, one message per line
            <textarea className="input mono" rows={4} value={text} onChange={e => setText(e.target.value)} />
          </label>
          <div className="stack">
            <label className="field">Then ask
              <input className="input" value={query} onChange={e => setQuery(e.target.value)} />
            </label>
            <label className="field">Baseline
              <select className="select input" value={baseline} onChange={e => setBaseline(e.target.value)}>
                <option value="S1">S1 - rule extractor, keyed by the words used</option>
                <option value="S0">S0 - append-only (never resolves updates)</option>
              </select>
            </label>
            <button className="btn primary" onClick={run} disabled={busy}>{busy ? 'Running…' : 'Run both'}</button>
          </div>
        </div>
        <ErrorBox error={error} />
      </div>
      {res && (
        <div className="stack">
          {res.steps.map((s: any, i: number) => (
            <div key={i} className="panel">
              <div className="bubble me" style={{ maxWidth: '100%', width: 'fit-content', marginBottom: 12 }}>{s.text}</div>
              <div className="grid-2">
                <Side title="OntoMem" active={s.ours.active} only={s.only_ours} decisions={s.ours.decisions} />
                <Side title={res.baseline} active={s.base.active} only={s.only_base} decisions={s.base.decisions} />
              </div>
            </div>
          ))}
          {res.query && (
            <div className="panel grid-2">
              <div><h3>OntoMem suggests for “{res.query}”</h3><ol className="small">{res.ours_recommendations.map((r: string) => <li key={r}>{r}</li>)}</ol></div>
              <div><h3>{res.baseline} suggests</h3><ol className="small">{res.base_recommendations.map((r: string) => <li key={r}>{r}</li>)}</ol></div>
            </div>
          )}
        </div>
      )}
    </>
  )
}

function Side({ title, active, only, decisions }: { title: string; active: string[]; only: string[]; decisions: any[] }) {
  return (
    <div className="sunk">
      <div className="spread"><b>{title}</b><span className="small muted">{decisions.map(d => d.decision).join(', ') || 'no preference found'}</span></div>
      {decisions.filter(d => d.target).map((d, i) => <div key={i} className="small" style={{ color: 'var(--dislikes)' }}>retired “{d.target}”</div>)}
      <ul className="small" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
        {active.map(a => <li key={a} style={only.includes(a) ? { fontWeight: 700, color: 'var(--accent)' } : undefined}>{a}{only.includes(a) && ' - only here'}</li>)}
      </ul>
    </div>
  )
}
