import type { LandMask } from './land'

/** Between stories at one place: two big markers' radii, and a gap, in degrees. */
export const RING_SPACING_DEG = 3.2

/**
 * Where the stories after the first one at a place go: on rings round it, RING_SPACING_DEG apart.
 * A place on land keeps them on land (a ring round Washington would otherwise put half of them in
 * the Atlantic), so the slots are the ring positions that are land, nearest ring first; only if
 * there aren't enough are the others used. A place at sea, or no land mask yet, uses every slot.
 */
export function freeSpots(
  lat: number,
  lng: number,
  count: number,
  mask: LandMask | null,
): { lat: number; lng: number }[] {
  const keepOnLand = mask !== null && mask.isLand(lat, lng)
  const cos = Math.max(Math.cos((lat * Math.PI) / 180), 0.2)
  const land: { lat: number; lng: number }[] = []
  const sea: { lat: number; lng: number }[] = []
  for (let ring = 1; ring <= 12 && land.length < count; ring++) {
    const n = 6 * ring
    for (let j = 0; j < n; j++) {
      const angle = (2 * Math.PI * (j + (ring % 2) / 2)) / n
      const spot = {
        lat: lat + RING_SPACING_DEG * ring * Math.sin(angle),
        lng: lng + (RING_SPACING_DEG * ring * Math.cos(angle)) / cos,
      }
      if (Math.abs(spot.lat) > 85) continue
      ;(!keepOnLand || mask.isLand(spot.lat, spot.lng) ? land : sea).push(spot)
    }
  }
  return [...land, ...sea].slice(0, count)
}
