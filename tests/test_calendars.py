from app.calendars import validate_calendars_payload


def valid_payload() -> dict:
    return {
        "generated_at": "2026-07-07T12:00:00+00:00",
        "organs": [
            {
                "id": "tfja",
                "name": "Tribunal Federal de Justicia Administrativa",
                "kind": "jurisdiccional",
                "source_page": "https://www.tfja.gob.mx/servicios/dinh2026/",
            }
        ],
        "days_by_organ": {
            "tfja": [
                {
                    "date": "2026-02-02",
                    "status": "inhabil",
                    "reason": "Conmemoración del 5 de febrero",
                    "source_url": "https://www.tfja.gob.mx/servicios/dinh2026/",
                    "analysis": "El Acuerdo SS/2/2026 declara inhábil el día.",
                    "verified": True,
                    "publicacion": "dof",
                    "acuerdo": "Acuerdo SS/2/2026, DOF 30/01/2026.",
                }
            ]
        },
    }


def test_valid_payload_passes():
    assert validate_calendars_payload(valid_payload()) == []


def test_rejects_weekend_and_bad_status_and_unknown_organ():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"].append(
        {
            "date": "2026-02-07",  # sábado
            "status": "festivo",
            "reason": "x",
            "source_url": "ftp://x",
            "analysis": "y",
            "verified": "sí",
        }
    )
    payload["days_by_organ"]["scjn"] = []

    errors = validate_calendars_payload(payload)

    joined = "\n".join(errors)
    assert "fin de semana" in joined
    assert "'inhabil' o 'vacaciones'" in joined
    assert "URL absoluta" in joined
    assert "verified debe ser booleano" in joined
    assert "no declarado: scjn" in joined


def test_rejects_duplicate_dates_and_missing_days_key():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"].append(dict(payload["days_by_organ"]["tfja"][0]))
    payload["organs"].append(
        {"id": "sat", "name": "SAT", "kind": "administrativa", "source_page": "https://sat.gob.mx"}
    )

    errors = validate_calendars_payload(payload)

    joined = "\n".join(errors)
    assert "duplicada" in joined
    assert "falta days_by_organ para el órgano sat" in joined


def test_accepts_payload_with_guardia_fields():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["guardia"] = True
    payload["days_by_organ"]["tfja"][0]["guardia_detalle"] = (
        "Guardia con oficialía de partes conforme al Acuerdo SS/12/2026."
    )

    assert validate_calendars_payload(payload) == []


def test_accepts_pendiente_publicacion_with_empty_acuerdo():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["publicacion"] = "pendiente"
    payload["days_by_organ"]["tfja"][0]["acuerdo"] = ""

    assert validate_calendars_payload(payload) == []


def test_rejects_invalid_publicacion_value():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["publicacion"] = "sitio_web"

    errors = validate_calendars_payload(payload)

    assert any("'dof', 'web_oficial' o 'pendiente'" in error for error in errors)


def test_rejects_empty_acuerdo_when_publicacion_is_not_pendiente():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["publicacion"] = "dof"
    payload["days_by_organ"]["tfja"][0]["acuerdo"] = ""

    errors = validate_calendars_payload(payload)

    assert any("acuerdo no puede quedar vacío" in error for error in errors)


def test_rejects_missing_acuerdo_field():
    payload = valid_payload()
    del payload["days_by_organ"]["tfja"][0]["acuerdo"]

    errors = validate_calendars_payload(payload)

    assert any("acuerdo debe ser una cadena" in error for error in errors)


def test_rejects_empty_guardia_detalle_when_present():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["guardia_detalle"] = "   "

    errors = validate_calendars_payload(payload)

    assert any("guardia_detalle debe ser una cadena no vacía" in error for error in errors)


def test_rejects_non_boolean_guardia():
    payload = valid_payload()
    payload["days_by_organ"]["tfja"][0]["guardia"] = "sí"

    errors = validate_calendars_payload(payload)

    assert any("guardia debe ser booleano" in error for error in errors)
