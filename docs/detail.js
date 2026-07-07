const elements = {
  breadcrumb: document.querySelector("#bc-meta"),
  title: document.querySelector("#detail-title"),
  organ: document.querySelector("#detail-organ"),
  signal: document.querySelector("#detail-signal"),
  importance: document.querySelector("#detail-importance"),
  content: document.querySelector("#detail-content"),
  fullDetails: document.querySelector("#detail-full"),
  fullContent: document.querySelector("#detail-full-content"),
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
  const n = Math.max(0, Math.min(3, Number(importance) || 0));
  for (let i = 0; i < 3; i++) {
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
  elements.fullDetails.hidden = true;
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

    if (item.card_body) {
      render(item.card_body, elements.content);
      if (item.detail_markdown) {
        render(item.detail_markdown, elements.fullContent);
        elements.fullDetails.hidden = false;
      } else {
        elements.fullDetails.hidden = true;
      }
    } else {
      render(item.detail_markdown || fallbackMarkdown(item), elements.content);
      elements.fullDetails.hidden = true;
    }

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
