"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false,
  unwrap: null, unwrapImage: null};
const settings = [...document.querySelectorAll("[data-setting]")];
const angle = (number) => number.toFixed(2) + "°";
const statusText = {pass: "在容差内", miss: "超出容差", outside: "反光点在球外"};
const UNWRAP_SCALE = 2;

async function api(path, options) {
  const response = await fetch("/glint" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  ctx.lineWidth = Math.max(1, card.width / 550);
  ctx.strokeStyle = "#8ce3cc";
  ctx.beginPath();
  ctx.arc(cfg.cx, cfg.cy, cfg.radius, 0, 2 * Math.PI);
  ctx.stroke();
  const size = Math.max(6, card.width / 135);
  const drawn = state.result.reading;
  ctx.strokeStyle = "#ffc887";
  ctx.beginPath();
  ctx.moveTo(drawn.x - size, drawn.y);
  ctx.lineTo(drawn.x + size, drawn.y);
  ctx.moveTo(drawn.x, drawn.y - size);
  ctx.lineTo(drawn.x, drawn.y + size);
  ctx.stroke();
  if (probe) {
    ctx.strokeStyle = "#f4fffc";
    ctx.strokeRect(probe.x - size / 2, probe.y - size / 2, size, size);
  }
}

function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  const values = state.result.profile;
  const {card} = state;
  const xAt = (x) => 38 + (width - 61) * x / (card.width - 1);
  const yAt = (y) => height - 24 - (height - 38) * y / 255;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#162326";
  ctx.fillRect(0, 0, width, height);
  ctx.font = "12px monospace";
  ctx.fillStyle = "#a1b3b2";
  ctx.strokeStyle = "#32494b";
  for (const level of [0, 128, 255]) {
    ctx.fillText(level, 5, yAt(level) + 3);
    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(level));
    ctx.lineTo(xAt(card.width - 1), yAt(level));
    ctx.stroke();
  }
  ctx.fillText("0", xAt(0), height - 6);
  ctx.fillText(String(card.width - 1), width - 36, height - 6);
  ctx.strokeStyle = "#e9b575";
  ctx.setLineDash([4, 6]);
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(card.settings.floor));
  ctx.lineTo(xAt(card.width - 1), yAt(card.settings.floor));
  ctx.stroke();
  ctx.setLineDash([]);
  if (!values.length) {
    ctx.fillText("选中行未穿过标定球体", 60, 42);
    return;
  }
  ctx.strokeStyle = "#8ce3cc";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (const [index, row] of values.entries()) {
    if (index === 0) ctx.moveTo(xAt(row.x), yAt(row.level));
    else ctx.lineTo(xAt(row.x), yAt(row.level));
  }
  ctx.stroke();
  const read = state.result.reading;
  if (read.direction) {
    ctx.fillStyle = "#e9b575";
    ctx.beginPath();
    ctx.arc(xAt(read.x), yAt(read.level), 4, 0, 2 * Math.PI);
    ctx.fill();
  }
}

function pointLabel(point) {
  const direction = point.direction ? angle(point.direction.yaw) + " / " + angle(point.direction.pitch) : "球外";
  return "(" + point.x + ", " + point.y + ") · " + point.level.toFixed(1) + " · " + direction;
}

function cellAt(clientX, clientY) {
  const canvas = get("unwrap");
  const meta = state.unwrap;
  const rect = canvas.getBoundingClientRect();
  const u = Math.max(0, Math.min(meta.width - 1,
    Math.floor((clientX - rect.left) * meta.width / rect.width)));
  const v = Math.max(0, Math.min(meta.height - 1,
    Math.floor((clientY - rect.top) * meta.height / rect.height)));
  return {u, v};
}

function paintUnwrap() {
  const canvas = get("unwrap");
  const ctx = canvas.getContext("2d");
  const meta = state.unwrap;
  if (!meta) {
    canvas.width = 2;
    canvas.height = 2;
    ctx.clearRect(0, 0, 2, 2);
    return;
  }
  canvas.width = meta.width * UNWRAP_SCALE;
  canvas.height = meta.height * UNWRAP_SCALE;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (state.unwrapImage) ctx.drawImage(state.unwrapImage, 0, 0, canvas.width, canvas.height);
  const read = state.result?.reading;
  if (!read?.direction) return;
  const span = (read.direction.yaw - meta.seam_yaw_deg + 360) % 360;
  const u = Math.min(meta.width - 1, Math.floor(span / meta.cell_yaw_deg));
  const v = Math.min(meta.height - 1, Math.max(0, Math.floor((90 - read.direction.pitch) / meta.cell_pitch_deg)));
  const x = (u + 0.5) * UNWRAP_SCALE;
  const y = (v + 0.5) * UNWRAP_SCALE;
  ctx.strokeStyle = "#f4fffc";
  ctx.lineWidth = 2;
  ctx.strokeRect(x - 4, y - 4, 8, 8);
}

function describeUnwrap() {
  const meta = state.unwrap;
  if (!meta) return;
  const percent = meta.transparent_fraction * 100;
  get("seam-report").textContent = "接缝方位 " + angle(meta.seam_yaw_deg) +
    " · 离相机背面 " + angle(meta.back_offset_deg);
  get("unwrap-note").textContent = "共 " + meta.width + " × " + meta.height + " 格（每格 " +
    meta.cell_yaw_deg.toFixed(2) + "°）· 左端 " + angle(meta.first_yaw_deg) + "，右端 " +
    angle(meta.last_yaw_deg) + "，右端接回左端 · 透明 " + meta.transparent_cells + " 格（" +
    (percent < 1 ? percent.toFixed(2) : percent.toFixed(1)) + "%），其余每格都取自标定球内实际原片像素 · " +
    "接缝限定在相机背面 ±" + meta.seam_limit_deg + "° 内，并按亮区下限优先避开实拍亮部。";
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? result.reading.x;
  get("probe-y").value = state.probe?.y ?? result.reading.y;
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const direction = result.reading.direction;
  get("yaw").textContent = direction ? angle(direction.yaw) : "—";
  get("pitch").textContent = direction ? angle(direction.pitch) : "—";
  get("verdict").dataset.status = result.aim.status;
  get("verdict").textContent = statusText[result.aim.status];
  get("gap").textContent = result.aim.error_deg === null ? "球外点没有方向" :
    "距目标 " + angle(result.aim.error_deg) + " · 容差 " + angle(card.settings.tolerance);
  get("selected-level").textContent = "亮度 " + result.reading.level.toFixed(1) +
    (result.reading.above_floor ? " · 达到亮区下限" : " · 低于亮区下限");
  for (const [id, filename] of Object.entries({proof: "export/proof.png", csv: "export/profile.csv",
    zip: "export/observation.zip", "unwrap-download": "unwrap.png"})) {
    get(id).href = "/glint" + route("/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
  paintUnwrap();
  describeUnwrap();
}

async function loadUnwrap() {
  const meta = await api(route("/unwrap") + savedQuery());
  const image = new Image();
  image.src = "/glint" + route("/unwrap.png") + savedQuery();
  await image.decode();
  state.unwrap = meta;
  state.unwrapImage = image;
  describeUnwrap();
  paintUnwrap();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/glint" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, unwrap: null, unwrapImage: null});
  get("library").value = card.id;
  show();
  await loadUnwrap();
}

async function pickCell(u, v) {
  const reading = await api(route("/unwrap/cell") + savedQuery() + "&u=" + u + "&v=" + v);
  if (!reading.opaque) {
    notice("(" + reading.yaw_deg.toFixed(2) + "°, " + reading.pitch_deg.toFixed(2) +
      "°) 没有实拍原片来源，反光点保持不变。");
    return;
  }
  settings.find((field) => field.dataset.setting === "seed_x").value = reading.source.x;
  settings.find((field) => field.dataset.setting === "seed_y").value = reading.source.y;
  notice("已取回原片像素 (" + reading.source.x + ", " + reading.source.y + ")，保存并重测后更新校样和灯位核对。");
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      settings.find((field) => field.dataset.setting === "seed_x").value = x;
      settings.find((field) => field.dataset.setting === "seed_y").value = y;
      notice("已填入反光坐标，保存后更新校样和测光结果。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
get("unwrap").addEventListener("click", (event) => {
  if (!state.unwrap) return;
  const {u, v} = cellAt(event.clientX, event.clientY);
  action(() => pickCell(u, v));
});
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
