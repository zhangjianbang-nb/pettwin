/* PetTwin App 逻辑（v0.6）
 * 四 tab：分身(iframe desktop 页) / 摄像头(getUserMedia 推帧/推 bbox) / 叫声(MediaRecorder→wav) / 看板
 * server 地址持久化（AndroidBridge.saveState / localStorage 兜底）。
 */
"use strict";

const $ = (id) => document.getElementById(id);
let API = localStorage.getItem("pt_server") || "";
let petId = localStorage.getItem("pt_pet") || "";
const PET_ID_E2E = new URLSearchParams(location.search).get("pet") || "";

/* ---------- tab 切换 ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("on"));
    document.querySelectorAll(".pane").forEach((p) => p.classList.remove("on"));
    btn.classList.add("on");
    $("tab-" + btn.dataset.tab).classList.add("on");
    if (btn.dataset.tab === "dash") refreshDash();
    if (btn.dataset.tab === "meow") refreshMeowList();
  };
});

/* ---------- 连接 ---------- */
$("serverUrl").value = API;
$("btnConn").onclick = async () => {
  API = $("serverUrl").value.replace(/\/+$/, "");
  localStorage.setItem("pt_server", API);
  try {
    const h = await jget("/v1/health");
    toast("已连接: " + JSON.stringify(h));
    await loadPets();
    if (petId) bindTwin();
  } catch (e) {
    toast("连接失败: " + e.message);
  }
};

function toast(msg) {
  if (window.AndroidBridge) AndroidBridge.toast(msg);
  else console.log("[toast]", msg);
}

async function jget(path) {
  const r = await fetch(API + path, { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function jpost(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("HTTP " + r.status + " " + (await r.text()).slice(0, 100));
  return r.json();
}

/* ---------- 宠物与分身 ---------- */
async function loadPets() {
  const pets = await jget("/v1/pets");
  const ul = $("petList");
  ul.innerHTML = "";
  for (const p of pets) {
    const li = document.createElement("li");
    li.innerHTML = `<b>${p.name}</b> <span class="t">${p.pet_id} ${p.species || ""}</span>`;
    li.onclick = () => {
      petId = p.pet_id;
      localStorage.setItem("pt_pet", petId);
      bindTwin();
      toast("已选: " + p.name);
      // 选中即跳到分身 tab
      document.querySelector('[data-tab="twin"]').click();
    };
    ul.appendChild(li);
  }
  if (PET_ID_E2E) petId = PET_ID_E2E;
  if (petId) bindTwin();
}

function bindTwin() {
  const f = $("twinFrame");
  f.src = `${API.replace(/^http/, "http")}/static/desktop/index.html?pet=${petId}&api=${API}&name=${petId}`;
  $("twinFrame").dataset.bound = "1";
}

/* ---------- 摄像头推帧/推 bbox ---------- */
let camStream = null, camTimer = null, canvas = null;

$("btnCam").onclick = async () => {
  if (camStream) { stopCam(); return; }
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    $("camView").srcObject = camStream;
    $("btnCam").textContent = "关闭摄像头";
    $("btnCam").classList.add("rec");
    scheduleCam();
  } catch (e) {
    toast("摄像头失败: " + e.message);
  }
};

$("camMode").onchange = () => { $("zoneRow").hidden = $("camMode").value !== "pose"; scheduleCam(); };

function scheduleCam() { clearInterval(camTimer); startCamLoop(); }

function startCamLoop() {
  const mode = $("camMode").value;
  if (!camStream || mode === "off" || !petId) return;
  canvas = canvas || document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const period = mode === "frame" ? 2000 : 5000;
  camTimer = setInterval(async () => {
    try {
      const v = $("camView");
      if (!v.videoWidth) return;
      canvas.width = 320; canvas.height = Math.round(320 * v.videoHeight / v.videoWidth);
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      if (mode === "frame") {
        const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.7));
        const fd = new FormData();
        fd.append("image", blob, "f.jpg");
        const r = await fetch(`${API}/v1/camera/ingest_frame?pet_id=${petId}`, { method: "POST", body: fd });
        if (!r.ok) throw new Error("ingest " + r.status);
        $("camStatus").textContent = "推帧中… " + new Date().toLocaleTimeString();
      } else {
        // bbox 模式：无 YOLO 时用"全幅运动区近似"——把整帧当一个 bbox（宽扁假设）
        // 真姿势识别由 server 端 YOLO 增强或后续帧差 bbox 化
        const zones = parseZones();
        const w = canvas.width, h = canvas.height;
        // 简易运动检测：帧差中心
        const cur = ctx.getImageData(0, 0, w, h).data;
        if (window.__prev) {
          const bb = diffBBox(window.__prev, cur, w, h);
          if (bb) {
            await jpost(`/v1/pose/${petId}`, { pet_id: petId, bboxes: [bb], zones, ts: Date.now() / 1000 });
            $("camStatus").textContent = "推 bbox… " + JSON.stringify(bb);
          }
        }
        window.__prev = cur;
      }
    } catch (e) { $("camStatus").textContent = "推送失败: " + e.message; }
  }, period);
}

function diffBBox(prev, cur, w, h) {
  let minx = w, miny = h, maxx = 0, maxy = 0, n = 0;
  for (let y = 0; y < h; y += 2) {
    for (let x = 0; x < w; x += 2) {
      const i = (y * w + x) * 4;
      const d = Math.abs(cur[i] - prev[i]) + Math.abs(cur[i + 1] - prev[i + 1]);
      if (d > 60) { n++; if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y; }
    }
  }
  if (n < 30) return null; // 没动
  return { x1: minx, y1: miny, x2: maxx + 2, y2: maxy + 2 };
}

function parseZones() {
  const v = $("zoneKb").value.split(",").map(Number);
  if (v.length !== 4 || v.some(isNaN)) return {};
  return { keyboard: v };
}

function stopCam() {
  clearInterval(camTimer); camTimer = null;
  camStream?.getTracks().forEach((t) => t.stop());
  camStream = null; window.__prev = null;
  $("camView").srcObject = null;
  $("btnCam").textContent = "开启摄像头";
  $("btnCam").classList.remove("rec");
  $("camStatus").textContent = "已停止。";
}

/* ---------- 叫声 ---------- */
let mediaRec = null, recChunks = [];

$("btnRec").onpointerdown = async () => {
  if (!petId) { toast("先在看板选宠物"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRec = new MediaRecorder(stream);
    recChunks = [];
    mediaRec.ondataavailable = (e) => recChunks.push(e.data);
    mediaRec.onstop = () => sendMeow(stream);
    mediaRec.start();
    $("btnRec").textContent = "⏹ 松开识别";
  } catch (e) { toast("麦克风失败: " + e.message); }
};
$("btnRec").onpointerup = () => {
  if (mediaRec && mediaRec.state === "recording") mediaRec.stop();
};

async function sendMeow(stream) {
  stream.getTracks().forEach((t) => t.stop());
  $("btnRec").textContent = "🎤 按住录音识别";
  const blob = new Blob(recChunks, { type: MediaRecorder.mimeType || "audio/webm" });
  const fd = new FormData();
  fd.append("audio", blob, "m.webm");
  fd.append("repeats", "1");
  const r = await fetch(`/v1/meow/${petId}`.replace(/^/, API), { method: "POST", body: fd });
  if (!r.ok) { toast("识别失败 " + r.status); return; }
  const body = await r.json();
  const card = $("meowResult");
  card.hidden = false;
  card.querySelector(".kind").textContent = kindLabel(body.kind) + (body.anomalous ? " ⚠ 异常" : "");
  card.querySelector(".note").textContent = body.note;
  card.querySelector(".meta").textContent =
    `时长 ${body.features.duration_s.toFixed(2)}s · 基频 ${Math.round(body.features.f0_hz)}Hz · conf ${body.confidence}`;
  refreshMeowList();
}

function kindLabel(k) {
  return { hunger: "🍖 讨食", greeting: "👋 打招呼", distress: "😿 痛苦预警", playful: "🎾 玩耍邀请", other: "🐱 其他叫声" }[k] || k;
}

async function refreshMeowList() {
  if (!petId || !API) return;
  try {
    const h = await jget(`/v1/meow/${petId}/history?limit=10`);
    $("meowList").innerHTML = h.history.map((x) =>
      `<li>${kindLabel(x.kind)} <span class="t">${new Date(x.ts * 1000).toLocaleString()} · ${x.note}</span></li>`).join("");
  } catch (e) { /* ignore */ }
}

/* ---------- 看板 ---------- */
async function refreshDash() {
  if (!petId || !API) return;
  try {
    const ins = await jget(`/v1/insights/${petId}`);
    $("insightList").innerHTML = ins.length
      ? ins.map((i) => `<li><b>${i.level}</b> ${i.title}<span class="t">${i.detail || ""}</span></li>`).join("")
      : "<li>暂无洞察</li>";
  } catch (e) { $("insightList").innerHTML = "<li>洞察加载失败</li>"; }
  try {
    const st = await jget(`/v1/avatar/${petId}/style`);
    $("styleCard").textContent = JSON.stringify(st.style || { hint: "数据不足" }, null, 1);
  } catch (e) { /* ignore */ }
}

/* ---------- 启动 ---------- */
if (API) { $("btnConn").click(); }
