// Place labels for the selected story: a few light pills ("Istanbul 5") pinned under the places its
// articles mention, in globe.gl's html layer. Only the busiest places get one, and each frame a pill
// that would overlap a busier one on screen is hidden, so a story naming lots of nearby places stays
// readable, and zooming in brings the hidden ones back. The rest are still there as dots (hover one
// for its article) and in the story panel.
import type { GlobeMethods } from 'react-globe.gl'
import { shortPlaceName, type PlaceLabel } from '../story/articles'

/** At most this many places are labelled; the busiest first. */
export const MAX_PLACE_LABELS = 5

export interface GlobeLabel extends PlaceLabel {
  /** The story's colour, for the pill's border. */
  color: string
  /** Set when globe.gl creates the label's element: the anchor it positions, and the pill. */
  el?: HTMLDivElement
  pill?: HTMLSpanElement
}

// The pill hangs this far below its place (Tailwind top-2), centred on it.
const BELOW_PX = 8
// Space kept clear around each pill.
const GAP_PX = 4

/** globe.gl's htmlElement accessor: a zero-size anchor at the place, with the pill under it. */
export function labelElement(o: object): HTMLElement {
  const label = o as GlobeLabel
  const name = shortPlaceName(label.name)
  const pill = document.createElement('span')
  pill.className =
    'absolute left-0 top-2 -translate-x-1/2 whitespace-nowrap rounded-full border bg-slate-950/45 px-2 text-[10px] font-medium leading-4 text-slate-100'
  pill.style.borderColor = `${label.color}99` // the story's colour, 60% opaque
  pill.textContent = name // textContent, never innerHTML: names come from scraped data
  if (label.mentions > 1) {
    const count = document.createElement('span')
    count.className = 'ml-1 text-slate-400'
    count.textContent = String(label.mentions)
    pill.append(count)
  }
  pill.title = `${label.mentions} ${label.mentions === 1 ? 'article mentions' : 'articles mention'} ${name}`

  const el = document.createElement('div')
  el.style.pointerEvents = 'none'
  el.append(pill)
  label.el = el
  label.pill = pill
  return el
}

interface Box {
  left: number
  right: number
  top: number
  bottom: number
}

/**
 * Shows each label unless it faces away from the camera or overlaps a busier label on screen
 * (`labels` must be busiest first). Cheap enough to run every frame, which it needs to: the pills'
 * screen positions change whenever the globe turns or zooms.
 */
export function cullLabels(
  globe: GlobeMethods,
  labels: readonly GlobeLabel[],
  altitude: number,
): void {
  if (!labels.length) return
  const cam = globe.camera().position
  const placed: Box[] = []
  for (const label of labels) {
    const { el, pill } = label
    if (!el || !pill) continue
    // On the far side of the globe (globe.gl hides those too, but they mustn't block others).
    const p = globe.getCoords(label.lat, label.lng, altitude)
    let show = p.x * (cam.x - p.x) + p.y * (cam.y - p.y) + p.z * (cam.z - p.z) > 0
    if (show) {
      const { x, y } = globe.getScreenCoords(label.lat, label.lng, altitude)
      const w = pill.offsetWidth || 8 + 6 * (label.name.length + 3) // estimate until it's laid out
      const h = pill.offsetHeight || 18
      const box = {
        left: x - w / 2 - GAP_PX,
        right: x + w / 2 + GAP_PX,
        top: y + BELOW_PX - GAP_PX,
        bottom: y + BELOW_PX + h + GAP_PX,
      }
      show = !placed.some(
        (b) => b.left < box.right && box.left < b.right && b.top < box.bottom && box.top < b.bottom,
      )
      if (show) placed.push(box)
    }
    const visibility = show ? 'visible' : 'hidden' // not display: hidden pills keep their size
    if (el.style.visibility !== visibility) el.style.visibility = visibility
  }
}
