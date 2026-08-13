const SUPPORTED_SCHEMAS = new Set([7, 8]);

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function isSupportedEnvelope(value) {
  return isRecord(value) && SUPPORTED_SCHEMAS.has(Number(value.schema_version));
}

export async function fetchJson(path, { optional = false } = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    if (optional && response.status === 404) return null;
    throw new Error(`${path}: HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType && !/json/i.test(contentType)) {
    throw new Error(`${path}: tipo de contenido inesperado`);
  }
  return await response.json();
}

export function assertMatchingCut(manifest, envelope, label = "Artefacto") {
  const expected = manifest?.cut_id;
  const actual = envelope?.cut_id;
  if (Number(manifest?.schema_version) !== 8
    || Number(envelope?.schema_version) !== 8
    || typeof expected !== "string" || !expected
    || typeof actual !== "string" || actual !== expected) {
    throw new Error(`${label}: cut_id no coincide con el manifiesto`);
  }
}

function assertLegacyEnvelope(envelope, label) {
  if (!isRecord(envelope) || Number(envelope.schema_version) !== 7) {
    throw new Error(`${label}: la compatibilidad sin manifiesto sólo admite schema_version 7`);
  }
}

export function artifactPath(manifest, name) {
  const artifacts = manifest?.artifacts;
  if (!artifacts) return "";
  const normalizedName = name.replace(/^data\//, "");
  if (isRecord(artifacts)) {
    const key = Object.keys(artifacts).find((candidate) => candidate === name
      || candidate === normalizedName
      || candidate === `data/${normalizedName}`
      || candidate.endsWith(`/${normalizedName}`));
    if (key) return key.replace(/^docs\//, "");
  }
  const candidates = [];
  if (Array.isArray(artifacts)) {
    candidates.push(...artifacts.filter((entry) => {
      const path = typeof entry === "string" ? entry : entry?.path || entry?.url || entry?.name;
      return path === name || path === `data/${normalizedName}` || path?.endsWith(`/${normalizedName}`);
    }));
  } else if (isRecord(artifacts)) {
    candidates.push(
      artifacts[name],
      artifacts[normalizedName],
      artifacts[`data/${normalizedName}`],
      ...Object.entries(artifacts)
        .filter(([key]) => key === name || key.endsWith(`/${normalizedName}`))
        .map(([, value]) => value),
    );
  }
  const value = candidates.find(Boolean);
  if (typeof value === "string") return value.replace(/^docs\//, "");
  if (isRecord(value)) return String(value.path || value.url || value.href || "").replace(/^docs\//, "");
  return "";
}

export function archivePaths(manifest) {
  const found = new Set();
  const add = (value) => {
    const path = typeof value === "string" ? value : value?.path || value?.url || value?.href || "";
    const match = String(path).match(/(?:^|\/)archive\/(\d{4}-\d{2})\.json$/);
    if (match) found.add(`data/archive/${match[1]}.json`);
    else if (/^\d{4}-\d{2}$/.test(String(value))) found.add(`data/archive/${value}.json`);
  };

  for (const month of manifest?.archive_months || manifest?.months || []) add(month);
  const artifacts = manifest?.artifacts;
  if (Array.isArray(artifacts)) artifacts.forEach(add);
  else if (isRecord(artifacts)) {
    Object.keys(artifacts).forEach(add);
    Object.values(artifacts).forEach(add);
  }
  return [...found].sort().reverse();
}

export function archivePathForMonth(paths, month) {
  if (!/^\d{4}-\d{2}$/.test(String(month || ""))) return "";
  const expected = `data/archive/${month}.json`;
  return paths.includes(expected) ? expected : "";
}

const CUT_TIME_ZONE = "America/Mexico_City";

function mexicoCityParts(instant) {
  const date = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(date.getTime())) return null;
  const values = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: CUT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date).filter(({ type }) => type !== "literal")
    .map(({ type, value }) => [type, value]));
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    time: `${values.hour}:${values.minute}:${values.second}`,
  };
}

function previousCalendarDate(isoDate) {
  const [year, month, day] = isoDate.split("-").map(Number);
  const previous = new Date(Date.UTC(year, month - 1, day - 1));
  return previous.toISOString().slice(0, 10);
}

export function isGeneratedCutOverdue(generatedAt, { now = Date.now() } = {}) {
  const generated = Date.parse(generatedAt);
  const current = now instanceof Date ? now.getTime()
    : typeof now === "string" ? Date.parse(now) : Number(now);
  if (!Number.isFinite(generated) || !Number.isFinite(current)) return true;
  if (generated > current) return true;

  const currentLocal = mexicoCityParts(current);
  const generatedLocal = mexicoCityParts(generated);
  if (!currentLocal || !generatedLocal) return true;

  let expectedDate = currentLocal.date;
  let expectedTime = "10:30:00";
  if (currentLocal.time < "12:00:00") {
    expectedDate = previousCalendarDate(currentLocal.date);
    expectedTime = "18:30:00";
  } else if (currentLocal.time >= "20:00:00") {
    expectedTime = "18:30:00";
  }
  return `${generatedLocal.date}T${generatedLocal.time}` < `${expectedDate}T${expectedTime}`;
}

export function itemList(payload) {
  if (Array.isArray(payload)) return payload;
  for (const key of ["items", "publications", "signals", "entries"]) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

export function sourceList(payload) {
  if (Array.isArray(payload?.sources)) return payload.sources;
  if (Array.isArray(payload?.coverage?.sources)) return payload.coverage.sources;
  return [];
}

export function itemKey(item) {
  const explicit = item?.item_key || item?.hash || item?.content_key;
  if (typeof explicit === "string" && /^[a-f0-9]{16,64}$/i.test(explicit)) return explicit;
  const match = String(item?.detail_url || "").match(/(?:items\/|notas\/)([a-f0-9]{16,64})\.(?:json|html)/i);
  return match?.[1] || "";
}

export function officialDate(item) {
  return item?.official_published_at || item?.published_at || "";
}

export function displayTitle(item) {
  const pending = item?.editorial_status === "needs_review";
  if (pending) return item?.official_title || item?.title || "Publicación oficial";
  return item?.title || item?.official_title || "Publicación oficial";
}

export function teaser(item) {
  if (item?.editorial_status === "needs_review") {
    return item?.review_reason || "La evidencia oficial está disponible; la nota editorial sigue en revisión.";
  }
  return item?.summary_teaser || item?.summary || item?.description || "";
}

export function editorialLabel(item) {
  return item?.editorial_status === "needs_review" ? "Revisión pendiente" : "Completa";
}

export function safeRelativePath(value) {
  if (typeof value !== "string" || !value || value.startsWith("/") || value.includes("..")) return "";
  if (/^[a-z][a-z\d+.-]*:/i.test(value)) return "";
  return value;
}

export function stableDetailHref(item, from = "archivo") {
  const stable = safeRelativePath(item?.detail_url);
  let base = stable;
  if (!base) {
    const key = itemKey(item);
    base = key ? `ficha.html?item=${encodeURIComponent(key)}` : `ficha.html?id=${encodeURIComponent(item?.id || "")}`;
  }
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}from=${encodeURIComponent(from)}`;
}

export async function loadManifest() {
  const manifest = await fetchJson("data/manifest.json", { optional: true });
  if (manifest === null) return null;
  if (!isSupportedEnvelope(manifest) || Number(manifest.schema_version) !== 8
    || typeof manifest.cut_id !== "string" || !manifest.cut_id) {
    throw new Error("Contrato de manifiesto inválido");
  }
  return manifest;
}

export async function loadEdition(manifest = undefined) {
  const resolvedManifest = manifest === undefined ? await loadManifest() : manifest;
  const path = artifactPath(resolvedManifest, "edition.json") || "data/edition.json";
  const edition = await fetchJson(path);
  if (!isRecord(edition) || !Array.isArray(edition.signals)) {
    throw new Error("Contrato de edición inválido");
  }
  if (resolvedManifest) assertMatchingCut(resolvedManifest, edition, "Edición");
  else assertLegacyEnvelope(edition, "Edición");
  return edition;
}

export async function loadArchive({ month = "" } = {}) {
  const manifest = await loadManifest();
  const paths = archivePaths(manifest);
  if (paths.length) {
    const requestedPath = archivePathForMonth(paths, month);
    const initialPath = requestedPath || paths[0];
    const envelope = await fetchJson(initialPath);
    assertMatchingCut(manifest, envelope, "Archivo mensual");
    const sources = sourceList(envelope).length ? sourceList(envelope) : sourceList(manifest);
    return {
      manifest,
      items: itemList(envelope),
      sources,
      loadedPaths: [initialPath],
      remainingPaths: paths.filter((path) => path !== initialPath),
      mode: "monthly",
    };
  }

  const compatibilityPath = artifactPath(manifest, "publications.json") || "data/publications.json";
  const payload = await fetchJson(compatibilityPath);
  if (manifest) assertMatchingCut(manifest, payload, "Archivo compatible");
  else assertLegacyEnvelope(payload, "Archivo compatible");
  if (!Array.isArray(payload.items)) throw new Error("Contrato de archivo inválido");
  return {
    manifest,
    items: itemList(payload),
    sources: sourceList(payload),
    mode: "compatibility",
  };
}

export async function loadArchiveMonth(path, manifest = undefined) {
  if (!/^data\/archive\/\d{4}-\d{2}\.json$/.test(path)) {
    throw new Error("Ruta mensual de archivo inválida");
  }
  const resolvedManifest = manifest === undefined ? await loadManifest() : manifest;
  if (!resolvedManifest) throw new Error("Un archivo mensual requiere manifiesto v8");
  const envelope = await fetchJson(path);
  assertMatchingCut(resolvedManifest, envelope, "Archivo mensual");
  if (!Array.isArray(envelope.items)) throw new Error("Contrato mensual inválido");
  return itemList(envelope);
}

export async function loadItem({ key = "", id = "" } = {}, manifest = undefined) {
  const resolvedManifest = manifest === undefined ? await loadManifest() : manifest;
  if (resolvedManifest && /^[a-f0-9]{16,64}$/i.test(key)) {
    const itemEnvelope = await fetchJson(`data/items/${key}.json`, { optional: true });
    if (itemEnvelope !== null) {
      assertMatchingCut(resolvedManifest, itemEnvelope, "Ficha");
      const item = isRecord(itemEnvelope.item) ? itemEnvelope.item : itemEnvelope;
      if (!isRecord(item)) throw new Error("Contrato de ficha inválido");
      return item;
    }
  }
  const path = artifactPath(resolvedManifest, "publications.json") || "data/publications.json";
  const payload = await fetchJson(path);
  if (resolvedManifest) assertMatchingCut(resolvedManifest, payload, "Archivo compatible");
  else assertLegacyEnvelope(payload, "Archivo compatible");
  if (!Array.isArray(payload.items)) throw new Error("Contrato de archivo inválido");
  return itemList(payload).find((item) => item.id === id || itemKey(item) === key) || null;
}
