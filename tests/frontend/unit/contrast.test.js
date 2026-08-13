import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const stylesheet = await readFile(new URL("../../../docs/styles.css", import.meta.url), "utf8");

function variables(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const matches = [...stylesheet.matchAll(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`, "g"))];
  assert.ok(matches.length, `No se encontró ${selector}`);
  const tokens = {};
  for (const match of matches) {
    for (const [, name, value] of match[1].matchAll(/(--[\w-]+):\s*([^;]+);/gi)) {
      tokens[name] = value.trim();
    }
  }
  return tokens;
}

function resolveToken(tokens, name, seen = new Set()) {
  assert.ok(!seen.has(name), `Referencia circular en ${name}`);
  const value = tokens[name];
  assert.ok(value, `No se encontró ${name}`);
  const reference = /^var\((--[\w-]+)\)$/.exec(value);
  if (!reference) return value;
  return resolveToken(tokens, reference[1], new Set([...seen, name]));
}

function linearChannels(color) {
  const hex = /^#([\da-f]{6})$/i.exec(color);
  if (hex) {
    return hex[1].match(/[\da-f]{2}/gi)
      .map((value) => Number.parseInt(value, 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  }

  const oklch = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)$/i.exec(color);
  assert.ok(oklch, `Color no soportado: ${color}`);
  const lightness = Number(oklch[1]) / 100;
  const chroma = Number(oklch[2]);
  const hue = Number(oklch[3]) * Math.PI / 180;
  const a = chroma * Math.cos(hue);
  const b = chroma * Math.sin(hue);
  const l = (lightness + (0.3963377774 * a) + (0.2158037573 * b)) ** 3;
  const m = (lightness - (0.1055613458 * a) - (0.0638541728 * b)) ** 3;
  const s = (lightness - (0.0894841775 * a) - (1.291485548 * b)) ** 3;
  return [
    (4.0767416621 * l) - (3.3077115913 * m) + (0.2309699292 * s),
    (-1.2684380046 * l) + (2.6097574011 * m) - (0.3413193965 * s),
    (-0.0041960863 * l) - (0.7034186147 * m) + (1.707614701 * s),
  ].map((value) => Math.max(0, Math.min(1, value)));
}

function luminance(color) {
  const [red, green, blue] = linearChannels(color);
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function assertNormalTextContrast(tokens, foreground, backgrounds) {
  for (const background of backgrounds) {
    const ratio = contrast(resolveToken(tokens, foreground), resolveToken(tokens, background));
    assert.ok(
      ratio >= 4.5,
      `${foreground} sobre ${background} tiene contraste ${ratio.toFixed(2)}:1`,
    );
  }
}

test("los tokens de texto secundario cumplen WCAG AA en tema claro", () => {
  const tokens = variables(":root");
  for (const foreground of ["--ink", "--ink-soft", "--ink-muted", "--accent-strong", "--success", "--danger"]) {
    assertNormalTextContrast(tokens, foreground, ["--canvas", "--surface"]);
  }
  assertNormalTextContrast(tokens, "--danger", ["--danger-soft"]);
  assertNormalTextContrast(tokens, "--warning", ["--warning-soft"]);
});

test("los tokens de texto secundario cumplen WCAG AA en tema oscuro", () => {
  const tokens = variables(':root[data-theme="dark"]');
  const baseTokens = { ...variables(":root"), ...tokens };
  for (const foreground of ["--ink", "--ink-soft", "--ink-muted", "--accent-strong", "--success", "--danger"]) {
    assertNormalTextContrast(baseTokens, foreground, ["--canvas", "--surface"]);
  }
  assertNormalTextContrast(baseTokens, "--danger", ["--danger-soft"]);
  assertNormalTextContrast(baseTokens, "--warning", ["--warning-soft"]);
});
