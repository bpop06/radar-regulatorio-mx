import test from "node:test";
import assert from "node:assert/strict";

import {
  archivePaths,
  archivePathForMonth,
  assertMatchingCut,
  artifactPath,
  displayTitle,
  editorialLabel,
  fetchJson,
  isGeneratedCutOverdue,
  olderArchivePaths,
  officialDate,
  stableDetailHref,
  teaser,
} from "../../../docs/data-client.js";

const manifest = {
  schema_version: 8,
  archive_months: ["2026-07", "2026-08"],
  artifacts: {
    "data/edition.json": { sha256: "a".repeat(64), bytes: 12 },
    "data/archive/2026-08.json": { sha256: "b".repeat(64), bytes: 20 },
  },
};

test("resuelve rutas del manifiesto v8", () => {
  assert.equal(artifactPath(manifest, "edition.json"), "data/edition.json");
  assert.deepEqual(archivePaths(manifest), [
    "data/archive/2026-08.json",
    "data/archive/2026-07.json",
  ]);
  assert.equal(archivePathForMonth(archivePaths(manifest), "2026-07"), "data/archive/2026-07.json");
  assert.equal(archivePathForMonth(archivePaths(manifest), "2025-12"), "");
  assert.deepEqual(olderArchivePaths(archivePaths(manifest), "data/archive/2026-07.json"), []);
  assert.deepEqual(olderArchivePaths([
    "data/archive/2026-08.json",
    "data/archive/2026-07.json",
    "data/archive/2026-05.json",
  ], "data/archive/2026-07.json"), ["data/archive/2026-05.json"]);
});

test("sólo un HTTP 404 permite omitir un JSON opcional", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response("no encontrado", { status: 404 });
    assert.equal(await fetchJson("manifest.json", { optional: true }), null);

    globalThis.fetch = async () => { throw new TypeError("fallo de red"); };
    await assert.rejects(fetchJson("manifest.json", { optional: true }), /fallo de red/);

    globalThis.fetch = async () => new Response("{", {
      status: 200, headers: { "content-type": "application/json" },
    });
    await assert.rejects(fetchJson("manifest.json", { optional: true }), SyntaxError);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rechaza artefactos v8 cuyo cut_id no coincide", () => {
  const cutManifest = { schema_version: 8, cut_id: "cut-a" };
  assert.doesNotThrow(() => assertMatchingCut(
    cutManifest, { schema_version: 8, cut_id: "cut-a" }, "Edición",
  ));
  assert.throws(() => assertMatchingCut(
    cutManifest, { schema_version: 8, cut_id: "cut-b" }, "Edición",
  ), /cut_id no coincide/);
  assert.throws(() => assertMatchingCut(
    cutManifest, { schema_version: 8 }, "Ficha",
  ), /cut_id no coincide/);
});

test("el SLA usa las ventanas de 10:30 y 18:30 de Ciudad de México", () => {
  assert.equal(isGeneratedCutOverdue("2026-08-11T18:30:00-06:00", {
    now: "2026-08-12T11:59:59-06:00",
  }), false);
  assert.equal(isGeneratedCutOverdue("2026-08-11T18:29:59-06:00", {
    now: "2026-08-12T11:59:59-06:00",
  }), true);
  assert.equal(isGeneratedCutOverdue("2026-08-11T18:30:00-06:00", {
    now: "2026-08-12T12:00:00-06:00",
  }), true);
  assert.equal(isGeneratedCutOverdue("2026-08-12T10:30:00-06:00", {
    now: "2026-08-12T12:00:00-06:00",
  }), false);
  assert.equal(isGeneratedCutOverdue("2026-08-12T10:30:00-06:00", {
    now: "2026-08-12T19:59:59-06:00",
  }), false);
  assert.equal(isGeneratedCutOverdue("2026-08-12T10:30:00-06:00", {
    now: "2026-08-12T20:00:00-06:00",
  }), true);
  assert.equal(isGeneratedCutOverdue("2026-08-12T18:30:00-06:00", {
    now: "2026-08-12T20:00:00-06:00",
  }), false);
});

test("el SLA rechaza timestamps inválidos o futuros", () => {
  const now = "2026-08-12T20:00:00-06:00";
  assert.equal(isGeneratedCutOverdue("2026-08-12T20:00:01-06:00", { now }), true);
  assert.equal(isGeneratedCutOverdue("fecha-inválida", { now }), true);
});

test("needs_review usa sólo título oficial y causa de revisión", () => {
  const item = {
    editorial_status: "needs_review",
    title: null,
    official_title: "Acuerdo oficial",
    review_reason: "Falta sustancia verificable.",
  };
  assert.equal(displayTitle(item), "Acuerdo oficial");
  assert.equal(teaser(item), "Falta sustancia verificable.");
  assert.equal(editorialLabel(item), "Revisión pendiente");
});

test("distingue fecha oficial y construye enlace permanente", () => {
  const item = {
    id: "doF:123",
    official_published_at: "2026-08-12T09:00:00-06:00",
    published_at: "2026-08-13",
    detail_url: "notas/abcdef0123456789abcdef01.html",
  };
  assert.equal(officialDate(item), "2026-08-12T09:00:00-06:00");
  assert.equal(
    stableDetailHref(item, "archivo"),
    "notas/abcdef0123456789abcdef01.html?from=archivo",
  );
});

test("rechaza una URL de detalle absoluta o ascendente", () => {
  assert.equal(stableDetailHref({ id: "x", detail_url: "https://evil.example/ficha" }), "ficha.html?id=x&from=archivo");
  assert.equal(stableDetailHref({ id: "x", detail_url: "../secreto" }), "ficha.html?id=x&from=archivo");
});
