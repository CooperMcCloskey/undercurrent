// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Globe, { type GlobeMethods } from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { CATEGORY_ORDER, CATEGORY_STYLE, hexToRgb, type Category } from './globe/categories'
import { loadLand } from './globe/countries'
import { createHeatLayer, type HeatPoint } from './globe/heat'
import { buildConstellation, type ConstellationItem } from './globe/constellation'
import {
  KIND_STYLE,
  formatWhen,
  hostOf,
  layoutDots,
  placeLabels,
  reachDeg,
  type DotPlacement,
  type PlaceLabel,
} from './story/articles'
import { StoryPanel } from './story/StoryPanel'
import { useArticles } from './story/useArticles'

// Vector globe: ocean sphere, merged land mesh (globe/land.ts), a heatmap of where stories are
// happening coloured by category (globe/heat.ts), a marker per story, and an outline of the
// hovered story's area. Hovering a marker also bursts out a small dot per article or post (all one
// size, so the story stays the big thing; globe/constellation.ts); clicking it opens the story's articles in a panel
// (story/StoryPanel.tsx).
const COLORS = {
  ocean: '#0b1424',
  land: '#1c2a3f',
  border: '#3d5373',
  atmosphere: '#7dd3fc',
}
// Layers stack outwards, as a fraction of the globe radius.
const LAND_ALTITUDE = 0.005
const HEAT_ALTITUDE = 0.007
const HIGHLIGHT_ALTITUDE = 0.009
const MARKER_ALTITUDE = 0.011
// Minimum spread of every place's heat, in km on the globe. Absolute, so the heat is a fixed
// layer on the Earth: zooming magnifies it and never reshapes it.
const HEAT_SPREAD_KM = 200
// Marker size in degrees of arc; the hovered one grows.
const MARKER_RADIUS = 0.6
const MARKER_RADIUS_HOVERED = 1.1
// While a story is highlighted, the rest of the heatmap dims to this.
const HEAT_OPACITY = 0.85
const HEAT_OPACITY_DIMMED = 0.3
// Selecting a story flies the camera to the story's marker, high enough up (altitude, in globe
// radii) to see all its dots: roughly 1 radius of altitude shows 32 degrees around the marker.
const SELECT_CAMERA_MIN_ALTITUDE = 0.8
const SELECT_CAMERA_MAX_ALTITUDE = 2.2
const SELECT_CAMERA_DEG_PER_ALTITUDE = 32
const SELECT_CAMERA_MS = 900
const NO_DOTS: DotPlacement[] = []
const NO_LABELS: PlaceLabel[] = []
const LABEL_ALTITUDE = 0.012
// Leaving a marker ends its hover this long afterwards, so the pointer can cross the gap from
// the marker to one of its article dots without the dots vanishing.
const HOVER_LEAVE_MS = 200

// PERFORMANCE: everything passed to <Globe> must keep the same identity between renders.
// react-globe.gl re-processes a whole layer whenever a data or accessor prop changes, and inline
// arrow functions or `?? []` are new on every render. So accessors, materials and config live at
// module scope, and data arrays are memoised.
const OCEAN = new MeshLambertMaterial({ color: COLORS.ocean })
const RENDERER_CONFIG = { powerPreference: 'high-performance' } as const // discrete GPU if present
const NO_STORIES: StorySummary[] = []

const colorOf = (s: StorySummary) => CATEGORY_STYLE[s.category].color

interface Marker {
  story: StorySummary
  lat: number
  lng: number
}
const marker = (o: object) => o as Marker
const pointLat = (o: object) => marker(o).lat
const pointLng = (o: object) => marker(o).lng
const pointColor = (o: object) => colorOf(marker(o).story)

/** Stories anchored at the same place (two Rotterdam stories) are fanned out in a small ring. */
function placeMarkers(stories: StorySummary[]): Marker[] {
  const byPlace = new Map<string, StorySummary[]>()
  for (const s of stories) {
    const key = `${s.location.lat.toFixed(2)},${s.location.lon.toFixed(2)}`
    byPlace.set(key, [...(byPlace.get(key) ?? []), s])
  }
  return [...byPlace.values()].flatMap((group) =>
    group.map((story, i) => {
      const angle = (2 * Math.PI * i) / group.length
      const r = group.length > 1 ? 2 * MARKER_RADIUS : 0 // degrees
      const { lat, lon } = story.location
      return {
        story,
        lat: lat + r * Math.sin(angle),
        lng: lon + (r * Math.cos(angle)) / Math.max(Math.cos((lat * Math.PI) / 180), 0.2),
      }
    }),
  )
}

// Tooltips are inserted as HTML, and titles come from scraped headlines: always escape.
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
const pointLabel = (o: object) => {
  const s = marker(o).story
  const { label, color } = CATEGORY_STYLE[s.category]
  const p = s.forecast ? ` · ${Math.round(s.forecast.p_grow * 100)}% chance of growing` : ''
  return `<span style="color:${color}">● ${label}</span><br/><b>${escapeHtml(s.title)}</b><br/>${s.article_count_7d} articles, ${s.post_count_7d} posts this week${p}<br/><i>Click to explore the articles</i>`
}
const dotLabel = (o: object) => {
  const { article, place } = o as ConstellationItem
  if (!article) return ''
  const { label } = KIND_STYLE[article.kind]
  const others = (article.locations ?? []).filter((l) => l.name !== place?.name)
  const where = place
    ? `📍 ${escapeHtml(place.name)}${others.length ? ` (it also mentions ${others.map((l) => escapeHtml(l.name)).join(', ')})` : ''}`
    : 'No known place: shown beside the story marker'
  return `<div style="max-width:260px;white-space:normal"><b>${escapeHtml(article.title)}</b><br/>${label} · ${escapeHtml(hostOf(article))} · ${formatWhen(article.published_at)}<br/>${where}</div>`
}

// Names the places the focused story's dots are at, so it's clear why the heat is where it is.
// These go in globe.gl's "html" layer: text in screen pixels, pinned to the place.
const labelLat = (o: object) => (o as PlaceLabel).lat
const labelLng = (o: object) => (o as PlaceLabel).lng
const labelElement = (o: object) => {
  const { name, mentions } = o as PlaceLabel
  const text = document.createElement('span')
  text.className =
    'absolute left-0 top-2 -translate-x-1/2 whitespace-nowrap rounded bg-slate-950/75 px-1.5 py-0.5 text-[11px] font-medium text-slate-100'
  text.textContent = `${name} · ${mentions} ${mentions === 1 ? 'mention' : 'mentions'}` // not innerHTML
  const el = document.createElement('div') // zero-size anchor; globe.gl positions it
  el.style.pointerEvents = 'none'
  el.append(text)
  return el
}
const layerObject = (d: object) => (d as { object: Object3D }).object
// Required, even though it does nothing: without an update callback, react-globe.gl's custom
// layer empties its scene on every data change and only re-adds *new* items. The layer list
// goes [heat] -> [land, heat] when the land loads, which would silently drop the heatmap.
const keepLayerObject = () => {}
const loadLandLayer = () => loadLand({ altitude: LAND_ALTITUDE, ...COLORS })

/** Each story's heat is its article count, spread over its footprint, in its category colour. */
function heatPoints(stories: StorySummary[]): HeatPoint[] {
  return stories.flatMap((s) => {
    const color = hexToRgb(colorOf(s))
    const footprint = s.footprint.length
      ? s.footprint
      : [{ lat: s.location.lat, lon: s.location.lon, weight: 1, radius_km: 150 }]
    return footprint.map((p) => ({
      lat: p.lat,
      lon: p.lon,
      weight: s.article_count_7d * p.weight,
      radiusKm: p.radius_km,
      color,
    }))
  })
}

export default function App() {
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const day = meta.data?.latest
  const snapshot = useQuery({
    queryKey: ['snapshot', day],
    queryFn: () => api.snapshot(day!),
    enabled: !!day,
  })
  const land = useQuery({
    queryKey: ['land'],
    queryFn: loadLandLayer,
    staleTime: Infinity,
    structuralSharing: false,
  })
  const stories = snapshot.data?.stories ?? NO_STORIES

  // Category filter: categories in `hidden` are left off the globe.
  const [hidden, setHidden] = useState<ReadonlySet<Category>>(() => new Set())
  const toggleCategory = (category: Category) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (!next.delete(category)) next.add(category)
      return next
    })
  const onlyCategory = (category: Category) =>
    setHidden(new Set(CATEGORY_ORDER.filter((c) => c !== category)))
  const visible = useMemo(() => stories.filter((s) => !hidden.has(s.category)), [stories, hidden])

  // Hovered story (from a marker or the legend) and selected story (clicked). The one in focus
  // (the hovered one, else the selected one) has its area outlined and the rest of the heat dims,
  // and gets a dot per article around its marker.
  const [hovered, setHovered] = useState<StorySummary | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = useMemo(
    () => visible.find((s) => s.id === selectedId) ?? null,
    [visible, selectedId],
  )
  const focus = hovered ?? selected
  const active = focus && !hidden.has(focus.category) ? focus : null
  const leaveTimer = useRef<number | undefined>(undefined)
  const hoverStory = useCallback((story: StorySummary | null) => {
    window.clearTimeout(leaveTimer.current)
    if (story) setHovered(story)
    else leaveTimer.current = window.setTimeout(() => setHovered(null), HOVER_LEAVE_MS)
  }, [])
  useEffect(() => () => window.clearTimeout(leaveTimer.current), [])
  const onPointHover = useCallback(
    (o: object | null) => hoverStory(o ? marker(o).story : null),
    [hoverStory],
  )
  const pointRadius = useCallback(
    (o: object) => (marker(o).story.id === active?.id ? MARKER_RADIUS_HOVERED : MARKER_RADIUS),
    [active],
  )

  const markers = useMemo(() => placeMarkers(visible), [visible])

  // Selecting a story: fly to it and open its panel.
  const globeRef = useRef<GlobeMethods | undefined>(undefined)
  const reachRef = useRef<{ storyId: string; deg: number } | null>(null) // how far the dots spread
  const [pickedId, setPickedId] = useState<string | null>(null) // article dot clicked on the globe
  const [highlightedId, setHighlightedId] = useState<string | null>(null) // article dot/row hovered
  const selectStory = useCallback((m: Marker) => {
    setSelectedId(m.story.id)
    setPickedId(null)
    const globe = globeRef.current
    if (globe) {
      // Frame the dots if they're loaded (they are once the story has been hovered).
      const reach = reachRef.current?.storyId === m.story.id ? reachRef.current.deg : 0
      const altitude = Math.min(
        SELECT_CAMERA_MAX_ALTITUDE,
        Math.max(SELECT_CAMERA_MIN_ALTITUDE, reach / SELECT_CAMERA_DEG_PER_ALTITUDE),
      )
      globe.pointOfView({ lat: m.lat, lng: m.lng, altitude }, SELECT_CAMERA_MS)
    }
  }, [])
  const deselect = useCallback(() => {
    setSelectedId(null)
    setPickedId(null)
    setHighlightedId(null)
  }, [])
  const onPointClick = useCallback((o: object) => selectStory(marker(o)), [selectStory])
  useEffect(() => {
    if (!selectedId) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && deselect()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, deselect])

  // The articles of the story in focus, as dots on the globe at their own places. Fetched on
  // first hover, then cached.
  const focusArticles = useArticles(active?.id)
  const anchor = useMemo(
    () => (active ? markers.find((m) => m.story.id === active.id) : undefined),
    [active, markers],
  )
  const dots = useMemo(
    () => (anchor ? layoutDots(focusArticles.articles, anchor, MARKER_RADIUS_HOVERED) : NO_DOTS),
    [anchor, focusArticles.articles],
  )
  const constellation = useMemo(
    () =>
      anchor && active && dots.length
        ? buildConstellation(anchor, dots, colorOf(active), active)
        : null,
    [anchor, active, dots],
  )
  const labels = useMemo(
    () => (constellation ? placeLabels(dots) : NO_LABELS),
    [constellation, dots],
  )
  useEffect(() => {
    reachRef.current =
      anchor && active && dots.length ? { storyId: active.id, deg: reachDeg(dots, anchor) } : null
  }, [anchor, active, dots])
  useEffect(() => () => constellation?.dispose(), [constellation])
  useEffect(() => {
    constellation?.setLit([highlightedId, pickedId])
  }, [constellation, highlightedId, pickedId])
  // Hovering a dot keeps its story in focus and lights the article's row in the panel.
  const onDotHover = useCallback(
    (o: object | null) => {
      const item = o as ConstellationItem | null
      hoverStory(item?.story ?? null)
      setHighlightedId(item?.article?.id ?? null)
    },
    [hoverStory],
  )
  // Clicking a dot selects its story (if it isn't already) and finds the article in the panel.
  const onDotClick = useCallback(
    (o: object) => {
      const { story, article } = o as ConstellationItem
      if (!story || !article) return
      const m = markers.find((m) => m.story.id === story.id)
      if (m && story.id !== selectedId) selectStory(m)
      setPickedId(article.id)
    },
    [markers, selectedId, selectStory],
  )
  const selectedArticles = useArticles(selected?.id)
  // Legend: every category present today (hidden ones greyed out), with its biggest stories.
  const legend = useMemo(
    () =>
      CATEGORY_ORDER.map((category) => ({
        category,
        stories: stories
          .filter((s) => s.category === category)
          .sort((a, b) => b.article_count_7d - a.article_count_7d),
      })).filter((group) => group.stories.length > 0),
    [stories],
  )

  // One heat layer and one highlight layer for the app's lifetime; only their textures change.
  const heat = useMemo(() => {
    const layer = createHeatLayer({ altitude: HEAT_ALTITUDE, opacity: HEAT_OPACITY })
    return { layer, item: { object: layer.object } }
  }, [])
  const highlight = useMemo(() => {
    const layer = createHeatLayer({ altitude: HIGHLIGHT_ALTITUDE, opacity: 1, style: 'outline' })
    return { layer, item: { object: layer.object } }
  }, [])
  useEffect(() => {
    heat.layer.update(heatPoints(visible), HEAT_SPREAD_KM)
  }, [heat, visible])
  useEffect(() => {
    highlight.layer.update(active ? heatPoints([active]) : [], HEAT_SPREAD_KM)
    heat.layer.setOpacity(active ? HEAT_OPACITY_DIMMED : HEAT_OPACITY)
  }, [heat, highlight, active])

  const landItem = useMemo(() => land.data && { object: land.data }, [land.data])
  const layers = useMemo(
    () => [
      ...(landItem ? [landItem] : []),
      heat.item,
      highlight.item,
      ...(constellation?.items ?? []),
    ],
    [landItem, heat, highlight, constellation],
  )

  const error = meta.error ?? snapshot.error ?? land.error

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <Globe
        ref={globeRef}
        rendererConfig={RENDERER_CONFIG}
        backgroundColor="rgba(0,0,0,0)"
        globeMaterial={OCEAN}
        atmosphereColor={COLORS.atmosphere}
        atmosphereAltitude={0.18}
        customLayerData={layers}
        customThreeObject={layerObject}
        customThreeObjectUpdate={keepLayerObject}
        // The only hoverable/clickable custom objects are the article dots.
        customLayerLabel={dotLabel}
        onCustomLayerHover={onDotHover}
        onCustomLayerClick={onDotClick}
        // Flat markers just above the heat. With 1,000+ stories set pointsMerge (one draw call,
        // but no per-point hover).
        pointsData={markers}
        pointLat={pointLat}
        pointLng={pointLng}
        pointAltitude={MARKER_ALTITUDE}
        pointRadius={pointRadius}
        pointColor={pointColor}
        pointLabel={pointLabel}
        onPointHover={onPointHover}
        onPointClick={onPointClick}
        onGlobeClick={deselect}
        htmlElementsData={labels}
        htmlLat={labelLat}
        htmlLng={labelLng}
        htmlAltitude={LABEL_ALTITUDE}
        htmlElement={labelElement}
        htmlTransitionDuration={0}
      />
      {selected && (
        <StoryPanel
          key={selected.id}
          story={selected}
          summary={selectedArticles.summary}
          articles={selectedArticles.articles}
          status={selectedArticles.status}
          error={selectedArticles.error}
          onRetry={selectedArticles.refetch}
          highlightedId={highlightedId}
          pickedId={pickedId}
          onHighlight={setHighlightedId}
          onClose={deselect}
        />
      )}
      <header className="pointer-events-none absolute left-6 top-5">
        <h1 className="text-2xl font-semibold tracking-tight">Undercurrent</h1>
        <p className="text-sm text-slate-400">
          {error
            ? `Error: ${error.message}`
            : day
              ? `${visible.length === stories.length ? stories.length : `${visible.length} of ${stories.length}`} stories as of ${day}`
              : 'Loading…'}
        </p>
      </header>
      {legend.length > 0 && (
        <aside className="absolute bottom-5 left-6 max-h-[70vh] w-80 overflow-y-auto rounded-lg bg-slate-900/80 p-3 text-xs backdrop-blur">
          <div className="mb-2 flex items-center justify-between text-slate-400">
            <span>Click a category to hide or show it</span>
            {hidden.size > 0 && (
              <button
                className="rounded px-1.5 py-0.5 text-slate-200 hover:bg-slate-700"
                onClick={() => setHidden(new Set())}
              >
                Show all
              </button>
            )}
          </div>
          <p className="mb-2 text-slate-400">
            Big dots are stories. Hover one to see its articles (small circles) and posts (small
            diamonds).
          </p>
          <div className="space-y-2">
            {legend.map(({ category, stories: group }) => {
              const off = hidden.has(category)
              const { label, color } = CATEGORY_STYLE[category]
              return (
                <section key={category} className={off ? 'opacity-40' : undefined}>
                  <div className="group flex items-center gap-2">
                    <button
                      className="flex min-w-0 flex-1 items-center gap-2 rounded py-0.5 text-left font-medium hover:bg-slate-800"
                      aria-pressed={!off}
                      onClick={() => toggleCategory(category)}
                    >
                      <span
                        className="size-2.5 shrink-0 rounded-full border-2"
                        style={{ borderColor: color, background: off ? 'transparent' : color }}
                      />
                      <span className="truncate">{label}</span>
                      <span className="ml-auto font-normal text-slate-400">{group.length}</span>
                    </button>
                    <button
                      className="invisible rounded px-1.5 py-0.5 text-slate-400 hover:bg-slate-700 hover:text-slate-100 group-hover:visible"
                      onClick={() => onlyCategory(category)}
                    >
                      only
                    </button>
                  </div>
                  {!off && (
                    <ul className="ml-[18px] text-slate-400">
                      {group.slice(0, 3).map((s) => (
                        <li
                          key={s.id}
                          className="cursor-pointer truncate rounded px-1 hover:bg-slate-800 hover:text-slate-100"
                          onMouseEnter={() => hoverStory(s)}
                          onMouseLeave={() => hoverStory(null)}
                          onClick={() => {
                            const m = markers.find((m) => m.story.id === s.id)
                            if (m) selectStory(m)
                          }}
                        >
                          {s.title}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              )
            })}
          </div>
        </aside>
      )}
    </div>
  )
}
