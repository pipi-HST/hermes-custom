import type { WorkbenchSideContext } from './KnowledgeWorkbench'

interface WorkbenchSidePanelProps {
  context: WorkbenchSideContext
  hidden?: boolean
}

interface WorkbenchSideContextContentProps {
  context?: WorkbenchSideContext | null
  variant?: 'plain' | 'cards'
}

export function WorkbenchSidePanel({ context, hidden = false }: WorkbenchSidePanelProps) {
  return (
    <aside className={`${hidden ? 'hidden' : ''} w-56 shrink-0 border-l border-neutral-200 dark:border-neutral-800
      overflow-y-auto px-4 py-3`}>
      <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-3">
        {context.title}
      </h3>
      <WorkbenchSideContextContent context={context} variant="plain" />
    </aside>
  )
}

export function WorkbenchSideContextContent({
  context,
  variant = 'plain',
}: WorkbenchSideContextContentProps) {
  const emptyText = context?.emptyText || '选择草稿或对话以查看相关信息'
  const cardStyle = variant === 'cards'

  if (!context || context.sections.length === 0) {
    return (
      <p className={cardStyle
        ? 'mt-4 rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-400 dark:border-neutral-700'
        : 'text-xs text-neutral-400'}
      >
        {emptyText}
      </p>
    )
  }

  return (
    <section className={cardStyle ? 'mt-4 space-y-3' : 'text-sm space-y-3'}>
      {context.sections.map(section => (
        <div
          key={`${section.label}-${section.value || section.tags?.join(',') || ''}`}
          className={cardStyle
            ? 'rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900'
            : ''}
        >
          <p className="text-xs text-neutral-500">{section.label}</p>
          {section.tags ? (
            <div className={`${cardStyle ? 'mt-2' : 'mt-1'} flex flex-wrap gap-1`}>
              {section.tags.map(tag => (
                <span
                  key={tag}
                  className={cardStyle
                    ? 'rounded-md bg-neutral-100 px-2 py-1 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'
                    : 'px-2 py-0.5 text-xs rounded-full bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400'}
                >
                  {tag}
                </span>
              ))}
            </div>
          ) : (
            <p
              className={cardStyle
                ? `mt-1 whitespace-pre-wrap text-sm text-neutral-700 dark:text-neutral-300 ${section.mono ? 'font-mono text-xs' : ''}`
                : `${section.mono ? 'font-mono' : ''} text-xs text-neutral-600 dark:text-neutral-400 whitespace-pre-wrap break-words`}
            >
              {section.value || '-'}
            </p>
          )}
        </div>
      ))}
      {context.emptyText && (
        <p className={cardStyle
          ? 'rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-400 dark:border-neutral-700'
          : 'text-xs text-neutral-400 mt-4'}
        >
          {context.emptyText}
        </p>
      )}
    </section>
  )
}
