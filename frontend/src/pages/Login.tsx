import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { useAuth } from '../App'
import { ErrorBox } from '../ui'

const STORY = [
  { when: 'March', txt: '“I love spicy food.”', res: 'New slot: you · spicy food · likes' },
  { when: 'June', txt: '“Spicy food doesn’t agree with me any more.”', res: 'Same slot, new value. March is kept as history, never shown again.' },
  { when: 'June', txt: '“I avoid seafood, but I love sushi.”', res: 'Sushi is narrower than seafood: both kept, sushi wins for sushi.' },
  { when: 'October', txt: '“Any dinner ideas?”', res: 'Salmon nigiri, vegetable maki. Prawn curry left out - and it says why.' },
]

export default function Login() {
  const { signIn } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [demos, setDemos] = useState<{ username: string; display_name: string; password: string }[]>([])

  useEffect(() => { api('/api/auth/demo-accounts').then(setDemos).catch(() => {}) }, [])

  async function submit(e: FormEvent, creds?: { username: string; password: string }) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body = creds ?? { username, password }
      const r = await api<{ token: string; account: any }>(`/api/auth/${creds ? 'login' : mode}`, { method: 'POST', body })
      signIn(r.token, r.account)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <section className="intro" aria-label="What OntoMem does">
        <h1>An assistant’s memory that knows what changed.</h1>
        <p>OntoMem keeps your preferences in a knowledge graph. When you change your mind, an ontology - not a guessing
          language model - decides which old preference the new one replaces.</p>
        <div className="demo-line">
          {STORY.map((s, i) => (
            <div key={i} style={{ display: 'contents' }}>
              <span className="when">{s.when}</span><span className="txt">{s.txt}</span>
              <span className="res">{s.res}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="form">
        <h2>{mode === 'login' ? 'Sign in' : 'Create an account'}</h2>
        <form onSubmit={submit} className="stack">
          <label className="field">Username
            <input className="input" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required minLength={3} />
          </label>
          <label className="field">Password
            <input className="input" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password} onChange={e => setPassword(e.target.value)} required minLength={6} />
          </label>
          <ErrorBox error={error} />
          <button className="btn primary" disabled={busy}>{mode === 'login' ? 'Sign in' : 'Create account'}</button>
        </form>
        <button className="btn quiet" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? 'New here? Create an account' : 'Already have an account? Sign in'}
        </button>
        {demos.length > 0 && (
          <div className="sunk stack" style={{ gap: 8 }}>
            <strong>Try a demo account</strong>
            <span className="small muted">Each has months of conversation history already in memory.</span>
            <div className="row">
              {demos.map(d => (
                <button key={d.username} className="btn" disabled={busy}
                  onClick={e => submit(e, { username: d.username, password: d.password })}>
                  Continue as {d.display_name}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
