"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false, rim: null};
const settings = [...document.querySelectorAll("[data-setting]")];
const angle = (number) => number.toFixed(2) + "°";
const statusText = {pass: "在容差内", miss: "超出容差", outside: "反光点在球外"};

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
  if (state.rim?.preview) {
    const ring = state.rim.preview;
    ctx.strokeStyle = state.rim.adoptable ? "#f2d16b" : "#d89090";
    ctx.setLineDash([7, 5]);
    ctx.beginPath();
    ctx.arc(ring.cx, ring.cy, ring.radius, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.setLineDash([]);
  }
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
  showRim();
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/glint" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
}

function rimSummary(rim) {
  const parts = ["边缘点 " + rim.evidence.edge_points, "粗圆环带 ±" + Math.round(rim.evidence.band * 100) + "%"];
  if (rim.preview) {
    parts.push("确认弧 " + rim.preview.support_bins + "/120 扇区", "象限 " + rim.preview.supported_quadrants + "/4",
      "贴边误差 " + rim.preview.residual.toFixed(2) + " px");
  }
  return parts.join(" · ");
}

function showRim() {
  const rim = state.rim;
  const adopt = get("rim-adopt");
  get("rim-status").textContent = rim ? (rim.adoptable ? "可以采用的精修圈" : "轮廓不足以定准") : "尚未精修";
  get("rim-status").dataset.status = rim ? (rim.adoptable ? "adoptable" : "insufficient") : "idle";
  get("rim-detail").textContent = rim ? rimSummary(rim) : "以手填中心和半径为粗标，向内外各找至多一成半半径的实拍球缘。";
  const reason = get("rim-reason");
  if (!rim) {
    reason.textContent = "";
    reason.hidden = true;
  } else if (rim.adoptable) {
    const aim = rim.candidate?.aim;
    const verdict = aim ? (aim.status === "pass" ? "在容差内" : "超出容差") : "均可求方向";
    reason.textContent = "精修圈与已存反光点和目标容差重测" + verdict + (rim.rivals.length > 1 ? "，相近轮廓判定一致" : "") + "。采用后只替换中心和半径。";
    reason.hidden = false;
  } else {
    reason.textContent = rim.reason;
    reason.hidden = false;
  }
  adopt.hidden = !rim?.adoptable;
}async function rim() {
  state.rim = await api(route("/rim") + savedQuery());
  show();
}

async function adoptRim() {
  if (!state.rim?.adoptable) return;
  const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
  const card = await api(route("/rim"), {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields,
                          candidate: state.rim.candidate}),
  });
  await open(card);
  await library();
  notice("已采用精修圈，中心和半径已填回标定。");
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/glint" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, rim: null});
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
get("rim-check").addEventListener("click", () => action(rim, "rim-notice"));
get("rim-adopt").addEventListener("click", () => action(adoptRim, "rim-notice"));
action(library);
