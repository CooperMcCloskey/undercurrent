import { useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, X } from 'lucide-react'
import type { Entity, StorySummary, TimelinePoint } from '../api/types.gen'
import { CATEGORY_STYLE } from '../globe/categories'
import {
  KIND_ORDER,
  KIND_STYLE,
  articlesOnGlobe,
  GLYPH_PX,
  formatWhen,
  hostOf,
  safeHref,
  type Article,
  type SourceKind,
} from './articles'
import { describeFactor, describeForecast, headlineHorizon } from './forecast'
import { ForecastChart, buildSeries } from './ForecastChart'
import { KindGlyph } from './KindGlyph'
import { toIndex } from '../timeline/days'

type Sort = 'importance' | 'newest'

/**
 * The selected story: its numbers, and every article, post and Wikipedia page in it, with links.
 * Hovering a row lights up its dot on the globe and the other way round (`highlightedId`);
 * clicking a dot scrolls its row into view (`pickedId`).
 */
export function StoryPanel({
  story,
  summary,
  timeline,
  entities,
  asOf,
  articles,
  status,
  error,
  onRetry,
  highlightedId,
  pickedId,
  onHighlight,
  onClose,
}: {
  story: StorySummary
  summary: string | null
  timeline: TimelinePoint[]
  entities: Entity[]
  /** The day the story was read on (YYYY-MM-DD): the chart's "today". */
  asOf: string
  articles: Article[]
  status: 'pending' | 'error' | 'success'
  error: Error | null
  onRetry: () => void
  highlightedId: string | null
  pickedId: string | null
  onHighlight: (id: string | null) => void
  onClose: () => void
}) {
  const { label, color } = CATEGORY_STYLE[story.category]
  const [kind, setKind] = useState<SourceKind | 'all'>('all')
  const [sort, setSort] = useState<Sort>('importance')

  const counts = useMemo(() => {
    const byKind: Partial<Record<SourceKind, number>> = {}
    for (const a of articles) byKind[a.kind] = (byKind[a.kind] ?? 0) + 1
    return byKind
  }, [articles])
  const rows = useMemo(() => {
    const matching = kind === 'all' ? articles : articles.filter((a) => a.kind === kind)
    // `articles` is already ranked by importance.
    return sort === 'newest'
      ? [...matching].sort((a, b) => b.published_at.localeCompare(a.published_at))
      : matching
  }, [articles, kind, sort])

  // A dot was clicked on the globe: show its row, even if the filter had hidden it.
  const rowRefs = useRef(new Map<string, HTMLLIElement>())
  useEffect(() => {
    if (!pickedId) return
    const picked = articles.find((a) => a.id === pickedId)
    if (picked && kind !== 'all' && picked.kind !== kind) {
      setKind('all') // the row mounts on the next render; this effect runs again for it
      return
    }
    rowRefs.current.get(pickedId)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [pickedId, articles, kind])

  const onGlobe = useMemo(() => articlesOnGlobe(articles).length, [articles])
  const ratio = 2 ** story.growth_7d // growth_7d is log2(this week / last week)
  const kinds = KIND_ORDER.filter((k) => counts[k])

  return (
    <aside className="absolute bottom-16 right-6 top-5 z-10 flex w-[26rem] max-w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-lg bg-slate-900/95 text-slate-100 shadow-xl">
      <header className="border-b border-slate-700/60 p-4 pb-3">
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="flex items-center gap-1.5 font-medium" style={{ color }}>
            <span className="size-2 rounded-full" style={{ background: color }} />
            {label}
          </span>
          <button
            className="-m-1 rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
            onClick={onClose}
            aria-label="Close story (Esc)"
            title="Close (Esc)"
          >
            <X className="size-4" />
          </button>
        </div>
        <h2 className="text-lg font-semibold leading-snug">{story.title}</h2>
        <p className="mt-1 text-xs text-slate-400">
          {story.location.name} · since {story.first_seen}
        </p>
        {summary && <p className="mt-2 line-clamp-3 text-xs text-slate-300">{summary}</p>}
        <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <Stat term="Articles, 7 days" value={story.article_count_7d} />
          <Stat term="Posts, 7 days" value={story.post_count_7d} />
          <Stat
            term="Vs previous week"
            value={`${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×`}
            tone={ratio >= 1 ? 'up' : 'down'}
          />
        </dl>
        <ForecastSection story={story} timeline={timeline} asOf={asOf} color={color} />
        {entities.length > 0 && (
          <p className="mt-2 flex flex-wrap gap-1 text-[11px] text-slate-300">
            {entities.slice(0, 6).map((e) => (
              <span key={e.name} className="rounded bg-slate-800 px-1.5 py-0.5">
                {e.name}
              </span>
            ))}
          </p>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-700/60 px-4 py-2.5 text-xs">
        <Chip active={kind === 'all'} onClick={() => setKind('all')}>
          All {articles.length}
        </Chip>
        {kinds.map((k) => (
          <Chip key={k} active={kind === k} onClick={() => setKind(k)}>
            <KindGlyph kind={k} color={color} size={GLYPH_PX} />
            {KIND_STYLE[k].label} {counts[k]}
          </Chip>
        ))}
        <select
          className="ml-auto rounded bg-slate-800 px-1.5 py-1 text-slate-200 outline-none focus-visible:ring-1 focus-visible:ring-slate-400"
          value={sort}
          onChange={(e) => setSort(e.target.value as Sort)}
          aria-label="Sort"
        >
          <option value="importance">Most important</option>
          <option value="newest">Newest</option>
        </select>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {status === 'pending' && <p className="p-4 text-xs text-slate-400">Loading articles…</p>}
        {status === 'error' && (
          <div className="p-4 text-xs text-slate-300">
            <p className="mb-2">Couldn’t load this story’s articles: {error?.message}</p>
            <button className="rounded bg-slate-700 px-2 py-1 hover:bg-slate-600" onClick={onRetry}>
              Try again
            </button>
          </div>
        )}
        {status === 'success' && rows.length === 0 && (
          <p className="p-4 text-xs text-slate-400">No articles in this story yet.</p>
        )}
        <ul className="divide-y divide-slate-800">
          {rows.map((a) => {
            const href = safeHref(a.url)
            const lit = a.id === highlightedId
            return (
              <li
                key={a.id}
                ref={(el) => {
                  if (el) rowRefs.current.set(a.id, el)
                  else rowRefs.current.delete(a.id)
                }}
                className={`flex gap-3 px-4 py-2.5 ${lit ? 'bg-slate-700/60' : a.id === pickedId ? 'bg-slate-800' : ''}`}
                onMouseEnter={() => onHighlight(a.id)}
                onMouseLeave={() => onHighlight(null)}
              >
                {/* Same mark as its dots on the globe. */}
                <span
                  className="mt-0.5 flex w-[22px] shrink-0 justify-center"
                  title={`${KIND_STYLE[a.kind].label}, importance ${Math.round(a.importance * 100)}%`}
                >
                  <KindGlyph kind={a.kind} color={color} size={GLYPH_PX} />
                </span>
                <div className="min-w-0 flex-1">
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group inline text-sm font-medium leading-snug text-slate-100 hover:underline focus-visible:underline"
                    >
                      {a.title}
                      <ExternalLink className="ml-1 inline size-3 -translate-y-px text-slate-500 group-hover:text-slate-300" />
                    </a>
                  ) : (
                    <span className="text-sm font-medium leading-snug">{a.title}</span>
                  )}
                  <p className="mt-0.5 truncate text-xs text-slate-400">
                    {places(a)}
                    {hostOf(a)}
                    {a.language && a.language !== 'en' && ` · ${a.language.toUpperCase()}`} ·{' '}
                    {formatWhen(a.published_at)}
                  </p>
                </div>
              </li>
            )
          })}
        </ul>
      </div>

      <footer className="border-t border-slate-700/60 px-4 py-2 text-[11px] leading-snug text-slate-500">
        Each circle on the globe is an article, each diamond a post, at a place it mentions.
        {onGlobe < articles.length && ` The globe shows the top ${onGlobe} of ${articles.length}.`}
      </footer>
    </aside>
  )
}

/**
 * The forecast graph: this story's articles per 7 days up to the day shown, the forecast with its
 * range, and, for a day that has since passed, a button that reveals what actually happened.
 */
function ForecastSection({
  story,
  timeline,
  asOf,
  color,
}: {
  story: StorySummary
  timeline: TimelinePoint[]
  asOf: string
  color: string
}) {
  const [reveal, setReveal] = useState(false)
  const day = toIndex(asOf)
  const { points, hasLater } = useMemo(
    () => buildSeries(timeline, day, story.article_count_7d, story.forecast, reveal),
    [timeline, day, story.article_count_7d, story.forecast, reveal],
  )
  if (timeline.length === 0) return null
  const horizon = story.forecast ? headlineHorizon(story.forecast) : null
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between text-[11px] text-slate-400">
        <span>Articles in the past 7 days</span>
        {story.forecast && hasLater && (
          <button
            className="rounded px-1.5 py-0.5 text-emerald-300 hover:bg-slate-700"
            onClick={() => setReveal(!reveal)}
          >
            {reveal ? 'Hide what happened' : 'Reveal what happened'}
          </button>
        )}
      </div>
      <ForecastChart points={points} asOf={day} color={color} />
      {story.forecast && horizon ? (
        <>
          <p className="mt-1 text-xs text-slate-300">{describeForecast(story.forecast)}</p>
          {horizon.factors.length > 0 && (
            <p className="text-[11px] text-slate-500">
              Mostly because of {horizon.factors.slice(0, 2).map(describeFactor).join(', ')}
            </p>
          )}
        </>
      ) : (
        <p className="mt-1 text-[11px] text-slate-500">
          No forecast for this story: too little history.
        </p>
      )}
    </div>
  )
}

function Stat({
  term,
  value,
  tone,
}: {
  term: string
  value: string | number
  tone?: 'up' | 'down'
}) {
  return (
    <div className="rounded bg-slate-800/70 px-2 py-1.5">
      <dt className="text-[11px] text-slate-400">{term}</dt>
      <dd
        className={`text-base font-semibold ${tone === 'up' ? 'text-emerald-300' : tone === 'down' ? 'text-rose-300' : ''}`}
      >
        {value}
      </dd>
    </div>
  )
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      aria-pressed={active}
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 ${active ? 'bg-slate-200 text-slate-900' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
    >
      {children}
    </button>
  )
}

/** "Erzurum, Ankara · " for an item's places (the first few), or nothing for an item with none. */
function places(article: Article): string {
  const names = (article.locations ?? []).map((l) => l.name)
  if (names.length === 0) return ''
  const shown = names.slice(0, 3).join(', ')
  return `${shown}${names.length > 3 ? ` +${names.length - 3}` : ''} · `
}
