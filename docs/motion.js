/**
 * Movimiento compartido para controles segmentados de Radar MX.
 *
 * El indicador mantiene dos siluetas nítidas: el núcleo llega primero y una
 * segunda silueta lo alcanza con retraso. CSS puede unir ambas con un filtro,
 * sin aplicar blur al texto ni a los controles interactivos.
 */

export const LIQUID_MOVE_TIMING = Object.freeze({
  coreDuration: 250,
  tailDuration: 340,
  coreEasing: "cubic-bezier(.3, 1.05, .4, 1)",
  tailEasing: "cubic-bezier(.22, 1, .36, 1)",
});

const DEFAULT_ACTIVE_SELECTOR = [
  '[aria-pressed="true"]',
  '[aria-selected="true"]',
  ".is-active",
  ".active",
].join(", ");

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function duration(value, fallback) {
  return Math.max(0, finite(value, fallback));
}

/**
 * Convierte el rectángulo de un control a coordenadas de su contenedor.
 * Los offsets de scroll conservan la posición dentro de bandas horizontales.
 */
export function relativeBox(containerRect, targetRect, offsets = {}) {
  if (!containerRect || !targetRect) {
    throw new TypeError("containerRect y targetRect son obligatorios");
  }

  const scrollLeft = finite(offsets.scrollLeft);
  const scrollTop = finite(offsets.scrollTop);
  const clientLeft = finite(offsets.clientLeft);
  const clientTop = finite(offsets.clientTop);
  const width = Math.max(0, finite(targetRect.width));
  const height = Math.max(0, finite(targetRect.height));

  return Object.freeze({
    x: finite(targetRect.left) - finite(containerRect.left) + scrollLeft - clientLeft,
    y: finite(targetRect.top) - finite(containerRect.top) + scrollTop - clientTop,
    width,
    height,
  });
}

/** Devuelve duraciones instantáneas cuando se solicita movimiento reducido. */
export function liquidMoveTiming(options = {}) {
  const reducedMotion = options.reducedMotion === true;
  return Object.freeze({
    coreDuration: reducedMotion ? 0 : duration(
      options.coreDuration,
      LIQUID_MOVE_TIMING.coreDuration,
    ),
    tailDuration: reducedMotion ? 0 : duration(
      options.tailDuration,
      LIQUID_MOVE_TIMING.tailDuration,
    ),
    coreEasing: options.coreEasing || LIQUID_MOVE_TIMING.coreEasing,
    tailEasing: options.tailEasing || LIQUID_MOVE_TIMING.tailEasing,
  });
}

function measureRelativeBox(container, target) {
  return relativeBox(container.getBoundingClientRect(), target.getBoundingClientRect(), {
    scrollLeft: container.scrollLeft,
    scrollTop: container.scrollTop,
    clientLeft: container.clientLeft,
    clientTop: container.clientTop,
  });
}

function setPartTransition(part, partDuration, easing, instant) {
  part.style.transitionProperty = "transform, width, height, opacity";
  part.style.transitionDuration = instant ? "0ms" : `${partDuration}ms`;
  part.style.transitionTimingFunction = easing;
}

function placePart(part, box) {
  part.style.width = `${box.width}px`;
  part.style.height = `${box.height}px`;
  part.style.transform = `translate3d(${box.x}px, ${box.y}px, 0)`;
  part.style.opacity = box.width > 0 && box.height > 0 ? "1" : "0";
}

function makeIndicator(documentObject) {
  const indicator = documentObject.createElement("span");
  indicator.className = "liquid-move";
  indicator.dataset.liquidMove = "";
  indicator.setAttribute("aria-hidden", "true");

  const tail = documentObject.createElement("span");
  tail.className = "liquid-move__tail";
  tail.dataset.liquidPart = "tail";
  const core = documentObject.createElement("span");
  core.className = "liquid-move__core";
  core.dataset.liquidPart = "core";
  indicator.append(tail, core);
  return { indicator, core, tail };
}

function dispatchMoveEvent(container, type, detail) {
  const ViewCustomEvent = container.ownerDocument?.defaultView?.CustomEvent;
  if (typeof ViewCustomEvent === "function") {
    container.dispatchEvent(new ViewCustomEvent(type, { detail }));
  }
}

/**
 * Instala un indicador compartido en un contenedor de controles.
 *
 * Hooks públicos:
 * - `moveTo(target, { instant })` permite controlar el destino sin framework.
 * - `sync({ instant })` mide de nuevo la opción activa.
 * - eventos `liquidmove` y `liquidsettle` exponen el ciclo a integraciones.
 * - clases/data-attributes de `.liquid-move*` son puntos de estilo estables.
 */
export function createLiquidMove(container, options = {}) {
  if (!container || typeof container.querySelector !== "function") {
    throw new TypeError("createLiquidMove requiere un contenedor DOM");
  }

  const documentObject = container.ownerDocument || document;
  const windowObject = documentObject.defaultView || window;
  const activeSelector = options.activeSelector || DEFAULT_ACTIVE_SELECTOR;
  const media = options.motionMedia || windowObject.matchMedia?.(
    "(prefers-reduced-motion: reduce)",
  );
  const parts = makeIndicator(documentObject);
  const computedPosition = windowObject.getComputedStyle?.(container).position;
  const suppliedPosition = container.style.position;
  let addedPosition = false;
  let activeTarget = null;
  let frame = 0;
  let settleTimer = 0;
  let generation = 0;
  let destroyed = false;

  if (!computedPosition || computedPosition === "static") {
    container.style.position = "relative";
    addedPosition = true;
  }
  container.dataset.liquidMoveContainer = "";
  container.append(parts.indicator);

  const prefersReducedMotion = () => options.reducedMotion === true || media?.matches === true;

  function clearScheduledWork() {
    if (frame) windowObject.cancelAnimationFrame(frame);
    if (settleTimer) windowObject.clearTimeout(settleTimer);
    frame = 0;
    settleTimer = 0;
  }

  function moveTo(target, moveOptions = {}) {
    if (destroyed || !target || !container.contains(target)) return false;
    resizeObserver?.observe(target);
    const box = measureRelativeBox(container, target);
    const reducedMotion = prefersReducedMotion();
    const instant = moveOptions.instant === true || reducedMotion;
    const timing = liquidMoveTiming({ ...options, reducedMotion });
    const previousTarget = activeTarget;
    const moveGeneration = ++generation;
    activeTarget = target;
    clearScheduledWork();

    setPartTransition(
      parts.core,
      timing.coreDuration,
      timing.coreEasing,
      instant,
    );
    setPartTransition(
      parts.tail,
      timing.tailDuration,
      timing.tailEasing,
      instant,
    );
    placePart(parts.core, box);
    placePart(parts.tail, box);
    parts.indicator.dataset.moving = String(!instant);

    const detail = Object.freeze({
      box,
      target,
      previousTarget,
      instant,
      timing,
    });
    dispatchMoveEvent(container, "liquidmove", detail);
    options.onMove?.(detail);

    const settle = () => {
      if (destroyed || moveGeneration !== generation) return;
      parts.indicator.dataset.moving = "false";
      dispatchMoveEvent(container, "liquidsettle", detail);
      options.onSettle?.(detail);
    };
    if (instant) settle();
    else settleTimer = windowObject.setTimeout(settle, timing.tailDuration);
    return true;
  }

  function activeElement() {
    return container.querySelector(activeSelector);
  }

  function sync(syncOptions = {}) {
    if (destroyed) return false;
    const target = activeElement() || activeTarget;
    return target ? moveTo(target, syncOptions) : false;
  }

  function scheduleSync(syncOptions = {}) {
    if (destroyed) return;
    if (frame) windowObject.cancelAnimationFrame(frame);
    frame = windowObject.requestAnimationFrame(() => {
      frame = 0;
      sync(syncOptions);
    });
  }

  const ResizeObserverClass = windowObject.ResizeObserver;
  const resizeObserver = typeof ResizeObserverClass === "function"
    ? new ResizeObserverClass(() => scheduleSync({ instant: true }))
    : null;
  resizeObserver?.observe(container);

  const MutationObserverClass = windowObject.MutationObserver;
  const mutationObserver = typeof MutationObserverClass === "function"
    ? new MutationObserverClass(() => {
      const nextTarget = activeElement();
      if (nextTarget && nextTarget !== activeTarget) moveTo(nextTarget);
    })
    : null;
  mutationObserver?.observe(container, {
    subtree: true,
    attributes: true,
    attributeFilter: ["aria-pressed", "aria-selected", "class", "hidden"],
  });

  const handleResize = () => scheduleSync({ instant: true });
  const handleMotionPreference = () => scheduleSync({ instant: true });
  if (!resizeObserver) windowObject.addEventListener("resize", handleResize, { passive: true });
  if (typeof media?.addEventListener === "function") {
    media.addEventListener("change", handleMotionPreference);
  } else {
    media?.addListener?.(handleMotionPreference);
  }

  scheduleSync({ instant: true });

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    generation += 1;
    clearScheduledWork();
    resizeObserver?.disconnect();
    mutationObserver?.disconnect();
    windowObject.removeEventListener("resize", handleResize);
    if (typeof media?.removeEventListener === "function") {
      media.removeEventListener("change", handleMotionPreference);
    } else {
      media?.removeListener?.(handleMotionPreference);
    }
    parts.indicator.remove();
    delete container.dataset.liquidMoveContainer;
    if (addedPosition) container.style.position = suppliedPosition;
  }

  return Object.freeze({
    destroy,
    indicator: parts.indicator,
    moveTo,
    sync,
  });
}
