/* Calendario de días hábiles — data-driven desde data/calendars.json.
 * Reglas: sábados/domingos inhábiles SIEMPRE (derivados en cliente); los días del
 * JSON pintan su estado encima; el resto son hábiles. Un calendario por órgano. */

import { isSafeHttpUrl, mexicoToday } from "./markdown.js";

const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const monthNamesFull = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];
const monthShort = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];
const dowNamesUpper = ["DOMINGO", "LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO"];
const dowNamesLower = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

let calendarYear = new Date().getFullYear();

const el = {
  chips: document.querySelector("#organ-chips"),
  chipIndicator: document.querySelector("#organ-chips .filter-indicator"),
  calendarPanel: document.querySelector("#calendar-panel"),
  select: document.querySelector("#organ-select"),
  subhead: document.querySelector("#organ-subhead"),
  grid: document.querySelector("#cal-grid"),
  monthLabel: document.querySelector("#cal-month-label"),
  prev: document.querySelector("#cal-prev"),
  next: document.querySelector("#cal-next"),
  today: document.querySelector("#cal-today"),
  panel: document.querySelector("#day-panel"),
  reading: document.querySelector("#reading-restantes"),
  readingLabel: document.querySelector("#reading-label"),
  updated: document.querySelector("#calendar-updated"),
};

const store = {
  organs: [],
  lookup: {}, // { organId: { 'YYYY-MM-DD': dayEntry } }
  selectedOrgan: null,
  month: 6, // julio por defecto (índice 0-based)
  selectedDate: null,
};

const desktopCalendar = window.matchMedia("(min-width: 58.75rem)");
let sheetTrigger = null;

/* ------------------------------------------------------------ utilidades fecha */
function pad2(n) { return String(n).padStart(2, "0"); }
function dateStr(y, m, d) { return `${y}-${pad2(m + 1)}-${pad2(d)}`; }
function dowOf(y, m, d) { return new Date(Date.UTC(y, m, d)).getUTCDay(); } // 0=Dom..6=Sáb
function daysInMonth(y, m) { return new Date(Date.UTC(y, m + 1, 0)).getUTCDate(); }
function mondayIndex(dow) { return (dow + 6) % 7; } // Lun=0..Dom=6

function todayInfo() {
  const iso = mexicoToday();
  const [y, month, d] = iso.split("-").map(Number);
  return { y, m: month - 1, d, iso };
}

function monoDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${pad2(d)}·${monthShort[m - 1]}·${y}`;
}

/* ------------------------------------------------------------ regla de negocio */
// Campos v6 (opcionales, contrato aún no los trae): `publicacion` ("dof"|"web_oficial"|
// "pendiente"), `acuerdo` (cita del acuerdo publicado) y `guardia`/`guardia_detalle`
// (overlay, no cambia el status). Tolerante a su ausencia en calendars.json.
function resolveDay(organId, iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dow = dowOf(y, m - 1, d);
  const entry = store.lookup[organId] && store.lookup[organId][iso];
  if (entry) {
    return {
      status: entry.status, // 'inhabil' | 'vacaciones'
      reason: entry.reason || "",
      source_url: entry.source_url || null,
      analysis: entry.analysis || "",
      verified: entry.verified !== false,
      derived: false,
      dow,
      publicacion: typeof entry.publicacion === "string" ? entry.publicacion : "",
      acuerdo: typeof entry.acuerdo === "string" ? entry.acuerdo.trim() : "",
      guardia: entry.guardia === true,
      guardia_detalle: typeof entry.guardia_detalle === "string" ? entry.guardia_detalle.trim() : "",
    };
  }
  if (dow === 0 || dow === 6) {
    return {
      status: "inhabil",
      reason: `${dow === 0 ? "Domingo" : "Sábado"} — inhábil por regla general`,
      source_url: null,
      analysis: "",
      verified: true,
      derived: true,
      weekend: true,
      dow,
      publicacion: "",
      acuerdo: "",
      guardia: false,
      guardia_detalle: "",
    };
  }
  return {
    status: "habil", reason: "", source_url: null, analysis: "", verified: true, derived: true, dow,
    publicacion: "", acuerdo: "", guardia: false, guardia_detalle: "",
  };
}

function statusLabel(status) {
  if (status === "habil") return "día hábil";
  if (status === "vacaciones") return "día en vacaciones";
  return "día inhábil";
}

/* ------------------------------------------------------------ selector de órgano */
function renderOrganSelector() {
  // chips
  for (const organ of store.organs) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "organ-chip";
    chip.dataset.organ = organ.id;
    chip.id = `organ-tab-${organ.id}`;
    chip.setAttribute("role", "tab");
    chip.setAttribute("aria-controls", "calendar-panel");
    chip.tabIndex = -1;
    chip.textContent = organ.id.toUpperCase();
    chip.title = organ.name;
    chip.addEventListener("click", () => selectOrgan(organ.id));
    chip.addEventListener("keydown", handleOrganKeydown);
    el.chips.append(chip);
  }
  // select (móvil)
  el.select.replaceChildren();
  for (const organ of store.organs) {
    el.select.append(new Option(`${organ.id.toUpperCase()} — ${organ.name}`, organ.id));
  }
  el.select.addEventListener("change", (e) => selectOrgan(e.target.value));
  organLiquid?.destroy();
  organLiquid = createLiquidMove(el.chips, {
    activeSelector: '.organ-chip[aria-selected="true"]',
  });
}

function handleOrganKeydown(event) {
  const tabs = Array.from(el.chips.querySelectorAll(".organ-chip"));
  const index = tabs.indexOf(event.currentTarget);
  let next = null;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") next = 0;
  if (event.key === "End") next = tabs.length - 1;
  if (next === null) return;
  event.preventDefault();
  selectOrgan(tabs[next].dataset.organ);
  tabs[next].focus();
}

function syncOrganActive() {
  el.chips.querySelectorAll(".organ-chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.organ === store.selectedOrgan);
    chip.setAttribute("aria-selected", String(chip.dataset.organ === store.selectedOrgan));
    chip.tabIndex = chip.dataset.organ === store.selectedOrgan ? 0 : -1;
  });
  const active = el.chips.querySelector(`[data-organ="${CSS.escape(store.selectedOrgan || "")}"]`);
  if (active) el.calendarPanel.setAttribute("aria-labelledby", active.id);
  if (el.select.value !== store.selectedOrgan) el.select.value = store.selectedOrgan;
}

function renderSubhead() {
  const organ = store.organs.find((o) => o.id === store.selectedOrgan);
  el.subhead.replaceChildren();
  if (!organ) return;
  const code = document.createElement("span");
  code.className = "folio";
  code.textContent = organ.id.toUpperCase();
  const name = document.createElement("span");
  name.className = "o-name";
  name.textContent = organ.name;
  const kind = document.createElement("span");
  kind.className = "o-kind";
  kind.textContent = organ.kind || "";
  el.subhead.append(code, name, kind);

  if (isSafeHttpUrl(organ.source_page)) {
    const src = document.createElement("span");
    src.className = "o-source";
    const a = document.createElement("a");
    a.href = organ.source_page;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    appendIcon(a, "external");
    const label = document.createElement("span");
    label.textContent = "Fuente oficial";
    const notice = document.createElement("span");
    notice.className = "sr-only";
    notice.textContent = " (se abre en una pestaña nueva)";
    a.append(label, notice);
    src.append(document.createTextNode("Base: "), a);
    el.subhead.append(src);
  }
}

/* ------------------------------------------------------------ lectura viva */
function computeRestantes() {
  const t = todayInfo();
  if (t.y !== calendarYear) {
    return countHabil(
      new Date(Date.UTC(calendarYear, 0, 1)),
      new Date(Date.UTC(calendarYear, 11, 31)),
    );
  }
  const from = new Date(Date.UTC(t.y, t.m, t.d + 1));
  const to = new Date(Date.UTC(calendarYear, 11, 31));
  return countHabil(from, to);
}
function countHabil(fromDate, toDate) {
  let count = 0;
  const cur = new Date(fromDate);
  while (cur <= toDate) {
    const iso = dateStr(cur.getUTCFullYear(), cur.getUTCMonth(), cur.getUTCDate());
    if (resolveDay(store.selectedOrgan, iso).status === "habil") count += 1;
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return count;
}
function updateReading() {
  const n = computeRestantes();
  el.reading.textContent = String(n);
}

/* ------------------------------------------------------------ render del mes */
function renderMonth() {
  const m = store.month;
  el.monthLabel.textContent = `${monthNamesFull[m].toUpperCase()} ${calendarYear}`;
  el.monthLabel.dateTime = `${calendarYear}-${pad2(m + 1)}`;
  el.prev.disabled = m <= 0;
  el.next.disabled = m >= 11;

  el.grid.replaceChildren();
  const t = todayInfo();
  const lead = mondayIndex(dowOf(calendarYear, m, 1));
  const total = daysInMonth(calendarYear, m);

  for (let i = 0; i < lead; i++) {
    const pad = document.createElement("div");
    pad.className = "cal-pad";
    pad.setAttribute("aria-hidden", "true");
    el.grid.append(pad);
  }

  for (let d = 1; d <= total; d++) {
    const iso = dateStr(calendarYear, m, d);
    const info = resolveDay(store.selectedOrgan, iso);
    const dow = dowOf(calendarYear, m, d);

    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = `cal-day day--${info.status}${info.guardia ? " day--guardia" : ""}`;
    cell.dataset.date = iso;
    cell.dataset.cell = "";
    cell.style.setProperty("--ci", lead + d - 1);
    cell.setAttribute("role", "gridcell");
    cell.setAttribute(
      "aria-label",
      `${dowNamesLower[dow]} ${d} de ${monthNamesFull[m]} de ${calendarYear}, ${statusLabel(info.status)}` +
        (info.guardia ? ", con guardia" : ""),
    );
    cell.setAttribute("aria-selected", String(iso === store.selectedDate));
    cell.tabIndex = -1;

    const isToday = t.y === calendarYear && t.m === m && t.d === d;
    if (isToday) cell.classList.add("is-today");
    if (iso === store.selectedDate) cell.classList.add("is-selected");

    const num = document.createElement("time");
    num.className = "num";
    num.dateTime = iso;
    num.textContent = String(d);
    const key = document.createElement("span");
    key.className = "state-key";
    key.setAttribute("aria-hidden", "true");
    cell.append(num, key);
    if (info.guardia) {
      const mark = document.createElement("span");
      mark.className = "guardia-mark";
      mark.setAttribute("aria-hidden", "true");
      cell.append(mark);
    }

    cell.addEventListener("click", () => selectDay(iso, cell));
    el.grid.append(cell);
  }

  // relleno final para completar la última fila
  const placed = lead + total;
  const trailing = (7 - (placed % 7)) % 7;
  for (let i = 0; i < trailing; i++) {
    const pad = document.createElement("div");
    pad.className = "cal-pad";
    pad.setAttribute("aria-hidden", "true");
    el.grid.append(pad);
  }

  const dayNodes = Array.from(el.grid.children);
  el.grid.replaceChildren();
  for (let index = 0; index < dayNodes.length; index += 7) {
    const row = document.createElement("div");
    row.className = "cal-row";
    row.setAttribute("role", "row");
    row.append(...dayNodes.slice(index, index + 7));
    el.grid.append(row);
  }

  setRovingTabindex();
  runScanIn();
}

function setRovingTabindex() {
  const cells = Array.from(el.grid.querySelectorAll(".cal-day"));
  if (!cells.length) return;
  let target = cells.find((c) => c.dataset.date === store.selectedDate) ||
    cells.find((c) => c.classList.contains("is-today")) || cells[0];
  cells.forEach((c) => (c.tabIndex = c === target ? 0 : -1));
}

// F2#7 (v7 QA): antes se difería un frame vía rAF para disparar la transición CSS de
// entrada. Combinado con document.startViewTransition (ver runWithViewTransition), esa
// espera de un frame corría el riesgo de perderse si la transición fallaba/se
// solapaba (InvalidStateError) — el resultado era una retícula que quedaba invisible
// ("gris") hasta el siguiente scroll/repintado. Ahora se marcan visibles de inmediato,
// en el mismo tick que se pintó el mes: nunca dependen de un frame futuro.
function runScanIn() {
  const cells = el.grid.querySelectorAll("[data-cell]");
  cells.forEach((c) => c.classList.add("is-in"));
}

// F2#7 (v7 QA): evita encadenar View Transitions solapadas. document.startViewTransition
// lanza InvalidStateError si se invoca mientras otra sigue en curso — con clics rápidos
// (o los ~10 cambios de mes seguidos del smoke) eso pasaba decenas de veces y, al
// abortar a medias, dejaba pseudo-elementos de la transición vieja pisando el DOM
// nuevo (de ahí la retícula gris hasta hacer scroll). Si ya hay una transición activa,
// se pinta directo, sin transición, en vez de encolar otra.
let viewTransitionPending = false;
function runWithViewTransition(render) {
  if (RM || !document.startViewTransition || viewTransitionPending) {
    render();
    return;
  }
  viewTransitionPending = true;
  try {
    const transition = document.startViewTransition(render);
    const clear = () => { viewTransitionPending = false; };
    Promise.resolve(transition.finished).then(clear, clear);
  } catch (error) {
    viewTransitionPending = false;
    render();
  }
}

function changeMonth(delta) {
  const nextMonth = store.month + delta;
  if (nextMonth < 0 || nextMonth > 11) return;
  store.month = nextMonth;
  runWithViewTransition(() => renderMonth());
}

/* ------------------------------------------------------------ órgano: barrido + recompute */
function selectOrgan(id) {
  if (store.selectedOrgan === id) return;
  store.selectedOrgan = id;
  syncOrganActive();
  renderSubhead();
  updateReading();

  const doRender = () => {
    renderMonth();
    // re-render panel si hay día seleccionado (cambia el estado por órgano)
    if (store.selectedDate) renderPanel(store.selectedDate);
  };
  runWithViewTransition(doRender);
}

/* ------------------------------------------------------------ panel de día */
function isDesktop() { return desktopCalendar.matches; }

function selectDay(iso, cell) {
  store.selectedDate = iso;
  el.grid.querySelectorAll(".cal-day").forEach((c) => {
    const on = c.dataset.date === iso;
    c.classList.toggle("is-selected", on);
    c.setAttribute("aria-selected", String(on));
  });
  if (cell) {
    el.grid.querySelectorAll(".cal-day").forEach((c) => (c.tabIndex = c === cell ? 0 : -1));
  }
  sheetTrigger = cell || document.activeElement;
  renderPanel(iso);
  if (!isDesktop()) openSheet();
}

function renderPanel(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dow = dowOf(y, m - 1, d);
  const info = resolveDay(store.selectedOrgan, iso);
  const organ = store.organs.find((o) => o.id === store.selectedOrgan);

  el.panel.classList.remove("empty");
  el.panel.replaceChildren();

  const heading = document.createElement("h2");
  heading.id = "day-panel-heading";
  heading.className = "sr-only";
  heading.textContent = "Detalle del día";
  el.panel.append(heading);

  const close = document.createElement("button");
  close.className = "dp-close";
  close.type = "button";
  close.setAttribute("aria-label", "Cerrar detalle");
  appendIcon(close, "close");
  close.addEventListener("click", closeSheet);
  el.panel.append(close);

  const dateEl = document.createElement("p");
  dateEl.className = "dp-date tabular";
  dateEl.dateTime = iso;
  dateEl.textContent = `${dowNamesUpper[dow]} ${monoDate(iso)}`;
  el.panel.append(dateEl);

  const pillRow = document.createElement("div");
  const pill = document.createElement("span");
  pill.className = `day-pill ${info.status}`;
  pill.textContent = info.status === "habil" ? "HÁBIL" : info.status === "vacaciones" ? "VACACIONES" : "INHÁBIL";
  pillRow.append(pill);
  if (info.verified === false) {
    const badge = document.createElement("span");
    badge.className = "dp-unverified";
    badge.textContent = "sin verificar";
    pillRow.append(badge);
  }
  // Contrato v6 (opcional): publicacion:"pendiente" -> el acuerdo aún no se publica en
  // el DOF. La guardia (overlay) no toca esta fila: sin cambios de fondo del pill.
  if (info.publicacion === "pendiente") {
    const pending = document.createElement("span");
    pending.className = "dp-pending";
    pending.textContent = "Acuerdo pendiente de publicación en el DOF";
    pillRow.append(pending);
  }
  el.panel.append(pillRow);

  if (info.reason) {
    const reason = document.createElement("p");
    reason.className = "dp-reason";
    reason.textContent = info.reason;
    el.panel.append(reason);
  } else if (info.status === "habil") {
    const reason = document.createElement("p");
    reason.className = "dp-reason";
    reason.textContent = `Día hábil para ${organ ? organ.name : "el órgano"}.`;
    el.panel.append(reason);
  }

  // Contrato v6: el texto del acuerdo publicado es la fuente principal; el sitio web
  // (source_url) queda como confirmación orientativa.
  if (info.acuerdo) {
    const acuerdo = document.createElement("p");
    acuerdo.className = "dp-acuerdo";
    const label = document.createElement("span");
    label.className = "dp-acuerdo-label";
    label.textContent = "Fuente principal";
    acuerdo.append(label, document.createTextNode(info.acuerdo));
    el.panel.append(acuerdo);
  }

  if (isSafeHttpUrl(info.source_url)) {
    const src = document.createElement("p");
    src.className = "dp-source";
    const a = document.createElement("a");
    a.href = info.source_url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    appendIcon(a, "external");
    const sourceLabel = document.createElement("span");
    sourceLabel.textContent = info.acuerdo ? "Confirmación en sitio oficial" : "Ver fuente oficial";
    const sourceNotice = document.createElement("span");
    sourceNotice.className = "sr-only";
    sourceNotice.textContent = " (se abre en una pestaña nueva)";
    a.append(sourceLabel, sourceNotice);
    src.append(a);
    el.panel.append(src);
  }

  if (info.guardia) {
    const guardia = document.createElement("p");
    guardia.className = "dp-guardia";
    const label = document.createElement("span");
    label.className = "dp-guardia-label";
    label.textContent = "Guardia: ";
    guardia.append(label, document.createTextNode(info.guardia_detalle || "Guardia activa este día."));
    el.panel.append(guardia);

    // F2#14 (v7 QA): la guardia no habilita el día para el cómputo de plazos — enlaza
    // a la guía (docs/GUARDIAS.md, servida en guardias.html) para que quede claro.
    const guideLink = document.createElement("p");
    guideLink.className = "dp-guardia-link";
    const guideAnchor = document.createElement("a");
    guideAnchor.href = "guardias.html";
    appendIcon(guideAnchor, "book");
    const guideLabel = document.createElement("span");
    guideLabel.textContent = "Ver guía de guardias y plazos";
    guideAnchor.append(guideLabel);
    appendIcon(guideAnchor, "arrow-forward");
    guideLink.append(guideAnchor);
    el.panel.append(guideLink);
  }

  if (info.analysis) {
    const analysis = document.createElement("p");
    analysis.className = "dp-analysis";
    analysis.textContent = info.analysis;
    el.panel.append(analysis);
  }

  const note = document.createElement("p");
  note.className = "dp-note";
  if (info.weekend) note.textContent = "Regla general: fines de semana inhábiles.";
  else if (info.derived && info.status === "habil") note.textContent = "Sin suspensión ni receso registrado para este día.";
  else note.textContent = "Estado tomado del calendario oficial del órgano.";
  el.panel.append(note);
  // F2#13 (v7 QA): el panel del día tardaba ~1s en aparecer por el stagger de
  // entrada (opacidad 0 + retraso por hijo antes de esta línea). Se quita: el panel
  // debe quedar visible de inmediato al seleccionar un día.
}

function openSheet() {
  if (isDesktop()) return;
  if (el.panel.open) el.panel.close();
  el.panel.classList.add("sheet-open");
  el.panel.showModal();
  el.panel.querySelector(".dp-close")?.focus();
}
function closeSheet() {
  el.panel.classList.remove("sheet-open");
  if (el.panel.open && !isDesktop()) el.panel.close();
  if (sheetTrigger instanceof HTMLElement && sheetTrigger.isConnected) sheetTrigger.focus();
}

function syncPanelMode() {
  if (isDesktop()) {
    if (el.panel.open) el.panel.close();
    el.panel.setAttribute("open", "");
    el.panel.classList.remove("sheet-open");
  } else if (el.panel.open) {
    el.panel.close();
  }
}

/* ------------------------------------------------------------ teclado */
function initKeyboard() {
  el.grid.addEventListener("keydown", (event) => {
    const cells = Array.from(el.grid.querySelectorAll(".cal-day"));
    const current = document.activeElement;
    let idx = cells.indexOf(current);
    if (idx < 0) return;
    let next = idx;
    switch (event.key) {
      case "ArrowRight": next = Math.min(cells.length - 1, idx + 1); break;
      case "ArrowLeft": next = Math.max(0, idx - 1); break;
      case "ArrowDown": next = Math.min(cells.length - 1, idx + 7); break;
      case "ArrowUp": next = Math.max(0, idx - 7); break;
      case "Home": next = idx - (idx % 7); break;
      case "End": next = Math.min(cells.length - 1, idx - (idx % 7) + 6); break;
      case "Enter":
      case " ":
        event.preventDefault();
        selectDay(cells[idx].dataset.date, cells[idx]);
        return;
      default: return;
    }
    event.preventDefault();
    cells.forEach((c) => (c.tabIndex = -1));
    cells[next].tabIndex = 0;
    cells[next].focus();
  });
}

/* ------------------------------------------------------------ leyenda */
/* ------------------------------------------------------------ init */
async function init() {
  el.prev.addEventListener("click", () => changeMonth(-1));
  el.next.addEventListener("click", () => changeMonth(1));
  el.today.addEventListener("click", () => {
    const t = todayInfo();
    const targetMonth = t.y === calendarYear ? t.m : store.month;
    if (targetMonth !== store.month) {
      store.month = targetMonth;
      runWithViewTransition(() => renderMonth());
    }
    if (t.y === calendarYear) selectDay(t.iso, null);
  });
  el.panel.addEventListener("cancel", (event) => {
    if (isDesktop()) event.preventDefault();
    else el.panel.classList.remove("sheet-open");
  });
  el.panel.addEventListener("close", () => {
    el.panel.classList.remove("sheet-open");
  });
  desktopCalendar.addEventListener("change", syncPanelMode);
  initKeyboard();

  try {
    const res = await fetch("data/calendars.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    const updated = datePresentation(payload.generated_at, { type: "datetime", short: true });
    el.updated.textContent = updated.label;
    if (updated.dateTime) el.updated.dateTime = updated.dateTime;
    else el.updated.removeAttribute("datetime");
    calendarYear = Number(payload.year) || calendarYear;
    el.readingLabel.textContent = `Días hábiles restantes en ${calendarYear}`;
    store.organs = Array.isArray(payload.organs) ? payload.organs : [];
    const dbo = payload.days_by_organ || {};
    for (const organ of store.organs) {
      const map = {};
      for (const day of dbo[organ.id] || []) map[day.date] = day;
      store.lookup[organ.id] = map;
    }
    store.selectedOrgan = store.organs.length ? store.organs[0].id : null;

    const t = todayInfo();
    store.month = t.y === calendarYear ? t.m : 0;

    renderOrganSelector();
    syncOrganActive();
    renderSubhead();
    updateReading();
    renderMonth();
    syncPanelMode();
  } catch (error) {
    el.grid.replaceChildren();
    const msg = document.createElement("p");
    msg.className = "dp-note";
    msg.textContent = "No fue posible cargar los calendarios.";
    el.grid.append(msg);
    console.error(error);
  }
}

init();
