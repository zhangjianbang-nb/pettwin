/* PetTwin Desktop — 3D 行为分身（v0.3）
 *
 * 渲染三层沿用 cat-rig 已验证路径：
 *   L1 cat_rig_v3.glb（51 骨骼 / 12 动画）+ 归一化
 *   L2 Fur Shell 多层毛发壳（onBeforeCompile 蒙皮后沿法线外推）
 *   L3 风格向量 → AnimationAction timeScale + 尾巴程序动画
 * v0.3 新增：轮询 /v1/avatar/{pet_id}/style，把真实行为日志映射成动画参数；
 *            向量平滑插值（lerp 向目标），状态机切换动画权重。
 */
import * as THREE from "three";
import { GLTFLoader } from "./lib/GLTFLoader.js";

/* ---------- 可配置 ---------- */
const params = new URLSearchParams(location.search);
const PET_ID = params.get("pet") || "demo";
const API_BASE = params.get("api") || "";            // 同源部署留空；分离部署填 http://host:port
const POLL_MS = Math.max(5, parseInt(params.get("poll") || "30", 10)) * 1000;
const NAME = params.get("name") || PET_ID;
const PALETTE = (params.get("color") || "orange")     // orange|black|gray|cow
  , PALETTES = {
      orange: ["#b96b2e", "#e8934a"],
      black:  ["#4a4038", "#6e615a"],
      gray:   ["#8d8d95", "#c9c9cf"],
      cow:    ["#33302c", "#8a8578"],
    };

/* ---------- 场景 ---------- */
const canvas = document.getElementById("stage");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x16141f);
const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.1, 100);
camera.position.set(3.0, 1.25, 3.9);
camera.lookAt(0, 0.42, 0);

/* 光照（简化版时变：跟随真实小时） */
const hemi = new THREE.HemisphereLight(0xdfe9ff, 0x8a7460, 1.4);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 1.7);
sun.position.set(3, 5, 2);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.radius = 6;
sun.shadow.camera.left = -4; sun.shadow.camera.right = 4;
sun.shadow.camera.top = 4; sun.shadow.camera.bottom = -4;
scene.add(sun);
const fill = new THREE.DirectionalLight(0x8899ff, 0.22);
fill.position.set(-3, 2, -2);
scene.add(fill);

function setTimeOfDay(h) {
  const warm = new THREE.Color(0xffd9a0), noon = new THREE.Color(0xffffff), cold = new THREE.Color(0xbcd2ff);
  let c;
  if (h < 10) c = cold.clone().lerp(noon, ((h - 6) / 16) * 2.5);
  else if (h < 17) c = noon;
  else c = noon.clone().lerp(warm, Math.min(1, (h - 17) / 5) * 0.8);
  sun.color = c;
  const dim = h < 8 || h > 20 ? 0.45 : 1.0;
  sun.intensity = 2.2 * dim + 0.5;
  hemi.intensity = 1.0 + 0.5 * dim;
}
setTimeOfDay(new Date().getHours() + new Date().getMinutes() / 60);

const ground = new THREE.Mesh(
  new THREE.CircleGeometry(6, 48),
  new THREE.MeshStandardMaterial({ color: 0x2a2735, roughness: 0.95 })
);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

/* ---------- L1: 模型 ---------- */
const mixer = new THREE.AnimationMixer(new THREE.Object3D());
let catRoot = null, catMesh = null, actions = {}, curAnim = "Idle";
const tailBones = [], earBones = [];

function fail(msg) {
  document.getElementById("err").classList.add("show");
  document.getElementById("errMsg").textContent = msg;
}

new GLTFLoader().load("cat_rig_v3.glb", (gltf) => {
  try {
    catRoot = gltf.scene;
    // 归一化：缩放到高 1.1 单位、站在原点
    const box = new THREE.Box3().setFromObject(catRoot);
    const size = new THREE.Vector3();
    box.getSize(size);
    const scale = 1.1 / Math.max(size.y, 0.001);
    catRoot.scale.setScalar(scale);
    box.setFromObject(catRoot);
    const center = new THREE.Vector3();
    box.getCenter(center);
    catRoot.position.x -= center.x;
    catRoot.position.z -= center.z;
    catRoot.position.y -= box.min.y;
    catRoot.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = true;
        o.frustumCulled = false;
        catMesh = o;
        if (o.material) o.material.color?.set(PALETTES[PALETTE][0]);
      }
      if (o.isBone) {
        if (/^Tail[1-8]/.test(o.name)) tailBones.push(o);
        if (/^Ear/.test(o.name) && /\.L$|\.R$/.test(o.name)) earBones.push(o);
      }
    });
    scene.add(catRoot);
    gltf.animations.forEach((clip) => { actions[clip.name] = mixer.clipAction(clip, catRoot); });
    Object.values(actions).forEach((a) => { a.play(); a.setEffectiveWeight(0); });
    setAnim("Idle");
    buildFur();
  } catch (e) { fail("模型初始化: " + e.message); }
}, undefined, (e) => fail("cat_rig_v3.glb 加载失败: " + (e?.message || e)));

/* ---------- L2: Fur Shell ---------- */
let furGroup = null;
const furMats = [];
const FUR_LAYERS = 5, FUR_LEN = 0.018;

function buildFur() {
  if (furGroup) {
    furGroup.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
    scene.remove(furGroup);
  }
  furGroup = new THREE.Group();
  if (!catMesh) { scene.add(furGroup); return; }
  const [rootC, tipC] = PALETTES[PALETTE];
  const srcGeo = catMesh.geometry;
  for (let i = 1; i <= FUR_LAYERS; i++) {
    const t = i / FUR_LAYERS;
    const mat = new THREE.MeshStandardMaterial({
      roughness: 0.9, metalness: 0.0, transparent: true,
      opacity: 0.55 + 0.35 * (1 - t), depthWrite: t < 0.3, side: THREE.DoubleSide,
    });
    mat.color.set(rootC).lerp(new THREE.Color(tipC), t * 0.3);
    const uLayer = { value: t }, uLen = { value: FUR_LEN }, uTimeF = { value: 0 };
    mat.userData.uniforms = { uLayer, uLen, uTimeF };
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uLayer = uLayer;
      shader.uniforms.uLen = uLen;
      shader.uniforms.uTimeF = uTimeF;
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", `#include <common>
          uniform float uLayer; uniform float uLen; uniform float uTimeF;`)
        .replace("#include <skinning_vertex>", `#include <skinning_vertex>
          vec3 furNrm = normalize(normal);
          vec3 flow = normalize(vec3(0.1, -0.25, 0.0));
          float grad = 1.0 - abs(dot(furNrm, vec3(0.0, 1.0, 0.0)));
          transformed += furNrm * uLen * uLayer;
          transformed += flow * uLen * uLayer * (0.3 + grad * 0.5);
          transformed += furNrm * uLen * uLayer * 0.15 * sin(uTimeF * 2.0 + position.y * 14.0);`);
    };
    furMats.push(mat);
    const mesh = new THREE.SkinnedMesh(srcGeo, mat);
    mesh.bindMode = catMesh.bindMode;
    mesh.bindMatrix.copy(catMesh.bindMatrix);
    mesh.bindMatrixInverse.copy(catMesh.bindMatrixInverse);
    mesh.bind(catMesh.skeleton, catMesh.bindMatrix);
    mesh.frustumCulled = false;
    furGroup.add(mesh);
  }
  catRoot.add(furGroup);
}

/* ---------- L3: 风格向量（API 驱动 + 平滑插值） ---------- */
const DEFAULT_STYLE = { energy: 0.6, gait: 1.0, tail: 1.0, bounce: 1.0, mood: 0.55, anim: "Idle" };
const target = { ...DEFAULT_STYLE };
const style = { ...DEFAULT_STYLE };   // 每帧向 target lerp
let lastVersion = -1;
let apiLive = false;
let lastFetchOk = 0;

const STATE_PILLS = {
  idle: "发呆中", walk: "散步中", gallop: "撒欢跑", eat: "干饭中",
  sleep: "打盹中", meow: "喵喵叫",
};

function setAnim(name) {
  if (!actions[name] || curAnim === name) return;
  curAnim = name;
  Object.entries(actions).forEach(([k, a]) => a.setEffectiveWeight(k === name ? 1 : 0));
}

function applyStyleNow() {
  const a = actions[curAnim];
  if (a) a.timeScale = style.gait * (curAnim === "Gallop" ? 1.15 : 1.0);
}

function hud() {
  document.getElementById("petName").textContent = NAME;
  const dot = document.getElementById("connDot");
  dot.className = apiLive ? "live" : (lastFetchOk ? "" : "err");
  const st = Object.entries(STATE_PILLS).find(([k]) => target.anim.startsWith(mapAnim(k)));
  document.getElementById("statePill").textContent = apiLive
    ? (st ? st[1] : target.anim) + (style.energy < 0.3 ? " · 有点蔫" : "")
    : "演示模式（未连 API）";
  const bars = { energy: 1, gait: 2, tail: 2, mood: 1 };
  for (const k of Object.keys(bars)) {
    document.getElementById("b_" + k).style.width = (target[k] / bars[k] * 100) + "%";
    document.getElementById("v_" + k).textContent = target[k].toFixed(2);
  }
}

function mapAnim(state) {
  return { idle: "Idle", walk: "Walk", gallop: "Gallop", eat: "Eating",
           sleep: "Idle_2_HeadLow", meow: "Idle_2" }[state] || "Idle";
}

async function pollStyle() {
  try {
    const r = await fetch(`${API_BASE}/v1/avatar/${encodeURIComponent(PET_ID)}/style`,
                          { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const body = await r.json();
    const v = body.style;
    if (v) {
      target.energy = v.energy; target.gait = v.gait;
      target.tail = v.tail; target.bounce = v.bounce; target.mood = v.mood;
      target.anim = v.anim || "Idle";
      if (v.version !== lastVersion) {
        lastVersion = v.version;
        setAnim(target.anim);
      }
    } else {
      // 数据不足 → 演示猫性格（仍可看效果）
      Object.assign(target, DEFAULT_STYLE);
      setAnim("Idle");
    }
    apiLive = true;
    lastFetchOk = Date.now();
  } catch (e) {
    apiLive = false;
  }
  hud();
}

/* ---------- 尾巴/耳朵程序动画 ---------- */
function tickTail(t) {
  const mood = style.mood;
  tailBones.forEach((bone, i) => {
    const ph = t * style.tail * (1.2 + mood * 2.2) + i * 0.55;
    bone.rotation.z = Math.sin(ph) * 0.06 * style.tail * (0.3 + mood) * (i / Math.max(1, tailBones.length) + 0.3);
    bone.rotation.x = Math.cos(ph * 0.7) * 0.03 * style.tail + (1 - mood) * 0.12 * (i / Math.max(1, tailBones.length));
  });
  earBones.forEach((b, i) => {
    b.rotation.z = Math.sin(t * 0.8 + i * 2.1) * 0.05 * (0.4 + mood);
  });
}

/* ---------- 主循环 ---------- */
const clock = new THREE.Clock();
let tAcc = 0;

function loop() {
  requestAnimationFrame(loop);
  const dt = Math.min(clock.getDelta(), 0.05);
  tAcc += dt;
  // 风格平滑跟随
  const k = 1 - Math.exp(-dt * 1.5);
  for (const key of Object.keys(target)) {
    if (typeof target[key] === "number") style[key] += (target[key] - style[key]) * k;
  }
  applyStyleNow();
  mixer.update(dt);
  tickTail(tAcc);
  furMats.forEach((m) => (m.userData.uniforms.uTimeF.value = tAcc));
  if (catRoot) {
    // 睡觉时呼吸起伏要慢而浅
    const amp = curAnim === "Idle_2_HeadLow" ? 0.006 : 0.02;
    const spd = curAnim === "Idle_2_HeadLow" ? 1.1 : style.gait * 4;
    catRoot.position.y = Math.abs(Math.sin(tAcc * spd)) * amp * style.bounce;
  }
  renderer.render(scene, camera);
}
loop();

pollStyle().then(() => { setInterval(pollStyle, POLL_MS); });

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

/* 调试钩子（验收用） */
window.__twin = {
  get style() { return { ...style }; },
  get target() { return { ...target }; },
  get curAnim() { return curAnim; },
  get hasCat() { return !!catRoot; },
  get furCount() { return furGroup ? furGroup.children.length : 0; },
  get actions() { return Object.keys(actions); },
  get apiLive() { return apiLive; },
  setTarget(o) { Object.assign(target, o); setAnim(o.anim || curAnim); },
};
