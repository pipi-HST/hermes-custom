import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/router'
import type { Capture, CaptureDraft, GardenCard } from '../lib/api'
import {
  commitCaptureDraft,
  createEditableCaptureDraft,
  fetchCaptureDrafts,
  fetchCaptures,
  fetchGardenCards,
  updateCapture,
} from '../lib/api'
import GardenBoard from '../features/garden/GardenBoard'
import KnowledgeWorkbench, { type WorkbenchMode } from '../features/workbench/KnowledgeWorkbench'
import type { WorkbenchSideContext } from '../features/workbench/KnowledgeWorkbench'
import { WorkbenchSideContextContent } from '../features/workbench/WorkbenchSidePanel'

type WorkspaceTab = 'library' | 'workbench' | 'garden'
type WorkspaceCapture = Capture

const baseFolderNames = ['Inbox', 'AI 产品', '数据 Agent', '待读', '临时']

const tabItems: Array<{
  key: WorkspaceTab
  label: string
  shortLabel: string
  description: string
}> = [
  { key: 'library', label: '资料库', shortLabel: '库', description: '文件夹和文件列表' },
  { key: 'workbench', label: '工作台', shortLabel: '台', description: '完整复用原工作台逻辑' },
  { key: 'garden', label: 'Garden Cards', shortLabel: '卡', description: '完整复用原卡片逻辑' },
]

export default function WorkspacePage() {
  const router = useRouter()
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('library')
  const [activeFolder, setActiveFolder] = useState('Inbox')
  const [expandedFolders, setExpandedFolders] = useState<string[]>(['Inbox'])
  const [selectedCaptureId, setSelectedCaptureId] = useState<string>('')
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [workbenchInitialMode, setWorkbenchInitialMode] = useState<WorkbenchMode>('chat')
  const [workbenchSideContext, setWorkbenchSideContext] = useState<WorkbenchSideContext | null>(null)
  const [creatingFolder, setCreatingFolder] = useState<string | null>(null)
  const [captures, setCaptures] = useState<WorkspaceCapture[]>([])
  const [gardenCards, setGardenCards] = useState<GardenCard[]>([])
  const [drafts, setDrafts] = useState<CaptureDraft[]>([])
  const [loading, setLoading] = useState(true)

  const loadWorkspace = useCallback(async () => {
    setLoading(true)
    const [captureItems, gardenItems, draftItems] = await Promise.all([
      fetchCaptures({ limit: 80 }),
      fetchGardenCards(80),
      fetchCaptureDrafts(40),
    ])

    const liveCaptures = (captureItems as WorkspaceCapture[]).filter(item => Boolean(item.file_path))
    const initialCapture = liveCaptures.find(item => (item.folder_path || 'Inbox') === activeFolder) || liveCaptures[0]
    setCaptures(liveCaptures)
    setGardenCards(gardenItems)
    setDrafts(draftItems)
    setSelectedCaptureId(prev =>
      liveCaptures.some(item => item.id === prev)
        ? prev
        : initialCapture?.id || '',
    )
    setLoading(false)
    return liveCaptures
  }, [activeFolder])

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])

  useEffect(() => {
    if (!router.isReady) return
    const nextTab = parseWorkspaceTab(router.query.tab)
    if (nextTab) setActiveTab(nextTab)
    const nextMode = parseWorkbenchMode(router.query.mode)
    if (nextMode) setWorkbenchInitialMode(nextMode)
    const nextFolder = parseQueryValue(router.query.folder)
    if (nextFolder) {
      setActiveFolder(nextFolder)
      setExpandedFolders(prev => prev.includes(nextFolder) ? prev : [...prev, nextFolder])
    }
  }, [router.isReady, router.query.folder, router.query.mode, router.query.tab])

  const folderNames = useMemo(() => {
    const dynamicFolders = captures
      .map(item => item.folder_path || 'Inbox')
      .filter(folder => !baseFolderNames.includes(folder))
    return [...baseFolderNames, ...Array.from(new Set(dynamicFolders))]
  }, [captures])

  const capturesByFolder = useMemo(() => {
    return folderNames.reduce<Record<string, WorkspaceCapture[]>>((acc, folder) => {
      acc[folder] = captures.filter(item => (item.folder_path || 'Inbox') === folder)
      return acc
    }, {})
  }, [captures, folderNames])

  const visibleCaptures = capturesByFolder[activeFolder] || []
  const selectedCapture = (
    captures.find(item =>
      item.id === selectedCaptureId &&
      (item.folder_path || 'Inbox') === activeFolder,
    ) || visibleCaptures[0]
  )
  const gridClass = leftCollapsed
    ? rightCollapsed
      ? 'lg:grid-cols-[64px_minmax(0,1fr)_56px]'
      : 'lg:grid-cols-[64px_minmax(0,1fr)_320px]'
    : rightCollapsed
      ? 'lg:grid-cols-[320px_minmax(0,1fr)_56px]'
      : 'lg:grid-cols-[320px_minmax(0,1fr)_320px]'

  const updateWorkspaceUrl = useCallback((
    tab: WorkspaceTab,
    folder = activeFolder,
    mode = workbenchInitialMode,
  ) => {
    if (!router.isReady) return
    const nextQuery = buildWorkspaceQuery(tab, mode, folder)
    const currentTab = parseWorkspaceTab(router.query.tab) || 'library'
    const currentMode = parseWorkbenchMode(router.query.mode) || 'chat'
    const currentFolder = parseQueryValue(router.query.folder)
    const nextMode = nextQuery.mode || 'chat'
    const nextFolder = nextQuery.folder || ''
    if (
      currentTab === tab &&
      currentMode === nextMode &&
      currentFolder === nextFolder
    ) {
      return
    }
    void router.replace(
      {
        pathname: '/workspace',
        query: nextQuery,
      },
      undefined,
      { shallow: true },
    )
  }, [activeFolder, router, router.query.folder, router.query.mode, router.query.tab, workbenchInitialMode])

  const selectTab = useCallback((tab: WorkspaceTab) => {
    setActiveTab(tab)
    updateWorkspaceUrl(tab)
  }, [updateWorkspaceUrl])

  const handleWorkbenchModeChange = useCallback((mode: WorkbenchMode) => {
    setWorkbenchInitialMode(mode)
    if (activeTab === 'workbench') {
      updateWorkspaceUrl('workbench', activeFolder, mode)
    }
  }, [activeFolder, activeTab, updateWorkspaceUrl])

  const toggleFolder = (folder: string) => {
    setActiveFolder(folder)
    setActiveTab('library')
    updateWorkspaceUrl('library', folder)
    const firstFile = (capturesByFolder[folder] || [])[0]
    setSelectedCaptureId(firstFile?.id || '')
    setExpandedFolders(prev =>
      prev.includes(folder)
        ? prev.filter(item => item !== folder)
        : [...prev, folder],
    )
  }

  const createFile = () => {
    setCreatingFolder(activeFolder)
    setSelectedCaptureId('')
    setActiveTab('library')
    updateWorkspaceUrl('library')
    setExpandedFolders(prev => prev.includes(activeFolder) ? prev : [...prev, activeFolder])
  }

  const saveCapture = async (captureId: string, params: { title: string; body: string; folder_path: string }) => {
    const result = await updateCapture(captureId, params)
    const item = result.item as WorkspaceCapture | undefined
    if (!item) return
    setCaptures(prev => prev.map(capture => capture.id === captureId ? item : capture))
    setSelectedCaptureId(item.id)
    setActiveFolder(item.folder_path || 'Inbox')
    updateWorkspaceUrl('library', item.folder_path || 'Inbox')
    setExpandedFolders(prev => prev.includes(item.folder_path || 'Inbox') ? prev : [...prev, item.folder_path || 'Inbox'])
  }

  const createCaptureFile = async (params: { title: string; body: string; folder_path: string }) => {
    const folderPath = params.folder_path || 'Inbox'
    const draftResult = await createEditableCaptureDraft({
      content: params.body,
      source: 'Hermes Workspace',
      source_type: 'manual',
      title: params.title,
      folder_path: folderPath,
    })
    const draftMeta = (draftResult.meta || {}) as Record<string, unknown>
    const draftId = String(draftMeta.draft_id || '')
    if (!draftId) throw new Error('新建文件失败：没有返回草稿 ID')

    const commitResult = await commitCaptureDraft(draftId)
    if (commitResult.status === 'error') {
      throw new Error(String(commitResult.chat_preview || '确认入库失败'))
    }

    const commitMeta = (commitResult.meta || {}) as Record<string, unknown>
    const captureId = String(commitMeta.capture_id || '')
    const liveCaptures = await loadWorkspace()
    const created = liveCaptures.find(item => item.id === captureId)
    setCreatingFolder(null)
    setActiveFolder(created?.folder_path || folderPath)
    setSelectedCaptureId(created?.id || captureId)
    updateWorkspaceUrl('library', created?.folder_path || folderPath)
    setExpandedFolders(prev => prev.includes(folderPath) ? prev : [...prev, folderPath])
  }

  return (
    <div className="min-h-[100dvh] bg-[#f6f7f4] text-neutral-950 dark:bg-[#101412] dark:text-neutral-100">
      <div className={`grid min-h-[100dvh] grid-cols-1 ${gridClass}`}>
        <LeftSidebar
          activeTab={activeTab}
          activeFolder={activeFolder}
          capturesByFolder={capturesByFolder}
          collapsed={leftCollapsed}
          expandedFolders={expandedFolders}
          folderNames={folderNames}
          selectedCaptureId={creatingFolder ? '' : selectedCapture?.id || ''}
          onCreateFile={createFile}
          onSelectCapture={(capture) => {
            setCreatingFolder(null)
            setSelectedCaptureId(capture.id)
            setActiveFolder(capture.folder_path || 'Inbox')
            setActiveTab('library')
            updateWorkspaceUrl('library', capture.folder_path || 'Inbox')
          }}
          onSelectTab={selectTab}
          onToggleCollapsed={() => setLeftCollapsed(value => !value)}
          onToggleFolder={toggleFolder}
        />

        <main className="min-w-0 overflow-hidden">
          <WorkspaceHeader activeTab={activeTab} activeFolder={activeFolder} loading={loading} />
          <div className="h-[calc(100dvh-73px)] overflow-auto px-4 pb-4 md:px-5">
            {loading ? (
              <LoadingState />
            ) : activeTab === 'library' ? (
              <LibraryView
                activeFolder={activeFolder}
                captures={visibleCaptures}
                creatingFolder={creatingFolder}
                selectedCapture={creatingFolder ? undefined : selectedCapture}
                onCreateFile={createFile}
                onCreateCaptureFile={createCaptureFile}
                onSaveCapture={saveCapture}
                onSelectCapture={(capture) => {
                  setCreatingFolder(null)
                  setSelectedCaptureId(capture.id)
                }}
              />
            ) : activeTab === 'workbench' ? (
              <section className="h-full overflow-hidden rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
                <KnowledgeWorkbench
                  embedded
                  initialMode={workbenchInitialMode}
                  defaultFolderPath={activeFolder}
                  onCaptureCommitted={async () => {
                    await loadWorkspace()
                  }}
                  onModeChange={handleWorkbenchModeChange}
                  onSideContextChange={setWorkbenchSideContext}
                  hideSidePanel
                />
              </section>
            ) : (
              <section className="min-h-full rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
                <GardenBoard embedded />
              </section>
            )}
          </div>
        </main>

        <AISidebar
          activeTab={activeTab}
          collapsed={rightCollapsed}
          drafts={drafts}
          gardenCards={gardenCards}
          selectedCapture={selectedCapture}
          workbenchContext={workbenchSideContext}
          onToggleCollapsed={() => setRightCollapsed(value => !value)}
        />
      </div>
    </div>
  )
}

function LeftSidebar({
  activeTab,
  activeFolder,
  capturesByFolder,
  collapsed,
  expandedFolders,
  folderNames,
  selectedCaptureId,
  onCreateFile,
  onSelectCapture,
  onSelectTab,
  onToggleCollapsed,
  onToggleFolder,
}: {
  activeTab: WorkspaceTab
  activeFolder: string
  capturesByFolder: Record<string, WorkspaceCapture[]>
  collapsed: boolean
  expandedFolders: string[]
  folderNames: string[]
  selectedCaptureId: string
  onCreateFile: () => void
  onSelectCapture: (capture: WorkspaceCapture) => void
  onSelectTab: (tab: WorkspaceTab) => void
  onToggleCollapsed: () => void
  onToggleFolder: (folder: string) => void
}) {
  return (
    <aside className="border-b border-neutral-200 bg-[#eef1ec] dark:border-neutral-800 dark:bg-[#151a17] lg:border-b-0 lg:border-r">
      <div className={`flex h-full flex-col ${collapsed ? 'items-center px-2 py-4' : 'px-4 py-5'}`}>
        <div className={`mb-5 flex w-full items-start ${collapsed ? 'justify-center' : 'justify-between gap-3'}`}>
          {!collapsed && (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#3f765d] dark:text-[#8ebaa2]">
                Hermes
              </p>
              <h1 className="mt-2 text-xl font-semibold leading-tight">Knowledge Workspace</h1>
            </div>
          )}
          <button
            onClick={onToggleCollapsed}
            className="rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm text-neutral-700 transition hover:bg-neutral-50 active:translate-y-px dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
            title={collapsed ? '展开左侧栏' : '收起左侧栏'}
          >
            {collapsed ? '>' : '<'}
          </button>
        </div>

        <nav className={`w-full ${collapsed ? 'space-y-2' : 'space-y-2'}`} aria-label="Workspace views">
          {tabItems.map(item => (
            <button
              key={item.key}
              onClick={() => onSelectTab(item.key)}
              className={`w-full rounded-lg text-left transition active:translate-y-px ${
                collapsed ? 'px-0 py-3 text-center' : 'px-3 py-3'
              } ${
                activeTab === item.key
                  ? 'bg-neutral-950 text-white shadow-[0_16px_40px_rgba(18,39,28,0.18)] dark:bg-neutral-50 dark:text-neutral-950'
                  : 'text-neutral-700 hover:bg-white/70 dark:text-neutral-300 dark:hover:bg-white/5'
              }`}
              title={item.label}
            >
              <span className="block text-sm font-semibold">{collapsed ? item.shortLabel : item.label}</span>
              {!collapsed && (
                <span className={`mt-1 block text-xs leading-5 ${
                  activeTab === item.key ? 'text-white/72 dark:text-neutral-700' : 'text-neutral-500 dark:text-neutral-500'
                }`}>
                  {item.description}
                </span>
              )}
            </button>
          ))}
        </nav>

        {!collapsed && (
          <div className="mt-6 flex min-h-0 w-full flex-1 flex-col">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400">
                文件夹
              </h2>
              <button
                onClick={onCreateFile}
                className="rounded-md bg-[#2f6f55] px-2 py-1 text-xs font-medium text-white transition hover:bg-[#285f49] active:translate-y-px"
              >
                新建文件
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
              {folderNames.map(folder => {
                const files = capturesByFolder[folder] || []
                const expanded = expandedFolders.includes(folder)
                return (
                  <div key={folder}>
                    <button
                      onClick={() => onToggleFolder(folder)}
                      className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-sm transition active:translate-y-px ${
                        activeFolder === folder && activeTab === 'library'
                          ? 'bg-white text-neutral-950 shadow-sm dark:bg-neutral-900 dark:text-neutral-100'
                          : 'text-neutral-600 hover:bg-white/60 dark:text-neutral-400 dark:hover:bg-white/5'
                      }`}
                    >
                      <span>{expanded ? 'v' : '>'} {folder}</span>
                      <span className="font-mono text-xs text-neutral-400">{files.length}</span>
                    </button>
                    {expanded && (
                      <div className="ml-3 mt-1 space-y-1 border-l border-neutral-200 pl-2 dark:border-neutral-800">
                        {files.length === 0 ? (
                          <p className="px-2 py-1 text-xs text-neutral-400">暂无文件</p>
                        ) : files.map(file => (
                          <button
                            key={file.id}
                            onClick={() => onSelectCapture(file)}
                            className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs transition ${
                              selectedCaptureId === file.id
                                ? 'bg-[#dfeae3] text-[#244b39] dark:bg-[#203229] dark:text-[#9cc7ae]'
                                : 'text-neutral-500 hover:bg-white/60 dark:text-neutral-400 dark:hover:bg-white/5'
                            }`}
                          >
                            {file.title || '(无标题)'}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

function WorkspaceHeader({
  activeTab,
  activeFolder,
  loading,
}: {
  activeTab: WorkspaceTab
  activeFolder: string
  loading: boolean
}) {
  return (
    <header className="flex min-h-[73px] items-center justify-between border-b border-neutral-200 bg-[#f6f7f4]/95 px-4 py-3 dark:border-neutral-800 dark:bg-[#101412]/95 md:px-5">
      <div>
        <p className="text-xs text-neutral-500 dark:text-neutral-400">新工作台主入口</p>
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">
          {activeTitle(activeTab, activeFolder)}
        </h2>
      </div>
      {loading && <span className="text-sm text-neutral-400">加载中...</span>}
    </header>
  )
}

function LibraryView({
  activeFolder,
  captures,
  creatingFolder,
  selectedCapture,
  onCreateFile,
  onCreateCaptureFile,
  onSaveCapture,
  onSelectCapture,
}: {
  activeFolder: string
  captures: WorkspaceCapture[]
  creatingFolder?: string | null
  selectedCapture?: WorkspaceCapture
  onCreateFile: () => void
  onCreateCaptureFile: (params: { title: string; body: string; folder_path: string }) => Promise<void>
  onSaveCapture: (captureId: string, params: { title: string; body: string; folder_path: string }) => Promise<void>
  onSelectCapture: (capture: WorkspaceCapture) => void
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [folderPath, setFolderPath] = useState(activeFolder)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveNotice, setSaveNotice] = useState<string | null>(null)
  const isCreating = Boolean(creatingFolder)

  useEffect(() => {
    if (creatingFolder) {
      setTitle('')
      setBody('')
      setFolderPath(creatingFolder)
      setSaveError(null)
      setSaveNotice(null)
      return
    }
    setTitle(selectedCapture?.title || '')
    setBody(selectedCapture?.body || selectedCapture?.summary || '')
    setFolderPath(selectedCapture?.folder_path || activeFolder)
    setSaveError(null)
    setSaveNotice(null)
  }, [creatingFolder, selectedCapture?.id, activeFolder])

  const folderOptions = useMemo(() => {
    const options = new Set([...baseFolderNames, folderPath || activeFolder])
    return Array.from(options)
  }, [activeFolder, folderPath])

  const hasSelection = Boolean(selectedCapture) && !isCreating
  const hasChanges = Boolean(
    isCreating ||
    (selectedCapture &&
      (
        title !== (selectedCapture.title || '') ||
        body !== (selectedCapture.body || selectedCapture.summary || '') ||
        folderPath !== (selectedCapture.folder_path || activeFolder)
      )),
  )

  const handleSave = async () => {
    if (!body.trim()) return
    setSaving(true)
    setSaveError(null)
    setSaveNotice(null)
    try {
      if (isCreating) {
        await onCreateCaptureFile({
          title,
          body,
          folder_path: folderPath || activeFolder || 'Inbox',
        })
        setSaveNotice('已保存并入库，检索链路会复用现有 LibraryAgent / RAG。')
      } else if (selectedCapture) {
        await onSaveCapture(selectedCapture.id, {
          title,
          body,
          folder_path: folderPath || 'Inbox',
        })
        setSaveNotice('已保存，Markdown 和检索链路已更新。')
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (captures.length === 0 && !isCreating) {
    return (
      <section className="flex min-h-full items-center justify-center rounded-lg border border-dashed border-neutral-300 bg-white/70 p-8 text-center dark:border-neutral-700 dark:bg-neutral-900/60">
        <div>
          <h3 className="text-lg font-semibold">{activeFolder} 还没有文件</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-sm leading-6 text-neutral-500 dark:text-neutral-400">
            从这里新建文件后，内容会按 frontmatter 的 folder_path 出现在当前文件夹。
          </p>
          <button
            onClick={onCreateFile}
            className="mt-4 rounded-lg bg-[#2f6f55] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#285f49] active:translate-y-px"
          >
            新建文件
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="grid min-h-full gap-4 xl:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
      <div className="space-y-2 overflow-y-auto rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold">{activeFolder}</h3>
          <span className="font-mono text-xs text-neutral-400">{captures.length}</span>
        </div>
        {captures.map(item => (
          <button
            key={item.id}
            onClick={() => onSelectCapture(item)}
            className={`rounded-lg border p-3 ${
              !isCreating && selectedCapture?.id === item.id
                ? 'border-[#8db79d] bg-[#eef6f1] dark:border-[#446b55] dark:bg-[#16241c]'
                : 'border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900'
            } text-left transition hover:border-[#8db79d] active:translate-y-px`}
          >
            <h4 className="text-sm font-semibold">{item.title || '(无标题)'}</h4>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-neutral-500 dark:text-neutral-400">
              {item.summary || '暂无摘要'}
            </p>
          </button>
        ))}
      </div>

      <article className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-950">
        {!hasSelection && !isCreating ? (
          <div className="flex min-h-[360px] items-center justify-center text-sm text-neutral-400">
            选择一个文件查看和编辑
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-xl font-semibold">{isCreating ? '新建文件' : '文件详情'}</h3>
                <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
                  {isCreating
                    ? `${folderPath || activeFolder} / 手动 / 待入库`
                    : `${folderPath || activeFolder} / ${sourceLabel(selectedCapture?.source_type || '')} / ${statusLabel(selectedCapture?.status || '')}`}
                </p>
              </div>
              <span className="rounded-md bg-[#ecf4ef] px-2 py-1 text-xs text-[#315f49] dark:bg-[#203229] dark:text-[#9cc7ae]">
                {isCreating ? body.trim().length : selectedCapture?.word_count || 0} 字
              </span>
            </div>

            {saveError && (
              <div className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
                {saveError}
              </div>
            )}
            {saveNotice && (
              <div className="mt-4 rounded-md bg-[#ecf4ef] px-3 py-2 text-sm text-[#315f49] dark:bg-[#203229] dark:text-[#9cc7ae]">
                {saveNotice}
              </div>
            )}

            <div className="mt-5 grid gap-4 md:grid-cols-[minmax(0,1fr)_180px]">
              <label className="block">
                <span className="text-xs font-medium text-neutral-500">标题</span>
                <input
                  value={title}
                  onChange={event => {
                    setTitle(event.target.value)
                    setSaveNotice(null)
                  }}
                  className="mt-1 w-full rounded-md border border-neutral-200 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#8db79d] dark:border-neutral-700"
                  placeholder={isCreating ? '可选：不填则由 Hermes 生成标题' : '未命名文件'}
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-neutral-500">文件夹</span>
                <select
                  value={folderPath || 'Inbox'}
                  onChange={event => {
                    setFolderPath(event.target.value)
                    setSaveNotice(null)
                  }}
                  className="mt-1 w-full rounded-md border border-neutral-200 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#8db79d] dark:border-neutral-700"
                >
                  {folderOptions.map(folder => (
                    <option key={folder} value={folder}>{folder}</option>
                  ))}
                </select>
              </label>
            </div>

            <label className="mt-4 block">
              <span className="text-xs font-medium text-neutral-500">正文</span>
              <textarea
                value={body}
                onChange={event => {
                  setBody(event.target.value)
                  setSaveNotice(null)
                }}
                rows={16}
                className="mt-1 w-full rounded-md border border-neutral-200 bg-transparent px-3 py-3 text-sm leading-7 focus:outline-none focus:ring-1 focus:ring-[#8db79d] dark:border-neutral-700"
                placeholder="暂无正文"
              />
            </label>

            <div className="mt-4 flex items-center justify-between gap-3">
              <p className="text-xs text-neutral-500">
                {isCreating
                  ? '保存会先生成 Capture 草稿，再确认入库并复用 LibraryAgent / RAG 链路。'
                  : '保存会写回 Markdown frontmatter/body，并复用 LibraryAgent 更新检索链路。'}
              </p>
              <button
                onClick={handleSave}
                disabled={!body.trim() || !hasChanges || saving}
                className="rounded-lg bg-[#2f6f55] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#285f49] active:translate-y-px disabled:opacity-40"
              >
                {saving ? '保存中...' : isCreating ? '保存并入库' : '保存'}
              </button>
            </div>
          </>
        )}
        <div className="mt-5 flex flex-wrap gap-1.5">
          {(selectedCapture?.tags || []).map(tag => (
            <span
              key={tag}
              className="rounded-md bg-neutral-100 px-2 py-1 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
            >
              {tag}
            </span>
          ))}
        </div>
      </article>
    </section>
  )
}

function AISidebar({
  activeTab,
  collapsed,
  drafts,
  gardenCards,
  selectedCapture,
  workbenchContext,
  onToggleCollapsed,
}: {
  activeTab: WorkspaceTab
  collapsed: boolean
  drafts: CaptureDraft[]
  gardenCards: GardenCard[]
  selectedCapture?: WorkspaceCapture
  workbenchContext?: WorkbenchSideContext | null
  onToggleCollapsed: () => void
}) {
  const gardenKindCounts = useMemo(() => countBy(gardenCards, card => card.card_kind || 'unknown'), [gardenCards])
  const gardenStatusCounts = useMemo(() => countBy(gardenCards, card => card.status || 'unknown'), [gardenCards])
  const title = activeTab === 'workbench'
    ? '来源与工具'
    : activeTab === 'garden'
      ? 'Garden 上下文'
      : 'AI 建议'
  const subtitle = activeTab === 'workbench'
    ? '工作台上下文'
    : activeTab === 'garden'
      ? '卡片分布与状态'
      : '只做辅助，不阻塞保存'

  if (collapsed) {
    return (
      <aside className="flex items-start justify-center border-t border-neutral-200 bg-white/70 px-2 py-4 dark:border-neutral-800 dark:bg-neutral-950/35 lg:border-l lg:border-t-0">
        <button
          onClick={onToggleCollapsed}
          className="rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm text-neutral-700 transition hover:bg-neutral-50 active:translate-y-px dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
          title="展开 AI 建议"
        >
          {'<'}
        </button>
      </aside>
    )
  }

  return (
    <aside className="border-t border-neutral-200 bg-white/70 px-5 py-5 dark:border-neutral-800 dark:bg-neutral-950/35 lg:border-l lg:border-t-0">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
            {subtitle}
          </p>
        </div>
        <button
          onClick={onToggleCollapsed}
          className="rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm text-neutral-700 transition hover:bg-neutral-50 active:translate-y-px dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
          title="收起 AI 建议"
        >
          {'>'}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Metric label="Garden" value={gardenCards.length} />
        <Metric label="建议" value={drafts.length} />
      </div>

      {activeTab === 'workbench' && (
        <WorkbenchSideContextContent context={workbenchContext} variant="cards" />
      )}

      {activeTab === 'garden' && (
        <GardenContextPanel kindCounts={gardenKindCounts} statusCounts={gardenStatusCounts} />
      )}

      {activeTab === 'library' && selectedCapture && (
        <section className="mt-4 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
          <h3 className="text-sm font-semibold">当前文件</h3>
          <p className="mt-2 text-sm font-medium">{selectedCapture.title || '(无标题)'}</p>
          <p className="mt-2 line-clamp-4 text-sm leading-6 text-neutral-500 dark:text-neutral-400">
            {selectedCapture.summary || '暂无摘要'}
          </p>
        </section>
      )}

      {activeTab === 'library' && (
      <section className="mt-4 space-y-3">
        {drafts.length === 0 ? (
          <p className="rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-400 dark:border-neutral-700">
            暂无 AI 建议
          </p>
        ) : drafts.map(draft => (
          <article
            key={draft.id}
            className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
          >
            <h3 className="text-sm font-semibold">{draft.title || '(无标题建议)'}</h3>
            <p className="mt-2 line-clamp-4 text-xs leading-5 text-neutral-500 dark:text-neutral-400">
              {draft.summary || draft.draft_text || '等待 AI 生成整理建议。'}
            </p>
            <p className="mt-3 text-xs text-neutral-400">
              可在工作台审阅后提交
            </p>
          </article>
        ))}
      </section>
      )}
    </aside>
  )
}

function GardenContextPanel({
  kindCounts,
  statusCounts,
}: {
  kindCounts: Record<string, number>
  statusCounts: Record<string, number>
}) {
  return (
    <section className="mt-4 space-y-3">
      <CountGroup title="类型分布" counts={kindCounts} />
      <CountGroup title="状态分布" counts={statusCounts} />
    </section>
  )
}

function CountGroup({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts)
  if (entries.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-400 dark:border-neutral-700">
        暂无数据
      </p>
    )
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="mt-3 space-y-2">
        {entries.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate text-neutral-600 dark:text-neutral-400">{label}</span>
            <span className="font-mono text-xs text-neutral-500">{value}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function LoadingState() {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {[0, 1, 2, 3].map(item => (
        <div key={item} className="h-44 animate-pulse rounded-lg bg-white/80 dark:bg-neutral-900" />
      ))}
    </div>
  )
}

function countBy<T>(items: T[], getKey: (item: T) => string): Record<string, number> {
  return items.reduce<Record<string, number>>((acc, item) => {
    const key = getKey(item)
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
      <p className="text-xs text-neutral-500 dark:text-neutral-400">{label}</p>
      <p className="mt-1 font-mono text-2xl text-neutral-950 dark:text-neutral-50">{value}</p>
    </div>
  )
}

function activeTitle(tab: WorkspaceTab, folder: string) {
  const titles: Record<WorkspaceTab, string> = {
    library: `${folder} 里的文件`,
    workbench: '满血工作台',
    garden: 'Garden Card 展示',
  }
  return titles[tab]
}

function parseWorkspaceTab(value: string | string[] | undefined): WorkspaceTab | null {
  const tab = Array.isArray(value) ? value[0] : value
  return tab === 'library' || tab === 'workbench' || tab === 'garden' ? tab : null
}

function parseQueryValue(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] || '' : value || ''
}

function parseWorkbenchMode(value: string | string[] | undefined): WorkbenchMode | null {
  const mode = Array.isArray(value) ? value[0] : value
  return (
    mode === 'chat' ||
    mode === 'editable-capture' ||
    mode === 'draft-review' ||
    mode === 'capture-review'
  ) ? mode : null
}

function buildWorkspaceQuery(
  tab: WorkspaceTab,
  mode: WorkbenchMode,
  folder: string,
): Record<string, string> {
  const query: Record<string, string> = { tab }
  if (tab === 'workbench' && mode !== 'chat') {
    query.mode = mode
  }
  if (tab === 'library' && folder) {
    query.folder = folder
  }
  return query
}

function sourceLabel(sourceType: string) {
  const labels: Record<string, string> = {
    manual: '手动',
    link: '链接',
    wechat: '微信',
    xiaohongshu: '小红书',
  }
  return labels[sourceType] || sourceType || '未知来源'
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    draft: '草稿',
    reviewing: '待审阅',
    indexed: '已索引',
    committed: '已入库',
  }
  return labels[status] || status || '未知状态'
}
