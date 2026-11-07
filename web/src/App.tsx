// Placeholder: proves the contract -> API/mock -> globe path works end to end.
// Workstream D replaces this with the real UI.
import { useQuery } from '@tanstack/react-query'
import Globe from 'react-globe.gl'
import { api } from './api/client'
import type { StorySummary } from './api/types.gen'

const story = (o: object) => o as StorySummary

export default function App() {
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const day = meta.data?.latest
  const snapshot = useQuery({
    queryKey: ['snapshot', day],
    queryFn: () => api.snapshot(day!),
    enabled: !!day,
  })
  const stories = snapshot.data?.stories ?? []
  const error = meta.error ?? snapshot.error

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <Globe
        globeImageUrl="https://unpkg.com/three-globe/example/img/earth-night.jpg"
        backgroundColor="rgba(0,0,0,0)"
        pointsData={stories}
        pointLat={(o) => story(o).location.lat}
        pointLng={(o) => story(o).location.lon}
        pointAltitude={(o) => Math.min(0.4, story(o).article_count_7d / 5000)}
        pointRadius={0.6}
        pointColor={(o) => ((story(o).forecast?.p_grow ?? 0) > 0.5 ? '#f97316' : '#38bdf8')}
        pointLabel={(o) => story(o).title}
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
