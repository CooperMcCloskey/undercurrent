import { useMemo } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Forecast, TimelinePoint } from '../api/types.gen'
import { formatLong, toIndex, type DayIndex } from '../timeline/days'

/** How much history is drawn before the selected day. */
const HISTORY_DAYS = 56
const WINDOW = 7

export interface SeriesPoint {
  t: DayIndex
  history?: number
  actual?: number
  forecast?: number
  band?: [number, number]
}

/**
 * The chart's data: the rolling 7-day sum of the story's daily articles (the same number as the
 * snapshot's `article_count_7d`, so history and forecast share a scale) up to `asOf`; the forecast
 * through each horizon with its range; and, if `reveal`, what actually happened after `asOf`.
 */
export function buildSeries(
  timeline: readonly TimelinePoint[],
  asOf: DayIndex,
  today: number,
  forecast: Forecast | null,
  reveal: boolean,
): { points: SeriesPoint[]; hasLater: boolean } {
  const daily = new Map(timeline.map((p) => [toIndex(p.day), p.articles]))
  const days = [...daily.keys()]
  const last = days.reduce((a, b) => Math.max(a, b), asOf)
  const first = Math.max(
    days.reduce((a, b) => Math.min(a, b), asOf),
    asOf - HISTORY_DAYS,
  )
  const rolling = (d: DayIndex) => {
    let sum = 0
    for (let k = 0; k < WINDOW; k++) sum += daily.get(d - k) ?? 0
    return sum
  }
  const byDay = new Map<DayIndex, SeriesPoint>()
  const at = (t: DayIndex) => {
    const p = byDay.get(t) ?? { t }
    byDay.set(t, p)
    return p
  }
  for (let d = first; d <= asOf; d++) at(d).history = d === asOf ? today : rolling(d)
  const reach = forecast ? Math.max(...forecast.horizons.map((h) => h.days)) : 0
  const hasLater = last > asOf
  if (reveal) {
    for (let d = asOf; d <= Math.min(last, asOf + Math.max(reach, 1)); d++) {
      at(d).actual = d === asOf ? today : rolling(d)
    }
  }
  if (forecast) {
    const start = at(asOf)
    start.forecast = today
    start.band = [today, today]
    for (const h of forecast.horizons) {
      const p = at(asOf + h.days)
      p.forecast = h.articles_7d
      p.band = [h.low, h.high]
    }
  }
  return { points: [...byDay.values()].sort((a, b) => a.t - b.t), hasLater }
}

const shortDay = (t: number) => formatLong(t).replace(/ \d{4}$/, '')

/** Articles in the past 7 days, over time: history, the forecast and its range, and optionally what happened. */
export function ForecastChart({
  points,
  asOf,
  color,
  height = 150,
}: {
  points: SeriesPoint[]
  asOf: DayIndex
  color: string
  height?: number
}) {
  const ticks = useMemo(() => {
    const lo = points[0]?.t ?? asOf
    const hi = points[points.length - 1]?.t ?? asOf
    return [lo, asOf, hi].filter((t, i, all) => all.indexOf(t) === i)
  }, [points, asOf])
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={points} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
        <CartesianGrid stroke="#334155" strokeOpacity={0.35} vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          domain={['dataMin', 'dataMax']}
          ticks={ticks}
          tickFormatter={shortDay}
          tick={{ fill: '#94a3b8', fontSize: 10 }}
          stroke="#475569"
          interval={0}
        />
        <YAxis
          tick={{ fill: '#94a3b8', fontSize: 10 }}
          stroke="#475569"
          width={44}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ stroke: '#64748b' }}
          contentStyle={{ background: '#0f172a', border: '1px solid #334155', fontSize: 11 }}
          labelFormatter={(t) => formatLong(Number(t))}
          formatter={(value, name) =>
            Array.isArray(value)
              ? [`${Math.round(Number(value[0]))}–${Math.round(Number(value[1]))}`, 'Range']
              : [
                  Math.round(Number(value)),
                  name === 'history'
                    ? 'Articles, 7 days'
                    : name === 'actual'
                      ? 'What happened'
                      : 'Forecast',
                ]
          }
        />
        <Area
          dataKey="band"
          stroke="none"
          fill={color}
          fillOpacity={0.22}
          isAnimationActive={false}
          connectNulls
        />
        <Line
          dataKey="history"
          stroke="#e2e8f0"
          strokeWidth={1.75}
          dot={false}
          isAnimationActive={false}
          connectNulls
        />
        <Line
          dataKey="forecast"
          stroke={color}
          strokeWidth={2}
          strokeDasharray="5 3"
          dot={{ r: 2.5, fill: color, stroke: 'none' }}
          isAnimationActive={false}
          connectNulls
        />
        <Line
          dataKey="actual"
          stroke="#34d399"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
          connectNulls
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
