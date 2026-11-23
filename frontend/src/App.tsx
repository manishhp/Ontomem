import { createContext, useContext, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api, getToken, setToken } from './api'
import { Icon } from './ui'
import Login from './pages/Login'
import Chat from './pages/Chat'
import MemoryGraph from './pages/MemoryGraph'
import Timeline from './pages/Timeline'
import Compare from './pages/Compare'
import Scenarios from './pages/Scenarios'
import Ontology from './pages/Ontology'
import Evaluation from './pages/Evaluation'
import DataPage from './pages/DataPage'

export interface Account { id: string; username: string; display_name: string; is_demo: number }
interface AuthCtx { account: Account | null; signIn: (token: string, a: Account) => void; signOut: () => void }
const Ctx = createContext<AuthCtx>({ account: null, signIn: () => {}, signOut: () => {} })
export const useAuth = () => useContext(Ctx)

const NAV = [
  { group: 'Use it' },
  { to: '/chat', label: 'Chat', icon: 'chat' },
  { to: '/memory', label: 'Memory graph', icon: 'graph' },
  { to: '/timeline', label: 'Slot timeline', icon: 'timeline' },
  { group: 'Understand it' },
  { to: '/scenarios', label: 'Scenarios', icon: 'play' },
  { to: '/compare', label: 'Compare', icon: 'compare' },
  { to: '/ontology', label: 'Ontology', icon: 'tree' },
  { group: 'Measure it' },
  { to: '/evaluation', label: 'Evaluation lab', icon: 'lab' },
  { to: '/data', label: 'Data & privacy', icon: 'data' },
] as const

export default function App() {
  const [account, setAccount] = useState<Account | null>(null)
  const [ready, setReady] = useState(false)
  const [open, setOpen] = useState(false)
  const loc = useLocation()

  useEffect(() => {
    const onLogout = () => setAccount(null)
    window.addEventListener('ontomem:logout', onLogout)
    if (getToken()) api<Account>('/api/auth/me').then(setAccount).catch(() => setToken(null)).finally(() => setReady(true))
    else setReady(true)
    return () => window.removeEventListener('ontomem:logout', onLogout)
  }, [])
  useEffect(() => setOpen(false), [loc.pathname])

  const ctx: AuthCtx = {
    account,
    signIn: (t, a) => { setToken(t); setAccount(a) },
    signOut: () => { setToken(null); setAccount(null) },
  }
  if (!ready) return null
  if (!account) return <Ctx.Provider value={ctx}><Login /></Ctx.Provider>

  return (
    <Ctx.Provider value={ctx}>
      <div className="shell">
        <aside className={`side ${open ? 'open' : ''}`} aria-label="Main navigation">
          <NavLink to="/chat" className="brand">
            <img src="/favicon.svg" width="30" height="30" alt="" />
            <span>OntoMem<small>preference memory</small></span>
          </NavLink>
          <nav className="nav">
            {NAV.map((n, i) => 'group' in n
              ? <div key={i} className="group">{n.group}</div>
              : <NavLink key={n.to} to={n.to}><Icon name={n.icon} />{n.label}</NavLink>)}
          </nav>
          <div className="who">
            <strong>{account.display_name}</strong>
            @{account.username}{account.is_demo ? ' · demo' : ''}
            <div><button onClick={ctx.signOut}>Sign out</button></div>
          </div>
        </aside>
        <main className="main">
          <button className="btn menu-btn" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /> Menu</button>
          <Routes>
            <Route path="/chat" element={<Chat />} />
            <Route path="/memory" element={<MemoryGraph />} />
            <Route path="/timeline" element={<Timeline />} />
            <Route path="/scenarios" element={<Scenarios />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/ontology" element={<Ontology />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="/data" element={<DataPage />} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </main>
      </div>
    </Ctx.Provider>
  )
}
