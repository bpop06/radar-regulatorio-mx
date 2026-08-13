import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Mexico_City", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());

const complete = {
  id: "dof:1",
  source: "DOF",
  source_id: "1",
  canonical_url: "https://dof.gob.mx/nota_detalle.php?codigo=1",
  official_published_at: `${today}T08:00:00-06:00`,
  detected_at: `${today}T10:30:00-06:00`,
  editorial_status: "complete",
  official_title: "Acuerdo oficial de prueba",
  title: "SHCP publica un acuerdo de prueba",
  summary_teaser: "El acuerdo establece una medida verificable para probar el corte público.",
  summary: "Resumen editorial de prueba.",
  card_body: "## Qué se publicó\n\nTexto oficial.\n\n## Sustancia\n\nContenido de prueba.",
  detail_url: "notas/aaaaaaaaaaaaaaaaaaaaaaaa.html",
  categories: ["Fiscal"],
  jurisdiction: "nacional",
  issuing_body: "SHCP",
  importance: 4,
};

const pending = {
  ...complete,
  id: "dof:2",
  source_id: "2",
  editorial_status: "needs_review",
  official_title: "Resolución oficial pendiente de revisión",
  title: null,
  summary_teaser: null,
  summary: null,
  review_reason: "La publicación no contiene sustancia suficiente para una interpretación responsable.",
  detail_url: "notas/bbbbbbbbbbbbbbbbbbbbbbbb.html",
};

const edition = {
  schema_version: 8,
  cut_id: "cut-test",
  edition_date: today,
  generated_at: new Date().toISOString(),
  state: "ready",
  coverage: { state: "degraded", ok: 17, failed: [{ source: "CIJ", error: "HTTP 404" }] },
  total_today: 2,
  last_available_date: today,
  signals: [{ ...complete, rank: 1, why_it_matters: "Cambia una obligación verificable." }, { ...pending, rank: 2 }],
};

async function mockV8(page, {
  editionPayload = edition,
  manifestCutId = "cut-test",
  archiveCutId = "cut-test",
} = {}) {
  await page.route("**/data/manifest.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      schema_version: 8, cut_id: manifestCutId, generated_at: new Date().toISOString(),
      edition_date: today, archive_months: [today.slice(0, 7)], artifact_count: 2,
      artifacts: {
        "data/edition.json": { sha256: "a".repeat(64), bytes: 100 },
        [`data/archive/${today.slice(0, 7)}.json`]: { sha256: "b".repeat(64), bytes: 100 },
      },
    }),
  }));
  await page.route("**/data/edition.json", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify(editionPayload),
  }));
  await page.route(`**/data/archive/${today.slice(0, 7)}.json`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ schema_version: 8, cut_id: archiveCutId, month: today.slice(0, 7), total_items: 2, items: [complete, pending] }),
  }));
}

test("portada muestra estados y pestañas APG operables", async ({ page }) => {
  await mockV8(page);
  await page.goto("/index.html");
  await expect(page.getByRole("heading", { name: "SHCP publica un acuerdo de prueba" })).toBeVisible();
  await expect(page.getByText(/Cobertura degradada/).first()).toBeVisible();
  const tabs = page.getByRole("tab");
  await tabs.first().focus();
  await page.keyboard.press("End");
  await expect(tabs.nth(1)).toBeFocused();
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#lead-panel").getByRole("heading", { name: pending.official_title })).toBeVisible();
  await expect(page.getByText("Revisión pendiente").first()).toBeVisible();
});

test("portada distingue sin novedades y corte pendiente", async ({ page }) => {
  await mockV8(page, { editionPayload: { ...edition, state: "empty", total_today: 0, signals: [] } });
  await page.goto("/index.html");
  await expect(page.getByRole("heading", { name: /no contiene publicaciones seleccionadas/i })).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  await mockV8(page, { editionPayload: {
    ...edition, edition_date: "2026-01-01", generated_at: "2026-01-01T18:30:00-06:00",
  } });
  await page.reload();
  await expect(page.getByRole("heading", { name: /corte vigente todavía no está publicado/i })).toBeVisible();
});

test("portada aplica los deadlines diarios de 12:00 y 20:00", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-08-12T11:59:59-06:00") });
  const previousEvening = {
    ...edition, edition_date: "2026-08-11", generated_at: "2026-08-11T18:30:00-06:00",
  };
  await mockV8(page, { editionPayload: previousEvening });
  await page.goto("/index.html");
  await expect(page.getByRole("heading", { name: complete.title })).toBeVisible();

  await page.clock.setFixedTime(new Date("2026-08-12T12:00:00-06:00"));
  await page.reload();
  await expect(page.getByRole("heading", { name: /corte vigente todavía no está publicado/i })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockV8(page, { editionPayload: {
    ...edition, generated_at: "2026-08-12T10:30:00-06:00",
  } });
  await page.clock.setFixedTime(new Date("2026-08-12T19:59:59-06:00"));
  await page.reload();
  await expect(page.getByRole("heading", { name: complete.title })).toBeVisible();

  await page.clock.setFixedTime(new Date("2026-08-12T20:00:00-06:00"));
  await page.reload();
  await expect(page.getByRole("heading", { name: /corte vigente todavía no está publicado/i })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockV8(page, { editionPayload: {
    ...edition, generated_at: "2026-08-12T18:30:00-06:00",
  } });
  await page.reload();
  await expect(page.getByRole("heading", { name: complete.title })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockV8(page, { editionPayload: {
    ...edition, generated_at: "2026-08-12T20:00:01-06:00",
  } });
  await page.reload();
  await expect(page.getByRole("heading", { name: /corte vigente todavía no está publicado/i })).toBeVisible();
});

test("portada explica un contrato no verificable y permite reintentar", async ({ page }) => {
  await page.route("**/data/manifest.json", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ schema_version: 6 }),
  }));
  await page.goto("/index.html");
  await expect(page.getByRole("heading", { name: "No fue posible comprobar el corte." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
});

test("portada rechaza mismatch de cut_id y fallo de red del manifiesto", async ({ page }) => {
  await mockV8(page, { editionPayload: { ...edition, cut_id: "cut-distinto" } });
  await page.goto("/index.html");
  await expect(page.getByRole("heading", { name: "No fue posible comprobar el corte." })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  let editionRequests = 0;
  await page.route("**/data/manifest.json", (route) => route.abort("failed"));
  await page.route("**/data/edition.json", (route) => {
    editionRequests += 1;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(edition) });
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "No fue posible comprobar el corte." })).toBeVisible();
  expect(editionRequests).toBe(0);
});

test("archivo rechaza un índice mensual de otro corte", async ({ page }) => {
  await mockV8(page, { archiveCutId: "cut-distinto" });
  await page.goto("/archivo.html");
  await expect(page.getByRole("heading", { name: "No fue posible cargar el archivo." })).toBeVisible();
  await expect(page.getByRole("heading", { name: complete.title })).not.toBeVisible();
});

test("ficha rechaza un envelope de otro corte", async ({ page }) => {
  const key = "cccccccccccccccccccccccc";
  await mockV8(page);
  await page.route(`**/data/items/${key}.json`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ schema_version: 8, cut_id: "cut-distinto", item: complete }),
  }));
  await page.goto(`/ficha.html?item=${key}`);
  await expect(page.getByRole("heading", { name: "No fue posible cargar la ficha" })).toBeVisible();
  await expect(page.getByRole("heading", { name: complete.title })).not.toBeVisible();
});

test("archivo mensual conserva ficha y estado editorial", async ({ page }) => {
  await mockV8(page);
  await page.goto("/archivo.html");
  await expect(page.getByRole("heading", { name: complete.title })).toBeVisible();
  const pendingLink = page.getByRole("link", { name: pending.official_title });
  await expect(pendingLink).toHaveAttribute("href", /notas\/bbbbbbbbbbbbbbbbbbbbbbbb\.html/);
  await page.getByLabel("Buscar").fill("pendiente");
  await expect(page.getByText("1 publicación")).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros" }).click();
  await expect(page.getByLabel("Buscar")).toBeFocused();
});

test("archivo descarga meses anteriores sólo cuando se solicitan", async ({ page }) => {
  let previousMonthRequests = 0;
  await page.route("**/data/manifest.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      schema_version: 8, cut_id: "cut-test", archive_months: [today.slice(0, 7), "2026-01"],
      artifacts: {
        [`data/archive/${today.slice(0, 7)}.json`]: { sha256: "a".repeat(64), bytes: 10 },
        "data/archive/2026-01.json": { sha256: "b".repeat(64), bytes: 10 },
      },
    }),
  }));
  await page.route(`**/data/archive/${today.slice(0, 7)}.json`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ schema_version: 8, cut_id: "cut-test", month: today.slice(0, 7), items: [complete] }),
  }));
  await page.route("**/data/archive/2026-01.json", (route) => {
    previousMonthRequests += 1;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ schema_version: 8, cut_id: "cut-test", month: "2026-01", items: [{ ...pending, official_published_at: "2026-01-15" }] }),
    });
  });
  await page.goto("/archivo.html");
  expect(previousMonthRequests).toBe(0);
  await page.getByRole("button", { name: "Cargar mes anterior" }).click();
  expect(previousMonthRequests).toBe(1);
  await expect(page.getByText("Archivo completo cargado.")).toBeVisible();
});

test("archivo abre directamente el mes indicado por fecha", async ({ page }) => {
  let latestMonthRequests = 0;
  let requestedMonthRequests = 0;
  const requestedDate = "2026-01-15";
  await page.route("**/data/manifest.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      schema_version: 8, cut_id: "cut-test", archive_months: [today.slice(0, 7), "2026-01"],
      artifacts: {
        [`data/archive/${today.slice(0, 7)}.json`]: { sha256: "a".repeat(64), bytes: 10 },
        "data/archive/2026-01.json": { sha256: "b".repeat(64), bytes: 10 },
      },
    }),
  }));
  await page.route(`**/data/archive/${today.slice(0, 7)}.json`, (route) => {
    latestMonthRequests += 1;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ schema_version: 8, cut_id: "cut-test", month: today.slice(0, 7), items: [complete] }),
    });
  });
  await page.route("**/data/archive/2026-01.json", (route) => {
    requestedMonthRequests += 1;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: 8, cut_id: "cut-test", month: "2026-01",
        items: [{ ...pending, official_published_at: requestedDate }],
      }),
    });
  });

  await page.goto(`/archivo.html?fecha=${requestedDate}`);
  await expect(page.getByRole("heading", { name: pending.official_title })).toBeVisible();
  expect(requestedMonthRequests).toBe(1);
  expect(latestMonthRequests).toBe(0);
  await expect(page.locator("#active-query-copy")).toContainText("15 de enero de 2026");
});

test("ficha v7 renderiza Markdown y SEO sin asteriscos literales", async ({ page }) => {
  await page.route("**/data/manifest.json", (route) => route.fulfill({ status: 404 }));
  await page.route("**/data/publications.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ schema_version: 7, items: [{ ...complete, id: "legacy", detail_url: "", card_body: "## Sustancia\n\nTexto con **evidencia oficial**." }] }),
  }));
  await page.route("**/data/edition.json", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...edition, schema_version: 7 }) }));
  await page.goto("/ficha.html?id=legacy");
  await expect(page.getByRole("heading", { name: complete.title })).toBeVisible();
  await expect(page.locator("strong", { hasText: "evidencia oficial" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("**");
  await expect(page).toHaveTitle(new RegExp(complete.title));
});

test("ficha internacional separa tratado, litis, resultado y razonamiento", async ({ page }) => {
  const internationalCase = {
    ...complete,
    id: "case-test",
    detail_url: "",
    source: "CIADI",
    case_number: "ARB/26/19",
    case_parties: "Inversionista de prueba c. México",
    case_treaty: "CPTPP",
    case_claim: "Trato injusto y expropiación indirecta",
    case_status: "Active",
    case_outcome: "Procedimiento pendiente",
    case_reasoning: "Aún no existe decisión sobre el fondo.",
    case_amount: "USD 10 millones",
  };
  await mockV8(page);
  await page.route("**/data/publications.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ schema_version: 8, cut_id: "cut-test", items: [internationalCase] }),
  }));
  await page.goto("/ficha.html?id=case-test");

  const evidence = page.locator(".evidence-list");
  await expect(evidence).toContainText("ARB/26/19");
  await expect(evidence).toContainText("Inversionista de prueba c. México");
  await expect(evidence).toContainText("CPTPP");
  await expect(evidence).toContainText("Trato injusto y expropiación indirecta");
  await expect(evidence).toContainText("Activo");
  await expect(evidence).toContainText("Procedimiento pendiente");
  await expect(evidence).toContainText("Aún no existe decisión sobre el fondo.");
  await expect(evidence).toContainText("USD 10 millones");
});

test("panel móvil del calendario atrapa foco, cierra con Escape y lo restaura", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/calendario.html");
  const day = page.locator(".cal-day").first();
  await day.click();
  const dialog = page.getByRole("dialog", { name: "Detalle del día" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "Cerrar detalle" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(day).toBeFocused();
});

test("refluye a 320 px sin desplazamiento horizontal", async ({ page }) => {
  await mockV8(page);
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/index.html");
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(widths.page).toBeLessThanOrEqual(widths.viewport);
  await expect(page.getByRole("link", { name: "Archivo" })).toBeVisible();
});

for (const path of ["/index.html", "/archivo.html", "/ficha.html?id=legacy", "/calendario.html"]) {
  test(`axe sin infracciones críticas: ${path}`, async ({ page }) => {
    await mockV8(page);
    if (path.includes("ficha")) {
      await page.route("**/data/publications.json", (route) => route.fulfill({
        contentType: "application/json", body: JSON.stringify({
          schema_version: 8, cut_id: "cut-test", items: [{ ...complete, id: "legacy" }],
        }),
      }));
    }
    await page.goto(path);
    await page.locator("main").waitFor();
    const results = await new AxeBuilder({ page }).analyze();
    const critical = results.violations.filter((violation) => violation.impact === "critical");
    expect(critical).toEqual([]);
  });
}
