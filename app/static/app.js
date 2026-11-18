"use strict";

const get = (id) => document.getElementById(id);
const roles = {erase: "Удалённый профиль", same_tenant: "Сосед у того же клиента", other_tenant: "Тот же ID у другого клиента"};
const stages = {restored: "Восстановление", retried: "Повтор", repeatability: "Между прогонами"};
const codes = {OK: "В норме", ERASED_ROWS_PRESENT: "Остались записи", CONTROL_MISSING: "Потеряны записи",
  CONTROL_CHANGED: "Изменены данные", REPLAY_DRIFT: "Есть расхождение"};
let catalog = [];
let report;
let generation = 0;

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function render() {
  const bad = report.checks.filter((c) => c.code !== "OK");
  get("verdict").classList.toggle("fail", bad.length > 0);
  get("verdict-title").textContent = bad.length ? "Обнаружены отклонения" : "Удаление и сохранность подтверждены";
  get("score").textContent = (report.total - report.failed) + " / " + report.total;
  const groups = new Map();
  for (const c of report.checks) {
    const key = c.table + "/" + c.role;
    if (!groups.has(key)) groups.set(key, {});
    groups.get(key)[c.stage] = c;
  }
  const rows = get("rows");
  rows.replaceChildren();
  for (const group of groups.values()) {
    if (get("only-failures").checked && Object.values(group).every((c) => c.code === "OK")) continue;
    const c = group.restored;
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.append(element("code", c.table), document.createTextNode(roles[c.role]));
    row.append(label);
    for (const stage of Object.keys(stages)) {
      const code = group[stage].code;
      row.append(element("td", (code === "OK" ? "✓ " : "! ") + codes[code], code === "OK" ? "ok" : "issue"));
    }
    rows.append(row);
  }
  get("empty").hidden = rows.childElementCount > 0;
  get("detail").hidden = bad.length === 0;
  get("findings").replaceChildren(...bad.map((c) => element("li",
    [c.table, roles[c.role], stages[c.stage]].join(" · ") + ". " + codes[c.code] + ". " +
    "Ожидалось строк: " + c.expected + ", найдено: " + c.actual +
    ". Не совпало с образцом: −" + c.removed + " / +" + c.added + ".")));
  get("command").textContent = "python3 -m restorecheck demo --scenario " + report.scenario;
}

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

function feedback(text) {
  get("feedback").hidden = !text;
  get("feedback").textContent = text;
}

async function run(id) {
  const request = ++generation;
  get("report").hidden = true;
  const scenario = catalog.find((s) => s.id === id);
  get("description").textContent = scenario.description;
  feedback("Выполняется репетиция…");
  try {
    const data = await json("/api/drills/" + encodeURIComponent(id));
    if (request !== generation) return;
    report = data;
    render();
    get("report").hidden = false;
    feedback("");
  } catch {
    if (request === generation) feedback("Не удалось провести репетицию. Обновите страницу для повтора.");
  }
}

async function init() {
  try {
    catalog = await json("/api/scenarios");
    const select = get("scenario");
    select.replaceChildren(...catalog.map((s) => new Option(s.name, s.id)));
    const requested = new URL(location.href).searchParams.get("scenario");
    select.value = catalog.some((s) => s.id === requested) ? requested : "clean";
    select.disabled = false;
    select.addEventListener("change", () => {
      const url = new URL(location.href);
      url.searchParams.set("scenario", select.value);
      history.replaceState(null, "", url);
      run(select.value);
    });
    get("only-failures").addEventListener("change", () => { if (report) render(); });
    await run(select.value);
  } catch {
    feedback("Не удалось загрузить сценарии. Обновите страницу для повтора.");
  }
}

init();
