// Loads the land layer. Country outlines are vectors (Natural Earth 1:50m, from the world-atlas
// package), so the globe stays sharp at any zoom. The ~750 KB file is fetched as a separate
// static asset rather than being bundled into the JS.
import type { Group } from 'three'
import countriesUrl from 'world-atlas/countries-50m.json?url'
import {
  buildLandArrays,
  buildLandMask,
  landObject,
  type CountriesTopology,
  type LandMask,
} from './land'

export async function loadLand(options: {
  altitude: number
  land: string
  border: string
}): Promise<{ object: Group; mask: LandMask | null }> {
  const res = await fetch(countriesUrl)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${res.url}`)
  const topo = (await res.json()) as CountriesTopology
  const object = landObject(buildLandArrays(topo, options.altitude), options)
  // The mask only keeps crowded markers off the sea: if it can't be built, draw the land without it.
  let mask: LandMask | null = null
  try {
    mask = buildLandMask(topo)
  } catch (error) {
    console.warn('Land mask not built; markers may be placed over the sea', error)
  }
  return { object, mask }
}
