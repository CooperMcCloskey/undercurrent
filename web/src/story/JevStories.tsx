import type { StorySummary } from '../api/types.gen'
import { useMemo, useState } from 'react'
import { CATEGORY_STYLE } from '../globe/categories'

export const EMERGENCE_LABELS = {
  emerging_popular: 'Emerging popular story',
  maybe_emerging_popular: 'Maybe emerging popular',
  not_emerging_popular: 'Not an emerging popular story',
}

export function isEmerging(story: StorySummary) {
  return !!story.emergence && story.emergence.label !== 'not_emerging_popular'
}

export function JevStories({
  stories,
  total,
  loading,
  error,
  selectedId,
  onSelect,
  onHover,
}: {
  stories: StorySummary[]
  total: number
  loading: boolean
  error: Error | null
  selectedId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}) {
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(100)
  const matches = useMemo(() => {
    const text = query.trim().toLocaleLowerCase()
    return text
      ? stories.filter((story) =>
          `${story.title} ${story.location.name} ${story.category}`
            .toLocaleLowerCase()
            .includes(text),
        )
      : stories
  }, [stories, query])
  return (
    <aside className="absolute left-6 top-44 z-10 max-h-[calc(100vh-26rem)] w-80 overflow-y-auto rounded-lg bg-slate-900/95 p-3 shadow-xl">
      <h2 className="font-semibold">Emerging news &amp; conflict</h2>
      <p className="mt-1 text-xs text-slate-400">
        Jev assessments from available coverage. Signals of future attention are uncertain.
      </p>
      <p className="mt-2 text-xs text-slate-300">
        {stories.length.toLocaleString()} emerging · {total.toLocaleString()} classified
      </p>
      <input
        type="search"
        aria-label="Search emerging story list"
        placeholder="Search stories or places"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setLimit(100)
        }}
        className="mt-2 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-sky-400"
      />
      {loading ? (
        <p className="mt-3 text-sm">Loading stories…</p>
      ) : error ? (
        <p className="mt-3 text-sm text-amber-200">Could not load stories: {error.message}</p>
      ) : total === 0 ? (
        <p className="mt-3 text-sm text-slate-300">No Jev classifications for this day yet.</p>
      ) : matches.length === 0 ? (
        <p className="mt-3 text-sm text-slate-300">
          No emerging stories match the current filters.
        </p>
      ) : null}
      <ul className="mt-3 divide-y divide-slate-800">
        {matches.slice(0, limit).map((story) => (
          <li
            key={story.id}
            onMouseEnter={() => onHover(story.id)}
            onMouseLeave={() => onHover(null)}
          >
            <button
              onClick={() => onSelect(story.id)}
              aria-pressed={selectedId === story.id}
              className={`w-full rounded p-2 text-left hover:bg-slate-800 ${selectedId === story.id ? 'bg-slate-800' : ''}`}
            >
              <p className="text-xs" style={{ color: CATEGORY_STYLE[story.category].color }}>
                {CATEGORY_STYLE[story.category].label} · {story.location.name}
              </p>
              <p className="mt-1 text-sm font-medium">{story.title}</p>
              {story.emergence && (
                <p className="mt-1 text-xs text-sky-200">
                  {EMERGENCE_LABELS[story.emergence.label]} ·{' '}
                  {Math.round(story.emergence.confidence * 100)}% confidence
                </p>
              )}
            </button>
          </li>
        ))}
      </ul>
      {matches.length > limit && (
        <button
          onClick={() => setLimit((n) => n + 100)}
          className="mt-2 w-full rounded bg-slate-800 px-2 py-2 text-xs hover:bg-slate-700"
        >
          Show 100 more ({limit.toLocaleString()} of {matches.length.toLocaleString()})
        </button>
      )}
    </aside>
  )
}
