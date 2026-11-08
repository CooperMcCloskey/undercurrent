import { CATEGORY_STYLE, type Category } from './categories'

/**
 * The categories on the globe today, one line each: click to hide or show it, "only" to show just
 * that one. Greyed out while hidden.
 */
export function Legend({
  groups,
  hidden,
  onToggle,
  onOnly,
  onShowAll,
  className = '',
}: {
  groups: readonly { category: Category; count: number }[]
  hidden: ReadonlySet<Category>
  onToggle: (category: Category) => void
  onOnly: (category: Category) => void
  onShowAll: () => void
  className?: string
}) {
  return (
    <aside className={`w-52 rounded-lg bg-slate-900/85 p-2 text-xs ${className}`}>
      <ul>
        {groups.map(({ category, count }) => {
          const off = hidden.has(category)
          const { label, color } = CATEGORY_STYLE[category]
          return (
            <li
              key={category}
              className={`group flex items-center gap-1 ${off ? 'opacity-40' : ''}`}
            >
              <button
                className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-slate-800"
                aria-pressed={!off}
                title={off ? `Show ${label}` : `Hide ${label}`}
                onClick={() => onToggle(category)}
              >
                <span
                  className="size-2.5 shrink-0 rounded-full border-2"
                  style={{ borderColor: color, background: off ? 'transparent' : color }}
                />
                <span className="truncate">{label}</span>
                <span className="ml-auto tabular-nums text-slate-400">{count}</span>
              </button>
              <button
                className="invisible rounded px-1 py-0.5 text-slate-400 hover:bg-slate-700 hover:text-slate-100 group-hover:visible"
                onClick={() => onOnly(category)}
              >
                only
              </button>
            </li>
          )
        })}
      </ul>
      {hidden.size > 0 && (
        <button
          className="mt-1 w-full rounded px-1 py-0.5 text-left text-slate-300 hover:bg-slate-800"
          onClick={onShowAll}
        >
          Show all
        </button>
      )}
    </aside>
  )
}
