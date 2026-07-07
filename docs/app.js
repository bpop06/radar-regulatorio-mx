const state = {
  items: [],
  sources: [],
  generatedAt: null,
  query: "",
  category: "Todas",
  source: "Todas",
  dateRange: "all",
  issuingBody: "Todas",
  jurisdiction: "Todas",
  month: "all",
  sort: "date",
  page: 1,
  pageSize: 24,
};

const supportedCategories = [
  "Fiscal",
  "Aduanero",
  "Comercio exterior",
  "Propiedad intelectual",
  "Normalización",
  "Derecho administrativo",
  "Nombramientos federales",
  "Contencioso administrativo",
  "Contencioso administrativo fiscal",
  "Iniciativa",
];

const searchAliases = {
  "Contencioso administrativo": "procesal administrativo juicio de nulidad",
  "Contencioso administrativo fiscal":
    "procesal fiscal procedimiento contencioso administrativo fiscal",
  "Derecho administrativo": "lfpa loapf administración pública federal",
  "Nombramientos federales":
    "designaciones cargos públicos directores subdirectores titulares",
};

const monthNames = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

const elements = {
  list: document.querySelector("#news-list"),
  template: document.querySelector("#news-template"),
  filters: document.querySelector("#category-filters"),
  search: document.querySelector("#search"),
  sourceFilter: document.querySelector("#source-filter"),
  dateRange: document.querySelector("#date-range"),
  issuingBodyFilter: document.querySelector("#issuing-body-filter"),
  jurisdictionFilter: document.querySelector("#jurisdiction-filter"),
  monthFilter: document.querySelector("#month-filter"),
  sort: document.querySelector("#sort"),
  pageSize: document.querySelector("#page-size"),
  pagination: document.querySelector("#pagination"),
  pagePrev: document.querySelector("#page-prev"),
  pageNext: document.querySelector("#page-next"),
  pageIndicator: document.querySelector("#page-indicator"),
  empty: document.querySelector("#empty-state"),
  emptyEyebrow: document.querySelector("#empty-eyebrow"),
  emptyTitle: document.querySelector("#empty-title"),
  count: document.querySelector("#result-count"),
  updated: document.querySelector("#last-update"),
  feedNote: document.querySelector("#feed-note"),
  sourceAlert: document.querySelector("#source-alert"),
  sourceSummary: document.querySelector("#source-summary"),
  sources: document.querySelector("#source-status"),
  clear: document.querySelector("#clear-filters"),
};

const dayInMilliseconds = 24 * 60 * 60 * 1000;

const dateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

function normalize(value) {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

function isSafeHttpUrl(u) {
  return typeof u === "string" && /^https?:\/\//i.test(u);
}

function detailUrlFor(item) {
  const fallback = `ficha.html?id=${encodeURIComponent(item.id)}`;
  if (typeof item.detail_url === "string" && item.detail_url.startsWith("ficha.html?id=")) {
    return item.detail_url;
  }
  return fallback;
}

function monthKey(item) {
  return `${item.published_year}-${String(item.published_month).padStart(2, "0")}`;
}

function monthLabel(key) {
  const [year, month] = key.split("-").map(Number);
  const name = monthNames[month - 1] || "";
  return `${name} ${year}`.trim();
}

function renderFilters() {
  elements.filters.replaceChildren();
  const allButton = document.createElement("button");
  allButton.className = "filter active";
  allButton.type = "button";
  allButton.dataset.category = "Todas";
  allButton.textContent = "Todas";
  elements.filters.append(allButton);

  const discovered = state.items.flatMap((item) => item.categories);
  const categories = [...new Set([...supportedCategories, ...discovered])];
  for (const category of categories) {
    if (category === "Todas") continue;
    const button = document.createElement("button");
    button.className = "filter";
    button.type = "button";
    button.dataset.category = category;
    button.textContent = category;
    elements.filters.append(button);
  }
}

function renderSourceFilter() {
  const selected = state.source;
  const sources = [...new Set(state.items.map((item) => item.source))].sort((left, right) =>
    left.localeCompare(right, "es"),
  );
  elements.sourceFilter.replaceChildren(new Option("Todas", "Todas"));
  for (const source of sources) {
    elements.sourceFilter.append(new Option(source, source));
  }
  state.source = sources.includes(selected) ? selected : "Todas";
  elements.sourceFilter.value = state.source;
}

function renderIssuingBodyFilter() {
  const selected = state.issuingBody;
  const bodies = [...new Set(state.items.map((item) => item.issuing_body).filter(Boolean))].sort(
    (left, right) => left.localeCompare(right, "es"),
  );
  elements.issuingBodyFilter.replaceChildren(new Option("Todas", "Todas"));
  for (const body of bodies) {
    elements.issuingBodyFilter.append(new Option(body, body));
  }
  state.issuingBody = bodies.includes(selected) ? selected : "Todas";
  elements.issuingBodyFilter.value = state.issuingBody;
}

function renderMonthFilter() {
  const selected = state.month;
  const keys = [...new Set(state.items.map((item) => monthKey(item)))].sort((left, right) =>
    right.localeCompare(left),
  );
  elements.monthFilter.replaceChildren(new Option("Todos", "all"));
  for (const key of keys) {
    const label = monthLabel(key);
    elements.monthFilter.append(new Option(label.charAt(0).toUpperCase() + label.slice(1), key));
  }
  state.month = keys.includes(selected) ? selected : "all";
  elements.monthFilter.value = state.month;
}

function activeAnchorDate() {
  if (state.generatedAt && !Number.isNaN(state.generatedAt.valueOf())) {
    return state.generatedAt;
  }
  const timestamps = state.items
    .map((item) => new Date(`${item.published_at}T00:00:00Z`).valueOf())
    .filter((value) => !Number.isNaN(value));
  return timestamps.length ? new Date(Math.max(...timestamps)) : null;
}

function matchesDateRange(item) {
  if (state.dateRange === "all") return true;
  const anchor = activeAnchorDate();
  const itemDate = new Date(`${item.published_at}T00:00:00Z`);
  if (!anchor || Number.isNaN(itemDate.valueOf())) return true;
  const elapsed = anchor.valueOf() - itemDate.valueOf();
  return elapsed >= 0 && elapsed <= Number(state.dateRange) * dayInMilliseconds;
}

function matchesIssuingBody(item) {
  return state.issuingBody === "Todas" || item.issuing_body === state.issuingBody;
}

function matchesJurisdiction(item) {
  return state.jurisdiction === "Todas" || item.jurisdiction === state.jurisdiction;
}

function matchesMonth(item) {
  return state.month === "all" || monthKey(item) === state.month;
}

function filteredItems() {
  const query = normalize(state.query.trim());
  const selected = state.items.filter((item) => {
    const matchesCategory =
      state.category === "Todas" || item.categories.includes(state.category);
    const matchesSource = state.source === "Todas" || item.source === state.source;
    const haystack = normalize(
      [
        item.title,
        item.summary,
        item.official_title,
        item.description,
        item.detail_markdown,
        item.source,
        item.authority,
        item.issuing_body,
        item.document_type,
        item.categories.join(" "),
        (item.topic_tags || []).join(" "),
        (item.subtopic_tags || []).join(" "),
        item.categories.map((category) => searchAliases[category] || "").join(" "),
      ].join(" "),
    );
    return (
      matchesCategory &&
      matchesSource &&
      matchesDateRange(item) &&
      matchesIssuingBody(item) &&
      matchesJurisdiction(item) &&
      matchesMonth(item) &&
      (!query || haystack.includes(query))
    );
  });

  return selected.sort((left, right) => {
    if (state.sort === "relevance") {
      return right.relevance_score - left.relevance_score ||
        right.published_at.localeCompare(left.published_at);
    }
    if (state.sort === "importance") {
      return (right.importance || 0) - (left.importance || 0) ||
        right.published_at.localeCompare(left.published_at);
    }
    return right.published_at.localeCompare(left.published_at) ||
      right.relevance_score - left.relevance_score;
  });
}

function resetToFirstPage() {
  state.page = 1;
}

function renderItems() {
  const filtered = filteredItems();
  const total = filtered.length;
  const pageSize = state.pageSize;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;

  const startIdx = total === 0 ? 0 : (state.page - 1) * pageSize + 1;
  const endIdx = Math.min(state.page * pageSize, total);
  const items = total === 0 ? [] : filtered.slice(startIdx - 1, endIdx);

  elements.list.replaceChildren();
  elements.empty.hidden = total !== 0;
  elements.list.hidden = total === 0;
  elements.pagination.hidden = total === 0;

  elements.count.textContent =
    total === 0
      ? `0 de ${state.items.length} ${state.items.length === 1 ? "documento" : "documentos"}`
      : `Mostrando ${startIdx}–${endIdx} de ${total} ${total === 1 ? "documento" : "documentos"}`;

  elements.feedNote.textContent =
    total === 0
      ? "Ajusta búsqueda, materia, fuente o periodo para ampliar resultados."
      : `Mostrando ${startIdx}–${endIdx} de ${total} coincidencias. Página ${state.page} de ${totalPages}.`;

  elements.pageIndicator.textContent = `Página ${state.page} de ${totalPages}`;
  elements.pagePrev.disabled = state.page <= 1;
  elements.pageNext.disabled = state.page >= totalPages;

  if (total === 0) {
    const hasData = state.items.length !== 0;
    elements.emptyEyebrow.textContent = hasData ? "Sin coincidencias" : "Sin datos";
    elements.emptyTitle.textContent = hasData
      ? "No encontramos documentos con esos filtros."
      : "La actualización no contiene novedades publicables.";
  }

  for (const item of items) {
    const fragment = elements.template.content.cloneNode(true);
    const card = fragment.querySelector(".news-card");
    const badges = fragment.querySelector(".badges");
    const detailUrl = detailUrlFor(item);

    const topicTags = Array.isArray(item.topic_tags) && item.topic_tags.length
      ? item.topic_tags
      : item.categories;
    for (const tag of topicTags.slice(0, 2)) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = tag;
      badges.append(badge);
    }

    const time = fragment.querySelector("time");
    time.dateTime = item.published_at;
    time.textContent = dateFormatter.format(new Date(`${item.published_at}T00:00:00Z`));

    const importanceChip = fragment.querySelector(".importance-chip");
    if (Number(item.importance) >= 4) {
      importanceChip.hidden = false;
    }

    const intlChip = fragment.querySelector(".intl-chip");
    if (item.jurisdiction === "internacional" && item.country_or_org) {
      intlChip.textContent = item.country_or_org;
      intlChip.hidden = false;
    }

    fragment.querySelector("h3").textContent = item.title;
    fragment.querySelector(".source-name").textContent = item.source;
    fragment.querySelector(".authority").textContent =
      item.issuing_body || item.authority || "Autoridad no identificada";

    const detailLink = fragment.querySelector(".detail-link");
    detailLink.href = detailUrl;
    detailLink.setAttribute("aria-label", `Ver ficha: ${item.title}`);

    const sourceLink = fragment.querySelector(".source-link-card");
    if (isSafeHttpUrl(item.url)) {
      sourceLink.href = item.url;
      sourceLink.setAttribute("aria-label", `Ver fuente oficial: ${item.official_title}`);
    } else {
      sourceLink.removeAttribute("href");
      sourceLink.setAttribute("aria-disabled", "true");
      sourceLink.classList.add("is-disabled");
    }

    card.dataset.id = item.id;
    card.dataset.detailUrl = detailUrl;
    elements.list.append(fragment);
  }
}

function renderSources() {
  elements.sources.replaceChildren();
  const failed = state.sources.filter((source) => source.status === "error");
  const okCount = state.sources.length - failed.length;
  elements.sourceSummary.textContent =
    state.sources.length === 0
      ? "Estado de fuentes pendiente."
      : `${okCount} de ${state.sources.length} fuentes respondieron correctamente.`;
  elements.sourceAlert.hidden = failed.length === 0;
  elements.sourceAlert.textContent =
    failed.length === 0
      ? ""
      : `Actualización parcial: ${failed.map((source) => source.source).join(", ")} no respondió.`;

  for (const source of state.sources) {
    const attempts = Number(source.attempts || 1);
    const retryText = attempts > 1 ? ` (${attempts} intentos)` : "";
    const container = document.createElement("div");
    container.className = "source-status";
    const dot = document.createElement("span");
    dot.className = `status-dot${source.status === "error" ? " error" : ""}`;
    dot.setAttribute("aria-hidden", "true");

    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = source.source;
    const detail = document.createElement("span");
    detail.textContent =
      source.status === "ok"
        ? `${source.items_found} registros revisados${retryText}`
        : `${source.error || "Consulta temporalmente no disponible"}${retryText}`;
    copy.append(name, detail);
    container.append(dot, copy);
    elements.sources.append(container);
  }
}

function bindControls() {
  elements.list.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("a")) return;
    const card = target.closest(".news-card");
    if (!card?.dataset.detailUrl) return;
    window.location.href = card.dataset.detailUrl;
  });
  elements.filters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    document.querySelectorAll(".filter").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    resetToFirstPage();
    renderItems();
  });
  elements.search.addEventListener("input", (event) => {
    state.query = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.sourceFilter.addEventListener("change", (event) => {
    state.source = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.dateRange.addEventListener("change", (event) => {
    state.dateRange = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.issuingBodyFilter.addEventListener("change", (event) => {
    state.issuingBody = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.jurisdictionFilter.addEventListener("change", (event) => {
    state.jurisdiction = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.monthFilter.addEventListener("change", (event) => {
    state.month = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.sort.addEventListener("change", (event) => {
    state.sort = event.target.value;
    resetToFirstPage();
    renderItems();
  });
  elements.pageSize.addEventListener("change", (event) => {
    state.pageSize = Number(event.target.value) || 24;
    resetToFirstPage();
    renderItems();
  });
  elements.pagePrev.addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    renderItems();
  });
  elements.pageNext.addEventListener("click", () => {
    state.page += 1;
    renderItems();
  });
  elements.clear.addEventListener("click", () => {
    state.query = "";
    state.category = "Todas";
    state.source = "Todas";
    state.dateRange = "all";
    state.issuingBody = "Todas";
    state.jurisdiction = "Todas";
    state.month = "all";
    elements.search.value = "";
    elements.sourceFilter.value = "Todas";
    elements.dateRange.value = "all";
    elements.issuingBodyFilter.value = "Todas";
    elements.jurisdictionFilter.value = "Todas";
    elements.monthFilter.value = "all";
    document.querySelectorAll(".filter").forEach((button) => {
      button.classList.toggle("active", button.dataset.category === "Todas");
    });
    resetToFirstPage();
    renderItems();
  });
}

async function loadData() {
  try {
    const response = await fetch("data/publications.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.items = payload.items || [];
    state.sources = payload.sources || [];
    state.generatedAt = new Date(payload.generated_at);
    const generated = new Date(payload.generated_at);
    elements.updated.textContent = Number.isNaN(generated.valueOf())
      ? "Pendiente de primera actualización"
      : `Actualizado ${new Intl.DateTimeFormat("es-MX", {
          dateStyle: "long",
          timeStyle: "short",
        }).format(generated)}`;
    renderFilters();
    renderSourceFilter();
    renderIssuingBodyFilter();
    renderMonthFilter();
    renderItems();
    renderSources();
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = false;
    elements.list.hidden = true;
    elements.pagination.hidden = true;
    elements.emptyEyebrow.textContent = "Error de datos";
    elements.emptyTitle.textContent = "No fue posible cargar la actualización.";
    elements.updated.textContent = "Error al consultar los datos";
    elements.feedNote.textContent = "Revisa que el archivo de datos esté disponible.";
    console.error(error);
  }
}

bindControls();
loadData();
