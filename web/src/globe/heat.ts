// Heatmap of where stories are happening, as one transparent sphere over the land.
// Each story has its own hue; intensity is shown by brightness within that hue (dark at the
// edges, full colour in the body, near white at the core), evened out so every hue is equally
// bright at the same heat. Where stories overlap, their hues blend by heat.
//
// Sizes are absolute, in km on the globe: zooming just magnifies the field, it never recomputes
// or reshapes it. `spreadKm` adds a fixed minimum spread to every place so a story's places merge
// into one field rather than reading as separate dots.
//
// Heat is computed on the CPU into a 0.5° lat/lng grid, only when the data changes
// (tens of ms: each point touches only the cells within 3 sigma of it). The GPU then draws
// that grid every frame. This is cheaper than react-globe.gl's heatmapsData, which rebuilds a
// ~130k-vertex sphere and recomputes the density at every vertex on each update.
import {
  ClampToEdgeWrapping,
  DataTexture,
  DataUtils,
  HalfFloatType,
  LinearFilter,
  Mesh,
  RepeatWrapping,
  RGBAFormat,
  ShaderMaterial,
  SphereGeometry,
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
 * `spreadKm` widens every point by a fixed amount (sigma = sqrt(radius^2 + spread^2)). It's in km
 * on the globe, so it doesn't depend on zoom.
 *
 * Distances use the local approximation dy = dlat, dx = dlng * cos(lat). That's accurate at these
 * radii, except for points within a few degrees of a pole, where heat across the pole is
 * underestimated. Along a row, consecutive Gaussian values are produced by multiplying by a running
 * ratio, so the inner loop has no Math.exp.
 */
export function accumulateHeat(points: HeatPoint[], grid: HeatGrid, spreadKm = 0): number {
  const { heat, red, green, blue } = grid
  heat.fill(0)
  red.fill(0)
  green.fill(0)
  blue.fill(0)
  for (const p of points) {
    if (!(p.weight > 0)) continue
    const [cr, cg, cb] = p.color
    const radiusKm = Math.hypot(Math.max(p.radiusKm, 10), spreadKm)
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

// Shared by both styles. Converts each pixel's position back to lat/lng (the inverse of
// three-globe's polar2Cartesian) and looks up the heat texture: alpha = intensity t, rgb = hue
// premultiplied by t (so blending between cells stays correct). evenHue() rescales the hue to a
// fixed luminance, so yellow isn't "hotter" than indigo at equal heat.
const shaderCommon = /* glsl */ `
  uniform sampler2D uHeat;
  uniform float uOpacity;
  varying vec3 vPos;
  // Not "PI": three.js #defines PI in its common chunk, which would break this if included.
  const float HEAT_PI = 3.141592653589793;
  const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722); // Rec. 709 relative luminance weights
  vec4 heatHere() {
    vec3 p = normalize(vPos);
    float lat = asin(clamp(p.y, -1.0, 1.0));
    float lng = 0.5 * HEAT_PI - atan(p.z, p.x);
    return texture2D(uHeat, vec2(lng / (2.0 * HEAT_PI) + 0.5, lat / HEAT_PI + 0.5));
  }
  vec3 evenHue(vec3 premultiplied, float t, float luma) {
    vec3 hue = premultiplied / max(t, 1e-4);
    return clamp(hue * (luma / max(dot(hue, LUMA), 0.05)), 0.0, 1.0);
  }
`

// "fill": the heatmap. Brightness rises with t (dark at the edges, full colour in the body, near
// white at the core) and opacity rises with t, so the field fades smoothly into the globe.
const fillShader =
  shaderCommon +
  /* glsl */ `
  void main() {
    vec4 texel = heatHere();
    float t = texel.a;
    if (t < 0.01) discard;
    vec3 hue = evenHue(texel.rgb, t, 0.55);
    vec3 color = hue * mix(0.25, 1.0, smoothstep(0.0, 0.6, t));
    color = mix(color, vec3(1.0), 0.65 * smoothstep(0.65, 1.0, t));
    gl_FragColor = vec4(color, uOpacity * smoothstep(0.0, 0.3, t));
  }
`

// "outline": one story's rough area, as a crisp bright contour where its heat crosses LEVEL
// (about 1.5 px wide at any zoom, via fwidth) around a faint fill. Used for the hover highlight.
const outlineShader =
  shaderCommon +
  /* glsl */ `
  const float LEVEL = 0.3; // on the sqrt scale: ~9% of the story's peak heat
  void main() {
    vec4 texel = heatHere();
    float t = texel.a;
    float w = max(fwidth(t), 1e-4); // before any discard: derivatives need every pixel
    if (t < 0.01) discard;
    float rim = 1.0 - smoothstep(0.0, 1.5 * w, abs(t - LEVEL));
    float inside = smoothstep(LEVEL - w, LEVEL + w, t);
    float alpha = max(rim, 0.2 * inside);
    if (alpha < 0.01) discard;
    vec3 hue = evenHue(texel.rgb, t, 0.7);
    gl_FragColor = vec4(mix(hue, vec3(1.0), 0.5 * rim), uOpacity * alpha);
  }
`

export interface HeatLayer {
  object: Mesh
  /**
   * Recompute the heatmap. Cheap enough to call on every time-slider step or hover.
   * `spreadKm` is a fixed minimum spread for every place, in km (see accumulateHeat).
   */
  update(points: HeatPoint[], spreadKm?: number): void
  /** Fade the whole layer, e.g. dim the heatmap while one story is highlighted. */
  setOpacity(opacity: number): void
}

export function createHeatLayer(options: {
  altitude: number
  opacity?: number
  /** "fill" = the heatmap; "outline" = one story's area as a contour (hover highlight). */
  style?: 'fill' | 'outline'
}): HeatLayer {
  const grid = createHeatGrid()
  // Half floats: the shader divides colour by intensity, which bands badly with 8-bit values in
  // the faint edges. 16-bit float textures filter linearly on every WebGL2 device.
  const pixels = new Uint16Array(W * H * 4)
  const texture = new DataTexture(pixels, W, H, RGBAFormat, HalfFloatType)
  texture.minFilter = texture.magFilter = LinearFilter
  texture.wrapS = RepeatWrapping
  texture.wrapT = ClampToEdgeWrapping
  texture.needsUpdate = true

  const material = new ShaderMaterial({
    uniforms: { uHeat: { value: texture }, uOpacity: { value: options.opacity ?? 0.85 } },
    vertexShader,
    fragmentShader: options.style === 'outline' ? outlineShader : fillShader,
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
    update(points, spreadKm = 0) {
      const max = accumulateHeat(points, grid, spreadKm)
      const half = DataUtils.toHalfFloat
      pixels.fill(0) // half-float 0 is all zero bits
      for (let i = 0; i < grid.heat.length; i++) {
        const h = grid.heat[i]
        if (!(h > 0)) continue
        // sqrt so smaller stories stay visible next to the biggest one.
        const t = Math.sqrt(h / max)
        const scale = t / h // colour = sum / heat, premultiplied by t
        pixels[4 * i] = half(grid.red[i] * scale)
        pixels[4 * i + 1] = half(grid.green[i] * scale)
        pixels[4 * i + 2] = half(grid.blue[i] * scale)
        pixels[4 * i + 3] = half(t)
      }
      texture.needsUpdate = true
    },
    setOpacity(opacity) {
      material.uniforms.uOpacity.value = opacity
    },
  }
}
