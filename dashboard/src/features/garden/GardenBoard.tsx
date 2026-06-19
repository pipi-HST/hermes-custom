import GardenCardItem from './GardenCardItem'
import GardenFilters from './GardenFilters'
import { useGardenCards } from './useGardenCards'

export default function GardenBoard({ embedded = false }: { embedded?: boolean }) {
  const {
    filteredCards,
    filterKind,
    kindLabels,
    kinds,
    loading,
    setFilterKind,
    statusLabels,
  } = useGardenCards(100)

  return (
    <div className={embedded ? 'px-5 py-5' : 'max-w-4xl mx-auto px-6 py-8'}>
      {!embedded && (
        <h1 className="text-lg font-medium mb-6 text-neutral-900 dark:text-neutral-100">
          知识卡片
        </h1>
      )}

      <GardenFilters
        filterKind={filterKind}
        kindLabels={kindLabels}
        kinds={kinds}
        onFilterKindChange={setFilterKind}
      />

      {loading ? (
        <p className="text-sm text-neutral-400">加载中...</p>
      ) : filteredCards.length === 0 ? (
        <p className="text-sm text-neutral-400">暂无知识卡片</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filteredCards.map(card => (
            <GardenCardItem
              key={card.id}
              card={card}
              kindLabels={kindLabels}
              statusLabels={statusLabels}
            />
          ))}
        </div>
      )}
    </div>
  )
}
