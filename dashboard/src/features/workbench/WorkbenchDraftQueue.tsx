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

  return (
    <aside className="w-64 shrink-0 border-r border-neutral-200 dark:border-neutral-800
      flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
        <h3 className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
          {isCaptureReview ? '捕获草稿' : '草稿列表'}
        </h3>
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
          drafts.length === 0 ? (
            <p className="text-xs text-neutral-400 px-2 py-4">暂无待审阅草稿</p>
          ) : (
            drafts.map(draft => (
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
              </button>
            ))
          )
        )}
      </div>
    </aside>
  )
}
