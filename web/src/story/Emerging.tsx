import { X } from 'lucide-react'
import { CATEGORY_STYLE } from '../globe/categories'
import { describeFactor, type Emerging } from './forecast'

/**
 * The stories projected to grow, ranked by how much (docs: "What we predict, and how it's shown").
 * A row is the story, where, today's size, the 7-day projection with its range and its main
 * reason, and a sketch of the forecast. Clicking a row selects the story on the globe. For a day
 * in the past, each row also says what the story actually did.
 */
export function EmergingList({
  rows,
  selectedId,
  onSelect,
  onHover,
  onClose,
  className = '',
}: {
  rows: readonly Emerging[]
  selectedId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  onClose: () => void
  className?: string
}) {
  const replay = rows.some((r) => r.actual !== null)
  return (
    <aside
      className={`flex flex-col overflow-hidden rounded-lg bg-slate-900/95 shadow-xl ${className}`}
    >
      <header className="flex items-start justify-between border-b border-slate-700/60 p-3">
        <div>
          <h2 className="text-sm font-semibold">Emerging stories</h2>
          <p className="text-[11px] text-slate-400">
            Projected to be bigger in a week, even at the low end of the range.
          </p>
        </div>
        <button
          className="-m-1 rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
          onClick={onClose}
          aria-label="Close the emerging stories list"
        >
          <X className="size-4" />
        </button>
      </header>
      <ol className="min-h-0 flex-1 divide-y divide-slate-800 overflow-y-auto">
        {rows.length === 0 && (
          <li className="p-4 text-xs text-slate-400">
            No story is projected to grow on this day (or none has a forecast yet).
          </li>
        )}
        {rows.map((r) => {
          const { color, label } = CATEGORY_STYLE[r.story.category]
          const { horizon } = r
          return (
            <li key={r.story.id}>
              <button
                className={`block w-full px-3 py-2.5 text-left hover:bg-slate-800/70 ${r.story.id === selectedId ? 'bg-slate-800' : ''}`}
                onClick={() => onSelect(r.story.id)}
                onMouseEnter={() => onHover(r.story.id)}
                onMouseLeave={() => onHover(null)}
              >
                <div className="flex items-center gap-1.5 text-[11px]" style={{ color }}>
                  <span className="size-2 rounded-full" style={{ background: color }} />
                  {label}
                  <span className="ml-auto font-semibold text-emerald-300">
                    ×{(2 ** r.growth).toFixed(1)}
                  </span>
                </div>
                <p className="mt-0.5 line-clamp-2 text-sm font-medium leading-snug">
                  {r.story.title}
                </p>
                <p className="text-[11px] text-slate-400">{r.story.location.name}</p>
                <div className="mt-1.5 flex items-center gap-3">
                  <Sketch row={r} color={color} />
                  <div className="text-[11px] leading-snug text-slate-300">
                    <p>
                      {r.story.article_count_7d} now → ~{Math.round(horizon.articles_7d)} in{' '}
                      {horizon.days} days
                    </p>
                    <p className="text-slate-500">
                      range {Math.round(horizon.low)}–{Math.round(horizon.high)}
                      {r.factor && ` · ${describeFactor(r.factor)}`}
                    </p>
                    {r.actual !== null && (
                      <p
                        className={
                          r.actual >= horizon.low && r.actual <= horizon.high
                            ? 'text-emerald-300'
                            : 'text-amber-300'
                        }
                      >
                        Actually {r.actual}
                        {r.actual >= horizon.low && r.actual <= horizon.high
                          ? ': inside the range'
                          : ': outside the range'}
                      </p>
                    )}
                  </div>
                </div>
              </button>
            </li>
          )
        })}
      </ol>
      {replay && (
        <footer className="border-t border-slate-700/60 px-3 py-2 text-[11px] text-slate-500">
          This day is in the past, so each row says what the story actually did.
        </footer>
      )}
    </aside>
  )
}

const W = 84
const H = 34

/** A tiny forecast graph: today, then each horizon's middle estimate over its range. */
function Sketch({ row, color }: { row: Emerging; color: string }) {
  const { story } = row
  const horizons = story.forecast?.horizons ?? []
  const reach = Math.max(1, ...horizons.map((h) => h.days))
  const hi = Math.max(story.article_count_7d, ...horizons.map((h) => h.high))
  const lo = Math.min(story.article_count_7d, ...horizons.map((h) => h.low))
  const x = (days: number) => 3 + (days / reach) * (W - 6)
  const y = (v: number) => H - 3 - ((v - lo) / Math.max(hi - lo, 1)) * (H - 6)
  const mid = [[0, story.article_count_7d], ...horizons.map((h) => [h.days, h.articles_7d])]
  const band = [
    [0, story.article_count_7d, story.article_count_7d],
    ...horizons.map((h) => [h.days, h.low, h.high]),
  ]
  const upper = band.map(([d, , h]) => `${x(d)},${y(h)}`)
  const lower = band.map(([d, l]) => `${x(d)},${y(l)}`).reverse()
  return (
    <svg width={W} height={H} className="shrink-0" aria-hidden>
      <polygon points={[...upper, ...lower].join(' ')} fill={color} fillOpacity={0.25} />
      <polyline
        points={mid.map(([d, v]) => `${x(d)},${y(v)}`).join(' ')}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeDasharray="4 2"
      />
      <circle cx={x(0)} cy={y(story.article_count_7d)} r={2.5} fill="#e2e8f0" />
    </svg>
  )
}
