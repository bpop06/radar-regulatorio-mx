import {
  displayTitle,
  editorialLabel,
  isGeneratedCutOverdue,
  isPublicEditorialItem,
  loadEdition,
  loadManifest,
  officialDate,
  stableDetailHref,
  teaser,
} from "./data-client.js";
import { formatDate, isSafeHttpUrl, mexicoToday, setTime } from "./markdown.js?v=20260813g";

const elements = {
  heading: document.querySelector("#edition-heading"),
  deck: document.querySelector("#edition-deck"),
  date: document.querySelector("#edition-date"),
  updated: document.querySelector("#edition-updated"),
  editionTotal: document.querySelector("#edition-total"),
  editionSignals: document.querySelector("#edition-signals"),
  loading: document.querySelector("#edition-loading"),
  ready: document.querySelector("#edition-ready"),
  empty: document.querySelector("#edition-empty"),
  error: document.querySelector("#edition-error"),
  emptyLabel: document.querySelector("#empty-label"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyCopy: document.querySelector("#empty-copy"),
  emptyUpdate: document.querySelector("#empty-update"),
  lastEdition: document.querySelector("#last-edition-link"),
  retry: document.querySelector("#retry-edition"),
  coverageIndicator: document.querySelector("#coverage-indicator"),
  coverageLabel: document.querySelector("#coverage-label"),
  coverageCopy: document.querySelector("#coverage-copy"),
  coverageUpdated: document.querySelector("#coverage-updated"),
  coverageDetail: document.querySelector("#coverage-detail"),
  leadPanel: document.querySelector("#lead-panel"),
  leadRank: document.querySelector("#lead-rank"),
  leadOrgan: document.querySelector("#lead-organ"),
  leadImportance: document.querySelector("#lead-importance"),
  leadStatus: document.querySelector("#lead-status"),
  leadDate: document.querySelector("#lead-date"),
  leadTitle: document.querySelector("#lead-title"),
  leadReasonLabel: document.querySelector("#lead-reason-label"),
  leadReason: document.querySelector("#lead-reason"),
  leadSummary: document.querySelector("#lead-summary"),
  leadDetail: document.querySelector("#lead-detail"),
  leadSource: document.querySelector("#lead-source"),
  band: document.querySelector("#signal-band"),
  list: document.querySelector("#signal-list"),
  total: document.querySelector("#today-total"),
  archiveToday: document.querySelector("#archive-today-link"),
  liveStatus: document.querySelector("#edition-status"),
};

let editionData = null;

const WATERCOLOR_INKS = [
  ["comercio internacional", "#3f6e9f"],
  ["comercio exterior", "#4f7f7c"],
  ["fiscal", "#94691f"],
  ["penal", "#944b54"],
  ["lavado", "#756a8b"],
  ["legislativo", "#5577a2"],
  ["descentralizada", "#8c5964"],
  ["centralizada", "#667c61"],
];
const CARD_TILTS = [-4.2, 2.6, -1.5, 3.8, -2.8, 1.6, -.7];

function watercolorFor(signal) {
  const taxonomy = [signal?.category, ...(signal?.categories || []), signal?.jurisdiction]
    .filter(Boolean).join(" ").toLocaleLowerCase("es-MX");
  return WATERCOLOR_INKS.find(([label]) => taxonomy.includes(label))?.[1] || "#54789b";
}

function showState(name) {
  elements.ready.setAttribute("aria-busy", String(name === "loading"));
  elements.loading.hidden = name !== "loading";
  elements.ready.hidden = name !== "ready";
  elements.empty.hidden = name !== "empty";
  elements.error.hidden = name !== "error";
  const messages = {
    loading: "Cargando el corte.",
    ready: "Corte cargado.",
    empty: elements.emptyTitle.textContent,
    error: "No fue posible cargar el corte.",
  };
  elements.liveStatus.textContent = messages[name] || "";
}

function formatGeneratedAt(value) {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "hora no disponible";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium", timeStyle: "short", timeZone: "America/Mexico_City",
  }).format(instant);
}

function coverageRows(data) {
  const coverage = data?.coverage || {};
  if (Array.isArray(coverage.sources)) return coverage.sources;
  const failed = Array.isArray(coverage.failed) ? coverage.failed : [];
  const degraded = Array.isArray(coverage.degraded) ? coverage.degraded : [];
  return [
    ...degraded.map((source) => typeof source === "string" ? { source, status: "degraded" } : source),
    ...failed.map((source) => typeof source === "string" ? { source, status: "error" } : source),
  ];
}

function renderCoverage(data) {
  const coverage = data?.coverage || {};
  const rows = coverageRows(data);
  const problematic = rows.filter((row) => !["ok", "success"].includes(row.status));
  const declaredOk = Number(coverage.ok);
  const ok = Number.isFinite(declaredOk)
    ? declaredOk
    : rows.filter((row) => ["ok", "success"].includes(row.status)).length;
  const total = Number(coverage.total) || Math.max(18, ok + problematic.length);
  const degraded = ["partial", "degraded"].includes(coverage.state) || problematic.length > 0;

  elements.coverageIndicator.classList.toggle("is-partial", degraded);
  elements.coverageIndicator.dataset.status = degraded ? "degraded" : "complete";
  elements.coverageLabel.textContent = degraded
    ? `Cobertura degradada · ${ok}/${total}`
    : `Cobertura completa · ${ok || total}/${total}`;
  elements.coverageCopy.textContent = degraded
    ? "El corte se publicó con incidencias aisladas. Las fuentes afectadas se identifican abajo."
    : "Las fuentes oficiales certificadas respondieron correctamente en este corte.";
  setTime(elements.coverageUpdated, data.generated_at, { type: "datetime", short: true });

  elements.coverageDetail.replaceChildren();
  if (degraded) {
    const title = document.createElement("p");
    title.className = "coverage-warning";
    title.textContent = "Fuentes con incidencia";
    const list = document.createElement("ul");
    for (const row of problematic) {
      const item = document.createElement("li");
      const name = row.source || row.name || "Fuente oficial";
      const reason = row.error || row.message || row.reason || "respuesta no verificable";
      item.textContent = `${name}: ${reason}`;
      list.append(item);
    }
    elements.coverageDetail.append(title, list);
  } else {
    const status = document.createElement("p");
    status.className = "coverage-ok";
    status.textContent = "Cobertura completa en el corte publicado.";
    elements.coverageDetail.append(status);
  }
}

function setLastEdition(data) {
  if (!data.last_available_date) {
    elements.lastEdition.hidden = true;
    return;
  }
  elements.lastEdition.href = `archivo.html?fecha=${encodeURIComponent(data.last_available_date)}`;
  elements.lastEdition.textContent = `Ver publicaciones con fecha oficial del ${formatDate(data.last_available_date, { short: true })}`;
  elements.lastEdition.hidden = false;
}

function renderPending(data) {
  setTime(elements.date, mexicoToday());
  setTime(elements.updated, data.generated_at, { type: "datetime", short: true });
  elements.editionTotal.textContent = "—";
  elements.editionSignals.textContent = "—";
  elements.emptyLabel.textContent = "Corte pendiente";
  elements.emptyTitle.textContent = "El corte vigente todavía no está publicado.";
  elements.emptyCopy.textContent = "La portada conserva la fecha real del último corte. Consulta esa edición o navega el archivo permanente.";
  elements.emptyUpdate.textContent = `Última actualización verificada: ${formatGeneratedAt(data.generated_at)}.`;
  setLastEdition(data);
  document.title = "Corte pendiente · Radar Regulatorio MX";
  showState("empty");
  renderCoverage(data);
}

function renderEmpty(data) {
  setTime(elements.date, data.edition_date);
  setTime(elements.updated, data.generated_at, { type: "datetime", short: true });
  elements.editionTotal.textContent = String(Number(data.total_today) || 0);
  elements.editionSignals.textContent = "0";
  elements.emptyLabel.textContent = "Sin novedades";
  elements.emptyTitle.textContent = "El corte de hoy no contiene publicaciones seleccionadas.";
  elements.emptyCopy.textContent = "Las fuentes fueron revisadas y no se detectaron novedades que cumplieran los criterios del radar.";
  elements.emptyUpdate.textContent = `Corte generado: ${formatGeneratedAt(data.generated_at)}.`;
  setLastEdition(data);
  document.title = "Sin novedades hoy · Radar Regulatorio MX";
  showState("empty");
  renderCoverage(data);
}

function signalLabel(signal) {
  return signal.issuing_body || signal.authority || signal.source || "Fuente oficial";
}

function dateLabel(signal) {
  const value = officialDate(signal) || signal.detected_at;
  const kind = signal.official_published_at || signal.published_at ? "Fecha oficial" : "Detectada";
  return value ? `${kind}: ${formatDate(value, { short: true })}` : "Fecha no informada";
}

function selectSignal(index, { focus = false } = {}) {
  const signal = editionData?.signals?.[index];
  if (!signal) return;
  const pending = signal.editorial_status === "needs_review";
  elements.leadPanel.style.setProperty("--wash", watercolorFor(signal));
  elements.leadRank.textContent = `Señal ${String(signal.rank || index + 1).padStart(2, "0")}`;
  elements.leadOrgan.textContent = signalLabel(signal);
  elements.leadImportance.textContent = `Importancia ${Number(signal.importance) || 0}/5`;
  elements.leadStatus.textContent = editorialLabel(signal);
  elements.leadStatus.className = `editorial-badge ${pending ? "is-pending" : "is-complete"}`;
  elements.leadDate.textContent = dateLabel(signal);
  const machineDate = officialDate(signal) || String(signal.detected_at || "").slice(0, 10);
  if (machineDate) elements.leadDate.setAttribute("datetime", machineDate);
  else elements.leadDate.removeAttribute("datetime");
  elements.leadTitle.textContent = displayTitle(signal);
  elements.leadReasonLabel.textContent = pending ? "Motivo de revisión" : "Por qué está en el radar";
  elements.leadReason.textContent = pending
    ? signal.review_reason || "La nota editorial requiere revisión antes de interpretar su alcance."
    : signal.why_it_matters || teaser(signal);
  elements.leadSummary.textContent = pending
    ? "Consulta los metadatos y la fuente oficial mientras concluye la revisión editorial."
    : teaser(signal) || "Consulta la ficha completa y la evidencia oficial.";
  elements.leadDetail.href = stableDetailHref(signal, "hoy");
  if (isSafeHttpUrl(signal.url || signal.canonical_url)) {
    elements.leadSource.href = signal.url || signal.canonical_url;
    elements.leadSource.hidden = false;
  } else elements.leadSource.hidden = true;

  elements.band.querySelectorAll(".signal-tab").forEach((button, buttonIndex) => {
    const active = buttonIndex === index;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    button.classList.toggle("is-active", active);
  });
  const selectedTab = elements.band.querySelectorAll(".signal-tab")[index];
  if (selectedTab) elements.leadPanel.setAttribute("aria-labelledby", selectedTab.id);
  elements.band.style.setProperty("--selected-index", String(index));
  if (focus) selectedTab?.focus();
}

function buildBand(signals) {
  elements.band.replaceChildren();
  signals.forEach((signal, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `signal-tab-${index + 1}`;
    button.className = "signal-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "lead-panel");
    button.setAttribute("aria-selected", String(index === 0));
    button.setAttribute("aria-label", `Señal ${signal.rank || index + 1}: ${displayTitle(signal)}. ${editorialLabel(signal)}.`);
    button.tabIndex = index === 0 ? 0 : -1;
    button.style.setProperty("--card-index", String(index));
    button.style.setProperty("--card-tilt", `${CARD_TILTS[index] || 0}deg`);
    button.style.setProperty("--impact", String(Math.max(1, Math.min(5, Number(signal.importance) || 1))));
    button.style.setProperty("--wash", watercolorFor(signal));

    const rank = document.createElement("span");
    rank.className = "signal-tab-rank";
    rank.textContent = String(signal.rank || index + 1).padStart(2, "0");
    const meter = document.createElement("span");
    meter.className = "signal-meter";
    meter.append(document.createElement("span"));
    const source = document.createElement("span");
    source.className = "signal-tab-source";
    source.textContent = signal.source || "Fuente";
    const title = document.createElement("span");
    title.className = "signal-tab-title";
    title.textContent = displayTitle(signal);
    const status = document.createElement("span");
    status.className = "signal-tab-impact";
    status.textContent = signal.editorial_status === "needs_review" ? "Pendiente" : `${Number(signal.importance) || 0}/5`;
    button.append(rank, source, title, meter, status);
    button.addEventListener("click", () => selectSignal(index));
    button.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % signals.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + signals.length) % signals.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = signals.length - 1;
      if (next === null) return;
      event.preventDefault();
      selectSignal(next, { focus: true });
    });
    elements.band.append(button);
  });
}

function importanceMeter(value) {
  const importance = Math.max(0, Math.min(5, Number(value) || 0));
  const meter = document.createElement("span");
  meter.className = "inline-importance";
  meter.setAttribute("role", "img");
  meter.setAttribute("aria-label", `Importancia ${importance} de 5`);
  for (let index = 1; index <= 5; index += 1) {
    const bar = document.createElement("span");
    if (index <= importance) bar.classList.add("is-on");
    meter.append(bar);
  }
  return meter;
}

function buildLedger(signals) {
  elements.list.replaceChildren();
  for (const signal of signals.slice(1)) {
    const row = document.createElement("li");
    row.className = "signal-row is-revealed";
    row.style.setProperty("--wash", watercolorFor(signal));
    const index = document.createElement("span");
    index.className = "signal-row-index";
    index.textContent = String(signal.rank).padStart(2, "0");
    const body = document.createElement("div");
    body.className = "signal-row-body";
    const meta = document.createElement("div");
    meta.className = "signal-row-meta";
    const organ = document.createElement("span");
    organ.textContent = signalLabel(signal);
    const badge = document.createElement("span");
    badge.className = `editorial-badge ${signal.editorial_status === "needs_review" ? "is-pending" : "is-complete"}`;
    badge.textContent = editorialLabel(signal);
    meta.append(organ, badge, importanceMeter(signal.importance));
    const title = document.createElement("h3");
    const link = document.createElement("a");
    link.href = stableDetailHref(signal, "hoy");
    link.textContent = displayTitle(signal);
    title.append(link);
    const summary = document.createElement("p");
    summary.textContent = teaser(signal);
    body.append(meta, title, summary);
    const action = document.createElement("a");
    action.className = "signal-row-action";
    action.href = stableDetailHref(signal, "hoy");
    action.textContent = "Abrir ficha";
    row.append(index, body, action);
    elements.list.append(row);
  }
}

function renderReady(data) {
  const signals = (Array.isArray(data.signals) ? data.signals : [])
    .filter((item) => isPublicEditorialItem(
      item, { allowLegacy: Number(data.schema_version) === 7 },
    ));
  if (!signals.length) return renderEmpty({ ...data, state: "empty" });
  editionData = { ...data, signals };
  elements.heading.textContent = "Corte regulatorio";
  if (elements.deck) {
    elements.deck.textContent = `${signals.length} señales priorizadas de ${data.total_today} publicaciones registradas hoy.`;
  }
  setTime(elements.date, data.edition_date);
  setTime(elements.updated, data.generated_at, { type: "datetime", short: true });
  elements.editionTotal.textContent = String(Number(data.total_today) || 0);
  elements.editionSignals.textContent = String(signals.length);
  const detected = data.total_today ?? signals.length;
  const detectionLabel = detected === 1 ? "detección" : "detecciones";
  const signalLabel = signals.length === 1 ? "señal" : "señales";
  const completeLabel = signals.length === 1 ? "completa" : "completas";
  elements.total.textContent = `${detected} ${detectionLabel} · ${signals.length} ${signalLabel} ${completeLabel}`;
  elements.archiveToday.href = "archivo.html";
  elements.archiveToday.textContent = "Explorar el archivo permanente";
  buildBand(signals);
  buildLedger(signals);
  selectSignal(0);
  renderCoverage(data);
  document.title = `Corte del ${formatDate(data.edition_date, { short: true })} · Radar Regulatorio MX`;
  showState("ready");
}

async function loadCurrentEdition() {
  showState("loading");
  try {
    const manifest = await loadManifest();
    const data = await loadEdition(manifest);
    if (data.state === "pending" || isGeneratedCutOverdue(data.generated_at)) renderPending(data);
    else if (["empty", "no_updates"].includes(data.state)) renderEmpty(data);
    else if (data.state === "ready") renderReady(data);
    else throw new Error("Estado de edición desconocido");
  } catch (error) {
    showState("error");
    setTime(elements.date, null);
    setTime(elements.updated, null, { type: "datetime" });
    setTime(elements.coverageUpdated, null, { type: "datetime" });
    elements.editionTotal.textContent = "—";
    elements.editionSignals.textContent = "—";
    elements.coverageLabel.textContent = "Cobertura no verificada";
    elements.coverageCopy.textContent = "No fue posible leer el manifiesto y el corte publicado.";
    console.error(error);
  }
}

elements.retry.addEventListener("click", loadCurrentEdition);
loadCurrentEdition();
