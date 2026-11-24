"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false, patchPaths: null};
const settings = [...document.querySelectorAll("[data-setting]")];
const angle = (number) => number.toFixed(2) + "°";
const statusText = {
  pass: "合格 · 在容差内",
  miss: "不合格 · 超出容差",
  outside: "请重新选点 · 反光点在球外",
  below_floor: "请重新选点 · 点中像素低于亮区下限",
  reselect: "请重新选点",
};
const statusWord = {
  pass: "合格",
  miss: "不合格",
  outside: "重新选点",
  below_floor: "重新选点",
  reselect: "重新选点",
};
const statusDetail = {
  pass: "在容差内",
  miss: "超出容差",
  outside: "反光点在球外",
  below_floor: "点中像素低于亮区下限",
  reselect: "",
};

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

function patchPaths(result) {
  if (state.patchPaths && state.patchPaths.result === result) return state.patchPaths;
  const patch = result && result.patch;
  if (!patch || patch.status !== "ok") {
    state.patchPaths = {result, sheet: null, over: null};
    return state.patchPaths;
  }
  const sheet = new Path2D();
  for (const [x, y] of patch.pixels) sheet.rect(x, y, 1, 1);
  const over = new Path2D();
  for (const [x, y] of patch.over_pixels) over.rect(x, y, 1, 1);
  state.patchPaths = {result, sheet, over};
  return state.patchPaths;
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe, result} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;

  if (result.check_mode === "patch" && result.patch && result.patch.status === "ok") {
    const paths = patchPaths(result);
    ctx.fillStyle = "rgba(132, 226, 203, 0.42)";
    ctx.fill(paths.sheet);
    ctx.fillStyle = "rgba(255, 107, 94, 0.66)";
    ctx.fill(paths.over);
  }

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

  const patch = result.patch;
  if (result.check_mode === "patch" && patch && patch.status === "ok") {
    const {center, far_point: far} = patch;
    ctx.lineWidth = Math.max(1.6, card.width / 420);
    ctx.strokeStyle = "#ff8a7a";
    ctx.beginPath();
    ctx.moveTo(center.x, center.y);
    ctx.lineTo(far.x, far.y);
    ctx.stroke();
    const d = Math.max(5, card.width / 90);
    ctx.strokeStyle = "#7dffe2";
    ctx.beginPath();
    ctx.moveTo(center.x, center.y - d);
    ctx.lineTo(center.x + d, center.y);
    ctx.lineTo(center.x, center.y + d);
    ctx.lineTo(center.x - d, center.y);
    ctx.closePath();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(center.x, center.y, d * 1.55, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.strokeStyle = "#ff6b5e";
    ctx.beginPath();
    ctx.arc(far.x, far.y, d * 0.7, 0, 2 * Math.PI);
    ctx.stroke();
    const labelSize = Math.max(11, Math.round(card.width / 52));
    ctx.font = `600 ${labelSize}px "Glint Noto SC", "Noto Sans CJK SC", sans-serif`;
    ctx.fillStyle = "#cffff2";
    ctx.textAlign = "center";
    ctx.fillText("建议中心", center.x, center.y - d * 2.5);
    ctx.fillStyle = "#ff9d92";
    const farLabel = "最远 " + patch.radius_deg.toFixed(1) + "°";
    if (far.x >= center.x) {
      ctx.textAlign = "left";
      ctx.fillText(farLabel, Math.min(far.x + d, card.width - 110), far.y + d * 1.8);
    } else {
      ctx.textAlign = "right";
      ctx.fillText(farLabel, Math.max(far.x - d, 104), far.y + d * 1.8);
    }
    ctx.textAlign = "start";
  }

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

function syncCheckMode(mode) {
  const hidden = settings.find((field) => field.dataset.setting === "check_mode");
  if (hidden) hidden.value = mode;
  document.querySelectorAll("[data-check-mode]").forEach((button) =>
    button.setAttribute("aria-pressed", String(button.dataset.checkMode === mode)));
  get("mode-hint").dataset.mode = mode;
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
  const checkMode = card.settings.check_mode || card.settings.mode || result.check_mode || "point";
  syncCheckMode(checkMode);
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? result.reading.x;
  get("probe-y").value = state.probe?.y ?? result.reading.y;
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const direction = result.reading.direction;
  get("yaw").textContent = direction ? angle(direction.yaw) : "—";
  get("pitch").textContent = direction ? angle(direction.pitch) : "—";
  get("verdict").dataset.status = result.aim.status;
  const verdict = get("verdict");
  verdict.replaceChildren();
  const word = document.createElement("b");
  word.className = "verdict-word";
  word.dataset.testid = "verdict-word";
  word.textContent = statusWord[result.aim.status] || result.aim.status;
  verdict.append(word);
  const detailText = statusDetail[result.aim.status];
  if (detailText) {
    const detail = document.createElement("span");
    detail.className = "verdict-detail";
    detail.textContent = " · " + detailText;
    verdict.append(detail);
  }
  verdict.title = statusText[result.aim.status] || result.aim.status;
  const patch = result.patch;
  const patchReady = checkMode === "patch" && patch && patch.status === "ok";
  get("patch-panel").hidden = !patchReady;
  const patchCsv = get("patch-csv");
  patchCsv.hidden = !patchReady;
  if (patchReady) {
    get("center-yaw").textContent = angle(patch.center.yaw);
    get("center-pitch").textContent = angle(patch.center.pitch);
    get("patch-radius").textContent =
      "距建议中心最远的亮片角距 " + angle(patch.radius_deg) +
      "（最远端 " + patch.far_point.x + ", " + patch.far_point.y + "）";
    get("patch-coverage").textContent =
      "整片 " + patch.count + " 个实测方向 · 最远端距目标 " + angle(patch.target_worst_deg) +
      " · 容差 " + angle(card.settings.tolerance) +
      " · 超标 " + patch.outside_count + "/" + patch.count + " px";
    const callout = get("patch-callout");
    if (result.aim.center_in_edge_out) {
      callout.hidden = false;
      callout.textContent = "建议中心距目标仅 " + angle(patch.center.target_gap_deg) +
        "、在容差内，但亮片边缘最远 " + angle(patch.target_worst_deg) +
        " 已超出 " + angle(card.settings.tolerance) + "，整片仍判不合格。";
      callout.dataset.tone = "edge";
    } else if (result.aim.status === "pass") {
      callout.hidden = false;
      callout.textContent = "整片所有实测方向都在原目标角度容差内。";
      callout.dataset.tone = "ok";
    } else {
      callout.hidden = true;
      callout.textContent = "";
    }
    get("gap").textContent = "整片最大目标夹角 " + angle(result.aim.error_deg) +
      " · 容差 " + angle(card.settings.tolerance);
  } else if (checkMode === "patch" && result.aim.reselect) {
    get("gap").textContent = result.aim.message || "请重新选点后保存重测";
  } else {
    get("gap").textContent = result.aim.error_deg === null ? "球外点没有方向" :
      "距目标 " + angle(result.aim.error_deg) + " · 容差 " + angle(card.settings.tolerance);
  }
  get("selected-level").textContent = "亮度 " + result.reading.level.toFixed(1) +
    (result.reading.above_floor ? " · 达到亮区下限" : " · 低于亮区下限");
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/glint" + route("/export/" + filename) + savedQuery();
  }
  if (patchReady) patchCsv.href = "/glint" + route("/export/patch.csv") + savedQuery();
  paintSource();
  paintChart();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => paintSource());
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/glint" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, patchPaths: null});
  get("library").value = card.id;
  show();
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
document.querySelectorAll("[data-check-mode]").forEach((button) => button.addEventListener("click", () => {
  if (!state.card) return;
  const mode = button.dataset.checkMode;
  if ((state.card.settings.check_mode || "point") === mode) return;
  syncCheckMode(mode);
  // Adopt immediately: persist the mode with the current form values so the
  // sheet (or single point) remeasures and a reopen keeps the chosen style.
  action(saveSettings);
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
async function saveSettings() {
  const fields = Object.fromEntries(settings.map((field) => [
    field.dataset.setting,
    field.dataset.setting === "check_mode" ? field.value : Number(field.value),
  ]));
  const card = await api(route(""), {
    method: "PATCH", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
  });
  await open(card);
  await library();
}
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(saveSettings);
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
