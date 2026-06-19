export default function GardenFilters({
  filterKind,
  filterStatus,
  kindLabels,
  kinds,
  onFilterKindChange,
  onFilterStatusChange,
  statusLabels,
  statuses,
}: {
  filterKind: string
  filterStatus: string
  kindLabels: Record<string, string>
  kinds: string[]
  onFilterKindChange: (kind: string) => void
  onFilterStatusChange: (status: string) => void
  statusLabels: Record<string, string>
  statuses: string[]
}) {
  return (
    <div className="mb-6 space-y-3">
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => onFilterKindChange('')}
          className={`rounded-md border px-3 py-1 text-xs transition-colors
            ${!filterKind
              ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
              : 'border-neutral-200 text-neutral-500 dark:border-neutral-700'
            }`}
        >
          全部类型
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
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => onFilterStatusChange('')}
          className={`rounded-md border px-3 py-1 text-xs transition-colors
            ${!filterStatus
              ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
              : 'border-neutral-200 text-neutral-500 dark:border-neutral-700'
            }`}
        >
          全部状态
        </button>
        {statuses.map(status => (
          <button
            key={status}
            onClick={() => onFilterStatusChange(status)}
            className={`rounded-md border px-3 py-1 text-xs transition-colors
              ${filterStatus === status
                ? 'border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800'
                : 'border-neutral-200 text-neutral-500 dark:border-neutral-700'
              }`}
          >
            {statusLabels[status] || status}
          </button>
        ))}
      </div>
    </div>
  )
}
