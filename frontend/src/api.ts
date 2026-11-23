// Thin client for the OntoMem API. The token lives in localStorage (wrapped: storage can be unavailable).

export type Polarity = 'likes' | 'dislikes' | 'avoids' | 'neutral'

export interface SlotKey { who: string; what: string; aspect: string; intent: string; context: string }

export interface PrefNode {
  id: string
  key: SlotKey
  key_str: string
  label: string
  path: string[]
  polarity: Polarity
  strength: number
  confidence: number
  reason: string
  surface: string
  source_text: string
  stated_at: string
  valid_from: string
  valid_to: string | null
  status: 'active' | 'retired'
  superseded_by: string | null
  supersedes: string | null
  evidence: string[]
  created_by_rule: string
  rule_name: string
  needs_confirmation: boolean
  mapped: boolean
  relations?: { exception_to: string[]; refines: string[] }
}

export interface Firing { rule: string; message: string; nodes: string[] }

export interface Candidate {
  raw: Record<string, any>
  mapping: { surface: string; concept: string; label: string; path: string[]; method: string; score: number; matched: string }
  validation: { conforms: boolean; validator: string; violations: { field: string; message: string; value: any }[] }
  repairs: { attempt: number; candidate: any; validation: any }[]
  accepted: boolean
  key: string | null
  outcome: { decision: string; node_id: string; key: string; retired: string | null; firings: Firing[]; flagged: string[] } | null
  node: any
}

export interface Trace {
  user_id: string; text: string; ts: string; turn_id: string; extractor: string
  candidates: Candidate[]
  clarifications: Clarification[]
  timings_ms: Record<string, number>
}

export interface Clarification { node_id: string; broader_id: string; question: string; keep_label: string; drop_label: string }

export interface Recommendation {
  item: { id: string; name: string; domain: string; concepts: string[]; description: string; price: string }
  score: number
  vetoed: boolean
  reasons: string[]
  proof: any
  counterfactual: any
}

export interface Answer {
  query: any
  memory_block: { block: string; items: any[]; used_tokens: number; budget_tokens: number; candidates: number; dropped: string[] }
  recommendations: Recommendation[]
  excluded: { item: any; reasons: string[] }[]
  excluded_count: number
  clarifications: Clarification[]
  reply: string
  latency_ms: number
}

const TOKEN_KEY = 'ontomem.token'

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* storage unavailable */ }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; params?: Record<string, any> } = {}): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [k, v] of Object.entries(opts.params ?? {})) if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
  const headers: Record<string, string> = {}
  const tok = getToken()
  if (tok) headers.Authorization = `Bearer ${tok}`
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(url, { method: opts.method ?? 'GET', headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined })
  if (res.status === 401 && tok) {
    setToken(null)
    window.dispatchEvent(new Event('ontomem:logout'))
  }
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* not json */ }
    throw new ApiError(res.status, msg)
  }
  const ct = res.headers.get('content-type') ?? ''
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>
}

export const fmtDate = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''
export const fmtDateTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
export const pct = (x: number | null | undefined, d = 1) => (x === null || x === undefined ? '–' : `${(100 * x).toFixed(d)}%`)
