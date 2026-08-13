/**
 * Devuelve el primer o último día real de la fila semanal (lunes a domingo)
 * que contiene `day`. Los espacios de relleno del inicio y final del mes no
 * son días navegables, así que el resultado se limita al rango del mes.
 */
export function weekBoundaryDay(day, leadingPads, totalDays, boundary) {
  if (!Number.isInteger(day) || !Number.isInteger(leadingPads) || !Number.isInteger(totalDays)) {
    throw new TypeError("day, leadingPads y totalDays deben ser enteros");
  }
  if (day < 1 || day > totalDays) {
    throw new RangeError("day debe pertenecer al mes");
  }
  if (leadingPads < 0 || leadingPads > 6 || totalDays < 1) {
    throw new RangeError("leadingPads o totalDays fuera de rango");
  }
  if (boundary !== "start" && boundary !== "end") {
    throw new TypeError('boundary debe ser "start" o "end"');
  }

  const gridIndex = leadingPads + day - 1;
  const weekStartIndex = gridIndex - (gridIndex % 7);
  const boundaryIndex = boundary === "start" ? weekStartIndex : weekStartIndex + 6;
  const boundaryDay = boundaryIndex - leadingPads + 1;
  return Math.min(totalDays, Math.max(1, boundaryDay));
}
