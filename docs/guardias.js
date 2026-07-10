import { renderMarkdown } from "./markdown.js";

const host = document.querySelector("#guardias-content");

async function init() {
  if (!host) return;
  try {
    const response = await fetch("GUARDIAS.md", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderMarkdown(await response.text(), host);
    const duplicateHeading = host.querySelector("h1");
    if (duplicateHeading) duplicateHeading.remove();
  } catch (error) {
    host.replaceChildren();
    const message = document.createElement("p");
    message.textContent = "No fue posible cargar la guía de guardias y plazos.";
    host.append(message);
    console.error(error);
  }
}

init();
