// The only place the web app talks to the backend. Response shapes come from types.gen.ts,
// which is generated from pipeline/undercurrent/contracts.py (npm run gen:types).
import type { Meta, Snapshot, StoryDetail } from './types.gen'

// `npm run dev:mock` reads web/public/mock/ directly, so no Python is needed.
// `npm run dev` goes through the FastAPI server, which serves data/export/ (or the same mock files
// until the pipeline has exported something).
const USE_MOCK = import.meta.env.MODE === 'mock'

// A request that hasn't answered by then fails with an explanation, instead of leaving the page
// on "Loading…" for ever (an API that has hung, or a file it can't read).
const TIMEOUT_MS = 15_000

async function get<T>(apiPath: string, mockPath: string): Promise<T> {
  const url = USE_MOCK ? `/mock/${mockPath}` : `/api/${apiPath}`
  let res: Response
  try {
    res = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'TimeoutError') {
      throw new Error(
        `${url} didn't answer within ${TIMEOUT_MS / 1000} s. Look for an error in the [api] lines of the npm run dev terminal.`,
      )
    }
    throw new Error(`Couldn't reach ${url}: is npm run dev still running?`)
  }
  // Vite's proxy answers 502/504 when nothing is listening on :8000 yet.
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    throw new Error(
      `The API on :8000 isn't up (${res.status}). It may still be starting (uv installs packages on the first run); this page retries on its own.`,
    )
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`)
  // Vite answers unknown paths with index.html, which would fail in res.json() with a confusing message.
  if (!(res.headers.get('content-type') ?? '').includes('json')) {
    throw new Error(`${url} didn't return JSON. Is the API (not just the web server) running?`)
  }
  return (await res.json()) as T
}

export const api = {
  meta: () => get<Meta>('meta', 'meta.json'),
  /** All stories as of `day` (YYYY-MM-DD), with forecasts made using only data up to that day. */
  snapshot: (day: string) => get<Snapshot>(`snapshots/${day}`, `snapshots/${day}.json`),
  story: (id: string) => get<StoryDetail>(`stories/${id}`, `stories/${id}.json`),
}
