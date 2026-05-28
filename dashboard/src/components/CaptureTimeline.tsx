import { useEffect, useState } from 'react'
import type { Capture } from '../lib/api'
import { fetchCaptures } from '../lib/api'
import { CaptureCard } from './CaptureCard'
import { TagBadge } from './TagBadge'

const ALL_FOLDERS = [
  'AI 技术理解', '产品方法论', '产品案例拆解',
  '行业与商业', '职业成长', '个人思考',
  '读书笔记', '健康与生活', '其他',
]

export function CaptureTimeline() {
  const [captures, setCaptures] = useState<Capture[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTag, setActiveTag] = useState<string>('')
  const [activeFolder, setActiveFolder] = useState<string>('')
  const [search, setSearch] = useState('')

  useEffect(() => {
    setLoading(true)
    fetchCaptures({
      tag: activeTag || undefined,
      folder: activeFolder || undefined,
      search: search || undefined,
    }).then(items => {
      setCaptures(items)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [activeTag, activeFolder, search])

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <h1 className="text-lg font-medium mb-6 text-neutral-900 dark:text-neutral-100">
        知识花园
      </h1>

      {/* Search + Filters */}
      <div className="flex flex-wrap gap-2 mb-6">
        <input
          type="search"
          placeholder="搜索..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-md border border-neutral-200
            dark:border-neutral-700 bg-transparent w-full max-w-xs"
        />
        <select
          value={activeFolder}
          onChange={e => setActiveFolder(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-md border border-neutral-200
            dark:border-neutral-700 bg-transparent"
        >
          <option value="">全部分类</option>
          {ALL_FOLDERS.map(f => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      </div>

      {/* Active tag filter */}
      {activeTag && (
        <div className="mb-4 flex items-center gap-2 text-sm text-neutral-500">
          标签过滤：
          <TagBadge tag={activeTag} />
          <button onClick={() => setActiveTag('')} className="text-xs hover:underline">
            ✕ 清除
          </button>
        </div>
      )}

      {/* Timeline */}
      {loading ? (
        <p className="text-sm text-neutral-400">加载中...</p>
      ) : captures.length === 0 ? (
        <p className="text-sm text-neutral-400">
          {search ? '无匹配结果' : '尚未捕获任何内容'}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {captures.map(c => (
            <CaptureCard key={c.id} capture={c} onTagClick={setActiveTag} />
          ))}
        </div>
      )}
    </div>
  )
}
