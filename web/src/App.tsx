// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { interpolateInferno } from 'd3-scale-chromatic'
import Globe from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { loadLand } from './globe/countries'
import { createHeatLayer, type HeatPoint } from './globe/heat'

// Vector globe: ocean sphere, merged land mesh (globe/land.ts), heatmap of where stories are
// happening (globe/heat.ts), and a small flat marker per story for hover.
const COLORS = {
  ocean: '#0b1424',
  land: '#1c2a3f',
  border: '#3d5373',
  atmosphere: '#7dd3fc',
  growing: '#22d3ee', // marker colour when the forecast says the story will grow
  steady: '#cbd5e1',
}
// Layers stack outwards, as a fraction of the globe radius.
const LAND_ALTITUDE = 0.005
const HEAT_ALTITUDE = 0.007
const MARKER_ALTITUDE = 0.011
// Skip inferno's near-black start so faint heat still shows on the dark globe.
const heatColor = (t: number) => interpolateInferno(0.25 + 0.75 * t)

// PERFORMANCE: everything passed to <Globe> must keep the same identity between renders.
// react-globe.gl re-processes a whole layer whenever a data or accessor prop changes, and inline
// arrow functions or `?? []` are new on every render. So accessors, materials and config live at
// module scope, and data arrays are memoised.
const OCEAN = new MeshLambertMaterial({ color: COLORS.ocean })
const RENDERER_CONFIG = { powerPreference: 'high-performance' } as const // discrete GPU if present
const NO_STORIES: StorySummary[] = []

const story = (o: object) => o as StorySummary
const pointLat = (o: object) => story(o).location.lat
const pointLng = (o: object) => story(o).location.lon
const pointColor = (o: object) =>
  (story(o).forecast?.p_grow ?? 0) > 0.5 ? COLORS.growing : COLORS.steady
// Tooltips are inserted as HTML, and titles come from scraped headlines: always escape.
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
const pointLabel = (o: object) => {
  const s = story(o)
  const p = s.forecast ? ` · ${Math.round(s.forecast.p_grow * 100)}% chance of growing` : ''
  return `<b>${escapeHtml(s.title)}</b>${p}`
}
const layerObject = (d: object) => (d as { object: Object3D }).object
const loadLandLayer = () => loadLand({ altitude: LAND_ALTITUDE, ...COLORS })

/** Each story's heat is its article count, spread over its footprint (or just its location). */
function heatPoints(stories: StorySummary[]): HeatPoint[] {
  return stories.flatMap((s) => {
    const footprint = s.footprint.length
      ? s.footprint
      : [{ lat: s.location.lat, lon: s.location.lon, weight: 1, radius_km: 150 }]
    return footprint.map((p) => ({
      lat: p.lat,
      lon: p.lon,
      weight: s.article_count_7d * p.weight,
      radiusKm: p.radius_km,
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

  // One heat layer for the app's lifetime; only its texture changes when the stories do.
  const heat = useMemo(() => {
    const layer = createHeatLayer({ altitude: HEAT_ALTITUDE, color: heatColor })
    return { layer, item: { object: layer.object } }
  }, [])
  useEffect(() => {
    heat.layer.update(heatPoints(stories))
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
        // Small flat markers, just above the heat, for hover. With 1,000+ stories set
        // pointsMerge (one draw call, but no per-point hover).
        pointsData={stories}
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
    </div>
  )
}
