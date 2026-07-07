from __future__ import annotations

from datetime import date
from typing import Any

VALID_STATUSES = {"inhabil", "vacaciones"}
# La fuente principal de un día es el TEXTO del acuerdo publicado (DOF u
# oficial); el sitio web del órgano solo confirma/orienta. "pendiente" cubre
# los días que el sitio anuncia pero cuyo acuerdo aún no se publica.
VALID_PUBLICACION_KINDS = {"dof", "web_oficial", "pendiente"}
REQUIRED_ORGAN_FIELDS = ("id", "name", "kind", "source_page")
REQUIRED_DAY_FIELDS = (
    "date",
    "status",
    "reason",
    "source_url",
    "analysis",
    "verified",
    "publicacion",
)


def validate_calendars_payload(payload: dict[str, Any]) -> list[str]:
    """Valida el contrato de docs/data/calendars.json. Devuelve la lista de
    errores (vacía = válido). Los fines de semana ordinarios no deben venir en
    los datos: el cliente los deriva."""
    errors: list[str] = []

    organs = payload.get("organs")
    days_by_organ = payload.get("days_by_organ")
    if not isinstance(organs, list) or not organs:
        errors.append("organs debe ser una lista no vacía")
        return errors
    if not isinstance(days_by_organ, dict):
        errors.append("days_by_organ debe ser un objeto {organ_id: [días]}")
        return errors

    organ_ids: set[str] = set()
    for index, organ in enumerate(organs):
        if not isinstance(organ, dict):
            errors.append(f"organs[{index}] debe ser un objeto")
            continue
        for field in REQUIRED_ORGAN_FIELDS:
            if not isinstance(organ.get(field), str) or not organ[field].strip():
                errors.append(f"organs[{index}].{field} debe ser una cadena no vacía")
        organ_id = organ.get("id")
        if isinstance(organ_id, str):
            if organ_id in organ_ids:
                errors.append(f"organs[{index}].id duplicado: {organ_id}")
            organ_ids.add(organ_id)

    for organ_id in days_by_organ:
        if organ_id not in organ_ids:
            errors.append(f"days_by_organ trae un órgano no declarado: {organ_id}")
    for organ_id in organ_ids:
        if organ_id not in days_by_organ:
            errors.append(f"falta days_by_organ para el órgano {organ_id}")

    for organ_id, days in days_by_organ.items():
        if not isinstance(days, list):
            errors.append(f"days_by_organ.{organ_id} debe ser una lista")
            continue
        seen_dates: set[str] = set()
        for index, day in enumerate(days):
            prefix = f"days_by_organ.{organ_id}[{index}]"
            if not isinstance(day, dict):
                errors.append(f"{prefix} debe ser un objeto")
                continue
            for field in REQUIRED_DAY_FIELDS:
                if field == "verified":
                    if not isinstance(day.get(field), bool):
                        errors.append(f"{prefix}.verified debe ser booleano")
                elif not isinstance(day.get(field), str) or not day[field].strip():
                    errors.append(f"{prefix}.{field} debe ser una cadena no vacía")

            raw_date = day.get("date")
            if isinstance(raw_date, str):
                try:
                    parsed = date.fromisoformat(raw_date)
                except ValueError:
                    errors.append(f"{prefix}.date no es una fecha ISO: {raw_date}")
                else:
                    if parsed.weekday() >= 5:
                        errors.append(
                            f"{prefix}.date cae en fin de semana ({raw_date}); "
                            "los sábados/domingos se derivan en el cliente"
                        )
                    if raw_date in seen_dates:
                        errors.append(f"{prefix}.date duplicada: {raw_date}")
                    seen_dates.add(raw_date)

            if day.get("status") not in VALID_STATUSES:
                errors.append(f"{prefix}.status debe ser 'inhabil' o 'vacaciones'")

            source_url = day.get("source_url")
            if isinstance(source_url, str) and not source_url.startswith(
                ("http://", "https://")
            ):
                errors.append(f"{prefix}.source_url debe ser una URL absoluta")

            publicacion = day.get("publicacion")
            if isinstance(publicacion, str) and publicacion not in VALID_PUBLICACION_KINDS:
                errors.append(
                    f"{prefix}.publicacion debe ser 'dof', 'web_oficial' o 'pendiente'"
                )

            # `acuerdo` es obligatorio como campo (cita del acuerdo publicado)
            # pero solo puede quedar vacío cuando el día está marcado como
            # publicación pendiente en el DOF.
            acuerdo = day.get("acuerdo")
            if not isinstance(acuerdo, str):
                errors.append(f"{prefix}.acuerdo debe ser una cadena")
            elif not acuerdo.strip() and publicacion != "pendiente":
                errors.append(
                    f"{prefix}.acuerdo no puede quedar vacío salvo publicacion='pendiente'"
                )

            if "guardia" in day and not isinstance(day.get("guardia"), bool):
                errors.append(f"{prefix}.guardia debe ser booleano")

            if "guardia_detalle" in day:
                guardia_detalle = day.get("guardia_detalle")
                if not isinstance(guardia_detalle, str) or not guardia_detalle.strip():
                    errors.append(f"{prefix}.guardia_detalle debe ser una cadena no vacía")

    return errors
