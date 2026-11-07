// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import Globe from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { CATEGORY_ORDER, CATEGORY_STYLE, hexToRgb } from './globe/categories'
import { loadLand } from './globe/countries'
import { createHeatLayer, type HeatPoint } from './globe/heat'

// Vector globe: ocean sphere, merged land mesh (globe/land.ts), a heatmap of where stories are
// happening coloured by category (globe/heat.ts), and a small flat marker per story.
const COLORS = {
  ocean: '#0b1424',
  land: '#1c2a3f',
  border: '#3d5373',
  atmosphere: '#7dd3fc',
}
// Layers stack outwards, as a fraction of the globe radius.
const LAND_ALTITUDE = 0.005
const HEAT_ALTITUDE = 0.007
const MARKER_ALTITUDE = 0.011
// Minimum spread of every place's heat, in km on the globe. Absolute, so the heat is a fixed
// layer on the Earth: zooming magnifies it and never reshapes it.
const HEAT_SPREAD_KM = 200

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
      const r = group.length > 1 ? 0.35 : 0 // degrees
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
  return `<span style="color:${color}">● ${label}</span><br/><b>${escapeHtml(s.title)}</b><br/>${s.article_count_7d} articles, ${s.post_count_7d} posts this week${p}`
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

  const markers = useMemo(() => placeMarkers(stories), [stories])
  // Legend: categories present today, each with its biggest stories.
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

  // One heat layer for the app's lifetime; only its texture changes when the stories do.
  const heat = useMemo(() => {
    const layer = createHeatLayer({ altitude: HEAT_ALTITUDE })
    return { layer, item: { object: layer.object } }
  }, [])
  useEffect(() => {
    heat.layer.update(heatPoints(stories), HEAT_SPREAD_KM)
  }, [heat, stories])

  const landItem = useMemo(() => land.data && { object: land.data }, [land.data])
  const layers = useMemo(() => (landItem ? [landItem, heat.item] : [heat.item]), [landItem, heat])

  const error = meta.error ?? snapshot.error ?? land.error

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <Globe
        rendererConfig={RENDERER_CONFIG}
        backgroundColor="rgba(0,0,0,0)"
        globeMaterial={OCEAN}
        atmosphereColor={COLORS.atmosphere}
        atmosphereAltitude={0.18}
        customLayerData={layers}
        customThreeObject={layerObject}
        customThreeObjectUpdate={keepLayerObject}
        // Small flat markers, just above the heat, for hover. With 1,000+ stories set
        // pointsMerge (one draw call, but no per-point hover).
        pointsData={markers}
        pointLat={pointLat}
        pointLng={pointLng}
        pointAltitude={MARKER_ALTITUDE}
        pointRadius={0.25}
        pointColor={pointColor}
        pointLabel={pointLabel}
      />
      <header className="pointer-events-none absolute left-6 top-5">
        <h1 className="text-2xl font-semibold tracking-tight">Undercurrent</h1>
        <p className="text-sm text-slate-400">
          {error
            ? `Error: ${error.message}`
            : day
              ? `${stories.length} stories as of ${day}`
              : 'Loading…'}
        </p>
      </header>
      {legend.length > 0 && (
        <div className="pointer-events-none absolute bottom-5 left-6 max-w-sm space-y-2 rounded-lg bg-slate-900/70 p-3 text-xs backdrop-blur">
          {legend.map(({ category, stories: group }) => (
            <section key={category}>
              <div className="flex items-center gap-2 font-medium">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: CATEGORY_STYLE[category].color }}
                />
                {CATEGORY_STYLE[category].label}
                <span className="ml-auto pl-2 font-normal text-slate-400">{group.length}</span>
              </div>
              <ul className="ml-[18px] text-slate-400">
                {group.slice(0, 3).map((s) => (
                  <li key={s.id} className="truncate">
                    {s.title}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
