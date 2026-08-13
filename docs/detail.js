import {
  datePresentation,
  getSections,
  isSafeHttpUrl,
  renderMarkdown,
  renderSection,
  translateCaseStatus,
} from "./markdown.js?v=20260812b";
import { appendIcon } from "./icons.js?v=20260812b";

const elements = {
  back: document.querySelector("#detail-back"),
  status: document.querySelector("#detail-status"),
  breadcrumb: document.querySelector("#detail-breadcrumb"),
  title: document.querySelector("#detail-title"),
  summary: document.querySelector("#detail-summary"),
  why: document.querySelector("#detail-why"),
  whyCopy: document.querySelector("#detail-why-copy"),
  content: document.querySelector("#detail-content"),
  date: document.querySelector("#detail-date"),
  updated: document.querySelector("#detail-updated"),
  organ: document.querySelector("#detail-organ"),
  source: document.querySelector("#detail-source"),
  importance: document.querySelector("#detail-importance"),
  caseRow: document.querySelector("#detail-case-row"),
  case: document.querySelector("#detail-case"),
  partiesRow: document.querySelector("#detail-parties-row"),
  parties: document.querySelector("#detail-parties"),
  categories: document.querySelector("#detail-categories"),
  officialSource: document.querySelector("#official-source"),
};

function setBackLink(origin) {
  const label = document.createElement("span");
  if (origin === "hoy") {
    elements.back.href = "index.html";
    label.textContent = "Volver a Hoy";
  } else {
    elements.back.href = "archivo.html";
    label.textContent = "Volver al archivo";
  }
  elements.back.replaceChildren();
  appendIcon(elements.back, "arrow-back");
  elements.back.append(label);
}

function setTime(element, value, options = {}) {
  const presentation = datePresentation(value, options);
  element.textContent = presentation.label;
  if (presentation.dateTime) element.setAttribute("datetime", presentation.dateTime);
  else element.removeAttribute("datetime");
}

function renderImportance(value) {
  const importance = Math.max(0, Math.min(5, Number(value) || 0));
  elements.importance.replaceChildren();
  elements.importance.setAttribute("aria-label", `Importancia ${importance} de 5`);
  for (let index = 1; index <= 5; index += 1) {
    const bar = document.createElement("span");
    if (index <= importance) bar.classList.add("is-on");
    elements.importance.append(bar);
  }
}

function fallbackMarkdown(item) {
  return [
    "## Qué se publicó",
    item.description || item.official_title || "Sin descripción disponible.",
    "## Sustancia",
    item.summary || "Sin síntesis disponible.",
    "## Fuente oficial",
    `[Abrir documento oficial](${item.url})`,
  ].join("\n\n");
}

function renderDocument(item) {
  const markdown = item.detail_markdown || item.card_body || fallbackMarkdown(item);
  const sections = getSections(markdown);
  elements.content.replaceChildren();
  if (sections.length) {
    sections.forEach((section) => renderSection(section, elements.content));
  } else {
    renderMarkdown(markdown, elements.content);
    const duplicateHeading = elements.content.querySelector("h1");
    if (duplicateHeading) duplicateHeading.remove();
  }
}

function findEditionReason(edition, id) {
  const signal = Array.isArray(edition?.signals)
    ? edition.signals.find((candidate) => candidate.id === id)
    : null;
  return signal?.why_it_matters || "";
}

function showMessage(title, message) {
  elements.title.textContent = title;
  elements.summary.textContent = message;
  elements.breadcrumb.textContent = "Evidencia no disponible";
  elements.content.replaceChildren();
  elements.status.textContent = title;
}

async function fetchOptionalEdition() {
  try {
    const response = await fetch("data/edition.json", { cache: "no-store" });
    return response.ok ? response.json() : null;
  } catch {
    return null;
  }
}

async function loadDetail() {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  setBackLink(params.get("from"));
  if (!id) {
    showMessage("Ficha no encontrada", "La URL no incluye el identificador de la publicación.");
    return;
  }

  try {
    const [response, edition] = await Promise.all([
      fetch("data/publications.json", { cache: "no-store" }),
      fetchOptionalEdition(),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const items = Array.isArray(payload.items) ? payload.items : [];
    const item = items.find((publication) => publication.id === id);
    if (!item) {
      showMessage("Ficha no encontrada", "No existe una publicación con ese identificador en el corte disponible.");
      return;
    }

    document.title = `${item.title} | Radar Regulatorio MX`;
    elements.breadcrumb.replaceChildren(document.createTextNode(`${item.source} · `));
    const breadcrumbDate = datePresentation(item.published_at);
    const breadcrumbTime = document.createElement("time");
    if (breadcrumbDate.dateTime) breadcrumbTime.dateTime = breadcrumbDate.dateTime;
    breadcrumbTime.textContent = breadcrumbDate.label;
    elements.breadcrumb.append(breadcrumbTime);
    elements.title.textContent = item.title || "Ficha regulatoria";
    elements.summary.textContent = item.summary || "";
    setTime(elements.date, item.published_at);
    setTime(elements.updated, payload.generated_at, { type: "datetime", short: true });
    elements.organ.textContent = item.issuing_body || item.authority || "No identificado";
    elements.source.textContent = item.source || "Fuente oficial";
    elements.categories.textContent = Array.isArray(item.categories) && item.categories.length
      ? item.categories.join(" · ")
      : "Sin materia clasificada";
    renderImportance(item.importance);

    const reason = findEditionReason(edition, id);
    if (reason) {
      elements.whyCopy.textContent = reason;
      elements.why.hidden = false;
    }

    const caseStatus = typeof item.case_status === "string" ? item.case_status.trim() : "";
    if (caseStatus) {
      elements.case.textContent = translateCaseStatus(caseStatus);
      elements.caseRow.hidden = false;
    }
    const parties = Array.isArray(item.case_parties)
      ? item.case_parties.filter(Boolean).join(", ")
      : typeof item.case_parties === "string" ? item.case_parties.trim() : "";
    if (parties) {
      elements.parties.textContent = parties;
      elements.partiesRow.hidden = false;
    }

    renderDocument(item);
    if (isSafeHttpUrl(item.url)) {
      elements.officialSource.href = item.url;
      elements.officialSource.setAttribute("aria-label", `Consultar fuente oficial: ${item.title}. Se abre en una pestaña nueva.`);
      elements.officialSource.hidden = false;
    }
    elements.status.textContent = "Ficha cargada.";
  } catch (error) {
    showMessage("No fue posible cargar la ficha", "Revisa que los datos publicados estén disponibles.");
    console.error(error);
  }
}

loadDetail();
