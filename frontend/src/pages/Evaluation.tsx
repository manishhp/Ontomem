import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ComposedChart, ErrorBar, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { api, pct } from '../api'
import { Empty, ErrorBox, Loading, PageHead, useApi } from '../ui'

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']
const MAIN = ['S0-append-only', 'S1-rule-surface', 'S5-learned', 'S3-OntoMem', 'S4-oracle']
const NAMES: Record<string, string> = { 'S0-append-only': 'S0 append-only', 'S1-rule-surface': 'S1 rule surface key', 'S5-learned': 'S5 learned keyer',
  'S3-OntoMem': 'S3 OntoMem', 'S4-oracle': 'S4 oracle keys' }
const COLS: [string, string, (x: any) => string][] = [
  ['missed_merge_rate_p', 'Missed merges p', x => pct(x)], ['false_merge_rate_q', 'False merges q', x => pct(x, 2)],
  ['stale_exposure', 'Stale exposure', x => pct(x)], ['contamination', 'Contamination', x => pct(x)],
  ['clean_retrieval', 'Clean retrieval', x => pct(x)], ['ndcg5', 'NDCG@5', x => (x == null ? '–' : x.toFixed(3))],
  ['harmful5', 'Harmful@5', x => pct(x)], ['ms_per_write', 'ms / write', x => (x == null ? '–' : x.toFixed(3))],
]

export default function Evaluation() {
  const [tab, setTab] = useState<'results' | 'ablation' | 'theory' | 'evolution' | 'failures' | 'figures' | 'run'>('results')
  const res = useApi<any>('/api/eval/results')
  return (
    <>
      <PageHead title="Evaluation lab">
        PrefRev test split: 150 synthetic users, about 3,000 statements and 1,500 probes, every slot key known.
        Headline numbers are counted against gold keys - no language model grades anything.
      </PageHead>
      <div className="tabs" role="tablist">
        {([['results', 'Main results'], ['ablation', 'Ablation'], ['theory', 'p/q model'], ['evolution', 'Ontology growth'],
          ['failures', 'Failure browser'], ['figures', 'Report figures'], ['run', 'Run an experiment']] as const).map(([k, l]) =>
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      <ErrorBox error={res.error} />
      {res.loading && !res.data && <Loading what="Loading results" />}
      {res.data && tab === 'results' && <Results r={res.data} />}
      {res.data && tab === 'ablation' && <Ablation r={res.data} />}
      {res.data && tab === 'theory' && <Theory r={res.data} />}
      {res.data && tab === 'evolution' && <Evolution r={res.data} />}
      {tab === 'failures' && <Failures />}
      {tab === 'figures' && <Figures />}
      {tab === 'run' && <Run />}
    </>
  )
}

function Table({ rows, highlight = 'S3-OntoMem' }: { rows: any[]; highlight?: string }) {
  return (
    <div className="scroll-x">
      <table className="t">
        <thead><tr><th>System</th>{COLS.map(([k, l]) => <th key={k} className="num">{l}</th>)}</tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.system} className={r.system.startsWith(highlight) ? 'hl' : ''}>
            <td>{NAMES[r.system] ?? r.system}</td>
            {COLS.map(([k, , f]) => <td key={k} className="num" title={r[`${k}_ci`] ? `95% CI ${pct(r[`${k}_ci`][0])} - ${pct(r[`${k}_ci`][1])}` : undefined}>{f(r[k])}</td>)}
          </tr>))}
        </tbody>
      </table>
    </div>
  )
}

function CleanChart({ rows }: { rows: any[] }) {
  const data = MAIN.map(s => rows.find(r => r.system === s)).filter(Boolean).map((r: any) => ({
    name: NAMES[r.system], v: +(100 * r.clean_retrieval).toFixed(1), sys: r.system,
    err: r.clean_retrieval_ci ? [100 * (r.clean_retrieval - r.clean_retrieval_ci[0]), 100 * (r.clean_retrieval_ci[1] - r.clean_retrieval)] : [0, 0],
  }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} layout="vertical" margin={{ left: 20, right: 30 }}>
        <CartesianGrid horizontal={false} stroke={css('--rule')} />
        <XAxis type="number" domain={[0, 100]} unit="%" stroke={css('--ink-3')} fontSize={12} />
        <YAxis type="category" dataKey="name" width={150} stroke={css('--ink-3')} fontSize={12} />
        <Tooltip formatter={(v: any) => [`${v}%`, 'clean retrieval']} contentStyle={{ background: css('--panel'), border: `1px solid ${css('--rule')}` }} />
        <Bar dataKey="v" radius={[0, 4, 4, 0]} barSize={22}>
          {data.map(d => <Cell key={d.sys} fill={d.sys.startsWith('S3') ? css('--accent') : d.sys.startsWith('S4') ? css('--rule-strong') : css('--ink-3')} />)}
          <ErrorBar dataKey="err" direction="x" stroke={css('--ink')} width={6} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function Results({ r }: { r: any }) {
  const [mode, setMode] = useState<'modeA' | 'modeB'>('modeA')
  const d = r[mode]
  if (!d) return <Empty title="No results yet">Run <code>make bench</code> to produce them.</Empty>
  const main = d.table.filter((x: any) => MAIN.includes(x.system))
  const s3 = d.table.find((x: any) => x.system === 'S3-OntoMem')
  const tests = d.tests ?? {}
  return (
    <div className="stack">
      <div className="spread">
        <div className="seg" role="group" aria-label="Setting">
          <button aria-pressed={mode === 'modeA'} onClick={() => setMode('modeA')}>Gold extraction (isolates keying)</button>
          <button aria-pressed={mode === 'modeB'} onClick={() => setMode('modeB')}>End-to-end from raw text</button>
        </div>
        <span className="small muted">{d.meta.episodes} users · {d.meta.gold_statements} statements · run {d.meta.created}</span>
      </div>
      <div className="grid-2">
        <section className="panel">
          <h2>Clean retrieval</h2>
          <p className="small">Share of requests whose memory block has every current preference, no outdated value and nothing about someone else. Bars show 95% cluster-bootstrap intervals.</p>
          <CleanChart rows={d.table} />
        </section>
        <section className="panel stack">
          <h2>What this shows</h2>
          {mode === 'modeA' ? (
            <p>With extraction held perfect, OntoMem keeps {pct(s3?.clean_retrieval)} of memory blocks clean, against {pct(main.find((x: any) => x.system === 'S1-rule-surface')?.clean_retrieval)} for
              the rule baseline and {pct(main.find((x: any) => x.system === 'S5-learned')?.clean_retrieval)} for a classifier trained without the ontology. Every remaining OntoMem error comes from mapping
              an unusual phrase to the wrong concept - none from the key or the rules.</p>
          ) : (
            <p>From raw text, extraction becomes the bottleneck for every system. OntoMem stays within a point of oracle keys given the same extractor
              (McNemar p = {tests['S4-oracle']?.p_holm?.toFixed(2) ?? '–'}), and well above every baseline.</p>
          )}
          <table className="t small"><thead><tr><th>OntoMem vs</th><th className="num">only OntoMem clean</th><th className="num">only other clean</th><th className="num">p (Holm)</th></tr></thead>
            <tbody>{Object.entries(tests).filter(([k]) => MAIN.includes(k)).map(([k, t]: any) => (
              <tr key={k}><td>{NAMES[k]}</td><td className="num">{t.a_only}</td><td className="num">{t.b_only}</td><td className="num">{t.p_holm < 1e-4 ? '< 0.0001' : t.p_holm.toFixed(3)}</td></tr>))}
            </tbody></table>
        </section>
      </div>
      <section className="panel"><h2 style={{ marginBottom: 10 }}>All systems</h2><Table rows={main} /></section>
      {d.error_stages?.['S3-OntoMem'] && (
        <section className="panel">
          <h2>Where OntoMem’s merge errors come from</h2>
          <table className="t small" style={{ maxWidth: 520, marginTop: 8 }}><tbody>
            {Object.entries(d.error_stages['S3-OntoMem']).sort((a: any, b: any) => b[1] - a[1]).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>)}
          </tbody></table>
        </section>
      )}
    </div>
  )
}

function Ablation({ r }: { r: any }) {
  const d = r.modeA
  if (!d) return <Empty title="No results yet" />
  const full = d.table.find((x: any) => x.system === 'S3-OntoMem')
  const abl = d.table.filter((x: any) => x.system.startsWith('S3 -'))
  const noise = r.noise
  return (
    <div className="stack">
      <section className="panel">
        <h2>Switching off one part at a time</h2>
        <p className="small">Same gold-extraction setting. Each row removes one component from OntoMem.</p>
        <Table rows={[full, ...abl]} />
        <ul className="small" style={{ marginTop: 12 }}>
          {abl.map((a: any) => {
            const dc = 100 * (full.clean_retrieval - a.clean_retrieval)
            const dh = 100 * ((a.harmful5 ?? 0) - (full.harmful5 ?? 0))
            return <li key={a.system}><b>{a.system.replace('S3 -', 'Without ')}</b>: {dc > 0.05 ? `${dc.toFixed(1)} points less clean retrieval` : 'no change in clean retrieval'}
              {dh > 0.05 ? `, ${dh.toFixed(1)} points more harmful suggestions` : ''}.</li>
          })}
        </ul>
      </section>
      {noise && (
        <section className="panel">
          <h2>SHACL under noisy extraction</h2>
          <p className="small">15% of candidates were corrupted the way small language models get JSON wrong (bad enum values, “high” instead of a number, …).
            With SHACL, every malformed candidate is caught and repaired before it reaches memory.</p>
          <Table rows={noise.table} highlight="S3-OntoMem" />
        </section>
      )}
    </div>
  )
}

function Theory({ r }: { r: any }) {
  const rows = r.sweep?.sweep as any[] | undefined
  const data = useMemo(() => {
    if (!rows) return []
    const ps = [...new Set(rows.map(x => x.p))].sort()
    return ps.map(p => {
      const o: any = { p }
      for (const x of rows.filter(y => y.p === p)) { o[`a${x.q}`] = +(100 * x.analytic).toFixed(2); o[`s${x.q}`] = +(100 * x.simulated).toFixed(2) }
      return o
    })
  }, [rows])
  if (!rows) return <Empty title="No sweep yet">Run <code>make sweep</code>.</Empty>
  const qs = [...new Set(rows.map(x => x.q))].sort()
  const err = rows.map(x => Math.abs(x.simulated - x.analytic))
  return (
    <section className="panel">
      <h2>How key-assignment errors turn into stale memory</h2>
      <p className="small">If an LLM misses a merge with probability p and files a statement under the wrong slot with probability q,
        the expected share of stale memory blocks follows a closed form (lines). Simulation on PrefRev (dots) agrees within
        {' '}{(100 * err.reduce((a, b) => a + b, 0) / err.length).toFixed(2)} points on average.</p>
      <ResponsiveContainer width="100%" height={340}>
        <ComposedChart data={data} margin={{ right: 20 }}>
          <CartesianGrid stroke={css('--rule')} />
          <XAxis dataKey="p" type="number" domain={[0, 0.5]} stroke={css('--ink-3')} label={{ value: 'missed-merge rate p', position: 'insideBottom', offset: -2 }} height={40} />
          <YAxis unit="%" stroke={css('--ink-3')} />
          <Tooltip contentStyle={{ background: css('--panel'), border: `1px solid ${css('--rule')}` }} />
          <Legend />
          {qs.map((q, i) => <Line key={`a${q}`} dataKey={`a${q}`} name={`model, q = ${q}`} stroke={SERIES[i]} strokeWidth={2} dot={false} />)}
          {qs.map((q, i) => <Scatter key={`s${q}`} dataKey={`s${q}`} name={`simulated, q = ${q}`} fill={SERIES[i]} legendType="none" />)}
        </ComposedChart>
      </ResponsiveContainer>
    </section>
  )
}

function Evolution({ r }: { r: any }) {
  const e = r.evolution
  if (!e) return <Empty title="No evolution run yet">Run <code>python -m ontomem.evaluation.evolution</code>.</Empty>
  const [before, after] = Object.entries(e.results) as any
  return (
    <div className="stack">
      <section className="panel">
        <h2>Growing the ontology from what people say</h2>
        <p className="small">Phrases from the training conversations that the ontology missed were turned into proposals. A reviewer accepted
          {' '}{e.review.accepted_from_top3} from the top three suggestions and placed {e.review.manual} by hand; the first suggestion was right
          {' '}{e.review.top1_correct} of {e.review.reviewed} times. The test split was then re-run with the grown ontology.</p>
        <table className="t" style={{ maxWidth: 760 }}>
          <thead><tr><th /><th className="num">{before[0]}</th><th className="num">{after[0]}</th></tr></thead>
          <tbody>
            {[['concepts', 'Concepts', (x: any) => x], ['test_statements_mapped', 'Statements mapped', pct], ['missed_merge_rate_p', 'Missed merges', pct],
              ['false_merge_rate_q', 'False merges', (x: any) => pct(x, 2)], ['stale_exposure', 'Stale exposure', pct], ['clean_retrieval', 'Clean retrieval', pct]].map(([k, l, f]: any) => (
              <tr key={k}><td>{l}</td><td className="num">{f(before[1][k])}</td><td className="num">{f(after[1][k])}</td></tr>))}
          </tbody>
        </table>
        <p className="small muted" style={{ marginTop: 10 }}>The paraphrase bank is shared across splits, so this measures learning vocabulary that recurs - not generalising to unseen phrasings.</p>
      </section>
      <section className="panel">
        <h3>Review log</h3>
        <div className="scroll-x"><table className="t small"><thead><tr><th>Phrase</th><th>Seen</th><th>Gold</th><th>Decision</th><th>Top suggestion</th></tr></thead>
          <tbody>{e.review_log.slice(0, 40).map((x: any) => <tr key={x.phrase}><td>“{x.phrase}”</td><td className="num">{x.count}</td><td>{x.gold}</td><td>{x.decision}</td>
            <td>{x.top_proposals[0] ? (x.top_proposals[0].kind === 'alias' ? `synonym of ${x.top_proposals[0].of}` : `under ${x.top_proposals[0].parent}`) : '–'}</td></tr>)}</tbody></table></div>
      </section>
    </div>
  )
}

function Failures() {
  const [exp, setExp] = useState('modeA')
  const [system, setSystem] = useState('S3-OntoMem')
  const [type, setType] = useState('')
  const f = useApi<any[]>('/api/eval/failures', { experiment: exp, system, type, limit: 150 })
  return (
    <div className="stack">
      <div className="panel row">
        <label className="field">Experiment<select className="select input" value={exp} onChange={e => setExp(e.target.value)}>
          <option value="modeA">Gold extraction</option><option value="modeB">End-to-end</option><option value="noise">Noisy extraction</option></select></label>
        <label className="field">System<select className="select input" value={system} onChange={e => setSystem(e.target.value)}>
          {MAIN.filter(s => !s.startsWith('S4')).map(s => <option key={s} value={s}>{NAMES[s]}</option>)}
          {['S3 -SKOS', 'S3 -intent', 'S3 -context', 'S3 -who', 'S3 -R3'].map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <label className="field">Kind<select className="select input" value={type} onChange={e => setType(e.target.value)}>
          <option value="">All</option><option value="false_merge">False merges</option><option value="missed_merge">Missed merges</option><option value="probe">Unclean memory blocks</option></select></label>
      </div>
      <ErrorBox error={f.error} />
      {f.loading && <Loading />}
      {f.data && !f.data.length && <div className="panel"><Empty title="No failures of this kind" /></div>}
      {f.data && f.data.length > 0 && (
        <section className="panel scroll-x">
          <table className="t small">
            <thead><tr><th>Kind</th><th>What was said</th><th>Detail</th><th>Stage</th></tr></thead>
            <tbody>{f.data.map((x, i) => (
              <tr key={i}>
                <td>{x.type.replace('_', ' ')}</td>
                <td>“{x.text}”</td>
                <td>{x.type === 'probe'
                  ? <>{x.stale_values?.length > 0 && <div>stale: {x.stale_values.join(', ')}</div>}{x.contaminating?.length > 0 && <div>about someone else / wrong context: {x.contaminating.join(', ')}</div>}{x.missing?.length > 0 && <div>missing: {x.missing.join(', ')}</div>}</>
                  : <>“{x.surface}” → <code>{x.concept}</code>{x.merged_into && <div>merged into: “{x.merged_into}”</div>}<div className="muted">gold {x.gold_key}</div></>}</td>
                <td>{x.stage ?? '–'}</td>
              </tr>))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}

function Figures() {
  const figs = useApi<string[]>('/api/eval/figures')
  if (figs.loading && !figs.data) return <Loading />
  if (!figs.data?.length) return <Empty title="No figures yet">Run <code>make figures</code>.</Empty>
  return (
    <div className="grid-2">
      {figs.data.map(f => (
        <figure key={f} className="figure panel" style={{ margin: 0 }}>
          <img src={`/api/eval/figures/${f}`} alt={f.replace(/_/g, ' ').replace('.png', '')} loading="lazy" />
          <figcaption>{f} · <a href={`/api/eval/figures/${f.replace('.png', '.svg')}`} download>SVG</a></figcaption>
        </figure>
      ))}
    </div>
  )
}

function Run() {
  const [mode, setMode] = useState('A')
  const [systems, setSystems] = useState(['S0', 'S1', 'S3', 'S4'])
  const [limit, setLimit] = useState(30)
  const [noise, setNoise] = useState(0)
  const [job, setJob] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const id = setInterval(() => api(`/api/eval/runs/${job.id}`).then(setJob).catch(() => {}), 800)
    return () => clearInterval(id)
  }, [job])
  const all = ['S0', 'S1', 'S3', 'S4', 'S5', 'ablations', 'S2', 'S2b']
  async function start() {
    setError(null)
    try { setJob(await api('/api/eval/runs', { method: 'POST', body: { mode, systems, limit, noise } })) } catch (e: any) { setError(e.message) }
  }
  return (
    <div className="stack">
      <section className="panel stack" style={{ maxWidth: 820 }}>
        <p className="small">Runs in the background on the server. S2 and S2b need a local language model (set <code>ONTOMEM_LLM</code>, e.g. <code>ollama:qwen2.5:7b-instruct</code>); otherwise they are skipped.</p>
        <div className="row">
          <div className="seg" role="group" aria-label="Setting">
            <button aria-pressed={mode === 'A'} onClick={() => setMode('A')}>Gold extraction</button>
            <button aria-pressed={mode === 'B'} onClick={() => setMode('B')}>End-to-end</button>
          </div>
          <label className="row small">Users <input className="input" type="number" min={1} max={150} value={limit} onChange={e => setLimit(+e.target.value)} style={{ width: 80 }} /></label>
          <label className="row small">Noise <input className="input" type="number" min={0} max={0.5} step={0.05} value={noise} onChange={e => setNoise(+e.target.value)} style={{ width: 80 }} /></label>
        </div>
        <fieldset className="row" style={{ border: 0, padding: 0 }}>
          <legend className="small muted">Systems</legend>
          {all.map(s => <label key={s} className="row small"><input type="checkbox" checked={systems.includes(s)}
            onChange={e => setSystems(e.target.checked ? [...systems, s] : systems.filter(x => x !== s))} />{s}</label>)}
        </fieldset>
        <div><button className="btn primary" onClick={start} disabled={!systems.length || job?.status === 'running'}>Start run</button></div>
        <ErrorBox error={error} />
      </section>
      {job && (
        <section className="panel stack">
          <div className="spread"><h3>{job.id}</h3><span className="small">{job.status === 'running' ? `Running - ${job.progress}` : job.status}</span></div>
          {job.error && <div className="err">{job.error}</div>}
          {job.summary && <><Table rows={job.summary.table} />{Object.keys(job.summary.meta.notes ?? {}).length > 0 && <pre className="code">{JSON.stringify(job.summary.meta.notes, null, 1)}</pre>}</>}
        </section>
      )}
    </div>
  )
}
