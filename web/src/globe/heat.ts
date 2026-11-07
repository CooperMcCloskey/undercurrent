// Heatmap of where stories are happening, as one transparent sphere over the land.
// Each story has its own colour; where stories overlap, their colours blend by heat.
//
// Heat is computed on the CPU into a 0.5° lat/lng grid, and only when the data changes
// (tens of ms: each point touches only the cells within 3 sigma of it). The GPU then draws
// that grid every frame. This is cheaper than react-globe.gl's heatmapsData, which rebuilds a
// ~130k-vertex sphere and recomputes the density at every vertex on each update.
import {
  ClampToEdgeWrapping,
  DataTexture,
  LinearFilter,
  Mesh,
  RepeatWrapping,
  RGBAFormat,
  ShaderMaterial,
  SphereGeometry,
  UnsignedByteType,
} from 'three'
import { GLOBE_RADIUS } from './land'

export type RGB = readonly [number, number, number] // 0..1

export interface HeatPoint {
  lat: number
  lon: number
  /** Total heat this point adds, e.g. the story's article count x its footprint weight. */
  weight: number
  /** How far it spreads (1 sigma of the Gaussian), in km. */
  radiusKm: number
  /** The story's colour. */
  color: RGB
}

const CELL_DEG = 0.5
const W = 360 / CELL_DEG
const H = 180 / CELL_DEG
const DEG = Math.PI / 180
const EARTH_KM = 6371
/** A point with this radius keeps its full weight as peak heat; wider ones peak lower. */
const REFERENCE_KM = 150
/**
 * How much wider points dim: peak = weight * (REFERENCE_KM / radius) ^ FALLOFF.
 * 2 conserves heat per area, but then country-wide footprints vanish next to city hot spots.
 * 0.5 (picked by eye on the globe) keeps a story's regional spread visible as a glow.
 */
const FALLOFF = 0.5

/** Per cell: total heat, and the sum of colour x heat (so colour = sum / heat). */
export interface HeatGrid {
  heat: Float32Array
  red: Float32Array
  green: Float32Array
  blue: Float32Array
}

export function createHeatGrid(): HeatGrid {
  const n = W * H
  return {
    heat: new Float32Array(n),
    red: new Float32Array(n),
    green: new Float32Array(n),
    blue: new Float32Array(n),
  }
}

/**
 * Sum of Gaussians on the sphere into `grid` (row 0 = lat -90, column 0 = lng -180), with each
 * point's colour weighted by its heat. Returns the maximum heat.
 *
 * Distances use the local approximation dy = dlat, dx = dlng * cos(lat). That's accurate at these
 * radii, except for points within a few degrees of a pole, where heat across the pole is
 * underestimated. Along a row, consecutive Gaussian values are produced by multiplying by a running
 * ratio, so the inner loop has no Math.exp.
 */
export function accumulateHeat(points: HeatPoint[], grid: HeatGrid): number {
  const { heat, red, green, blue } = grid
  heat.fill(0)
  red.fill(0)
  green.fill(0)
  blue.fill(0)
  for (const p of points) {
    if (!(p.weight > 0)) continue
    const [cr, cg, cb] = p.color
    const radiusKm = Math.max(p.radiusKm, 10)
    const sigma = radiusKm / EARTH_KM // radians
    const twoSigmaSq = 2 * sigma * sigma
    const reachDeg = (3 * sigma) / DEG
    const amplitude = p.weight * (REFERENCE_KM / radiusKm) ** FALLOFF

    const row0 = Math.max(0, Math.floor((p.lat - reachDeg + 90) / CELL_DEG))
    const row1 = Math.min(H - 1, Math.floor((p.lat + reachDeg + 90) / CELL_DEG))
    for (let row = row0; row <= row1; row++) {
      const rowLat = -90 + (row + 0.5) * CELL_DEG
      const dy = (rowLat - p.lat) * DEG
      const rowPeak = amplitude * Math.exp(-(dy * dy) / twoSigmaSq)
      const cosLat = Math.cos(rowLat * DEG)
      const halfWidth = Math.min(180, reachDeg / Math.max(cosLat, 1e-6))
      const cols = Math.min(W, 2 * Math.ceil(halfWidth / CELL_DEG) + 1)
      const colStart = Math.floor((p.lon - halfWidth + 180) / CELL_DEG)

      // Gaussian along the row: g_k = exp(-x_k^2 / 2s^2), x_k = x_0 + k*step.
      // g_{k+1} = g_k * r_k, r_k = exp(-(2*x_k*step + step^2) / 2s^2), r_{k+1} = r_k * q.
      const step = CELL_DEG * DEG * cosLat
      const x0 = (-180 + (colStart + 0.5) * CELL_DEG - p.lon) * DEG * cosLat
      let gauss = Math.exp(-(x0 * x0) / twoSigmaSq)
      let ratio = Math.exp(-(2 * x0 * step + step * step) / twoSigmaSq)
      const q = Math.exp(-(2 * step * step) / twoSigmaSq)
      const base = row * W
      let col = ((colStart % W) + W) % W // wraps across the antimeridian
      for (let k = 0; k < cols; k++) {
        const v = rowPeak * gauss
        const i = base + col
        heat[i] += v
        red[i] += v * cr
        green[i] += v * cg
        blue[i] += v * cb
        gauss *= ratio
        ratio *= q
        if (++col === W) col = 0
      }
    }
  }
  let max = 0
  for (let i = 0; i < heat.length; i++) if (heat[i] > max) max = heat[i]
  return max
}

const vertexShader = /* glsl */ `
  varying vec3 vPos;
  void main() {
    vPos = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

// Converts each pixel's position back to lat/lng (the inverse of three-globe's polar2Cartesian)
// and looks up the heat texture: alpha = intensity, rgb = story colour premultiplied by intensity
// (so blending between cells stays correct). Hot cores glow towards white; low heat fades out.
const fragmentShader = /* glsl */ `
  uniform sampler2D uHeat;
  uniform float uOpacity;
  varying vec3 vPos;
  // Not "PI": three.js #defines PI in its common chunk, which would break this if included.
  const float HEAT_PI = 3.141592653589793;
  void main() {
    vec3 p = normalize(vPos);
    float lat = asin(clamp(p.y, -1.0, 1.0));
    float lng = 0.5 * HEAT_PI - atan(p.z, p.x);
    vec4 texel = texture2D(uHeat, vec2(lng / (2.0 * HEAT_PI) + 0.5, lat / HEAT_PI + 0.5));
    float t = texel.a;
    if (t < 0.02) discard;
    vec3 color = texel.rgb / t;
    color = mix(color, vec3(1.0), 0.45 * smoothstep(0.7, 1.0, t));
    gl_FragColor = vec4(color, uOpacity * smoothstep(0.02, 0.4, t));
  }
`

export interface HeatLayer {
  object: Mesh
  /** Recompute the heatmap. Cheap enough to call on every time-slider step. */
  update(points: HeatPoint[]): void
}

export function createHeatLayer(options: { altitude: number; opacity?: number }): HeatLayer {
  const grid = createHeatGrid()
  const pixels = new Uint8Array(W * H * 4)
  const texture = new DataTexture(pixels, W, H, RGBAFormat, UnsignedByteType)
  texture.minFilter = texture.magFilter = LinearFilter
  texture.wrapS = RepeatWrapping
  texture.wrapT = ClampToEdgeWrapping
  texture.needsUpdate = true

  const material = new ShaderMaterial({
    uniforms: { uHeat: { value: texture }, uOpacity: { value: options.opacity ?? 0.85 } },
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
  })
  const object = new Mesh(
    new SphereGeometry(GLOBE_RADIUS * (1 + options.altitude), 128, 64),
    material,
  )
  object.raycast = () => {} // never a hover/click target

  return {
    object,
    update(points) {
      const max = accumulateHeat(points, grid)
      for (let i = 0; i < grid.heat.length; i++) {
        const h = grid.heat[i]
        // sqrt so smaller stories stay visible next to the biggest one.
        const t = max > 0 ? Math.sqrt(h / max) : 0
        const scale = h > 0 ? (255 * t) / h : 0 // colour = sum / heat, premultiplied by t
        pixels[4 * i] = grid.red[i] * scale
        pixels[4 * i + 1] = grid.green[i] * scale
        pixels[4 * i + 2] = grid.blue[i] * scale
        pixels[4 * i + 3] = 255 * t
      }
      texture.needsUpdate = true
    },
  }
}
