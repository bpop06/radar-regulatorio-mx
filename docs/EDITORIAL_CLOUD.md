# Rutina diaria integral en la nube (Claude)

Todo el ciclo diario del radar corre en la nube con una rutina de Claude y la
suscripción del propietario — sin API de pago, sin Codex y **sin procesar ni
guardar nada en máquinas locales**.

## Qué hace cada día (11:00 CDMX / 17:00 UTC)

1. **Recolecta** las 10 fuentes oficiales ejecutando el pipeline determinista
   del repo: `python -m app.cli collect` consulta DOF, SNICE, PLATIICA,
   Diputados, Senado, IMPI, gob.mx APF, ONU Noticias, USTR y Trade.gov,
   clasifica, valida y escribe `docs/data/publications.json`. La recolección
   es **código, no navegación del agente**: el modelo nunca "lee" páginas web
   crudas; procesa los campos ya extraídos y validados por el pipeline.
2. **Editorializa** los ítems nuevos (`ai_generated: false`) con subagentes
   de razonamiento opus:
   - **Titular de noticia**: órgano + verbo + qué cambió; nunca abre con
     número de oficio/acuerdo.
   - **Resumen** de entre **40 y 80 palabras** (el rango que exige el
     validador; ni 39 ni 81).
   - **`card_body`** con las tres secciones fijas:
     - **## Qué se publicó**: qué acto se publicó, **sin su número** ni clave
       y con el **nombre corto** del órgano (SHCP, SAT, IMPI...), no el nombre
       legal completo.
     - **## Sustancia**: el significado esencial razonado en 4 a 8 frases (qué
       cambia, a quién obliga y por qué importa), no un recorte del título.
     - **## Fuente**: enlace a la publicación oficial.

   Todo se aplica por el único canal permitido,
   `python -m app.cli apply-editorial` (validación dura, todo-o-nada).
3. **Audita** el corte (guía read-only `.agents/skills/radar-diario/SKILL.md`)
   y revisa si el corte trae acuerdos de días inhábiles que ameriten
   actualizar los calendarios (solo lo reporta).
4. **Publica**: commit únicamente de los datos del corte —
   `docs/data/publications.json` y `docs/data/icsid_snapshot.json` (el
   estado {caso: estatus} del CIADI, que debe sobrevivir entre sesiones
   efímeras para que solo se emitan casos nuevos o con cambio de
   estatus)— (`chore: radar diario`) y push a `main`; GitHub Pages
   actualiza el sitio. El historial de git es el archivo histórico del
   radar (un corte por commit); no hay base de datos local.
5. Termina con el **parte diario** (notificación push).

## Requisito del entorno: red hacia las fuentes

El entorno de Claude Code donde corre la rutina debe permitir salida HTTPS a
los dominios oficiales del recolector:

```
www.dof.gob.mx          www.snice.gob.mx        platiica.economia.gob.mx
gaceta.diputados.gob.mx transparenciaparlamentaria.senado.gob.mx
www.impi.gob.mx         www.gob.mx              news.un.org
ustr.gov                blog.trade.gov
```

Se configura en claude.ai/code → ajustes del entorno → acceso a red
(allowlist con esos dominios, o red completa). Sin esa apertura, `collect`
reporta todas las fuentes en error y la rutina no publica datos nuevos
(publica solo editorial pendiente, si la hay).

## Cómo consulta las fuentes (endpoints primero)

Regla del repo (AGENTS.md): interfaces estructuradas oficiales primero; HTML
solo donde no existe API. Estado actual: DOF (RSS oficial), PLATIICA (REST),
Senado (JSON de transparencia), ONU/USTR/Trade.gov (RSS) son endpoints;
SNICE, Gaceta de Diputados, IMPI y gob.mx no publican API y se leen de su
HTML público con parsers tolerantes y probados con fixtures
(`docs/SOURCE_AUDIT.md` documenta cada interfaz y sus límites).

## Medidas de seguridad del ciclo

- **TLS verificado** en todas las solicitudes (truststore), User-Agent
  identificado, timeouts y reintentos acotados, una corrida al día.
- **Recolectores aislados**: una fuente caída o malformada no afecta a las
  demás; XML parseado sin entidades externas (sin XXE); el contenido nunca se
  ejecuta.
- **Gate de validación duro** antes de publicar: contrato completo, resúmenes
  de 40 a 80 palabras, «Qué se publicó» sin número de acto, URLs http(s),
  `detail_url` interno.
- **Canal editorial acotado**: el agente solo puede modificar
  título/resumen/cuerpo de ítems existentes vía `apply-editorial`.
- **Publicación mínima**: la rutina commitea solo archivos de datos bajo
  `docs/data/` (el corte y el snapshot del CIADI); nunca código, nunca
  force-push.
- El sitio publicado valida esquemas de URL en el cliente y no usa
  `innerHTML`.

## Operación de la rutina

Administrable desde cualquier sesión de Claude conectada a la cuenta
(`list_triggers`, `update_trigger` para pausar o cambiar horario,
`fire_trigger` para dispararla ya, `delete_trigger`), o en lenguaje natural:
«pausa/dispara la rutina del radar».

## Respaldo manual

Si un día la rutina no corre, cualquier máquina con red puede publicar el
corte: `scripts/collect_daily.sh` (recolecta, valida y commitea solo el
archivo de datos). La editorial pendiente la tomará la siguiente corrida de
la rutina.
