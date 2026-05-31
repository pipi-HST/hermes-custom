import { useEffect, useMemo, useState } from 'react'
import {
  fetchLifeEvents,
  fetchLifeReminders,
  fetchLifeReviews,
  fetchLifeSummary,
  fetchLifeWeek,
  type LifeDailyReview,
  type LifeEvent,
  type LifeLongTermDecision,
  type LifeReminder,
  type LifeReviews,
  type LifeSummary,
  type LifeWeek,
  type LifeWeekTrendRow,
  type LifeWeeklyReview,
} from '../lib/lifeApi'

const EVENT_LABELS: Record<string, string> = {
  sleep_start: '入睡',
  sleep_end: '醒来',
  meal: '饮食',
  exercise: '运动',
  water: '喝水',
  sedentary_start: '坐姿窗口',
  sedentary_break: '活动中断',
}

const REMINDER_LABELS: Record<string, string> = {
  sedentary: '久坐提醒',
  exercise: '运动提醒',
  water: '喝水提醒',
  sleep: '早睡提醒',
  daily_review: '日复盘',
  weekly_review: '周复盘',
  long_term_decision_review: '长期决定复盘',
}

const STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  auto_resolved: '自动结束',
  confirmed: '已确认',
  completed_unknown: '已完成',
  scheduled: '已安排',
  sending: '已触发',
  skipped: '已跳过',
  completed: '已完成',
  paused: '已暂停',
  failed: '失败',
}

const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const LIFE_VIEW_STORAGE_KEY = 'life-dashboard-view'

type ViewMode = 'day' | 'week'

interface SittingRow {
  id: string
  start: string
  end?: string
  durationSeconds: number
  status: string
  active: boolean
  crossedThreshold: boolean
}

interface SleepSession {
  id: string
  start?: LifeEvent
  end?: LifeEvent
  durationMinutes?: number
}

function todayKey(): string {
  return dateKey(new Date())
}

function viewModeFromText(value: string | null): ViewMode | null {
  if (value === 'day' || value === 'week') return value
  return null
}

function shiftDate(date: string, days: number): string {
  const current = new Date(`${date}T00:00:00`)
  current.setDate(current.getDate() + days)
  return dateKey(current)
}

function dateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function parseTime(value?: string): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function secondsBetween(start?: string, end?: string): number {
  const startDate = parseTime(start)
  const endDate = end ? parseTime(end) : new Date()
  if (!startDate || !endDate) return 0
  return Math.max(0, Math.floor((endDate.getTime() - startDate.getTime()) / 1000))
}

function formatTime(value?: string): string {
  const date = parseTime(value)
  if (!date) return value?.slice(11, 16) || ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatDateRange(start?: string, end?: string): string {
  if (!start || !end) return ''
  return `${start} 至 ${end}`
}

function formatDateTime(value?: string): string {
  if (!value) return ''
  const date = parseTime(value)
  if (!date) return value.slice(0, 16)
  const day = date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  return `${day} ${formatTime(value)}`
}

function formatShortDate(value: string): string {
  const date = parseTime(`${value}T00:00:00`)
  if (!date) return value.slice(5)
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

function formatClockSeconds(seconds: number): string {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return [hours, minutes, rest].map(part => String(part).padStart(2, '0')).join(':')
}

function formatDurationSeconds(seconds: number): string {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  if (hours) return `${hours}小时${minutes}分${rest}秒`
  if (minutes) return `${minutes}分${rest}秒`
  return `${rest}秒`
}

function formatMinutes(minutes?: number): string {
  const value = minutes || 0
  const hours = Math.floor(value / 60)
  const rest = value % 60
  if (hours && rest) return `${hours}小时${rest}分`
  if (hours) return `${hours}小时`
  return `${rest}分`
}

function percent(value: number, max: number, min = 0): number {
  if (!max) return min
  return Math.max(min, Math.min(100, Math.round((value / max) * 100)))
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status
}

function deliveryLabel(value: unknown): string {
  if (value === 'sent') return '已推送'
  if (value === 'pending') return '待推送'
  if (value === 'failed') return '失败'
  if (typeof value === 'string' && value) return value
  return '未知'
}

function evidenceText(value: Record<string, unknown>): string {
  const text = value.text
  if (typeof text === 'string' && text) return text
  return JSON.stringify(value)
}

function payloadText(payload?: Record<string, unknown>): string {
  if (!payload || Object.keys(payload).length === 0) return ''
  const parts = []
  if (typeof payload.content === 'string') parts.push(payload.content)
  if (typeof payload.exercise_type === 'string') parts.push(payload.exercise_type)
  if (typeof payload.meal_slot === 'string') parts.push(payload.meal_slot)
  if (typeof payload.source === 'string') parts.push(payload.source)
  if (parts.length) return parts.join(' · ')
  return JSON.stringify(payload)
}

function payloadString(payload: Record<string, unknown> | undefined, key: string): string {
  const value = payload?.[key]
  return typeof value === 'string' ? value : ''
}

function mealSlotLabel(slot: unknown): string {
  if (slot === 'breakfast') return '早餐'
  if (slot === 'lunch') return '午饭'
  if (slot === 'dinner') return '晚饭'
  if (slot === 'snack') return '加餐'
  return '饮食'
}

function mealHasExactTime(meal: LifeEvent): boolean {
  return meal.payload_json.meal_time_precision !== 'missing'
}

function eventText(event: LifeEvent): string {
  return event.raw_text || payloadText(event.payload_json) || EVENT_LABELS[event.event_type] || event.event_type
}

function sleepRows(events: LifeEvent[]): SleepSession[] {
  const starts = events
    .filter(event => event.event_type === 'sleep_start')
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  const ends = events
    .filter(event => event.event_type === 'sleep_end')
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  const usedEnds = new Set<string>()
  const rows: SleepSession[] = []

  for (const start of starts) {
    const end = ends.find(candidate => (
      !usedEnds.has(candidate.id) && candidate.occurred_at >= start.occurred_at
    ))
    if (end) usedEnds.add(end.id)
    rows.push({
      id: `${start.id}:${end?.id || 'open'}`,
      start,
      end,
      durationMinutes: end?.duration_minutes || Math.floor(secondsBetween(start.occurred_at, end?.occurred_at) / 60),
    })
  }

  for (const end of ends) {
    if (!usedEnds.has(end.id)) {
      rows.push({ id: `unpaired:${end.id}`, end, durationMinutes: end.duration_minutes })
    }
  }

  return rows.sort((a, b) => {
    const left = a.end?.occurred_at || a.start?.occurred_at || ''
    const right = b.end?.occurred_at || b.start?.occurred_at || ''
    return right.localeCompare(left)
  })
}

function isUserExercisePlan(reminder: LifeReminder): boolean {
  if (reminder.reminder_type !== 'exercise') return false
  if (payloadString(reminder.payload_json, 'source') === 'user_reschedule') return true
  if (reminder.dedupe_key.startsWith('exercise_reschedule:')) return true
  return reminder.status === 'skipped' || reminder.dedupe_key.startsWith('exercise_skip:')
}

function exercisePlanDetail(plan: LifeReminder): string {
  if (plan.status === 'skipped') return '今天不练'
  if (plan.status === 'completed') return payloadString(plan.payload_json, 'exercise_type') || '已完成'
  return payloadString(plan.payload_json, 'exercise_type') || payloadString(plan.payload_json, 'content') || '用户指定时间'
}

function buildSittingRows(events: LifeEvent[], activeSitting: LifeEvent | null): SittingRow[] {
  return events
    .filter(event => event.event_type === 'sedentary_start')
    .map(event => {
      const isActive = event.status === 'active' || event.id === activeSitting?.id
      const durationSeconds = isActive
        ? secondsBetween(event.occurred_at)
        : (event.duration_minutes || Math.floor(secondsBetween(event.occurred_at, event.ended_at) / 60)) * 60
      return {
        id: event.id,
        start: event.occurred_at,
        end: event.ended_at,
        durationSeconds,
        status: event.status,
        active: isActive,
        crossedThreshold: durationSeconds >= 3600,
      }
    })
    .sort((a, b) => b.start.localeCompare(a.start))
}

export default function LifePage() {
  const [date, setDate] = useState(todayKey())
  const [mode, setMode] = useState<ViewMode>('day')
  const [summary, setSummary] = useState<LifeSummary | null>(null)
  const [events, setEvents] = useState<LifeEvent[]>([])
  const [reminders, setReminders] = useState<LifeReminder[]>([])
  const [reviews, setReviews] = useState<LifeReviews | null>(null)
  const [week, setWeek] = useState<LifeWeek | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [lastSyncedAt, setLastSyncedAt] = useState('')
  const [clock, setClock] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const savedMode = viewModeFromText(params.get('view')) || viewModeFromText(window.localStorage.getItem(LIFE_VIEW_STORAGE_KEY))
    if (savedMode) setMode(savedMode)
  }, [])

  const changeMode = (nextMode: ViewMode) => {
    setMode(nextMode)
    window.localStorage.setItem(LIFE_VIEW_STORAGE_KEY, nextMode)
    const url = new URL(window.location.href)
    if (nextMode === 'week') {
      url.searchParams.set('view', 'week')
    } else {
      url.searchParams.delete('view')
    }
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }

  useEffect(() => {
    let cancelled = false
    let inFlight = false

    const refresh = (showLoading: boolean) => {
      if (inFlight) return
      inFlight = true
      if (showLoading) {
        setLoading(true)
      } else {
        setRefreshing(true)
      }
      setError('')
      Promise.all([
        fetchLifeSummary(date),
        fetchLifeEvents(date),
        fetchLifeReminders(date),
        fetchLifeReviews(date),
        fetchLifeWeek(date),
      ]).then(([nextSummary, nextEvents, nextReminders, nextReviews, nextWeek]) => {
        if (cancelled) return
        setSummary(nextSummary)
        setEvents(nextEvents)
        setReminders(nextReminders)
        setReviews(nextReviews)
        setWeek(nextWeek)
        setLastSyncedAt(new Date().toISOString())
      }).catch(err => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Life API 请求失败')
      }).finally(() => {
        if (cancelled) return
        inFlight = false
        setLoading(false)
        setRefreshing(false)
      })
    }

    const refreshIfVisible = () => {
      if (cancelled) return
      if (document.visibilityState === 'visible') refresh(false)
    }

    refresh(true)
    const pollingTimer = window.setInterval(refreshIfVisible, 30000)
    document.addEventListener('visibilitychange', refreshIfVisible)

    return () => {
      cancelled = true
      window.clearInterval(pollingTimer)
      document.removeEventListener('visibilitychange', refreshIfVisible)
    }
  }, [date])

  const counts = summary?.metrics.event_counts || {}
  const sittingRows = useMemo(
    () => buildSittingRows(events, summary?.active_sitting || null),
    [events, summary?.active_sitting, clock],
  )
  const activeSitting = sittingRows.find(row => row.active)
  const activeSeconds = activeSitting?.durationSeconds || 0
  const displayedTotalMinutes = (summary?.metrics.sitting_total_minutes || 0) + Math.floor(activeSeconds / 60)
  const sleepSessions = useMemo(() => sleepRows(events), [events])
  const meals = events.filter(event => event.event_type === 'meal')
  const water = events.filter(event => event.event_type === 'water')
  const exercises = events.filter(event => event.event_type === 'exercise')
  const exercisePlans = reminders.filter(isUserExercisePlan)
  const dateShiftDays = mode === 'week' ? 7 : 1

  return (
    <main className="min-h-screen bg-[#f6f2e9] text-neutral-950 dark:bg-neutral-950 dark:text-neutral-100">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-6">
        <header className="mb-5 grid gap-4 border-b border-stone-200 pb-4 dark:border-neutral-800 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-end">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-normal text-emerald-700 dark:text-emerald-400">
              Life Assistant
            </p>
            <h1 className="mt-1 text-2xl font-semibold">我的生活活动日志</h1>
            <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
              {mode === 'week' && week ? `${formatDateRange(week.week_start, week.week_end)} · 周期数据` : `${date} · 只读生活记录`}
            </p>
            <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-500">
              {lastSyncedAt ? `最后同步 ${formatTime(lastSyncedAt)}${refreshing ? ' · 同步中' : ''}` : '等待首次同步'}
            </p>
          </div>
          <div className="flex flex-col gap-2 lg:items-end">
            <div className="flex rounded-md border border-stone-300 bg-white p-1 dark:border-neutral-700 dark:bg-neutral-900">
              <button
                className={`rounded px-3 py-1.5 text-sm ${mode === 'day' ? 'bg-neutral-950 text-white dark:bg-neutral-100 dark:text-neutral-950' : 'text-neutral-600 dark:text-neutral-300'}`}
                onClick={() => changeMode('day')}
              >
                日视图
              </button>
              <button
                className={`rounded px-3 py-1.5 text-sm ${mode === 'week' ? 'bg-neutral-950 text-white dark:bg-neutral-100 dark:text-neutral-950' : 'text-neutral-600 dark:text-neutral-300'}`}
                onClick={() => changeMode('week')}
              >
                周视图
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-2 lg:flex-nowrap">
              <button className="rounded-md border border-stone-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, -dateShiftDays))}>
                {mode === 'week' ? '上一周' : '前一天'}
              </button>
              <input
                type="date"
                value={date}
                onChange={event => setDate(event.target.value)}
                className="w-40 rounded-md border border-stone-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
              />
              <button className="rounded-md border border-stone-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, dateShiftDays))}>
                {mode === 'week' ? '下一周' : '后一天'}
              </button>
              <button className="rounded-md bg-neutral-950 px-3 py-2 text-sm text-white dark:bg-neutral-100 dark:text-neutral-950" onClick={() => setDate(todayKey())}>
                今天
              </button>
            </div>
          </div>
        </header>

        {error && (
          <section className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
            {error}
          </section>
        )}

        {loading ? (
          <section className="rounded-md border border-stone-200 bg-white p-6 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
            加载 Life 数据中...
          </section>
        ) : (
          mode === 'week' ? (
            <WeekView week={week} />
          ) : (
            <div className="space-y-4">
              <SittingSection
                activeSeconds={activeSeconds}
                displayedTotalMinutes={displayedTotalMinutes}
                sedentaryMinutes={summary?.metrics.sedentary_over_limit_minutes || 0}
                rows={sittingRows}
              />

              <ReviewSection reviews={reviews} />

              <div className="grid gap-4 lg:grid-cols-2">
                <SleepSection rows={sleepSessions} />
                <ExerciseSection exercises={exercises} plans={exercisePlans} />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <MealSection meals={meals} />
                <WaterSection water={water} />
              </div>

              <details className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
                <summary className="cursor-pointer text-sm font-semibold">诊断信息</summary>
                <p className="mt-2 text-xs text-neutral-500">提醒状态、dedupe key、payload 和错误信息只在排障时看。</p>
                <div className="mt-3 space-y-2">
                  {reminders.length === 0 ? (
                    <EmptyState text="这一天没有提醒日志。" />
                  ) : reminders.map(reminder => (
                    <ReminderRow key={reminder.id} reminder={reminder} />
                  ))}
                </div>
              </details>
            </div>
          )
        )}

        {!loading && mode === 'day' && (
          <p className="mt-4 text-xs text-neutral-500">
            今日记录：久坐 {sittingRows.length} 段 · 睡眠 {sleepSessions.length} 条 · 饮食 {meals.length} 条 · 喝水 {water.length} 条 · 运动 {exercises.length + exercisePlans.length} 条
          </p>
        )}
      </div>
    </main>
  )
}

function WeekView({ week }: { week: LifeWeek | null }) {
  if (!week) {
    return (
      <section className="rounded-md border border-stone-200 bg-white p-6 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
        这周还没有 Life 数据。
      </section>
    )
  }

  const review = week.weekly_review
  const delivery = review?.delivery_json || {}

  return (
    <div className="space-y-4">
      <WeekRhythmMap week={week} />

      <div className="grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
        <SittingBarChart rows={week.trend_rows} />
        <SleepExerciseChart rows={week.trend_rows} />
      </div>

      <RecordCoverageChart week={week} />

      <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <SectionTitle title="周复盘推送" meta={review ? '已生成' : '未生成'} />
        {!review ? (
          <EmptyState text="这周还没有生成周复盘。" />
        ) : (
          <div className="space-y-3">
            <div className="rounded-md border border-stone-200 px-3 py-3 dark:border-neutral-800">
              <p className="text-sm font-semibold">{review.headline || '周复盘'}</p>
              <p className="mt-2 break-words text-sm leading-6 text-neutral-700 dark:text-neutral-300">{review.summary}</p>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span className="rounded-md bg-stone-100 px-2 py-1 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  微信 {deliveryLabel(delivery.wechat)}
                </span>
                <span className="rounded-md bg-stone-100 px-2 py-1 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  飞书 {deliveryLabel(delivery.feishu)}
                </span>
                <span className="rounded-md bg-stone-100 px-2 py-1 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  {review.report_text.length} 字
                </span>
              </div>
            </div>
            {review.evidence_json.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-neutral-500">本周依据</p>
                <div className="space-y-2">
                  {review.evidence_json.map((item, index) => (
                    <RecordRow key={`${evidenceText(item)}:${index}`} title={`依据 ${index + 1}`} detail={evidenceText(item)} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}

function WeekRhythmMap({ week }: { week: LifeWeek }) {
  const maxSitting = Math.max(1, ...week.trend_rows.map(row => row.sitting_total_minutes))
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <SectionTitle title="本周节律图" meta={formatDateRange(week.week_start, week.week_end)} />
      <div className="space-y-3">
        {week.trend_rows.map((row, index) => (
          <DayRhythmRow key={row.date} row={row} index={index} maxSitting={maxSitting} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-neutral-500">
        <Legend color="bg-sky-500" label="睡眠" />
        <Legend color="bg-emerald-500" label="运动" />
        <Legend color="bg-stone-500" label="坐姿" />
        <Legend color="bg-amber-500" label="久坐" />
      </div>
    </section>
  )
}

function DayRhythmRow({
  row,
  index,
  maxSitting,
}: {
  row: LifeWeekTrendRow
  index: number
  maxSitting: number
}) {
  const sleepWidth = percent(row.sleep_minutes, 10 * 60, row.sleep_minutes ? 3 : 0)
  const sittingWidth = percent(row.sitting_total_minutes, maxSitting, row.sitting_total_minutes ? 3 : 0)
  const sedentaryWidth = percent(row.sedentary_over_limit_minutes, maxSitting, 0)
  const exerciseWidth = percent(row.exercise_minutes, 60, row.exercise_minutes ? 3 : 0)
  const score = [
    row.sleep_minutes > 0,
    row.exercise_count > 0,
    row.meal_count > 0,
    row.water_count > 0,
    row.sedentary_over_limit_minutes === 0,
  ].filter(Boolean).length

  return (
    <div className="grid gap-3 rounded-md border border-stone-200 bg-stone-50 p-3 dark:border-neutral-800 dark:bg-neutral-950 md:grid-cols-[72px_1fr_170px] md:items-center">
      <div>
        <p className="text-sm font-semibold">{WEEKDAY_LABELS[index] || ''}</p>
        <p className="text-xs text-neutral-500">{formatShortDate(row.date)}</p>
      </div>
      <div className="space-y-2">
        <RhythmLane label="睡眠" color="bg-sky-500" width={sleepWidth} text={row.sleep_minutes ? formatMinutes(row.sleep_minutes) : '无记录'} />
        <div className="grid grid-cols-[42px_1fr_74px] items-center gap-2">
          <span className="text-xs text-neutral-500">坐姿</span>
          <div className="relative h-3 overflow-hidden rounded-full bg-stone-200 dark:bg-neutral-800">
            <div className="h-full rounded-full bg-stone-500" style={{ width: `${sittingWidth}%` }} />
            {row.sedentary_over_limit_minutes > 0 && (
              <div className="absolute left-0 top-0 h-full rounded-full bg-amber-500" style={{ width: `${sedentaryWidth}%` }} />
            )}
          </div>
          <span className="truncate text-right text-xs text-neutral-500">{row.sitting_total_minutes ? formatMinutes(row.sitting_total_minutes) : '无记录'}</span>
        </div>
        <RhythmLane label="运动" color="bg-emerald-500" width={exerciseWidth} text={row.exercise_minutes ? formatMinutes(row.exercise_minutes) : '无记录'} />
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-neutral-600 dark:text-neutral-300">生活完整度 {score}/5</p>
        <div className="flex flex-wrap gap-1.5">
          <StatusPill active={row.sleep_minutes > 0} activeText="有睡眠" inactiveText="缺睡眠" />
          <StatusPill active={row.exercise_count > 0} activeText="有运动" inactiveText="缺运动" />
          <StatusPill active={row.meal_count > 0} activeText="有饮食" inactiveText="缺饮食" />
          <StatusPill active={row.water_count > 0} activeText="有喝水" inactiveText="缺喝水" />
          <StatusPill active={row.sedentary_over_limit_minutes === 0} activeText="未久坐" inactiveText={`久坐${formatMinutes(row.sedentary_over_limit_minutes)}`} />
        </div>
      </div>
    </div>
  )
}

function StatusPill({
  active,
  activeText,
  inactiveText,
}: {
  active: boolean
  activeText: string
  inactiveText: string
}) {
  return (
    <span className={`rounded-md px-2 py-1 text-xs ${active ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200' : 'bg-stone-200 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400'}`}>
      {active ? activeText : inactiveText}
    </span>
  )
}

function RhythmLane({
  label,
  color,
  width,
  text,
}: {
  label: string
  color: string
  width: number
  text: string
}) {
  return (
    <div className="grid grid-cols-[42px_1fr_74px] items-center gap-2">
      <span className="text-xs text-neutral-500">{label}</span>
      <div className="h-3 overflow-hidden rounded-full bg-stone-200 dark:bg-neutral-800">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${width}%` }} />
      </div>
      <span className="truncate text-right text-xs text-neutral-500">{text}</span>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
      {label}
    </span>
  )
}

function SittingBarChart({ rows }: { rows: LifeWeekTrendRow[] }) {
  const maxSitting = Math.max(1, ...rows.map(row => row.sitting_total_minutes))
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <SectionTitle title="坐姿压力图" meta="灰色为总坐姿，黄色为久坐" />
      <div className="space-y-3">
        {rows.map((row, index) => {
          const totalWidth = percent(row.sitting_total_minutes, maxSitting, row.sitting_total_minutes ? 3 : 0)
          const overWidth = percent(row.sedentary_over_limit_minutes, maxSitting, 0)
          return (
            <div key={row.date} className="grid grid-cols-[54px_1fr_64px] items-center gap-3 text-sm">
              <div>
                <p className="text-xs font-medium">{WEEKDAY_LABELS[index]}</p>
                <p className="text-xs text-neutral-500">{formatShortDate(row.date)}</p>
              </div>
              <div className="relative h-5 overflow-hidden rounded-full bg-stone-100 dark:bg-neutral-800">
                <div className="h-full rounded-full bg-stone-500" style={{ width: `${totalWidth}%` }} />
                {row.sedentary_over_limit_minutes > 0 && (
                  <div className="absolute left-0 top-0 h-full rounded-full bg-amber-500" style={{ width: `${overWidth}%` }} />
                )}
              </div>
              <p className="text-right text-xs text-neutral-500">{row.sitting_total_minutes ? formatMinutes(row.sitting_total_minutes) : '-'}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function SleepExerciseChart({ rows }: { rows: LifeWeekTrendRow[] }) {
  const hourTicks = [10, 8, 6, 4, 2, 0]
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <SectionTitle title="睡眠 / 运动图" meta="左轴睡眠小时，绿色按60分钟满格" />
      <div className="grid grid-cols-[34px_1fr] gap-2">
        <div className="flex h-44 flex-col justify-between pb-6 text-right text-[11px] text-neutral-500">
          {hourTicks.map(tick => (
            <span key={tick}>{tick}h</span>
          ))}
        </div>
        <div>
          <div className="relative h-44 border-b border-stone-200 dark:border-neutral-800">
            <div className="absolute inset-0 flex flex-col justify-between">
              {hourTicks.map(tick => (
                <div key={tick} className="border-t border-stone-100 first:border-stone-200 dark:border-neutral-800" />
              ))}
            </div>
            <div className="relative z-10 grid h-full grid-cols-7 items-end gap-2 px-1">
              {rows.map((row, index) => (
                <div key={row.date} className="flex h-full flex-col items-center justify-end gap-2">
                  <div className="flex h-36 items-end gap-1">
                    <div
                      className="w-5 rounded-t bg-sky-500"
                      style={{ height: `${percent(row.sleep_minutes, 10 * 60, row.sleep_minutes ? 5 : 0)}%` }}
                      title={`睡眠 ${formatMinutes(row.sleep_minutes)}`}
                    />
                    <div
                      className="w-3 rounded-t bg-emerald-500"
                      style={{ height: `${percent(row.exercise_minutes, 60, row.exercise_minutes ? 5 : 0)}%` }}
                      title={`运动 ${formatMinutes(row.exercise_minutes)}`}
                    />
                  </div>
                  <div className="text-center">
                    <p className="text-xs font-medium">{WEEKDAY_LABELS[index].slice(1)}</p>
                    <p className="text-[11px] text-neutral-500">{formatShortDate(row.date)}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-neutral-500">
        <p>睡眠记录 {rows.filter(row => row.sleep_minutes > 0).length} 天</p>
        <p className="text-right">运动记录 {rows.filter(row => row.exercise_count > 0).length} 天</p>
      </div>
    </section>
  )
}

function RecordCoverageChart({ week }: { week: LifeWeek }) {
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <SectionTitle title="记录覆盖图" meta="看这一周哪些模块真的留下了记录" />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <CoverageStrip label="睡眠" color="bg-sky-500" rows={week.trend_rows} active={row => row.sleep_minutes > 0} />
        <CoverageStrip label="运动" color="bg-emerald-500" rows={week.trend_rows} active={row => row.exercise_count > 0} />
        <CoverageStrip label="饮食" color="bg-rose-500" rows={week.trend_rows} active={row => row.meal_count > 0} value={row => row.meal_count} suffix="餐" />
        <CoverageStrip label="喝水" color="bg-cyan-500" rows={week.trend_rows} active={row => row.water_count > 0} value={row => row.water_count} suffix="次" />
      </div>
    </section>
  )
}

function CoverageStrip({
  label,
  color,
  rows,
  active,
  value,
  suffix = '',
}: {
  label: string
  color: string
  rows: LifeWeekTrendRow[]
  active: (row: LifeWeekTrendRow) => boolean
  value?: (row: LifeWeekTrendRow) => number
  suffix?: string
}) {
  const activeCount = rows.filter(active).length
  return (
    <div className="rounded-md border border-stone-200 bg-stone-50 p-3 dark:border-neutral-800 dark:bg-neutral-950">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm font-semibold">{label}</p>
        <p className="text-xs text-neutral-500">{activeCount}/7 天</p>
      </div>
      <div className="grid grid-cols-7 gap-1">
        {rows.map((row, index) => (
          <div key={row.date} className="text-center">
            <div className={`flex h-8 items-center justify-center rounded text-xs font-semibold ${active(row) ? `${color} text-white` : 'bg-stone-200 text-neutral-400 dark:bg-neutral-800'}`}>
              {value && value(row) > 0 ? `${value(row)}${suffix}` : ''}
            </div>
            <p className="mt-1 text-[10px] text-neutral-500">{WEEKDAY_LABELS[index].slice(1)}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function ReviewSection({ reviews }: { reviews: LifeReviews | null }) {
  const daily = reviews?.daily_review || null
  const weekly = reviews?.weekly_review || null
  const decisions = reviews?.long_term_decisions || []
  const count = (daily ? 1 : 0) + (weekly ? 1 : 0) + decisions.length

  return (
    <ModuleSection title="定期复盘" meta={count ? `${count} 条` : '暂无'}>
      {!daily && !weekly && decisions.length === 0 ? (
        <EmptyState text="这一天还没有可查看的复盘结果。" />
      ) : (
        <div className="grid gap-2 lg:grid-cols-2">
          {daily && <DailyReviewCard review={daily} />}
          {weekly && <WeeklyReviewCard review={weekly} />}
          {decisions.map(decision => (
            <LongTermDecisionCard key={decision.id} decision={decision} />
          ))}
        </div>
      )}
    </ModuleSection>
  )
}

function DailyReviewCard({ review }: { review: LifeDailyReview }) {
  return (
    <div className="rounded-md border border-stone-200 px-3 py-3 dark:border-neutral-800">
      <p className="text-sm font-semibold">日复盘 · {review.review_date}</p>
      <p className="mt-2 break-words text-sm leading-6 text-neutral-700 dark:text-neutral-300">{review.summary}</p>
      <p className="mt-2 text-xs text-neutral-500">生成于 {formatTime(review.created_at)}</p>
    </div>
  )
}

function WeeklyReviewCard({ review }: { review: LifeWeeklyReview }) {
  return (
    <div className="rounded-md border border-stone-200 px-3 py-3 dark:border-neutral-800">
      <p className="text-sm font-semibold">周复盘 · {formatDateRange(review.week_start, review.week_end)}</p>
      <p className="mt-2 break-words text-sm leading-6 text-neutral-700 dark:text-neutral-300">{review.summary}</p>
      {review.recommendations_json.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-neutral-600 dark:text-neutral-400">
          {review.recommendations_json.map(item => (
            <li key={item} className="break-words">{item}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-neutral-500">生成于 {formatTime(review.created_at)}</p>
    </div>
  )
}

function LongTermDecisionCard({ decision }: { decision: LifeLongTermDecision }) {
  const latest = decision.latest_review
  const nextReviewText = decision.next_review_at
    ? `下次复盘 ${formatDateTime(decision.next_review_at)}`
    : '待补下次复盘时间'
  const intervalText = decision.review_interval_days > 0
    ? ` · 间隔 ${decision.review_interval_days} 天`
    : ''
  return (
    <div className="rounded-md border border-stone-200 px-3 py-3 dark:border-neutral-800">
      <p className="text-sm font-semibold">长期决定</p>
      <p className="mt-2 break-words text-sm leading-6 text-neutral-700 dark:text-neutral-300">{decision.content}</p>
      <div className="mt-2 space-y-1 text-xs text-neutral-500">
        <p>{nextReviewText}{intervalText}</p>
        {latest && <p className="break-words">上次复盘：{latest.review_note}</p>}
      </div>
    </div>
  )
}

function SittingSection({
  activeSeconds,
  displayedTotalMinutes,
  sedentaryMinutes,
  rows,
}: {
  activeSeconds: number
  displayedTotalMinutes: number
  sedentaryMinutes: number
  rows: SittingRow[]
}) {
  const activeRow = rows.find(row => row.active)
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="grid gap-4 lg:grid-cols-[.9fr_1.1fr]">
        <div className="rounded-md bg-emerald-50 p-4 text-emerald-950 dark:bg-emerald-950 dark:text-emerald-100">
          <p className="text-sm font-semibold">当前连续坐姿</p>
          <p className="mt-3 font-mono text-4xl font-semibold tracking-normal">
            {activeRow ? formatClockSeconds(activeSeconds) : '00:00:00'}
          </p>
          <p className="mt-2 text-sm opacity-75">
            {activeRow ? `从 ${formatTime(activeRow.start)} 开始，实时计时到秒` : '当前没有进行中的坐姿窗口'}
          </p>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <MiniStat label="今日总坐姿" value={formatMinutes(displayedTotalMinutes)} />
            <MiniStat label="久坐超标" value={formatMinutes(sedentaryMinutes)} />
          </div>
        </div>

        <div>
          <SectionTitle title="单次坐姿记录" meta={`${rows.length} 段`} />
          <div className="space-y-2">
            {rows.length === 0 ? (
              <EmptyState text="这一天还没有坐姿记录。" />
            ) : rows.map(row => (
              <div key={row.id} className="flex items-start justify-between gap-3 rounded-md border border-stone-200 px-3 py-2 dark:border-neutral-800">
                <div>
                  <p className="text-sm font-medium">
                    {formatTime(row.start)} - {row.active ? '现在' : formatTime(row.end)}
                  </p>
                  <p className="mt-1 text-xs text-neutral-500">
                    {row.active ? formatDurationSeconds(row.durationSeconds) : formatMinutes(Math.floor(row.durationSeconds / 60))}
                  </p>
                </div>
                <span className={`rounded-md px-2 py-1 text-xs ${row.crossedThreshold ? 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100' : 'bg-stone-100 text-stone-600 dark:bg-neutral-800 dark:text-neutral-300'}`}>
                  {row.active ? '进行中' : row.crossedThreshold ? '久坐' : '未超标'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

function SleepSection({ rows }: { rows: SleepSession[] }) {
  return (
    <ModuleSection title="睡眠记录" meta={`${rows.length} 条`}>
      {rows.length === 0 ? (
        <EmptyState text="这一天还没有睡眠记录。" />
      ) : rows.map(row => (
        <RecordRow
          key={row.id}
          title={row.start && row.end ? `${formatTime(row.start.occurred_at)} - ${formatTime(row.end.occurred_at)}` : row.start ? `入睡 ${formatTime(row.start.occurred_at)}` : `醒来 ${formatTime(row.end?.occurred_at)}`}
          detail={row.start && row.end ? `已配对 · ${formatMinutes(row.durationMinutes)}` : '未配对记录'}
          note={[row.start?.raw_text, row.end?.raw_text].filter(Boolean).join(' / ')}
        />
      ))}
    </ModuleSection>
  )
}

function MealSection({ meals }: { meals: LifeEvent[] }) {
  return (
    <ModuleSection title="饮食记录" meta={`${meals.length} 条`}>
      {meals.length === 0 ? (
        <EmptyState text="这一天还没有饮食记录。" />
      ) : meals.map(meal => {
        const detail = payloadString(meal.payload_json, 'content') || eventText(meal)
        const notes = []
        if (!mealHasExactTime(meal)) notes.push('时间未填写')
        return (
          <RecordRow
            key={meal.id}
            title={mealHasExactTime(meal)
              ? `${formatTime(meal.occurred_at)} · ${mealSlotLabel(meal.payload_json.meal_slot)}`
              : mealSlotLabel(meal.payload_json.meal_slot)}
            detail={detail}
            note={notes.join(' · ')}
          />
        )
      })}
    </ModuleSection>
  )
}

function WaterSection({ water }: { water: LifeEvent[] }) {
  return (
    <ModuleSection title="喝水记录" meta={`${water.length} 条`}>
      {water.length === 0 ? (
        <EmptyState text="这一天还没有喝水记录。" />
      ) : water.map(item => (
        <RecordRow
          key={item.id}
          title={`${formatTime(item.occurred_at)} · 喝水`}
          detail={item.raw_text || '喝水记录'}
        />
      ))}
    </ModuleSection>
  )
}

function ExerciseSection({
  exercises,
  plans,
}: {
  exercises: LifeEvent[]
  plans: LifeReminder[]
}) {
  const rows = [
    ...plans.map(plan => ({
      id: plan.id,
      title: `${formatTime(plan.scheduled_for)} · 运动安排`,
      detail: exercisePlanDetail(plan),
      note: statusLabel(plan.status),
    })),
    ...exercises.map(exercise => ({
      id: exercise.id,
      title: `${formatTime(exercise.occurred_at)} · 运动完成`,
      detail: payloadText(exercise.payload_json) || eventText(exercise),
      note: exercise.duration_minutes ? formatMinutes(exercise.duration_minutes) : statusLabel(exercise.status),
    })),
  ]

  return (
    <ModuleSection title="运动记录" meta={`${rows.length} 条`}>
      {rows.length === 0 ? (
        <EmptyState text="这一天还没有运动记录或运动安排。" />
      ) : rows.map(row => (
        <RecordRow key={row.id} title={row.title} detail={row.detail} note={row.note} />
      ))}
    </ModuleSection>
  )
}

function ModuleSection({
  title,
  meta,
  children,
}: {
  title: string
  meta: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-md border border-stone-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <SectionTitle title={title} meta={meta} />
      <div className="space-y-2">{children}</div>
    </section>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white/70 p-3 dark:bg-neutral-900/50">
      <p className="text-xs opacity-70">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  )
}

function SectionTitle({ title, meta }: { title: string; meta: string }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h2 className="text-base font-semibold">{title}</h2>
      <span className="text-xs text-neutral-500 dark:text-neutral-400">{meta}</span>
    </div>
  )
}

function RecordRow({
  title,
  detail,
  note,
}: {
  title: string
  detail: string
  note?: string
}) {
  return (
    <div className="rounded-md border border-stone-200 px-3 py-2 dark:border-neutral-800">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 break-words text-sm text-neutral-700 dark:text-neutral-300">{detail}</p>
      {note && <p className="mt-1 break-words text-xs text-neutral-500">{note}</p>}
    </div>
  )
}

function ReminderRow({ reminder }: { reminder: LifeReminder }) {
  const payload = payloadText(reminder.payload_json)
  return (
    <div className="rounded-md border border-stone-200 px-3 py-2 text-xs dark:border-neutral-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{REMINDER_LABELS[reminder.reminder_type] || reminder.reminder_type} · {statusLabel(reminder.status)}</p>
          <p className="mt-1 break-all text-neutral-500">{reminder.dedupe_key}</p>
          {payload && <p className="mt-1 break-words text-neutral-500">{payload}</p>}
          {reminder.error && <p className="mt-1 break-words text-red-600">{reminder.error}</p>}
        </div>
        <time className="shrink-0 text-neutral-500">{formatTime(reminder.scheduled_for)}</time>
      </div>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-stone-300 bg-stone-50 px-4 py-6 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950 dark:text-neutral-400">
      {text}
    </div>
  )
}
