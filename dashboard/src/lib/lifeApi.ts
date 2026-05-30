export interface LifeSummary {
  date: string
  metrics: {
    sitting_total_minutes: number
    sedentary_over_limit_minutes: number
    event_counts: Record<string, number>
  }
  active_sitting: LifeEvent | null
  latest_reminders: Record<string, LifeReminder>
}

export interface LifeEvent {
  id: string
  event_type: string
  occurred_at: string
  ended_at?: string
  duration_minutes?: number
  source: string
  raw_text: string
  payload_json: Record<string, unknown>
  status: string
  created_at: string
  updated_at: string
}

export interface LifeReminder {
  id: string
  rule_id: string
  reminder_type: string
  scheduled_for: string
  sent_at?: string
  status: string
  user_response: string
  resolved_at?: string
  dedupe_key: string
  error: string
  payload_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

const API_BASE = 'http://localhost:8000/api'

export async function fetchLifeSummary(date: string): Promise<LifeSummary> {
  return fetchJson<LifeSummary>(`/life/summary?date=${encodeURIComponent(date)}`)
}

export async function fetchLifeEvents(date: string): Promise<LifeEvent[]> {
  const data = await fetchJson<{ items: LifeEvent[] }>(`/life/events?date=${encodeURIComponent(date)}`)
  return Array.isArray(data.items) ? data.items : []
}

export async function fetchLifeReminders(date: string): Promise<LifeReminder[]> {
  const data = await fetchJson<{ items: LifeReminder[] }>(`/life/reminders?date=${encodeURIComponent(date)}`)
  return Array.isArray(data.items) ? data.items : []
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    throw new Error(`Life API 请求失败：${res.status}`)
  }
  return res.json()
}
