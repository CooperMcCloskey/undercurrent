// The sea: a shader instead of a flat colour. It shades from a bright ocean blue in the tropics to
// deep navy at the poles, lets slow ripples drift across the surface (value noise moving through the
// sphere's own coordinates, so there is no texture and no seam), catches a few faint glints, and
// lightens toward the rim like a thick layer of air. All of it is quiet on purpose: the stories
// are the bright things on the globe.
import { Color, ShaderMaterial } from 'three'

const vertexShader = /* glsl */ `
  varying vec3 vSphere; // unit direction from the globe's centre, in object space
  varying vec3 vNormalView;
  varying vec3 vToCamera;
  void main() {
    vSphere = normalize(position);
    vNormalView = normalize(normalMatrix * normal);
    vec4 view = modelViewMatrix * vec4(position, 1.0);
    vToCamera = normalize(-view.xyz);
    gl_Position = projectionMatrix * view;
  }
`

const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform vec3 uPolar;
  uniform vec3 uTemperate;
  uniform vec3 uTropic;
  varying vec3 vSphere;
  varying vec3 vNormalView;
  varying vec3 vToCamera;

  float hash(vec3 p) {
    p = fract(p * 0.3183099 + 0.1);
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
  }
  float noise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x), mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
      mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x), mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
      f.z
    );
  }

  void main() {
    vec3 p = normalize(vSphere);
    float lat = abs(p.y); // 0 at the equator, 1 at the poles

    // Colour by latitude: turquoise in the tropics, blue-green in between, navy toward the poles.
    vec3 color = mix(uTropic, uTemperate, smoothstep(0.1, 0.55, lat));
    color = mix(color, uPolar, smoothstep(0.5, 0.95, lat));

    // Slow ripples: two layers of noise drifting in different directions.
    float t = uTime;
    float ripple = noise(p * 55.0 + vec3(t * 0.06, t * 0.035, 0.0)) * 0.6
                 + noise(p * 120.0 - vec3(0.0, t * 0.05, t * 0.03)) * 0.4;
    color *= 0.88 + 0.24 * ripple;

    // A few glints that come and go.
    float glint = smoothstep(0.80, 0.95, noise(p * 210.0 + vec3(t * 0.12, -t * 0.08, t * 0.05)));

    // Light from the upper left of the view, and a pale rim like the air at the edge.
    vec3 n = normalize(vNormalView);
    float facing = clamp(dot(n, normalize(vToCamera)), 0.0, 1.0);
    float diffuse = 0.72 + 0.38 * clamp(dot(n, normalize(vec3(-0.45, 0.55, 0.7))), 0.0, 1.0);
    color *= diffuse;
    color += vec3(0.75, 0.88, 1.0) * glint * 0.16 * facing;
    color = mix(color, vec3(0.32, 0.55, 0.92), pow(1.0 - facing, 3.0) * 0.45);

    gl_FragColor = vec4(color, 1.0);
  }
`

export interface Ocean {
  material: ShaderMaterial
  /** Call every frame with the time in seconds. */
  setTime(seconds: number): void
}

export function createOcean(): Ocean {
  const material = new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uPolar: { value: new Color('#0a2a55') },
      uTemperate: { value: new Color('#0b3d72') },
      uTropic: { value: new Color('#0e4f8a') },
    },
    vertexShader,
    fragmentShader,
  })
  return {
    material,
    setTime: (seconds) => {
      material.uniforms.uTime.value = seconds
    },
  }
}
