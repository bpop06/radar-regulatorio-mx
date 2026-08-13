import assert from "node:assert/strict";
import test from "node:test";

import { weekBoundaryDay } from "../../docs/calendar-core.js";

test("Home y End respetan los pads iniciales de julio de 2026", () => {
  assert.equal(weekBoundaryDay(8, 2, 31, "start"), 6);
  assert.equal(weekBoundaryDay(8, 2, 31, "end"), 12);
});

test("los límites se recortan en las semanas parciales del mes", () => {
  assert.equal(weekBoundaryDay(1, 2, 31, "start"), 1);
  assert.equal(weekBoundaryDay(1, 2, 31, "end"), 5);
  assert.equal(weekBoundaryDay(31, 2, 31, "start"), 27);
  assert.equal(weekBoundaryDay(31, 2, 31, "end"), 31);
});

test("rechaza días y límites inválidos", () => {
  assert.throws(() => weekBoundaryDay(0, 2, 31, "start"), RangeError);
  assert.throws(() => weekBoundaryDay(8, 2, 31, "middle"), TypeError);
});
