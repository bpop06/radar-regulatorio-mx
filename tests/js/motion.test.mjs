import assert from "node:assert/strict";
import test from "node:test";

import {
  LIQUID_MOVE_TIMING,
  createLiquidMove,
  liquidMoveTiming,
  relativeBox,
} from "../../docs/motion.js";

function fakeDom() {
  let frameId = 0;
  let timerId = 0;
  const frames = new Map();
  const timers = new Map();
  const resizeObservers = [];

  class FakeElement {
    constructor(documentObject) {
      this.ownerDocument = documentObject;
      this.children = [];
      this.dataset = {};
      this.style = { position: "" };
      this.attributes = new Map();
      this.parentElement = null;
      this.rect = { left: 0, top: 0, width: 0, height: 0 };
      this.scrollLeft = 0;
      this.scrollTop = 0;
      this.clientLeft = 0;
      this.clientTop = 0;
      this.activeTarget = null;
      this.events = [];
    }

    append(...children) {
      for (const child of children) {
        child.parentElement = this;
        this.children.push(child);
      }
    }

    contains(candidate) {
      return candidate === this || this.children.some((child) => child.contains(candidate));
    }

    dispatchEvent(event) {
      this.events.push(event);
      return true;
    }

    getBoundingClientRect() {
      return this.rect;
    }

    querySelector() {
      return this.activeTarget;
    }

    remove() {
      if (!this.parentElement) return;
      this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
      this.parentElement = null;
    }

    setAttribute(name, value) {
      this.attributes.set(name, String(value));
    }
  }

  class FakeCustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }

  class FakeResizeObserver {
    constructor(callback) {
      this.callback = callback;
      this.disconnected = false;
      this.observed = [];
      resizeObservers.push(this);
    }

    observe(element) {
      if (!this.observed.includes(element)) this.observed.push(element);
    }

    disconnect() {
      this.disconnected = true;
    }
  }

  class FakeMutationObserver {
    constructor(callback) {
      this.callback = callback;
    }

    observe() {}

    disconnect() {}
  }

  const media = {
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  };
  const view = {
    CustomEvent: FakeCustomEvent,
    MutationObserver: FakeMutationObserver,
    ResizeObserver: FakeResizeObserver,
    addEventListener() {},
    cancelAnimationFrame(id) { frames.delete(id); },
    clearTimeout(id) { timers.delete(id); },
    getComputedStyle() { return { position: "static" }; },
    matchMedia() { return media; },
    removeEventListener() {},
    requestAnimationFrame(callback) {
      const id = ++frameId;
      frames.set(id, callback);
      return id;
    },
    setTimeout(callback) {
      const id = ++timerId;
      timers.set(id, callback);
      return id;
    },
  };
  const documentObject = {
    defaultView: view,
    createElement() { return new FakeElement(documentObject); },
  };

  return {
    FakeElement,
    documentObject,
    frames,
    media,
    resizeObservers,
    timers,
    flushFrames() {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(0));
    },
    flushTimers() {
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach((callback) => callback());
    },
  };
}

test("el indicador usa el ritmo Move de la referencia sin una dependencia", () => {
  assert.deepEqual(LIQUID_MOVE_TIMING, {
    coreDuration: 250,
    tailDuration: 340,
    coreEasing: "cubic-bezier(.3, 1.05, .4, 1)",
    tailEasing: "cubic-bezier(.22, 1, .36, 1)",
  });
  assert.deepEqual(liquidMoveTiming(), LIQUID_MOVE_TIMING);
});

test("reduced motion conserva el estado final pero elimina el desplazamiento", () => {
  assert.deepEqual(liquidMoveTiming({
    reducedMotion: true,
    coreDuration: 900,
    tailDuration: 1200,
  }), {
    coreDuration: 0,
    tailDuration: 0,
    coreEasing: LIQUID_MOVE_TIMING.coreEasing,
    tailEasing: LIQUID_MOVE_TIMING.tailEasing,
  });
});

test("las coordenadas relativas incluyen scroll y excluyen el borde", () => {
  const box = relativeBox(
    { left: 100, top: 40 },
    { left: 132, top: 67, width: 88, height: 44 },
    { scrollLeft: 24, scrollTop: 5, clientLeft: 1, clientTop: 2 },
  );
  assert.deepEqual(box, {
    x: 55,
    y: 30,
    width: 88,
    height: 44,
  });
  assert.equal(Object.isFrozen(box), true);
});

test("normaliza duraciones y rectángulos defectuosos sin propagar NaN", () => {
  assert.deepEqual(liquidMoveTiming({ coreDuration: -10, tailDuration: "bad" }), {
    coreDuration: 0,
    tailDuration: LIQUID_MOVE_TIMING.tailDuration,
    coreEasing: LIQUID_MOVE_TIMING.coreEasing,
    tailEasing: LIQUID_MOVE_TIMING.tailEasing,
  });
  assert.deepEqual(relativeBox(
    { left: Number.NaN, top: 10 },
    { left: 20, top: Number.NaN, width: -80, height: Number.NaN },
  ), {
    x: 20,
    y: -10,
    width: 0,
    height: 0,
  });
  assert.throws(() => relativeBox(null, {}), TypeError);
});

test("retargeting interrumpe la cola anterior y termina en el control más reciente", () => {
  const dom = fakeDom();
  const container = new dom.FakeElement(dom.documentObject);
  container.rect = { left: 100, top: 50, width: 300, height: 60 };
  const first = new dom.FakeElement(dom.documentObject);
  first.rect = { left: 110, top: 58, width: 80, height: 44 };
  const second = new dom.FakeElement(dom.documentObject);
  second.rect = { left: 210, top: 58, width: 110, height: 44 };
  container.append(first, second);
  container.activeTarget = first;

  const liquid = createLiquidMove(container);
  dom.flushFrames();
  assert.deepEqual(dom.resizeObservers[0].observed, [container, first]);
  liquid.moveTo(first);
  liquid.moveTo(second);

  assert.equal(dom.timers.size, 1);
  assert.deepEqual(dom.resizeObservers[0].observed, [container, first, second]);
  assert.equal(liquid.indicator.children[1].style.transform, "translate3d(110px, 8px, 0)");
  assert.equal(liquid.indicator.children[1].style.transitionDuration, "250ms");
  assert.equal(liquid.indicator.children[0].style.transitionDuration, "340ms");
  dom.flushTimers();
  const settleEvents = container.events.filter(
    (event) => event.type === "liquidsettle" && event.detail.instant === false,
  );
  assert.equal(settleEvents.length, 1);
  assert.equal(settleEvents[0].detail.target, second);
  assert.equal(liquid.indicator.dataset.moving, "false");
});

test("resize realinea sin viajar y reduced-motion nunca programa movimiento", () => {
  const dom = fakeDom();
  dom.media.matches = true;
  const container = new dom.FakeElement(dom.documentObject);
  container.rect = { left: 20, top: 10, width: 220, height: 60 };
  const target = new dom.FakeElement(dom.documentObject);
  target.rect = { left: 32, top: 18, width: 90, height: 44 };
  container.append(target);
  container.activeTarget = target;

  const liquid = createLiquidMove(container);
  dom.flushFrames();
  assert.equal(liquid.indicator.children[1].style.transitionDuration, "0ms");
  assert.equal(dom.timers.size, 0);

  target.rect = { left: 82, top: 18, width: 100, height: 44 };
  dom.resizeObservers[0].callback();
  dom.flushFrames();
  assert.equal(liquid.indicator.children[1].style.transform, "translate3d(62px, 8px, 0)");
  assert.equal(liquid.indicator.children[1].style.width, "100px");

  liquid.destroy();
  assert.equal(dom.resizeObservers[0].disconnected, true);
  assert.equal(container.children.includes(liquid.indicator), false);
  assert.equal(Object.hasOwn(container.dataset, "liquidMoveContainer"), false);
  assert.equal(container.style.position, "");
});
