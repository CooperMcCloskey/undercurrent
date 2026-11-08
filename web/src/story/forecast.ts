import type {
  Factor,
  Forecast,
  HorizonForecast,
  HorizonMetrics,
  ModelMetrics,
  StorySummary,
} from '../api/types.gen'

// Wording for a story's forecast. A forecast is a projection of `article_count_7d` (the number the
// globe draws the heat from) at several horizons, so these describe it in the same unit as the
// numbers beside it.

/** The horizon to quote in one-liners: the 7-day one if the model has it, else the longest. */
export function headlineHorizon(forecast: Forecast): HorizonForecast {
  return (
    forecast.horizons.find((h) => h.days === 7) ??
    forecast.horizons.reduce((a, b) => (b.days > a.days ? b : a))
  )
}

/** "~340 articles a week in 7 days (80% range 220–510)", for the story panel. */
export function describeForecast(forecast: Forecast): string {
  const h = headlineHorizon(forecast)
  const pct = Math.round(forecast.interval * 100)
  return `~${Math.round(h.articles_7d)} articles a week in ${h.days} days (${pct}% range ${Math.round(h.low)}–${Math.round(h.high)})`
}

/** "~340 a week in 7 days", for the globe tooltip. */
export function describeForecastShort(forecast: Forecast): string {
  const h = headlineHorizon(forecast)
  return `~${Math.round(h.articles_7d)} a week in ${h.days} days`
}

// ---- Which forecasts to believe -------------------------------------------------------------------

/**
 * The forecast with only the horizons where the model beat "the story stays the size it is" on the
 * held-out days (`meta.model`). A horizon that doesn't beat it isn't a forecast worth showing.
 * With no model card yet, everything is kept. Null if no horizon is left.
 */
export function trustedForecast(
  forecast: Forecast | null,
  model: ModelMetrics | null,
): Forecast | null {
  if (!forecast || !model) return forecast
  const good = new Set(model.horizons.filter(beatsBaseline).map((h) => h.days))
  const horizons = forecast.horizons.filter((h) => good.has(h.days))
  const [first, ...rest] = horizons
  return first ? { ...forecast, horizons: [first, ...rest] } : null
}

export const beatsBaseline = (h: HorizonMetrics) => h.mae_log2 < h.baseline_mae_log2

/**
 * A story's size (`article_count_7d`) `offset` days after its snapshot, read off its forecast: a
 * straight line between today's size and each horizon's middle estimate, and the last horizon's
 * after that. A story with no forecast stays the size it is (the globe draws it unchanged).
 */
export function sizeAfter(story: StorySummary, offset: number): number {
  const points = [
    { days: 0, size: story.article_count_7d },
    ...(story.forecast?.horizons ?? []).map((h) => ({ days: h.days, size: h.articles_7d })),
  ]
  if (offset <= 0 || points.length === 1) return story.article_count_7d
  for (let i = 1; i < points.length; i++) {
    if (offset <= points[i].days) {
      const a = points[i - 1]
      const b = points[i]
      return a.size + ((b.size - a.size) * (offset - a.days)) / (b.days - a.days)
    }
  }
  return points[points.length - 1].size
}

/** The story as the globe draws it `offset` days ahead. */
export function projectStory(story: StorySummary, offset: number): StorySummary {
  return offset > 0 && story.forecast
    ? { ...story, article_count_7d: Math.round(sizeAfter(story, offset)) }
    : story
}

// ---- Emerging stories -----------------------------------------------------------------------------

/** A story must be at least this big today to be listed, so the list isn't small, noisy stories. */
export const EMERGING_MIN_SIZE = 20

export interface Emerging {
  story: StorySummary
  horizon: HorizonForecast
  /** log2((projected + 1) / (today + 1)): 1 means doubling. */
  growth: number
  /** The biggest reason behind the projection. */
  factor: Factor | null
  /** What actually happened at that horizon, for a day in the past. */
  actual: number | null
}

/**
 * The stories projected to grow: the headline horizon's whole range (`low`) is above today's size.
 * Ranked by projected growth. The globe's rings and the Emerging list both use this, so they agree.
 */
export function emergingStories(stories: readonly StorySummary[]): Emerging[] {
  const out: Emerging[] = []
  for (const story of stories) {
    if (!story.forecast || story.article_count_7d < EMERGING_MIN_SIZE) continue
    const horizon = headlineHorizon(story.forecast)
    if (horizon.low <= story.article_count_7d) continue
    const factor = horizon.factors.reduce<Factor | null>(
      (best, f) => (!best || Math.abs(f.contribution) > Math.abs(best.contribution) ? f : best),
      null,
    )
    const actual = story.outcome?.horizons.find((o) => o.days === horizon.days)?.articles_7d ?? null
    out.push({
      story,
      horizon,
      growth: Math.log2((horizon.articles_7d + 1) / (story.article_count_7d + 1)),
      factor,
      actual,
    })
  }
  return out.sort((a, b) => b.growth - a.growth)
}

const FACTOR_NAMES: Record<string, string> = {
  growth_7d: 'last week’s growth',
  wiki_pageview_growth: 'Wikipedia pageviews',
  log_wiki_views: 'Wikipedia pageviews',
  log_wiki_edits: 'Wikipedia edits',
  log_count: 'recent article count',
  log_domains: 'number of outlets',
  log_countries: 'number of countries',
  tone: 'tone of the coverage',
}

/** "last week’s growth ↑" for a factor, in words. */
export function describeFactor(f: Factor): string {
  const name = FACTOR_NAMES[f.name] ?? f.name.replace(/_/g, ' ')
  return `${name} ${f.contribution >= 0 ? '↑' : '↓'}`
}
