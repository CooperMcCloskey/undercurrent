import { useEffect, useRef, useState } from 'react'
import type { StorySummary } from '../api/types.gen'

/** A story as drawn on the globe, part-way to where the current day says it should be. */
export interface EasedStory {
  /** The latest version of the story (kept while it fades out after leaving the day). */
  story: StorySummary
  /** Marker size, 0..1: grows in as the story arrives, shrinks as it leaves or fades. */
  level: number
  /** Heat: articles this week, gliding between days instead of jumping. */
  count: number
}

interface Entry extends EasedStory {
  targetLevel: number
  targetCount: number
}

/** Close enough to the target to stop animating. */
const SETTLED = 0.002

/**
 * Stories with their size and heat eased over time (time constant `tauMs`), so that when the day
 * changes, stories grow in, swell, shrink and fade out smoothly instead of popping. The day can
 * change every frame (playing or dragging the time bar); this just keeps chasing the latest
 * targets, and stops its animation loop once everything has settled.
 *
 * `presence` gives each story's target size (0..1).
 */
export function useEasedStories(
  stories: readonly StorySummary[],
  presence: (story: StorySummary) => number,
  tauMs = 250,
): EasedStory[] {
  const entries = useRef(new Map<string, Entry>())
  const [shown, setShown] = useState<EasedStory[]>([])
  const frame = useRef(0)

  useEffect(() => {
    // New targets: every story in the day heads for its presence and count; the rest head to 0.
    const map = entries.current
    const live = new Set<string>()
    for (const story of stories) {
      live.add(story.id)
      const targetLevel = presence(story)
      const targetCount = story.article_count_7d
      const entry = map.get(story.id)
      if (entry) Object.assign(entry, { story, targetLevel, targetCount })
      else map.set(story.id, { story, level: 0, count: 0, targetLevel, targetCount })
    }
    for (const [id, entry] of map) {
      if (!live.has(id)) Object.assign(entry, { targetLevel: 0, targetCount: 0 })
    }

    // (Re)start the animation loop if it isn't running.
    if (frame.current) return
    let last = performance.now()
    const tick = (now: number) => {
      const k = 1 - Math.exp(-(now - last) / tauMs)
      last = now
      let moving = false
      for (const [id, e] of map) {
        e.level += (e.targetLevel - e.level) * k
        e.count += (e.targetCount - e.count) * k
        const levelDone = Math.abs(e.targetLevel - e.level) < SETTLED
        const countDone =
          Math.abs(e.targetCount - e.count) <= Math.max(0.5, SETTLED * e.targetCount)
        if (levelDone && countDone) {
          e.level = e.targetLevel
          e.count = e.targetCount
          if (e.targetLevel === 0) map.delete(id)
        } else moving = true
      }
      setShown([...map.values()].map(({ story, level, count }) => ({ story, level, count })))
      frame.current = moving ? requestAnimationFrame(tick) : 0
    }
    frame.current = requestAnimationFrame(tick)
  }, [stories, presence, tauMs])

  useEffect(
    () => () => {
      cancelAnimationFrame(frame.current)
      frame.current = 0
    },
    [],
  )
  return shown
}
