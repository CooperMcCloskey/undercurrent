import { ChevronDown, ChevronUp } from 'lucide-react'
import { CATEGORY_STYLE } from '../globe/categories'
import { describeFactor, type Emerging, type EmergingKind, type EmergingView } from './forecast'

const HEADINGS: Record<EmergingKind, (days: number) => { title: string; note: string }> = {
  prediction: (days) => ({
    title: 'Prediction',
    note: `Stories the model expects to jump over the next ${days} days, even at the low end of its range.`,
  }),
  actual: (days) => ({
    title: `What jumped in the next ${days} days`,
    note: 'Stories that at least doubled in the days after this one: what really happened.',
  }),
  momentum: () => ({
    title: 'Growing fastest',
    note: 'No forecast for this day yet, so this is not a prediction: the stories that at least doubled this week.',
  }),
}

/**
 * The stories that jump, always on screen. For a day near the present, where the model has a
 * forecast, it is a prediction: ranked by projected growth, with the range, the main reason and a
 * sketch. For an earlier day it is what actually happened, read from the later snapshots. With no
 * forecast at all it falls back to the fastest-growing stories, and says it is not a prediction.
 * Clicking a row selects the story on the globe.
 */
export function EmergingList({
  view,
  loading,
  open,
  onToggle,
  selectedId,
  onSelect,
  onHover,
  className = '',
}: {
  view: EmergingView
  loading: boolean
  open: boolean
  onToggle: () => void
  selectedId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  className?: string
}) {
  const { title, note } = HEADINGS[view.kind](view.days)
  const accent = view.kind === 'prediction' ? 'text-amber-200' : 'text-emerald-300'
  return (
    <aside
      className={`flex flex-col overflow-hidden rounded-lg bg-slate-900/90 shadow-xl ${className}`}
    >
      <button
        className="flex items-start justify-between gap-2 p-3 text-left hover:bg-slate-800/60"
        onClick={onToggle}
        aria-expanded={open}
      >
        <span>
          <span className={`block text-sm font-semibold ${accent}`}>{title}</span>
          {open && (
            <span className="mt-0.5 block text-[11px] leading-snug text-slate-400">{note}</span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-[11px] text-slate-400">
          {view.rows.length}
          {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </span>
      </button>
      {open && (
        <ol className="min-h-0 flex-1 divide-y divide-slate-800 overflow-y-auto border-t border-slate-700/60">
          {loading && <li className="p-3 text-xs text-slate-400">Loading…</li>}
          {!loading && view.rows.length === 0 && (
            <li className="p-3 text-xs text-slate-400">No story jumps on this day.</li>
          )}
          {view.rows.map((row) => (
            <Row
              key={row.story.id}
              row={row}
              kind={view.kind}
              days={view.days}
              selected={row.story.id === selectedId}
              onSelect={onSelect}
              onHover={onHover}
            />
          ))}
        </ol>
      )}
    </aside>
  )
}

function Row({
  row,
  kind,
  days,
  selected,
  onSelect,
  onHover,
}: {
  row: Emerging
  kind: EmergingKind
  days: number
  selected: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}) {
  const { color, label } = CATEGORY_STYLE[row.story.category]
  const { horizon } = row
  const inside = row.actual !== null && row.actual >= horizon.low && row.actual <= horizon.high
  return (
    <li>
      <button
        className={`block w-full px-3 py-2 text-left hover:bg-slate-800/70 ${selected ? 'bg-slate-800' : ''}`}
        onClick={() => onSelect(row.story.id)}
        onMouseEnter={() => onHover(row.story.id)}
        onMouseLeave={() => onHover(null)}
      >
        <div className="flex items-center gap-1.5 text-[11px]" style={{ color }}>
          <span className="size-2 rounded-full" style={{ background: color }} />
          {label}
          <span className="ml-auto font-semibold text-emerald-300">
            {row.isNew ? 'New' : `×${(2 ** row.growth).toFixed(1)}`}
          </span>
        </div>
        <p className="mt-0.5 line-clamp-2 text-sm font-medium leading-snug">{row.story.title}</p>
        <p className="text-[11px] text-slate-400">{row.story.location.name}</p>
        <div className="mt-1.5 flex items-center gap-3">
          {kind === 'prediction' && <Sketch row={row} color={color} />}
          <div className="text-[11px] leading-snug text-slate-300">
            {kind === 'prediction' && (
              <>
                <p>
                  {row.story.article_count_7d} now → ~{Math.round(horizon.articles_7d)} in {days}{' '}
                  days
                </p>
                <p className="text-slate-500">
                  range {Math.round(horizon.low)}–{Math.round(horizon.high)}
                  {row.factor && ` · ${describeFactor(row.factor)}`}
                </p>
                {row.actual !== null && (
                  <p className={inside ? 'text-emerald-300' : 'text-amber-300'}>
                    Actually {row.actual}: {inside ? 'inside' : 'outside'} the range
                  </p>
                )}
              </>
            )}
            {kind === 'actual' && (
              <p>
                {row.story.article_count_7d} articles a week →{' '}
                <b>{Math.round(horizon.articles_7d)}</b> {days} days later
              </p>
            )}
            {kind === 'momentum' && (
              <p>
                {row.story.article_count_7d} articles this week
                {row.isNew ? ', almost none last week' : ', up from last week'}
              </p>
            )}
          </div>
        </div>
      </button>
    </li>
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
