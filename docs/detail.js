import {
  displayTitle,
  editorialLabel,
  loadEdition,
  loadItem,
  loadManifest,
  officialDate,
  teaser,
} from "./data-client.js";
import {
  formatDate,
  getSections,
  isSafeHttpUrl,
  renderMarkdown,
  renderSection,
  translateCaseStatus,
} from "./markdown.js";

const elements = {
  back: document.querySelector("#detail-back"),
  breadcrumb: document.querySelector("#detail-breadcrumb"),
  title: document.querySelector("#detail-title"),
  status: document.querySelector("#detail-editorial-status"),
  summary: document.querySelector("#detail-summary"),
  review: document.querySelector("#detail-review"),
  reviewReason: document.querySelector("#detail-review-reason"),
  why: document.querySelector("#detail-why"),
  whyCopy: document.querySelector("#detail-why-copy"),
  loadStatus: document.querySelector("#detail-load-status"),
  content: document.querySelector("#detail-content"),
  officialDate: document.querySelector("#detail-date"),
  detectedDate: document.querySelector("#detail-detected-date"),
  detectedRow: document.querySelector("#detail-detected-row"),
  identifier: document.querySelector("#detail-identifier"),
  identifierRow: document.querySelector("#detail-identifier-row"),
  organ: document.querySelector("#detail-organ"),
  source: document.querySelector("#detail-source"),
  importance: document.querySelector("#detail-importance"),
  caseNumberRow: document.querySelector("#detail-case-number-row"),
  caseNumber: document.querySelector("#detail-case-number"),
  caseRow: document.querySelector("#detail-case-row"),
  case: document.querySelector("#detail-case"),
  partiesRow: document.querySelector("#detail-parties-row"),
  parties: document.querySelector("#detail-parties"),
  treatyRow: document.querySelector("#detail-treaty-row"),
  treaty: document.querySelector("#detail-treaty"),
  litisRow: document.querySelector("#detail-litis-row"),
  litis: document.querySelector("#detail-litis"),
  outcomeRow: document.querySelector("#detail-outcome-row"),
  outcome: document.querySelector("#detail-outcome"),
  reasoningRow: document.querySelector("#detail-reasoning-row"),
  reasoning: document.querySelector("#detail-reasoning"),
  amountRow: document.querySelector("#detail-amount-row"),
  amount: document.querySelector("#detail-amount"),
  categories: document.querySelector("#detail-categories"),
  officialSource: document.querySelector("#official-source"),
};

function setBackLink(origin) {
  if (origin === "hoy") {
    elements.back.href = "index.html";
    elements.back.textContent = "← Volver a Hoy";
  } else {
    elements.back.href = "archivo.html";
    elements.back.textContent = "← Volver al archivo";
  }
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
  const blocks = [
    "## Qué se publicó", item.description || item.official_title || "Sin descripción disponible.",
    "## Sustancia", item.summary || "La síntesis editorial no está disponible.",
  ];
  if (isSafeHttpUrl(item.url || item.canonical_url)) {
    blocks.push("## Fuente oficial", `[Abrir documento oficial](${item.url || item.canonical_url})`);
  }
  return blocks.join("\n\n");
}

function renderDocument(item) {
  const markdown = item.detail_markdown || item.card_body || fallbackMarkdown(item);
  const sections = getSections(markdown);
  elements.content.replaceChildren();
  if (sections.length) sections.forEach((section) => renderSection(section, elements.content));
  else renderMarkdown(markdown, elements.content);
}

function appendPlainSection(title, value) {
  if (!value) return;
  const section = document.createElement("section");
  section.className = "document-section";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = value;
  section.append(heading, copy);
  elements.content.append(section);
}

function findEditionReason(edition, item) {
  const signals = Array.isArray(edition?.signals) ? edition.signals : [];
  const match = signals.find((candidate) => candidate.id === item.id
    || (candidate.source === item.source && candidate.source_id && candidate.source_id === item.source_id));
  return match?.why_it_matters || "";
}

function setMeta(name, content, property = false) {
  if (!content) return;
  const selector = property ? `meta[property="${name}"]` : `meta[name="${name}"]`;
  let meta = document.querySelector(selector);
  if (!meta) {
    meta = document.createElement("meta");
    meta.setAttribute(property ? "property" : "name", name);
    document.head.append(meta);
  }
  meta.content = content;
}

function updateSeo(item) {
  const title = displayTitle(item);
  const description = teaser(item) || item.official_title || "Ficha de evidencia oficial.";
  document.title = `${title} · Radar Regulatorio MX`;
  setMeta("description", description);
  setMeta("og:title", title, true);
  setMeta("og:description", description, true);
  setMeta("og:type", "article", true);
  if (isSafeHttpUrl(location.href)) setMeta("og:url", location.href, true);
}

function stringValue(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join(" · ");
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value);
}

function setOptionalRow(row, target, value, transform = stringValue) {
  const rendered = transform(value);
  if (!rendered) return;
  target.textContent = rendered;
  row.hidden = false;
}

function internationalCase(item) {
  return item.international_case || item.case_details || {};
}

function renderCase(item) {
  const details = internationalCase(item);
  setOptionalRow(elements.caseNumberRow, elements.caseNumber,
    details.number || item.case_number || item.case_no);
  setOptionalRow(elements.caseRow, elements.case,
    details.status || item.case_status, translateCaseStatus);
  setOptionalRow(elements.partiesRow, elements.parties,
    details.parties || item.case_parties);
  setOptionalRow(elements.treatyRow, elements.treaty,
    details.treaty || details.instrument || item.case_treaty);
  setOptionalRow(elements.litisRow, elements.litis,
    details.claim || details.dispute || details.litis
      || item.case_claim || item.case_litis || item.case_facts);
  setOptionalRow(elements.outcomeRow, elements.outcome,
    details.outcome || details.result || item.case_outcome);
  setOptionalRow(elements.reasoningRow, elements.reasoning,
    details.reasoning || item.case_reasoning);
  setOptionalRow(elements.amountRow, elements.amount,
    details.amount || item.case_amount);
}

function renderReview(item) {
  elements.review.hidden = false;
  elements.reviewReason.textContent = item.review_reason
    || "La publicación requiere revisión editorial antes de resumir o interpretar su alcance.";
  elements.summary.textContent = "";
  elements.content.replaceChildren();
  appendPlainSection("Evidencia disponible", item.official_title
    || "Los metadatos y el enlace a la publicación oficial están disponibles para consulta.");
}

function showMessage(title, message) {
  elements.title.textContent = title;
  elements.summary.textContent = message;
  elements.breadcrumb.textContent = "Evidencia no disponible";
  elements.status.textContent = "Ficha no disponible";
  elements.status.className = "editorial-badge is-pending";
  elements.content.replaceChildren();
  elements.content.setAttribute("aria-busy", "false");
  elements.loadStatus.textContent = message;
  document.title = `${title} · Radar Regulatorio MX`;
}

async function fetchOptionalEdition(manifest) {
  return loadEdition(manifest);
}

async function loadDetail() {
  const params = new URLSearchParams(location.search);
  const key = params.get("item") || params.get("key") || "";
  const id = params.get("id") || "";
  setBackLink(params.get("from"));
  if (!key && !id) {
    showMessage("Ficha no encontrada", "La URL no incluye el identificador de la publicación.");
    return;
  }

  try {
    const manifest = await loadManifest();
    const [item, edition] = await Promise.all([
      loadItem({ key, id }, manifest),
      fetchOptionalEdition(manifest),
    ]);
    if (!item) {
      showMessage("Ficha no encontrada", "No existe una publicación con ese identificador en el archivo disponible.");
      return;
    }

    const pending = item.editorial_status === "needs_review";
    updateSeo(item);
    elements.breadcrumb.textContent = `${item.source || "Fuente oficial"} · ${formatDate(officialDate(item))}`;
    elements.title.textContent = displayTitle(item);
    elements.status.textContent = editorialLabel(item);
    elements.status.className = `editorial-badge ${pending ? "is-pending" : "is-complete"}`;
    elements.summary.textContent = pending ? "" : teaser(item);
    elements.officialDate.textContent = formatDate(officialDate(item));
    elements.organ.textContent = item.issuing_body || item.authority || "No identificado";
    elements.source.textContent = item.source || "Fuente oficial";
    elements.categories.textContent = Array.isArray(item.categories) && item.categories.length
      ? item.categories.join(" · ") : "Sin materia clasificada";
    renderImportance(item.importance);

    if (item.detected_at) {
      elements.detectedDate.textContent = formatDate(item.detected_at);
      elements.detectedRow.hidden = false;
    }
    const identifier = item.official_identifier || item.source_id;
    if (identifier) {
      elements.identifier.textContent = identifier;
      elements.identifierRow.hidden = false;
    }

    if (pending) renderReview(item);
    else {
      renderDocument(item);
      const reason = findEditionReason(edition, item);
      if (reason) {
        elements.whyCopy.textContent = reason;
        elements.why.hidden = false;
      }
    }
    renderCase(item);
    elements.content.setAttribute("aria-busy", "false");
    elements.loadStatus.textContent = "Ficha cargada.";

    const sourceUrl = item.url || item.canonical_url;
    if (isSafeHttpUrl(sourceUrl)) {
      elements.officialSource.href = sourceUrl;
      elements.officialSource.hidden = false;
    }
  } catch (error) {
    showMessage("No fue posible cargar la ficha", "Comprueba la conexión y vuelve a intentarlo desde el archivo.");
    console.error(error);
  }
}

loadDetail();
