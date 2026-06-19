import type { WorkbenchSideContext } from './KnowledgeWorkbench'

interface WorkbenchSidePanelProps {
  context: WorkbenchSideContext
  hidden?: boolean
}

export function WorkbenchSidePanel({ context, hidden = false }: WorkbenchSidePanelProps) {
  return (
    <aside className={`${hidden ? 'hidden' : ''} w-56 shrink-0 border-l border-neutral-200 dark:border-neutral-800
      overflow-y-auto px-4 py-3`}>
      <h3 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-3">
        {context.title}
      </h3>
      {context.sections.length > 0 ? (
        <div className="text-sm space-y-3">
          {context.sections.map(section => (
            <div key={`${section.label}-${section.value || section.tags?.join(',') || ''}`}>
              <p className="text-xs text-neutral-500">{section.label}</p>
              {section.tags ? (
                <div className="flex flex-wrap gap-1 mt-1">
                  {section.tags.map(tag => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 text-xs rounded-full bg-neutral-100 dark:bg-neutral-800
                        text-neutral-600 dark:text-neutral-400"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              ) : (
                <p
                  className={`${section.mono ? 'font-mono' : ''} text-xs text-neutral-600
                    dark:text-neutral-400 whitespace-pre-wrap break-words`}
                >
                  {section.value}
                </p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-neutral-400">
          {context.emptyText}
        </p>
      )}
      {context.sections.length > 0 && context.emptyText && (
        <p className="text-xs text-neutral-400 mt-4">
          {context.emptyText}
        </p>
      )}
    </aside>
  )
}
