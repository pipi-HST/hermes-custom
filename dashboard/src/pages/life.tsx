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
  sedentary: '久坐',
  exercise: '运动',
  water: '喝水',
  sleep: '早睡',
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

function formatTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(11, 16) || value
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatMinutes(minutes?: number): string {
  const value = minutes || 0
  const hours = Math.floor(value / 60)
  const rest = value % 60
  if (hours && rest) return `${hours}小时${rest}分`
  if (hours) return `${hours}小时`
  return `${rest}分`
}

function eventLabel(type: string): string {
  return EVENT_LABELS[type] || type
}

function reminderLabel(type: string): string {
  return REMINDER_LABELS[type] || type
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

function eventSummary(event: LifeEvent): string {
  const payload = payloadText(event.payload_json)
  if (event.raw_text) return event.raw_text
  if (payload) return payload
  if (event.duration_minutes) return formatMinutes(event.duration_minutes)
  return eventLabel(event.event_type)
}

export default function LifePage() {
  const [date, setDate] = useState(todayKey())
  const [summary, setSummary] = useState<LifeSummary | null>(null)
  const [events, setEvents] = useState<LifeEvent[]>([])
  const [reminders, setReminders] = useState<LifeReminder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

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
  const latestReminderRows = useMemo(() => Object.values(summary?.latest_reminders || {}), [summary])
  const sleepCount = (counts.sleep_start || 0) + (counts.sleep_end || 0)

  return (
    <main className="min-h-screen bg-[#f7f7f4] text-neutral-950 dark:bg-neutral-950 dark:text-neutral-100">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-6">
        <header className="mb-5 flex flex-col gap-3 border-b border-neutral-200 pb-4 dark:border-neutral-800 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-normal text-emerald-700 dark:text-emerald-400">
              Life Assistant
            </p>
            <h1 className="mt-1 text-2xl font-semibold">生活记录运维看板</h1>
            <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
              {date} · 只读视图
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, -1))}>
              前一天
            </button>
            <input
              type="date"
              value={date}
              onChange={event => setDate(event.target.value)}
              className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            />
            <button className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700" onClick={() => setDate(shiftDate(date, 1))}>
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

        <section className="mb-4 grid gap-3 md:grid-cols-4">
          <Metric label="累计坐姿" value={formatMinutes(summary?.metrics.sitting_total_minutes)} detail={summary?.active_sitting ? '当前有进行中窗口' : '已完成窗口合计'} tone="emerald" />
          <Metric label="久坐超标" value={formatMinutes(summary?.metrics.sedentary_over_limit_minutes)} detail="单次坐姿满 60 分钟计入" tone="amber" />
          <Metric label="运动" value={`${counts.exercise || 0} 条`} detail={latestReminderRows.find(row => row.reminder_type === 'exercise') ? '有运动提醒记录' : '无运动提醒'} tone="blue" />
          <Metric label="睡眠 / 喝水 / 饮食" value={`${sleepCount} / ${counts.water || 0} / ${counts.meal || 0}`} detail="今日记录数" tone="neutral" />
        </section>

        {loading ? (
          <section className="rounded-md border border-neutral-200 bg-white p-6 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
            加载 Life 数据中...
          </section>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[1.35fr_.85fr]">
            <section>
              <SectionTitle title="生活记录时间线" meta={`${events.length} 条`} />
              <div className="space-y-2">
                {events.length === 0 ? (
                  <EmptyState text="这一天还没有 Life 记录。" />
                ) : events.map(event => (
                  <EventRow key={event.id} event={event} />
                ))}
              </div>
            </section>

            <aside className="space-y-4">
              <section>
                <SectionTitle title="最近提醒状态" meta={`${latestReminderRows.length} 类`} />
                <div className="space-y-2">
                  {latestReminderRows.length === 0 ? (
                    <EmptyState text="这一天还没有提醒状态。" />
                  ) : latestReminderRows.map(reminder => (
                    <ReminderRow key={reminder.id} reminder={reminder} compact />
                  ))}
                </div>
              </section>

              <section>
                <SectionTitle title="提醒日志" meta={`${reminders.length} 条`} />
                <div className="space-y-2">
                  {reminders.length === 0 ? (
                    <EmptyState text="这一天还没有提醒日志。" />
                  ) : reminders.map(reminder => (
                    <ReminderRow key={reminder.id} reminder={reminder} />
                  ))}
                </div>
              </section>
            </aside>
          </div>
        )}
      </div>
    </main>
  )
}

function Metric({
  label,
  value,
  detail,
  tone,
}: {
  label: string
  value: string
  detail: string
  tone: 'emerald' | 'amber' | 'blue' | 'neutral'
}) {
  const tones = {
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-100',
    amber: 'border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100',
    blue: 'border-sky-200 bg-sky-50 text-sky-950 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100',
    neutral: 'border-neutral-200 bg-white text-neutral-950 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100',
  }
  return (
    <div className={`min-h-[116px] rounded-md border p-4 ${tones[tone]}`}>
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-3 text-2xl font-semibold">{value}</p>
      <p className="mt-2 text-xs opacity-70">{detail}</p>
    </div>
  )
}

function SectionTitle({ title, meta }: { title: string; meta: string }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h2 className="text-base font-semibold">{title}</h2>
      <span className="text-xs text-neutral-500 dark:text-neutral-400">{meta}</span>
    </div>
  )
}

function EventRow({ event }: { event: LifeEvent }) {
  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{eventLabel(event.event_type)}</span>
            <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              {statusLabel(event.status)}
            </span>
            {event.duration_minutes ? (
              <span className="text-xs text-neutral-500">{formatMinutes(event.duration_minutes)}</span>
            ) : null}
          </div>
          <p className="mt-2 break-words text-sm text-neutral-700 dark:text-neutral-300">{eventSummary(event)}</p>
          <p className="mt-2 break-all text-xs text-neutral-400">{event.source || 'unknown'} · {event.id}</p>
        </div>
        <time className="shrink-0 text-sm text-neutral-500">{formatTime(event.occurred_at)}</time>
      </div>
    </div>
  )
}

function ReminderRow({ reminder, compact = false }: { reminder: LifeReminder; compact?: boolean }) {
  const payload = payloadText(reminder.payload_json)
  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{reminderLabel(reminder.reminder_type)}</span>
            <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              {statusLabel(reminder.status)}
            </span>
          </div>
          {!compact && (
            <>
              <p className="mt-2 break-all text-xs text-neutral-500">{reminder.dedupe_key}</p>
              {payload && <p className="mt-1 break-words text-xs text-neutral-500">{payload}</p>}
              {reminder.error && <p className="mt-1 break-words text-xs text-red-600">{reminder.error}</p>}
            </>
          )}
        </div>
        <time className="shrink-0 text-sm text-neutral-500">{formatTime(reminder.scheduled_for)}</time>
      </div>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-neutral-300 bg-white px-4 py-6 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400">
      {text}
    </div>
  )
}
