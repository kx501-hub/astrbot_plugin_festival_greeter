const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginPage;
let state, holidays = [], birthdays = [], dirty = false, busy = false;
const clone = (value) => JSON.parse(JSON.stringify(value));
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function message(text, error = false) {
  $("status").textContent = text;
  $("status").className = `status${error ? " error" : ""}`;
}
function controls() {
  document.querySelectorAll("section").forEach((section) => { section.inert = busy || !state; });
  for (const id of ["save", "reload", "add-birthday", "add-holiday", "preview", "history-refresh"]) {
    $(id).disabled = busy || !state;
  }
  $("reload").textContent = dirty ? "放弃草稿并重载" : "重新载入";
}
function changed() {
  dirty = true;
  message("有未保存的更改；预览使用当前草稿。");
  $("preview-result").replaceChildren();
  controls();
}
async function run(action) {
  if (busy) return;
  busy = true; controls();
  try { await action(); }
  catch (error) { message(error.message || String(error), true); }
  finally { busy = false; controls(); }
}
function field(parent, title, record, key, options = {}) {
  const label = node("label", title, options.wide ? "wide" : "");
  const input = node(options.choices ? "select" : options.text ? "textarea" : "input");
  if (options.choices) {
    for (const [value, text] of options.choices) {
      const option = node("option", text); option.value = value; input.append(option);
    }
  } else if (!options.text) input.type = options.type || "text";
  if (options.type === "checkbox") { input.checked = !!record[key]; label.className = "check"; }
  else input.value = record[key] ?? "";
  if (options.min !== undefined) input.min = options.min;
  if (options.max !== undefined) input.max = options.max;
  input.addEventListener("input", () => {
    record[key] = options.type === "checkbox" ? input.checked
      : options.type === "number" ? (input.value === "" ? null : Number(input.value)) : input.value;
    changed();
  });
  label.append(input); parent.append(label);
  return input;
}
function renderEntries(kind, records) {
  const parent = $(kind); parent.replaceChildren();
  if (!records.length) parent.append(node("p", "还没有条目，点击上方按钮添加。", "empty"));
  records.forEach((record, index) => {
    const card = node("div", undefined, "entry");
    const header = node("div", undefined, "entry-head");
    header.append(node("h3", `${kind === "birthdays" ? "生日" : "节日"} ${index + 1}`));
    const remove = node("button", "删除", "danger");
    remove.onclick = () => { records.splice(index, 1); changed(); renderEntries(kind, records); };
    header.append(remove); card.append(header);
    const grid = node("div", undefined, "grid");
    if (kind === "birthdays") {
      field(grid, "祝福对象", record, "recipient");
      field(grid, "完整目标群会话 ID", record, "target_session");
      const calendar = field(grid, "历法", record, "calendar", {choices: [["solar", "公历"], ["lunar", "农历"]]});
      field(grid, "月份", record, "month", {type: "number", min: 1, max: 12});
      const day = field(grid, "日期", record, "day", {type: "number", min: 1, max: record.calendar === "lunar" ? 30 : 31});
      const leap = field(grid, "仅对应农历闰月发送", record, "leap_month", {type: "checkbox"});
      leap.disabled = record.calendar !== "lunar";
      calendar.addEventListener("change", () => {
        leap.disabled = record.calendar !== "lunar";
        day.max = record.calendar === "lunar" ? 30 : 31;
        if (leap.disabled) { record.leap_month = false; leap.checked = false; }
      });
      field(grid, "附加信息（可选）", record, "extra_info", {text: true, wide: true});
    } else {
      field(grid, "节日名称", record, "name");
      field(grid, "公历月份", record, "month", {type: "number", min: 1, max: 12});
      field(grid, "公历日期", record, "day", {type: "number", min: 1, max: 31});
      field(grid, "持续天数", record, "length_days", {type: "number", min: 1, max: 366});
      field(grid, "别名（用逗号分隔）", record, "aliasesText");
      field(grid, "说明（可选）", record, "description", {text: true, wide: true});
    }
    card.append(grid); parent.append(card);
  });
}
function draft() {
  const records = holidays.map(({aliasesText, ...item}) => ({
    ...item, aliases: aliasesText.split(/[,，]/).map((x) => x.trim()).filter(Boolean),
  }));
  // Retain the original simple list format where possible, for the standard config editor.
  const simple = records.every((item) => item.length_days === 1 && !item.description && !item.aliases.length
    && Object.keys(item).every((key) => ["name", "month", "day", "length_days", "aliases", "description"].includes(key)));
  return {
    birthdays: clone(birthdays),
    custom_holidays: simple ? records.flatMap((item) => [
      `${String(item.month).padStart(2, "0")}${String(item.day).padStart(2, "0")}`, item.name,
    ]) : records,
  };
}
async function load() {
  const loaded = await bridge.apiGet("settings");
  const raw = loaded.custom_holidays;
  if (!Array.isArray(raw) || !Array.isArray(loaded.birthdays)) throw new Error("旧配置格式无效，请先在插件基础配置中修正。");
  if (raw.some((x) => typeof x !== "string") && raw.some((x) => typeof x !== "object" || x === null)) {
    throw new Error("自定义节日混用了不同格式，请先修正，页面不会覆盖原数据。");
  }
  if (raw.every((x) => typeof x === "string") && raw.length % 2) throw new Error("自定义节日日期与名称未配对，请先在基础配置中修正。");
  holidays = raw.every((x) => typeof x === "string") ? Array.from({length: raw.length / 2}, (_, index) => ({
    name: raw[index * 2 + 1], month: Number(raw[index * 2].slice(0, 2)), day: Number(raw[index * 2].slice(2)),
    length_days: 1, description: "", aliasesText: "",
  })) : raw.map((item) => ({length_days: 1, description: "", ...clone(item), aliasesText: (item.aliases || []).join(", ")}));
  birthdays = clone(loaded.birthdays);
  state = loaded; dirty = false;
  $("summary").textContent = `${state.timezone} · 每日 ${state.trigger_time} · 人格 ${state.persona} · 普通节日目标群 ${state.targets.length} 个`;
  $("start").value = state.today;
  renderEntries("birthdays", birthdays); renderEntries("holidays", holidays);
  $("preview-result").replaceChildren();
  message("已载入。修改条目后点击“保存更改”。");
}
function table(parent, headers, rows) {
  parent.replaceChildren();
  if (!rows.length) { parent.append(node("p", "暂无记录。", "empty")); return; }
  const wrap = node("div", undefined, "scroll"), t = node("table"), head = node("thead"), tr = node("tr");
  headers.forEach((text) => tr.append(node("th", text))); head.append(tr); t.append(head);
  const body = node("tbody");
  for (const row of rows) { const tr = node("tr"); for (const cell of row) {
    const td = node("td"); td.append(cell instanceof Node ? cell : document.createTextNode(String(cell))); tr.append(td);
  } body.append(tr); } t.append(body); wrap.append(t); parent.append(wrap);
}
async function history() {
  const rows = await bridge.apiGet("history");
  table($("history"), ["群／会话", "发送时间", "记录标识"], rows.map((row) => [row.group, row.sent_at, row.key]));
}
$("reload").onclick = () => run(load);
$("save").onclick = () => run(async () => {
  const document = draft();
  const result = await bridge.apiPost("settings/save", {...document, revision: state.revision});
  state.revision = result.revision; dirty = false; message("已保存，后续调度使用新的生日和节日配置。");
});
$("add-birthday").onclick = () => {
  birthdays.push({__template_key: "birthday", recipient: "", target_session: "", calendar: "solar", month: 1, day: 1, leap_month: false, extra_info: ""});
  changed(); renderEntries("birthdays", birthdays);
};
$("add-holiday").onclick = () => {
  holidays.push({name: "", month: 1, day: 1, length_days: 1, aliasesText: "", description: ""});
  changed(); renderEntries("holidays", holidays);
};
$("history-refresh").onclick = () => run(history);
$("preview").onclick = () => run(async () => {
  const result = await bridge.apiPost("preview", {settings: draft(), start: $("start").value, days: Number($("days").value)});
  table($("preview-result"), ["公历日期", "节日／生日", "目标群", "输入预览"], result.items.map((row) => {
    const details = node("details"); details.append(node("summary", "查看提示词"), node("pre", row.prompt));
    return [row.date, `${row.name} · ${row.type}`, row.targets.join("\n") || "无可发送目标（未登记或被群名单过滤）", details];
  }));
  message(`已预览 ${result.items.length} 条安排${result.truncated ? "，结果超过上限，仅展示前 2000 条" : ""}。${dirty ? "当前草稿尚未保存。" : ""}`);
});
run(async () => {
  if (!bridge) throw new Error("请从 AstrBot → 插件 → 节日与生日祝福 → Pages 打开此页。");
  await bridge.ready(); await load(); await history();
});
