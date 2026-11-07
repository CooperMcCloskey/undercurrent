// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import Globe from 'react-globe.gl'
import { MeshLambertMaterial, type Object3D } from 'three'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'
import { loadLand } from './globe/countries'

// Vector globe: a plain sphere for the ocean plus one merged land mesh (see globe/land.ts).
const COLORS = {
  ocean: '#0b1424',
  land: '#1c2a3f',
  border: '#3d5373',
  atmosphere: '#7dd3fc',
  growing: '#f97316',
  steady: '#38bdf8',
}
const LAND_ALTITUDE = 0.005

// PERFORMANCE: everything passed to <Globe> must keep the same identity between renders.
// react-globe.gl re-processes a whole layer whenever a data or accessor prop changes, and inline
// arrow functions or `?? []` are new on every render. So accessors, materials and config live at
// module scope, and data arrays are memoised.
const OCEAN = new MeshLambertMaterial({ color: COLORS.ocean })
const RENDERER_CONFIG = { powerPreference: 'high-performance' } as const // discrete GPU if present
const NO_STORIES: StorySummary[] = []
const NO_LAYERS: object[] = []

const story = (o: object) => o as StorySummary
const pointLat = (o: object) => story(o).location.lat
const pointLng = (o: object) => story(o).location.lon
const pointAltitude = (o: object) => Math.max(0.02, Math.min(0.4, story(o).article_count_7d / 5000))
const pointColor = (o: object) =>
  (story(o).forecast?.p_grow ?? 0) > 0.5 ? COLORS.growing : COLORS.steady
const pointLabel = (o: object) => story(o).title
const layerObject = (d: object) => (d as { object: Object3D }).object
const loadLandLayer = () => loadLand({ altitude: LAND_ALTITUDE, ...COLORS })

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
  const landLayer = useMemo(() => (land.data ? [{ object: land.data }] : NO_LAYERS), [land.data])

  const stories = snapshot.data?.stories ?? NO_STORIES
  const error = meta.error ?? snapshot.error ?? land.error

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <Globe
        rendererConfig={RENDERER_CONFIG}
        backgroundColor="rgba(0,0,0,0)"
        globeMaterial={OCEAN}
        atmosphereColor={COLORS.atmosphere}
        atmosphereAltitude={0.18}
        customLayerData={landLayer}
        customThreeObject={layerObject}
        // One mesh per point keeps hover labels. With 1,000+ stories, set pointsMerge (one draw
        // call, but no per-point hover) or switch to hexBinPointsData.
        pointsData={stories}
        pointLat={pointLat}
        pointLng={pointLng}
        pointAltitude={pointAltitude}
        pointRadius={0.6}
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
