const state = {
  items: [],
  sources: [],
  generatedAt: null,
  generatedISO: null,
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
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

const monthShort = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];

const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const elements = {
  list: document.querySelector("#news-list"),
  template: document.querySelector("#news-template"),
  filters: document.querySelector("#category-filters"),
  filterIndicator: document.querySelector(".filter-indicator"),
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
  feedNote: document.querySelector("#feed-note"),
  sourceAlert: document.querySelector("#source-alert"),
  sourceSummary: document.querySelector("#source-summary"),
  sources: document.querySelector("#source-status"),
  clear: document.querySelector("#clear-filters"),
  readingDate: document.querySelector("#reading-date"),
  readingCount: document.querySelector("#reading-count"),
  readingOrgans: document.querySelector("#reading-organs"),
  hero: document.querySelector(".hero"),
};

const dayInMilliseconds = 24 * 60 * 60 * 1000;

function normalize(value) {
  return value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function isSafeHttpUrl(u) {
  return typeof u === "string" && /^https?:\/\//i.test(u);
}

function monoDate(iso) {
  if (typeof iso !== "string") return "—";
  const parts = iso.slice(0, 10).split("-").map(Number);
  const [y, m, d] = parts;
  if (!y || !m || !d) return "—";
  return `${String(d).padStart(2, "0")}·${monthShort[m - 1] || "?"}·${y}`;
}

function sourceCode(src) {
  return String(src || "").split(/\s+/)[0].toUpperCase();
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

/* ---------------------------------------------------------------- motion */
const revealObserver =
  "IntersectionObserver" in window
    ? new IntersectionObserver(
        (entries, obs) => {
          for (const entry of entries) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              obs.unobserve(entry.target);
            }
          }
        },
        { threshold: 0.15, rootMargin: "0px 0px -10% 0px" },
      )
    : null;

function observeReveal(el) {
  if (RM || !revealObserver) {
    el.classList.add("is-visible");
    return;
  }
  revealObserver.observe(el);
}

function countUp(el, target, format) {
  const fmt = format || ((v) => String(v));
  if (RM || !el) {
    if (el) el.textContent = fmt(target);
    return;
  }
  const duration = 900;
  const start = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 4); // aproxima ease-out-expo
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    el.textContent = fmt(Math.round(ease(t) * target));
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function placeFilterIndicator() {
  const indicator = elements.filterIndicator;
  if (!indicator) return;
  const active = elements.filters.querySelector(".filter.active");
  if (!active) {
    indicator.style.opacity = "0";
    return;
  }
  const parentRect = elements.filters.getBoundingClientRect();
  const rect = active.getBoundingClientRect();
  const left = rect.left - parentRect.left + elements.filters.scrollLeft;
  indicator.style.width = `${rect.width}px`;
  indicator.style.transform = `translateX(${left}px)`;
  indicator.style.opacity = "1";
}

/* ---------------------------------------------------------------- filters UI */
function renderFilters() {
  const indicator = elements.filterIndicator;
  elements.filters.replaceChildren();
  if (indicator) elements.filters.append(indicator);

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
  requestAnimationFrame(placeFilterIndicator);
}

function renderSourceFilter() {
  const selected = state.source;
  const sources = [...new Set(state.items.map((item) => item.source))].sort((left, right) =>
    left.localeCompare(right, "es"),
  );
  elements.sourceFilter.replaceChildren(new Option("Todas", "Todas"));
  for (const source of sources) elements.sourceFilter.append(new Option(source, source));
  state.source = sources.includes(selected) ? selected : "Todas";
  elements.sourceFilter.value = state.source;
}

function renderIssuingBodyFilter() {
  const selected = state.issuingBody;
  const bodies = [...new Set(state.items.map((item) => item.issuing_body).filter(Boolean))].sort(
    (left, right) => left.localeCompare(right, "es"),
  );
  elements.issuingBodyFilter.replaceChildren(new Option("Todas", "Todas"));
  for (const body of bodies) elements.issuingBodyFilter.append(new Option(body, body));
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

/* ---------------------------------------------------------------- filtering (v2 logic intacta) */
function activeAnchorDate() {
  if (state.generatedAt && !Number.isNaN(state.generatedAt.valueOf())) return state.generatedAt;
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
    const matchesCategory = state.category === "Todas" || item.categories.includes(state.category);
    const matchesSource = state.source === "Todas" || item.source === state.source;
    const haystack = normalize(
      [
        item.title, item.summary, item.official_title, item.description, item.detail_markdown,
        item.source, item.authority, item.issuing_body, item.document_type,
        item.categories.join(" "),
        (item.topic_tags || []).join(" "),
        (item.subtopic_tags || []).join(" "),
        item.categories.map((category) => searchAliases[category] || "").join(" "),
      ].join(" "),
    );
    return (
      matchesCategory && matchesSource && matchesDateRange(item) && matchesIssuingBody(item) &&
      matchesJurisdiction(item) && matchesMonth(item) && (!query || haystack.includes(query))
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

/* ---------------------------------------------------------------- card render */
function buildImportanceBar(container, importance) {
  container.replaceChildren();
  const n = Math.max(0, Math.min(3, Number(importance) || 0));
  for (let i = 0; i < 3; i++) {
    const seg = document.createElement("span");
    seg.className = i < n ? "seg on" : "seg";
    container.append(seg);
  }
}

function findSection(sections, needle) {
  return sections.find((s) => normalize(s.title).includes(normalize(needle)));
}

function populateCard(fragment, item, indexInPage) {
  const card = fragment.querySelector(".news-card");
  const detailUrl = detailUrlFor(item);

  card.style.setProperty("--i", indexInPage < 8 ? indexInPage : 0);

  fragment.querySelector(".folio").textContent = `№ ${item._folio}`;
  fragment.querySelector(".source-chip").textContent = sourceCode(item.source);

  const intlChip = fragment.querySelector(".intl-chip");
  if (item.jurisdiction === "internacional" && item.country_or_org) {
    intlChip.textContent = item.country_or_org;
    intlChip.hidden = false;
  }

  const time = fragment.querySelector("time");
  time.dateTime = item.published_at;
  time.textContent = monoDate(item.published_at);

  buildImportanceBar(fragment.querySelector(".importance-bar"), item.importance);

  fragment.querySelector(".card-organ").textContent =
    item.issuing_body || item.authority || "Autoridad no identificada";

  fragment.querySelector("h3 .u").textContent = item.title;

  const badges = fragment.querySelector(".badges");
  const topicTags = Array.isArray(item.topic_tags) && item.topic_tags.length
    ? item.topic_tags
    : item.categories;
  for (const tag of topicTags.slice(0, 2)) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = tag;
    badges.append(badge);
  }

  // card_body markdown: "Qué se publicó" siempre visible; "Sustancia"/"Fuente" al expandir.
  const sections = window.Radar ? window.Radar.getSections(item.card_body || "") : [];
  const primaryHost = fragment.querySelector(".card-substance-primary");
  const moreHost = fragment.querySelector(".card-more-inner");
  const expandBtn = fragment.querySelector(".card-expand");

  const qué = findSection(sections, "publicó") || findSection(sections, "publico");
  if (qué && window.Radar) {
    const sec = document.createElement("div");
    sec.className = "md-section";
    window.Radar.renderSection(qué, sec);
    primaryHost.append(sec);
  }
  const rest = sections.filter((s) => s !== qué);
  if (rest.length && window.Radar) {
    for (const s of rest) {
      const sec = document.createElement("div");
      sec.className = "md-section";
      window.Radar.renderSection(s, sec);
      moreHost.append(sec);
    }
  } else {
    expandBtn.hidden = true;
  }

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
  return card;
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

  items.forEach((item, index) => {
    const fragment = elements.template.content.cloneNode(true);
    const card = populateCard(fragment, item, index);
    elements.list.append(fragment);
    observeReveal(card);
  });
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
    name.className = "s-name";
    name.textContent = source.source;
    const detail = document.createElement("span");
    detail.className = "s-detail";
    detail.textContent =
      source.status === "ok"
        ? `${source.items_found} registros revisados${retryText}`
        : `${source.error || "Consulta temporalmente no disponible"}${retryText}`;
    copy.append(name, detail);

    const count = document.createElement("span");
    count.className = "s-count tabular";
    count.textContent = source.status === "ok" ? String(source.items_found) : "—";

    container.append(dot, copy, count);
    elements.sources.append(container);
  }
}

/* ---------------------------------------------------------------- controls */
function bindControls() {
  elements.list.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const expandBtn = target.closest(".card-expand");
    if (expandBtn) {
      const card = expandBtn.closest(".news-card");
      const more = card.querySelector(".card-more");
      const open = expandBtn.getAttribute("aria-expanded") === "true";
      expandBtn.setAttribute("aria-expanded", String(!open));
      more.classList.toggle("open", !open);
      return;
    }
    if (target.closest("a")) return;
    if (target.closest("button")) return;
    const card = target.closest(".news-card");
    if (!card?.dataset.detailUrl) return;
    window.location.href = card.dataset.detailUrl;
  });

  elements.filters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    elements.filters.querySelectorAll(".filter").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    placeFilterIndicator();
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
    elements.filters.querySelectorAll(".filter").forEach((button) => {
      button.classList.toggle("active", button.dataset.category === "Todas");
    });
    placeFilterIndicator();
    resetToFirstPage();
    renderItems();
  });

  window.addEventListener("resize", placeFilterIndicator);
}

/* ---------------------------------------------------------------- readings */
function updateOrganReading() {
  const setOrgans = (n) => countUp(elements.readingOrgans, n);
  fetch("data/calendars.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((payload) => {
      const n = Array.isArray(payload.organs) ? payload.organs.length : 0;
      setOrgans(n || fallbackOrganCount());
    })
    .catch(() => setOrgans(fallbackOrganCount()));
}
function fallbackOrganCount() {
  return new Set(state.items.map((i) => i.issuing_body).filter(Boolean)).size;
}

/* ---------------------------------------------------------------- load */
async function loadData() {
  try {
    const response = await fetch("data/publications.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.items = payload.items || [];
    state.sources = payload.sources || [];
    state.generatedAt = new Date(payload.generated_at);
    state.generatedISO = typeof payload.generated_at === "string" ? payload.generated_at : null;

    state.items.forEach((item, idx) => {
      item._folio = String(idx + 1).padStart(3, "0");
    });

    elements.readingDate.textContent = monoDate(state.generatedISO);
    countUp(elements.readingCount, Number(payload.total_items || state.items.length));
    updateOrganReading();

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
    elements.feedNote.textContent = "Revisa que el archivo de datos esté disponible.";
    elements.readingDate.textContent = "ERROR";
    console.error(error);
  }
}

function initHero() {
  if (!elements.hero) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => elements.hero.classList.add("is-loaded"));
  });
}

// Reveals estáticos (meridianas, encabezados de sección)
document.querySelectorAll("[data-reveal]").forEach((el) => {
  if (!el.closest("#news-list")) observeReveal(el);
});

initHero();
bindControls();
loadData();
