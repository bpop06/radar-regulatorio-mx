import {
  detailHref,
  formatDate,
  isSafeHttpUrl,
  mexicoToday,
} from "./markdown.js";

const elements = {
  date: document.querySelector("#edition-date"),
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
  coverageDetail: document.querySelector("#coverage-detail"),
  leadPanel: document.querySelector("#lead-panel"),
  leadRank: document.querySelector("#lead-rank"),
  leadOrgan: document.querySelector("#lead-organ"),
  leadImportance: document.querySelector("#lead-importance"),
  leadTitle: document.querySelector("#lead-title"),
  leadReason: document.querySelector("#lead-reason"),
  leadSummary: document.querySelector("#lead-summary"),
  leadDetail: document.querySelector("#lead-detail"),
  leadSource: document.querySelector("#lead-source"),
  band: document.querySelector("#signal-band"),
  list: document.querySelector("#signal-list"),
  total: document.querySelector("#today-total"),
  archiveToday: document.querySelector("#archive-today-link"),
};

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let editionData = null;
let selectedIndex = 0;

const WATERCOLOR_INKS = [
  ["comercio internacional", "#3f6e9f"],
  ["comercio exterior", "#4f7f7c"],
  ["fiscal", "#b18443"],
  ["penal", "#a45b62"],
  ["lavado", "#756a8b"],
  ["legislativo", "#5577a2"],
  ["descentralizada", "#9b6971"],
  ["centralizada", "#73866f"],
];

const CARD_TILTS = [-4.2, 2.6, -1.5, 3.8, -2.8, 1.6, -.7];

function watercolorFor(signal) {
  const taxonomy = [signal?.category, ...(signal?.categories || []), signal?.jurisdiction]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("es-MX");
  return WATERCOLOR_INKS.find(([label]) => taxonomy.includes(label))?.[1] || "#54789b";
}

function showState(name) {
  elements.loading.hidden = name !== "loading";
  elements.ready.hidden = name !== "ready";
  elements.empty.hidden = name !== "empty";
  elements.error.hidden = name !== "error";
}

function formatGeneratedAt(value) {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "hora no disponible";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "America/Mexico_City",
  }).format(instant);
}

function renderCoverage(data) {
  const coverage = data?.coverage || {};
  const failed = Array.isArray(coverage.failed) ? coverage.failed.filter(Boolean) : [];
  const ok = Number(coverage.ok) || 0;
  const total = ok + failed.length;
  const partial = coverage.state === "partial" || failed.length > 0;

  elements.coverageIndicator.classList.toggle("is-partial", partial);
  elements.coverageLabel.textContent = partial
    ? `${ok}/${total} fuentes operativas`
    : `${ok}/${total || ok} fuentes verificadas`;
  elements.coverageCopy.textContent = partial
    ? `El corte es parcial: ${failed.length} fuente${failed.length === 1 ? "" : "s"} no respondió.`
    : `${ok} fuentes oficiales respondieron correctamente en la última recolección.`;

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

function renderPending(data) {
  const editionDate = data.edition_date;
  elements.date.textContent = `Hoy · ${formatDate(mexicoToday())}`;
  elements.emptyLabel.textContent = "Corte pendiente";
  elements.emptyTitle.textContent = "El corte de hoy todavía no está publicado.";
  elements.emptyCopy.textContent =
    "Radar MX no mezcla titulares anteriores con la fecha vigente. Puedes consultar el último corte o navegar el archivo.";
  elements.emptyUpdate.textContent = `Última actualización verificada: ${formatGeneratedAt(data.generated_at)}.`;
  if (data.last_available_date) {
    elements.lastEdition.href = `archivo.html?fecha=${encodeURIComponent(data.last_available_date)}`;
    elements.lastEdition.textContent = `Abrir edición del ${formatDate(data.last_available_date, { short: true })}`;
    elements.lastEdition.hidden = false;
  } else {
    elements.lastEdition.hidden = true;
  }
  document.title = "Corte pendiente | Radar Regulatorio MX";
  showState("empty");
  if (editionDate) renderCoverage(data);
}

function renderEmpty(data) {
  elements.date.textContent = `Hoy · ${formatDate(data.edition_date)}`;
  elements.emptyLabel.textContent = "Corte publicado";
  elements.emptyTitle.textContent = "El corte de hoy no contiene novedades seleccionadas.";
  elements.emptyCopy.textContent =
    "Las fuentes fueron revisadas, pero ninguna publicación superó los criterios del radar para esta edición.";
  elements.emptyUpdate.textContent = `Corte generado: ${formatGeneratedAt(data.generated_at)}.`;
  if (data.last_available_date && data.last_available_date !== data.edition_date) {
    elements.lastEdition.href = `archivo.html?fecha=${encodeURIComponent(data.last_available_date)}`;
    elements.lastEdition.textContent = `Abrir edición del ${formatDate(data.last_available_date, { short: true })}`;
    elements.lastEdition.hidden = false;
  } else {
    elements.lastEdition.hidden = true;
  }
  document.title = "Sin novedades hoy | Radar Regulatorio MX";
  showState("empty");
  renderCoverage(data);
}

function signalLabel(signal) {
  return signal.issuing_body || signal.source || "Fuente oficial";
}

function selectSignal(index, { pointer = false, focus = false } = {}) {
  if (!editionData?.signals?.[index]) return;
  selectedIndex = index;
  const signal = editionData.signals[index];
  const update = () => {
    elements.leadPanel.style.setProperty("--wash", watercolorFor(signal));
    elements.leadRank.textContent = `Señal ${String(signal.rank).padStart(2, "0")}`;
    elements.leadOrgan.textContent = signalLabel(signal);
    elements.leadImportance.textContent = `Importancia ${Number(signal.importance) || 0}/5`;
    elements.leadTitle.textContent = signal.title || "Actualización regulatoria";
    elements.leadReason.textContent = signal.why_it_matters || signal.summary || "";
    elements.leadSummary.textContent = signal.summary || "Sin síntesis disponible.";
    elements.leadDetail.href = detailHref(signal, "hoy");
    if (isSafeHttpUrl(signal.url)) {
      elements.leadSource.href = signal.url;
      elements.leadSource.hidden = false;
    } else {
      elements.leadSource.hidden = true;
    }
    elements.band.querySelectorAll(".signal-tab").forEach((button, buttonIndex) => {
      const active = buttonIndex === index;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      button.classList.toggle("is-active", active);
    });
    elements.band.style.setProperty("--selected-index", String(index));
  };

  if (pointer && !reducedMotion) {
    elements.leadPanel.classList.add("is-leaving");
    setTimeout(() => {
      update();
      elements.leadPanel.classList.remove("is-leaving");
      elements.leadPanel.classList.add("is-entering");
      setTimeout(() => elements.leadPanel.classList.remove("is-entering"), 260);
    }, 150);
  } else {
    update();
  }
  if (focus) {
    const target = elements.band.querySelectorAll(".signal-tab")[index];
    if (target) target.focus();
  }
}

function buildBand(signals, editionDate) {
  elements.band.replaceChildren();
  signals.forEach((signal, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "signal-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "lead-panel");
    button.setAttribute("aria-selected", String(index === 0));
    button.setAttribute(
      "aria-label",
      `Señal ${signal.rank}: ${signal.title}. Importancia ${signal.importance} de 5.`,
    );
    button.tabIndex = index === 0 ? 0 : -1;
    button.style.setProperty("--card-index", String(index));
    button.style.setProperty("--card-tilt", `${CARD_TILTS[index] || 0}deg`);
    button.style.setProperty("--impact", String(Math.max(1, Math.min(5, Number(signal.importance) || 1))));
    button.style.setProperty("--wash", watercolorFor(signal));

    const rank = document.createElement("span");
    rank.className = "signal-tab-rank";
    rank.textContent = String(signal.rank).padStart(2, "0");
    const meter = document.createElement("span");
    meter.className = "signal-meter";
    const meterFill = document.createElement("span");
    meter.append(meterFill);
    const source = document.createElement("span");
    source.className = "signal-tab-source";
    source.textContent = signal.source || "Fuente";
    const title = document.createElement("span");
    title.className = "signal-tab-title";
    title.textContent = signal.title || "Actualización regulatoria";
    const impact = document.createElement("span");
    impact.className = "signal-tab-impact";
    impact.textContent = `${Number(signal.importance) || 0}/5`;
    button.append(rank, source, title, meter, impact);
    button.addEventListener("click", (event) => selectSignal(index, { pointer: event.detail > 0 }));
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

  try {
    const seen = localStorage.getItem("radar-seen-edition");
    if (seen !== editionDate && !reducedMotion) elements.band.classList.add("is-new-edition");
    localStorage.setItem("radar-seen-edition", editionDate);
  } catch {
    // El barrido es una mejora opcional; la edición no depende del almacenamiento.
  }
}

function createImportance(importance) {
  const value = Math.max(0, Math.min(5, Number(importance) || 0));
  const wrap = document.createElement("span");
  wrap.className = "inline-importance";
  wrap.setAttribute("aria-label", `Importancia ${value} de 5`);
  for (let index = 1; index <= 5; index += 1) {
    const bar = document.createElement("span");
    if (index <= value) bar.classList.add("is-on");
    wrap.append(bar);
  }
  return wrap;
}

function buildLedger(signals) {
  elements.list.replaceChildren();
  for (const signal of signals.slice(1)) {
    const row = document.createElement("li");
    row.className = "signal-row";
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
    meta.append(organ, createImportance(signal.importance));
    const title = document.createElement("h3");
    const link = document.createElement("a");
    link.href = detailHref(signal, "hoy");
    link.textContent = signal.title || "Actualización regulatoria";
    title.append(link);
    const reason = document.createElement("p");
    reason.textContent = signal.why_it_matters || signal.summary || "";
    body.append(meta, title, reason);
    const action = document.createElement("a");
    action.className = "signal-row-action";
    action.href = detailHref(signal, "hoy");
    action.textContent = "Abrir ficha";
    row.append(index, body, action);
    elements.list.append(row);
  }
  revealPaperCards(elements.list);
}

function revealPaperCards(container) {
  const cards = [...container.children];
  if (reducedMotion || !("IntersectionObserver" in window)) {
    cards.forEach((card) => card.classList.add("is-revealed"));
    return;
  }
  cards.forEach((card, index) => {
    card.classList.add("paper-reveal");
    card.style.setProperty("--reveal-delay", `${Math.min(index, 4) * 35}ms`);
  });
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-revealed");
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -8%", threshold: .08 });
  cards.forEach((card) => observer.observe(card));
}

function setupDeskMotion() {
  const desk = document.querySelector(".signal-desk");
  if (!desk || reducedMotion || !matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  let frame = 0;
  const setPosition = (x, y) => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      desk.style.setProperty("--pointer-x", x.toFixed(3));
      desk.style.setProperty("--pointer-y", y.toFixed(3));
    });
  };
  desk.addEventListener("pointermove", (event) => {
    const bounds = desk.getBoundingClientRect();
    setPosition(
      ((event.clientX - bounds.left) / bounds.width - .5) * 2,
      ((event.clientY - bounds.top) / bounds.height - .5) * 2,
    );
  });
  desk.addEventListener("pointerleave", () => setPosition(0, 0));
}

function renderReady(data) {
  const signals = Array.isArray(data.signals) ? data.signals : [];
  if (!signals.length) throw new Error("Una edición ready debe contener señales");
  editionData = data;
  selectedIndex = 0;
  elements.date.textContent = `Hoy · ${formatDate(data.edition_date)}`;
  elements.total.textContent = `${data.total_today} novedades · ${signals.length} seleccionadas`;
  elements.archiveToday.href = `archivo.html?fecha=${encodeURIComponent(data.edition_date)}`;
  buildBand(signals, data.edition_date);
  buildLedger(signals);
  selectSignal(0);
  renderCoverage(data);
  document.title = `Hoy, ${formatDate(data.edition_date, { short: true })} | Radar Regulatorio MX`;
  showState("ready");
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
  try {
    const response = await fetch("data/edition.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!validateEnvelope(data)) throw new Error("Contrato de edición inválido");
    const today = mexicoToday();
    if (data.edition_date !== today) {
      renderPending(data);
    } else if (data.state === "empty") {
      renderEmpty(data);
    } else {
      renderReady(data);
    }
  } catch (error) {
    showState("error");
    elements.coverageLabel.textContent = "Cobertura no verificada";
    elements.coverageCopy.textContent = "No fue posible leer el estado de las fuentes.";
    console.error(error);
  }
}

setupDeskMotion();

elements.retry.addEventListener("click", loadEdition);
loadEdition();
