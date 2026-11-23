import { useMemo, useState } from 'react'
import { fmtDate } from '../api'
import { Empty, ErrorBox, KeyView, Loading, PageHead, Pol, RuleTag, useApi } from '../ui'

interface Val { node_id: string; polarity: string; from: string; to: string | null; status: string; rule: string; source_text: string;
  strength: number; evidence: number; confirmations: string[]; needs_confirmation: boolean }
interface Slot { key: string; who: string; concept: string; label: string; aspect: string; intent: string; context: string; values: Val[] }

export default function Timeline() {
  const { data, error, loading } = useApi<Slot[]>('/api/memory/timeline')
  const [sel, setSel] = useState<{ slot: Slot; v: Val } | null>(null)
  const [filter, setFilter] = useState<'all' | 'changed' | 'others'>('all')

  const span = useMemo(() => {
    if (!data?.length) return null
    const ts = data.flatMap(s => s.values.flatMap(v => [new Date(v.from).getTime(), v.to ? new Date(v.to).getTime() : 0]))
    const min = Math.min(...ts.filter(Boolean))
    const max = Math.max(...ts) + 14 * 864e5
    return { min, max }
  }, [data])

  if (loading && !data) return <Loading />
  const slots = (data ?? []).filter(s => filter === 'all' || (filter === 'changed' ? s.values.length > 1 : s.who !== 'self'))
  const x = (t: number) => span ? ((t - span.min) / (span.max - span.min)) * 100 : 0
  const ticks = span ? Array.from({ length: 5 }, (_, i) => span.min + (span.max - span.min) * i / 4) : []

  return (
    <>
      <PageHead title="Slot timeline"
        actions={<div className="seg" role="group" aria-label="Filter slots">
          {(['all', 'changed', 'others'] as const).map(f => (
            <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === 'all' ? 'All slots' : f === 'changed' ? 'Changed values' : 'Other people'}
            </button>))}
        </div>}>
        One row per slot key. A slot holds one value at a time; when you change your mind the old value ends (hatched) and the new one begins.
        Small marks are repeat confirmations.
      </PageHead>
      <ErrorBox error={error} />
      {!slots.length ? <div className="panel"><Empty title="No slots yet">Tell the assistant a few preferences in Chat.</Empty></div> : (
        <div className="grid-chat" style={{ gridTemplateColumns: 'minmax(0, 8fr) minmax(280px, 4fr)' }}>
          <section className="panel">
            <div className="axis"><div /><div className="marks">{ticks.map(t => <span key={t} style={{ left: `${x(t)}%` }}>{fmtDate(new Date(t).toISOString())}</span>)}</div></div>
            <div className="lanes">
              {slots.map(s => (
                <div className="lane" key={s.key}>
                  <div className="lane-label">
                    <b>{s.who !== 'self' && `${s.who}: `}{s.label}</b>
                    <div className="meta">{[s.intent !== 'standing' && s.intent, s.context !== 'any' && s.context, s.aspect !== 'general' && s.aspect].filter(Boolean).join(' · ') || 'standing preference'}</div>
                  </div>
                  <div className="track">
                    {s.values.map(v => {
                      const a = x(new Date(v.from).getTime())
                      const b = v.to ? x(new Date(v.to).getTime()) : 100
                      return (
                        <button key={v.node_id} className={`seg-v ${v.polarity} ${v.status === 'retired' ? 'retired' : ''}`}
                          style={{ left: `${a}%`, width: `${Math.max(b - a, 1.5)}%`, outline: v.needs_confirmation ? '2px solid var(--flag)' : undefined }}
                          onClick={() => setSel({ slot: s, v })} aria-label={`${s.label}: ${v.polarity} from ${fmtDate(v.from)}${v.to ? ` to ${fmtDate(v.to)}` : ''}`}>
                          {v.polarity}
                          {v.confirmations.slice(1).map(c => (
                            <span key={c} className="tick" style={{ left: `${((x(new Date(c).getTime()) - a) / Math.max(b - a, 1.5)) * 100}%` }} />
                          ))}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </section>
          <aside className="panel stack">
            {!sel ? <Empty title="Select a value">Click a coloured bar to read the statement behind it.</Empty> : (
              <>
                <div className="spread"><h2>{sel.slot.label}</h2><Pol p={sel.v.polarity} /></div>
                <KeyView k={sel.slot.key} />
                <dl className="kv">
                  <dt>Said</dt><dd>“{sel.v.source_text}”</dd>
                  <dt>From</dt><dd>{fmtDate(sel.v.from)}</dd>
                  <dt>Until</dt><dd>{sel.v.to ? fmtDate(sel.v.to) : 'still current'}</dd>
                  <dt>Rule</dt><dd><RuleTag r={sel.v.rule} /></dd>
                  <dt>Evidence</dt><dd>{sel.v.evidence} statement(s), strength {sel.v.strength}</dd>
                </dl>
                {sel.v.needs_confirmation && <span className="flagged">Waiting for your confirmation</span>}
              </>
            )}
          </aside>
        </div>
      )}
    </>
  )
}
