export function TagBadge({ tag }: { tag: string }) {
  return (
    <span className="inline-block px-2 py-0.5 text-xs rounded-full
      bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
      {tag}
    </span>
  )
}
