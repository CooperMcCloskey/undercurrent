import { useMemo } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Forecast, TimelinePoint } from '../api/types.gen'
import { formatLong, toIndex, type DayIndex } from '../timeline/days'

/** The longest history drawn before the selected day: a story's whole life, up to about a year. */
const MAX_HISTORY_DAYS = 400
const WINDOW = 7

/**
 * What the line measures. "day": articles on each day (the story's real shape, bursts and gaps
 * included); the forecast is then drawn as the daily average of its forecast week. "week": articles
 * in the 7 days ending that day, the number the globe sizes a story by and the forecast predicts.
 */
export type Measure = 'day' | 'week'

export interface SeriesPoint {
  t: DayIndex
  history?: number
  actual?: number
  forecast?: number
  band?: [number, number]
}

/**
 * The chart's data over the story's whole life: its history up to `asOf`, the forecast through each
 * horizon with its range, and, if `reveal`, what actually happened after `asOf`. Starts at the
 * story's first day with data (not further back than MAX_HISTORY_DAYS).
 */
export function buildSeries(
  timeline: readonly TimelinePoint[],
  asOf: DayIndex,
  today: number,
  forecast: Forecast | null,
  reveal: boolean,
  measure: Measure,
): { points: SeriesPoint[]; hasLater: boolean } {
  const daily = new Map(timeline.map((p) => [toIndex(p.day), p.articles]))
  const days = [...daily.keys()]
  const last = days.reduce((a, b) => Math.max(a, b), asOf)
  const first = Math.max(
    days.reduce((a, b) => Math.min(a, b), asOf),
    asOf - MAX_HISTORY_DAYS,
  )
  const rolling = (d: DayIndex) => {
    let sum = 0
    for (let k = 0; k < WINDOW; k++) sum += daily.get(d - k) ?? 0
    return sum
  }
  const value = (d: DayIndex) => (measure === 'day' ? (daily.get(d) ?? 0) : rolling(d))
  const scale = measure === 'day' ? 1 / WINDOW : 1 // a week's total, as a daily average
  const byDay = new Map<DayIndex, SeriesPoint>()
  const at = (t: DayIndex) => {
    const p = byDay.get(t) ?? { t }
    byDay.set(t, p)
    return p
  }
  for (let d = first; d <= asOf; d++) at(d).history = value(d)
  const reach = forecast ? Math.max(...forecast.horizons.map((h) => h.days)) : 0
  const hasLater = last > asOf
  if (reveal) {
    for (let d = asOf; d <= Math.min(last, asOf + Math.max(reach, 1)); d++) at(d).actual = value(d)
  }
  if (forecast) {
    const start = at(asOf)
    start.forecast = today * scale
    start.band = [today * scale, today * scale]
    for (const h of forecast.horizons) {
      const p = at(asOf + h.days)
      p.forecast = h.articles_7d * scale
      p.band = [h.low * scale, h.high * scale]
    }
  }
  return { points: [...byDay.values()].sort((a, b) => a.t - b.t), hasLater }
}

/** A tick label that stays inside the chart: the first is left-aligned, the last right-aligned. */
function EdgeTick(props: {
  x?: number
  y?: number
  payload?: { value: number }
  index?: number
  visibleTicksCount?: number
  long: boolean
}) {
  const { x = 0, y = 0, payload, index = 0, visibleTicksCount = 1, long } = props
  const anchor = index === 0 ? 'start' : index === visibleTicksCount - 1 ? 'end' : 'middle'
  const text = formatLong(payload?.value ?? 0)
  return (
    <text x={x} y={y + 10} fill="#94a3b8" fontSize={10} textAnchor={anchor}>
      {long ? text : text.replace(/ \d{4}$/, '')}
    </text>
  )
}

/**
 * The story over its whole life: history, the forecast with its range, the selected day marked
 * with its date, and optionally what happened afterwards.
 */
export function ForecastChart({
  points,
  asOf,
  color,
  measure,
  height = 160,
}: {
  points: SeriesPoint[]
  asOf: DayIndex
  color: string
  measure: Measure
  height?: number
}) {
  const first = points[0]?.t ?? asOf
  const lastDay = points[points.length - 1]?.t ?? asOf
  const long = lastDay - first > 330 // across a year boundary the year matters
  const ticks = useMemo(() => {
    const span = Math.max(1, lastDay - first)
    // The ends, and the selected day unless it is too close to one of them to read.
    const all = [first, asOf, lastDay]
    return all.filter(
      (t, i) =>
        all.indexOf(t) === i &&
        (i === 0 || i === 2 || Math.min(t - first, lastDay - t) > 0.18 * span),
    )
  }, [first, lastDay, asOf])
  const names = {
    history: measure === 'day' ? 'Articles that day' : 'Articles, 7 days',
    actual: 'What happened',
    forecast: measure === 'day' ? 'Forecast (per day)' : 'Forecast',
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={points} margin={{ top: 14, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke="#334155" strokeOpacity={0.35} vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          domain={['dataMin', 'dataMax']}
          ticks={ticks}
          tick={<EdgeTick long={long} />}
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
                  Math.round(Number(value) * 10) / 10,
                  names[name as keyof typeof names] ?? String(name),
                ]
          }
        />
        <ReferenceLine
          x={asOf}
          stroke="#cbd5e1"
          strokeDasharray="2 3"
          label={{ value: formatLong(asOf), position: 'top', fill: '#e2e8f0', fontSize: 10 }}
        />
        <Area
          dataKey="band"
          stroke="none"
          fill={color}
          fillOpacity={0.22}
          isAnimationActive={false}
          connectNulls
        />
        <Area
          dataKey="history"
          stroke="#e2e8f0"
          strokeWidth={1.5}
          fill="#e2e8f0"
          fillOpacity={0.12}
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
