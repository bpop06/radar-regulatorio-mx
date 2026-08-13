const SVG_NS = "http://www.w3.org/2000/svg";

const node = (tag, attributes) => Object.freeze({
  tag,
  attributes: Object.freeze({ ...attributes }),
});

const ICON_DEFINITIONS = Object.freeze({
  today: Object.freeze([
    node("rect", { x: "3", y: "4.5", width: "18", height: "16.5", rx: "2.5", class: "rr-icon-fill" }),
    node("path", { d: "M8 2.5v4M16 2.5v4M3 9h18" }),
    node("circle", { cx: "12", cy: "14.5", r: "2.5", class: "rr-icon-fill" }),
  ]),
  archive: Object.freeze([
    node("path", { d: "M4 8.5h16v10A2.5 2.5 0 0 1 17.5 21h-11A2.5 2.5 0 0 1 4 18.5v-10Z", class: "rr-icon-fill" }),
    node("path", { d: "M3 4.5A1.5 1.5 0 0 1 4.5 3h15A1.5 1.5 0 0 1 21 4.5v2A1.5 1.5 0 0 1 19.5 8h-15A1.5 1.5 0 0 1 3 6.5v-2ZM9 12h6" }),
  ]),
  calendar: Object.freeze([
    node("rect", { x: "3", y: "4.5", width: "18", height: "16.5", rx: "2.5", class: "rr-icon-fill" }),
    node("path", { d: "M8 2.5v4M16 2.5v4M3 9h18M7.5 13h.01M12 13h.01M16.5 13h.01M7.5 17h.01M12 17h.01M16.5 17h.01" }),
  ]),
  sun: Object.freeze([
    node("circle", { cx: "12", cy: "12", r: "4", class: "rr-icon-fill" }),
    node("path", { d: "M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" }),
  ]),
  moon: Object.freeze([
    node("path", { d: "M20.4 15.1A8.7 8.7 0 0 1 8.9 3.6 9 9 0 1 0 20.4 15.1Z", class: "rr-icon-fill" }),
  ]),
  date: Object.freeze([
    node("rect", { x: "3", y: "4.5", width: "18", height: "16.5", rx: "2.5" }),
    node("path", { d: "M8 2.5v4M16 2.5v4M3 9h18M7.5 13h4M7.5 17h9" }),
  ]),
  file: Object.freeze([
    node("path", { d: "M6 2.5h8l4 4V21H6V2.5Z", class: "rr-icon-fill" }),
    node("path", { d: "M14 2.5v4h4M9 11h6M9 15h6" }),
  ]),
  external: Object.freeze([
    node("path", { d: "M14 4h6v6M20 4l-9 9" }),
    node("path", { d: "M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" }),
  ]),
  "arrow-back": Object.freeze([
    node("path", { d: "M19 12H5M11 18l-6-6 6-6" }),
  ]),
  "arrow-forward": Object.freeze([
    node("path", { d: "M5 12h14M13 6l6 6-6 6" }),
  ]),
  "chevron-back": Object.freeze([
    node("path", { d: "m15 18-6-6 6-6" }),
  ]),
  "chevron-forward": Object.freeze([
    node("path", { d: "m9 18 6-6-6-6" }),
  ]),
  "chevron-down": Object.freeze([
    node("path", { d: "m6 9 6 6 6-6" }),
  ]),
  search: Object.freeze([
    node("circle", { cx: "11", cy: "11", r: "6.5" }),
    node("path", { d: "m16 16 4.5 4.5" }),
  ]),
  sliders: Object.freeze([
    node("path", { d: "M4 6h5M15 6h5M4 12h9M19 12h1M4 18h2M12 18h8" }),
    node("circle", { cx: "12", cy: "6", r: "2" }),
    node("circle", { cx: "16", cy: "12", r: "2" }),
    node("circle", { cx: "9", cy: "18", r: "2" }),
  ]),
  close: Object.freeze([
    node("path", { d: "M6 6l12 12M18 6 6 18" }),
  ]),
  refresh: Object.freeze([
    node("path", { d: "M20 7v5h-5M4 17v-5h5" }),
    node("path", { d: "M18.2 9A7 7 0 0 0 6.6 6.6L4 9M5.8 15A7 7 0 0 0 17.4 17.4L20 15" }),
  ]),
  book: Object.freeze([
    node("path", { d: "M3.5 5.5A2.5 2.5 0 0 1 6 3h5v16H6a2.5 2.5 0 0 0-2.5 2V5.5ZM20.5 5.5A2.5 2.5 0 0 0 18 3h-5v16h5a2.5 2.5 0 0 1 2.5 2V5.5Z", class: "rr-icon-fill" }),
  ]),
  "coverage-ok": Object.freeze([
    node("circle", { cx: "12", cy: "12", r: "9", class: "rr-icon-fill" }),
    node("path", { d: "m8 12 2.5 2.5L16.5 9" }),
  ]),
  "coverage-warning": Object.freeze([
    node("path", { d: "M10.2 4.2 2.8 17a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.8 4.2a2 2 0 0 0-3.6 0Z", class: "rr-icon-fill" }),
    node("path", { d: "M12 9v4M12 17h.01" }),
  ]),
  signal: Object.freeze([
    node("circle", { cx: "12", cy: "12", r: "2", class: "rr-icon-fill" }),
    node("path", { d: "M8.5 15.5a5 5 0 0 1 0-7M15.5 8.5a5 5 0 0 1 0 7M5.7 18.3a9 9 0 0 1 0-12.6M18.3 5.7a9 9 0 0 1 0 12.6" }),
  ]),
});

const ICON_ALIASES = Object.freeze({ x: "close" });

export const ICON_NAMES = Object.freeze([
  ...Object.keys(ICON_DEFINITIONS),
  ...Object.keys(ICON_ALIASES),
]);

function resolveIconName(name) {
  if (typeof name !== "string" || !name.trim()) {
    throw new TypeError("El nombre del icono debe ser una cadena no vacía");
  }
  const normalized = name.trim().toLowerCase();
  const canonical = ICON_ALIASES[normalized] || normalized;
  if (!Object.hasOwn(ICON_DEFINITIONS, canonical)) {
    throw new RangeError(`Icono desconocido: ${name}`);
  }
  return { requested: normalized, canonical };
}

function requireDocument(candidate = globalThis.document) {
  if (!candidate || typeof candidate.createElementNS !== "function") {
    throw new TypeError("createIcon requiere un documento con createElementNS");
  }
  return candidate;
}

function buildIcon(documentRef, name, options = {}) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError("Las opciones del icono deben ser un objeto");
  }
  const { requested, canonical } = resolveIconName(name);
  const className = options.className === undefined ? "rr-icon" : options.className;
  const label = options.label;
  if (typeof className !== "string") throw new TypeError("className debe ser una cadena");
  if (label !== undefined && typeof label !== "string") {
    throw new TypeError("label debe ser una cadena cuando se proporciona");
  }

  const svg = documentRef.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "24");
  svg.setAttribute("height", "24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("data-icon", requested);
  if (className.trim()) svg.setAttribute("class", className.trim());

  const accessibleLabel = label?.trim();
  if (accessibleLabel) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", accessibleLabel);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }

  for (const spec of ICON_DEFINITIONS[canonical]) {
    const child = documentRef.createElementNS(SVG_NS, spec.tag);
    for (const [attribute, value] of Object.entries(spec.attributes)) {
      child.setAttribute(attribute, value);
    }
    svg.append(child);
  }
  return svg;
}

/**
 * Crea un SVG inline seguro. Sin `label` es decorativo; con `label` expone role=img.
 */
export function createIcon(name, options = {}) {
  return buildIcon(requireDocument(), name, options);
}

/**
 * Añade un icono usando el ownerDocument del destino, útil para iframes y pruebas.
 */
export function appendIcon(target, name, options = {}) {
  if (!target || typeof target.append !== "function") {
    throw new TypeError("appendIcon requiere un destino que implemente append");
  }
  const documentRef = requireDocument(target.ownerDocument || globalThis.document);
  const icon = buildIcon(documentRef, name, options);
  target.append(icon);
  return icon;
}

/** Hidrata marcadores HTML `[data-icon]` sin reemplazar su caja de layout. */
export function hydrateIcons(root = globalThis.document) {
  if (!root || typeof root.querySelectorAll !== "function") {
    throw new TypeError("hydrateIcons requiere un nodo con querySelectorAll");
  }
  const hosts = [...root.querySelectorAll("[data-icon]:not(svg)")];
  for (const host of hosts) {
    const documentRef = requireDocument(host.ownerDocument || globalThis.document);
    const icon = buildIcon(documentRef, host.dataset.icon, {
      className: host.dataset.iconClass || "rr-icon",
    });
    host.replaceChildren(icon);
    host.dataset.iconReady = "";
  }
  return hosts.length;
}
