import { hydrateIcons } from "./icons.js?v=20260813g";

const THEME_COLORS = { light: "#F7F4EC", dark: "#050B16" };
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_INSTANT_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/i;
const MEXICO_CITY_TIME_ZONE = "America/Mexico_City";
const CASE_STATUS_LABELS = {
  Pending: "Pendiente",
  Concluded: "Concluido",
  Active: "Activo",
  Completed: "Concluido",
};

export function isSafeHttpUrl(value) {
  return typeof value === "string" && /^https?:\/\//i.test(value);
}

export function translateCaseStatus(status) {
  const key = typeof status === "string" ? status.trim() : "";
  return CASE_STATUS_LABELS[key] || key;
}

export function normalizeIsoDate(value) {
  if (typeof value !== "string") return null;
  const match = ISO_DATE_PATTERN.exec(value.trim());
  if (!match) return null;
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day) return null;
  return `${yearText}-${monthText}-${dayText}`;
}

export function formatDate(iso, options = {}) {
  const normalized = normalizeIsoDate(typeof iso === "string" ? iso.slice(0, 10) : iso);
  if (!normalized) return "—";
  const [year, month, day] = normalized.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: options.short ? "short" : "long",
    year: options.year === false ? undefined : "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function formatDateTime(value, options = {}) {
  if (typeof value !== "string" || !ISO_INSTANT_PATTERN.test(value.trim())) return "—";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "—";
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: options.short ? "short" : "long",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: MEXICO_CITY_TIME_ZONE,
    timeZoneName: "short",
  }).format(instant).replace("GMT-6", "CDMX").replace("CST", "CDMX");
}

export function datePresentation(value, options = {}) {
  if (options.type === "datetime") {
    if (typeof value !== "string" || !ISO_INSTANT_PATTERN.test(value.trim())) {
      return { dateTime: null, label: "—" };
    }
    const instant = new Date(value);
    if (Number.isNaN(instant.getTime())) return { dateTime: null, label: "—" };
    return {
      dateTime: instant.toISOString(),
      label: formatDateTime(value, options),
    };
  }
  const normalized = normalizeIsoDate(value);
  return {
    dateTime: normalized,
    label: normalized ? formatDate(normalized, options) : "—",
  };
}

export function setTime(element, value, options = {}) {
  if (!element) return;
  const presentation = datePresentation(value, options);
  element.textContent = presentation.label;
  if (presentation.dateTime) element.setAttribute("datetime", presentation.dateTime);
  else element.removeAttribute("datetime");
}

export function mexicoToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Mexico_City",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

export function detailHref(item, from = "archivo") {
  const candidate = typeof item?.detail_url === "string" ? item.detail_url : "";
  const safeDetail = candidate
    && !candidate.startsWith("/")
    && !candidate.includes("..")
    && !/^[a-z][a-z\d+.-]*:/i.test(candidate)
    ? candidate
    : "";
  const base = safeDetail || `ficha.html?id=${encodeURIComponent(item?.id || "")}`;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}from=${encodeURIComponent(from)}`;
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
    } else if (match[3] && match[4] && isSafeHttpUrl(match[4])) {
      const link = document.createElement("a");
      link.href = match[4];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[3];
      const arrow = document.createElement("span");
      arrow.className = "external-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = " ↗";
      link.append(arrow);
      const hint = document.createElement("span");
      hint.className = "sr-only";
      hint.textContent = " (abre en una pestaña nueva)";
      link.append(hint);
      container.append(link);
    } else if (match[3]) {
      container.append(document.createTextNode(match[3]));
    }
    lastIndex = inlinePattern.lastIndex;
    match = inlinePattern.exec(text);
  }
  if (lastIndex < text.length) container.append(document.createTextNode(text.slice(lastIndex)));
}

function splitTableRow(line) {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map((cell) => cell.trim());
}

const tableSeparator = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

export function renderMarkdown(markdown, container) {
  container.replaceChildren();
  const blocks = String(markdown || "").trim().split(/\n{2,}/);
  for (const raw of blocks) {
    const block = raw.trim();
    if (!block) continue;
    const heading = /^(#{1,3})\s+(.+)$/.exec(block);
    if (heading && !heading[2].includes("\n")) {
      const node = document.createElement(`h${heading[1].length}`);
      node.textContent = heading[2].trim();
      container.append(node);
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
    if (block.startsWith(">")) {
      const quote = document.createElement("blockquote");
      appendInline(quote, block.replace(/^>\s?/gm, ""));
      container.append(quote);
      continue;
    }
    const lines = block.split("\n");
    if (lines.length >= 2 && lines[0].trim().startsWith("|") && tableSeparator.test(lines[1])) {
      const wrap = document.createElement("div");
      wrap.className = "md-table-wrap";
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const value of splitTableRow(lines[0])) {
        const cell = document.createElement("th");
        cell.scope = "col";
        appendInline(cell, value);
        headRow.append(cell);
      }
      head.append(headRow);
      table.append(head);
      const body = document.createElement("tbody");
      for (const line of lines.slice(2)) {
        const row = document.createElement("tr");
        for (const value of splitTableRow(line)) {
          const cell = document.createElement("td");
          appendInline(cell, value);
          row.append(cell);
        }
        body.append(row);
      }
      table.append(body);
      wrap.append(table);
      container.append(wrap);
      continue;
    }
    const paragraph = document.createElement("p");
    block.split("\n").forEach((line, index) => {
      if (index) paragraph.append(document.createElement("br"));
      appendInline(paragraph, line);
    });
    container.append(paragraph);
  }
}

export function getSections(markdown) {
  const lines = String(markdown || "").split("\n");
  const sections = [];
  let current = null;
  for (const line of lines) {
    const match = /^##\s+(.+)$/.exec(line);
    if (match) {
      current = { title: match[1].trim(), body: [] };
      sections.push(current);
    } else if (current) {
      current.body.push(line);
    }
  }
  return sections.map((section) => ({
    title: section.title,
    body: section.body.join("\n").trim(),
  }));
}

export function renderSection(section, container) {
  const sectionNode = document.createElement("section");
  sectionNode.className = "document-section";
  const heading = document.createElement("h2");
  heading.textContent = section.title;
  sectionNode.append(heading);
  const body = document.createElement("div");
  body.className = "document-section-body";
  renderMarkdown(section.body, body);
  sectionNode.append(body);
  container.append(sectionNode);
}

function resolveTheme(theme) {
  if (theme === "dark" || theme === "light") return theme;
  return "light";
}

function syncThemeColor(theme) {
  const color = THEME_COLORS[resolveTheme(theme)];
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
    meta.setAttribute("content", color);
  });
}

function initTheme() {
  const button = document.querySelector("#theme-toggle");
  if (!button) return;
  const sync = () => {
    const theme = resolveTheme(document.documentElement.dataset.theme);
    button.setAttribute("aria-label", theme === "dark" ? "Usar tema claro" : "Usar tema oscuro");
    button.setAttribute("aria-pressed", String(theme === "dark"));
    const glyph = button.querySelector(".theme-glyph");
    if (glyph) glyph.textContent = theme === "dark" ? "☀" : "☾";
    syncThemeColor(theme);
  };
  button.addEventListener("click", () => {
    const current = resolveTheme(document.documentElement.dataset.theme);
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("radar-theme", next);
    } catch {
      // El tema funciona durante la sesión aunque el almacenamiento esté bloqueado.
    }
    sync();
  });
  sync();
}

function initNavigation() {
  const filename = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  const page = filename === "archivo.html"
    ? "archive"
    : filename === "calendario.html" || filename === "guardias.html"
      ? "calendar"
      : filename === "index.html" || filename === ""
        ? "today"
        : "detail";
  document.querySelectorAll(".nav-link[data-page]").forEach((link) => {
    const active = link.dataset.page === page;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

if (typeof document !== "undefined") {
  hydrateIcons(document);
  initTheme();
  initNavigation();
}
