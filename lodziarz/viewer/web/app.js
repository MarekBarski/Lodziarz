import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

// ------------------------------------------------------------ scena
const canvas = document.getElementById("canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d22);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 5000);
camera.position.set(2.5, 1.8, 3.2);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;

const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(4, 8, 5);
scene.add(sun, new THREE.AmbientLight(0xffffff, 0.25));
const grid = new THREE.GridHelper(10, 20, 0x3a4150, 0x262b33);
scene.add(grid);

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w || canvas.height !== h) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}
renderer.setAnimationLoop(() => { resize(); controls.update(); renderer.render(scene, camera); });

// ------------------------------------------------------------ stan modelu
let modelRoot = null;
let lodNodes = [];          // [{node, tris, verts, variants?}]
// variants: {baked: {node,tris,verts}, orig: {node,tris,verts}} gdy GLB
// ma oba warianty per LOD (bake przelaczalny po operacji)
let activeLod = 0;
let bakeMask = [];          // bake on/off per LOD (tylko przy variants)
let lastCache = "";         // plik cache do re-exportu FBX
let viewMode = "lit";
const slotTextures = { basecolor: null, normal: null, orm: null, emissive: null,
                       opacity: null, ao: null, rough: null, metal: null, gloss: null };
let checkerTex = null;

function makeChecker() {
  if (checkerTex) return checkerTex;
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d");
  const n = 16, s = 512 / n;
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      g.fillStyle = (x + y) % 2 ? "#808080" : "#c8c8c8";
      g.fillRect(x * s, y * s, s, s);
    }
  g.fillStyle = "#d33"; g.fillRect(0, 0, s, s);
  checkerTex = new THREE.CanvasTexture(c);
  checkerTex.wrapS = checkerTex.wrapT = THREE.RepeatWrapping;
  checkerTex.colorSpace = THREE.SRGBColorSpace;
  return checkerTex;
}

const CHANNEL_FS = `
uniform sampler2D uTex;
uniform int uMode;      // 0 rgb, 1 r, 2 g, 3 b
varying vec2 vUv;
void main() {
  vec4 t = texture2D(uTex, vUv);
  vec3 c = t.rgb;
  if (uMode == 1) c = vec3(t.r);
  else if (uMode == 2) c = vec3(t.g);
  else if (uMode == 3) c = vec3(t.b);
  gl_FragColor = vec4(c, 1.0);
}`;
const CHANNEL_VS = `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

function channelMaterial(tex, mode) {
  return new THREE.ShaderMaterial({
    uniforms: { uTex: { value: tex || makeChecker() }, uMode: { value: mode } },
    vertexShader: CHANNEL_VS, fragmentShader: CHANNEL_FS,
  });
}

// widok w przestrzeni UV: mesh rozplaszczony do layoutu (uv albo uv1)
const UVFLAT_VS = (attr) => `
${attr === "uv1" ? "attribute vec2 uv1;" : ""}
varying vec2 vUv;
void main() {
  vUv = ${attr};
  gl_Position = vec4(${attr}.x * 1.8 - 0.9, ${attr}.y * 1.8 - 0.9, 0.0, 1.0);
}`;
const UVFLAT_FS = `
uniform sampler2D uTex;
varying vec2 vUv;
void main() { gl_FragColor = vec4(texture2D(uTex, vUv).rgb, 1.0); }`;

function uvFlatMaterial(attr) {
  return new THREE.ShaderMaterial({
    uniforms: { uTex: { value: makeChecker() } },
    vertexShader: UVFLAT_VS(attr), fragmentShader: UVFLAT_FS,
    side: THREE.DoubleSide, depthTest: false,
  });
}

function applyViewMode() {
  if (!modelRoot) return;
  const dx = document.getElementById("normalDx").checked;
  modelRoot.traverse((o) => {
    if (!o.isMesh) return;
    const std = o.userData.stdMaterial;
    if (!std) return;
    std.wireframe = false;
    std.normalScale.set(1, dx ? -1 : 1);
    const hasUv1 = !!o.geometry.attributes.uv1;
    switch (viewMode) {
      case "lit": o.material = std; break;
      case "wire": o.material = std; std.wireframe = true; break;
      case "basecolor": o.material = channelMaterial(std.map, 0); break;
      case "normal": o.material = channelMaterial(std.normalMap, 0); break;
      case "ao": o.material = channelMaterial(std.aoMap, 1); break;
      case "rough": {
        // roughnessMap czyta G; osobna mapa rough/gloss laduje w tym samym slocie
        const ch = std.roughnessMap === std.metalnessMap ? 2 : 2;
        o.material = channelMaterial(std.roughnessMap, ch); break;
      }
      case "metal": o.material = channelMaterial(std.metalnessMap, std.roughnessMap === std.metalnessMap ? 3 : 3); break;
      case "opacity": o.material = channelMaterial(std.alphaMap, 2); break;
      case "checker": {
        o.material = new THREE.MeshStandardMaterial({ map: makeChecker(), roughness: 0.8 });
        break;
      }
      case "checker2": {
        const t = makeChecker().clone();
        t.channel = hasUv1 ? 1 : 0;
        o.material = new THREE.MeshStandardMaterial({ map: t, roughness: 0.8 });
        if (!hasUv1) logOnce("brak oryginalnych UV w GLB (przetworz z bake) — pokazuje UV po bake");
        break;
      }
      case "uvflat": o.material = uvFlatMaterial("uv"); break;
      case "uvflat2": {
        o.material = uvFlatMaterial(hasUv1 ? "uv1" : "uv");
        if (!hasUv1) logOnce("brak oryginalnych UV w GLB (przetworz z bake) — pokazuje UV po bake");
        break;
      }
    }
  });
  grid.visible = !viewMode.startsWith("uvflat");
}

let _onceMsgs = new Set();
function logOnce(msg) {
  if (_onceMsgs.has(msg)) return;
  _onceMsgs.add(msg);
  logLine("WARN viewer: " + msg, "warn");
}

// ------------------------------------------------------------ LOD UI
function setLod(i) {
  activeLod = Math.max(0, Math.min(lodNodes.length - 1, i));
  lodNodes.forEach((l, k) => { l.node.visible = k === activeLod; });
  applyBakeMask();
  document.querySelectorAll("#lodBar button").forEach((b, k) =>
    b.classList.toggle("active", k === activeLod));
  const l = lodNodes[activeLod];
  let src = l;
  if (l && l.variants)
    src = bakeMask[activeLod] ? l.variants.baked : l.variants.orig;
  document.getElementById("stats").innerHTML =
    src ? `LOD${activeLod} &nbsp; tri: ${src.tris.toLocaleString()} &nbsp; verts: ${src.verts.toLocaleString()}` : "tri: — verts: —";
}

// widocznosc wariantow baked/orig wg maski (per LOD)
function applyBakeMask() {
  lodNodes.forEach((l, i) => {
    if (!l.variants) return;
    l.variants.baked.node.visible = !!bakeMask[i];
    l.variants.orig.node.visible = !bakeMask[i];
  });
}

function rebuildBakeBar() {
  const bar = document.getElementById("bakeBar");
  bar.innerHTML = "";
  const has = lodNodes.some((l) => l.variants);
  bar.style.display = has ? "" : "none";
  if (!has) return;
  const title = document.createElement("span");
  title.textContent = "bake:";
  bar.appendChild(title);
  lodNodes.forEach((l, i) => {
    if (!l.variants) return;
    const lab = document.createElement("label");
    lab.className = "chk";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!bakeMask[i];
    cb.onchange = () => {
      bakeMask[i] = cb.checked;
      applyBakeMask();
      setLod(activeLod);   // odswiez statystyki
      refreshSlotThumbs();
    };
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode("LOD" + i));
    bar.appendChild(lab);
  });
  const btn = document.createElement("button");
  btn.id = "reexportBtn";
  btn.textContent = "Re-export FBX";
  btn.onclick = reexportFbx;
  bar.appendChild(btn);
}

async function reexportFbx() {
  if (!lastCache) { logLine("WARN brak cache do re-exportu", "warn"); return; }
  const r = await fetch("/api/reexport", {
    method: "POST",
    body: JSON.stringify({ cache: lastCache, mask: bakeMask }),
    headers: { "Content-Type": "application/json" },
  }).then((r) => r.json());
  if (r.error) { logLine("ERROR " + r.error, "err"); return; }
  const btn = document.getElementById("reexportBtn");
  if (btn) btn.disabled = true;
  polling = setInterval(poll, 400);
}
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const d = parseInt(e.key, 10);
  if (!Number.isNaN(d) && d < lodNodes.length) setLod(d);
});

function rebuildLodBar() {
  const bar = document.getElementById("lodBar");
  bar.innerHTML = "";
  lodNodes.forEach((_, i) => {
    const b = document.createElement("button");
    b.textContent = "LOD" + i;
    b.onclick = () => setLod(i);
    bar.appendChild(b);
  });
}

// ------------------------------------------------------------ ladowanie GLB
const loader = new GLTFLoader();

function countGeo(node) {
  let tris = 0, verts = 0;
  const seenPos = new Set();  // primitives moga dzielic bufor pozycji
  node.traverse((o) => {
    if (o.isMesh && o.geometry) {
      const pos = o.geometry.attributes.position;
      if (!seenPos.has(pos.uuid)) {
        seenPos.add(pos.uuid);
        verts += pos.count;
      }
      tris += (o.geometry.index ? o.geometry.index.count : pos.count) / 3;
    }
  });
  return { tris: Math.round(tris), verts };
}

function loadModel(url) {
  loader.load(url, (gltf) => {
    if (modelRoot) scene.remove(modelRoot);
    modelRoot = gltf.scene;
    scene.add(modelRoot);

    // zbierz nody LOD po nazwach *_LOD<n>
    const found = [];
    modelRoot.traverse((o) => {
      const m = /_LOD(\d+)$/.exec(o.name || "");
      if (m && o.children.length + (o.isMesh ? 1 : 0) > 0) {
        // nod moze byc meshem albo grupa
        found.push({ index: parseInt(m[1], 10), node: o });
      }
    });
    // zostaw tylko najwyzsze wpisy (uniknij mesh-dziecka o tej samej nazwie)
    const byIndex = new Map();
    for (const f of found)
      if (!byIndex.has(f.index) || !f.node.isMesh) byIndex.set(f.index, f.node);
    lodNodes = [...byIndex.keys()].sort((a, b) => a - b)
      .map((i) => { const n = byIndex.get(i); const c = countGeo(n); return { node: n, ...c }; });
    if (!lodNodes.length) {
      const c = countGeo(modelRoot);
      lodNodes = [{ node: modelRoot, ...c }];
    }

    // warianty baked/orig per LOD (GLB z przelaczalnym bake po operacji)
    let anyVariants = false;
    lodNodes.forEach((l) => {
      let baked = null, orig = null;
      l.node.traverse((o) => {
        if (/_baked$/.test(o.name || "")) baked = o;
        if (/_orig$/.test(o.name || "")) orig = o;
      });
      if (baked && orig) {
        anyVariants = true;
        l.variants = { baked: { node: baked, ...countGeo(baked) },
                       orig: { node: orig, ...countGeo(orig) } };
      }
    });
    if (anyVariants && bakeMask.length !== lodNodes.length)
      bakeMask = lodNodes.map((_, i) => i > 0);   // default jak pipeline
    if (!anyVariants) bakeMask = [];

    modelRoot.traverse((o) => {
      if (o.isMesh) {
        if (!(o.material && o.material.isMeshStandardMaterial))
          o.material = new THREE.MeshStandardMaterial({ color: 0xbfc3c9 });
        // blend z depthWrite=false wyglada jak "przenikanie przez siebie"
        if (o.material.transparent) o.material.depthWrite = true;
        o.userData.stdMaterial = o.material;
      }
    });

    // kamera na model
    const box = new THREE.Box3().setFromObject(modelRoot);
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    const center = box.getCenter(new THREE.Vector3());
    controls.target.copy(center);
    camera.position.copy(center).add(new THREE.Vector3(0.7, 0.45, 0.9).multiplyScalar(size));
    camera.near = size / 500; camera.far = size * 20;
    camera.updateProjectionMatrix();
    grid.position.y = box.min.y;

    rebuildLodBar();
    rebuildBakeBar();
    setLod(0);
    refreshSlotThumbs();
    applyViewMode();
  }, undefined, (err) => logLine("ERROR viewer: " + (err.message || err), "err"));
}
window.lodziarzLoadModel = loadModel;

// drag&drop GLB wprost na viewport
const viewport = document.getElementById("viewport");
viewport.addEventListener("dragover", (e) => {
  if (e.target.closest(".slot")) return;
  e.preventDefault();
});
viewport.addEventListener("drop", (e) => {
  if (e.target.closest(".slot")) return;
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file && /\.glb$/i.test(file.name)) loadModel(URL.createObjectURL(file));
});

// ------------------------------------------------------------ sloty map
const SLOT_ASSIGN = {
  basecolor: (m, t) => { t.colorSpace = THREE.SRGBColorSpace; m.map = t; },
  normal: (m, t) => { t.colorSpace = THREE.NoColorSpace; m.normalMap = t; },
  // packed ORM — ustawia wszystkie trzy kanaly naraz
  orm: (m, t) => {
    t.colorSpace = THREE.NoColorSpace;
    m.aoMap = t; m.roughnessMap = t; m.metalnessMap = t;
    m.roughness = 1.0; m.metalness = 1.0;
  },
  emissive: (m, t) => {
    t.colorSpace = THREE.SRGBColorSpace;
    m.emissiveMap = t; m.emissive = new THREE.Color(0xffffff);
  },
  opacity: (m, t) => {
    t.colorSpace = THREE.NoColorSpace;
    m.alphaMap = t; m.transparent = true; m.depthWrite = false;
  },
  // mapy osobne — grayscale ma te sama wartosc w R/G/B, wiec kanalowe
  // sample'owanie three (ao=R, rough=G, metal=B) dziala bez konwersji
  ao: (m, t) => { t.colorSpace = THREE.NoColorSpace; m.aoMap = t; },
  rough: (m, t) => { t.colorSpace = THREE.NoColorSpace; m.roughnessMap = t; m.roughness = 1.0; },
  metal: (m, t) => { t.colorSpace = THREE.NoColorSpace; m.metalnessMap = t; m.metalness = 1.0; },
  gloss: (m, t) => { t.colorSpace = THREE.NoColorSpace; m.roughnessMap = t; m.roughness = 1.0; },
};
const SLOT_GET = {
  basecolor: (m) => m.map, normal: (m) => m.normalMap,
  orm: (m) => m.aoMap || m.roughnessMap, emissive: (m) => m.emissiveMap,
  opacity: (m) => m.alphaMap,
  ao: (m) => m.aoMap, rough: (m) => m.roughnessMap,
  metal: (m) => m.metalnessMap, gloss: () => null,
};

// glossiness -> roughness: inwersja pikseli na canvasie
function invertImage(img) {
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const g = c.getContext("2d");
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) {
    d.data[i] = 255 - d.data[i];
    d.data[i + 1] = 255 - d.data[i + 1];
    d.data[i + 2] = 255 - d.data[i + 2];
  }
  g.putImageData(d, 0, 0);
  return c;
}

function forEachStdMaterial(fn) {
  if (!modelRoot) return;
  const seen = new Set();
  modelRoot.traverse((o) => {
    if (o.isMesh && o.userData.stdMaterial && !seen.has(o.userData.stdMaterial)) {
      seen.add(o.userData.stdMaterial);
      fn(o.userData.stdMaterial);
    }
  });
}

function refreshSlotThumbs() {
  document.querySelectorAll(".slot").forEach((el) => {
    const slot = el.dataset.slot;
    const cnv = el.querySelector("canvas");
    const w = cnv.width, h = cnv.height;
    const g = cnv.getContext("2d");
    g.fillStyle = "#101216"; g.fillRect(0, 0, w, h);
    let tex = slotTextures[slot];
    if (!tex) forEachStdMaterial((m) => { if (!tex) tex = SLOT_GET[slot](m); });
    const img = tex && tex.image;
    if (img && (img.width || img.videoWidth)) {
      try { g.drawImage(img, 0, 0, w, h); } catch { /* ImageBitmap z GLB */
        try { g.drawImage(img, 0, 0, img.width, img.height, 0, 0, w, h); } catch {}
      }
    } else {
      g.fillStyle = "#3a4150"; g.font = "10px sans-serif";
      g.fillText("brak", w / 2 - 10, h / 2 + 3);
    }
  });
}

document.querySelectorAll(".slot").forEach((el) => {
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("dragover"); });
  el.addEventListener("dragleave", () => el.classList.remove("dragover"));
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    el.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (!/\.(png|jpe?g|webp)$/i.test(file.name)) {
      logLine("WARN viewer: przegladarka nie zdekoduje " + file.name +
              " — uzyj PNG/JPG/WebP", "warn");
      return;
    }
    const url = URL.createObjectURL(file);
    const slot = el.dataset.slot;
    new THREE.TextureLoader().load(url, (t) => {
      if (slot === "gloss") {
        t = new THREE.CanvasTexture(invertImage(t.image));
        logLine("INFO viewer: glossiness odwrocony do roughness", "");
      }
      t.flipY = false;             // konwencja glTF
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      slotTextures[slot] = t;
      forEachStdMaterial((m) => { SLOT_ASSIGN[slot](m, t); m.needsUpdate = true; });
      refreshSlotThumbs();
      applyViewMode();
    });
  });
});

// ------------------------------------------------------------ panel / API
const $ = (id) => document.getElementById(id);
const logEl = $("log");
let lastLogLen = 0;

// checkboxy "ktory LOD dostaje bake" — generowane pod liczbe LOD-ow;
// default: LOD0 odznaczony (oryginalne materialy), reszta zaznaczona
function rebuildBakeLodsRow() {
  const row = $("bakeLodsRow");
  const count = Math.max(1, Math.min(8, +$("lods").value || 4));
  const prev = {};
  row.querySelectorAll("input").forEach((c) => { prev[c.dataset.lod] = c.checked; });
  row.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.lod = i;
    cb.checked = i in prev ? prev[i] : i > 0;
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode("LOD" + i));
    row.appendChild(lab);
  }
}
rebuildBakeLodsRow();
$("lods").addEventListener("input", rebuildBakeLodsRow);

function bakedLodsFromUI() {
  return [...$("bakeLodsRow").querySelectorAll("input")]
    .filter((c) => c.checked).map((c) => +c.dataset.lod);
}

function logLine(text, cls) {
  const div = document.createElement("div");
  if (cls) div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

async function browse(kind, targetId) {
  const r = await fetch("/api/dialog", {
    method: "POST", body: JSON.stringify({ kind }),
    headers: { "Content-Type": "application/json" },
  }).then((r) => r.json()).catch(() => ({ path: "" }));
  if (r.path) $(targetId).value = r.path;
  else if (r.error) logLine("INFO dialogi natywne tylko w oknie aplikacji — wpisz sciezke recznie", "warn");
}
$("browseInput").onclick = () => browse("open", "inputPath");
$("browseOut").onclick = () => browse("folder", "outPath");

$("normalDx").onchange = applyViewMode;
$("viewMode").onchange = (e) => { viewMode = e.target.value; applyViewMode(); };

// ------------------------------------------- panel materialow (per-materiał)
const MAT_SLOTS = [
  ["basecolor", "BaseCol"], ["normal", "Normal"], ["orm", "ORM"],
  ["occlusion", "AO"], ["roughness", "Rough"], ["metallic", "Metal"],
  ["gloss", "Gloss"], ["emissive", "Emis"], ["opacity", "Opac"],
];

function renderMaterials(mats) {
  const list = $("materialsList");
  list.innerHTML = "";
  $("matCount").textContent = `(${mats.length})`;
  $("materialsSection").style.display = "";
  for (const m of mats) {
    const row = document.createElement("div");
    row.className = "mat-row";
    const name = document.createElement("div");
    name.className = "mat-name";
    name.textContent = m.name;
    name.title = m.name;
    row.appendChild(name);
    const slots = document.createElement("div");
    slots.className = "mat-slots";
    for (const [key, label] of MAT_SLOTS) {
      const el = document.createElement("div");
      el.className = "mslot";
      el.dataset.material = m.name;
      el.dataset.slot = key;
      el.title = `${m.name} — ${label} (drag&drop PNG/JPG/WebP)`;
      const cnv = document.createElement("canvas");
      cnv.width = cnv.height = 34;
      const g = cnv.getContext("2d");
      g.fillStyle = "#101216"; g.fillRect(0, 0, 34, 34);
      // mapa juz obecna w pliku (embedded / znaleziona z konwencji nazw)
      if (m.maps && m.maps[key]) {
        el.classList.add("filled");
        g.fillStyle = "#3f7d4f"; g.font = "9px sans-serif";
        g.fillText("plik", 8, 20);
      }
      const span = document.createElement("span");
      span.textContent = label;
      el.appendChild(cnv);
      el.appendChild(span);
      _wireMatSlot(el, cnv);
      slots.appendChild(el);
    }
    row.appendChild(slots);
    list.appendChild(row);
  }
}

function _wireMatSlot(el, cnv) {
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("dragover"); });
  el.addEventListener("dragleave", () => el.classList.remove("dragover"));
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    el.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (!/\.(png|jpe?g|webp|tga|bmp|tiff?)$/i.test(file.name)) {
      logLine("WARN: nieobslugiwany format " + file.name, "warn");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      const r = await fetch("/api/assign_texture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          material: el.dataset.material, slot: el.dataset.slot,
          filename: file.name, data: reader.result,
        }),
      }).then((r) => r.json()).catch((e) => ({ error: String(e) }));
      if (r.error) { logLine("ERROR przypisanie mapy: " + r.error, "err"); return; }
      el.classList.add("filled");
      logLine(`INFO ${el.dataset.material}: ${el.dataset.slot} <- ${file.name}`);
      // miniatura (tga/bmp moga sie nie zdekodowac w <img> — wtedy zostaje ramka)
      const img = new Image();
      img.onload = () => {
        const g = cnv.getContext("2d");
        g.drawImage(img, 0, 0, 34, 34);
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

// ------------------------------------------------------------ wczytywanie
$("loadBtn").onclick = async () => {
  const payload = { input: $("inputPath").value.trim(), up: $("upAxis").value };
  logEl.innerHTML = ""; lastLogLen = 0;
  const r = await fetch("/api/load", {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  }).then((r) => r.json());
  if (r.error) { logLine("ERROR " + r.error, "err"); return; }
  $("loadBtn").disabled = true;
  $("processBtn").disabled = true;
  polling = setInterval(poll, 400);
};

let polling = null;
$("processBtn").onclick = async () => {
  const payload = {
    input: $("inputPath").value.trim(),
    out: $("outPath").value.trim(),
    lods: +$("lods").value, ratio: +$("ratio").value,
    bake: $("bake").checked, backend: $("backend").value,
    bakedLods: bakedLodsFromUI(),
    atlas: +$("atlas").value, dilation: +$("dilation").value,
    ssaa: +$("ssaa").value,
    inputNormalDx: $("inputNormalDx").checked,
    outputNormalDx: $("outputNormalDx").checked,
    texFormat: $("texFormat").value,
    perLodFbx: $("perLodFbx").checked,
    embedTextures: $("embedTextures").checked,
    up: $("upAxis").value,
  };
  logEl.innerHTML = ""; lastLogLen = 0;
  const r = await fetch("/api/process", {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  }).then((r) => r.json());
  if (r.error) { logLine("ERROR " + r.error, "err"); return; }
  $("processBtn").disabled = true;
  $("loadBtn").disabled = true;
  polling = setInterval(poll, 400);
};

async function poll() {
  const p = await fetch("/api/progress").then((r) => r.json()).catch(() => null);
  if (!p) return;
  $("progressBar").style.width = p.percent + "%";
  $("stage").textContent = p.stage || "";
  for (let i = lastLogLen; i < p.log.length; i++) {
    const line = p.log[i];
    logLine(line, line.includes("ERROR") ? "err" : line.includes("WARN") ? "warn" : "");
  }
  lastLogLen = p.log.length;
  if (!p.running && p.result) {
    clearInterval(polling); polling = null;
    $("processBtn").disabled = false;
    $("loadBtn").disabled = false;
    if (p.result.kind === "load" && p.result.ok) {
      renderMaterials(p.result.materials || []);
      if (p.result.up_detected === "z")
        logLine("INFO orientacja zrodla: Z-up — skonwertowano do Y-up");
      Object.keys(slotTextures).forEach((k) => (slotTextures[k] = null));
      loadModel(p.result.preview + "?t=" + Date.now());
    } else if (p.result.kind === "process" && p.result.ok && p.result.glb) {
      Object.keys(slotTextures).forEach((k) => (slotTextures[k] = null));
      bakeMask = (p.result.baked_mask || []).map(Boolean);
      lastCache = p.result.cache || "";
      loadModel("/out/" + encodeURIComponent(p.result.glb) + "?t=" + Date.now());
    } else if (p.result.kind === "reexport") {
      const btn = document.getElementById("reexportBtn");
      if (btn) btn.disabled = false;
      if (p.result.ok)
        logLine("INFO FBX wyeksportowany z nowa maska: " + p.result.fbx);
    }
  }
}
