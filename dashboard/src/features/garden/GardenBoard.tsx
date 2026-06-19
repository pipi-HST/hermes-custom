import { useEffect, useMemo, useState } from 'react'
import type { GardenCard } from '../../lib/api'
import GardenCardItem from './GardenCardItem'
import GardenFilters from './GardenFilters'
import { useGardenCards } from './useGardenCards'

export default function GardenBoard({ embedded = false }: { embedded?: boolean }) {
  const {
    filteredCards,
    filterKind,
    filterStatus,
    kindLabels,
    kinds,
    loading,
    setFilterKind,
    setFilterStatus,
    statusLabels,
    statuses,
  } = useGardenCards(100)
  const [selectedCardId, setSelectedCardId] = useState('')
  const selectedCard = useMemo(
    () => filteredCards.find(card => card.id === selectedCardId) || filteredCards[0],
    [filteredCards, selectedCardId],
  )

  useEffect(() => {
    if (!selectedCard) {
      setSelectedCardId('')
      return
    }
    if (selectedCard.id !== selectedCardId) {
      setSelectedCardId(selectedCard.id)
    }
  }, [selectedCard, selectedCardId])

  return (
    <div className={embedded ? 'px-5 py-5' : 'max-w-4xl mx-auto px-6 py-8'}>
      {!embedded && (
        <h1 className="text-lg font-medium mb-6 text-neutral-900 dark:text-neutral-100">
          知识卡片
        </h1>
      )}

      <GardenFilters
        filterKind={filterKind}
        filterStatus={filterStatus}
        kindLabels={kindLabels}
        kinds={kinds}
        onFilterKindChange={setFilterKind}
        onFilterStatusChange={setFilterStatus}
        statusLabels={statusLabels}
        statuses={statuses}
      />

      {loading ? (
        <p className="text-sm text-neutral-400">加载中...</p>
      ) : filteredCards.length === 0 ? (
        <p className="text-sm text-neutral-400">暂无知识卡片</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {filteredCards.map(card => (
              <GardenCardItem
                key={card.id}
                card={card}
                isSelected={selectedCard?.id === card.id}
                kindLabels={kindLabels}
                onSelect={item => setSelectedCardId(item.id)}
                statusLabels={statusLabels}
              />
            ))}
          </div>
          <GardenCardDetail
            card={selectedCard}
            kindLabels={kindLabels}
            statusLabels={statusLabels}
          />
        </div>
      )}
    </div>
  )
}

function GardenCardDetail({
  card,
  kindLabels,
  statusLabels,
}: {
  card?: GardenCard
  kindLabels: Record<string, string>
  statusLabels: Record<string, string>
}) {
  if (!card) {
    return (
      <aside className="rounded-lg border border-dashed border-neutral-300 bg-white p-4 text-sm text-neutral-400 dark:border-neutral-700 dark:bg-neutral-900">
        选择卡片查看完整内容
      </aside>
    )
  }

  return (
    <aside className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-neutral-500">当前卡片</p>
          <h2 className="mt-1 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
            {card.title || '(无标题)'}
          </h2>
        </div>
        <span className="rounded-md bg-neutral-100 px-2 py-1 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
          {statusLabels[card.status] || card.status}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <span className="rounded-md bg-[#ecf4ef] px-2 py-1 text-xs text-[#315f49] dark:bg-[#203229] dark:text-[#9cc7ae]">
          {kindLabels[card.card_kind] || card.card_kind}
        </span>
      </div>

      <section className="mt-4">
        <h3 className="text-xs font-medium text-neutral-500">完整内容</h3>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-neutral-700 dark:text-neutral-300">
          {card.claim || '暂无内容'}
        </p>
      </section>

      <dl className="mt-5 space-y-3 text-xs">
        <div>
          <dt className="text-neutral-500">文件路径</dt>
          <dd className="mt-1 break-all font-mono text-neutral-600 dark:text-neutral-400">
            {card.file_path || '-'}
          </dd>
        </div>
        <div>
          <dt className="text-neutral-500">创建时间</dt>
          <dd className="mt-1 text-neutral-600 dark:text-neutral-400">
            {formatDateTime(card.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-neutral-500">更新时间</dt>
          <dd className="mt-1 text-neutral-600 dark:text-neutral-400">
            {formatDateTime(card.updated_at)}
          </dd>
        </div>
      </dl>
    </aside>
  )
}

function formatDateTime(value: string) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
