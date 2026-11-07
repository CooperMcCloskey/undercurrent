// Days as integers (days since 1970-01-01, UTC), so the time bar can do plain arithmetic and
// nothing depends on the viewer's time zone. The contract's dates are 'YYYY-MM-DD' strings.

const MS_PER_DAY = 86_400_000

export type DayIndex = number

export function toIndex(day: string): DayIndex {
  const [y, m, d] = day.split('-').map(Number)
  return Math.round(Date.UTC(y, m - 1, d) / MS_PER_DAY)
}

export function toDay(index: DayIndex): string {
  return new Date(index * MS_PER_DAY).toISOString().slice(0, 10)
}

/** The same calendar day `years` earlier (29 Feb falls back to 1 Mar). */
export function yearsBefore(index: DayIndex, years: number): DayIndex {
  const date = new Date(index * MS_PER_DAY)
  date.setUTCFullYear(date.getUTCFullYear() - years)
  return Math.round(date.getTime() / MS_PER_DAY)
}

// No weekday: while the bar plays or is dragged it flickers through every name and reads as noise.
const LONG = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

/** "6 Nov 2026" */
export const formatLong = (index: DayIndex) => LONG.format(index * MS_PER_DAY)
