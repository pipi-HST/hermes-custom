import { useEffect, useMemo, useState, useRef, useCallback } from 'react'
import type { ReadingDraft, ReadingSessionDetail, CaptureDraft } from '../../lib/api'
import {
  fetchReadingDrafts,
  fetchReadingSession,
  commitReadingDraft,
  reviseReadingDraft,
  fetchKnowledgeDrafts,
  fetchCaptureDrafts,
  fetchCaptureDraft,
  createEditableCaptureDraft,
  updateEditableCaptureDraft,
  reviseCaptureDraft,
  commitCaptureDraft,
  sendMessageToGateway,
} from '../../lib/api'
import { DraftReviewPanel } from '../../components/DraftReviewPanel'

type WorkbenchMode = 'chat' | 'editable-capture' | 'draft-review' | 'capture-review'
type EditableStatus = 'empty' | 'editing' | 'saving_draft' | 'ready_to_commit' | 'committing' | 'committed' | 'error'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export interface WorkbenchSideContext {
  title: string
  emptyText?: string
  sections: Array<{
    label: string
    value?: string
    mono?: boolean
    tags?: string[]
  }>
}

function WorkbenchChatBubble({ message }: { message: ChatMessage }) {
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
        {message.content || (message.role === 'assistant' ? '...' : '')}
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

function editableStatusLabel(status: EditableStatus): string {
  const labels: Record<EditableStatus, string> = {
    empty: '等待粘贴内容',
    editing: '正在编辑',
    saving_draft: '正在生成标题、摘要和标签',
    ready_to_commit: '草稿已保存，可确认入库',
    committing: '正在写入知识库并建立检索索引',
    committed: '已入库，可被知识库问答检索',
    error: '需要处理错误后重试',
  }
  return labels[status]
}

export default function KnowledgeWorkbench({
  embedded = false,
  initialMode = 'chat',
  defaultFolderPath = '',
  onCaptureCommitted,
  onSideContextChange,
  hideSidePanel = false,
}: {
  embedded?: boolean
  initialMode?: WorkbenchMode
  defaultFolderPath?: string
  onCaptureCommitted?: () => void | Promise<void>
  onSideContextChange?: (context: WorkbenchSideContext) => void
  hideSidePanel?: boolean
}) {
  const [mode, setMode] = useState<WorkbenchMode>(initialMode)
  const [drafts, setDrafts] = useState<ReadingDraft[]>([])
  const [selectedDraft, setSelectedDraft] = useState<ReadingDraft | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  // Gateway session ID for conversation continuity
  const [sessionId] = useState(() => crypto.randomUUID())

  // Capture-review state
  const [captureDrafts, setCaptureDrafts] = useState<CaptureDraft[]>([])
  const [selectedCapture, setSelectedCapture] = useState<CaptureDraft | null>(null)
  const [captureReviseInput, setCaptureReviseInput] = useState('')
  const [captureActionLoading, setCaptureActionLoading] = useState(false)
  const [captureLoading, setCaptureLoading] = useState(false)

  // Free editable capture state
  const [editableContent, setEditableContent] = useState('')
  const [editableSource, setEditableSource] = useState('')
  const [editableTitle, setEditableTitle] = useState('')
  const [editableDraft, setEditableDraft] = useState<CaptureDraft | null>(null)
  const [editableStatus, setEditableStatus] = useState<EditableStatus>('empty')
  const [editableError, setEditableError] = useState<string | null>(null)

  useEffect(() => {
    loadDrafts()
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  // Load capture drafts when entering capture-review mode
  useEffect(() => {
    if (mode === 'capture-review') {
      loadCaptureDrafts()
    }
  }, [mode])

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

  const loadCaptureDrafts = async () => {
    setCaptureLoading(true)
    try {
      const items = await fetchCaptureDrafts(50)
      setCaptureDrafts(items)
    } finally {
      setCaptureLoading(false)
    }
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

  const handleSendMessage = useCallback(async () => {
    if (!inputValue.trim() || isLoading) return

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toISOString(),
    }
    const updatedMessages = [...chatMessages, userMsg]
    setChatMessages(updatedMessages)
    setInputValue('')
    setIsLoading(true)

    // Create an empty assistant message that we'll stream into
    const assistantMsgId = crypto.randomUUID()
    const assistantMsg: ChatMessage = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    }
    setChatMessages(prev => [...prev, assistantMsg])

    // Build the messages array for the Gateway (role + content)
    const gatewayMessages = updatedMessages.map(m => ({
      role: m.role,
      content: m.content,
    }))

    await sendMessageToGateway(
      gatewayMessages,
      {
        onDelta: (delta: string) => {
          setChatMessages(prev =>
            prev.map(m =>
              m.id === assistantMsgId
                ? { ...m, content: m.content + delta }
                : m,
            ),
          )
        },
        onComplete: (_fullText: string) => {
          setIsLoading(false)
        },
        onError: (error: string) => {
          setChatMessages(prev =>
            prev.map(m =>
              m.id === assistantMsgId
                ? { ...m, content: `[Error] ${error}` }
                : m,
            ),
          )
          setIsLoading(false)
        },
      },
      sessionId,
    )
  }, [inputValue, isLoading, chatMessages, sessionId])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  // ─── Capture-review actions ──────────────────────────────────────────────

  const handleCaptureSelect = (draft: CaptureDraft) => {
    setSelectedCapture(draft)
    setCaptureReviseInput('')
  }

  const handleCaptureRevise = async () => {
    if (!selectedCapture || !captureReviseInput.trim()) return
    setCaptureActionLoading(true)
    try {
      const result = await reviseCaptureDraft(selectedCapture.id, captureReviseInput.trim())
      // Update selected capture with revised draft_text if returned
      if (result.draft_text) {
        setSelectedCapture(prev => prev ? { ...prev, draft_text: result.draft_text as string } : null)
      }
      setCaptureReviseInput('')
      // Refresh the list
      await loadCaptureDrafts()
    } catch (err) {
      console.error('Failed to revise capture draft:', err)
    } finally {
      setCaptureActionLoading(false)
    }
  }

  const handleCaptureCommit = async () => {
    if (!selectedCapture) return
    setCaptureActionLoading(true)
    try {
      await commitCaptureDraft(selectedCapture.id)
      setSelectedCapture(null)
      await loadCaptureDrafts()
    } catch (err) {
      console.error('Failed to commit capture draft:', err)
    } finally {
      setCaptureActionLoading(false)
    }
  }

  // ─── Editable capture actions ───────────────────────────────────────────

  const loadEditableDraftFromResult = async (result: Record<string, unknown>) => {
    const meta = (result.meta || {}) as Record<string, unknown>
    const draftId = String(meta.draft_id || '')
    if (!draftId) return null
    const draft = await fetchCaptureDraft(draftId)
    if (!draft) return null
    setEditableDraft(draft)
    setEditableTitle(draft.title || '')
    setEditableContent(draft.draft_text || draft.raw_text || '')
    return draft
  }

  const handleEditableSave = async () => {
    if (!editableContent.trim()) return
    setEditableStatus('saving_draft')
    setEditableError(null)
    try {
      const result = editableDraft
        ? await updateEditableCaptureDraft(editableDraft.id, {
            content: editableContent,
            title: editableTitle,
          })
        : await createEditableCaptureDraft({
            content: editableContent,
            source: editableSource,
            source_type: 'manual',
            title: editableTitle,
            folder_path: defaultFolderPath,
          })
      const draft = await loadEditableDraftFromResult(result)
      setEditableStatus(draft ? 'ready_to_commit' : 'error')
      if (!draft) setEditableError('草稿已保存，但没有返回可加载的草稿 ID。')
      await loadCaptureDrafts()
    } catch (err) {
      setEditableStatus('error')
      setEditableError(err instanceof Error ? err.message : '保存失败')
    }
  }

  const handleEditableCommit = async () => {
    if (!editableDraft) return
    setEditableStatus('committing')
    setEditableError(null)
    try {
      const result = await updateEditableCaptureDraft(editableDraft.id, {
        content: editableContent,
        title: editableTitle,
      })
      const draft = await loadEditableDraftFromResult(result)
      if (!draft) throw new Error('提交前无法刷新草稿')
      await commitCaptureDraft(draft.id)
      setEditableStatus('committed')
      setEditableDraft(null)
      await loadCaptureDrafts()
      await onCaptureCommitted?.()
    } catch (err) {
      setEditableStatus('error')
      setEditableError(err instanceof Error ? err.message : '提交失败')
    }
  }

  const handleEditableReset = () => {
    setEditableContent('')
    setEditableSource('')
    setEditableTitle('')
    setEditableDraft(null)
    setEditableStatus('empty')
    setEditableError(null)
  }

  const sideContext = useMemo<WorkbenchSideContext>(() => {
    if (mode === 'editable-capture') {
      const sections: WorkbenchSideContext['sections'] = [
        { label: '状态', value: editableStatusLabel(editableStatus) },
      ]
      if (defaultFolderPath) {
        sections.push({ label: '目标文件夹', value: defaultFolderPath })
      }
      if (editableDraft) {
        sections.push(
          { label: '草稿 ID', value: editableDraft.id, mono: true },
          { label: '标题', value: editableDraft.title || '生成中' },
        )
        if (editableDraft.summary) {
          sections.push({ label: '摘要', value: editableDraft.summary })
        }
        if (editableDraft.tags.length > 0) {
          sections.push({ label: '标签', tags: editableDraft.tags })
        }
        sections.push({
          label: '检索链路',
          value: '确认入库后会复用现有 Library Agent 和 RAG 索引。',
        })
      }
      return {
        title: '来源与工具',
        emptyText: editableDraft ? undefined : '保存后这里会显示 Hermes 自动生成的标题、摘要和标签。',
        sections,
      }
    }

    if (mode === 'capture-review' && selectedCapture) {
      const sections: WorkbenchSideContext['sections'] = [
        { label: '草稿 ID', value: selectedCapture.id, mono: true },
        { label: '来源', value: selectedCapture.source },
        { label: '来源类型', value: selectedCapture.source_type },
      ]
      if (selectedCapture.instruction) {
        sections.push({ label: '捕获指令', value: selectedCapture.instruction })
      }
      if (selectedCapture.committed_capture_id) {
        sections.push({ label: '已提交捕获', value: selectedCapture.committed_capture_id, mono: true })
      }
      sections.push({ label: '创建时间', value: new Date(selectedCapture.created_at).toLocaleString() })
      return { title: '来源与工具', sections }
    }

    if (selectedDraft) {
      const sections: WorkbenchSideContext['sections'] = [
        { label: '草稿类型', value: outputTypeLabel(selectedDraft.output_type) },
        { label: '会话 ID', value: selectedDraft.session_id, mono: true },
      ]
      if (selectedDraft.target_capture_id) {
        sections.push({ label: '目标捕获', value: selectedDraft.target_capture_id, mono: true })
      }
      return { title: '来源与工具', sections }
    }

    return {
      title: '来源与工具',
      emptyText: '选择草稿或对话以查看相关信息',
      sections: [],
    }
  }, [
    defaultFolderPath,
    editableDraft,
    editableStatus,
    mode,
    selectedCapture,
    selectedDraft,
  ])

  useEffect(() => {
    onSideContextChange?.(sideContext)
  }, [onSideContextChange, sideContext])

  return (
    <div className={`flex ${embedded ? 'h-full min-h-[640px]' : 'h-screen'}`}>
      {/* Left panel: Draft list or Capture list depending on mode */}
      <aside className="w-64 shrink-0 border-r border-neutral-200 dark:border-neutral-800
        flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
          <h3 className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
            {mode === 'capture-review' ? '捕获草稿' : '草稿列表'}
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {mode === 'capture-review' ? (
            /* Capture drafts list */
            captureLoading ? (
              <p className="text-xs text-neutral-400 px-2 py-4">加载中...</p>
            ) : captureDrafts.length === 0 ? (
              <p className="text-xs text-neutral-400 px-2 py-4">暂无捕获草稿</p>
            ) : (
              captureDrafts.map(draft => (
                <button
                  key={draft.id}
                  onClick={() => handleCaptureSelect(draft)}
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
            /* Reading / knowledge drafts list */
            drafts.length === 0 ? (
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
            )
          )}
        </div>
      </aside>

      {/* Center: Chat or Draft Review */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mode tabs */}
        <div className="flex border-b border-neutral-200 dark:border-neutral-800 px-4">
          {([
            ['chat', '对话'],
            ['editable-capture', '粘贴整理'],
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
                  <WorkbenchChatBubble key={msg.id} message={msg} />
                ))
              )}
              {isLoading && chatMessages[chatMessages.length - 1]?.content === '' && (
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

        {mode === 'editable-capture' && (
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="px-6 py-4 border-b border-neutral-200 dark:border-neutral-800">
              <h2 className="text-lg font-medium text-neutral-900 dark:text-neutral-100">
                粘贴整理
              </h2>
              <p className="text-sm text-neutral-500 mt-1">
                直接粘贴或写下内容，Hermes 会自动生成标题、标签和检索索引。
              </p>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
              {editableError && (
                <div className="px-3 py-2 rounded-md bg-red-50 dark:bg-red-950
                  text-red-700 dark:text-red-300 text-sm">
                  {editableError}
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-neutral-500 mb-1">
                  来源
                </label>
                <input
                  value={editableSource}
                  onChange={e => setEditableSource(e.target.value)}
                  placeholder="可选：链接、公众号、小红书、手动记录"
                  className="w-full px-3 py-2 text-sm rounded-md border border-neutral-200
                    dark:border-neutral-700 bg-transparent
                    focus:outline-none focus:ring-1 focus:ring-neutral-400"
                />
              </div>

              {editableDraft && (
                <div>
                  <label className="block text-xs font-medium text-neutral-500 mb-1">
                    生成标题
                  </label>
                  <input
                    value={editableTitle}
                    onChange={e => setEditableTitle(e.target.value)}
                    placeholder="Hermes 会自动生成标题；你也可以手动覆盖"
                    className="w-full px-3 py-2 text-sm rounded-md border border-neutral-200
                      dark:border-neutral-700 bg-transparent
                      focus:outline-none focus:ring-1 focus:ring-neutral-400"
                  />
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-neutral-500 mb-1">
                  正文
                </label>
                <textarea
                  value={editableContent}
                  onChange={e => {
                    setEditableContent(e.target.value)
                    if (editableStatus === 'empty') setEditableStatus('editing')
                  }}
                  placeholder="把文章片段、产品判断、公众号内容、小红书内容或自己的想法粘贴到这里..."
                  rows={18}
                  className="w-full px-3 py-3 text-sm rounded-md border border-neutral-200
                    dark:border-neutral-700 bg-transparent resize-y leading-relaxed
                    focus:outline-none focus:ring-1 focus:ring-neutral-400"
                />
              </div>
            </div>

            <div className="border-t border-neutral-200 dark:border-neutral-800 px-6 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs text-neutral-500">
                  {editableStatusLabel(editableStatus)}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={handleEditableReset}
                    className="px-3 py-2 text-sm rounded-md border border-neutral-300 dark:border-neutral-600
                      text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800"
                  >
                    新建
                  </button>
                  <button
                    onClick={handleEditableSave}
                    disabled={!editableContent.trim() || editableStatus === 'saving_draft' || editableStatus === 'committing'}
                    className="px-4 py-2 text-sm rounded-md border border-neutral-300 dark:border-neutral-600
                      text-neutral-700 dark:text-neutral-300 disabled:opacity-40 transition-opacity
                      hover:bg-neutral-100 dark:hover:bg-neutral-800"
                  >
                    {editableStatus === 'saving_draft' ? '整理中...' : '保存并整理'}
                  </button>
                  <button
                    onClick={handleEditableCommit}
                    disabled={!editableDraft || editableStatus === 'committing' || editableStatus === 'saving_draft'}
                    className="px-4 py-2 text-sm rounded-md bg-neutral-900 dark:bg-neutral-100
                      text-white dark:text-neutral-900 disabled:opacity-40 transition-opacity"
                  >
                    {editableStatus === 'committing' ? '入库中...' : '确认入库'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {mode === 'draft-review' && (
          <DraftReviewPanel
            draft={selectedDraft}
            onCommitted={handleDraftCommitted}
            onBack={() => setMode('chat')}
          />
        )}

        {mode === 'capture-review' && (
          <div className="flex-1 flex flex-col overflow-hidden">
            {selectedCapture ? (
              /* Capture draft detail + actions */
              <div className="flex-1 flex flex-col overflow-hidden">
                {/* Header */}
                <div className="px-6 py-4 border-b border-neutral-200 dark:border-neutral-800">
                  <h2 className="text-lg font-medium text-neutral-800 dark:text-neutral-200">
                    {selectedCapture.title || '(无标题)'}
                  </h2>
                  <div className="flex gap-3 mt-1 text-xs text-neutral-400">
                    <span>来源: {selectedCapture.source}</span>
                    <span>类型: {selectedCapture.source_type}</span>
                    <span>{selectedCapture.word_count} 字</span>
                    <span>状态: {selectedCapture.status}</span>
                  </div>
                  {selectedCapture.tags.length > 0 && (
                    <div className="flex gap-1 mt-2 flex-wrap">
                      {selectedCapture.tags.map(tag => (
                        <span
                          key={tag}
                          className="px-2 py-0.5 text-xs rounded-full bg-neutral-100 dark:bg-neutral-800
                            text-neutral-600 dark:text-neutral-400"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Draft text */}
                <div className="flex-1 overflow-y-auto px-6 py-4">
                  {selectedCapture.summary && (
                    <div className="mb-4">
                      <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-1">
                        摘要
                      </h3>
                      <p className="text-sm text-neutral-600 dark:text-neutral-400">
                        {selectedCapture.summary}
                      </p>
                    </div>
                  )}
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-1">
                      草稿内容
                    </h3>
                    <div className="text-sm text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap leading-relaxed">
                      {selectedCapture.draft_text}
                    </div>
                  </div>
                  {selectedCapture.raw_text && (
                    <details className="mt-6">
                      <summary className="text-xs text-neutral-400 cursor-pointer hover:text-neutral-600">
                        查看原文
                      </summary>
                      <div className="mt-2 text-xs text-neutral-500 whitespace-pre-wrap bg-neutral-50 dark:bg-neutral-900 p-3 rounded-md max-h-64 overflow-y-auto">
                        {selectedCapture.raw_text}
                      </div>
                    </details>
                  )}
                </div>

                {/* Actions bar */}
                <div className="border-t border-neutral-200 dark:border-neutral-800 px-6 py-3 space-y-2">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={captureReviseInput}
                      onChange={e => setCaptureReviseInput(e.target.value)}
                      placeholder="修改指令 (例如: 精简摘要、添加关键词...)"
                      className="flex-1 px-3 py-2 text-sm rounded-md border border-neutral-200
                        dark:border-neutral-700 bg-transparent
                        focus:outline-none focus:ring-1 focus:ring-neutral-400"
                      onKeyDown={e => {
                        if (e.key === 'Enter') handleCaptureRevise()
                      }}
                    />
                    <button
                      onClick={handleCaptureRevise}
                      disabled={!captureReviseInput.trim() || captureActionLoading}
                      className="px-4 py-2 text-sm rounded-md border border-neutral-300 dark:border-neutral-600
                        text-neutral-700 dark:text-neutral-300 disabled:opacity-40 transition-opacity
                        hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    >
                      修改
                    </button>
                    <button
                      onClick={handleCaptureCommit}
                      disabled={captureActionLoading}
                      className="px-4 py-2 text-sm rounded-md bg-neutral-900 dark:bg-neutral-100
                        text-white dark:text-neutral-900 disabled:opacity-40 transition-opacity"
                    >
                      提交
                    </button>
                  </div>
                  <button
                    onClick={() => setSelectedCapture(null)}
                    className="text-xs text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300"
                  >
                    &larr; 返回列表
                  </button>
                </div>
              </div>
            ) : (
              /* Empty state for capture-review */
              <div className="flex-1 flex items-center justify-center text-neutral-400 text-sm">
                <div className="text-center">
                  <p className="mb-2">捕获审阅模式</p>
                  <p className="text-xs">从左侧列表选择待审阅的捕获草稿</p>
                  {captureDrafts.length === 0 && !captureLoading && (
                    <button
                      onClick={loadCaptureDrafts}
                      className="mt-4 px-3 py-1 text-xs rounded-md border border-neutral-300
                        dark:border-neutral-600 hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    >
                      刷新列表
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Right panel: Sources & tools */}
      <aside className={`${hideSidePanel ? 'hidden' : ''} w-56 shrink-0 border-l border-neutral-200 dark:border-neutral-800
        overflow-y-auto px-4 py-3`}>
        <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-3">
          来源与工具
        </h3>
        {mode === 'editable-capture' ? (
          <div className="text-sm space-y-4">
            <div>
              <p className="text-xs text-neutral-500">状态</p>
              <p className="text-neutral-700 dark:text-neutral-300">
                {editableStatusLabel(editableStatus)}
              </p>
            </div>
            {editableDraft ? (
              <>
                <div>
                  <p className="text-xs text-neutral-500">草稿 ID</p>
                  <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono truncate">
                    {editableDraft.id}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-neutral-500">标题</p>
                  <p className="text-neutral-700 dark:text-neutral-300">
                    {editableDraft.title || '生成中'}
                  </p>
                </div>
                {editableDraft.summary && (
                  <div>
                    <p className="text-xs text-neutral-500">摘要</p>
                    <p className="text-xs text-neutral-600 dark:text-neutral-400">
                      {editableDraft.summary}
                    </p>
                  </div>
                )}
                {editableDraft.tags.length > 0 && (
                  <div>
                    <p className="text-xs text-neutral-500 mb-1">标签</p>
                    <div className="flex flex-wrap gap-1">
                      {editableDraft.tags.map(tag => (
                        <span
                          key={tag}
                          className="px-2 py-0.5 text-xs rounded-full bg-neutral-100 dark:bg-neutral-800
                            text-neutral-600 dark:text-neutral-400"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <p className="text-xs text-neutral-500">检索链路</p>
                  <p className="text-xs text-neutral-600 dark:text-neutral-400">
                    确认入库后会复用现有 Library Agent 和 RAG 索引。
                  </p>
                </div>
              </>
            ) : (
              <p className="text-xs text-neutral-400">
                保存后这里会显示 Hermes 自动生成的标题、摘要和标签。
              </p>
            )}
          </div>
        ) : mode === 'capture-review' && selectedCapture ? (
          <div className="text-sm space-y-3">
            <div>
              <p className="text-xs text-neutral-500">草稿 ID</p>
              <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono truncate">
                {selectedCapture.id}
              </p>
            </div>
            <div>
              <p className="text-xs text-neutral-500">来源</p>
              <p className="text-neutral-700 dark:text-neutral-300">
                {selectedCapture.source}
              </p>
            </div>
            <div>
              <p className="text-xs text-neutral-500">来源类型</p>
              <p className="text-neutral-700 dark:text-neutral-300">
                {selectedCapture.source_type}
              </p>
            </div>
            {selectedCapture.instruction && (
              <div>
                <p className="text-xs text-neutral-500">捕获指令</p>
                <p className="text-xs text-neutral-600 dark:text-neutral-400">
                  {selectedCapture.instruction}
                </p>
              </div>
            )}
            {selectedCapture.committed_capture_id && (
              <div>
                <p className="text-xs text-neutral-500">已提交捕获</p>
                <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono">
                  {selectedCapture.committed_capture_id}
                </p>
              </div>
            )}
            <div>
              <p className="text-xs text-neutral-500">创建时间</p>
              <p className="text-xs text-neutral-600 dark:text-neutral-400">
                {new Date(selectedCapture.created_at).toLocaleString()}
              </p>
            </div>
          </div>
        ) : selectedDraft ? (
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
