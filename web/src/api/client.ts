// The only place the web app talks to the backend. Response shapes come from types.gen.ts,
// which is generated from pipeline/undercurrent/contracts.py (npm run gen:types).
import type { Meta, Snapshot, StoryDetail } from './types.gen'

// `npm run dev:mock` reads web/public/mock/ directly, so no Python is needed.
// `npm run dev` goes through the FastAPI server, which serves data/export/ (or the same mock files
// until the pipeline has exported something).
const USE_MOCK = import.meta.env.MODE === 'mock'

async function get<T>(apiPath: string, mockPath: string): Promise<T> {
  const res = await fetch(USE_MOCK ? `/mock/${mockPath}` : `/api/${apiPath}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${res.url}`)
  return (await res.json()) as T
}

export const api = {
  meta: () => get<Meta>('meta', 'meta.json'),
  /** All stories as of `day` (YYYY-MM-DD), with forecasts made using only data up to that day. */
  snapshot: (day: string) => get<Snapshot>(`snapshots/${day}`, `snapshots/${day}.json`),
  story: (id: string) => get<StoryDetail>(`stories/${id}`, `stories/${id}.json`),
}
