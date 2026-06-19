import { useMemo, useState } from 'react'
import type { CaptureDraft, ReadingDraft } from '../../lib/api'

interface WorkbenchDraftQueueProps {
  mode: 'capture-review' | 'draft-review' | 'chat' | 'editable-capture'
  drafts: ReadingDraft[]
  selectedDraft: ReadingDraft | null
  captureDrafts: CaptureDraft[]
  selectedCapture: CaptureDraft | null
  captureLoading: boolean
  onDraftSelect: (draft: ReadingDraft) => void
  onCaptureSelect: (draft: CaptureDraft) => void
  outputTypeLabel: (type: string) => string
}

export function WorkbenchDraftQueue({
  mode,
  drafts,
  selectedDraft,
  captureDrafts,
  selectedCapture,
  captureLoading,
  onDraftSelect,
  onCaptureSelect,
  outputTypeLabel,
}: WorkbenchDraftQueueProps) {
  const isCaptureReview = mode === 'capture-review'
  const [draftFilter, setDraftFilter] = useState<'open' | 'all'>('open')
  const filteredDrafts = useMemo(
    () => draftFilter === 'all' ? drafts : drafts.filter(draft => draft.status === 'open'),
    [draftFilter, drafts],
  )

  return (
    <aside className="w-64 shrink-0 border-r border-neutral-200 dark:border-neutral-800
      flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
        <h3 className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
          {isCaptureReview ? '捕获草稿' : '草稿列表'}
        </h3>
        {!isCaptureReview && (
          <div className="mt-3 flex gap-2">
            {(['open', 'all'] as const).map(filter => (
              <button
                key={filter}
                onClick={() => setDraftFilter(filter)}
                className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
                  draftFilter === filter
                    ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
                    : 'border-neutral-200 text-neutral-500 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900'
                }`}
              >
                {filter === 'open' ? '待审阅' : '全部'}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {isCaptureReview ? (
          captureLoading ? (
            <p className="text-xs text-neutral-400 px-2 py-4">加载中...</p>
          ) : captureDrafts.length === 0 ? (
            <p className="text-xs text-neutral-400 px-2 py-4">暂无捕获草稿</p>
          ) : (
            captureDrafts.map(draft => (
              <button
                key={draft.id}
                onClick={() => onCaptureSelect(draft)}
                className={`w-full text-left px-3 py-2 rounded-md mb-1 text-sm transition-colors
                  ${selectedCapture?.id === draft.id
                    ? 'bg-neutral-200 dark:bg-neutral-800'
                    : 'hover:bg-neutral-100 dark:hover:bg-neutral-900'
                  }`}
              >
                <div className="font-medium text-neutral-800 dark:text-neutral-200 truncate">
                  {draft.title || '(无标题)'}
                </div>
                <div className="text-xs text-neutral-400 mt-0.5">
                  {draft.source_type} · {draft.status}
                </div>
                <div className="text-xs text-neutral-400 truncate">
                  {draft.summary || `${draft.word_count} 字`}
                </div>
              </button>
            ))
          )
        ) : (
          filteredDrafts.length === 0 ? (
            <p className="text-xs text-neutral-400 px-2 py-4">
              {draftFilter === 'open' ? '暂无待审阅草稿' : '暂无草稿'}
            </p>
          ) : (
            filteredDrafts.map(draft => (
              <button
                key={draft.id}
                onClick={() => onDraftSelect(draft)}
                className={`w-full text-left px-3 py-2 rounded-md mb-1 text-sm transition-colors
                  ${selectedDraft?.id === draft.id
                    ? 'bg-neutral-200 dark:bg-neutral-800'
                    : 'hover:bg-neutral-100 dark:hover:bg-neutral-900'
                  }`}
              >
                <div className="font-medium text-neutral-800 dark:text-neutral-200 truncate">
                  {draft.title || '(无标题)'}
                </div>
                <div className="text-xs text-neutral-400 mt-0.5">
                  {outputTypeLabel(draft.output_type)} · {draft.status}
                </div>
                {draft.summary && (
                  <div className="text-xs text-neutral-500 mt-1 line-clamp-2">
                    {draft.summary}
                  </div>
                )}
              </button>
            ))
          )
        )}
      </div>
    </aside>
  )
}
