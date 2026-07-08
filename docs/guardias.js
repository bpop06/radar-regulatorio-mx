/* Página "Guardias y plazos" (v7 QA #14) — carga y renderiza docs/GUARDIAS.md con el
 * mini-renderer compartido (window.Radar.renderMarkdown, ver markdown.js). Sin datos
 * dinámicos: es contenido editorial estático. El .md vive junto a este archivo en
 * docs/, así que el fetch es relativo a esta misma carpeta. */
(function () {
  "use strict";

  const host = document.querySelector("#guardias-content");
  if (!host) return;

  fetch("GUARDIAS.md", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    })
    .then((markdown) => {
      if (window.Radar) {
        window.Radar.renderMarkdown(markdown, host);
      } else {
        host.replaceChildren();
        const pre = document.createElement("pre");
        pre.textContent = markdown;
        host.append(pre);
      }
    })
    .catch((error) => {
      host.replaceChildren();
      const p = document.createElement("p");
      p.textContent = "No fue posible cargar la guía de guardias y plazos.";
      host.append(p);
      console.error(error);
    });
})();
