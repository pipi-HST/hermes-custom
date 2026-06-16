import { useState } from 'react'
import type { ReadingDraft } from '../lib/api'
import {
  commitReadingDraft,
  reviseReadingDraft,
  commitKnowledgeDraft,
  reviseKnowledgeDraft,
} from '../lib/api'

interface DraftReviewPanelProps {
  draft: ReadingDraft | null
  onCommitted: () => void
  onBack: () => void
}

export function DraftReviewPanel({ draft, onCommitted, onBack }: DraftReviewPanelProps) {
  const [revisionInstruction, setRevisionInstruction] = useState('')
  const [isRevising, setIsRevising] = useState(false)
  const [isCommitting, setIsCommitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  if (!draft) {
    return (
      <div className="flex-1 flex items-center justify-center text-neutral-400 text-sm py-20">
        选择一个草稿进行审阅
      </div>
    )
  }

  const handleRevise = async () => {
    if (!revisionInstruction.trim()) return
    setIsRevising(true)
    setError(null)
    try {
      // Use the appropriate revise endpoint based on context
      await reviseReadingDraft(draft.id, revisionInstruction)
      setRevisionInstruction('')
      // Refresh the draft to get updated content
      window.location.reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : '修改失败')
    } finally {
      setIsRevising(false)
    }
  }

  const handleCommit = async () => {
    setIsCommitting(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const result = await commitReadingDraft(draft.id)
      setSuccessMessage('草稿已提交到知识库')
      setTimeout(onCommitted, 1500)
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
    } finally {
      setIsCommitting(false)
    }
  }

  const outputTypeLabels: Record<string, string> = {
    enhance_capture: '增强原文',
    local_learning_note: '学习笔记',
    knowledge_card: '知识卡片',
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3
        border-b border-neutral-200 dark:border-neutral-800">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="text-sm text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300"
          >
            ← 返回
          </button>
          <h3 className="text-sm font-medium text-neutral-800 dark:text-neutral-200">
            {draft.title || '(无标题)'}
          </h3>
          <span className="text-xs px-2 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800
            text-neutral-600 dark:text-neutral-400">
            {outputTypeLabels[draft.output_type] || draft.output_type}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleCommit}
            disabled={isCommitting || draft.status !== 'open'}
            className="px-3 py-1.5 text-xs rounded-md bg-neutral-900 dark:bg-neutral-100
              text-white dark:text-neutral-900 disabled:opacity-40 transition-opacity"
          >
            {isCommitting ? '提交中...' : '确认提交'}
          </button>
        </div>
      </div>

      {/* Draft content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && (
          <div className="mb-4 px-3 py-2 rounded-md bg-red-50 dark:bg-red-950
            text-red-700 dark:text-red-300 text-sm">
            {error}
          </div>
        )}
        {successMessage && (
          <div className="mb-4 px-3 py-2 rounded-md bg-green-50 dark:bg-green-950
            text-green-700 dark:text-green-300 text-sm">
            {successMessage}
          </div>
        )}

        {draft.summary && (
          <div className="mb-4">
            <h4 className="text-xs font-medium text-neutral-500 mb-1">摘要</h4>
            <p className="text-sm text-neutral-700 dark:text-neutral-300">{draft.summary}</p>
          </div>
        )}

        <div className="prose prose-sm dark:prose-invert max-w-none">
          <pre className="whitespace-pre-wrap text-sm text-neutral-800 dark:text-neutral-200
            font-sans leading-relaxed bg-transparent p-0 border-0">
            {draft.draft_text || '(草稿内容为空)'}
          </pre>
        </div>
      </div>

      {/* Revision input */}
      {draft.status === 'open' && (
        <div className="border-t border-neutral-200 dark:border-neutral-800 px-6 py-3">
          <div className="flex gap-2">
            <input
              value={revisionInstruction}
              onChange={e => setRevisionInstruction(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleRevise()
              }}
              placeholder="输入修改意见..."
              className="flex-1 px-3 py-1.5 text-sm rounded-md border border-neutral-200
                dark:border-neutral-700 bg-transparent
                focus:outline-none focus:ring-1 focus:ring-neutral-400"
            />
            <button
              onClick={handleRevise}
              disabled={!revisionInstruction.trim() || isRevising}
              className="px-3 py-1.5 text-xs rounded-md border border-neutral-300 dark:border-neutral-600
                text-neutral-700 dark:text-neutral-300 disabled:opacity-40 transition-opacity
                hover:bg-neutral-100 dark:hover:bg-neutral-800"
            >
              {isRevising ? '修改中...' : '修改'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
