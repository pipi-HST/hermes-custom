export default function GardenFilters({
  filterKind,
  kindLabels,
  kinds,
  onFilterKindChange,
}: {
  filterKind: string
  kindLabels: Record<string, string>
  kinds: string[]
  onFilterKindChange: (kind: string) => void
}) {
  return (
    <div className="mb-6 flex gap-2">
      <button
        onClick={() => onFilterKindChange('')}
        className={`rounded-md border px-3 py-1 text-xs transition-colors
          ${!filterKind
            ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
            : 'border-neutral-200 text-neutral-500 dark:border-neutral-700'
          }`}
      >
        全部
      </button>
      {kinds.map(kind => (
        <button
          key={kind}
          onClick={() => onFilterKindChange(kind)}
          className={`rounded-md border px-3 py-1 text-xs transition-colors
            ${filterKind === kind
              ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
              : 'border-neutral-200 text-neutral-500 dark:border-neutral-700'
            }`}
        >
          {kindLabels[kind] || kind}
        </button>
      ))}
    </div>
  )
}
