import { useCallback, useEffect, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { formatLong, type DayIndex } from './days'

/** Playback speed: two weeks per second sweeps the 5 years in about two minutes. */
const DAYS_PER_SECOND = 14

/**
 * The time bar: a thin white line with a white circle at the day shown. Drag or click it, or use
 * the keys (arrows ±1 day, Shift ±30, PageUp/PageDown ±1 year, Home/End, Space to play). The date
 * itself is in the header, not here.
 */
export function TimeBar({
  start,
  end,
  value,
  onChange,
  className = '',
}: {
  start: DayIndex
  end: DayIndex
  value: DayIndex
  onChange: (day: DayIndex) => void
  className?: string
}) {
  const span = Math.max(1, end - start)
  const pct = ((value - start) / span) * 100
  const set = useCallback(
    (d: DayIndex) => onChange(Math.min(end, Math.max(start, Math.round(d)))),
    [onChange, start, end],
  )

  const track = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const dayAt = (clientX: number) => {
    const rect = track.current!.getBoundingClientRect()
    return start + ((clientX - rect.left) / rect.width) * span
  }

  // Playing advances by real time, so the speed doesn't depend on the frame rate.
  const [playing, setPlaying] = useState(false)
  const position = useRef(value)
  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    let frame = requestAnimationFrame(function tick(now) {
      position.current = Math.min(end, position.current + ((now - last) / 1000) * DAYS_PER_SECOND)
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
    } else if (e.key in moves) {
      e.preventDefault()
      setPlaying(false)
      set(moves[e.key])
    }
  }

  return (
    <div className={`flex select-none items-center gap-3 ${className}`}>
      <button
        className="rounded-full p-1.5 text-white/70 hover:bg-white/10 hover:text-white"
        onClick={togglePlay}
        aria-label={playing ? 'Pause' : 'Play'}
        title={playing ? 'Pause (Space)' : 'Play (Space)'}
      >
        {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
      </button>
      <div
        ref={track}
        role="slider"
        tabIndex={0}
        aria-label="Day shown on the globe"
        aria-valuemin={start}
        aria-valuemax={end}
        aria-valuenow={value}
        aria-valuetext={formatLong(value)}
        className="group relative h-6 flex-1 cursor-pointer touch-none outline-none"
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
        <span
          className="absolute left-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-white"
          style={{ width: `${pct}%` }}
        />
        <span
          className={`absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.5)] transition-transform group-focus-visible:ring-2 group-focus-visible:ring-white/50 ${dragging ? 'scale-125' : ''}`}
          style={{ left: `${pct}%` }}
        />
      </div>
    </div>
  )
}
