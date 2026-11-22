"""Three.js overlay effects rendered to a transparent video layer.

render_fx(name, params, W, H, fps, duration, cache_dir) -> Path to a .mov (PNG codec, alpha).
Effects (all deterministic functions of time):
  particles  glowing dust/embers drifting up with depth      color, count, speed, size
  hologram   rotating wireframe core + rings + scan plane     color, pos [x,y], scale
  streaks    anamorphic light streaks crossing the frame      colors [a,b], count, speed
  grid       neon perspective grid floor rushing forward      color, speed, horizon
Needs Node with puppeteer(-core) and Chrome (CHROME_PATH to override).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from common import ffmpeg, log

VERSION = 4
HERE = Path(__file__).parent

COMMON_JS = r"""
function rng(seed){return function(){seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);
 t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
const P = window.FX_PARAMS, W = innerWidth, H = innerHeight;
const renderer = new THREE.WebGLRenderer({alpha:true, antialias:true, preserveDrawingBuffer:true});
// light effects render on black and are keyed by brightness later (additive look when overlaid)
renderer.setSize(W, H); renderer.setClearColor(0x000000, 1); document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, W / H, 0.1, 200); camera.position.set(0, 0, 10);
camera.lookAt(0, 0, 0); camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
function glowTexture(){const c=document.createElement('canvas');c.width=c.height=64;const g=c.getContext('2d');
 const r=g.createRadialGradient(32,32,0,32,32,32);r.addColorStop(0,'rgba(255,255,255,1)');
 r.addColorStop(0.25,'rgba(255,255,255,0.6)');r.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=r;g.fillRect(0,0,64,64);
 return new THREE.CanvasTexture(c);}
function screenToWorld(x, y, z){ // x,y in 0..1 of the frame, on plane at depth z
 const v = new THREE.Vector3(x * 2 - 1, -(y * 2 - 1), 0.5).unproject(camera).sub(camera.position).normalize();
 const d = (z - camera.position.z) / v.z; return camera.position.clone().add(v.multiplyScalar(d));}
"""

EFFECTS = {
    "particles": r"""
const N = P.count || 260, R = rng(7), col = new THREE.Color(P.color || '#FFD9A0');
const base = [], geo = new THREE.BufferGeometry(), pos = new Float32Array(N * 3);
for (let i = 0; i < N; i++) base.push([R()*24-12, R()*14-7, R()*12-8, 0.3+R()*0.9, R()*6.28]);
geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
const mat = new THREE.PointsMaterial({size: P.size || 0.55, map: glowTexture(), color: col, transparent: true,
  depthWrite: false, blending: THREE.AdditiveBlending, opacity: P.opacity || 1.0});
scene.add(new THREE.Points(geo, mat));
window.renderFrame = function(t){
  const sp = P.speed || 0.6;
  for (let i = 0; i < N; i++) { const b = base[i];
    pos[i*3] = b[0] + Math.sin(t * 0.7 + b[4]) * 0.4;
    pos[i*3+1] = ((b[1] + t * sp * b[3] + 7) % 14 + 14) % 14 - 7;
    pos[i*3+2] = b[2]; }
  geo.attributes.position.needsUpdate = true;
  mat.opacity = (P.opacity || 1.0) * Math.min(1, t / 0.5);
  camera.position.x = Math.sin(t * 0.2) * 0.6; camera.lookAt(0, 0, 0);
  renderer.render(scene, camera); };
""",
    "hologram": r"""
const col = new THREE.Color(P.color || '#3FF2FF'), g = new THREE.Group();
const m = (o) => new THREE.MeshBasicMaterial(Object.assign({color: col, transparent: true, opacity: 1.0,
  blending: THREE.AdditiveBlending, depthWrite: false}, o || {}));
const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.1, 1), m({wireframe: true, opacity: 0.9}));
g.add(core);
const rings = [];
for (let i = 0; i < 3; i++) { const r = new THREE.Mesh(new THREE.TorusGeometry(1.8 + i * 0.45, 0.018, 8, 160), m({opacity: 0.7}));
  r.rotation.x = Math.PI / 2 + i * 0.5; g.add(r); rings.push(r); }
const ticks = new THREE.Mesh(new THREE.RingGeometry(2.9, 3.05, 120, 1, 0, Math.PI * 1.5), m({opacity: 0.6, side: THREE.DoubleSide}));
g.add(ticks);
const scan = new THREE.Mesh(new THREE.PlaneGeometry(6.5, 0.04), m({opacity: 0.8}));
g.add(scan);
const p = P.pos || [0.5, 0.5]; g.position.copy(screenToWorld(p[0], p[1], 0)); g.scale.setScalar(P.scale || 1);
scene.add(g);
window.renderFrame = function(t){
  core.rotation.y = t * 0.9; core.rotation.x = t * 0.4;
  rings.forEach((r, i) => { r.rotation.z = t * (0.6 + i * 0.35) * (i % 2 ? -1 : 1); });
  ticks.rotation.z = -t * 0.5;
  scan.position.y = Math.sin(t * 1.6) * 1.6;
  const flick = 0.75 + 0.25 * Math.sin(t * 37.0) * Math.sin(t * 13.0);
  g.children.forEach(c => c.material.opacity = Math.min(1, t / 0.4) * flick * (c === core ? 1.0 : 0.85));
  renderer.render(scene, camera); };
""",
    "streaks": r"""
const N = P.count || 7, R = rng(3), cols = (P.colors || ['#7FD4FF', '#FFB36B']).map(c => new THREE.Color(c));
function streakTex(){const c=document.createElement('canvas');c.width=512;c.height=16;const g=c.getContext('2d');
 const l=g.createLinearGradient(0,0,512,0);l.addColorStop(0,'rgba(255,255,255,0)');l.addColorStop(0.5,'rgba(255,255,255,1)');
 l.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=l;g.fillRect(0,0,512,16);
 const v=g.getImageData(0,0,512,16);for(let y=0;y<16;y++){const f=Math.exp(-Math.pow((y-7.5)/3.2,2));
 for(let x=0;x<512;x++)v.data[(y*512+x)*4+3]*=f;}g.putImageData(v,0,0);return new THREE.CanvasTexture(c);}
const tex = streakTex(), items = [];
for (let i = 0; i < N; i++) {
  const mat = new THREE.MeshBasicMaterial({map: tex, color: cols[i % cols.length], transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0});
  const s = new THREE.Mesh(new THREE.PlaneGeometry(10 + R() * 10, 0.35 + R() * 0.5), mat);
  s.position.set(0, R() * 7 - 3.5, R() * 3 - 1); scene.add(s);
  items.push({s, sp: (0.6 + R()) * (P.speed || 6) * (R() < 0.5 ? -1 : 1), ph: R() * 40, op: 0.7 + R() * 0.3}); }
window.renderFrame = function(t){
  items.forEach(it => { const x = ((it.ph + t * it.sp) % 40 + 40) % 40 - 20; it.s.position.x = x;
    it.s.material.opacity = it.op * Math.min(1, t / 0.3) * (0.7 + 0.3 * Math.sin(t * 9 + it.ph)); });
  renderer.render(scene, camera); };
""",
    "grid": r"""
const col = new THREE.Color(P.color || '#FF2BD6');
// neon floor: lines along x and z, faded by distance in the shader (fog would paint opaque black)
const pts = [], S = 60, step = 1.5;
// keep every vertex in front of the camera (z < 10): software GL drops lines crossing the near plane
for (let x = -S; x <= S; x += step) { pts.push(x, 0, -S * 2, x, 0, 8); }
for (let z = -S * 2; z <= 8; z += step) { pts.push(-S, 0, z, S, 0, z); }
const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
const mat = new THREE.ShaderMaterial({transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: {color: {value: col}, opacity: {value: 0}, far: {value: P.far || 40}},
  vertexShader: 'varying float d; void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); d = -mv.z; gl_Position = projectionMatrix * mv; }',
  fragmentShader: 'uniform vec3 color; uniform float opacity; uniform float far; varying float d; void main(){ float a = opacity * (1.0 - smoothstep(far*0.25, far, d)); gl_FragColor = vec4(color * 1.6, a); }'});
const floor = new THREE.LineSegments(geo, mat); floor.position.y = -2.4; scene.add(floor);
camera.position.set(0, P.height || 0.4, 10); camera.lookAt(0, -(P.horizon || 0.8), 0);
window.renderFrame = function(t){
  floor.position.z = (t * (P.speed || 3)) % step;
  mat.uniforms.opacity.value = 0.95 * Math.min(1, t / 0.5);
  renderer.render(scene, camera); };
""",
}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#000;overflow:hidden}canvas{display:block}</style></head><body>
<script src="{three}"></script>
<script>window.FX_PARAMS = {params};</script>
<script>{common}
{effect}
window.fxReady = true;</script></body></html>"""


def _node_env():
    env = dict(os.environ)
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        root = ""
    paths = [env.get("NODE_PATH", ""), root]
    if root:  # puppeteer installed as a dependency of a global tool
        for sub in Path(root).glob("*/node_modules"):
            if (sub / "puppeteer").exists() or (sub / "puppeteer-core").exists():
                paths.append(str(sub))
        for sub in Path(root).glob("@*/*/node_modules"):
            if (sub / "puppeteer").exists() or (sub / "puppeteer-core").exists():
                paths.append(str(sub))
    env["NODE_PATH"] = os.pathsep.join(p for p in paths if p)
    return env


def available() -> str | None:
    """None when three.js effects can render here, else the reason (for recipes/menus)."""
    import shutil
    if not shutil.which("node"):
        return "three.js effects need Node.js"
    chk = subprocess.run(["node", "-e", "try{require.resolve('puppeteer-core')}catch(e){require.resolve('puppeteer')}"],
                         capture_output=True, text=True, env=_node_env())
    if chk.returncode != 0:
        return "three.js effects need puppeteer or puppeteer-core (npm i -g puppeteer-core)"
    chrome = os.environ.get("CHROME_PATH") or next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
    if not chrome:
        return "three.js effects need Chrome or Chromium (set CHROME_PATH)"
    return None


CHROME_CANDIDATES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # same list as fx_render.js
                     "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     "C:/Program Files/Google/Chrome/Application/chrome.exe"]


def _luma_key(frames_dir: Path, gain: float):
    """Black-background light effect -> RGBA whose normal 'over' compositing equals adding
    the light: alpha = brightest channel, colour = rgb / alpha (un-premultiplied)."""
    import numpy as np
    from PIL import Image
    for fp in sorted(frames_dir.glob("f*.png")):
        a = np.asarray(Image.open(fp).convert("RGB"), dtype=np.float32) / 255.0 * gain
        a = np.clip(a, 0, 1)
        al = a.max(axis=2, keepdims=True)
        rgb = np.where(al > 1e-4, a / np.maximum(al, 1e-4), 0)
        Image.fromarray((np.concatenate([rgb, al], axis=2) * 255).astype("uint8"), "RGBA").save(fp, compress_level=1)


def render_fx(name: str, params: dict, W: int, H: int, fps: float, duration: float, cache_dir: Path) -> Path:
    if name not in EFFECTS:
        raise SystemExit(f"unknown fx '{name}'. options: {', '.join(EFFECTS)}")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([name, params, W, H, fps, round(duration, 3), VERSION],
                                  sort_keys=True).encode()).hexdigest()[:12]
    out = cache_dir / f"{name}_{key}.mov"
    if out.exists():
        return out
    work = cache_dir / f"_{key}"
    if work.exists():
        shutil.rmtree(work)
    (work / "frames").mkdir(parents=True)
    page = work / "fx.html"
    page.write_text(PAGE.replace("{three}", (HERE / "vendor" / "three.min.js").resolve().as_uri())
                    .replace("{params}", json.dumps(params)).replace("{common}", COMMON_JS)
                    .replace("{effect}", EFFECTS[name]), encoding="utf-8")
    log(f"fx {name}: rendering {duration:.1f}s at {W}x{H} with headless Chrome")
    r = subprocess.run(["node", str(HERE / "fx_render.js"), str(page), str(work / "frames"), str(W), str(H),
                        str(fps), str(duration)], capture_output=True, text=True, env=_node_env())
    if r.returncode != 0:
        raise SystemExit(f"fx {name} failed:\n{r.stderr[-1500:]}")
    if r.stderr.strip():
        log(r.stderr.strip()[-400:])
    _luma_key(work / "frames", float(params.get("gain", 1.3)))
    ffmpeg(["-framerate", str(fps), "-i", str(work / "frames" / "f%05d.png"), "-c:v", "png",
            "-pix_fmt", "rgba", str(out)])
    shutil.rmtree(work, ignore_errors=True)
    return out
