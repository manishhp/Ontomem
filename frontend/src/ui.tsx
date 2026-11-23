import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api } from './api'

export function useApi<T>(path: string | null, params?: Record<string, any>, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const key = JSON.stringify(params ?? {})
  const reload = useCallback(async () => {
    if (!path) return
    setLoading(true)
    setError(null)
    try { setData(await api<T>(path, { params })) } catch (e: any) { setError(e.message) } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, ...deps])
  useEffect(() => { reload() }, [reload])
  return { data, error, loading, reload, setData }
}

export function Pol({ p }: { p: string }) {
  return <span className={`pol ${p}`}>{p}</span>
}

export function RuleTag({ r, title }: { r: string; title?: string }) {
  return <span className={`rule-tag ${r}`} title={title}>{r}</span>
}

export function KeyView({ k }: { k: { who: string; what: string; aspect: string; intent: string; context: string } | string }) {
  const parts = typeof k === 'string' ? k.split('·') : [k.who, k.what, k.aspect, k.intent, k.context]
  const [who, what, aspect, intent, context] = parts
  const dim = (v: string, d: string) => (v === d ? 'dim' : '')
  return (
    <span className="key" title="slot key: who · what · aspect · intent · context">
      <span className={`k-who ${dim(who, 'self')}`}>{who}</span>
      <span className="k-what">{what}</span>
      <span className={dim(aspect, 'general')}>{aspect}</span>
      <span className={dim(intent, 'standing')}>{intent}</span>
      <span className={dim(context, 'any')}>{context}</span>
    </span>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children}</div>
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div className="loading" role="status">{what}…</div>
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="err" role="alert">{error}</div> : null
}

export function PageHead({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{children && <p>{children}</p>}</div>
      {actions && <div className="row">{actions}</div>}
    </div>
  )
}

const ICON: Record<string, string> = {
  chat: 'M4 5h16v10H9l-5 4z',
  graph: 'M6 6a2 2 0 1 0 0 .01M18 6a2 2 0 1 0 0 .01M12 18a2 2 0 1 0 0 .01M7.5 7.5l3.5 9M16.5 7.5l-3.5 9M8 6h8',
  timeline: 'M4 7h9M4 12h14M4 17h6M17 5v4M20 10v4M12 15v4',
  compare: 'M4 5h7v14H4zM13 5h7v14h-7z',
  play: 'M7 5l11 7-11 7z',
  tree: 'M12 4v4M12 8H6v4M12 8h6v4M6 12v4M18 12v4M4 16h4M16 16h4M10 4h4',
  lab: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3',
  data: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  menu: 'M4 6h16M4 12h16M4 18h16',
}

export function Icon({ name, size = 18 }: { name: keyof typeof ICON | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[name]} /></svg>
  )
}
