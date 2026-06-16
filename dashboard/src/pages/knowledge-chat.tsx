import { useEffect, useState, useRef } from 'react'
import type { ReadingDraft, ReadingSessionDetail } from '../lib/api'
import {
  fetchReadingDrafts,
  fetchReadingSession,
  commitReadingDraft,
  reviseReadingDraft,
  fetchKnowledgeDrafts,
} from '../lib/api'
import { DraftReviewPanel } from '../components/DraftReviewPanel'

type WorkbenchMode = 'chat' | 'draft-review' | 'capture-review'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export default function KnowledgeChatPage() {
  const [mode, setMode] = useState<WorkbenchMode>('chat')
  const [drafts, setDrafts] = useState<ReadingDraft[]>([])
  const [selectedDraft, setSelectedDraft] = useState<ReadingDraft | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadDrafts()
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  const loadDrafts = async () => {
    const [readingDrafts, knowledgeDrafts] = await Promise.all([
      fetchReadingDrafts(20),
      fetchKnowledgeDrafts(20),
    ])
    // Deduplicate by id, merge both sources
    const seen = new Set<string>()
    const merged = [...readingDrafts, ...knowledgeDrafts].filter(d => {
      if (seen.has(d.id)) return false
      seen.add(d.id)
      return true
    })
    setDrafts(merged)
  }

  const handleDraftSelect = (draft: ReadingDraft) => {
    setSelectedDraft(draft)
    setMode('draft-review')
  }

  const handleDraftCommitted = () => {
    setSelectedDraft(null)
    setMode('chat')
    loadDrafts()
  }

  const handleSendMessage = () => {
    if (!inputValue.trim()) return

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toISOString(),
    }
    setChatMessages(prev => [...prev, userMsg])
    setInputValue('')

    // Simulate assistant response (in real usage this connects to Hermes gateway)
    setIsLoading(true)
    setTimeout(() => {
      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '这是知识工作台的聊天界面。后续将连接到 Hermes Gateway，支持阅读讨论、RAG 问答和知识沉淀。\n\n当前可用功能：\n- 左侧草稿列表：查看和审阅待提交的知识草稿\n- 草稿审阅：确认提交或修改草稿内容\n- 保存回答：将高质量回答存为知识草稿',
        timestamp: new Date().toISOString(),
      }
      setChatMessages(prev => [...prev, assistantMsg])
      setIsLoading(false)
    }, 500)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  return (
    <div className="flex h-screen">
      {/* Left panel: Draft list */}
      <aside className="w-64 shrink-0 border-r border-neutral-200 dark:border-neutral-800
        flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
          <h3 className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
            草稿列表
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {drafts.length === 0 ? (
            <p className="text-xs text-neutral-400 px-2 py-4">暂无待审阅草稿</p>
          ) : (
            drafts.map(draft => (
              <button
                key={draft.id}
                onClick={() => handleDraftSelect(draft)}
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
          )}
        </div>
      </aside>

      {/* Center: Chat or Draft Review */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mode tabs */}
        <div className="flex border-b border-neutral-200 dark:border-neutral-800 px-4">
          {([
            ['chat', '对话'],
            ['draft-review', '草稿审阅'],
            ['capture-review', '捕获审阅'],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setMode(key)}
              className={`px-4 py-2 text-sm border-b-2 transition-colors
                ${mode === key
                  ? 'border-neutral-900 dark:border-neutral-100 text-neutral-900 dark:text-neutral-100'
                  : 'border-transparent text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'
                }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === 'chat' && (
          <>
            {/* Chat messages */}
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {chatMessages.length === 0 ? (
                <div className="text-center text-neutral-400 mt-20">
                  <p className="text-lg mb-2">知识工作台</p>
                  <p className="text-sm">在这里进行阅读讨论、知识问答和内容沉淀</p>
                </div>
              ) : (
                chatMessages.map(msg => (
                  <ChatBubble key={msg.id} message={msg} />
                ))
              )}
              {isLoading && (
                <div className="flex gap-2 items-start mb-4">
                  <div className="w-6 h-6 rounded-full bg-neutral-200 dark:bg-neutral-700 shrink-0" />
                  <div className="bg-neutral-100 dark:bg-neutral-800 rounded-lg px-4 py-2 text-sm">
                    <span className="animate-pulse">思考中...</span>
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input */}
            <div className="border-t border-neutral-200 dark:border-neutral-800 px-6 py-3">
              <div className="flex gap-2">
                <textarea
                  value={inputValue}
                  onChange={e => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
                  rows={2}
                  className="flex-1 px-3 py-2 text-sm rounded-md border border-neutral-200
                    dark:border-neutral-700 bg-transparent resize-none
                    focus:outline-none focus:ring-1 focus:ring-neutral-400"
                />
                <button
                  onClick={handleSendMessage}
                  disabled={!inputValue.trim() || isLoading}
                  className="px-4 py-2 text-sm rounded-md bg-neutral-900 dark:bg-neutral-100
                    text-white dark:text-neutral-900 disabled:opacity-40 transition-opacity"
                >
                  发送
                </button>
              </div>
            </div>
          </>
        )}

        {mode === 'draft-review' && (
          <DraftReviewPanel
            draft={selectedDraft}
            onCommitted={handleDraftCommitted}
            onBack={() => setMode('chat')}
          />
        )}

        {mode === 'capture-review' && (
          <div className="flex-1 flex items-center justify-center text-neutral-400 text-sm">
            <div className="text-center">
              <p className="mb-2">捕获审阅模式</p>
              <p className="text-xs">从左侧草稿列表选择待审阅的捕获草稿</p>
            </div>
          </div>
        )}
      </div>

      {/* Right panel: Sources & tools (placeholder) */}
      <aside className="w-56 shrink-0 border-l border-neutral-200 dark:border-neutral-800
        overflow-y-auto px-4 py-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-3">
          来源与工具
        </h3>
        {selectedDraft ? (
          <div className="text-sm space-y-3">
            <div>
              <p className="text-xs text-neutral-500">草稿类型</p>
              <p className="text-neutral-700 dark:text-neutral-300">
                {outputTypeLabel(selectedDraft.output_type)}
              </p>
            </div>
            <div>
              <p className="text-xs text-neutral-500">会话 ID</p>
              <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono">
                {selectedDraft.session_id}
              </p>
            </div>
            {selectedDraft.target_capture_id && (
              <div>
                <p className="text-xs text-neutral-500">目标捕获</p>
                <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono">
                  {selectedDraft.target_capture_id}
                </p>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-neutral-400">
            选择草稿或对话以查看相关信息
          </p>
        )}
      </aside>
    </div>
  )
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={`flex gap-2 items-start mb-4 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className={`w-6 h-6 rounded-full shrink-0 flex items-center justify-center text-xs
          ${isUser
            ? 'bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300'
            : 'bg-neutral-200 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300'
          }`}
      >
        {isUser ? 'U' : 'H'}
      </div>
      <div
        className={`max-w-[70%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap
          ${isUser
            ? 'bg-blue-50 dark:bg-blue-950 text-neutral-900 dark:text-neutral-100'
            : 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
          }`}
      >
        {message.content}
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
