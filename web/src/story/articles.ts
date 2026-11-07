// Turns a story's items (articles, posts, Wikipedia pages) into what the UI draws: a ranked list,
// a dot size per item, and where each dot sits around the story's marker.
import type { Item, Location } from '../api/types.gen'

export type SourceKind = Item['kind']

/** How each kind of item looks: `shape` is how its dot is drawn (colour is the story's category). */
export const KIND_STYLE: Record<SourceKind, { label: string; shape: 'disc' | 'ring' | 'diamond' }> =
  {
    news: { label: 'News', shape: 'disc' },
    wikipedia: { label: 'Wikipedia', shape: 'diamond' },
    bluesky: { label: 'Bluesky', shape: 'ring' },
    reddit: { label: 'Reddit', shape: 'ring' },
  }
export const KIND_ORDER = Object.keys(KIND_STYLE) as SourceKind[]

export interface Article extends Item {
  /** 0..1, relative to the most important item of the same story (the top item is 1). */
  importance: number
}

// IMPORTANCE. The contract has no per-item importance (yet), so it is derived from what an item
// does have:
//   - similarity: how squarely the item is about the story (cosine similarity to its centroid);
//     squared, so the gap between a core item and a fringe one shows up in the dot size.
//   - kind: a news article or a Wikipedia page counts for more than a single social post.
// Swap this out when the export carries a real signal: syndicated copies (`dup_count` in
// articles.parquet), social engagement, or the page's Wikipedia pageviews.
const KIND_WEIGHT: Record<SourceKind, number> = { news: 1, wikipedia: 1, bluesky: 0.6, reddit: 0.6 }

/** The story's items, most important first (newest first among equals), with `importance` set. */
export function rankArticles(items: readonly Item[]): Article[] {
  const raw = items.map((it) => Math.max(it.similarity, 0) ** 2 * KIND_WEIGHT[it.kind])
  const top = raw.reduce((a, b) => Math.max(a, b), 1e-9) // not Math.max(...raw): huge stories
  return items
    .map((it, i) => ({ ...it, importance: raw[i] / top }))
    .sort((a, b) => b.importance - a.importance || b.published_at.localeCompare(a.published_at))
}

/** Size of an item's mark in the panel, px. Grows with importance like its dot on the globe. */
export const glyphSize = (importance: number) => 7 + 15 * importance ** 2

// Dots live on the globe, so their sizes and spacing are in degrees of arc (1 deg ~ 111 km): they
// scale with the globe when you zoom, like the heat and the markers. They are small next to the
// story's marker (radius 0.6 deg, 1.1 when hovered), so the marker stays the clear centre.
const DOT_RADIUS_MIN_DEG = 0.2
const DOT_RADIUS_MAX_DEG = 0.5
export const dotRadius = (importance: number) =>
  DOT_RADIUS_MIN_DEG + (DOT_RADIUS_MAX_DEG - DOT_RADIUS_MIN_DEG) * importance ** 2

/** One dot of the constellation: a point on the globe. */
export interface DotPlacement {
  article: Article
  /** The place this dot is for (one of the article's places), or null for an item with no place. */
  place: Location | null
  lat: number
  lng: number
  /**
   * True if the dot is at its place (nudged aside only where dots would overlap).
   * False if the item has no known place, so its dot sits beside the story's marker instead.
   */
  atPlace: boolean
  /** Dot radius, degrees of arc. */
  radius: number
  /** Stagger for the burst-out animation, ms. */
  delay: number
}

/** The globe shows at most this many dots per story; the panel lists every article. */
export const MAX_DOTS = 80
/** An article gets a dot at each place it mentions, up to this many (its first ones: the main place first). */
const MAX_PLACES_PER_ARTICLE = 4
const DEG = Math.PI / 180
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))
const MARKER_GAP_DEG = 0.15 // dots keep this far from the edge of the story's marker
const DOT_GAP_DEG = 0.1
const SPREAD_ITERATIONS = 80
// Items with no place: a sunflower spiral beside the marker.
const BESIDE_INNER_DEG = 2.0
const BESIDE_OUTER_BASE_DEG = 2.6
const BESIDE_OUTER_PER_SQRT_DEG = 0.55

const placesOf = (article: Article): Location[] =>
  (article.locations ?? []).slice(0, MAX_PLACES_PER_ARTICLE) // `?? []`: older exports lack it

/** The articles that get dots: the most important ones, as many as fit in MAX_DOTS dots. */
export function articlesOnGlobe(ranked: readonly Article[]): Article[] {
  const shown: Article[] = []
  let dots = 0
  for (const article of ranked) {
    dots += Math.max(1, placesOf(article).length)
    if (dots > MAX_DOTS) break
    shown.push(article)
  }
  return shown
}

const cosLat = (lat: number) => Math.max(Math.cos(lat * DEG), 0.2) // 0.2: no blow-up at the poles
const wrapLng = (lng: number) => ((((lng + 180) % 360) + 360) % 360) - 180

type Spot = { lat: number; lng: number }

/**
 * If `a` and `b` are closer than `need` degrees, push them apart (`moveA` + `moveB` = 1 is the
 * share of the push each takes). Distances use the local flat approximation, fine at this size.
 * Returns whether anything moved.
 */
function separate(a: Spot, b: Spot, need: number, moveA: number, moveB: number, tie: number) {
  const midCos = cosLat((a.lat + b.lat) / 2)
  const dx = wrapLng(b.lng - a.lng) * midCos
  const dy = b.lat - a.lat
  if (Math.abs(dx) >= need || Math.abs(dy) >= need) return false
  const dist = Math.hypot(dx, dy)
  if (dist >= need) return false
  // Exactly the same place: split along a fixed direction that differs per pair.
  const ux = dist < 1e-6 ? Math.cos(tie * GOLDEN_ANGLE) : dx / dist
  const uy = dist < 1e-6 ? Math.sin(tie * GOLDEN_ANGLE) : dy / dist
  const shift = need - dist
  if (moveA) {
    a.lat -= uy * shift * moveA
    a.lng -= (ux * shift * moveA) / midCos
  }
  b.lat += uy * shift * moveB
  b.lng += (ux * shift * moveB) / midCos
  return true
}

/**
 * One dot per article per place it mentions, each at that place. These are the same places the
 * story's heat is built from, so every patch of heat has dots in it, and the dots differ from
 * story to story. Dots whose places coincide (three articles all about El Fasher) can't stand on
 * one another, so overlapping dots are pushed apart until they just touch, and all are kept clear
 * of the marker (`markerRadius`, degrees, as drawn while hovered). They move as little as that
 * takes. Items with no place (most posts) get one dot in a spiral beside the marker.
 *
 * `articles` must be ranked (rankArticles); only the first few are drawn (articlesOnGlobe).
 */
export function layoutDots(
  articles: readonly Article[],
  marker: Spot,
  markerRadius: number,
): DotPlacement[] {
  const shown = articlesOnGlobe(articles)
  const beside = shown.filter((a) => placesOf(a).length === 0).length
  const outer = BESIDE_OUTER_BASE_DEG + BESIDE_OUTER_PER_SQRT_DEG * Math.sqrt(beside)
  let k = 0 // index among the items placed beside the marker
  const dots: DotPlacement[] = []
  shown.forEach((article, i) => {
    const radius = dotRadius(article.importance)
    const delay = Math.min(i * 12, 300)
    const places = placesOf(article)
    for (const place of places) {
      dots.push({ article, place, lat: place.lat, lng: place.lon, atPlace: true, radius, delay })
    }
    if (places.length === 0) {
      const r = BESIDE_INNER_DEG + (outer - BESIDE_INNER_DEG) * Math.sqrt((k + 0.5) / beside)
      const angle = k++ * GOLDEN_ANGLE
      dots.push({
        article,
        place: null,
        lat: marker.lat + r * Math.sin(angle),
        lng: marker.lng + (r * Math.cos(angle)) / cosLat(marker.lat),
        atPlace: false,
        radius,
        delay,
      })
    }
  })

  for (let iteration = 0; iteration < SPREAD_ITERATIONS; iteration++) {
    let moved = false
    dots.forEach((a, i) => {
      moved = separate(marker, a, markerRadius + MARKER_GAP_DEG + a.radius, 0, 1, i) || moved
      for (let j = i + 1; j < dots.length; j++) {
        const b = dots[j]
        moved = separate(a, b, a.radius + b.radius + DOT_GAP_DEG, 0.5, 0.5, i + 31 * j) || moved
      }
    })
    if (!moved) break
  }
  for (const d of dots) {
    d.lat = Math.max(-89.5, Math.min(89.5, d.lat))
    d.lng = wrapLng(d.lng)
  }
  return dots
}

/** A place that some of the story's dots are for, with how many articles mention it. */
export interface PlaceLabel {
  name: string
  lat: number
  lng: number
  mentions: number
}

/** The distinct places of `dots` (not the nudged positions: the places themselves), busiest first. */
export function placeLabels(dots: readonly DotPlacement[]): PlaceLabel[] {
  const byPlace = new Map<string, PlaceLabel & { articles: Set<string> }>()
  for (const { place, article } of dots) {
    if (!place) continue
    const key = `${place.name}|${place.country ?? ''}`
    const entry = byPlace.get(key) ?? {
      name: place.name,
      lat: place.lat,
      lng: place.lon,
      mentions: 0,
      articles: new Set<string>(),
    }
    entry.articles.add(article.id)
    entry.mentions = entry.articles.size
    byPlace.set(key, entry)
  }
  return [...byPlace.values()]
    .map(({ name, lat, lng, mentions }) => ({ name, lat, lng, mentions }))
    .sort((a, b) => b.mentions - a.mentions)
}

/** How far the farthest dot is from the marker, degrees of arc (to frame the story with the camera). */
export function reachDeg(dots: readonly DotPlacement[], marker: Spot): number {
  const u = (p: Spot) => {
    const phi = (90 - p.lat) * DEG
    const theta = (90 - p.lng) * DEG
    return [Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta)]
  }
  const m = u(marker)
  return dots.reduce((far, d) => {
    const v = u(d)
    const cos = m[0] * v[0] + m[1] * v[1] + m[2] * v[2]
    return Math.max(far, Math.acos(Math.max(-1, Math.min(1, cos))) / DEG + d.radius)
  }, 0)
}

/** The URL only if it is plain http(s). URLs come from scraped data: never put `javascript:` in an href. */
export function safeHref(url: string): string | null {
  try {
    const { protocol, href } = new URL(url)
    return protocol === 'https:' || protocol === 'http:' ? href : null
  } catch {
    return null
  }
}

export function hostOf(article: Item): string {
  if (article.source) return article.source
  try {
    return new URL(article.url).hostname
  } catch {
    return article.kind
  }
}

const WHEN = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
})
/** "5 Nov, 12:00 UTC". Timestamps without a zone are UTC too (the contract says so). */
export function formatWhen(iso: string): string {
  const date = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? iso : `${WHEN.format(date)} UTC`
}
