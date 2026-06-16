const API_BASE = 'http://localhost:8000/api'

// ─── Capture ──────────────────────────────────────────────────────────────────

export interface Capture {
  id: string
  title: string
  source: string
  source_url: string
  source_type: string
  file_path: string
  raw_path: string
  summary: string
  tags: string[]
  word_count: number
  status: string
  created_at: string
  updated_at: string
  review_count: number
}

export async function fetchCaptures(params?: {
  tag?: string
  search?: string
  folder?: string
  limit?: number
}): Promise<Capture[]> {
  const qs = new URLSearchParams()
  if (params?.tag) qs.set('tag', params.tag)
  if (params?.search) qs.set('search', params.search)
  if (params?.folder) qs.set('folder', params.folder)
  if (params?.limit) qs.set('limit', String(params.limit))

  const res = await fetch(`${API_BASE}/captures?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}

// ─── Reading Sessions ─────────────────────────────────────────────────────────

export interface ReadingSession {
  id: string
  source_type: string
  source_id: string
  source_url: string
  source_title: string
  focus: string
  status: string
  created_at: string
  updated_at: string
  last_turn_at: string | null
  meta_json: Record<string, unknown>
}

export interface ReadingTurn {
  id: string
  session_id: string
  turn_index: number
  user_message: string
  assistant_answer: string
  focus: string
  confidence: string
  created_at: string
}

export interface ReadingEvidence {
  id: string
  session_id: string
  turn_id: string
  source_kind: string
  source_id: string
  title: string
  url: string
  excerpt: string
  used_for: string
}

export interface ReadingSessionDetail extends ReadingSession {
  turns: ReadingTurn[]
  evidence: ReadingEvidence[]
}

export async function fetchReadingSessions(status = 'active', limit = 20): Promise<ReadingSession[]> {
  const qs = new URLSearchParams({ status, limit: String(limit) })
  const res = await fetch(`${API_BASE}/reading/sessions?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}

export async function fetchReadingSession(sessionId: string): Promise<ReadingSessionDetail | null> {
  const res = await fetch(`${API_BASE}/reading/sessions/${sessionId}`)
  if (!res.ok) return null
  return res.json()
}

// ─── Drafts ───────────────────────────────────────────────────────────────────

export interface ReadingDraft {
  id: string
  session_id: string
  output_type: string
  target_capture_id: string
  title: string
  summary: string
  draft_text: string
  status: string
  meta_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

export async function fetchReadingDrafts(limit = 20): Promise<ReadingDraft[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  const res = await fetch(`${API_BASE}/reading/drafts?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}

export async function fetchReadingDraft(draftId: string): Promise<ReadingDraft | null> {
  const res = await fetch(`${API_BASE}/reading/drafts/${draftId}`)
  if (!res.ok) return null
  return res.json()
}

export async function createReadingDraft(
  sessionId: string,
  outputType: string,
  instruction: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/reading/drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      output_type: outputType,
      instruction,
    }),
  })
  if (!res.ok) throw new Error(`Failed to create draft: ${res.statusText}`)
  return res.json()
}

export async function reviseReadingDraft(
  draftId: string,
  instruction: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/reading/drafts/${draftId}/revise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) throw new Error(`Failed to revise draft: ${res.statusText}`)
  return res.json()
}

export async function commitReadingDraft(draftId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/reading/drafts/${draftId}/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`Failed to commit draft: ${res.statusText}`)
  return res.json()
}

// ─── Knowledge / Answer Drafts ────────────────────────────────────────────────

export async function fetchKnowledgeDrafts(limit = 50): Promise<ReadingDraft[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  const res = await fetch(`${API_BASE}/knowledge/drafts?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}

export async function saveAnswerDraft(params: {
  hermes_session_id?: string
  user_message?: string
  assistant_answer: string
  instruction?: string
  output_type?: string
  sources?: Array<Record<string, unknown>>
  target_capture_id?: string
}): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/knowledge/answer-drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) throw new Error(`Failed to save answer: ${res.statusText}`)
  return res.json()
}

export async function reviseKnowledgeDraft(
  draftId: string,
  instruction: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/knowledge/drafts/${draftId}/revise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) throw new Error(`Failed to revise draft: ${res.statusText}`)
  return res.json()
}

export async function commitKnowledgeDraft(draftId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/knowledge/drafts/${draftId}/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`Failed to commit draft: ${res.statusText}`)
  return res.json()
}

// ─── Garden Cards ─────────────────────────────────────────────────────────────

export interface GardenCard {
  id: string
  title: string
  card_kind: string
  status: string
  file_path: string
  claim: string
  created_at: string
  updated_at: string
}

export async function fetchGardenCards(limit = 50): Promise<GardenCard[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  const res = await fetch(`${API_BASE}/knowledge/garden-cards?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}
