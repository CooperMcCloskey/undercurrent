// Which stories the globe draws, in the order the slider reveals them: stories in the open first,
// biggest first, then the ones in crowded patches. Europe has hundreds of stories; a handful of the
// biggest tell the story, and the rest only pile up on top of each other, so they come last. Every
// story is still in the order, so the slider's far end shows all of them.
import type { StorySummary } from '../api/types.gen'

/** Stories are grouped by how many bigger ones are within DENSE_RADIUS_DEG, DENSE_STEP at a time. */
export const DENSE_STEP = 3
export const DENSE_RADIUS_DEG = 2.5

const DEG = Math.PI / 180

/** How many of `bigger` are within DENSE_RADIUS_DEG of `story`. */
function biggerNearby(story: StorySummary, bigger: readonly StorySummary[]): number {
  const { lat, lon } = story.location
  const cos = Math.max(Math.cos(lat * DEG), 0.2)
  let n = 0
  for (const other of bigger) {
    const dLat = other.location.lat - lat
    if (Math.abs(dLat) > DENSE_RADIUS_DEG) continue
    const dLon = ((((other.location.lon - lon + 180) % 360) + 360) % 360) - 180
    if (Math.hypot(dLon * cos, dLat) <= DENSE_RADIUS_DEG) n++
  }
  return n
}

/**
 * The first `count` stories in this order: those with fewer than DENSE_STEP bigger stories within
 * DENSE_RADIUS_DEG (biggest first), then those with fewer than 2 x DENSE_STEP, and so on. A crowded
 * capital keeps its biggest few ahead of the many smaller stories at the same spot, and the rest of
 * the world gets the slots first. Stories in `keep` (the selected one, those projected to grow) are
 * always included, and don't count against `count`.
 */
export function pickStories(
  stories: readonly StorySummary[],
  count: number,
  keep: ReadonlySet<string>,
): StorySummary[] {
  const ranked = [...stories].sort(
    (a, b) => b.article_count_7d - a.article_count_7d || a.id.localeCompare(b.id),
  )
  const tier = new Map<string, number>()
  ranked.forEach((story, i) => {
    tier.set(story.id, Math.floor(biggerNearby(story, ranked.slice(0, i)) / DENSE_STEP))
  })
  // A stable sort: within a tier, the biggest stay first.
  const ordered = ranked.sort((a, b) => tier.get(a.id)! - tier.get(b.id)!)
  const forced = ordered.filter((s) => keep.has(s.id))
  return [...forced, ...ordered.filter((s) => !keep.has(s.id)).slice(0, count)]
}
