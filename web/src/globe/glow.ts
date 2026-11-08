// A soft glow behind each story's marker, like a star: a small round gradient in the story's
// colour, added to what is behind it. It sits exactly on the marker, so the globe shows one thing
// per story (a lit dot) instead of a marker plus a separate patch of colour elsewhere. Where
// stories crowd together their glows add up, which is what makes a busy region read as busy.
//
// One THREE.Points object. Sizes are in world units (degrees of arc on the globe), so a glow grows
// with the globe when you zoom, like the markers; the shader turns that into pixels using the
// camera, which the caller keeps current with setViewport().
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Points,
  ShaderMaterial,
  Vector2,
  type WebGLRenderer,
} from 'three'
import { GLOBE_RADIUS } from './land'
import type { RGB } from './heat'

export interface GlowPoint {
  lat: number
  lng: number
  /** Diameter of the glow, in degrees of arc. */
  diameterDeg: number
  color: RGB
  /** 0..1, multiplies the glow's brightness (a story easing in or out). */
  strength: number
}

const DEG = Math.PI / 180

const vertexShader = /* glsl */ `
  attribute float aSize;
  attribute vec4 aColor;
  uniform float uPixelsPerUnit; // pixels one world unit covers at distance 1
  varying vec4 vColor;
  void main() {
    vColor = aColor;
    vec4 view = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = max(aSize * uPixelsPerUnit / -view.z, 1.0);
    gl_Position = projectionMatrix * view;
  }
`
// Brightest at the middle, falling off smoothly to nothing at the edge of the point.
const fragmentShader = /* glsl */ `
  uniform float uOpacity;
  varying vec4 vColor;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d >= 1.0) discard;
    float falloff = (1.0 - d) * (1.0 - d);
    gl_FragColor = vec4(vColor.rgb, vColor.a * falloff * uOpacity);
  }
`

export interface GlowLayer {
  object: Points
  update(points: readonly GlowPoint[], altitude: number): void
  setOpacity(opacity: number): void
  /** Call every frame with the renderer and the camera's vertical field of view (degrees). */
  setViewport(renderer: WebGLRenderer, fovDeg: number): void
}

export function createGlowLayer(): GlowLayer {
  const material = new ShaderMaterial({
    uniforms: { uOpacity: { value: 1 }, uPixelsPerUnit: { value: 1000 } },
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
  })
  const object = new Points(new BufferGeometry(), material)
  object.frustumCulled = false // the bounding sphere isn't kept in step with the points
  object.raycast = () => {} // never a hover/click target
  const size = new Vector2()

  return {
    object,
    update(points, altitude) {
      const n = points.length
      const position = new Float32Array(3 * n)
      const sizes = new Float32Array(n)
      const colors = new Float32Array(4 * n)
      const radius = GLOBE_RADIUS * (1 + altitude)
      points.forEach((p, i) => {
        // The same mapping as three-globe's polar2Cartesian.
        const phi = (90 - p.lat) * DEG
        const theta = (90 - p.lng) * DEG
        position[3 * i] = radius * Math.sin(phi) * Math.cos(theta)
        position[3 * i + 1] = radius * Math.cos(phi)
        position[3 * i + 2] = radius * Math.sin(phi) * Math.sin(theta)
        sizes[i] = p.diameterDeg * DEG * GLOBE_RADIUS
        colors.set([p.color[0], p.color[1], p.color[2], p.strength], 4 * i)
      })
      const geometry = new BufferGeometry()
      geometry.setAttribute('position', new BufferAttribute(position, 3))
      geometry.setAttribute('aSize', new BufferAttribute(sizes, 1))
      geometry.setAttribute('aColor', new BufferAttribute(colors, 4))
      object.geometry.dispose()
      object.geometry = geometry
    },
    setOpacity(opacity) {
      material.uniforms.uOpacity.value = opacity
    },
    setViewport(renderer, fovDeg) {
      renderer.getDrawingBufferSize(size)
      material.uniforms.uPixelsPerUnit.value = size.y / (2 * Math.tan((fovDeg * DEG) / 2))
    },
  }
}
