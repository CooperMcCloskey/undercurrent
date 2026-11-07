// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import Globe from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { loadLand } from './globe/countries'
import { createHeatLayer, type HeatPoint } from './globe/heat'
import { hexToRgb, storyColors } from './globe/storyColors'

// Vector globe: ocean sphere, merged land mesh (globe/land.ts), a heatmap of where stories are
// happening with one colour per story (globe/heat.ts), and a small flat marker per story.
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

// PERFORMANCE: everything passed to <Globe> must keep the same identity between renders.
// react-globe.gl re-processes a whole layer whenever a data or accessor prop changes, and inline
// arrow functions or `?? []` are new on every render. So accessors, materials and config live at
// module scope, and data arrays are memoised.
const OCEAN = new MeshLambertMaterial({ color: COLORS.ocean })
const RENDERER_CONFIG = { powerPreference: 'high-performance' } as const // discrete GPU if present
const NO_STORIES: StorySummary[] = []

interface Marker {
  story: StorySummary
  color: string
  lat: number
  lng: number
}
const marker = (o: object) => o as Marker
const pointLat = (o: object) => marker(o).lat
const pointLng = (o: object) => marker(o).lng

/** Stories anchored at the same place (two Rotterdam stories) are fanned out in a small ring. */
function placeMarkers(stories: StorySummary[], colors: Map<string, string>): Marker[] {
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
        color: colors.get(story.id)!,
        lat: lat + r * Math.sin(angle),
        lng: lon + (r * Math.cos(angle)) / Math.max(Math.cos((lat * Math.PI) / 180), 0.2),
      }
    }),
  )
}
const pointColor = (o: object) => marker(o).color
// Tooltips are inserted as HTML, and titles come from scraped headlines: always escape.
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
const pointLabel = (o: object) => {
  const { story: s, color } = marker(o)
  const p = s.forecast ? ` · ${Math.round(s.forecast.p_grow * 100)}% chance of growing` : ''
  return `<span style="color:${color}">●</span> <b>${escapeHtml(s.title)}</b><br/>${s.article_count_7d} articles, ${s.post_count_7d} posts this week${p}`
}
const layerObject = (d: object) => (d as { object: Object3D }).object
const loadLandLayer = () => loadLand({ altitude: LAND_ALTITUDE, ...COLORS })

/** Each story's heat is its article count, spread over its footprint (or just its location). */
function heatPoints(stories: StorySummary[], colors: Map<string, string>): HeatPoint[] {
  return stories.flatMap((s) => {
    const color = hexToRgb(colors.get(s.id)!)
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

  const colors = useMemo(() => storyColors(stories.map((s) => s.id)), [stories])
  const markers = useMemo(() => placeMarkers(stories, colors), [stories, colors])
  const legend = useMemo(
    () => [...markers].sort((a, b) => b.story.article_count_7d - a.story.article_count_7d),
    [markers],
  )

  // One heat layer for the app's lifetime; only its texture changes when the stories do.
  const heat = useMemo(() => {
    const layer = createHeatLayer({ altitude: HEAT_ALTITUDE })
    return { layer, item: { object: layer.object } }
  }, [])
  useEffect(() => {
    heat.layer.update(heatPoints(stories, colors))
  }, [heat, stories, colors])

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
        <ol className="pointer-events-none absolute bottom-5 left-6 max-w-sm space-y-1 rounded-lg bg-slate-900/70 p-3 text-xs backdrop-blur">
          {legend.slice(0, 12).map(({ story, color }) => (
            <li key={story.id} className="flex items-center gap-2">
              <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
              <span className="truncate">{story.title}</span>
              <span className="ml-auto pl-2 text-slate-400">{story.article_count_7d}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
