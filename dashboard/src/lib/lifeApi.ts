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

export interface LifeDailyStatusItem {
  key: string
  label: string
  status: 'done' | 'planned' | 'pending' | 'attention' | string
  summary: string
  detail: string
}

export interface LifeDailyStatus {
  date: string
  items: LifeDailyStatusItem[]
  counts: Record<string, number>
}

export interface LifeDailyReview {
  id: string
  review_date: string
  summary: string
  metrics_json: Record<string, unknown>
  notes_json: Record<string, unknown>
  created_at: string
}

export interface LifeWeeklyReview {
  id: string
  week_start: string
  week_end: string
  summary: string
  headline: string
  report_text: string
  labels_json: Record<string, unknown>
  patterns_json: Record<string, unknown>
  recommendations_json: string[]
  evidence_json: Array<Record<string, unknown>>
  delivery_json: Record<string, unknown>
  created_at: string
}

export interface LifeDecisionReview {
  id: string
  decision_id: string
  reviewed_at: string
  review_note: string
  outcome: string
  summary: string
  next_review_at: string
  created_at: string
}

export interface LifeLongTermDecision {
  id: string
  content: string
  source: string
  status: string
  review_interval_days: number
  next_review_at: string
  created_at: string
  updated_at: string
  closed_at?: string
  latest_review: LifeDecisionReview | null
}

export interface LifeReviews {
  date: string
  daily_review: LifeDailyReview | null
  weekly_review: LifeWeeklyReview | null
  long_term_decisions: LifeLongTermDecision[]
}

export interface LifeWeekTrendRow {
  date: string
  sleep_minutes: number
  exercise_count: number
  exercise_minutes: number
  sitting_total_minutes: number
  sedentary_over_limit_minutes: number
  meal_count: number
  water_count: number
}

export interface LifeWeekMetrics {
  sleep_days: number
  sleep_total_minutes: number
  sleep_average_minutes: number
  exercise_days: number
  exercise_total_minutes: number
  sitting_total_minutes: number
  sedentary_over_limit_minutes: number
  meal_count: number
  water_count: number
}

export interface LifeWeekRecordGroups {
  sleep: LifeEvent[]
  meal: LifeEvent[]
  exercise: LifeEvent[]
  sedentary: LifeEvent[]
  water: LifeEvent[]
}

export interface LifeWeek {
  date: string
  week_start: string
  week_end: string
  metrics: LifeWeekMetrics
  trend_rows: LifeWeekTrendRow[]
  record_groups: LifeWeekRecordGroups
  weekly_review: LifeWeeklyReview | null
}

export interface LifeRuntimeCron {
  found: boolean
  health: string
  path: string
  enabled: boolean
  state: string
  schedule_display: string
  last_run_at: string
  next_run_at: string
  last_run_age_seconds: number | null
  last_status: string
  last_error: string
  last_delivery_error: string
  repeat_completed: number
}

export interface LifeRuntimeDelivery {
  status: string
  cooling_down: boolean
  cooldown_until: string
  recent_failed_count: number
  recent_sending_count: number
  recent_burst_count: number
  last_failure: {
    reminder_type: string
    scheduled_for: string
    error: string
  } | null
}

export interface LifeRuntimeTick {
  tick_time?: string
  status?: string
  idle_seconds?: number | null
  activity_available?: boolean
  interruption_seconds?: number | null
  interruption_source?: string
  interruption_started_at?: string
  interruption_ended_at?: string
  sedentary_state?: string
  due_candidates?: string[]
  selected_reminder?: string
  should_notify?: boolean
  dry_run?: boolean
  warnings?: string[]
}

export interface LifeReminderRuleStatus {
  reminder_type: string
  label: string
  enabled: boolean
  schedule_text: string
  planned_times: string[]
  next_due_at: string
  today_status: string
  latest_log: LifeReminder | null
  detail: string
}

export interface LifeRuntimeStatus {
  now: string
  cron: LifeRuntimeCron
  delivery: LifeRuntimeDelivery
  last_tick: LifeRuntimeTick | null
  recent_reminders: LifeReminder[]
  reminder_rules: LifeReminderRuleStatus[]
}

export interface LifeMemoryItem {
  id: string
  domain: string
  memory_type: string
  status: string
  content: string
  confidence: string
  evidence_json: Record<string, unknown>
  source: string
  effective_from: string
  expires_at: string
  last_used_at: string
  created_at: string
  updated_at: string
}

export interface LifeMemoryObservation {
  id: string
  domain: string
  observation_type: string
  content: string
  evidence_json: Record<string, unknown>
  source: string
  observed_at: string
  created_at: string
}

export interface LifeMemoryHit {
  id: string
  memory_id: string
  used_by: string
  target_id: string
  used_at: string
  effect_json: Record<string, unknown>
  created_at: string
}

export interface LifeMemories {
  items: LifeMemoryItem[]
  observations: LifeMemoryObservation[]
  hits: LifeMemoryHit[]
}

export interface LifeDecisionTraceCandidate {
  reminder_type: string
  priority?: number
  reason: string
  dedupe_key: string
}

export interface LifeDecisionTraceMemoryInfluence {
  memory_id: string
  domain: string
  memory_type: string
  status: string
  content: string
  effect: string
  reminder_type: string
}

export interface LifeDecisionTrace {
  id: string
  tick_at: string
  status: string
  selected_reminder_type: string
  selected_reminder_id: string
  context_json: Record<string, unknown>
  candidates_json: LifeDecisionTraceCandidate[]
  memory_influences_json: LifeDecisionTraceMemoryInfluence[]
  decision_reason: string
  suppression_reason: string
  warnings_json: string[]
  created_at: string
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

export async function fetchLifeDailyStatus(date: string): Promise<LifeDailyStatus> {
  return fetchJson<LifeDailyStatus>(`/life/daily-status?date=${encodeURIComponent(date)}`)
}

export async function fetchLifeReviews(date: string): Promise<LifeReviews> {
  return fetchJson<LifeReviews>(`/life/reviews?date=${encodeURIComponent(date)}`)
}

export async function fetchLifeWeek(date: string): Promise<LifeWeek> {
  return fetchJson<LifeWeek>(`/life/week?date=${encodeURIComponent(date)}`)
}

export async function fetchLifeRuntime(): Promise<LifeRuntimeStatus> {
  return fetchJson<LifeRuntimeStatus>('/life/runtime')
}

export async function fetchLifeMemories(): Promise<LifeMemories> {
  return fetchJson<LifeMemories>('/life/memories')
}

export async function fetchLifeDecisionTraces(date: string): Promise<LifeDecisionTrace[]> {
  const data = await fetchJson<{ items: LifeDecisionTrace[] }>(`/life/decision-traces?date=${encodeURIComponent(date)}`)
  return Array.isArray(data.items) ? data.items : []
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    throw new Error(`Life API 请求失败：${res.status}`)
  }
  return res.json()
}
