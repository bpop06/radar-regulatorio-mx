# Capa editorial Codex

Este nombre de archivo se conserva para no romper enlaces históricos. La
operación vigente está documentada en [OPERATIONS.md](OPERATIONS.md).

Codex lee únicamente evidencia oficial y aplica títulos, teaser, resumen y
cuerpo estructurado mediante `python -m app.cli apply-editorial`. Python no
llama APIs de modelos. El comando valida el lote completo antes de reemplazar
artefactos; si una edición no tiene sustento, el ítem permanece públicamente
como `needs_review`.

No se infieren tratados, efectos jurídicos, resultados, montos o razonamientos
ausentes de la fuente. Una editorial `complete` se conserva sólo mientras su
`content_hash` siga intacto.
