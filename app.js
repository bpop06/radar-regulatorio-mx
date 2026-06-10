const state = {
  items: [],
  sources: [],
  query: "",
  category: "Todas",
  sort: "date",
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

const elements = {
  list: document.querySelector("#news-list"),
  template: document.querySelector("#news-template"),
  filters: document.querySelector("#category-filters"),
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  empty: document.querySelector("#empty-state"),
  count: document.querySelector("#result-count"),
  updated: document.querySelector("#last-update"),
  sources: document.querySelector("#source-status"),
  clear: document.querySelector("#clear-filters"),
};

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

function renderFilters() {
  const discovered = state.items.flatMap((item) => item.categories);
  const categories = [...new Set([...supportedCategories, ...discovered])];
  for (const category of categories) {
    const button = document.createElement("button");
    button.className = "filter";
    button.type = "button";
    button.dataset.category = category;
    button.textContent = category;
    elements.filters.append(button);
  }

  elements.filters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    document.querySelectorAll(".filter").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderItems();
  });
}

function filteredItems() {
  const query = normalize(state.query.trim());
  const selected = state.items.filter((item) => {
    const matchesCategory =
      state.category === "Todas" || item.categories.includes(state.category);
    const haystack = normalize(
      [
        item.title,
        item.summary,
        item.official_title,
        item.source,
        item.authority,
        item.document_type,
        item.categories.join(" "),
        item.categories.map((category) => searchAliases[category] || "").join(" "),
      ].join(" "),
    );
    return matchesCategory && (!query || haystack.includes(query));
  });

  return selected.sort((left, right) => {
    if (state.sort === "relevance") {
      return right.relevance_score - left.relevance_score ||
        right.published_at.localeCompare(left.published_at);
    }
    return right.published_at.localeCompare(left.published_at) ||
      right.relevance_score - left.relevance_score;
  });
}

function renderItems() {
  const items = filteredItems();
  elements.list.replaceChildren();
  elements.empty.hidden = items.length !== 0;
  elements.list.hidden = items.length === 0;
  elements.count.textContent = `${items.length} ${items.length === 1 ? "documento" : "documentos"}`;

  for (const item of items) {
    const fragment = elements.template.content.cloneNode(true);
    const card = fragment.querySelector(".news-card");
    const badges = fragment.querySelector(".badges");

    for (const category of item.categories.slice(0, 3)) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = category;
      badges.append(badge);
    }

    const time = fragment.querySelector("time");
    time.dateTime = item.published_at;
    time.textContent = dateFormatter.format(new Date(`${item.published_at}T00:00:00Z`));
    fragment.querySelector("h3").textContent = item.title;
    fragment.querySelector(".summary").textContent = item.summary;
    fragment.querySelector(".official-title").textContent =
      `Título oficial: ${item.official_title}`;
    fragment.querySelector(".source-name").textContent = item.source;
    fragment.querySelector(".authority").textContent = item.authority;

    const link = fragment.querySelector(".card-footer a");
    link.href = item.url;
    link.setAttribute("aria-label", `Ver fuente oficial: ${item.official_title}`);
    card.dataset.id = item.id;
    elements.list.append(fragment);
  }
}

function renderSources() {
  elements.sources.replaceChildren();
  for (const source of state.sources) {
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
        ? `${source.items_found} registros revisados`
        : "Consulta temporalmente no disponible";
    copy.append(name, detail);
    container.append(dot, copy);
    elements.sources.append(container);
  }
}

function bindControls() {
  elements.search.addEventListener("input", (event) => {
    state.query = event.target.value;
    renderItems();
  });
  elements.sort.addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderItems();
  });
  elements.clear.addEventListener("click", () => {
    state.query = "";
    state.category = "Todas";
    elements.search.value = "";
    document.querySelectorAll(".filter").forEach((button) => {
      button.classList.toggle("active", button.dataset.category === "Todas");
    });
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
    const generated = new Date(payload.generated_at);
    elements.updated.textContent = Number.isNaN(generated.valueOf())
      ? "Pendiente de primera actualización"
      : `Actualizado ${new Intl.DateTimeFormat("es-MX", {
          dateStyle: "long",
          timeStyle: "short",
        }).format(generated)}`;
    renderFilters();
    renderItems();
    renderSources();
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = false;
    elements.list.hidden = true;
    elements.empty.querySelector("h2").textContent =
      "No fue posible cargar la actualización.";
    elements.updated.textContent = "Error al consultar los datos";
    console.error(error);
  }
}

bindControls();
loadData();
