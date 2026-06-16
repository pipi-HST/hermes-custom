import { useEffect, useState } from 'react'
import type { GardenCard } from '../lib/api'
import { fetchGardenCards } from '../lib/api'

export default function GardenPage() {
  const [cards, setCards] = useState<GardenCard[]>([])
  const [loading, setLoading] = useState(true)
  const [filterKind, setFilterKind] = useState<string>('')

  useEffect(() => {
    setLoading(true)
    fetchGardenCards(100).then(items => {
      setCards(items)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const kinds = ['viewpoint', 'fact', 'method', 'risk']
  const kindLabels: Record<string, string> = {
    viewpoint: '观点',
    fact: '事实',
    method: '方法',
    risk: '风险',
  }
  const statusLabels: Record<string, string> = {
    seed: '种子',
    accepted: '已接受',
    archived: '已归档',
  }

  const filtered = filterKind
    ? cards.filter(c => c.card_kind === filterKind)
    : cards

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <h1 className="text-lg font-medium mb-6 text-neutral-900 dark:text-neutral-100">
        知识卡片
      </h1>

      {/* Kind filter */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setFilterKind('')}
          className={`px-3 py-1 text-xs rounded-md border transition-colors
            ${!filterKind
              ? 'border-neutral-400 dark:border-neutral-600 bg-neutral-100 dark:bg-neutral-800'
              : 'border-neutral-200 dark:border-neutral-700 text-neutral-500'
            }`}
        >
          全部
        </button>
        {kinds.map(k => (
          <button
            key={k}
            onClick={() => setFilterKind(k)}
            className={`px-3 py-1 text-xs rounded-md border transition-colors
              ${filterKind === k
                ? 'border-neutral-400 dark:border-neutral-600 bg-neutral-100 dark:bg-neutral-800'
                : 'border-neutral-200 dark:border-neutral-700 text-neutral-500'
              }`}
          >
            {kindLabels[k] || k}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-neutral-400">加载中...</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-neutral-400">暂无知识卡片</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filtered.map(card => (
            <div
              key={card.id}
              className="border border-neutral-200 dark:border-neutral-700 rounded-lg p-4"
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <h3 className="text-sm font-medium text-neutral-800 dark:text-neutral-200">
                  {card.title}
                </h3>
                <span
                  className={`shrink-0 text-xs px-2 py-0.5 rounded-full
                    ${card.status === 'accepted'
                      ? 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300'
                      : 'bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300'
                    }`}
                >
                  {statusLabels[card.status] || card.status}
                </span>
              </div>
              <div className="flex gap-2 mb-2">
                <span className="text-xs px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800
                  text-neutral-600 dark:text-neutral-400">
                  {kindLabels[card.card_kind] || card.card_kind}
                </span>
              </div>
              {card.claim && (
                <p className="text-xs text-neutral-600 dark:text-neutral-400 line-clamp-3">
                  {card.claim}
                </p>
              )}
              {card.file_path && (
                <p className="text-xs text-neutral-400 mt-2 font-mono truncate">
                  {card.file_path}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
