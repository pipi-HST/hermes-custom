import { useEffect, useState } from 'react'
import type { ReadingDraft } from '../lib/api'
import { fetchReadingDrafts, fetchKnowledgeDrafts } from '../lib/api'
import { DraftReviewPanel } from '../components/DraftReviewPanel'

export default function DraftsPage() {
  const [drafts, setDrafts] = useState<ReadingDraft[]>([])
  const [selectedDraft, setSelectedDraft] = useState<ReadingDraft | null>(null)
  const [filter, setFilter] = useState<'all' | 'open' | 'committed'>('open')

  useEffect(() => {
    loadDrafts()
  }, [])

  const loadDrafts = async () => {
    const [readingDrafts, knowledgeDrafts] = await Promise.all([
      fetchReadingDrafts(50),
      fetchKnowledgeDrafts(50),
    ])
    const seen = new Set<string>()
    const merged = [...readingDrafts, ...knowledgeDrafts].filter(d => {
      if (seen.has(d.id)) return false
      seen.add(d.id)
      return true
    })
    setDrafts(merged)
    setSelectedDraft(prev => {
      if (!prev) return prev
      return merged.find(draft => draft.id === prev.id) || prev
    })
  }

  const handleCommitted = () => {
    setSelectedDraft(null)
    loadDrafts()
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <h1 className="text-lg font-medium mb-6 text-neutral-900 dark:text-neutral-100">
        草稿审阅
      </h1>

      <div className="flex gap-6">
        {/* Draft list */}
        <div className="w-72 shrink-0">
          <div className="flex gap-2 mb-3">
            {(['open', 'all'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 text-xs rounded-md border transition-colors
                  ${filter === f
                    ? 'border-neutral-400 dark:border-neutral-600 bg-neutral-100 dark:bg-neutral-800'
                    : 'border-neutral-200 dark:border-neutral-700 text-neutral-500'
                  }`}
              >
                {f === 'open' ? '待审阅' : '全部'}
              </button>
            ))}
          </div>

          {drafts.length === 0 ? (
            <p className="text-sm text-neutral-400 py-4">暂无草稿</p>
          ) : (
            <div className="flex flex-col gap-1">
              {drafts
                .filter(d => filter === 'all' || d.status === 'open')
                .map(draft => (
                  <button
                    key={draft.id}
                    onClick={() => setSelectedDraft(draft)}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors
                      ${selectedDraft?.id === draft.id
                        ? 'bg-neutral-200 dark:bg-neutral-800'
                        : 'hover:bg-neutral-100 dark:hover:bg-neutral-900'
                      }`}
                  >
                    <div className="font-medium text-neutral-800 dark:text-neutral-200 truncate">
                      {draft.title || '(无标题)'}
                    </div>
                    <div className="text-xs text-neutral-400 mt-0.5 flex justify-between">
                      <span>{outputTypeLabel(draft.output_type)}</span>
                      <span>{draft.status}</span>
                    </div>
                    {draft.summary && (
                      <div className="text-xs text-neutral-500 mt-1 line-clamp-2">
                        {draft.summary}
                      </div>
                    )}
                  </button>
                ))}
            </div>
          )}
        </div>

        {/* Draft review panel */}
        <div className="flex-1 min-w-0">
          {selectedDraft ? (
            <DraftReviewPanel
              draft={selectedDraft}
              onCommitted={handleCommitted}
              onBack={() => setSelectedDraft(null)}
              onRevised={loadDrafts}
            />
          ) : (
            <div className="text-center text-neutral-400 text-sm py-20">
              从左侧选择一个草稿进行审阅
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function outputTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    enhance_capture: '增强原文',
    local_learning_note: '学习笔记',
    knowledge_card: '知识卡片',
  }
  return labels[type] || type
}
