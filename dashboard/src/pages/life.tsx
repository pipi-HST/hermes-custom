import { useEffect, useMemo, useState } from 'react'
import {
  fetchLifeEvents,
  fetchLifeReminders,
  fetchLifeSummary,
  type LifeEvent,
  type LifeReminder,
  type LifeSummary,
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
}

const STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  auto_resolved: '自动结束',
  confirmed: '已确认',
  completed_unknown: '已完成',
  scheduled: '已安排',
  sending: '已触发',
  skipped: '已跳过',
  paused: '已暂停',
  failed: '失败',
}

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

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status
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

function mealSlotLabel(slot: unknown): string {
  if (slot === 'breakfast') return '早餐'
  if (slot === 'lunch') return '午饭'
  if (slot === 'dinner') return '晚饭'
  if (slot === 'snack') return '加餐'
  return '饮食'
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
  const [summary, setSummary] = useState<LifeSummary | null>(null)
  const [events, setEvents] = useState<LifeEvent[]>([])
  const [reminders, setReminders] = useState<LifeReminder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [clock, setClock] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    Promise.all([
      fetchLifeSummary(date),
      fetchLifeEvents(date),
      fetchLifeReminders(date),
    ]).then(([nextSummary, nextEvents, nextReminders]) => {
      if (cancelled) return
      setSummary(nextSummary)
      setEvents(nextEvents)
      setReminders(nextReminders)
      setLoading(false)
    }).catch(err => {
      if (cancelled) return
      setError(err instanceof Error ? err.message : 'Life API 请求失败')
      setLoading(false)
    })
    return () => {
      cancelled = true
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
  const exercisePlans = reminders.filter(reminder => reminder.reminder_type === 'exercise')

  return (
    <main className="min-h-screen bg-[#f6f2e9] text-neutral-950 dark:bg-neutral-950 dark:text-neutral-100">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-6">
        <header className="mb-5 flex flex-col gap-3 border-b border-stone-200 pb-4 dark:border-neutral-800 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-normal text-emerald-700 dark:text-emerald-400">
              Life Assistant
            </p>
            <h1 className="mt-1 text-2xl font-semibold">我的生活活动日志</h1>
            <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
              {date} · 只读生活记录
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="rounded-md border border-stone-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, -1))}>
              前一天
            </button>
            <input
              type="date"
              value={date}
              onChange={event => setDate(event.target.value)}
              className="rounded-md border border-stone-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            />
            <button className="rounded-md border border-stone-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, 1))}>
              后一天
            </button>
            <button className="rounded-md bg-neutral-950 px-3 py-2 text-sm text-white dark:bg-neutral-100 dark:text-neutral-950" onClick={() => setDate(todayKey())}>
              今天
            </button>
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
          <div className="space-y-4">
            <SittingSection
              activeSeconds={activeSeconds}
              displayedTotalMinutes={displayedTotalMinutes}
              sedentaryMinutes={summary?.metrics.sedentary_over_limit_minutes || 0}
              rows={sittingRows}
            />

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
        )}

        {!loading && (
          <p className="mt-4 text-xs text-neutral-500">
            今日记录：久坐 {sittingRows.length} 段 · 睡眠 {sleepSessions.length} 条 · 饮食 {meals.length} 条 · 喝水 {water.length} 条 · 运动 {exercises.length + exercisePlans.length} 条
          </p>
        )}
      </div>
    </main>
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
      ) : meals.map(meal => (
        <RecordRow
          key={meal.id}
          title={`${formatTime(meal.occurred_at)} · ${mealSlotLabel(meal.payload_json.meal_slot)}`}
          detail={String(meal.payload_json.content || eventText(meal))}
          note={meal.raw_text}
        />
      ))}
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
      detail: payloadText(plan.payload_json) || statusLabel(plan.status),
      note: plan.status === 'skipped' ? '今天不练' : '等待完成记录',
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
