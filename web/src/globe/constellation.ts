// The "constellation": one flat dot per article of a story, lying on the globe at the article's
// own place (story/articles.ts decides where), plus a faint line from the story's marker to each.
// The dots are real objects on the globe, sized and positioned in degrees of arc, so they turn
// with the globe and scale with zoom exactly like the heat and the markers (an earlier version
// laid them out in screen pixels and spread across continents when zoomed out).
//
// Each dot is its own datum of react-globe.gl's custom layer, so hovering and clicking one reaches
// onCustomLayerHover / onCustomLayerClick with its article. The lines are not hoverable.
import {
  BufferGeometry,
  CircleGeometry,
  Color,
  DoubleSide,
  Float32BufferAttribute,
  LineBasicMaterial,
  LineSegments,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  RingGeometry,
  Vector3,
  type Object3D,
} from 'three'
import type { Location, StorySummary } from '../api/types.gen'
import { KIND_STYLE, type Article, type DotPlacement } from '../story/articles'
import { GLOBE_RADIUS } from './land'

/** Where the dots lie, as a fraction of the globe radius: just above the story markers. */
export const DOT_ALTITUDE = 0.0125

/** A custom-layer datum: a dot (with its article and the place it is for), or the spokes. */
export interface ConstellationItem {
  object: Object3D
  story?: StorySummary
  article?: Article
  place?: Location | null
}

export interface Constellation {
  items: ConstellationItem[]
  /** Light up every dot of these articles (hovered in the panel, hovered or picked on the globe). */
  setLit(ids: readonly (string | null)[]): void
  /** Free the GPU resources. */
  dispose(): void
}

const DEG = Math.PI / 180
const RADIUS = GLOBE_RADIUS * (1 + DOT_ALTITUDE)
const OPACITY = 0.95
const SPOKE_OPACITY = 0.3
const SPOKE_STEP_DEG = 2 // a spoke is bent along the globe (a vertex every 2 deg), not a chord through it
const LIT_SCALE = 1.6
const LIT_WHITE = 0.55
const GROW_MS = 450
const WHITE = new Color('#ffffff')

// Unit-radius shapes, scaled per dot. The "diamond" is a 4-sided circle: its corners point
// east, north, west and south, so it stands on a corner.
const SHAPES = {
  disc: new CircleGeometry(1, 32),
  ring: new RingGeometry(0.58, 1, 32),
  diamond: new CircleGeometry(1, 4),
}

/** Same formula as three-globe's polar2Cartesian, for a unit sphere. */
function surfaceVector(lat: number, lng: number): Vector3 {
  const phi = (90 - lat) * DEG
  const theta = (90 - lng) * DEG
  const s = Math.sin(phi)
  return new Vector3(s * Math.cos(theta), Math.cos(phi), s * Math.sin(theta))
}

/** East and north unit vectors at a point of the unit sphere (three-globe's y axis is north). */
function localFrame(up: Vector3): { east: Vector3; north: Vector3 } {
  const east = new Vector3(0, 1, 0).cross(up)
  if (east.lengthSq() < 1e-9) east.set(1, 0, 0) // exactly at a pole
  east.normalize()
  return { east, north: up.clone().cross(east) }
}

/** The point a fraction `t` of the way along the great circle from `a` to `b` (unit vectors). */
function along(a: Vector3, b: Vector3, angle: number, t: number): Vector3 {
  const sin = Math.sin(angle)
  if (sin < 1e-6) return a.clone() // the same point (or exactly opposite: no one great circle)
  return a
    .clone()
    .multiplyScalar(Math.sin((1 - t) * angle) / sin)
    .addScaledVector(b, Math.sin(t * angle) / sin)
}

const noRaycast = () => {}
const reducedMotion = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export function buildConstellation(
  anchor: { lat: number; lng: number },
  dots: readonly DotPlacement[],
  color: string,
  story: StorySummary,
): Constellation {
  const origin = surfaceVector(anchor.lat, anchor.lng)
  const born = performance.now()
  const instant = reducedMotion()

  const items: ConstellationItem[] = []
  const materials: MeshBasicMaterial[] = []
  const lit = new Map<string, ((on: boolean) => void)[]>() // by article: it may have several dots
  const spokePoints: number[] = []

  for (const dot of dots) {
    const up = surfaceVector(dot.lat, dot.lng)
    const frame = localFrame(up)

    const material = new MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0,
      side: DoubleSide,
      depthWrite: false,
    })
    materials.push(material)
    const mesh = new Mesh(SHAPES[KIND_STYLE[dot.article.kind].shape], material)
    mesh.quaternion.setFromRotationMatrix(new Matrix4().makeBasis(frame.east, frame.north, up))
    mesh.renderOrder = 5 // over the heat layers, which are transparent spheres

    // Bursts out of the marker: drawn at its place, then grows and glides to the dot's own.
    const target = up.clone().multiplyScalar(RADIUS)
    const start = origin.clone().multiplyScalar(RADIUS)
    const baseScale = RADIUS * dot.radius * DEG
    let isLit = false
    const place = (e: number) => {
      mesh.position.copy(start).lerp(target, e).setLength(RADIUS)
      mesh.scale.setScalar(baseScale * (0.25 + 0.75 * e) * (isLit ? LIT_SCALE : 1))
      material.opacity = OPACITY * e
    }
    place(instant ? 1 : 0)
    // Runs every frame, just before the dot is drawn (three.js applies it from the next frame).
    mesh.onBeforeRender = () => {
      const t = Math.min(1, Math.max(0, (performance.now() - born - dot.delay) / GROW_MS))
      place(instant ? 1 : 1 - (1 - t) ** 3) // ease out
    }
    const light = (on: boolean) => {
      isLit = on
      material.color.set(color).lerp(WHITE, on ? LIT_WHITE : 0)
    }
    lit.set(dot.article.id, [...(lit.get(dot.article.id) ?? []), light])
    items.push({ object: mesh, story, article: dot.article, place: dot.place })

    // Spoke: marker -> dot, along the globe.
    const angle = Math.acos(Math.max(-1, Math.min(1, origin.dot(up))))
    const steps = Math.max(1, Math.ceil(angle / (SPOKE_STEP_DEG * DEG)))
    for (let s = 0; s < steps; s++) {
      for (const k of [s, s + 1]) {
        const p = along(origin, up, angle, k / steps).multiplyScalar(RADIUS)
        spokePoints.push(p.x, p.y, p.z)
      }
    }
  }

  const spokeGeometry = new BufferGeometry()
  spokeGeometry.setAttribute('position', new Float32BufferAttribute(spokePoints, 3))
  const spokeMaterial = new LineBasicMaterial({
    color,
    transparent: true,
    opacity: SPOKE_OPACITY,
    depthWrite: false,
  })
  const spokes = new LineSegments(spokeGeometry, spokeMaterial)
  spokes.raycast = noRaycast
  spokes.renderOrder = 4
  items.unshift({ object: spokes })

  let litIds: readonly (string | null)[] = []
  return {
    items,
    setLit(ids) {
      for (const id of litIds) if (id) lit.get(id)?.forEach((light) => light(false))
      litIds = ids
      for (const id of ids) if (id) lit.get(id)?.forEach((light) => light(true))
    },
    dispose() {
      materials.forEach((m) => m.dispose())
      spokeGeometry.dispose()
      spokeMaterial.dispose()
    },
  }
}
