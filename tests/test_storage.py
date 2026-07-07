from app.storage import Storage


def sample_payload(generated_at: str, item_id: str = "dof:1") -> dict:
    return {
        "generated_at": generated_at,
        "lookback_days": 31,
        "total_items": 1,
        "sources": [
            {
                "source": "DOF",
                "status": "ok",
                "items_found": 5,
                "attempts": 1,
                "error": None,
            }
        ],
        "items": [
            {
                "id": item_id,
                "published_at": "2026-07-03",
                "title": "Título de prueba",
            }
        ],
    }


def test_save_run_and_export_roundtrip(tmp_path):
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        run_id = storage.save_run(sample_payload("2026-07-03T10:00:00+00:00"))
        exported = storage.export_payload()

    assert run_id == 1
    assert exported["generated_at"] == "2026-07-03T10:00:00+00:00"
    assert exported["total_items"] == 1
    assert exported["items"][0]["id"] == "dof:1"
    assert exported["sources"][0]["source"] == "DOF"


def test_documents_deduplicate_across_runs(tmp_path):
    db = tmp_path / "radar.sqlite3"
    with Storage(db) as storage:
        storage.save_run(sample_payload("2026-07-03T10:00:00+00:00"))
        storage.save_run(sample_payload("2026-07-04T10:00:00+00:00"))
        report = storage.report()
        exported = storage.export_payload()

    assert report.runs == 2
    assert report.documents == 1
    assert exported["generated_at"] == "2026-07-04T10:00:00+00:00"


def test_report_on_empty_database(tmp_path):
    with Storage(tmp_path / "radar.sqlite3") as storage:
        report = storage.report()

    assert report.runs == 0
    assert report.documents == 0
    assert report.last_generated_at is None
