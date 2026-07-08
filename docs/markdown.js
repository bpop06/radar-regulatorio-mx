/*
 * Radar Regulatorio MX — runtime compartido (script clásico, sin ESM para file:// compat).
 * Expone window.Radar con: isSafeHttpUrl, renderMarkdown, getSections, renderSection.
 * Además arranca el "chrome" común de las 3 páginas: toggle de tema, nav condensado,
 * estado activo del nav e indicador deslizante. Cero innerHTML (solo textContent/createElement).
 */
(function () {
  "use strict";

  function isSafeHttpUrl(u) {
    return typeof u === "string" && /^https?:\/\//i.test(u);
  }

  // Contrato v4 (opcional) case_status: chip "CASO · …" en tarjeta/ficha (v7 QA #18).
  // Traduce estados conocidos; cualquier otro valor (incluidos los textos largos de
  // integración de paneles arbitrales que a veces llegan en este campo) se deja tal cual.
  const CASE_STATUS_LABELS = {
    Pending: "Pendiente",
    Concluded: "Concluido",
    Active: "Activo",
    Completed: "Concluido",
  };
  function translateCaseStatus(status) {
    const key = typeof status === "string" ? status.trim() : "";
    return CASE_STATUS_LABELS[key] || key;
  }

  // --- Mini-renderer markdown (extraído de detail.js v2) --------------------
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
        const label = document.createElement("span");
        label.textContent = match[3];
        link.append(label);
        const arrow = document.createElement("span");
        arrow.className = "ext-arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "↗";
        link.append(arrow);
        container.append(link);
      } else if (match[3]) {
        // enlace no seguro: degradar a texto plano
        container.append(document.createTextNode(match[3]));
      }
      lastIndex = inlinePattern.lastIndex;
      match = inlinePattern.exec(text);
    }

    if (lastIndex < text.length) {
      container.append(document.createTextNode(text.slice(lastIndex)));
    }
  }

  // Fila de tabla "| a | b | c |" -> celdas ["a","b","c"] (tolera bordes sin "|").
  function splitTableRow(line) {
    let trimmed = line.trim();
    if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
    if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
    return trimmed.split("|").map((cell) => cell.trim());
  }
  const tableSeparatorRe = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

  function renderMarkdown(markdown, container) {
    container.replaceChildren();
    const blocks = String(markdown || "").trim().split(/\n{2,}/);

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
      if (block.startsWith("### ")) {
        const heading = document.createElement("h3");
        heading.textContent = block.slice(4).trim();
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
      if (block.startsWith(">")) {
        const quote = document.createElement("blockquote");
        block.split("\n").forEach((line, index) => {
          if (index > 0) quote.append(document.createElement("br"));
          appendInline(quote, line.replace(/^>\s?/, ""));
        });
        container.append(quote);
        continue;
      }
      const lines = block.split("\n");
      if (lines.length >= 2 && lines[0].trim().startsWith("|") && tableSeparatorRe.test(lines[1].trim())) {
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headRow = document.createElement("tr");
        for (const cell of splitTableRow(lines[0])) {
          const th = document.createElement("th");
          appendInline(th, cell);
          headRow.append(th);
        }
        thead.append(headRow);
        table.append(thead);
        const tbody = document.createElement("tbody");
        for (const line of lines.slice(2)) {
          if (!line.trim()) continue;
          const tr = document.createElement("tr");
          for (const cell of splitTableRow(line)) {
            const td = document.createElement("td");
            appendInline(td, cell);
            tr.append(td);
          }
          tbody.append(tr);
        }
        table.append(tbody);
        const wrap = document.createElement("div");
        wrap.className = "md-table-wrap";
        wrap.append(table);
        container.append(wrap);
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

  // Divide un cuerpo markdown en secciones por encabezado "## Titulo".
  function getSections(markdown) {
    const lines = String(markdown || "").split("\n");
    const sections = [];
    let current = null;
    for (const line of lines) {
      const m = /^##\s+(.+)$/.exec(line);
      if (m) {
        current = { title: m[1].trim(), body: [] };
        sections.push(current);
      } else if (current) {
        current.body.push(line);
      }
    }
    return sections.map((s) => ({ title: s.title, body: s.body.join("\n").trim() }));
  }

  // Renderiza una sección (label mono + cuerpo) dentro de container.
  function renderSection(section, container) {
    const label = document.createElement("p");
    label.className = "md-label";
    label.textContent = section.title;
    container.append(label);
    const body = document.createElement("div");
    body.className = "md-body";
    renderMarkdown(section.body, body);
    container.append(body);
  }

  // --- Chrome común: tema + nav --------------------------------------------
  // Colores reales de --paper por tema (deben coincidir con los <meta name="theme-color"> del <head>).
  const THEME_COLOR = { light: "#F4F4F1", dark: "#0F1113" };

  function resolveTheme(theme) {
    if (theme === "dark" || theme === "light") return theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  // Los <meta name="theme-color"> con media prefers-color-scheme cubren el estado
  // por defecto (sin JS / sin override). Al togglear manualmente, el navegador sigue
  // decidiendo qué meta usar según el SO, así que sincronizamos el content de AMBOS
  // al color resuelto para que el toggle manual también controle el chrome del navegador.
  function syncThemeColorMeta(theme) {
    const color = THEME_COLOR[resolveTheme(theme)] || THEME_COLOR.light;
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
      meta.setAttribute("content", color);
    });
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "dark" || theme === "light") {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    syncThemeColorMeta(theme);
  }

  function currentTheme() {
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr === "dark" || attr === "light") return attr;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function initThemeToggle() {
    const btn = document.querySelector("#theme-toggle");
    if (!btn) return;
    const sync = () => {
      const isDark = currentTheme() === "dark";
      btn.setAttribute("aria-label", isDark ? "Cambiar a modo claro" : "Cambiar a modo oscuro");
      btn.setAttribute("aria-pressed", String(isDark));
      const glyph = btn.querySelector(".theme-glyph");
      if (glyph) glyph.textContent = isDark ? "☀" : "☽"; // sol / luna
    };
    sync();
    syncThemeColorMeta(currentTheme());
    btn.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      applyTheme(next);
      try { localStorage.setItem("radar-theme", next); } catch (e) { /* noop */ }
      sync();
    });
  }

  function initNav() {
    const nav = document.querySelector(".nav");
    if (!nav) return;

    // Estado condensado al hacer scroll (>16px).
    const onScroll = () => {
      const scrolled = window.scrollY > 16;
      nav.classList.toggle("is-condensed", scrolled);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    // Estado activo por página + indicador deslizante.
    const path = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    const links = Array.from(nav.querySelectorAll(".nav-link"));
    let active = null;
    for (const link of links) {
      const page = (link.getAttribute("data-page") || "").toLowerCase();
      const match =
        (page === "index" && (path === "" || path === "index.html")) ||
        (page === "calendario" && path === "calendario.html") ||
        (page === "ficha" && path === "ficha.html");
      link.classList.toggle("is-active", !!match);
      if (match) active = link;
    }

    const indicator = nav.querySelector(".nav-indicator");
    if (indicator && active) {
      const place = () => {
        const navRect = nav.querySelector(".nav-links").getBoundingClientRect();
        const rect = active.getBoundingClientRect();
        indicator.style.width = rect.width + "px";
        indicator.style.transform = "translateX(" + (rect.left - navRect.left) + "px)";
        indicator.style.opacity = "1";
      };
      place();
      window.addEventListener("resize", place);
      // reflow tras cargar fuentes
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(place).catch(() => {});
    }
  }

  function initChrome() {
    initThemeToggle();
    initNav();
  }

  window.Radar = {
    isSafeHttpUrl: isSafeHttpUrl,
    renderMarkdown: renderMarkdown,
    getSections: getSections,
    renderSection: renderSection,
    appendInline: appendInline,
    applyTheme: applyTheme,
    currentTheme: currentTheme,
    syncThemeColorMeta: syncThemeColorMeta,
    translateCaseStatus: translateCaseStatus,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChrome);
  } else {
    initChrome();
  }
})();
