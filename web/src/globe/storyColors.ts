// A distinct colour per story, bright enough to read on the dark globe.
import type { RGB } from './heat'

/** Nine well-separated hues (Tailwind 400 shades). */
export const STORY_PALETTE = [
  '#f87171', // red
  '#fb923c', // orange
  '#facc15', // yellow
  '#a3e635', // lime
  '#2dd4bf', // teal
  '#38bdf8', // sky
  '#818cf8', // indigo
  '#c084fc', // purple
  '#f472b6', // pink
] as const

function hash(id: string): number {
  let h = 2166136261 // FNV-1a
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619)
  return h >>> 0
}

/**
 * Colour per story id. The colour comes from a hash of the id, so a story keeps its colour as
 * the time slider moves. If two stories on screen land on the same colour, the later id (in
 * sorted order) takes the next free one. Beyond nine stories, colours repeat.
 */
export function storyColors(ids: string[]): Map<string, string> {
  const n = STORY_PALETTE.length
  const used = new Set<number>()
  const colors = new Map<string, string>()
  for (const id of [...ids].sort()) {
    if (used.size === n) used.clear()
    let slot = hash(id) % n
    while (used.has(slot)) slot = (slot + 1) % n
    used.add(slot)
    colors.set(id, STORY_PALETTE[slot])
  }
  return colors
}

export function hexToRgb(hex: string): RGB {
  const v = parseInt(hex.slice(1), 16)
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255]
}
