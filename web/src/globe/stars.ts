// A field of stars behind the globe: a few thousand points on a huge sphere around it, fixed in
// space, so they swing past as you turn the globe. Mostly small and dim, some bright, tinted a
// little blue or warm, and each twinkles slowly. Drawn with the depth test on, so the globe and its
// atmosphere hide the ones behind them; the sphere must sit inside the camera's far plane.
import { BufferAttribute, BufferGeometry, Points, ShaderMaterial } from 'three'

export interface Stars {
  object: Points
  /** Radius of the star sphere: keep the camera's far plane beyond it. */
  radius: number
  /** Call every frame with the time in seconds, for the twinkle. */
  setTime(seconds: number): void
  dispose(): void
}

const vertexShader = /* glsl */ `
  attribute float aSize;
  attribute float aPhase;
  attribute vec3 aColor;
  uniform float uTime;
  varying vec3 vColor;
  varying float vTwinkle;
  void main() {
    vColor = aColor;
    // Each star dims and brightens a little, at its own pace and offset.
    vTwinkle = 0.8 + 0.2 * sin(uTime * (0.6 + 0.8 * fract(aPhase * 7.0)) + aPhase * 6.2831);
    gl_PointSize = aSize;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`
const fragmentShader = /* glsl */ `
  varying vec3 vColor;
  varying float vTwinkle;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d >= 1.0) discard;
    float a = (1.0 - d) * (1.0 - d);
    gl_FragColor = vec4(vColor * vTwinkle, a);
  }
`

/** A small seeded random generator (mulberry32), so the sky is the same every time. */
function random(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function createStars(count = 2400, radius = 4000, seed = 7): Stars {
  const rand = random(seed)
  const position = new Float32Array(3 * count)
  const size = new Float32Array(count)
  const phase = new Float32Array(count)
  const color = new Float32Array(3 * count)
  for (let i = 0; i < count; i++) {
    // A uniform direction on the sphere.
    const z = 2 * rand() - 1
    const angle = 2 * Math.PI * rand()
    const ring = Math.sqrt(1 - z * z)
    position.set(
      [radius * ring * Math.cos(angle), radius * z, radius * ring * Math.sin(angle)],
      3 * i,
    )
    // Many faint, few bright: brightness and size both grow with a low-probability draw.
    const bright = rand() ** 4
    size[i] = 1.2 + 2.2 * bright
    phase[i] = rand()
    const level = 0.35 + 0.65 * Math.max(bright, rand() * 0.5)
    const tint = rand()
    const rgb = tint < 0.2 ? [0.8, 0.9, 1] : tint < 0.35 ? [1, 0.92, 0.78] : [1, 1, 1]
    color.set([rgb[0] * level, rgb[1] * level, rgb[2] * level], 3 * i)
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(position, 3))
  geometry.setAttribute('aSize', new BufferAttribute(size, 1))
  geometry.setAttribute('aPhase', new BufferAttribute(phase, 1))
  geometry.setAttribute('aColor', new BufferAttribute(color, 3))
  const material = new ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
  })
  const object = new Points(geometry, material)
  object.frustumCulled = false
  object.renderOrder = -1
  object.raycast = () => {} // never a hover/click target
  return {
    object,
    radius,
    setTime: (seconds) => {
      material.uniforms.uTime.value = seconds
    },
    dispose: () => {
      geometry.dispose()
      material.dispose()
    },
  }
}
