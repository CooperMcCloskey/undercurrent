# Globe scaling: loading and clutter at 12 months × 750 stories

An implementation brief for an agent. The globe was built against 6 mock stories. The 12-month run gives it 365 daily snapshots of up to 750 stories each (`stories/export.py --top 750`). This brief makes it load fast and stay readable at that scale.

**Status:** not started. **Code owner:** workstream D (`web/`, `pipeline/undercurrent/api/`). Steps 1 (API) and 3 (contract + exporter) cross into other workstreams: agree them with the team first, as `AGENTS.md` requires. Everything else is web-only.

Read `docs/ARCHITECTURE.md`, section "Web", before starting. Its rules still apply: keep `<Globe>` props stable, always pass `customThreeObjectUpdate`, don't use `polygonsData` or `heatmapsData`.

## The problem

### Loading

- **One uncompressed JSON fetch per day.** `web/src/api/client.ts` fetches `snapshots/<day>.json`, about 2 MB for 750 stories (estimated from the mock: ~2.7 KB a story). The API (`api/app.py`) has no gzip.
- **Playback asks for 3–8 days a second.** `timeline/TimeBar.tsx` crosses the view in `SWEEP_SECONDS` (130 s): a one-year view plays at ~2.8 days/s, ~8.4 at 3×. Each day is a new fetch, so the globe lags behind the bar and skips days (`keepPreviousData` in `App.tsx`).
- **Skipped days still download.** `get()` uses only `AbortSignal.timeout`, not react-query's `signal`, so passed-over requests keep running and queue behind each other.
- **Memory grows.** react-query keeps inactive queries for 5 minutes (default `gcTime`), so playing through a year holds ~365 parsed snapshots.
- **React re-renders every frame.** `timeline/useEasedStories.ts` calls `setShown` on every animation frame until the easing settles; while playing it never settles. Each render reruns `placeMarkers` and hands `pointsData` a new array, which react-globe.gl re-processes (one mesh per marker).
- **Code that slows down quadratically.** `globe/select.ts` `pickStories` compares each story with every bigger one (O(n²)); `placeMarkers` in `App.tsx` copies arrays inside its loop (`[...byPlace.get(key) ?? [], e]`). Fine at 750, slow if `--top` goes up.

### Clutter

- The only defence is the global story-count slider (default 60, up to all) plus `pickStories`' crowded-areas-last order. At the slider's far end, Europe is a pile of overlapping markers and glows.
- The count is global, not per screen: zoomed out, 60 markers crowd Europe; zoomed into Africa, there are almost none.
- Every drawn story gets a glow, and every emerging story a pulsing ring, so nothing stands out.
- Playback animates everything at full detail, which is the most cluttered and most expensive moment.

## Targets

Measure these before and after each step (step 0):

| Measure | Target |
|---|---|
| Frame time while panning, 750 stories drawn | ≤ 16 ms (60 fps) |
| Playing a 1-year view at 3× | No frozen frames; the globe stays on the bar's day |
| Picking a day while paused → stories drawn | < 300 ms |
| JS heap after playing a full year | < 300 MB |
| Marks on screen at full zoom-out | ≤ ~40 |

## Steps

Do them in this order: **0, 1, 2, 6, 7, 4, 3, 8, 9, 10, 5**. Each one is a separate commit (`web: ...`, `api: ...`), and `npm run check` passes before each.

### 0. Measure (web-only)

- Add a dev-only performance overlay (shown with `?perf` in the URL, or only when `import.meta.env.DEV`): frame time, markers drawn, last snapshot's fetch + parse ms, `performance.memory.usedJSHeapSize` where available.
- Add a large mock set: extend `pipeline/undercurrent/mock.py` (or a separate script under `web/scripts/`) to generate 365 days × 750 stories in the contract's shape, with clustering in Europe, the US and South Asia like real data. Don't commit the generated files if they are large; document the command instead.
- Write the "before" numbers into the Notes of D's section in `PLAN.md`.

### 1. Compress responses (API, owner D)

- `api/app.py`: `app.add_middleware(GZipMiddleware, minimum_size=1024)`.
- Check with `fastapi.testclient.TestClient` that `Content-Encoding: gzip` comes back when asked for, and that `uv run pytest` still passes.

### 2. Stop wasted downloads (web-only)

- `api/client.ts`: accept a `signal` and combine it with the timeout (`AbortSignal.any([signal, AbortSignal.timeout(TIMEOUT_MS)])`). In `App.tsx` pass react-query's `signal` from `queryFn: ({ signal }) => ...`.
- Snapshots: `gcTime` of ~30 s, so only the recent few stay in memory.
- When paused, `queryClient.prefetchQuery` the exported days either side of the current one (±3).
- While playing, don't fetch full snapshots for every day: fetch at most one at a time and drop days the bar has already passed (until step 3 replaces this).

### 3. A playback file (contract change: D + B, agreed with the team)

- New optional contract model, e.g. `Track`: a story index once (`id`, `title`, `location`, `category`), then per day the stories' sizes as parallel arrays (`story` indexes, `article_count_7d`). Roughly 274k pairs for a year, a few MB before gzip.
- Exporter writes `data/export/track.json` (or one per month if it gets big); API serves it at `/api/track`; `Meta` gets an optional pointer or flag.
- Follow the contract procedure in `AGENTS.md` in one commit: `contracts.py` → `npm run gen:types` → `mock.py` + `npm run gen:mock` → `uv run pytest`.
- Web: while playing, draw from the track only. On pause, fetch that day's full snapshot for forecasts, outcomes, footprints and the panel.

### 4. Take React out of the frame loop (web-only)

- Replace `pointsData` markers with one custom layer: an `InstancedMesh` (or a `Points` with a shader, like `globe/glow.ts`), one instance per story, with per-instance position, radius, colour and level attributes updated in place.
- Move easing out of React state: keep targets in a ref and step them in `scene.onBeforeRender` (where the glow and stars are already updated). React only re-renders when the day, selection or filters change.
- Hover and click: on pointer move, find the nearest marker in screen space (project the instances' positions; use a small grid so it isn't O(n) per move). Keep the existing tooltip text (`pointLabel`) and behaviour, including `HOVER_LEAVE_MS`.
- Rings for emerging stories can stay on `ringsData` (they're few, see step 8).

### 5. Remove the quadratic bits (web-only, only if `--top` goes above ~1,000)

- `pickStories`: put the stories into 2.5° cells and look only at neighbouring cells.
- `placeMarkers`: push into arrays instead of copying them.

### 6. Level of detail by zoom (web-only)

Read the camera altitude (`globe.pointOfView().altitude`) and pick a level:

| Zoom | Altitude (globe radii) | Draw |
|---|---|---|
| Far | > ~1.4 | One bubble per country (`location.country`), or per region where countries are tiny. Sized by summed `article_count_7d`, coloured by the dominant category; tooltip lists the top 3 stories. ≤ ~40 marks |
| Mid | ~0.6 – 1.4 | Individual stories, decluttered per screen cell (step 7) |
| Near | < ~0.6 | Every story in view, using the existing ring layout (`globe/spots.ts`) |

- Clicking a bubble flies in (`pointOfView`) to the mid level centred on it.
- Cross-fade between levels with the existing easing (a bubble's level fades out as its stories' fade in), so zooming doesn't pop.
- Change level only once the camera has been still for ~150 ms, and use a small margin between levels so they don't flicker at the edge.
- The selected story and the emerging ones are always drawn individually, at every level.

### 7. Declutter by screen space (web-only)

- Divide the screen into ~50 px cells. Project each candidate story's position; keep the biggest per cell; show a small "+k" badge (html layer, `textContent` only) for the rest.
- Recompute when the camera stops moving or the day changes, not every frame.
- Skip stories on the far side of the globe (dot product of the position with the camera direction < 0).
- Turn the slider into density: "markers per cell" (1–3), not a global count. Keep its logarithmic feel and the keyboard access.

### 8. Visual hierarchy (web-only)

- The top ~10 stories in view: glow and a short always-visible title (reuse `globe/labels.ts` and `cullLabels` so titles never overlap).
- Everyone else: plain dot, no glow (glow `strength` 0).
- Emerging rings: at most ~8, the strongest first.
- Selecting a story dims the rest to ~15% (glows already dim: `GLOW_OPACITY_DIMMED`; do the same for markers).

### 9. Calm playback (web-only)

- While the time bar is playing (expose `playing` from `TimeBar`): top ~30 stories only, no labels, rings, badges or article dots, and level of detail fixed at its current level.
- Pausing restores everything, with the normal easing.

### 10. Narrowing tools (web-only)

- An "Emerging only" toggle next to the legend.
- A search box (title and place, client-side over the day's stories) that flies to and selects the match.

## Rules for this work

- Stay in `web/` (and `api/` for step 1). Anything in `stories/` or `contracts.py` goes through the team first; write it under "Needs from others" in D's section of `PLAN.md` and carry on with a stub.
- All server data goes through `web/src/api/client.ts` and react-query.
- Never hand-edit `web/src/api/types.gen.ts`.
- Scraped text goes into the DOM with `textContent` or escaped (`escapeHtml` in `App.tsx`), never raw `innerHTML`.
- After each step, add the measured numbers to D's Notes in `PLAN.md`, and update the "Web" section of `docs/ARCHITECTURE.md` when a behaviour changes (for example, the slider's meaning in step 7).

## Done when

- Every target in the table above is met on the 365 × 750 mock, and the numbers are in `PLAN.md`.
- Zoomed out, the globe shows ≤ ~40 marks and reads at a glance; zoomed in, every story is reachable.
- `npm run check` passes, and the app was run in a browser at each zoom level and while playing.
