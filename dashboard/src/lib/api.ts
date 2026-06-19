const API_BASE = 'http://localhost:8000/api'

async function fetchItems<T>(url: string): Promise<T[]> {
  try {
    const res = await fetch(url)
    if (!res.ok) return []
    const data = await res.json()
    return Array.isArray(data.items) ? data.items : []
  } catch {
    return []
  }
}

async function fetchJsonOrNull<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url)
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

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
  body: string
  folder_path: string
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

  return fetchItems<Capture>(`${API_BASE}/captures?${qs}`)
}

export async function updateCapture(
  captureId: string,
  params: {
    body?: string
    title?: string
    folder_path?: string
  },
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/captures/${captureId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      body: params.body || '',
      title: params.title || '',
      folder_path: params.folder_path,
    }),
  })
  if (!res.ok) throw new Error(`Failed to update capture: ${res.statusText}`)
  return res.json()
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
  return fetchItems<ReadingSession>(`${API_BASE}/reading/sessions?${qs}`)
}

export async function fetchReadingSession(sessionId: string): Promise<ReadingSessionDetail | null> {
  return fetchJsonOrNull<ReadingSessionDetail>(`${API_BASE}/reading/sessions/${sessionId}`)
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
  return fetchItems<ReadingDraft>(`${API_BASE}/reading/drafts?${qs}`)
}

export async function fetchReadingDraft(draftId: string): Promise<ReadingDraft | null> {
  return fetchJsonOrNull<ReadingDraft>(`${API_BASE}/reading/drafts/${draftId}`)
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
  return fetchItems<ReadingDraft>(`${API_BASE}/knowledge/drafts?${qs}`)
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
  return fetchItems<GardenCard>(`${API_BASE}/knowledge/garden-cards?${qs}`)
}

// ─── Capture Drafts ───────────────────────────────────────────────────────

export interface CaptureDraft {
  id: string
  raw_text: string
  source: string
  source_type: string
  title: string
  word_count: number
  draft_text: string
  instruction: string
  capture_intent: Record<string, unknown>
  summary: string
  tags: string[]
  status: string
  committed_capture_id: string
  created_at: string
  updated_at: string
}

export async function fetchCaptureDrafts(limit = 50): Promise<CaptureDraft[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  return fetchItems<CaptureDraft>(`${API_BASE}/capture-drafts?${qs}`)
}

export async function fetchCaptureDraft(draftId: string): Promise<CaptureDraft | null> {
  return fetchJsonOrNull<CaptureDraft>(`${API_BASE}/capture-drafts/${draftId}`)
}

export async function createEditableCaptureDraft(params: {
  content: string
  instruction?: string
  source?: string
  source_type?: string
  title?: string
  folder_path?: string
}): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/capture-drafts/editable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content: params.content,
      instruction: params.instruction || '',
      source: params.source || '',
      source_type: params.source_type || 'manual',
      title: params.title || '',
      folder_path: params.folder_path || '',
    }),
  })
  if (!res.ok) throw new Error(`Failed to create editable draft: ${res.statusText}`)
  return res.json()
}

export async function updateEditableCaptureDraft(
  draftId: string,
  params: {
    content: string
    title?: string
  },
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/capture-drafts/${draftId}/editable`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content: params.content,
      title: params.title || '',
    }),
  })
  if (!res.ok) throw new Error(`Failed to update editable draft: ${res.statusText}`)
  return res.json()
}

export async function reviseCaptureDraft(draftId: string, instruction: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/capture-drafts/${draftId}/revise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) throw new Error(`Failed to revise: ${res.statusText}`)
  return res.json()
}

export async function commitCaptureDraft(draftId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/capture-drafts/${draftId}/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`Failed to commit: ${res.statusText}`)
  return res.json()
}

// ─── Hermes Gateway Chat (SSE) ────────────────────────────────────────────

const GATEWAY_URL = '/api/hermes-chat'

export interface ChatStreamCallbacks {
  onDelta: (text: string) => void
  onComplete: (fullText: string) => void
  onError: (error: string) => void
  onToolProgress?: (toolName: string, preview: string) => void
}

export async function sendMessageToGateway(
  messages: Array<{ role: string; content: string }>,
  callbacks: ChatStreamCallbacks,
  sessionId?: string,
): Promise<void> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const res = await fetch(GATEWAY_URL, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      model: 'hermes-agent',
      messages,
      stream: true,
      session_id: sessionId,
    }),
  })

  if (!res.ok) {
    callbacks.onError(`Gateway error: ${res.status} ${res.statusText}`)
    return
  }

  const reader = res.body?.getReader()
  if (!reader) {
    callbacks.onError('No response body')
    return
  }

  const decoder = new TextDecoder()
  let fullText = ''
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6).trim()
        if (data === '[DONE]') {
          callbacks.onComplete(fullText)
          return
        }
        try {
          const parsed = JSON.parse(data)
          const delta = parsed.choices?.[0]?.delta?.content
          if (delta) {
            fullText += delta
            callbacks.onDelta(delta)
          }
        } catch {
          // Skip non-JSON lines (e.g., hermes.tool.progress events)
        }
      }
    }
  }

  callbacks.onComplete(fullText)
}
