// Where to put the marks and labels along the time bar for the days in view. Years are always
// marked (labelled when there is room); as you zoom in, months appear, then days, so there is
// always something readable under the cursor.
import type { DayIndex } from './days'

export interface Tick {
  day: DayIndex
  /** Text under the tick, or null for a bare mark. */
  label: string | null
  /** Year starts: drawn taller and brighter. */
  major: boolean
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MS_PER_DAY = 86_400_000
const dayOf = (year: number, month: number, date = 1): DayIndex =>
  Math.round(Date.UTC(year, month, date) / MS_PER_DAY)
const yearOf = (day: DayIndex) => new Date(day * MS_PER_DAY).getUTCFullYear()

/** Room one label needs, px. */
const LABEL_PX = 44
/** Closest two marks may sit, px. */
const MARK_PX = 7

export function makeTicks(viewStart: DayIndex, viewEnd: DayIndex, widthPx: number): Tick[] {
  const span = Math.max(1, viewEnd - viewStart)
  const pxPerDay = Math.max(widthPx, 1) / span
  const ticks: Tick[] = []
  const inView = (d: DayIndex) => d >= viewStart && d <= viewEnd

  // Years: label every year if they fit, else every 2nd, 5th...
  const yearStep = [1, 2, 5, 10].find((n) => pxPerDay * 365 * n >= LABEL_PX) ?? 10
  for (let y = yearOf(viewStart); y <= yearOf(viewEnd) + 1; y++) {
    const day = dayOf(y, 0)
    if (inView(day)) {
      ticks.push({
        day,
        label: y % yearStep === 0 || yearStep === 1 ? String(y) : null,
        major: true,
      })
    }
  }

  // Months: a mark each when they are 7 px apart or more, named when there is room.
  if (pxPerDay * 28 >= MARK_PX) {
    const named = pxPerDay * 28 >= LABEL_PX
    for (let y = yearOf(viewStart); y <= yearOf(viewEnd) + 1; y++) {
      for (let m = 1; m < 12; m++) {
        const day = dayOf(y, m)
        if (!inView(day)) continue // Januaries are the years above
        ticks.push({ day, label: named ? MONTHS[m] : null, major: false })
      }
    }
  }

  // Days: when a day is 7 px or more, a mark each; numbered every 1, 2, 5 or 10 days as room allows.
  if (pxPerDay >= MARK_PX) {
    const step = [1, 2, 5, 10].find((n) => pxPerDay * n >= 26) ?? 10
    for (let d = Math.ceil(viewStart); d <= viewEnd; d++) {
      const date = new Date(d * MS_PER_DAY).getUTCDate()
      if (date === 1) continue // the month's own mark names it
      ticks.push({
        day: d,
        label: (date - 1) % step === 0 && date + step <= 31 ? String(date) : null,
        major: false,
      })
    }
  }
  return ticks.sort((a, b) => a.day - b.day)
}
