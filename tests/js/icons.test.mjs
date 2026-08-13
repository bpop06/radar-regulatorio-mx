import assert from "node:assert/strict";
import test from "node:test";

import { appendIcon, createIcon, hydrateIcons, ICON_NAMES } from "../../docs/icons.js";

const SVG_NS = "http://www.w3.org/2000/svg";

class FakeElement {
  constructor(namespaceURI, tagName, ownerDocument) {
    this.namespaceURI = namespaceURI;
    this.tagName = tagName;
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.dataset = {};
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }
}

function fakeDocument() {
  const documentRef = {
    createElementNS(namespaceURI, tagName) {
      return new FakeElement(namespaceURI, tagName, documentRef);
    },
  };
  return documentRef;
}

function withDocument(callback) {
  const previous = globalThis.document;
  globalThis.document = fakeDocument();
  try {
    return callback(globalThis.document);
  } finally {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  }
}

const expectedNames = [
  "today",
  "archive",
  "calendar",
  "sun",
  "moon",
  "date",
  "file",
  "external",
  "arrow-back",
  "arrow-forward",
  "chevron-back",
  "chevron-forward",
  "chevron-down",
  "search",
  "sliders",
  "close",
  "refresh",
  "book",
  "coverage-ok",
  "coverage-warning",
  "signal",
  "x",
];

test("publica el catálogo completo y estable de iconos", () => {
  assert.deepEqual(ICON_NAMES, expectedNames);
  assert.equal(Object.isFrozen(ICON_NAMES), true);
});

test("todos los iconos comparten geometría, currentColor y trazo coherente", () => {
  withDocument(() => {
    for (const name of ICON_NAMES) {
      const icon = createIcon(name);
      assert.equal(icon.namespaceURI, SVG_NS, name);
      assert.equal(icon.tagName, "svg", name);
      assert.equal(icon.getAttribute("viewBox"), "0 0 24 24", name);
      assert.equal(icon.getAttribute("width"), "24", name);
      assert.equal(icon.getAttribute("height"), "24", name);
      assert.equal(icon.getAttribute("fill"), "none", name);
      assert.equal(icon.getAttribute("stroke"), "currentColor", name);
      assert.equal(icon.getAttribute("stroke-width"), "2", name);
      assert.equal(icon.getAttribute("stroke-linecap"), "round", name);
      assert.equal(icon.getAttribute("stroke-linejoin"), "round", name);
      assert.equal(icon.getAttribute("class"), "rr-icon", name);
      assert.equal(icon.getAttribute("data-icon"), name, name);
      assert.ok(icon.children.length > 0, name);

      for (const child of icon.children) {
        assert.equal(child.namespaceURI, SVG_NS, name);
        assert.equal(child.hasAttribute("style"), false, name);
        assert.equal(child.hasAttribute("onclick"), false, name);
        const stroke = child.getAttribute("stroke");
        const fill = child.getAttribute("fill");
        assert.ok(stroke === null || stroke === "currentColor", name);
        assert.ok(fill === null || fill === "none" || fill === "currentColor", name);
      }
    }
  });
});

test("los iconos son decorativos por defecto y etiquetables cuando informan", () => {
  withDocument(() => {
    const decorative = createIcon("search", { className: "control-icon search-icon" });
    assert.equal(decorative.getAttribute("aria-hidden"), "true");
    assert.equal(decorative.getAttribute("focusable"), "false");
    assert.equal(decorative.getAttribute("role"), null);
    assert.equal(decorative.getAttribute("class"), "control-icon search-icon");

    const informative = createIcon("coverage-warning", { label: "Cobertura parcial" });
    assert.equal(informative.getAttribute("aria-hidden"), null);
    assert.equal(informative.getAttribute("role"), "img");
    assert.equal(informative.getAttribute("aria-label"), "Cobertura parcial");
    assert.equal(informative.getAttribute("focusable"), "false");
  });
});

test("x es un alias visual de close sin perder su nombre solicitado", () => {
  withDocument(() => {
    const close = createIcon("close");
    const x = createIcon("x");
    assert.deepEqual(
      x.children.map((child) => Object.fromEntries(child.attributes)),
      close.children.map((child) => Object.fromEntries(child.attributes)),
    );
    assert.equal(x.getAttribute("data-icon"), "x");
  });
});

test("appendIcon usa el ownerDocument del destino y devuelve el SVG añadido", () => {
  const documentRef = fakeDocument();
  const target = new FakeElement(null, "button", documentRef);
  const icon = appendIcon(target, "refresh", { className: "retry-icon" });
  assert.equal(target.children.length, 1);
  assert.equal(target.children[0], icon);
  assert.equal(icon.ownerDocument, documentRef);
  assert.equal(icon.getAttribute("class"), "retry-icon");
});

test("hydrateIcons conserva la caja del marcador y evita SVG anidados", () => {
  const documentRef = fakeDocument();
  const host = new FakeElement(null, "span", documentRef);
  host.dataset.icon = "calendar";
  host.dataset.iconClass = "nav-icon";
  const root = { querySelectorAll: () => [host] };

  assert.equal(hydrateIcons(root), 1);
  assert.equal(host.dataset.iconReady, "");
  assert.equal(host.children.length, 1);
  assert.equal(host.children[0].tagName, "svg");
  assert.equal(host.children[0].getAttribute("data-icon"), "calendar");
  assert.equal(host.children[0].getAttribute("class"), "nav-icon");
});

test("rechaza nombres, opciones y destinos inválidos", () => {
  withDocument(() => {
    assert.throws(() => createIcon("unknown"), RangeError);
    assert.throws(() => createIcon(""), TypeError);
    assert.throws(() => createIcon("search", null), TypeError);
    assert.throws(() => createIcon("search", { className: 42 }), TypeError);
    assert.throws(() => createIcon("search", { label: 42 }), TypeError);
    assert.throws(() => appendIcon(null, "search"), TypeError);
  });
});

test("createIcon falla de forma explícita fuera de un entorno DOM", () => {
  const previous = globalThis.document;
  try {
    delete globalThis.document;
    assert.throws(() => createIcon("search"), /createElementNS/);
  } finally {
    if (previous !== undefined) globalThis.document = previous;
  }
});
