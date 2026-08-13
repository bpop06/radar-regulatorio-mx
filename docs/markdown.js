import { appendIcon, hydrateIcons } from "./icons.js?v=20260812b";
import { createLiquidMove } from "./motion.js?v=20260812b";

const THEME_COLORS = { light: "#F6F5F1", dark: "#101827" };
const DATE_UNAVAILABLE = "Fecha no disponible";
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
  const candidate = value.trim();
  if (!ISO_DATE_PATTERN.test(candidate)) return null;
  const instant = new Date(`${candidate}T00:00:00.000Z`);
  if (Number.isNaN(instant.getTime()) || instant.toISOString().slice(0, 10) !== candidate) {
    return null;
  }
  return candidate;
}

export function datePresentation(value, options = {}) {
  const type = options.type === "datetime" ? "datetime" : "date";
  if (type === "datetime") {
    const candidate = typeof value === "string" ? value.trim() : "";
    if (!ISO_INSTANT_PATTERN.test(candidate)) {
      return { dateTime: null, label: DATE_UNAVAILABLE };
    }
    const instant = new Date(candidate);
    if (Number.isNaN(instant.getTime())) {
      return { dateTime: null, label: DATE_UNAVAILABLE };
    }
    const label = new Intl.DateTimeFormat("es-MX", {
      dateStyle: options.short ? "medium" : "long",
      timeStyle: "short",
      timeZone: MEXICO_CITY_TIME_ZONE,
    }).format(instant);
    return {
      dateTime: instant.toISOString(),
      label: options.zoneLabel === false ? label : `${label} CDMX`,
    };
  }

  const normalized = normalizeIsoDate(value);
  if (!normalized) return { dateTime: null, label: DATE_UNAVAILABLE };
  const [year, month, day] = normalized.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  return {
    dateTime: normalized,
    label: new Intl.DateTimeFormat("es-MX", {
      day: "numeric",
      month: options.short ? "short" : "long",
      year: options.year === false ? undefined : "numeric",
      timeZone: "UTC",
    }).format(date),
  };
}

export function formatDate(iso, options = {}) {
  return datePresentation(iso, options).label;
}

export function formatDateTime(iso, options = {}) {
  return datePresentation(iso, { ...options, type: "datetime" }).label;
}

export function createTime(value, options = {}, ownerDocument = globalThis.document) {
  if (!ownerDocument || typeof ownerDocument.createElement !== "function") {
    throw new TypeError("createTime requires a document");
  }
  const presentation = datePresentation(value, options);
  const element = ownerDocument.createElement(presentation.dateTime ? "time" : "span");
  element.textContent = presentation.label;
  if (presentation.dateTime) element.setAttribute("datetime", presentation.dateTime);
  return element;
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
  const base = typeof item?.detail_url === "string"
    ? item.detail_url
    : `ficha.html?id=${encodeURIComponent(item?.id || "")}`;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}from=${encodeURIComponent(from)}`;
}

export function parseInline(text) {
  const inlinePattern = /(\*\*([^*\n]+)\*\*)|(\*([^*\n]+)\*)|\[([^\]]+)\]\((https?:\/\/[^)]+)\)/gi;
  const tokens = [];
  let lastIndex = 0;
  let match = inlinePattern.exec(text);
  while (match) {
    if (match.index > lastIndex) {
      tokens.push({ type: "text", value: text.slice(lastIndex, match.index) });
    }
    if (match[2]) {
      tokens.push({ type: "strong", value: match[2] });
    } else if (match[4]) {
      tokens.push({ type: "emphasis", value: match[4] });
    } else if (match[5] && match[6] && isSafeHttpUrl(match[6])) {
      tokens.push({ type: "link", value: match[5], url: match[6] });
    }
    lastIndex = inlinePattern.lastIndex;
    match = inlinePattern.exec(text);
  }
  if (lastIndex < text.length) tokens.push({ type: "text", value: text.slice(lastIndex) });
  return tokens;
}

function appendInline(container, text) {
  for (const token of parseInline(text)) {
    if (token.type === "strong") {
      const strong = document.createElement("strong");
      strong.textContent = token.value;
      container.append(strong);
    } else if (token.type === "emphasis") {
      const emphasis = document.createElement("em");
      emphasis.textContent = token.value;
      container.append(emphasis);
    } else if (token.type === "link") {
      const link = document.createElement("a");
      link.href = token.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = token.value;
      appendIcon(link, "external", { className: "rr-icon external-arrow" });
      const notice = document.createElement("span");
      notice.className = "sr-only";
      notice.textContent = " (se abre en una pestaña nueva)";
      link.append(notice);
      container.append(link);
    } else {
      container.append(document.createTextNode(token.value));
    }
  }
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
    if (/^(-{3,}|\*{3,})$/.test(block)) {
      container.append(document.createElement("hr"));
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
    if (/^\d+\.\s/.test(block)) {
      const list = document.createElement("ol");
      for (const line of block.split("\n")) {
        const itemMatch = /^\d+\.\s+(.+)$/.exec(line);
        if (!itemMatch) continue;
        const item = document.createElement("li");
        appendInline(item, itemMatch[1]);
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
    const label = theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.dataset.themeState = theme;
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
  const navigation = document.querySelector(".nav-links[data-liquid-group]");
  if (navigation) {
    const liquid = createLiquidMove(navigation, { activeSelector: ".nav-link.is-active" });
    navigation.addEventListener("pointerover", (event) => {
      const link = event.target.closest?.(".nav-link");
      if (link) liquid.moveTo(link);
    });
    navigation.addEventListener("pointerleave", () => liquid.sync());
    navigation.addEventListener("focusin", (event) => {
      const link = event.target.closest?.(".nav-link");
      if (link) liquid.moveTo(link);
    });
    navigation.addEventListener("focusout", (event) => {
      if (!navigation.contains(event.relatedTarget)) liquid.sync();
    });
  }
}

if (typeof document !== "undefined") {
  hydrateIcons(document);
  initTheme();
  initNavigation();
}
