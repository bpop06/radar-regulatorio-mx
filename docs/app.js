import { appendIcon, createIcon } from "./icons.js?v=20260812b";
import {
  datePresentation,
  detailHref,
  formatDate,
  isSafeHttpUrl,
  mexicoToday,
} from "./markdown.js?v=20260812b";
import { createLiquidMove } from "./motion.js?v=20260812b";

const elements = {
  heading: document.querySelector("#edition-heading"),
  date: document.querySelector("#edition-date"),
  updated: document.querySelector("#edition-updated"),
  editionTotal: document.querySelector("#edition-total"),
  editionSignals: document.querySelector("#edition-signals"),
  loading: document.querySelector("#edition-loading"),
  ready: document.querySelector("#edition-ready"),
  empty: document.querySelector("#edition-empty"),
  error: document.querySelector("#edition-error"),
  status: document.querySelector("#edition-status"),
  signalStatus: document.querySelector("#signal-status"),
  emptyLabel: document.querySelector("#empty-label"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyCopy: document.querySelector("#empty-copy"),
  emptyUpdate: document.querySelector("#empty-update"),
  lastEdition: document.querySelector("#last-edition-link"),
  retry: document.querySelector("#retry-edition"),
  coverageIndicator: document.querySelector("#coverage-indicator"),
  coverageIcon: document.querySelector(".coverage-icon"),
  coverageLabel: document.querySelector("#coverage-label"),
  coverageCopy: document.querySelector("#coverage-copy"),
  coverageUpdated: document.querySelector("#coverage-updated"),
  coverageDetail: document.querySelector("#coverage-detail"),
  leadPanel: document.querySelector("#lead-panel"),
  leadRank: document.querySelector("#lead-rank"),
  leadOrgan: document.querySelector("#lead-organ"),
  leadDate: document.querySelector("#lead-date"),
  leadImportance: document.querySelector("#lead-importance"),
  leadTitle: document.querySelector("#lead-title"),
  leadReason: document.querySelector("#lead-reason"),
  leadSummary: document.querySelector("#lead-summary"),
  leadDetail: document.querySelector("#lead-detail"),
  leadSource: document.querySelector("#lead-source"),
  band: document.querySelector("#signal-band"),
  total: document.querySelector("#today-total"),
  archiveToday: document.querySelector("#archive-today-link"),
};

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let editionData = null;
let leadAnimation = null;
let bandLiquid = null;

function setTime(element, value, options = {}) {
  const presentation = datePresentation(value, options);
  element.textContent = presentation.label;
  if (presentation.dateTime) element.setAttribute("datetime", presentation.dateTime);
  else element.removeAttribute("datetime");
}

function showState(name) {
  elements.loading.hidden = name !== "loading";
  elements.ready.hidden = name !== "ready";
  elements.empty.hidden = name !== "empty";
  elements.error.hidden = name !== "error";
}

function setCoverageIcon(name) {
  elements.coverageIcon.dataset.icon = name;
  elements.coverageIcon.replaceChildren(createIcon(name));
}

function renderCoverage(data) {
  const coverage = data?.coverage || {};
  const failed = Array.isArray(coverage.failed) ? coverage.failed.filter(Boolean) : [];
  const ok = Number(coverage.ok) || 0;
  const total = ok + failed.length;
  const partial = coverage.state === "partial" || failed.length > 0;

  elements.coverageIndicator.classList.toggle("is-partial", partial);
  setCoverageIcon(partial ? "coverage-warning" : "coverage-ok");
  elements.coverageLabel.textContent = partial
    ? `${ok}/${total} fuentes operativas`
    : `${ok}/${total || ok} fuentes verificadas`;
  elements.coverageCopy.textContent = partial
    ? `Cobertura parcial: ${failed.length} fuente${failed.length === 1 ? " no respondió" : "s no respondieron"}.`
    : `${ok} fuentes oficiales respondieron en la recolección.`;
  setTime(elements.coverageUpdated, data.generated_at, { type: "datetime", short: true });

  elements.coverageDetail.replaceChildren();
  if (partial) {
    const title = document.createElement("p");
    title.className = "coverage-warning";
    title.textContent = "Fuentes con incidencia";
    const list = document.createElement("ul");
    for (const source of failed) {
      const item = document.createElement("li");
      item.textContent = source;
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

function appendTimeSentence(container, prefix, value, options = {}, suffix = ".") {
  const presentation = datePresentation(value, options);
  container.replaceChildren(document.createTextNode(prefix));
  const time = document.createElement(presentation.dateTime ? "time" : "span");
  if (presentation.dateTime) time.dateTime = presentation.dateTime;
  time.textContent = presentation.label;
  container.append(time, document.createTextNode(suffix));
}

function setEditionHeader(data, signalCount = 0) {
  setTime(elements.date, data.edition_date);
  setTime(elements.updated, data.generated_at, { type: "datetime", short: true });
  elements.editionTotal.textContent = String(Number(data.total_today) || 0);
  elements.editionSignals.textContent = String(signalCount);
}

function renderPending(data) {
  const today = mexicoToday();
  setTime(elements.date, today);
  setTime(elements.updated, data.generated_at, { type: "datetime", short: true });
  elements.editionTotal.textContent = "—";
  elements.editionSignals.textContent = "—";
  elements.heading.textContent = "El corte de hoy sigue en preparación.";
  elements.emptyLabel.textContent = "Corte pendiente";
  elements.emptyTitle.textContent = "El corte de hoy todavía no está publicado.";
  elements.emptyCopy.textContent =
    "Radar MX no mezcla titulares anteriores con la fecha vigente. Consulta la última edición o el archivo.";
  appendTimeSentence(elements.emptyUpdate, "Última actualización verificada: ", data.generated_at, {
    type: "datetime",
    short: true,
  });
  if (data.last_available_date) {
    elements.lastEdition.href = `archivo.html?fecha=${encodeURIComponent(data.last_available_date)}`;
    elements.lastEdition.querySelector("span:last-child").textContent =
      `Abrir edición del ${formatDate(data.last_available_date, { short: true })}`;
    elements.lastEdition.hidden = false;
  } else {
    elements.lastEdition.hidden = true;
  }
  document.title = "Corte pendiente | Radar Regulatorio MX";
  showState("empty");
  elements.status.textContent = "El corte de hoy está pendiente. Se muestra la última actualización verificada.";
  if (data.edition_date) renderCoverage(data);
}

function renderEmpty(data) {
  setEditionHeader(data, 0);
  elements.heading.textContent = "Hoy no hubo novedades seleccionadas.";
  elements.emptyLabel.textContent = "Corte publicado";
  elements.emptyTitle.textContent = "El corte no contiene novedades seleccionadas.";
  elements.emptyCopy.textContent =
    "Las fuentes fueron revisadas, pero ninguna publicación superó los criterios del radar para esta edición.";
  appendTimeSentence(elements.emptyUpdate, "Corte generado: ", data.generated_at, {
    type: "datetime",
    short: true,
  });
  if (data.last_available_date && data.last_available_date !== data.edition_date) {
    elements.lastEdition.href = `archivo.html?fecha=${encodeURIComponent(data.last_available_date)}`;
    elements.lastEdition.querySelector("span:last-child").textContent =
      `Abrir edición del ${formatDate(data.last_available_date, { short: true })}`;
    elements.lastEdition.hidden = false;
  } else {
    elements.lastEdition.hidden = true;
  }
  document.title = "Sin novedades hoy | Radar Regulatorio MX";
  showState("empty");
  elements.status.textContent = "El corte de hoy fue publicado sin novedades seleccionadas.";
  renderCoverage(data);
}

function signalLabel(signal) {
  return signal.issuing_body || signal.source || "Fuente oficial";
}

function updateLead(signal, index) {
  const tab = document.querySelector(`#signal-choice-${index + 1}`);
  elements.leadPanel.setAttribute("aria-labelledby", tab?.id || "impact-heading");
  elements.leadRank.textContent = `Señal ${String(signal.rank).padStart(2, "0")}`;
  elements.leadOrgan.textContent = signalLabel(signal);
  setTime(elements.leadDate, signal.published_at, { short: true });
  elements.leadImportance.textContent = `Importancia ${Number(signal.importance) || 0}/5`;
  elements.leadTitle.textContent = signal.title || "Actualización regulatoria";
  elements.leadReason.textContent = signal.why_it_matters || signal.summary || "";
  elements.leadSummary.textContent = signal.summary || "Sin síntesis disponible.";
  elements.leadDetail.href = detailHref(signal, "hoy");
  elements.leadDetail.setAttribute("aria-label", `Abrir ficha: ${signal.title}`);
  if (isSafeHttpUrl(signal.url)) {
    elements.leadSource.href = signal.url;
    elements.leadSource.hidden = false;
  } else {
    elements.leadSource.hidden = true;
  }
}

function animateLead() {
  if (reducedMotion || typeof elements.leadPanel.animate !== "function") return;
  leadAnimation?.cancel();
  leadAnimation = elements.leadPanel.animate([
    { opacity: .78, transform: "translateY(4px)" },
    { opacity: 1, transform: "translateY(0)" },
  ], {
    duration: 150,
    easing: "cubic-bezier(.2, 0, 0, 1)",
  });
}

function selectSignal(index, { animate = false, focus = false, announce = false } = {}) {
  if (!editionData?.signals?.[index]) return;
  const signal = editionData.signals[index];
  updateLead(signal, index);
  elements.band.querySelectorAll(".signal-tab").forEach((button, buttonIndex) => {
    const active = buttonIndex === index;
    button.setAttribute("aria-selected", String(active));
    button.classList.toggle("is-active", active);
    button.tabIndex = active ? 0 : -1;
  });
  if (animate) animateLead();
  if (announce) {
    elements.signalStatus.textContent =
      `Señal ${index + 1} de ${editionData.signals.length}, registrada el ${formatDate(signal.published_at)}: ${signal.title}.`;
  }
  if (focus) document.querySelector(`#signal-choice-${index + 1}`)?.focus();
}

function buildBand(signals) {
  bandLiquid?.destroy();
  elements.band.replaceChildren();
  signals.forEach((signal, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "signal-tab pressable";
    button.id = `signal-choice-${index + 1}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "lead-panel");
    button.setAttribute("aria-selected", String(index === 0));
    button.tabIndex = index === 0 ? 0 : -1;
    button.setAttribute(
      "aria-label",
      `Señal ${signal.rank}: ${signal.title}. Registrada el ${formatDate(signal.published_at)}. Importancia ${signal.importance} de 5.`,
    );

    const rank = document.createElement("span");
    rank.className = "signal-tab-rank";
    rank.textContent = String(signal.rank).padStart(2, "0");
    const marker = document.createElement("span");
    marker.className = "signal-tab-marker";
    appendIcon(marker, "signal");
    const source = document.createElement("span");
    source.className = "signal-tab-source";
    source.textContent = signal.source || "Fuente";
    const date = datePresentation(signal.published_at, { short: true });
    const dateElement = document.createElement("time");
    dateElement.className = "signal-tab-date";
    if (date.dateTime) dateElement.dateTime = date.dateTime;
    dateElement.textContent = date.label;
    const title = document.createElement("span");
    title.className = "signal-tab-title";
    title.textContent = signal.title || "Actualización regulatoria";
    const impact = document.createElement("span");
    impact.className = "signal-tab-impact";
    impact.textContent = `${Number(signal.importance) || 0}/5`;
    button.append(rank, marker, source, dateElement, title, impact);

    button.addEventListener("click", () => selectSignal(index, { animate: true, announce: true }));
    button.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % signals.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + signals.length) % signals.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = signals.length - 1;
      if (next === null) return;
      event.preventDefault();
      selectSignal(next, { animate: true, focus: true, announce: true });
    });
    elements.band.append(button);
  });
  bandLiquid = createLiquidMove(elements.band, { activeSelector: '.signal-tab[aria-selected="true"]' });
}

function renderReady(data) {
  const signals = Array.isArray(data.signals) ? data.signals : [];
  if (!signals.length) throw new Error("Una edición ready debe contener señales");
  editionData = data;
  setEditionHeader(data, signals.length);
  elements.heading.textContent = `${data.total_today} novedades. ${signals.length} requieren atención.`;
  elements.total.textContent = `${data.total_today} publicaciones registradas el ${formatDate(data.edition_date)}.`;
  elements.archiveToday.href = `archivo.html?fecha=${encodeURIComponent(data.edition_date)}`;
  buildBand(signals);
  selectSignal(0);
  renderCoverage(data);
  document.title = `Hoy, ${formatDate(data.edition_date, { short: true })} | Radar Regulatorio MX`;
  showState("ready");
  elements.status.textContent = `Corte del ${formatDate(data.edition_date)}: ${signals.length} señales priorizadas.`;
}

function validateEnvelope(data) {
  return data
    && Number(data.schema_version) === 7
    && typeof data.edition_date === "string"
    && typeof data.generated_at === "string"
    && ["ready", "empty"].includes(data.state)
    && Array.isArray(data.signals);
}

async function loadEdition() {
  showState("loading");
  elements.retry.disabled = true;
  elements.retry.classList.add("is-loading");
  elements.status.textContent = "Cargando el corte diario.";
  try {
    const response = await fetch("data/edition.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!validateEnvelope(data)) throw new Error("Contrato de edición inválido");
    if (data.edition_date !== mexicoToday()) renderPending(data);
    else if (data.state === "empty") renderEmpty(data);
    else renderReady(data);
  } catch (error) {
    showState("error");
    elements.heading.textContent = "No pudimos verificar el corte.";
    setTime(elements.date, null);
    setTime(elements.updated, null, { type: "datetime" });
    setTime(elements.coverageUpdated, null, { type: "datetime" });
    elements.editionTotal.textContent = "—";
    elements.editionSignals.textContent = "—";
    elements.status.textContent = "No fue posible comprobar el corte. Puedes reintentar o consultar el archivo.";
    elements.coverageLabel.textContent = "Cobertura no verificada";
    elements.coverageCopy.textContent = "No fue posible leer el estado de las fuentes.";
    setCoverageIcon("coverage-warning");
    console.error(error);
  } finally {
    elements.retry.disabled = false;
    elements.retry.classList.remove("is-loading");
  }
}

elements.retry.addEventListener("click", loadEdition);
loadEdition();
