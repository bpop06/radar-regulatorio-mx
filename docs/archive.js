import {
  displayTitle,
  editorialLabel,
  loadArchive,
  loadArchiveMonth,
  officialDate,
  stableDetailHref,
  teaser,
} from "./data-client.js";
import { formatDate, setTime } from "./markdown.js?v=20260813g";

const ARCHIVE_WATERCOLORS = ["#54789b", "#667c61", "#94691f", "#8c5964", "#756a8b"];
const ARCHIVE_TILTS = [-.6, .45, -.25, .7, -.4, .3];
const PAGE_SIZE = 25;

const state = {
  items: [], sources: [], query: "", days: "all", category: "Todas", organ: "Todas",
  source: "Todas", jurisdiction: "Todas", sort: "date", exactDate: "", page: 1,
  manifest: null, remainingPaths: [],
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
  error: document.querySelector("#archive-error"),
  pagination: document.querySelector("#pagination"),
  prev: document.querySelector("#page-prev"),
  next: document.querySelector("#page-next"),
  pageIndicator: document.querySelector("#page-indicator"),
  sourceStatus: document.querySelector("#source-status"),
  monthStatus: document.querySelector("#archive-month-status"),
  loadMore: document.querySelector("#load-more-months"),
  from: document.querySelector("#archive-from"),
  to: document.querySelector("#archive-to"),
  updated: document.querySelector("#archive-updated"),
};

function updateDatasetFacts() {
  const dates = state.items.map((item) => officialDate(item)).filter(Boolean).sort();
  setTime(elements.from, dates[0]);
  setTime(elements.to, dates.at(-1));
  setTime(elements.updated, state.manifest?.generated_at, { type: "datetime", short: true });
}

function normalize(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function identity(item) {
  return `${item.source || ""}:${item.source_id || item.id || item.canonical_url || item.url || ""}`;
}

function uniqueItems(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = identity(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function populateSelect(select, values, allLabel) {
  select.replaceChildren(new Option(allLabel, "Todas"));
  values.forEach((value) => select.append(new Option(value, value)));
}

function populateFilters() {
  populateSelect(elements.category, [...new Set(state.items.flatMap((item) => item.categories || []))]
    .sort((a, b) => a.localeCompare(b, "es")), "Todas");
  populateSelect(elements.organ, [...new Set(state.items.map((item) => item.issuing_body || item.authority).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "es")), "Todos");
  populateSelect(elements.source, [...new Set(state.items.map((item) => item.source).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "es")), "Todas");
  syncControls();
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
  return state.items.map(officialDate).filter(Boolean).sort().reverse()[0] || "";
}

function matchesPeriod(item) {
  const itemDate = officialDate(item);
  if (state.exactDate) return itemDate.slice(0, 10) === state.exactDate;
  if (state.days === "all") return true;
  const anchor = new Date(`${anchorDate().slice(0, 10)}T12:00:00Z`);
  const current = new Date(`${itemDate.slice(0, 10)}T12:00:00Z`);
  const delta = (anchor - current) / 86400000;
  return Number.isFinite(delta) && delta >= 0 && delta < Number(state.days);
}

function filteredItems() {
  const query = normalize(state.query);
  const selected = state.items.filter((item) => {
    const haystack = normalize([
      displayTitle(item), item.summary_teaser, item.summary, item.description, item.review_reason,
      item.official_title, item.issuing_body, item.authority, ...(item.categories || []),
      ...(item.topic_tags || []),
    ].join(" "));
    return (!query || haystack.includes(query))
      && matchesPeriod(item)
      && (state.category === "Todas" || (item.categories || []).includes(state.category))
      && (state.organ === "Todas" || (item.issuing_body || item.authority) === state.organ)
      && (state.source === "Todas" || item.source === state.source)
      && (state.jurisdiction === "Todas" || item.jurisdiction === state.jurisdiction);
  });
  return selected.sort((left, right) => {
    if (state.sort === "importance") {
      return (Number(right.importance) - Number(left.importance))
        || officialDate(right).localeCompare(officialDate(left));
    }
    if (state.sort === "relevance") {
      return (Number(right.relevance_score) - Number(left.relevance_score))
        || officialDate(right).localeCompare(officialDate(left));
    }
    return officialDate(right).localeCompare(officialDate(left))
      || (Number(right.importance) - Number(left.importance));
  });
}

function importanceMeter(value) {
  const meter = document.createElement("span");
  const importance = Math.max(0, Math.min(5, Number(value) || 0));
  meter.className = "inline-importance";
  meter.setAttribute("role", "img");
  meter.setAttribute("aria-label", `Importancia ${importance} de 5`);
  for (let index = 1; index <= 5; index += 1) {
    const bar = document.createElement("span");
    if (index <= importance) bar.classList.add("is-on");
    meter.append(bar);
  }
  return meter;
}

function buildRow(item, absoluteIndex) {
  const pending = item.editorial_status === "needs_review";
  const row = document.createElement("li");
  row.className = "archive-row is-revealed";
  row.style.setProperty("--wash", ARCHIVE_WATERCOLORS[absoluteIndex % ARCHIVE_WATERCOLORS.length]);
  row.style.setProperty("--card-tilt", `${ARCHIVE_TILTS[absoluteIndex % ARCHIVE_TILTS.length]}deg`);
  row.dataset.importance = String(Math.max(0, Math.min(5, Number(item.importance) || 0)));
  const number = document.createElement("span");
  number.className = "archive-row-number";
  number.textContent = String(absoluteIndex + 1).padStart(3, "0");

  const body = document.createElement("article");
  const meta = document.createElement("div");
  meta.className = "archive-row-meta";
  const date = document.createElement("time");
  date.dateTime = officialDate(item);
  date.textContent = `${item.official_published_at ? "Fecha oficial" : "Fecha publicada"}: ${formatDate(officialDate(item), { short: true })}`;
  const organ = document.createElement("span");
  organ.textContent = item.issuing_body || item.authority || item.source;
  const status = document.createElement("span");
  status.className = `editorial-badge ${pending ? "is-pending" : "is-complete"}`;
  status.textContent = editorialLabel(item);
  meta.append(date, organ, status, importanceMeter(item.importance));

  const title = document.createElement("h3");
  const link = document.createElement("a");
  link.href = stableDetailHref(item, "archivo");
  link.textContent = displayTitle(item);
  title.append(link);
  const summary = document.createElement("p");
  summary.textContent = teaser(item) || "Consulta la evidencia oficial en la ficha.";
  const taxonomy = document.createElement("p");
  taxonomy.className = "archive-taxonomy";
  taxonomy.textContent = [...(item.categories || []), item.jurisdiction].filter(Boolean).join(" · ");
  body.append(meta, title, summary, taxonomy);

  const action = document.createElement("a");
  action.className = "archive-row-action";
  action.href = stableDetailHref(item, "archivo");
  action.textContent = pending ? "Revisar evidencia" : "Abrir ficha";
  row.append(number, body, action);
  return row;
}

function renderActiveQuery() {
  const labels = [];
  if (state.exactDate) labels.push(`Fecha oficial: ${formatDate(state.exactDate)}`);
  else if (state.days !== "all") labels.push(`Últimos ${state.days} días`);
  if (state.query) labels.push(`“${state.query}”`);
  if (state.category !== "Todas") labels.push(state.category);
  if (state.organ !== "Todas") labels.push(state.organ);
  if (state.source !== "Todas") labels.push(state.source);
  if (state.jurisdiction !== "Todas") labels.push(state.jurisdiction);
  elements.activeQuery.hidden = labels.length === 0;
  elements.activeQueryCopy.textContent = labels.join(" · ");
}

function render() {
  elements.list.setAttribute("aria-busy", "true");
  const filtered = filteredItems();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * PAGE_SIZE;
  const page = filtered.slice(start, start + PAGE_SIZE);
  elements.list.replaceChildren(...page.map((item, index) => buildRow(item, start + index)));
  elements.count.textContent = filtered.length === 1 ? "1 publicación" : `${filtered.length} publicaciones`;
  elements.empty.hidden = filtered.length !== 0;
  elements.error.hidden = true;
  elements.pagination.hidden = filtered.length <= PAGE_SIZE;
  elements.pageIndicator.textContent = `Página ${state.page} de ${totalPages}`;
  elements.prev.disabled = state.page <= 1;
  elements.next.disabled = state.page >= totalPages;
  renderActiveQuery();
  updateUrl();
  elements.list.removeAttribute("aria-busy");
}

function renderSources() {
  elements.sourceStatus.replaceChildren();
  for (const source of state.sources) {
    const degraded = !["ok", "success"].includes(source.status);
    const row = document.createElement("div");
    row.className = `source-status-row ${degraded ? "is-error" : ""}`;
    const name = document.createElement("strong");
    name.textContent = source.source || source.name || "Fuente oficial";
    const status = document.createElement("span");
    status.textContent = degraded
      ? `Cobertura degradada · ${source.error || source.message || "respuesta no verificable"}`
      : `${source.items_found || 0} registros revisados · Operativa`;
    row.append(name, status);
    elements.sourceStatus.append(row);
  }
  if (!state.sources.length) {
    const note = document.createElement("p");
    note.textContent = "El estado de las fuentes está disponible en el corte vigente.";
    elements.sourceStatus.append(note);
  }
}

function resetPageAndRender() {
  state.page = 1;
  render();
}

function bind() {
  const mobileFilters = matchMedia("(max-width: 47.5rem)");
  if (mobileFilters.matches) elements.filterDrawer.open = false;
  mobileFilters.addEventListener("change", () => {
    if (!mobileFilters.matches) elements.filterDrawer.open = true;
  });
  elements.search.addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    state.exactDate = "";
    resetPageAndRender();
  });
  [[elements.days, "days"], [elements.category, "category"], [elements.organ, "organ"],
    [elements.source, "source"], [elements.jurisdiction, "jurisdiction"], [elements.sort, "sort"]]
    .forEach(([control, key]) => control.addEventListener("change", (event) => {
      state[key] = event.target.value;
      if (key === "days") state.exactDate = "";
      resetPageAndRender();
    }));
  elements.clear.addEventListener("click", () => {
    Object.assign(state, { query: "", days: "all", category: "Todas", organ: "Todas",
      source: "Todas", jurisdiction: "Todas", sort: "date", exactDate: "", page: 1 });
    syncControls();
    if (mobileFilters.matches) elements.filterDrawer.open = false;
    render();
    elements.search.focus({ preventScroll: true });
  });
  elements.prev.addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    render();
    document.querySelector("#results-heading").focus();
  });
  elements.next.addEventListener("click", () => {
    state.page += 1;
    render();
    document.querySelector("#results-heading").focus();
  });
  elements.loadMore.addEventListener("click", async () => {
    const path = state.remainingPaths.shift();
    if (!path) return;
    elements.loadMore.disabled = true;
    elements.monthStatus.textContent = "Cargando el mes anterior…";
    try {
      state.items = uniqueItems([...state.items, ...await loadArchiveMonth(path, state.manifest)]);
      updateDatasetFacts();
      populateFilters();
      render();
      elements.monthStatus.textContent = state.remainingPaths.length
        ? `${state.items.length} publicaciones cargadas; quedan ${state.remainingPaths.length} meses por consultar.`
        : "Archivo completo cargado.";
    } catch (error) {
      state.remainingPaths.unshift(path);
      elements.monthStatus.textContent = "No fue posible cargar el mes anterior. Intenta de nuevo.";
      console.error(error);
    } finally {
      elements.loadMore.disabled = false;
      elements.loadMore.hidden = state.remainingPaths.length === 0;
    }
  });
}

async function init() {
  loadUrlState();
  bind();
  elements.list.setAttribute("aria-busy", "true");
  try {
    const requestedMonth = /^\d{4}-\d{2}-\d{2}$/.test(state.exactDate)
      ? state.exactDate.slice(0, 7) : "";
    const payload = await loadArchive({ month: requestedMonth });
    state.manifest = payload.manifest;
    state.items = uniqueItems(payload.items);
    state.sources = payload.sources;
    state.remainingPaths = payload.remainingPaths || [];
    updateDatasetFacts();
    populateFilters();
    elements.loadMore.hidden = state.remainingPaths.length === 0;
    elements.monthStatus.textContent = state.remainingPaths.length
      ? `${requestedMonth ? `Mostrando ${requestedMonth}.` : "Mostrando el mes más reciente."} Hay ${state.remainingPaths.length} meses adicionales disponibles.`
      : "Archivo disponible completo.";
    renderSources();
    render();
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = true;
    elements.error.hidden = false;
    elements.count.textContent = "Datos no disponibles";
    setTime(elements.from, null);
    setTime(elements.to, null);
    setTime(elements.updated, null, { type: "datetime" });
    console.error(error);
  } finally {
    elements.list.removeAttribute("aria-busy");
  }
}

init();
