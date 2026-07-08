const elements = {
  breadcrumb: document.querySelector("#bc-meta"),
  title: document.querySelector("#detail-title"),
  organ: document.querySelector("#detail-organ"),
  signal: document.querySelector("#detail-signal"),
  importance: document.querySelector("#detail-importance"),
  case: document.querySelector("#detail-case"),
  caseChip: document.querySelector("#detail-case .case-chip"),
  parties: document.querySelector("#detail-parties"),
  categories: document.querySelector("#detail-categories"),
  content: document.querySelector("#detail-content"),
  officialSource: document.querySelector("#official-source"),
};

const monthShort = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];

function monoDate(iso) {
  if (typeof iso !== "string") return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return "—";
  return `${String(d).padStart(2, "0")}·${monthShort[m - 1] || "?"}·${y}`;
}

function buildImportanceBar(container, importance) {
  container.replaceChildren();
  const n = Math.max(0, Math.min(5, Number(importance) || 0));
  container.dataset.importance = String(n);
  for (let i = 0; i < 5; i++) {
    const seg = document.createElement("span");
    seg.className = i < n ? "seg on" : "seg";
    container.append(seg);
  }
}

function render(markdown, container) {
  if (window.Radar) window.Radar.renderMarkdown(markdown, container);
}

function fallbackMarkdown(item) {
  const categories = Array.isArray(item.categories) && item.categories.length
    ? item.categories.join(", ")
    : "Sin materia clasificada";
  return [
    `## Resumen ejecutivo`,
    item.summary || "Sin resumen disponible.",
    "## Información oficial",
    `**Título oficial:** ${item.official_title || "Sin título oficial"}`,
    `**Tipo de documento:** ${item.document_type || "Documento oficial"}`,
    `**Descripción de origen:** ${item.description || item.official_title || "Sin descripción"}`,
    "## Clasificación",
    `**Materias:** ${categories}.`,
    "## Fuente oficial",
    `[Abrir documento oficial](${item.url})`,
  ].join("\n\n");
}

function showMessage(title, detail) {
  elements.title.textContent = title;
  elements.breadcrumb.textContent = "—";
  elements.content.replaceChildren();
  const paragraph = document.createElement("p");
  paragraph.textContent = detail;
  elements.content.append(paragraph);
}

async function loadDetail() {
  const id = new URLSearchParams(window.location.search).get("id");
  if (!id) {
    showMessage("Ficha no encontrada", "La URL no incluye el identificador de la noticia.");
    return;
  }

  try {
    const response = await fetch("data/publications.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const items = payload.items || [];
    const idx = items.findIndex((publication) => publication.id === id);
    const item = idx >= 0 ? items[idx] : null;

    if (!item) {
      showMessage("Ficha no encontrada", "No hay una publicación con ese identificador.");
      return;
    }

    document.title = `${item.title} | Radar Regulatorio MX`;

    const folio = String(idx + 1).padStart(3, "0");
    elements.breadcrumb.textContent = `№ ${folio} · ${item.source} · ${monoDate(item.published_at)}`;
    elements.title.textContent = item.title || "Ficha regulatoria";

    if (Number(item.importance) > 0) {
      buildImportanceBar(elements.importance, item.importance);
      elements.signal.hidden = false;
    }

    const organ = item.issuing_body || item.authority;
    if (organ) {
      elements.organ.textContent = organ;
      elements.organ.hidden = false;
    }

    // Contrato v4 (opcional): case_status / case_parties. Tolerante a su ausencia
    // (los datos del contrato viejo no traen estos campos).
    const caseStatus = typeof item.case_status === "string" ? item.case_status.trim() : "";
    if (caseStatus && elements.case && elements.caseChip) {
      const caseLabel = window.Radar ? window.Radar.translateCaseStatus(caseStatus) : caseStatus;
      elements.caseChip.textContent = `CASO · ${caseLabel}`;
      elements.case.hidden = false;
    }

    const partiesText = Array.isArray(item.case_parties)
      ? item.case_parties.filter(Boolean).join(", ")
      : typeof item.case_parties === "string"
        ? item.case_parties.trim()
        : "";
    if (partiesText && elements.parties) {
      elements.parties.textContent = `Partes: ${partiesText}`;
      elements.parties.hidden = false;
    }

    // Contrato v6: materias como fila de chips (reusa .badge, ver docs/styles.css).
    const categories = Array.isArray(item.categories) ? item.categories.filter(Boolean) : [];
    if (categories.length && elements.categories) {
      elements.categories.replaceChildren();
      for (const category of categories) {
        const chip = document.createElement("span");
        chip.className = "badge";
        chip.textContent = category;
        elements.categories.append(chip);
      }
      elements.categories.hidden = false;
    }

    // Ficha única (v6): el backend ya integra card_body en detail_markdown. Solo los
    // ítems del contrato viejo (sin detail_markdown) degradan a card_body o al fallback.
    render(item.detail_markdown || item.card_body || fallbackMarkdown(item), elements.content);

    if (window.Radar && window.Radar.isSafeHttpUrl(item.url)) {
      elements.officialSource.href = item.url;
      elements.officialSource.hidden = false;
    }
  } catch (error) {
    showMessage("No fue posible cargar la ficha", "Revisa que los datos publicados estén disponibles.");
    console.error(error);
  }
}

loadDetail();
