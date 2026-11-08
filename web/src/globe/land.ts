// The land layer (country fills + borders) as two merged three.js objects: 2 draw calls.
//
// react-globe.gl's polygonsData builds a separate mesh + outline for every polygon. The 1:50m
// countries have 1,616 polygons (mostly islands), which meant ~3,200 objects, ~4,800 draw calls
// per frame, and ~3.7 s of main-thread triangulation at load. Building one merged mesh with
// earcut is a few hundred ms, and the result is excluded from hover raycasting.
//
// buildLandArrays is pure (no DOM), so it can move into a Web Worker if load time ever matters.
import earcut from 'earcut'
import { feature, mesh } from 'topojson-client'
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshLambertMaterial,
} from 'three'
import type { Position } from 'geojson'
import type { GeometryCollection, Topology } from 'topojson-specification'

/** three-globe's internal globe radius (what globe.getGlobeRadius() returns). */
export const GLOBE_RADIUS = 100
/** Subdivide edges longer than this (degrees) so flat triangles and lines hug the sphere. */
const MAX_EDGE_DEG = 3
const MAX_EDGE_SQ = MAX_EDGE_DEG * MAX_EDGE_DEG
const DEG = Math.PI / 180

export type CountriesTopology = Topology<{ countries: GeometryCollection<{ name: string }> }>

export interface LandArrays {
  fillPositions: Float32Array
  fillNormals: Float32Array
  fillIndex: Uint32Array
  borderPositions: Float32Array
}

/** Same formula as three-globe's polar2Cartesian, so land lines up with every other layer. */
function pushXYZ(out: number[], lng: number, lat: number, r: number): void {
  const phi = (90 - lat) * DEG
  const theta = (90 - lng) * DEG
  const s = Math.sin(phi)
  out.push(r * s * Math.cos(theta), r * Math.cos(phi), r * s * Math.sin(theta))
}

/** Make longitudes continuous across the antimeridian (Russia, Fiji). `turns` != 0 means the ring circles a pole. */
function unwrap(ring: Position[]): { pts: [number, number][]; turns: number } {
  const pts: [number, number][] = []
  let offset = 0
  let prev = ring[0][0]
  for (const [lng, lat] of ring) {
    if (lng - prev > 180) offset -= 360
    else if (prev - lng > 180) offset += 360
    prev = lng
    pts.push([lng + offset, lat])
  }
  return { pts, turns: offset }
}

const isPoleRing = (ring: Position[]) => ring.every(([, lat]) => Math.abs(lat) > 89.99)

/** Triangulate one polygon (outer ring + holes) and append it to the shared buffers. */
function addPolygon(rings: Position[][], r: number, pos: number[], index: number[]): void {
  // Planar lng/lat works for everything except polygons that contain a pole (Antarctica),
  // which are stored as a degenerate ring at the pole plus the coastline. Those get triangulated
  // in an azimuthal projection centred on the pole instead.
  let pole = 0
  const poleRing = rings.find(isPoleRing)
  if (poleRing) pole = Math.sign(poleRing[0][1])
  const unwrapped = rings.filter((ring) => !isPoleRing(ring)).map(unwrap)
  if (!pole && unwrapped.some((u) => u.turns !== 0)) {
    pole = Math.sign(unwrapped[0].pts.reduce((sum, [, lat]) => sum + lat, 0))
  }
  if (!unwrapped.length) return

  const toPlane = pole
    ? ([lng, lat]: [number, number]) => {
        const d = 90 - pole * lat
        return [d * Math.cos(lng * DEG), d * Math.sin(lng * DEG)]
      }
    : (p: [number, number]) => p
  const toLngLat = pole
    ? (u: number, v: number): [number, number] => [
        Math.atan2(v, u) / DEG,
        pole * (90 - Math.hypot(u, v)),
      ]
    : (u: number, v: number): [number, number] => [u, v]

  // Plane coordinates, without each ring's closing duplicate point.
  const plane: number[] = []
  const holes: number[] = []
  unwrapped.forEach(({ pts }, i) => {
    if (i > 0) holes.push(plane.length / 2)
    for (let k = 0; k < pts.length - 1; k++) plane.push(...toPlane(pts[k]))
  })

  // Longest-edge bisection until every edge is short. Splitting always happens at an edge's
  // midpoint, so triangles that share an edge split it identically and no cracks appear.
  const midpoints = new Map<number, number>()
  const midpoint = (a: number, b: number): number => {
    const key = a < b ? a * 1_048_576 + b : b * 1_048_576 + a
    let m = midpoints.get(key)
    if (m === undefined) {
      m = plane.length / 2
      plane.push((plane[2 * a] + plane[2 * b]) / 2, (plane[2 * a + 1] + plane[2 * b + 1]) / 2)
      midpoints.set(key, m)
    }
    return m
  }
  const distSq = (a: number, b: number) =>
    (plane[2 * a] - plane[2 * b]) ** 2 + (plane[2 * a + 1] - plane[2 * b + 1]) ** 2

  const stack = earcut(plane, holes, 2)
  const tris: number[] = []
  while (stack.length) {
    const c = stack.pop()!
    const b = stack.pop()!
    const a = stack.pop()!
    const ab = distSq(a, b)
    const bc = distSq(b, c)
    const ca = distSq(c, a)
    const longest = Math.max(ab, bc, ca)
    if (longest <= MAX_EDGE_SQ) tris.push(a, b, c)
    else if (longest === ab) {
      const m = midpoint(a, b)
      stack.push(a, m, c, m, b, c)
    } else if (longest === bc) {
      const m = midpoint(b, c)
      stack.push(a, b, m, a, m, c)
    } else {
      const m = midpoint(c, a)
      stack.push(a, b, m, m, b, c)
    }
  }

  const base = pos.length / 3
  for (let i = 0; i < plane.length / 2; i++) {
    const [lng, lat] = toLngLat(plane[2 * i], plane[2 * i + 1])
    pushXYZ(pos, lng, lat, r)
  }
  // Wind every triangle to face outwards, so back-face culling works.
  for (let t = 0; t < tris.length; t += 3) {
    const [a, b, c] = [tris[t] + base, tris[t + 1] + base, tris[t + 2] + base]
    const [ax, ay, az] = [pos[3 * a], pos[3 * a + 1], pos[3 * a + 2]]
    const [ux, uy, uz] = [pos[3 * b] - ax, pos[3 * b + 1] - ay, pos[3 * b + 2] - az]
    const [vx, vy, vz] = [pos[3 * c] - ax, pos[3 * c + 1] - ay, pos[3 * c + 2] - az]
    // (u x v) . a: positive when the triangle faces away from the globe's centre.
    const outward = (uy * vz - uz * vy) * ax + (uz * vx - ux * vz) * ay + (ux * vy - uy * vx) * az
    if (outward >= 0) index.push(a, b, c)
    else index.push(a, c, b)
  }
}

function addBorders(lines: Position[][], r: number, out: number[]): void {
  for (const line of lines) {
    const { pts } = unwrap(line)
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1]
      const [x1, y1] = pts[i]
      if (Math.abs(y0) > 89.99 && Math.abs(y1) > 89.99) continue // degenerate edge at a pole
      const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / MAX_EDGE_DEG))
      for (let k = 0; k < n; k++) {
        pushXYZ(out, x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n, r)
        pushXYZ(out, x0 + ((x1 - x0) * (k + 1)) / n, y0 + ((y1 - y0) * (k + 1)) / n, r)
      }
    }
  }
}

/** Pure geometry build. `altitude` is relative to the globe radius, like other globe layers. */
export function buildLandArrays(topo: CountriesTopology, altitude: number): LandArrays {
  const r = GLOBE_RADIUS * (1 + altitude)
  const pos: number[] = []
  const index: number[] = []
  for (const country of feature(topo, topo.objects.countries).features) {
    const g = country.geometry
    if (g.type === 'Polygon') addPolygon(g.coordinates, r, pos, index)
    else if (g.type === 'MultiPolygon') for (const p of g.coordinates) addPolygon(p, r, pos, index)
  }
  const fillPositions = new Float32Array(pos)
  const fillNormals = fillPositions.map((v) => v / r)

  // Every shared border and coastline exactly once (per-country outlines draw borders twice).
  const borders: number[] = []
  addBorders(mesh(topo, topo.objects.countries).coordinates, r * 1.0001, borders)

  return {
    fillPositions,
    fillNormals,
    fillIndex: new Uint32Array(index),
    borderPositions: new Float32Array(borders),
  }
}

// The land's colour: natural greens by latitude (rainforest, savanna, temperate green, northern
// forest, tundra, ice), with the big deserts in tan so it isn't a set of flat bands. Dark enough that the story markers stay the brightest
// things on the globe, and no more muted than it has to be: grey-olive earth looked dusty.
const LAND_STOPS: [number, string][] = [
  [0, '#2c6a3b'],
  [12, '#3c7a3f'],
  [22, '#6a8a45'],
  [32, '#5f8444'],
  [45, '#3b7a46'],
  [58, '#2e5f48'],
  [68, '#6c8878'],
  [75, '#cddce6'],
  [90, '#f0f6fa'],
]
const stopColors = LAND_STOPS.map(([lat, hex]) => ({ lat, color: new Color(hex) }))
const DESERT = new Color('#b39b64')

/** Smooth 2D value noise in 0..1, deterministic, for where the deserts are. */
function noise2(x: number, y: number): number {
  const hash = (i: number, j: number) => {
    const h = Math.sin(i * 127.1 + j * 311.7) * 43758.5453
    return h - Math.floor(h)
  }
  const [ix, iy] = [Math.floor(x), Math.floor(y)]
  const [fx, fy] = [x - ix, y - iy]
  const [u, v] = [fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)]
  const top = hash(ix, iy) * (1 - u) + hash(ix + 1, iy) * u
  const bottom = hash(ix, iy + 1) * (1 - u) + hash(ix + 1, iy + 1) * u
  return top * (1 - v) + bottom * v
}

// The big deserts, as ellipses (centre, and half-width in degrees of latitude and longitude): the
// colour is tan inside and fades out at the edge, broken up by noise so they aren't clean ovals.
const DESERTS: { lat: number; lng: number; rLat: number; rLng: number }[] = [
  { lat: 23, lng: 10, rLat: 8, rLng: 27 }, // Sahara
  { lat: 24, lng: 45, rLat: 8, rLng: 10 }, // Arabia
  { lat: 33, lng: 55, rLat: 5, rLng: 10 }, // Iran
  { lat: 42, lng: 95, rLat: 6, rLng: 17 }, // Gobi and Taklamakan
  { lat: 27, lng: 71, rLat: 4, rLng: 5 }, // Thar
  { lat: -25, lng: 134, rLat: 9, rLng: 14 }, // Australian outback
  { lat: -24, lng: 20, rLat: 7, rLng: 7 }, // Kalahari and Namib
  { lat: 31, lng: -111, rLat: 6, rLng: 6 }, // Sonoran and Chihuahuan
  { lat: -23, lng: -69, rLat: 6, rLng: 3 }, // Atacama
]

function desertAmount(lat: number, lng: number): number {
  let best = 0
  for (const d of DESERTS) {
    const dist = Math.hypot((lat - d.lat) / d.rLat, (lng - d.lng) / d.rLng)
    best = Math.max(best, 1 - Math.min(1, dist))
  }
  const ragged = 0.6 + 0.8 * noise2(lng / 6, lat / 4) // breaks up the edge
  return Math.min(1, Math.max(0, best * 1.6 * ragged))
}

/** The land colour (linear RGB) at a place: the latitude blend, with the deserts in tan. */
export function landColorAt(lat: number, lng = 0): [number, number, number] {
  const a = Math.min(90, Math.abs(lat))
  let i = stopColors.findIndex((s) => s.lat >= a)
  if (i <= 0) i = 1
  const lo = stopColors[i - 1]
  const hi = stopColors[i]
  const k = (a - lo.lat) / (hi.lat - lo.lat)
  const c = lo.color.clone().lerp(hi.color, Math.min(1, Math.max(0, k)))
  c.lerp(DESERT, desertAmount(lat, lng) * 0.9)
  return [c.r, c.g, c.b]
}

export function landObject(arrays: LandArrays, colors: { border: string }): Group {
  const fillGeometry = new BufferGeometry()
  fillGeometry.setAttribute('position', new BufferAttribute(arrays.fillPositions, 3))
  const vertexColors = new Float32Array(arrays.fillPositions.length)
  for (let i = 0; i < arrays.fillPositions.length; i += 3) {
    const [x, y, z] = [
      arrays.fillPositions[i],
      arrays.fillPositions[i + 1],
      arrays.fillPositions[i + 2],
    ]
    const lat = Math.asin(y / Math.hypot(x, y, z)) / DEG
    const lng = 90 - Math.atan2(z, x) / DEG // the inverse of pushXYZ
    vertexColors.set(landColorAt(lat, lng), i)
  }
  fillGeometry.setAttribute('color', new BufferAttribute(vertexColors, 3))
  fillGeometry.setAttribute('normal', new BufferAttribute(arrays.fillNormals, 3))
  fillGeometry.setIndex(new BufferAttribute(arrays.fillIndex, 1))
  // Push the fill back slightly in depth so borders always win and never flicker.
  const fill = new Mesh(
    fillGeometry,
    new MeshLambertMaterial({
      vertexColors: true,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    }),
  )

  const borderGeometry = new BufferGeometry()
  borderGeometry.setAttribute('position', new BufferAttribute(arrays.borderPositions, 3))
  const borders = new LineSegments(borderGeometry, new LineBasicMaterial({ color: colors.border }))

  // Never a hover/click target: skips raycasting ~100k triangles on every mouse move.
  fill.raycast = () => {}
  borders.raycast = () => {}

  const group = new Group()
  group.add(fill, borders)
  return group
}
