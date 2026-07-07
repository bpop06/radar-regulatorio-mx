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
