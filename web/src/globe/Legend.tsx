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
  sizeKey,
  className = '',
}: {
  /** The smallest and biggest story drawn, in articles this week: the key to marker size. */
  sizeKey: { smallest: number; biggest: number } | null
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
      {sizeKey && <SizeKey {...sizeKey} />}
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

/** Three sample markers, from the smallest story drawn to the biggest, with their article counts. */
function SizeKey({ smallest, biggest }: { smallest: number; biggest: number }) {
  const at = (t: number) => Math.round(smallest * (biggest / Math.max(smallest, 1)) ** t)
  const samples = [0, 0.5, 1].map((t) => ({ t, count: at(t), px: 6 + 16 * t }))
  return (
    <div className="mt-2 border-t border-slate-700/60 pt-2">
      <p className="mb-1 text-[11px] text-slate-400">Marker size: articles this week</p>
      <div className="flex items-end justify-between px-1">
        {samples.map(({ t, count, px }) => (
          <div
            key={t}
            className="flex flex-col items-center gap-1 text-[11px] tabular-nums text-slate-300"
          >
            <span className="rounded-full bg-slate-300/80" style={{ width: px, height: px }} />
            {count.toLocaleString()}
          </div>
        ))}
      </div>
    </div>
  )
}
