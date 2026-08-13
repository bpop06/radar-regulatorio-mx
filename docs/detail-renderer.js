function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function paragraphs(parent, values) {
  for (const value of Array.isArray(values) ? values : []) {
    const copy = text(value);
    if (!copy) continue;
    const node = document.createElement("p");
    node.textContent = copy;
    parent.append(node);
  }
}

function section(root, key, title) {
  const node = document.createElement("section");
  node.className = `document-section detail-section detail-section--${key}`;
  node.dataset.section = key;
  const heading = document.createElement("h2");
  heading.textContent = title;
  node.append(heading);
  root.append(node);
  return node;
}

function subsection(parent, title, copies) {
  const node = document.createElement("div");
  node.className = "detail-subsection";
  const heading = document.createElement("h3");
  heading.textContent = title;
  node.append(heading);
  paragraphs(node, copies);
  parent.append(node);
  return node;
}

function optionalFact(list, label, value) {
  const copy = text(value);
  if (!copy) return;
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = copy;
  list.append(term, description);
}

export function renderStructuredDetail(root, detail) {
  root.replaceChildren();

  const executive = section(root, "executive-summary", "Resumen ejecutivo");
  paragraphs(executive, detail?.executive_summary);

  const detailed = section(root, "detailed-summary", "Resumen detallado");
  for (const entry of Array.isArray(detail?.detailed_summary) ? detail.detailed_summary : []) {
    if (!entry || typeof entry !== "object") continue;
    subsection(detailed, text(entry.heading) || "Contenido", entry.paragraphs);
  }

  const impacts = section(root, "impacts", "Impactos");
  const general = detail?.impacts?.general;
  subsection(
    impacts,
    "Impacto general",
    Array.isArray(general?.paragraphs) ? general.paragraphs : [],
  );
  for (const affected of Array.isArray(detail?.impacts?.sectors) ? detail.impacts.sectors : []) {
    if (!affected || typeof affected !== "object") continue;
    subsection(
      impacts,
      text(affected.sector) || "Sector identificado",
      affected.paragraphs,
    );
  }

  const actions = section(root, "recommended-actions", "Acciones recomendadas");
  for (const action of Array.isArray(detail?.recommended_actions)
    ? detail.recommended_actions : []) {
    if (!action || typeof action !== "object") continue;
    const group = subsection(
      actions,
      text(action.affected_group) || "Persona o sector afectado",
      action.paragraphs,
    );
    const facts = document.createElement("dl");
    facts.className = "action-facts";
    optionalFact(facts, "Detonante", action.trigger);
    optionalFact(facts, "Plazo", action.deadline);
    optionalFact(facts, "Fundamento", action.legal_basis);
    if (facts.childElementCount) group.append(facts);
  }
}

export function renderTraceability(root, detail) {
  root.replaceChildren();
  for (const locator of Array.isArray(detail?.evidence?.locators)
    ? detail.evidence.locators : []) {
    const node = document.createElement("li");
    node.textContent = `${text(locator.label)} — ${text(locator.location)}`;
    root.append(node);
  }
}

export function structuredDetailIsUsable(detail) {
  const hasParagraphs = (value) => Array.isArray(value)
    && value.length > 0 && value.every((entry) => text(entry));
  const detailed = Array.isArray(detail?.detailed_summary)
    && detail.detailed_summary.length > 0
    && detail.detailed_summary.every((entry) => text(entry?.heading)
      && hasParagraphs(entry?.paragraphs) && hasParagraphs(entry?.evidence_refs));
  const general = hasParagraphs(detail?.impacts?.general?.paragraphs)
    && hasParagraphs(detail?.impacts?.general?.evidence_refs);
  const sectors = Array.isArray(detail?.impacts?.sectors)
    && detail.impacts.sectors.length > 0
    && detail.impacts.sectors.every((entry) => text(entry?.sector)
      && hasParagraphs(entry?.paragraphs) && hasParagraphs(entry?.evidence_refs));
  const actions = Array.isArray(detail?.recommended_actions)
    && detail.recommended_actions.length > 0
    && detail.recommended_actions.every((entry) => text(entry?.affected_group)
      && hasParagraphs(entry?.paragraphs) && hasParagraphs(entry?.evidence_refs));
  const locators = Array.isArray(detail?.evidence?.locators)
    && detail.evidence.locators.length > 0
    && detail.evidence.locators.every((entry) => text(entry?.id)
      && text(entry?.label) && text(entry?.location));
  const locatorIds = new Set((detail?.evidence?.locators || []).map((entry) => entry.id));
  const refs = [
    ...(detail?.detailed_summary || []).flatMap((entry) => entry.evidence_refs || []),
    ...(detail?.impacts?.general?.evidence_refs || []),
    ...(detail?.impacts?.sectors || []).flatMap((entry) => entry.evidence_refs || []),
    ...(detail?.recommended_actions || []).flatMap((entry) => entry.evidence_refs || []),
  ];
  const evidence = text(detail?.evidence?.official_url).startsWith("https://")
    && text(detail?.evidence?.retrieved_at)
    && /^[a-f0-9]{64}$/i.test(text(detail?.evidence?.content_hash))
    && text(detail?.evidence?.extraction_method)
    && locators && refs.every((reference) => locatorIds.has(reference));
  const coverage = Array.isArray(detail?.coverage) && detail.coverage.length > 0
    && detail.coverage.every((entry) => locatorIds.has(entry?.source_ref)
      && ["summarized", "not_material"].includes(entry?.disposition)
      && (entry.disposition !== "summarized"
        || (Array.isArray(entry.target_sections) && entry.target_sections.length > 0))
      && (entry.disposition !== "not_material" || text(entry.reason)));
  return hasParagraphs(detail?.executive_summary)
    && detailed && general && sectors && actions && evidence && coverage;
}
