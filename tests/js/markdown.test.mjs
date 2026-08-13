import assert from "node:assert/strict";
import test from "node:test";

import {
  detailHref,
  formatDate,
  isSafeHttpUrl as safeUrl,
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
  assert.equal(formatDate("12/08/2026"), "—");
  assert.equal(formatDate(null), "—");
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
