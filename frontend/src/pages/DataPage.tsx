import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../App'
import { ErrorBox, PageHead } from '../ui'

const QUERIES: Record<string, string> = {
  'Current preferences': `PREFIX pref: <https://w3id.org/ontomem/pref#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?who ?concept ?polarity ?since WHERE {
  ?p pref:status "active" ; pref:subjectRole ?who ; pref:polarity ?polarity ; pref:statedAt ?since ;
     pref:concept ?c . ?c skos:prefLabel ?concept .
} ORDER BY ?since`,
  'Everything that was superseded': `PREFIX pref: <https://w3id.org/ontomem/pref#>
SELECT ?old ?oldPolarity ?new ?newPolarity ?when WHERE {
  ?o pref:supersededBy ?n ; pref:polarity ?oldPolarity ; pref:sourceText ?old ; pref:validTo ?when .
  ?n pref:polarity ?newPolarity ; pref:sourceText ?new .
}`,
  'Exceptions (rule R2)': `PREFIX pref: <https://w3id.org/ontomem/pref#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?specific ?general WHERE {
  ?a pref:exceptionTo ?b ; pref:concept ?ca . ?b pref:concept ?cb .
  ?ca skos:prefLabel ?specific . ?cb skos:prefLabel ?general .
}`,
  'Everything about seafood, at any depth': `PREFIX pref: <https://w3id.org/ontomem/pref#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX c: <https://w3id.org/ontomem/concept/>
SELECT ?label ?polarity ?status WHERE {
  ?p pref:concept ?x ; pref:polarity ?polarity ; pref:status ?status .
  ?x skos:broader* c:Seafood ; skos:prefLabel ?label .
}`,
}

export default function DataPage() {
  const { account, signOut } = useAuth()
  const [q, setQ] = useState(QUERIES['Current preferences'])
  const [res, setRes] = useState<any>(null)
  const [val, setVal] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  async function run() {
    setError(null)
    try { setRes(await api('/api/sparql', { method: 'POST', body: { query: q } })) } catch (e: any) { setError(e.message); setRes(null) }
  }
  async function download(fmt: string) {
    const text = await api<string>('/api/me/export', { params: { fmt } })
    const blob = new Blob([typeof text === 'string' ? text : JSON.stringify(text, null, 1)], { type: 'text/plain' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `ontomem-${account?.username}.${fmt === 'turtle' ? 'ttl' : 'jsonld'}`
    a.click()
  }
  async function erase() {
    const r = await api<any>('/api/me/data', { method: 'DELETE' })
    setMsg(`Deleted ${r.erased_events} events. Only a content-free record of the deletion remains.`)
    setConfirm('')
  }

  return (
    <>
      <PageHead title="Data & privacy">Your memory is an RDF graph you can query, check and take with you - or delete.</PageHead>
      <div className="grid-2" style={{ alignItems: 'start' }}>
        <section className="panel stack">
          <h2>Query your memory with SPARQL</h2>
          <div className="row">{Object.keys(QUERIES).map(k => <button key={k} className="btn small" onClick={() => setQ(QUERIES[k])}>{k}</button>)}</div>
          <textarea className="input mono" rows={9} value={q} onChange={e => setQ(e.target.value)} aria-label="SPARQL query" spellCheck={false} />
          <div><button className="btn primary" onClick={run}>Run query</button> <span className="small muted">read-only</span></div>
          <ErrorBox error={error} />
          {res?.type === 'select' && (
            <div className="scroll-x">
              <p className="small muted">{res.rows.length} row(s) from a graph of {res.triples_in_graph} triples</p>
              <table className="t small"><thead><tr>{res.vars.map((v: string) => <th key={v}>{v}</th>)}</tr></thead>
                <tbody>{res.rows.map((r: any, i: number) => <tr key={i}>{res.vars.map((v: string) => <td key={v}>{r[v]?.replace(/^.*[#/]/, '') ?? ''}</td>)}</tr>)}</tbody></table>
            </div>
          )}
          {res?.type === 'ask' && <p>Answer: <b>{String(res.boolean)}</b></p>}
        </section>
        <div className="stack">
          <section className="panel stack">
            <h2>Check integrity</h2>
            <p className="small">Runs every SHACL shape over your whole memory, including the two graph-level invariants:
              no two active preferences share a slot, and every retired value names its successor.</p>
            <div><button className="btn" onClick={async () => setVal(await api('/api/validate/graph'))}>Validate my memory</button></div>
            {val && (val.conforms
              ? <p style={{ color: 'var(--likes)' }}><b>Conforms.</b> {val.triples} triples checked.</p>
              : <ul className="small">{val.violations.map((v: any, i: number) => <li key={i}>{v.message} - {v.value}</li>)}</ul>)}
          </section>
          <section className="panel stack">
            <h2>Download your data</h2>
            <p className="small">Everything, including retired values and provenance, as linked data.</p>
            <div className="row"><button className="btn" onClick={() => download('json-ld')}>Download JSON-LD</button><button className="btn" onClick={() => download('turtle')}>Download Turtle</button></div>
          </section>
          <section className="panel stack" style={{ borderColor: 'var(--avoids)' }}>
            <h2>Delete my memory</h2>
            <p className="small">Removes every event and preference. This cannot be undone. Type <b>{account?.username}</b> to confirm.</p>
            <div className="row">
              <input className="input" style={{ maxWidth: 220 }} value={confirm} onChange={e => setConfirm(e.target.value)} aria-label="Type your username to confirm" />
              <button className="btn danger" disabled={confirm !== account?.username} onClick={erase}>Delete my memory</button>
            </div>
            {msg && <p className="small" role="status">{msg}</p>}
            {account?.is_demo ? <div><button className="btn small" onClick={async () => { await api('/api/me/reset-demo', { method: 'POST' }); setMsg('Demo history restored.') }}>Restore the demo history</button></div> : null}
            <div><button className="btn quiet small" onClick={signOut}>Sign out</button></div>
          </section>
        </div>
      </div>
    </>
  )
}
