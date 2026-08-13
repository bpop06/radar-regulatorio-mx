# Operación de Radar Regulatorio MX

## Responsable único

Una automatización de proyecto de Codex ejecuta dos cortes diarios, a las
10:30 y 18:30, hora de Ciudad de México, incluidos fines de semana. Siempre
parte de un worktree limpio. Las automatizaciones locales anteriores quedan
pausadas durante el relanzamiento y sólo se eliminan después de dos cortes
productivos consecutivos.

La rama diaria es `codex/radar-daily`. La automatización crea o actualiza un
solo pull request abierto; nunca crea PR competidores y nunca empuja a
`main`.

## Secuencia de un corte

1. Actualizar `main` y recrear o poner al día el worktree aislado.
2. Recolectar las 18 fuentes con límites de tiempo y reintentos acotados.
   El payload registra `collection_notes.dof_previous_day_reviewed=true` sólo
   si la ventana incluyó el día anterior y DOF validó transporte y estructura
   sin degradación.
3. Conservar la editorial completa cuyo `content_hash` no cambió.
4. Revisar evidencia oficial de ítems nuevos o modificados y aplicar cambios
   con `apply-editorial`. Lo no sustentado permanece `needs_review`.
5. Generar el corte completo en staging, validar contrato, referencias y
   hashes, y hacer un único reemplazo.
6. Ejecutar Ruff, pytest, validación del corte y calendarios, pruebas
   JavaScript, Playwright y axe.
7. Actualizar el PR diario y habilitar auto-merge sólo con todos los checks
   requeridos en verde.
8. Tras el merge, verificar HTTP 200, `cut_id`, hashes, enlaces oficiales y
   una ficha histórica en GitHub Pages.
9. Informar totales completos/pendientes, fuentes degradadas, PR, merge,
   despliegue y tiempo de publicación.

## Gates

El relanzamiento inicial requiere:

- 18 fuentes certificadas, sin falsos verdes;
- dos cortes sombra consecutivos deterministas;
- checks `Python` y `Frontend` completamente verdes;
- manifiesto, edición, archivo, fichas y estado con el mismo `cut_id`;
- cero diferencias en una segunda generación con las mismas entradas.

El gate se ejecuta sin escribir artefactos:

```bash
python -m app.cli certify-sources --days 31
```

Durante el relanzamiento, el helper local lo exige antes de generar el corte:

```bash
RADAR_REQUIRE_RELAUNCH_CERTIFICATION=1 scripts/collect_daily.sh
```

Antes del primer corte candidato v8 se ejecuta **una sola vez** la
reconstrucción del inventario permanente de CIADI y del Secretariado T-MEC:

```bash
.venv/bin/python -m app.cli collect --days 31 --rebuild-stateful-history
```

La opción ignora los snapshots existentes sólo durante esa ejecución, consulta
las fichas/tablas oficiales y sustituye identidades legacy por
`source + case_number`. El inventario se escribe en `data/items`, notas e
índices mensuales, pero no en las novedades de `publications.json` ni en las
señales de `edition.json`. Estado y fichas se confirman juntos; si falla la
validación, no cambia ninguno. Un ensayo seguro puede añadir `--dry-run` y no
escribe artefactos ni snapshots.

`--rebuild-stateful-history` no se incorpora a `scripts/collect_daily.sh`, a la
automatización diaria ni a sus dos horarios. Después del bootstrap, los cortes
normales vuelven a comparar contra `docs/data/state/{icsid,tmec}.json`.

Las fichas históricas CIJ/CPI creadas antes de los extractores estrictos se
enriquecen mediante otro comando **one-shot**, sin consultar la red:

```bash
.venv/bin/python -m app.cli rebuild-international-history --dry-run
.venv/bin/python -m app.cli rebuild-international-history
```

El comando sólo lee `official_title`, `description` y `url` de los envelopes
enumerados y autenticados por el manifiesto vigente. Conserva cualquier campo
no vacío, añade únicamente metadatos literales reconocidos por los extractores
CIJ/CPI y transporta los cambios como `_historical_items`; por ello no inserta
casos antiguos en `publications.json` ni en las señales de `edition.json`. Un
cambio de evidencia recalcula `content_hash` y devuelve la editorial a
`needs_review`. El ensayo `--dry-run` no escribe ningún artefacto.

Después de los dos cortes productivos de aceptación, la variable se omite. Así,
una fuente aislada en estado `degraded` se informa en el corte sin bloquear a
las demás; sólo el fallo total conserva el corte anterior.

Si GitHub Actions termina en `startup_failure`, el PR no se integra. La
automatización informa el incidente y conserva producción. No se sustituyen
los checks por una revisión manual.

### Recuperar un bloqueo de facturación sin contratar un plan

GitHub documenta que una cuenta bloqueada por facturación puede bajar a
**GitHub Free** y conservar estas funciones avanzadas en repositorios públicos.
La ruta operativa es:

1. Mantener el PR abierto y no fusionarlo manualmente; guardar la URL de la
   ejecución y el texto exacto de `startup_failure`.
2. En la cuenta propietaria, abrir **Settings → Billing & Licensing** y bajar
   el plan personal u organizacional a **GitHub Free**. No hace falta contratar
   Pro/Team ni añadir un método de pago para la arquitectura de este proyecto.
3. Confirmar que el plan activo aparece como Free y que
   `bpop06/radar-regulatorio-mx` continúa público. Verificar además que Actions
   sigue habilitado y que Pages conserva `main` + `/docs`.
4. Volver a ejecutar el workflow fallido. No habilitar larger runners, merge
   queue ni un despliegue alterno para intentar sortear el bloqueo.
5. Si el mismo bloqueo persiste después de confirmar Free, abrir un caso de
   **cuenta/facturación** en el portal de GitHub Support. Adjuntar usuario,
   repositorio, URL y hora de la ejecución, mensaje exacto y captura donde se
   vea el plan Free. Las cuentas Free pueden reportar problemas de cuenta.

Hasta que `Python` y `Frontend` inicien y terminen en verde, el relanzamiento
queda detenido y Pages conserva el último corte aprobado.

Referencias oficiales: [desbloquear una cuenta][locked], [bajar a GitHub
Free][downgrade] y [contactar GitHub Support][support].

[locked]: https://docs.github.com/billing/how-tos/troubleshooting/locked-account
[downgrade]: https://docs.github.com/billing/how-tos/manage-plan-and-licenses/downgrade-plan
[support]: https://docs.github.com/support/contacting-github-support

## Estados públicos

- **Completa:** evidencia y editorial validadas.
- **Revisión pendiente:** evidencia disponible, interpretación aún no
  sustentada.
- **Cobertura degradada:** al menos una fuente no pudo certificarse en el
  corte; se muestran las fuentes afectadas.
- **Sin novedades:** corte vigente y válido con cero publicaciones nuevas.
- **Corte pendiente:** el último corte excedió el SLA o aún no terminó.

Un cero sólo es válido si HTTP, tipo de contenido y estructura pasan. HTML en
un endpoint de feed, una estructura irreconocible o un error parcial se
reportan como `degraded`.

## Retiros y rollback

Una ficha certificada como falso positivo se retira mediante el único canal
transaccional autorizado; nunca se borran artefactos a mano:

```bash
.venv/bin/python -m app.cli retract-items SOURCE:ID \
  --reason "Motivo verificable" --input docs/data/publications.json
```

El comando exige un corte v8 válido, elimina el registro de la ventana móvil,
el inventario permanente, el índice mensual y la nota estática, y confirma el
motivo junto con el nuevo `cut_id`. El estado de retiros sólo se consume si su
envelope está enumerado y hasheado por el manifiesto vigente. Para revertir el
retiro de forma igualmente transaccional:

```bash
.venv/bin/python -m app.cli restore-items SOURCE:ID \
  --input docs/data/publications.json
```

Ambos comandos fallan sin escribir si el identificador no existe, el estado
fue alterado o cualquier validación del corte no pasa.

## SLA y alertas

Cada corte debe aparecer en Pages dentro de 90 minutos. Se alerta cuando:

- el despliegue no refleja el `cut_id` esperado;
- un check falla o no inicia;
- una fuente queda degradada;
- el corte rebasa 90 minutos;
- los hashes públicos no coinciden con el manifiesto.

Un día sin publicaciones produce una edición vigente **Sin novedades**; nunca
se reutiliza una portada vieja como si fuera el corte actual.

La automatización consulta GitHub inmediatamente después de subir la rama y
durante la ventana de despliegue. Si no existe una ejecución de CI, la
ejecución termina como `startup_failure` operativo: no integra ni reintenta por
otra vía. La comprobación de frescura se puede ejecutar con
`python -m app.cli validate --input docs/data/publications.json
--require-v8 --max-age-hours 1.5`. El chequeo diario de `main` permite 25
horas para no fallar antes del primer corte; la verificación posterior a cada
corte y la portada mantienen el SLA estricto de 90 minutos.

## Recuperación

- Una corrida fallida o `--dry-run` no cambia datos ni estado.
- Un fallo total de fuentes conserva el corte anterior.
- No se reescribe la historia de `main`; una regresión se revierte mediante
  PR.
- El helper `scripts/collect_daily.sh` sólo genera y valida en una rama
  limpia. Nunca commitea ni publica.
- El workflow `Shadow regulatory collection` es diagnóstico manual y de sólo
  lectura sobre el repositorio.
