import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><main id='root'></main>", { url: "https://radar.example/ficha.html" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;

const { renderMarkdown } = await import("../../../docs/markdown.js");

test("renderiza negritas con doble asterisco sin dejar Markdown literal", () => {
  const root = document.querySelector("#root");
  renderMarkdown("La **evidencia oficial** prevalece.", root);
  assert.equal(root.querySelector("strong")?.textContent, "evidencia oficial");
  assert.equal(root.textContent.includes("**"), false);
});

test("renderiza enlaces HTTPS seguros y anuncia la pestaña nueva", () => {
  const root = document.querySelector("#root");
  renderMarkdown("[Abrir fuente](https://example.gob.mx/documento)", root);
  assert.equal(root.querySelector("a")?.href, "https://example.gob.mx/documento");
  assert.match(root.querySelector("a")?.textContent || "", /abre en una pestaña nueva/);
});
