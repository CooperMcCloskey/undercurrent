import type { Forecast, HorizonForecast } from '../api/types.gen'

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
