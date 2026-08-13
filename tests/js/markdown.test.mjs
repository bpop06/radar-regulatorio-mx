import assert from "node:assert/strict";
import test from "node:test";

import {
  createTime,
  datePresentation,
  detailHref,
  formatDate,
  formatDateTime,
  isSafeHttpUrl as safeUrl,
  normalizeIsoDate,
  parseInline,
  translateCaseStatus as translateStatus,
} from "../../docs/markdown.js";

test("safeUrl accepts HTTP(S) URLs and rejects unsafe values", () => {
  assert.equal(safeUrl("https://www.dof.gob.mx/nota_detalle.php"), true);
  assert.equal(safeUrl("HTTP://example.test/documento"), true);
  assert.equal(safeUrl("javascript:alert(1)"), false);
  assert.equal(safeUrl("mailto:persona@example.test"), false);
  assert.equal(safeUrl(null), false);
});

test("formatDate formats ISO dates for Mexico without shifting the day", () => {
  assert.equal(formatDate("2026-08-12"), "12 de agosto de 2026");
  assert.equal(formatDate("2026-08-12", { short: true }), "12 ago 2026");
  assert.equal(formatDate("2026-08-12", { year: false }), "12 de agosto");
  assert.equal(formatDate("12/08/2026"), "Fecha no disponible");
  assert.equal(formatDate("2026-02-29"), "Fecha no disponible");
  assert.equal(formatDate(null), "Fecha no disponible");
});

test("normalizeIsoDate accepts only real date-only ISO values", () => {
  assert.equal(normalizeIsoDate(" 2026-08-12 "), "2026-08-12");
  assert.equal(normalizeIsoDate("2024-02-29"), "2024-02-29");
  assert.equal(normalizeIsoDate("2026-02-29"), null);
  assert.equal(normalizeIsoDate("2026-08-12T00:00:00Z"), null);
  assert.equal(normalizeIsoDate("2026-13-01"), null);
});

test("datePresentation returns a canonical machine value and an es-MX label", () => {
  assert.deepEqual(datePresentation("2026-08-12"), {
    dateTime: "2026-08-12",
    label: "12 de agosto de 2026",
  });
  assert.deepEqual(datePresentation("not-a-date", { short: true }), {
    dateTime: null,
    label: "Fecha no disponible",
  });
});

test("formatDateTime presents generated_at in Mexico City and normalizes datetime", () => {
  const generatedAt = "2026-08-13T02:57:51.987493+00:00";
  assert.equal(
    formatDateTime(generatedAt),
    "12 de agosto de 2026 a las 8:57 p.m. CDMX",
  );
  assert.deepEqual(datePresentation(generatedAt, { type: "datetime", short: true }), {
    dateTime: "2026-08-13T02:57:51.987Z",
    label: "12 ago 2026, 8:57 p.m. CDMX",
  });
  assert.equal(formatDateTime("2026-08-13T02:57:51"), "Fecha no disponible");
  assert.equal(formatDateTime("invalid"), "Fecha no disponible");
});

test("createTime omits datetime entirely when the value is unavailable", () => {
  const fakeDocument = {
    createElement(tagName) {
      return {
        attributes: {},
        tagName: tagName.toUpperCase(),
        textContent: "",
        setAttribute(name, value) {
          this.attributes[name] = value;
        },
      };
    },
  };
  const valid = createTime("2026-08-12", { short: true }, fakeDocument);
  assert.equal(valid.tagName, "TIME");
  assert.equal(valid.textContent, "12 ago 2026");
  assert.deepEqual(valid.attributes, { datetime: "2026-08-12" });

  const invalid = createTime("", {}, fakeDocument);
  assert.equal(invalid.tagName, "SPAN");
  assert.equal(invalid.textContent, "Fecha no disponible");
  assert.equal(Object.hasOwn(invalid.attributes, "datetime"), false);
});

test("detailHref builds encoded local links and preserves existing queries", () => {
  assert.equal(
    detailHref({ id: "DOF 1/2" }, "hoy"),
    "ficha.html?id=DOF%201%2F2&from=hoy",
  );
  assert.equal(
    detailHref({ detail_url: "ficha.html?id=abc" }, "archivo"),
    "ficha.html?id=abc&from=archivo",
  );
  assert.equal(
    detailHref({ detail_url: "ficha.html" }, "calendario y plazos"),
    "ficha.html?from=calendario%20y%20plazos",
  );
});

test("translateStatus localizes known case statuses and preserves unknown ones", () => {
  assert.equal(translateStatus("Pending"), "Pendiente");
  assert.equal(translateStatus("Completed"), "Concluido");
  assert.equal(translateStatus(" Active "), "Activo");
  assert.equal(translateStatus("Suspended"), "Suspended");
  assert.equal(translateStatus(undefined), "");
});

test("parseInline renders strong, emphasis, and safe links without literal markers", () => {
  assert.deepEqual(
    parseInline("**Materia:** *Título oficial* [Fuente](https://example.test/doc)"),
    [
      { type: "strong", value: "Materia:" },
      { type: "text", value: " " },
      { type: "emphasis", value: "Título oficial" },
      { type: "text", value: " " },
      { type: "link", value: "Fuente", url: "https://example.test/doc" },
    ],
  );
  assert.deepEqual(
    parseInline("[No abrir](javascript:alert(1))"),
    [{ type: "text", value: "[No abrir](javascript:alert(1))" }],
  );
});
