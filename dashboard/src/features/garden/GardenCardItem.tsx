import type { GardenCard } from '../../lib/api'

export default function GardenCardItem({
  card,
  kindLabels,
  statusLabels,
}: {
  card: GardenCard
  kindLabels: Record<string, string>
  statusLabels: Record<string, string>
}) {
  return (
    <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-700">
      <div className="mb-2 flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium text-neutral-800 dark:text-neutral-200">
          {card.title}
        </h3>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs
            ${card.status === 'accepted'
              ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
              : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300'
            }`}
        >
          {statusLabels[card.status] || card.status}
        </span>
      </div>
      <div className="mb-2 flex gap-2">
        <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
          {kindLabels[card.card_kind] || card.card_kind}
        </span>
      </div>
      {card.claim && (
        <p className="line-clamp-3 text-xs text-neutral-600 dark:text-neutral-400">
          {card.claim}
        </p>
      )}
      {card.file_path && (
        <p className="mt-2 truncate font-mono text-xs text-neutral-400">
          {card.file_path}
        </p>
      )}
    </div>
  )
}
