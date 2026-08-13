import { appendIcon } from "./icons.js?v=20260812b";
import { datePresentation, detailHref } from "./markdown.js?v=20260812b";

const PAGE_SIZE = 25;
const state = {
  items: [],
  sources: [],
  query: "",
  days: "all",
  category: "Todas",
  organ: "Todas",
  source: "Todas",
  jurisdiction: "Todas",
  sort: "date",
  exactDate: "",
  page: 1,
  generatedAt: "",
};

const elements = {
  search: document.querySelector("#search"),
  days: document.querySelector("#date-range"),
  category: document.querySelector("#category-filter"),
  organ: document.querySelector("#organ-filter"),
  source: document.querySelector("#source-filter"),
  jurisdiction: document.querySelector("#jurisdiction-filter"),
  sort: document.querySelector("#sort"),
  activeQuery: document.querySelector("#active-query"),
  activeQueryCopy: document.querySelector("#active-query-copy"),
  clear: document.querySelector("#clear-filters"),
  filterDrawer: document.querySelector("#filter-drawer"),
  count: document.querySelector("#result-count"),
  list: document.querySelector("#archive-list"),
  empty: document.querySelector("#archive-empty"),
  retry: document.querySelector("#retry-archive"),
  pagination: document.querySelector("#pagination"),
  prev: document.querySelector("#page-prev"),
  next: document.querySelector("#page-next"),
  pageIndicator: document.querySelector("#page-indicator"),
  sourceStatus: document.querySelector("#source-status"),
  from: document.querySelector("#archive-from"),
  to: document.querySelector("#archive-to"),
  updated: document.querySelector("#archive-updated"),
};

let initialRevealComplete = false;

function setTime(element, value, options = {}) {
  const presentation = datePresentation(value, options);
  element.textContent = presentation.label;
  if (presentation.dateTime) element.setAttribute("datetime", presentation.dateTime);
  else element.removeAttribute("datetime");
}

function normalize(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function populateSelect(select, values, allLabel) {
  select.replaceChildren(new Option(allLabel, "Todas"));
  values.forEach((value) => select.append(new Option(value, value)));
}

function loadUrlState() {
  const params = new URLSearchParams(location.search);
  state.query = params.get("q") || "";
  state.days = params.get("dias") || "all";
  state.category = params.get("materia") || "Todas";
  state.organ = params.get("organo") || "Todas";
  state.source = params.get("fuente") || "Todas";
  state.jurisdiction = params.get("jurisdiccion") || "Todas";
  state.sort = params.get("orden") || "date";
  state.exactDate = params.get("fecha") || "";
  state.page = Math.max(1, Number(params.get("pagina")) || 1);
}

function syncControls() {
  elements.search.value = state.query;
  elements.days.value = state.days;
  elements.category.value = state.category;
  elements.organ.value = state.organ;
  elements.source.value = state.source;
  elements.jurisdiction.value = state.jurisdiction;
  elements.sort.value = state.sort;
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.days !== "all") params.set("dias", state.days);
  if (state.category !== "Todas") params.set("materia", state.category);
  if (state.organ !== "Todas") params.set("organo", state.organ);
  if (state.source !== "Todas") params.set("fuente", state.source);
  if (state.jurisdiction !== "Todas") params.set("jurisdiccion", state.jurisdiction);
  if (state.sort !== "date") params.set("orden", state.sort);
  if (state.exactDate) params.set("fecha", state.exactDate);
  if (state.page > 1) params.set("pagina", String(state.page));
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
}

function anchorDate() {
  const dates = state.items.map((item) => item.published_at).filter(Boolean).sort().reverse();
  return dates[0] || "";
}

function matchesPeriod(item) {
  if (state.exactDate) return item.published_at === state.exactDate;
  if (state.days === "all") return true;
  const anchor = new Date(`${anchorDate()}T12:00:00Z`);
  const current = new Date(`${item.published_at}T12:00:00Z`);
  const delta = (anchor - current) / 86400000;
  return delta >= 0 && delta < Number(state.days);
}

function filteredItems() {
  const query = normalize(state.query);
  const selected = state.items.filter((item) => {
    const haystack = normalize([
      item.title,
      item.summary,
      item.official_title,
      item.issuing_body,
      item.authority,
      ...(item.categories || []),
      ...(item.topic_tags || []),
    ].join(" "));
    return (!query || haystack.includes(query))
      && matchesPeriod(item)
      && (state.category === "Todas" || (item.categories || []).includes(state.category))
      && (state.organ === "Todas" || item.issuing_body === state.organ)
      && (state.source === "Todas" || item.source === state.source)
      && (state.jurisdiction === "Todas" || item.jurisdiction === state.jurisdiction);
  });
  return selected.sort((left, right) => {
    if (state.sort === "importance") {
      return (Number(right.importance) - Number(left.importance))
        || String(right.published_at).localeCompare(String(left.published_at));
    }
    if (state.sort === "relevance") {
      return (Number(right.relevance_score) - Number(left.relevance_score))
        || String(right.published_at).localeCompare(String(left.published_at));
    }
    return String(right.published_at).localeCompare(String(left.published_at))
      || (Number(right.importance) - Number(left.importance));
  });
}

function importanceMeter(value) {
  const meter = document.createElement("span");
  const importance = Math.max(0, Math.min(5, Number(value) || 0));
  meter.className = "inline-importance";
  meter.setAttribute("aria-label", `Importancia ${importance} de 5`);
  for (let index = 1; index <= 5; index += 1) {
    const bar = document.createElement("span");
    if (index <= importance) bar.classList.add("is-on");
    meter.append(bar);
  }
  return meter;
}

function buildRow(item, absoluteIndex) {
  const row = document.createElement("li");
  row.className = "archive-row";
  row.dataset.importance = String(Math.max(0, Math.min(5, Number(item.importance) || 0)));
  const number = document.createElement("span");
  number.className = "archive-row-number";
  number.textContent = String(absoluteIndex + 1).padStart(3, "0");

  const body = document.createElement("article");
  const meta = document.createElement("div");
  meta.className = "archive-row-meta";
  const date = document.createElement("time");
  const published = datePresentation(item.published_at, { short: true });
  if (published.dateTime) date.dateTime = published.dateTime;
  date.textContent = published.label;
  const organ = document.createElement("span");
  organ.textContent = item.issuing_body || item.authority || item.source;
  meta.append(date, organ, importanceMeter(item.importance));

  const title = document.createElement("h3");
  const link = document.createElement("a");
  link.href = detailHref(item, "archivo");
  link.textContent = item.title || item.official_title || "Publicación oficial";
  title.append(link);
  const summary = document.createElement("p");
  summary.textContent = item.summary || item.description || "Sin síntesis disponible.";
  const taxonomy = document.createElement("p");
  taxonomy.className = "archive-taxonomy";
  taxonomy.textContent = [...(item.categories || []), item.jurisdiction].filter(Boolean).join(" · ");
  body.append(meta, title, summary, taxonomy);

  const action = document.createElement("a");
  action.className = "archive-row-action pressable";
  action.href = detailHref(item, "archivo");
  action.setAttribute("aria-label", `Abrir ficha: ${link.textContent}`);
  appendIcon(action, "file");
  const actionLabel = document.createElement("span");
  actionLabel.textContent = "Abrir ficha";
  action.append(actionLabel);
  row.append(number, body, action);
  return row;
}

function revealArchiveCards(animate = false) {
  const cards = [...elements.list.children];
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!animate || reducedMotion || !("IntersectionObserver" in window)) {
    cards.forEach((card) => card.classList.add("is-revealed"));
    return;
  }
  cards.forEach((card, index) => {
    card.classList.add("paper-reveal");
    card.style.setProperty("--reveal-delay", `${Math.min(index, 5) * 28}ms`);
  });
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-revealed");
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -6%", threshold: .06 });
  cards.forEach((card) => observer.observe(card));
}

function renderActiveQuery() {
  const labels = [];
  if (state.query) labels.push(`“${state.query}”`);
  if (state.category !== "Todas") labels.push(state.category);
  if (state.organ !== "Todas") labels.push(state.organ);
  if (state.source !== "Todas") labels.push(state.source);
  if (state.jurisdiction !== "Todas") labels.push(state.jurisdiction);
  elements.activeQuery.hidden = labels.length === 0 && !state.exactDate;
  elements.activeQueryCopy.replaceChildren();
  if (state.exactDate) {
    elements.activeQueryCopy.append(document.createTextNode("Edición del "));
    const exact = datePresentation(state.exactDate);
    const time = document.createElement("time");
    if (exact.dateTime) time.dateTime = exact.dateTime;
    time.textContent = exact.label;
    elements.activeQueryCopy.append(time);
    if (labels.length) elements.activeQueryCopy.append(document.createTextNode(" · "));
  }
  if (labels.length) elements.activeQueryCopy.append(document.createTextNode(labels.join(" · ")));
}

function render() {
  const filtered = filteredItems();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * PAGE_SIZE;
  const page = filtered.slice(start, start + PAGE_SIZE);
  elements.list.replaceChildren(...page.map((item, index) => buildRow(item, start + index)));
  revealArchiveCards(!initialRevealComplete);
  initialRevealComplete = true;
  elements.count.textContent = filtered.length === 1 ? "1 publicación" : `${filtered.length} publicaciones`;
  elements.empty.hidden = filtered.length !== 0;
  elements.retry.hidden = true;
  elements.pagination.hidden = filtered.length <= PAGE_SIZE;
  elements.pageIndicator.textContent = `Página ${state.page} de ${totalPages}`;
  elements.prev.disabled = state.page <= 1;
  elements.next.disabled = state.page >= totalPages;
  renderActiveQuery();
  updateUrl();
}

function renderSources() {
  elements.sourceStatus.replaceChildren();
  for (const source of state.sources) {
    const row = document.createElement("div");
    row.className = `source-status-row ${source.status === "error" ? "is-error" : ""}`;
    const name = document.createElement("strong");
    name.textContent = source.source || "Fuente";
    const status = document.createElement("span");
    status.textContent = source.status === "error"
      ? `Incidencia · ${source.error || "sin detalle"}`
      : `${source.items_found || 0} registros revisados`;
    row.append(name, status);
    elements.sourceStatus.append(row);
  }
}

function resetPageAndRender() {
  state.page = 1;
  render();
}

function bind() {
  const mobileFilters = matchMedia("(max-width: 760px)");
  const syncDrawer = () => {
    if (!mobileFilters.matches) elements.filterDrawer.open = true;
  };
  if (mobileFilters.matches) elements.filterDrawer.open = false;
  mobileFilters.addEventListener("change", syncDrawer);
  elements.search.addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    state.exactDate = "";
    resetPageAndRender();
  });
  const controls = [
    [elements.days, "days"],
    [elements.category, "category"],
    [elements.organ, "organ"],
    [elements.source, "source"],
    [elements.jurisdiction, "jurisdiction"],
    [elements.sort, "sort"],
  ];
  controls.forEach(([control, key]) => control.addEventListener("change", (event) => {
    state[key] = event.target.value;
    if (key === "days") state.exactDate = "";
    resetPageAndRender();
  }));
  elements.clear.addEventListener("click", () => {
    Object.assign(state, {
      query: "", days: "all", category: "Todas", organ: "Todas",
      source: "Todas", jurisdiction: "Todas", sort: "date", exactDate: "", page: 1,
    });
    syncControls();
    if (mobileFilters.matches) elements.filterDrawer.open = false;
    render();
  });
  elements.retry.addEventListener("click", () => location.reload());
  elements.prev.addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    render();
    document.querySelector("#results-heading").scrollIntoView();
  });
  elements.next.addEventListener("click", () => {
    state.page += 1;
    render();
    document.querySelector("#results-heading").scrollIntoView();
  });
}

async function init() {
  loadUrlState();
  bind();
  try {
    const response = await fetch("data/publications.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.items = Array.isArray(payload.items) ? payload.items : [];
    state.sources = Array.isArray(payload.sources) ? payload.sources : [];
    state.generatedAt = payload.generated_at || "";
    const dates = state.items.map((item) => item.published_at).filter(Boolean).sort();
    setTime(elements.from, dates[0]);
    setTime(elements.to, dates.at(-1));
    setTime(elements.updated, state.generatedAt, { type: "datetime", short: true });
    populateSelect(
      elements.category,
      [...new Set(state.items.flatMap((item) => item.categories || []))].sort((a, b) => a.localeCompare(b, "es")),
      "Todas",
    );
    populateSelect(
      elements.organ,
      [...new Set(state.items.map((item) => item.issuing_body).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es")),
      "Todos",
    );
    populateSelect(
      elements.source,
      [...new Set(state.items.map((item) => item.source).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es")),
      "Todas",
    );
    syncControls();
    renderSources();
    render();
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = false;
    elements.empty.querySelector("h3").textContent = "No fue posible cargar el archivo.";
    elements.empty.querySelector("p").textContent = "Comprueba tu conexión y vuelve a intentar la carga.";
    elements.retry.hidden = false;
    elements.count.textContent = "Datos no disponibles";
    setTime(elements.from, null);
    setTime(elements.to, null);
    setTime(elements.updated, null, { type: "datetime" });
    console.error(error);
  }
}

init();
