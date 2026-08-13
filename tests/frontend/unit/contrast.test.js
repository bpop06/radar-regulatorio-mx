import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const stylesheet = await readFile(new URL("../../../docs/styles.css", import.meta.url), "utf8");

function variables(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = stylesheet.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(match, `No se encontró ${selector}`);
  return Object.fromEntries([...match[1].matchAll(/(--[\w-]+):\s*(#[\da-f]{6})/gi)]
    .map(([, name, value]) => [name, value]));
}

function luminance(hex) {
  const channels = hex.match(/[\da-f]{2}/gi).map((value) => Number.parseInt(value, 16) / 255);
  const [red, green, blue] = channels.map((value) => value <= 0.04045
    ? value / 12.92
    : ((value + 0.055) / 1.055) ** 2.4);
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function assertNormalTextContrast(tokens, foreground, backgrounds) {
  for (const background of backgrounds) {
    const ratio = contrast(tokens[foreground], tokens[background]);
    assert.ok(
      ratio >= 4.5,
      `${foreground} sobre ${background} tiene contraste ${ratio.toFixed(2)}:1`,
    );
  }
}

test("los tokens de texto secundario cumplen WCAG AA en tema claro", () => {
  const tokens = variables(":root");
  for (const foreground of ["--ink", "--ink-soft", "--ink-faint", "--accent-strong", "--ok", "--danger"]) {
    assertNormalTextContrast(tokens, foreground, ["--paper", "--surface"]);
  }
  assertNormalTextContrast(tokens, "--danger", ["--danger-soft"]);
  assertNormalTextContrast(tokens, "--gold", ["--gold-soft"]);
});

test("los tokens de texto secundario cumplen WCAG AA en tema oscuro", () => {
  const tokens = variables(':root[data-theme="dark"]');
  for (const foreground of ["--ink", "--ink-soft", "--ink-faint", "--accent-strong", "--ok", "--danger"]) {
    assertNormalTextContrast(tokens, foreground, ["--paper", "--surface"]);
  }
  assertNormalTextContrast(tokens, "--danger", ["--danger-soft"]);
  assertNormalTextContrast(tokens, "--gold", ["--gold-soft"]);
});
