import { useCallback, useEffect, useRef, useState } from 'react'
import { Pause, Play, ZoomOut } from 'lucide-react'
import { formatLong, type DayIndex } from './days'
import { makeTicks } from './ticks'

/**
 * Playing crosses whatever part of the bar is in view in about this long, so the further you have
 * zoomed in, the slower time runs: the whole 5 years in two minutes (14 days a second), one year
 * in about 2 minutes at 2.8 days a second, a month at the floor below.
 */
const SWEEP_SECONDS = 130
const MIN_DAYS_PER_SECOND = 0.5
const MIN_VIEW_DAYS = 7

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** "14 days/s", or "1 day per 2 s" when slower than a day a second. */
function describeSpeed(daysPerSecond: number): string {
  if (daysPerSecond >= 1) return `${Math.round(daysPerSecond)} days/s`
  return `1 day per ${Math.round(1 / daysPerSecond)} s`
}

/** "5.0 years", "7 months", "21 days": how much time the bar shows. */
function describeSpan(days: number): string {
  if (days >= 548) return `${(days / 365).toFixed(1)} years`
  if (days >= 60) return `${Math.round(days / 30.4)} months`
  return `${Math.round(days)} days`
}

interface View {
  start: DayIndex
  span: number
}

/**
 * The time bar: a thin white line with a white circle at the day shown, years (then months, then
 * days, as you zoom in) marked and labelled under it. Drag or click it to pick a day. Scroll over
 * it to zoom around the pointer, scroll sideways to move along it. Playing runs slower the more
 * you have zoomed in. Keys: arrows ±1 day, Shift ±30, PageUp/PageDown ±1 year, Home/End, Space to
 * play, + and - to zoom, 0 to see everything.
 */
export function TimeBar({
  start,
  end,
  value,
  onChange,
  futureFrom,
  className = '',
}: {
  start: DayIndex
  end: DayIndex
  value: DayIndex
  onChange: (day: DayIndex) => void
  /** The last day with data: the bar past it is the forecast, drawn in amber. */
  futureFrom?: DayIndex
  className?: string
}) {
  const full = Math.max(1, end - start)
  const set = useCallback(
    (d: DayIndex) => onChange(Math.min(end, Math.max(start, Math.round(d)))),
    [onChange, start, end],
  )

  // The part of the bar in view.
  const clampView = useCallback(
    (v: View): View => {
      const span = clamp(v.span, Math.min(MIN_VIEW_DAYS, full), full)
      return { start: clamp(v.start, start, end - span), span }
    },
    [start, end, full],
  )
  const [view, setView] = useState<View>({ start, span: full })
  const viewRef = useRef(view)
  viewRef.current = view
  useEffect(() => setView({ start, span: full }), [start, full]) // a new range: show it all
  const viewEnd = view.start + view.span
  const zoomed = view.span < full - 0.5
  const pct = (day: DayIndex) => ((day - view.start) / view.span) * 100

  const track = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  useEffect(() => {
    const el = track.current
    if (!el) return
    const observer = new ResizeObserver(() => setWidth(el.getBoundingClientRect().width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  const ticks = makeTicks(view.start, viewEnd, width)

  const zoomBy = useCallback(
    (factor: number, anchorFraction: number, anchorDay?: DayIndex) =>
      setView((v) => {
        const anchor = anchorDay ?? v.start + anchorFraction * v.span
        const span = clamp(v.span * factor, Math.min(MIN_VIEW_DAYS, full), full)
        return clampView({ start: anchor - anchorFraction * span, span })
      }),
    [clampView, full],
  )
  // Wheel: zoom around the pointer; sideways scrolling moves along the bar. Needs a native
  // listener, because React's onWheel is passive and can't stop the page scrolling.
  useEffect(() => {
    const el = track.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        setView((v) =>
          clampView({ start: v.start + (e.deltaX / rect.width) * v.span, span: v.span }),
        )
      } else {
        zoomBy(
          Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)),
          (e.clientX - rect.left) / rect.width,
        )
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [clampView, zoomBy])

  const [dragging, setDragging] = useState(false)
  const dayAt = (clientX: number) => {
    const rect = track.current!.getBoundingClientRect()
    return view.start + clamp((clientX - rect.left) / rect.width, 0, 1) * view.span
  }

  // Playing advances by real time, so the speed doesn't depend on the frame rate. The speed is
  // read every frame, so zooming while it plays changes it at once.
  const daysPerSecond = Math.max(MIN_DAYS_PER_SECOND, view.span / SWEEP_SECONDS)
  const speed = useRef(daysPerSecond)
  speed.current = daysPerSecond
  const [playing, setPlaying] = useState(false)
  const position = useRef(value)
  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    let frame = requestAnimationFrame(function tick(now) {
      position.current = Math.min(end, position.current + ((now - last) / 1000) * speed.current)
      last = now
      set(position.current)
      if (position.current >= end) setPlaying(false)
      else frame = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(frame)
  }, [playing, end, set])
  const togglePlay = () => {
    if (!playing) position.current = value >= end ? start : value
    setPlaying(!playing)
  }

  // Keep the day shown in view: when it leaves (playing, or picked some other way), bring it back,
  // near the left while playing so the most time is visible ahead of it.
  useEffect(() => {
    setView((v) => {
      const ahead = v.start + v.span * (playing ? 0.92 : 1)
      if (value >= v.start && value <= ahead) return v
      return clampView({ start: value - v.span * (playing ? 0.1 : 0.5), span: v.span })
    })
  }, [value, playing, clampView])

  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 30 : 1
    const moves: Record<string, DayIndex> = {
      ArrowLeft: value - step,
      ArrowDown: value - step,
      ArrowRight: value + step,
      ArrowUp: value + step,
      PageDown: value - 365,
      PageUp: value + 365,
      Home: start,
      End: end,
    }
    if (e.key === ' ') {
      e.preventDefault()
      togglePlay()
    } else if (e.key === '+' || e.key === '=' || e.key === '-' || e.key === '_') {
      e.preventDefault()
      const day = clamp(value, view.start, viewEnd)
      zoomBy(e.key === '+' || e.key === '=' ? 0.6 : 1 / 0.6, (day - view.start) / view.span, day)
    } else if (e.key === '0') {
      e.preventDefault()
      setView({ start, span: full })
    } else if (e.key in moves) {
      e.preventDefault()
      setPlaying(false)
      set(moves[e.key])
    }
  }

  const futurePct = futureFrom === undefined ? null : clamp(pct(futureFrom), 0, 100)
  const markerPct = pct(value)
  return (
    <div className={`flex select-none items-start gap-3 ${className}`}>
      <button
        className="rounded-full p-1.5 text-white/70 hover:bg-white/10 hover:text-white"
        onClick={togglePlay}
        aria-label={playing ? 'Pause' : 'Play'}
        title={playing ? 'Pause (Space)' : 'Play (Space)'}
      >
        {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
      </button>
      <div className="min-w-0 flex-1">
        <div
          ref={track}
          role="slider"
          tabIndex={0}
          aria-label="Day shown on the globe"
          aria-valuemin={start}
          aria-valuemax={end}
          aria-valuenow={value}
          aria-valuetext={formatLong(value)}
          title="Scroll to zoom, drag to pick a day"
          className="group relative h-6 cursor-pointer touch-none outline-none"
          onKeyDown={onKeyDown}
          onPointerDown={(e) => {
            if (e.button !== 0) return
            e.currentTarget.setPointerCapture(e.pointerId)
            setPlaying(false)
            setDragging(true)
            set(dayAt(e.clientX))
          }}
          onPointerMove={(e) => dragging && set(dayAt(e.clientX))}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
        >
          <span className="absolute inset-x-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-white/30" />
          {futurePct !== null && futurePct < 100 && (
            <span
              className="absolute right-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-amber-300/50"
              style={{ left: `${futurePct}%` }}
              title="Forecast: beyond the last day with data"
            />
          )}
          <span
            className={`absolute left-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full ${futureFrom !== undefined && value > futureFrom ? 'bg-amber-300' : 'bg-white'}`}
            style={{ width: `${clamp(markerPct, 0, 100)}%` }}
          />
          {ticks.map((t) => (
            <span
              key={t.day}
              className={`absolute top-1/2 mt-1 w-px ${t.major ? 'h-2.5 bg-white/70' : 'h-1.5 bg-white/35'}`}
              style={{ left: `${pct(t.day)}%` }}
            />
          ))}
          <span
            className={`absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.5)] transition-transform group-focus-visible:ring-2 group-focus-visible:ring-white/50 ${dragging ? 'scale-125' : ''}`}
            style={{
              left: `${markerPct}%`,
              display: markerPct < -1 || markerPct > 101 ? 'none' : undefined,
            }}
          />
        </div>
        <div className="pointer-events-none relative h-4 text-[11px] leading-4 text-white/60">
          {ticks.map(
            (t) =>
              t.label && (
                <span
                  key={t.day}
                  className={`absolute -translate-x-1/2 whitespace-nowrap tabular-nums ${t.major ? 'font-medium text-white/85' : ''}`}
                  style={{ left: `${clamp(pct(t.day), 1.5, 98.5)}%` }}
                >
                  {t.label}
                </span>
              ),
          )}
        </div>
      </div>
      <div className="flex w-44 shrink-0 items-center justify-end gap-1.5 pt-1 text-[11px] tabular-nums text-white/50">
        <span title="How much time the bar shows, and how fast it plays">
          {zoomed ? `${describeSpan(view.span)} in view · ` : 'Scroll to zoom · '}
          {describeSpeed(daysPerSecond)}
        </span>
        {zoomed && (
          <button
            className="rounded p-0.5 text-white/70 hover:bg-white/10 hover:text-white"
            onClick={() => setView({ start, span: full })}
            aria-label="Show the whole range (0)"
            title="Show the whole range (0)"
          >
            <ZoomOut className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}
