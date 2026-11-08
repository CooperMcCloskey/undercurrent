// Story categories (from the contract) -> colour and label. Everything on the globe that
// identifies what a story is about (heat, markers, legend, tooltip) uses these.
import type { StorySummary } from '../api/types.gen'
import type { RGB } from './heat'

export type Category = StorySummary['category']

// Typed as Record<Category, ...>, so adding a category to contracts.py without a colour here is
// a type error after `npm run gen:types`.
export const CATEGORY_STYLE: Record<Category, { label: string; color: string }> = {
  politics: { label: 'Politics', color: '#818cf8' }, // indigo
  conflict: { label: 'Conflict', color: '#f87171' }, // red
  economy: { label: 'Economy', color: '#facc15' }, // yellow
  society: { label: 'Society & crime', color: '#f472b6' }, // pink
  disaster: { label: 'Disaster & climate', color: '#fb923c' }, // orange
  health: { label: 'Health', color: '#2dd4bf' }, // teal
  technology: { label: 'Science & tech', color: '#a3e635' }, // lime
  sport: { label: 'Sport', color: '#38bdf8' }, // sky blue
  culture: { label: 'Culture & entertainment', color: '#c084fc' }, // purple
  other: { label: 'Other', color: '#94a3b8' }, // slate
}

export const CATEGORY_ORDER = Object.keys(CATEGORY_STYLE) as Category[]

export function hexToRgb(hex: string): RGB {
  const v = parseInt(hex.slice(1), 16)
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255]
}
