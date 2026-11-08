// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import Globe, { type GlobeMethods } from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D, type PerspectiveCamera } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { CATEGORY_ORDER, CATEGORY_STYLE, hexToRgb, type Category } from './globe/categories'
import { Legend } from './globe/Legend'
import { loadLand } from './globe/countries'
import { createGlowLayer, type GlowPoint } from './globe/glow'
import { createHeatLayer, type HeatPoint } from './globe/heat'
import { buildConstellation, type ConstellationItem } from './globe/constellation'
import {
  KIND_STYLE,
  formatWhen,
  hostOf,
  layoutDots,
  pickLabels,
  placeLabels,
  reachDeg,
  shortPlaceName,
  type DotPlacement,
  type PlaceLabel,
} from './story/articles'
import { EmergingList } from './story/Emerging'
import {
  describeForecastShort,
  emergingStories,
  projectStory,
  trustedForecast,
} from './story/forecast'
import { ModelCard } from './story/ModelCard'
import { StoryPanel } from './story/StoryPanel'
import { useArticles } from './story/useArticles'
import { formatLong, toDay, toIndex, yearsBefore, type DayIndex } from './timeline/days'
import { historySnapshot } from './timeline/history'
import { HISTORY_YEARS } from './timeline/source'
import { TimeBar } from './timeline/TimeBar'
import { useEasedStories, type EasedStory } from './timeline/useEasedStories'

// Vector globe: ocean sphere, merged land mesh (globe/land.ts), a glowing marker per story, coloured by category
// (globe/glow.ts), and an outline (globe/heat.ts) of the
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
const HIGHLIGHT_ALTITUDE = 0.009
const MARKER_ALTITUDE = 0.011
// How wide the hovered story's outlined area is around each of its places, in km on the globe.
// Absolute, so the area is fixed on the Earth: zooming magnifies it and never reshapes it.
const OUTLINE_SPREAD_KM = 120
// Each story's marker has a soft glow behind it, like a star: diameter in degrees of arc, from the
// smallest story to the biggest, and how bright it is (dimmed while one story is highlighted).
const GLOW_DIAMETER_MIN_DEG = 2.2
const GLOW_DIAMETER_MAX_DEG = 6
const GLOW_OPACITY = 0.55
const GLOW_OPACITY_DIMMED = 0.15
// Marker size in degrees of arc; the hovered one grows.
const MARKER_RADIUS = 0.9 // the biggest story's; smaller stories shrink to half of it
// Only the biggest stories are drawn, so the globe reads as a handful of main stories instead of
// 300 overlapping blobs. Stories projected to grow, and the one selected, are always drawn.
const MAX_STORIES = 60
const MARKER_RADIUS_HOVERED = 1.1
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
// Depth precision. globe.gl's camera has a fixed near plane of 0.05, so zoomed out the depth
// buffer can't separate the land (0.5 units above the sea) from the ocean, and they flicker into
// each other. Instead the near plane follows the camera: half its distance to the outermost layer
// (the atmosphere, ~1.18 radii), so nothing visible is ever clipped.
const NEAR_CLEARANCE_RADIUS = 120 // globe radius is 100
const MIN_NEAR = 0.05

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
  /** Size, 0..1, eased: the marker grows in as its story arrives and shrinks as it fades. */
  level: number
  /** 0.5..1: how big the story is next to the biggest one drawn. */
  size: number
}
const marker = (o: object) => o as Marker
const pointLat = (o: object) => marker(o).lat
const pointLng = (o: object) => marker(o).lng
const pointColor = (o: object) => colorOf(marker(o).story)
// Rings pulse out from stories projected to grow (the same rule as the Emerging list).
const RING_ALTITUDE = 0.013
const ringColor = (o: object) => {
  const [r, g, b] = hexToRgb(colorOf(marker(o).story)).map((v) => Math.round(v * 255))
  return (t: number) => `rgba(${r},${g},${b},${(1 - t) * 0.8})`
}
const NO_MARKERS: Marker[] = []
const RING_SPACING_DEG = 2.2 // between stories at one place: two markers' radii, and a gap
// Markers smaller than this aren't drawn.
const MIN_MARKER_LEVEL = 0.02

/**
 * One marker per story, sized by how many articles it has. Stories anchored at the same place
 * (Moscow has many) are laid out around the biggest, which keeps the place: the rest go on rings
 * round it, far enough out not to touch. Each story keeps its marker object (`cache`) from frame
 * to frame, so the globe updates the marker in place while it grows or fades.
 */
function placeMarkers(eased: readonly EasedStory[], cache: Map<string, Marker>): Marker[] {
  const biggest = eased.reduce((m, e) => Math.max(m, e.count), 1)
  const byPlace = new Map<string, EasedStory[]>()
  for (const e of eased) {
    if (e.level < MIN_MARKER_LEVEL) continue
    const { lat, lon } = e.story.location
    const key = `${Math.round(lat * 2)},${Math.round(lon * 2)}` // half-degree cells
    byPlace.set(key, [...(byPlace.get(key) ?? []), e])
  }
  const markers = [...byPlace.values()].flatMap((group) => {
    group.sort((a, b) => b.count - a.count || a.story.id.localeCompare(b.story.id))
    let ring = 1 // rings hold 6, 12, 18... markers
    let slot = 0
    return group.map(({ story, level, count }, i) => {
      let r = 0
      let angle = 0
      if (i > 0) {
        if (slot === 6 * ring) {
          ring++
          slot = 0
        }
        r = RING_SPACING_DEG * ring
        angle = (2 * Math.PI * (slot + (ring % 2) / 2)) / (6 * ring)
        slot++
      }
      const { lat, lon } = story.location
      const m = cache.get(story.id) ?? ({} as Marker)
      return Object.assign(m, {
        story,
        level,
        size: 0.5 + 0.5 * Math.sqrt(count / biggest),
        lat: lat + r * Math.sin(angle),
        lng: lon + (r * Math.cos(angle)) / Math.max(Math.cos((lat * Math.PI) / 180), 0.2),
      })
    })
  })
  cache.clear()
  for (const m of markers) cache.set(m.story.id, m)
  return markers
}

// Tooltips are inserted as HTML, and titles come from scraped headlines: always escape.
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
const pointLabel = (o: object) => {
  const s = marker(o).story
  const { label, color } = CATEGORY_STYLE[s.category]
  const p = s.forecast ? ` · ${describeForecastShort(s.forecast)}` : ''
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

// Names the places the focused story's dots are at, so it's clear where the story is happening.
// These go in globe.gl's "html" layer: text in screen pixels, pinned to the place.
const labelLat = (o: object) => (o as PlaceLabel).lat
const labelLng = (o: object) => (o as PlaceLabel).lng
const labelElement = (o: object) => {
  const { name, mentions } = o as PlaceLabel
  const text = document.createElement('span')
  text.className =
    'absolute left-0 top-2 -translate-x-1/2 whitespace-nowrap rounded bg-slate-950/70 px-1 py-px text-[10px] font-medium text-slate-200'
  // Short name, and how many articles mention it when it's more than one. Not innerHTML.
  text.textContent = mentions > 1 ? `${shortPlaceName(name)} · ${mentions}` : shortPlaceName(name)
  const el = document.createElement('div') // zero-size anchor; globe.gl positions it
  el.style.pointerEvents = 'none'
  el.append(text)
  return el
}
const layerObject = (d: object) => (d as { object: Object3D }).object
// Required, even though it does nothing: without an update callback, react-globe.gl's custom
// layer empties its scene on every data change and only re-adds *new* items. The layer list
// goes [highlight] -> [land, highlight] when the land loads, which would silently drop it.
const keepLayerObject = () => {}
const loadLandLayer = () => loadLand({ altitude: LAND_ALTITUDE, ...COLORS })

/** The places a story's area is outlined at when it is hovered, one point per footprint place. */
function outlinePoints(story: StorySummary): HeatPoint[] {
  const color = hexToRgb(colorOf(story))
  const footprint = story.footprint.length
    ? story.footprint
    : [{ lat: story.location.lat, lon: story.location.lon, weight: 1, radius_km: 150 }]
  return footprint.map((p) => ({
    lat: p.lat,
    lon: p.lon,
    weight: story.article_count_7d * p.weight,
    radiusKm: p.radius_km,
    color,
  }))
}

export default function App() {
  const meta = useQuery({
    queryKey: ['meta'],
    queryFn: api.meta,
    // Until the API answers (it may still be starting), keep asking, so the page loads by itself
    // once it's up instead of staying on the error.
    refetchInterval: (query) => (query.state.status === 'error' ? 3000 : false),
  })
  // The day on the time bar (null = the latest). Days the pipeline exported are real; the rest
  // of the range comes from the illustrative archive (timeline/history.ts).
  const latest = meta.data ? toIndex(meta.data.latest) : null
  const realDays = useMemo(() => new Set((meta.data?.dates ?? []).map(toIndex)), [meta.data])
  // The bar continues past the latest day by the forecast horizons: there, the latest snapshot is
  // drawn with each story at its forecast size.
  const reach = meta.data?.horizons.length ? Math.max(...meta.data.horizons) : 0
  const [pickedDay, setPickedDay] = useState<DayIndex | null>(null)
  const barDay = pickedDay ?? latest
  // Computing a day is cheap, so the globe follows the bar straight away; useEasedStories then
  // smooths the change on screen.
  const shownDay = barDay
  const ahead = shownDay !== null && latest !== null ? Math.max(0, shownDay - latest) : 0
  const isRealDay = shownDay !== null && (realDays.has(shownDay) || ahead > 0)
  // The day whose snapshot is read: the bar's day, or the latest one when looking ahead.
  const dataDay = ahead > 0 ? latest : shownDay
  const day = dataDay === null ? undefined : toDay(dataDay)
  const snapshot = useQuery({
    queryKey: ['snapshot', day],
    queryFn: () => api.snapshot(day!),
    enabled: !!day && isRealDay,
    placeholderData: keepPreviousData,
  })
  const archive = useMemo(
    () => (shownDay !== null && !isRealDay ? historySnapshot(shownDay) : null),
    [shownDay, isRealDay],
  )
  const land = useQuery({
    queryKey: ['land'],
    queryFn: loadLandLayer,
    staleTime: Infinity,
    structuralSharing: false,
  })
  const model = meta.data?.model ?? null
  // The day's stories as read, keeping only the forecast horizons the model beat the baseline at.
  const dayStories = useMemo(() => {
    const raw = (isRealDay ? snapshot.data?.stories : archive?.snapshot.stories) ?? NO_STORIES
    return raw.some((s) => s.forecast)
      ? raw.map((s) => (s.forecast ? { ...s, forecast: trustedForecast(s.forecast, model) } : s))
      : raw
  }, [isRealDay, snapshot.data, archive, model])
  // Looking ahead: the same stories at their forecast size.
  const stories = useMemo(
    () => (ahead > 0 ? dayStories.map((s) => projectStory(s, ahead)) : dayStories),
    [dayStories, ahead],
  )
  const emerging = useMemo(() => emergingStories(dayStories), [dayStories])
  // How present each story is (0..1) on this day: archive stories fade in and out around their
  // threshold; pipeline stories are simply there.
  const presenceOf = useCallback(
    (s: StorySummary) => (isRealDay ? 1 : (archive?.presence.get(s.id) ?? 1)),
    [isRealDay, archive],
  )

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
  // (the hovered one, else the selected one) has its area outlined and the rest of the glows dim,
  // and gets a dot per article around its marker.
  const [hovered, setHovered] = useState<StorySummary | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = useMemo(
    () => visible.find((s) => s.id === selectedId) ?? null,
    [visible, selectedId],
  )
  const [showAll, setShowAll] = useState(false)
  const shown = useMemo(() => {
    if (showAll || visible.length <= MAX_STORIES) return visible
    const keep = new Set(
      [...visible]
        .sort((a, b) => b.article_count_7d - a.article_count_7d)
        .slice(0, MAX_STORIES)
        .map((s) => s.id),
    )
    for (const e of emerging) keep.add(e.story.id)
    if (selectedId) keep.add(selectedId)
    return visible.filter((s) => keep.has(s.id))
  }, [visible, showAll, emerging, selectedId])
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
    (o: object) =>
      (marker(o).story.id === active?.id ? MARKER_RADIUS_HOVERED : MARKER_RADIUS * marker(o).size) *
      marker(o).level,
    [active],
  )

  // What's drawn: the visible stories, with sizes and glows easing toward the day's values, so
  // stories grow in and fade out as the time bar moves instead of popping.
  const eased = useEasedStories(shown, presenceOf)
  const markerCache = useRef(new Map<string, Marker>())
  const markers = useMemo(() => placeMarkers(eased, markerCache.current), [eased])
  const glow = useMemo(() => createGlowLayer(), [])

  // Selecting a story: fly to it and open its panel.
  const globeRef = useRef<GlobeMethods | undefined>(undefined)
  // Runs before every frame, so it also covers pointOfView fly-tos, not just mouse zooming.
  useEffect(() => {
    const globe = globeRef.current
    if (!globe) return
    const scene = globe.scene()
    const camera = globe.camera() as PerspectiveCamera
    scene.onBeforeRender = (renderer) => {
      glow.setViewport(renderer, camera.fov)
      const near = Math.max(MIN_NEAR, (camera.position.length() - NEAR_CLEARANCE_RADIUS) / 2)
      if (Math.abs(near - camera.near) > near * 0.01) {
        camera.near = near
        camera.updateProjectionMatrix()
      }
    }
    return () => {
      scene.onBeforeRender = () => {}
    }
  }, [glow])
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
    () => (constellation ? pickLabels(placeLabels(dots)) : NO_LABELS),
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
  // Legend: every category present today, with how many stories.
  const legend = useMemo(
    () =>
      CATEGORY_ORDER.map((category) => ({
        category,
        count: stories.filter((s) => s.category === category).length,
      })).filter((group) => group.count > 0),
    [stories],
  )
  // Emerging stories: a list on the left, and rings on the globe.
  const [showEmerging, setShowEmerging] = useState(false)
  const surgeIds = useMemo(() => new Set(emerging.map((e) => e.story.id)), [emerging])
  const rings = useMemo(
    () => (ahead > 0 ? NO_MARKERS : markers.filter((m) => surgeIds.has(m.story.id))),
    [markers, surgeIds, ahead],
  )
  const selectById = useCallback(
    (id: string) => {
      const m = markers.find((m) => m.story.id === id)
      if (m) selectStory(m)
    },
    [markers, selectStory],
  )
  const hoverById = useCallback(
    (id: string | null) => hoverStory(id ? (stories.find((s) => s.id === id) ?? null) : null),
    [stories, hoverStory],
  )

  // One outline layer and one glow layer for the app's lifetime; only their contents change.
  const highlight = useMemo(() => {
    const layer = createHeatLayer({ altitude: HIGHLIGHT_ALTITUDE, opacity: 1, style: 'outline' })
    return { layer, item: { object: layer.object } }
  }, [])
  const glowItem = useMemo(() => ({ object: glow.object }), [glow])
  useEffect(() => {
    const points: GlowPoint[] = markers.map((m) => ({
      lat: m.lat,
      lng: m.lng,
      diameterDeg:
        GLOW_DIAMETER_MIN_DEG +
        (GLOW_DIAMETER_MAX_DEG - GLOW_DIAMETER_MIN_DEG) * Math.min(1, (m.size - 0.5) * 2),
      color: hexToRgb(colorOf(m.story)),
      strength: m.level,
    }))
    glow.update(points, MARKER_ALTITUDE)
  }, [glow, markers])
  useEffect(() => {
    highlight.layer.update(active ? outlinePoints(active) : [], OUTLINE_SPREAD_KM)
    glow.setOpacity(active ? GLOW_OPACITY_DIMMED : GLOW_OPACITY)
  }, [glow, highlight, active])

  const landItem = useMemo(() => land.data && { object: land.data }, [land.data])
  const layers = useMemo(
    () => [
      ...(landItem ? [landItem] : []),
      highlight.item,
      glowItem,
      ...(constellation?.items ?? []),
    ],
    [landItem, highlight, glowItem, constellation],
  )

  const error = meta.error ?? (isRealDay ? snapshot.error : null) ?? land.error

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
        // Flat markers. With 1,000+ stories set pointsMerge (one draw call,
        // but no per-point hover).
        pointsData={markers}
        pointLat={pointLat}
        pointLng={pointLng}
        pointAltitude={MARKER_ALTITUDE}
        pointRadius={pointRadius}
        pointColor={pointColor}
        // Markers change on every step of the time bar: no grow-in animation, or they flicker.
        pointsTransitionDuration={0}
        pointLabel={pointLabel}
        onPointHover={onPointHover}
        onPointClick={onPointClick}
        onGlobeClick={deselect}
        ringsData={rings}
        ringLat={pointLat}
        ringLng={pointLng}
        ringColor={ringColor}
        ringAltitude={RING_ALTITUDE}
        ringMaxRadius={3}
        ringPropagationSpeed={1.2}
        ringRepeatPeriod={2400}
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
          // The forecast graph is drawn from the day's own numbers, even when looking ahead.
          story={dayStories.find((s) => s.id === selected.id) ?? selected}
          asOf={day ?? ''}
          timeline={selectedArticles.timeline}
          entities={selectedArticles.entities}
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
      {latest !== null && barDay !== null && (
        <TimeBar
          start={yearsBefore(latest, HISTORY_YEARS)}
          end={latest + reach}
          futureFrom={reach > 0 ? latest : undefined}
          value={barDay}
          onChange={setPickedDay}
          // Full width along the bottom; the legend and the story panel sit above it.
          className="absolute bottom-4 left-6 right-6"
        />
      )}
      <header className="pointer-events-none absolute left-6 top-5">
        <h1 className="text-2xl font-semibold tracking-tight">Undercurrent</h1>
        {error ? (
          <p className="text-sm text-slate-400">Error: {error.message}</p>
        ) : barDay !== null ? (
          <>
            {/* The bar's day, straight away; the stories catch up a moment later. */}
            <p className="mt-1 text-lg font-medium tabular-nums text-slate-100">
              {formatLong(barDay)}
              {ahead > 0 && (
                <span className="ml-2 rounded bg-amber-300/20 px-1.5 py-0.5 align-middle text-xs font-normal text-amber-200">
                  forecast, {ahead} {ahead === 1 ? 'day' : 'days'} ahead
                </span>
              )}
            </p>
            <p className="text-sm text-slate-400">
              {shown.length === stories.length
                ? stories.length
                : `${shown.length} of ${stories.length}`}{' '}
              {stories.length === 1 ? 'story' : 'stories'}
              {!isRealDay && (
                <span
                  className="text-amber-300/80"
                  title="Real events with made-up coverage numbers, until the backfill reaches this date"
                >
                  {' '}
                  · illustrative archive
                </span>
              )}
            </p>
            <div className="pointer-events-auto mt-2 flex items-start gap-2">
              {visible.length > MAX_STORIES && (
                <button
                  className="rounded bg-slate-800/80 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-700"
                  title={`The globe draws the ${MAX_STORIES} biggest stories (and any projected to grow)`}
                  onClick={() => setShowAll(!showAll)}
                >
                  {showAll ? `Only the ${MAX_STORIES} biggest` : 'Show all stories'}
                </button>
              )}
              {emerging.length > 0 || showEmerging ? (
                <button
                  className={`rounded px-2 py-0.5 text-xs ${showEmerging ? 'bg-slate-200 text-slate-900' : 'bg-slate-800/80 text-slate-300 hover:bg-slate-700'}`}
                  aria-pressed={showEmerging}
                  onClick={() => setShowEmerging(!showEmerging)}
                >
                  Emerging stories {emerging.length}
                </button>
              ) : null}
              {model && <ModelCard model={model} />}
            </div>
          </>
        ) : (
          <p className="text-sm text-slate-400">Loading…</p>
        )}
      </header>
      {showEmerging && (
        <EmergingList
          rows={emerging}
          selectedId={selectedId}
          onSelect={selectById}
          onHover={hoverById}
          onClose={() => setShowEmerging(false)}
          className="absolute bottom-16 left-6 top-40 z-10 w-80"
        />
      )}
      {legend.length > 0 && !showEmerging && (
        // Sits on top of the time bar.
        <Legend
          groups={legend}
          hidden={hidden}
          onToggle={toggleCategory}
          onOnly={onlyCategory}
          onShowAll={() => setHidden(new Set())}
          className="absolute bottom-16 left-6"
        />
      )}
    </div>
  )
}
