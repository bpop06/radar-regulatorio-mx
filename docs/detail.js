const elements = {
  content: document.querySelector("#detail-content"),
  fullDetails: document.querySelector("#detail-full"),
  fullContent: document.querySelector("#detail-full-content"),
  officialSource: document.querySelector("#official-source"),
};

function renderMarkdown(markdown, container) {
  container.replaceChildren();
  const blocks = markdown.trim().split(/\n{2,}/);

  for (const rawBlock of blocks) {
    const block = rawBlock.trim();
    if (!block) continue;

    if (block.startsWith("# ")) {
      const heading = document.createElement("h1");
      heading.textContent = block.slice(2).trim();
      container.append(heading);
      continue;
    }

    if (block.startsWith("## ")) {
      const heading = document.createElement("h2");
      heading.textContent = block.slice(3).trim();
      container.append(heading);
      continue;
    }

    if (block.startsWith("- ")) {
      const list = document.createElement("ul");
      for (const line of block.split("\n")) {
        if (!line.startsWith("- ")) continue;
        const item = document.createElement("li");
        appendInline(item, line.slice(2).trim());
        list.append(item);
      }
      container.append(list);
      continue;
    }

    const paragraph = document.createElement("p");
    block.split("\n").forEach((line, index) => {
      if (index > 0) paragraph.append(document.createElement("br"));
      appendInline(paragraph, line);
    });
    container.append(paragraph);
  }
}

function appendInline(container, text) {
  const inlinePattern = /(\*\*([^*]+)\*\*)|\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
  let lastIndex = 0;
  let match = inlinePattern.exec(text);

  while (match) {
    if (match.index > lastIndex) {
      container.append(document.createTextNode(text.slice(lastIndex, match.index)));
    }

    if (match[2]) {
      const strong = document.createElement("strong");
      strong.textContent = match[2];
      container.append(strong);
    } else if (match[3] && match[4]) {
      const link = document.createElement("a");
      link.href = match[4];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[3];
      container.append(link);
    }

    lastIndex = inlinePattern.lastIndex;
    match = inlinePattern.exec(text);
  }

  if (lastIndex < text.length) {
    container.append(document.createTextNode(text.slice(lastIndex)));
  }
}

function fallbackMarkdown(item) {
  const categories = Array.isArray(item.categories) && item.categories.length
    ? item.categories.join(", ")
    : "Sin materia clasificada";
  return [
    `# ${item.title || "Ficha regulatoria"}`,
    (
      `**Fecha de publicación:** ${item.published_at || "Sin fecha"}. ` +
      `**Fuente:** ${item.source || "Sin fuente"}. ` +
      `**Autoridad:** ${item.authority || "Autoridad no identificada"}.`
    ),
    "## Resumen ejecutivo",
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

function buildCardMarkdown(item) {
  const metaLine =
    `**Fecha de publicación:** ${item.published_at || "Sin fecha"}. ` +
    `**Fuente:** ${item.source || "Sin fuente"}. ` +
    `**Emitido por:** ${item.issuing_body || item.authority || "Autoridad no identificada"}.`;
  return [`# ${item.title || "Ficha regulatoria"}`, metaLine, item.card_body].join("\n\n");
}

function showMessage(title, detail) {
  elements.content.replaceChildren();
  const heading = document.createElement("h1");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = detail;
  elements.content.append(heading, paragraph);
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
    const item = (payload.items || []).find((publication) => publication.id === id);

    if (!item) {
      showMessage("Ficha no encontrada", "No hay una publicación con ese identificador.");
      return;
    }

    document.title = `${item.title} | Radar Regulatorio MX`;

    if (item.card_body) {
      renderMarkdown(buildCardMarkdown(item), elements.content);
      if (item.detail_markdown) {
        renderMarkdown(item.detail_markdown, elements.fullContent);
        elements.fullDetails.hidden = false;
      } else {
        elements.fullDetails.hidden = true;
      }
    } else {
      renderMarkdown(item.detail_markdown || fallbackMarkdown(item), elements.content);
      elements.fullDetails.hidden = true;
    }

    if (typeof item.url === "string" && /^https?:\/\//i.test(item.url)) {
      elements.officialSource.href = item.url;
      elements.officialSource.hidden = false;
    }
  } catch (error) {
    showMessage("No fue posible cargar la ficha", "Revisa que los datos publicados estén disponibles.");
    console.error(error);
  }
}

loadDetail();
