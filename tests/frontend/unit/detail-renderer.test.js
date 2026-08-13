import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";

import {
  renderStructuredDetail,
  renderTraceability,
  structuredDetailIsUsable,
} from "../../../docs/detail-renderer.js";

test("renders four semantic sections and treats editorial strings as text", () => {
  const dom = new JSDOM("<main id='root'></main>");
  global.document = dom.window.document;
  const root = document.querySelector("#root");
  renderStructuredDetail(root, {
    executive_summary: ["Cambio sustantivo.", "<img src=x onerror=alert(1)>"] ,
    detailed_summary: [{ heading: "Regla", paragraphs: ["Contenido"], evidence_refs: ["p1"] }],
    impacts: {
      general: { paragraphs: ["Sin impacto general directo."], evidence_refs: ["p1"] },
      sectors: [{ sector: "Contribuyentes", paragraphs: ["Revisan sus CFDI."], evidence_refs: ["p1"] }],
    },
    recommended_actions: [{
      affected_group: "Contribuyentes",
      paragraphs: ["Conservar evidencia."],
      trigger: "Recibir un CFDI señalado",
      deadline: "30 días",
      legal_basis: "Artículo 49 Bis",
      evidence_refs: ["p1"],
    }],
    evidence: {
      official_url: "https://dof.gob.mx/nota", retrieved_at: "2026-08-13T12:00:00Z",
      content_hash: "a".repeat(64), extraction_method: "html-adapter",
      locators: [{ id: "p1", label: "Cuerpo", location: "párrafo 1" }],
    },
    coverage: [{ source_ref: "p1", disposition: "summarized", target_sections: ["detailed_summary"] }],
  });

  assert.deepEqual(
    [...root.querySelectorAll(":scope > section")].map((node) => node.dataset.section),
    ["executive-summary", "detailed-summary", "impacts", "recommended-actions"],
  );
  assert.equal(root.querySelector("img"), null);
  assert.match(root.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.deepEqual([...root.querySelectorAll("h2")].map((node) => node.textContent), [
    "Resumen ejecutivo", "Resumen detallado", "Impactos", "Acciones recomendadas",
  ]);
  delete global.document;
});

test("renders evidence locators without exposing source text", () => {
  const dom = new JSDOM("<ul id='root'></ul>");
  global.document = dom.window.document;
  const root = document.querySelector("#root");
  renderTraceability(root, {
    evidence: { locators: [{ id: "p1", label: "Anexo 1", location: "tabla 1" }] },
  });
  assert.equal(root.children.length, 1);
  assert.equal(root.textContent, "Anexo 1 — tabla 1");
  delete global.document;
});

test("rejects empty mandatory structured blocks", () => {
  assert.equal(structuredDetailIsUsable({
    executive_summary: [], detailed_summary: [], impacts: {}, recommended_actions: [],
  }), false);
});

test("rejects prose without traceable evidence and coverage", () => {
  assert.equal(structuredDetailIsUsable({
    executive_summary: ["Resumen"],
    detailed_summary: [{ heading: "Regla", paragraphs: ["Contenido"], evidence_refs: ["p1"] }],
    impacts: { general: { paragraphs: ["Impacto"], evidence_refs: ["p1"] }, sectors: [{ sector: "Sector", paragraphs: ["Impacto"], evidence_refs: ["p1"] }] },
    recommended_actions: [{ affected_group: "Sector", paragraphs: ["Acción"], evidence_refs: ["p1"] }],
  }), false);
});
