import type { Capture } from '../lib/api'
import { TagBadge } from './TagBadge'

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 86400000) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

export function CaptureCard({
  capture,
  onTagClick,
}: {
  capture: Capture
  onTagClick?: (tag: string) => void
}) {
  const notePath = capture.file_path || capture.raw_path

  return (
    <div className="p-4 rounded-lg border border-neutral-200
      dark:border-neutral-800 hover:border-neutral-300
      dark:hover:border-neutral-700 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm leading-relaxed text-neutral-900 dark:text-neutral-100">
            {capture.title ? (
              <span className="font-medium">{capture.title}</span>
            ) : null}
            {capture.title && ' — '}
            {capture.summary}
          </p>
          <div className="flex flex-wrap gap-1 mt-2">
            {capture.tags?.map(tag => (
              <button key={tag} type="button" onClick={() => onTagClick?.(tag)}>
                <TagBadge tag={tag} />
              </button>
            ))}
          </div>
        </div>
        <span className="text-xs text-neutral-400 shrink-0">
          {formatDate(capture.created_at)}
        </span>
      </div>

      <div className="flex items-center gap-3 mt-3 text-xs text-neutral-400">
        <span>{capture.source_type}</span>
        <span>{capture.status}</span>
        {capture.word_count > 0 && (
          <span>{capture.word_count} 字</span>
        )}
        {notePath && (
          <span>{notePath}</span>
        )}
      </div>
    </div>
  )
}
