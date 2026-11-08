// Loads the land layer. Country outlines are vectors (Natural Earth 1:50m, from the world-atlas
// package), so the globe stays sharp at any zoom. The ~750 KB file is fetched as a separate
// static asset rather than being bundled into the JS.
import type { Group } from 'three'
import countriesUrl from 'world-atlas/countries-50m.json?url'
import { buildLandArrays, landObject, type CountriesTopology } from './land'

export async function loadLand(options: { altitude: number; border: string }): Promise<Group> {
  const res = await fetch(countriesUrl)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${res.url}`)
  const topo = (await res.json()) as CountriesTopology
  return landObject(buildLandArrays(topo, options.altitude), options)
}
