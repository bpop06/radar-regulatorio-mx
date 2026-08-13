from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from app.edition import item_key, write_site_artifacts_legacy
from app.models import Candidate
from app.pipeline import _publication_from_classified
from app.relevance import classify
from app.sources.icsid import IcsidCollector
from app.sources.tmec import TmecCollector

ICSID_DETAIL = """
<div class="case__detail">
  <h1>Canadian Pacific Kansas City Limited v. United Mexican States
  (ICSID Case No. ARB/26/19)</h1>
  <ul>
    <li class="row"><label>Subject of Dispute:</label>
      <div class="rightcol">Railroad concession</div></li>
    <li class="row"><label>Economic Sector:</label>
      <div class="rightcol">Transportation</div></li>
    <li class="row"><label>Instrument(s) Invoked:</label>
      <div class="rightcol">Comprehensive and Progressive Agreement for
      Trans-Pacific Partnership (CPTPP)</div></li>
    <li class="row"><label>Date Registered:</label>
      <div class="rightcol">April 27, 2026</div></li>
    <li class="row"><label>Status of Proceeding:</label>
      <div class="rightcol">Pending</div></li>
    <li class="row"><label>Latest Development:</label>
      <div class="rightcol">July 3, 2026 - Arbitrator appointed.</div></li>
  </ul>
</div>
"""


TMEC_PAGE = """
<table>
<tr><th>Panel Review Number</th><th>Title</th><th>Instrument Invoked</th>
<th>Dispute Settlement Mechanism</th><th>Complaining Party</th>
<th>Responding Party</th><th>Third Party</th><th>Panel Composition</th>
<th>Date of Panel Request</th><th>Status</th><th>Date of Final Report</th></tr>
<tr><td>MEX-USA-2024-31A-04</td><td>Rapid Response Labor Panel</td>
<td>Canada United States Mexico Agreement (CUSMA)</td><td>Annex 31-A</td>
<td>United States</td><td>Mexico</td><td>N/A</td>
<td>Adolfo Ciudad, Chair / Janice Bellace, panelist</td>
<td>2024-12-18</td><td>Active</td><td>N/A</td></tr>
</table>
"""


def _publication(candidate: Candidate, generated_at: datetime) -> dict[str, object]:
    return _publication_from_classified(classify(candidate), generated_at).to_dict()


def _empty_cut(*historical: dict[str, object]) -> dict[str, object]:
    return {
        "generated_at": "2026-08-12T16:30:00+00:00",
        "lookback_days": 31,
        "total_items": 0,
        "sources": [
            {
                "source": "CIADI",
                "status": "ok",
                "items_found": 0,
                "error": None,
                "attempts": 1,
            },
            {
                "source": "Secretariado T-MEC",
                "status": "ok",
                "items_found": 0,
                "error": None,
                "attempts": 1,
            },
        ],
        "items": [],
        "_historical_items": list(historical),
        "_pending_state": {"icsid": {}, "tmec": {}},
    }


def _icsid_candidate() -> Candidate:
    url = (
        "https://icsid.worldbank.org/cases/case-database/case-detail"
        "?CaseNo=ARB%2F26%2F19"
    )
    return Candidate(
        source="CIADI",
        source_id="ARB/26/19",
        url=url,
        canonical_url=url,
        official_title=(
            "Canadian Pacific Kansas City Limited v. United Mexican States "
            "(ICSID Case No. ARB/26/19)"
        ),
        description="Instrumento invocado: CPTPP.",
        published_at=date(2026, 7, 3),
        official_published_at=date(2026, 7, 3),
        authority="Centro Internacional de Arreglo de Diferencias",
        document_type="Caso de arbitraje de inversión",
        case_number="ARB/26/19",
        case_parties="Canadian Pacific Kansas City Limited v. United Mexican States",
        case_status="Pending",
        case_treaty=(
            "Comprehensive and Progressive Agreement for Trans-Pacific Partnership (CPTPP)"
        ),
        case_claim="Railroad concession",
        official_evidence={
            "case_detail": url,
            "instrument_invoked": (
                "Comprehensive and Progressive Agreement for Trans-Pacific Partnership "
                "(CPTPP)"
            ),
            "status": "Pending",
        },
    )


def _tmec_candidate() -> Candidate:
    proposal = TmecCollector.propose(
        [(TMEC_PAGE, TmecCollector.urls[1])],
        previous=None,
        today=date(2026, 8, 12),
    )
    return proposal.historical_candidates[0]


def _icsid_inventory(numbers: list[str]) -> dict[str, object]:
    return {
        "data": {
            "GetAllCasesResult": [
                {
                    "caseno": number,
                    "claimant": f"Claimant {number}",
                    "respondent": "United Mexican States",
                    "status": "Concluded",
                }
                for number in numbers
            ]
        }
    }


def _tmec_inventory(numbers: list[str]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{number}</td><td>Panel {number}</td><td>CUSMA</td>"
        "<td>Chapter 31</td><td>United States</td><td>Mexico</td><td>N/A</td>"
        "<td>Persona panelista</td><td>2024-12-18</td><td>Active</td><td>N/A</td>"
        "</tr>"
        for number in numbers
    )
    return (
        "<table><tr><th>Panel Review Number</th><th>Title</th>"
        "<th>Instrument Invoked</th><th>Dispute Settlement Mechanism</th>"
        "<th>Complaining Party</th><th>Responding Party</th><th>Third Party</th>"
        "<th>Panel Composition</th><th>Date of Panel Request</th><th>Status</th>"
        f"<th>Date of Final Report</th></tr>{rows}</table>"
    )


def test_icsid_bootstrap_enriches_permanent_history_without_current_news(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "state" / "icsid.json"
    api_payload = {
        "data": {
            "GetAllCasesResult": [
                {
                    "caseno": "ARB/26/19",
                    "claimant": "Canadian Pacific Kansas City Limited",
                    "respondent": "United Mexican States",
                    "status": "Pending",
                    "subject": "",
                    "econsector": "",
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/all/cases":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                json=api_payload,
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=utf-8"},
            text=ICSID_DETAIL,
        )

    async def run() -> tuple[list[Candidate], IcsidCollector]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = IcsidCollector(client, snapshot_path=snapshot)
            current = await collector.collect(date(2026, 8, 1))
            return current, collector

    current, collector = asyncio.run(run())

    assert current == []
    assert not snapshot.exists()
    assert collector.pending_state is not None
    assert len(collector.historical_candidates) == 1
    historical = collector.historical_candidates[0]
    assert historical.source_id == historical.case_number == "ARB/26/19"
    assert historical.case_treaty.endswith("(CPTPP)")
    assert "T-MEC" not in historical.case_treaty
    assert historical.official_evidence["instrument_invoked"].endswith("(CPTPP)")


def test_force_bootstrap_ignores_existing_state_without_writing_it(tmp_path: Path) -> None:
    snapshot = tmp_path / "icsid.json"
    original = {"ARB/26/19": "Pending"}
    snapshot.write_text(json.dumps(original), encoding="utf-8")
    api_payload = {
        "data": {
            "GetAllCasesResult": [
                {
                    "caseno": "ARB/26/19",
                    "claimant": "Canadian Pacific Kansas City Limited",
                    "respondent": "United Mexican States",
                    "status": "Pending",
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/all/cases":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                json=api_payload,
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html"},
            text=ICSID_DETAIL,
        )

    async def run() -> tuple[list[Candidate], IcsidCollector]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = IcsidCollector(
                client,
                snapshot_path=snapshot,
                force_bootstrap=True,
            )
            return await collector.collect(date(2026, 8, 1)), collector

    current, collector = asyncio.run(run())

    assert current == []
    assert [item.case_number for item in collector.historical_candidates] == ["ARB/26/19"]
    assert json.loads(snapshot.read_text(encoding="utf-8")) == original


def test_icsid_inventory_guard_blocks_empty_and_shrink_then_recovers(
    tmp_path: Path,
) -> None:
    numbers = ["ARB/20/1", "ARB/20/2", "ARB/20/3", "ARB/20/4"]
    snapshot = tmp_path / "icsid.json"
    snapshot.write_text(
        json.dumps({number: "Concluded" for number in numbers}),
        encoding="utf-8",
    )

    async def run(payload: dict[str, object]) -> IcsidCollector:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                json=payload,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = IcsidCollector(client, snapshot_path=snapshot)
            assert await collector.collect(date(2026, 8, 1)) == []
            return collector

    empty = asyncio.run(run(_icsid_inventory([])))
    assert empty.diagnostics.status == "degraded"
    assert empty.pending_state is None
    assert "0 casos frente a 4" in empty.diagnostics.warnings[0]

    shrink = asyncio.run(run(_icsid_inventory(numbers[:1])))
    assert shrink.diagnostics.status == "degraded"
    assert shrink.pending_state is None

    recovered = asyncio.run(run(_icsid_inventory(numbers)))
    assert recovered.diagnostics.status == "certified"
    assert recovered.pending_state is not None
    assert set(recovered.pending_state) == set(numbers)
    assert json.loads(snapshot.read_text(encoding="utf-8")) == {
        number: "Concluded" for number in numbers
    }


def test_tmec_inventory_guard_blocks_empty_and_shrink_then_recovers(
    tmp_path: Path,
) -> None:
    numbers = [
        "MEX-USA-2024-31-01",
        "MEX-USA-2024-31-02",
        "MEX-USA-2024-31-03",
        "MEX-USA-2024-31-04",
    ]
    snapshot = tmp_path / "tmec.json"
    snapshot.write_text(
        json.dumps({number: "Active" for number in numbers}),
        encoding="utf-8",
    )

    async def run(payload: str) -> TmecCollector:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html"},
                text=payload,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = TmecCollector(client, snapshot_path=snapshot)
            assert await collector.collect(date(2026, 8, 1)) == []
            return collector

    empty = asyncio.run(run(_tmec_inventory([])))
    assert empty.diagnostics.status == "degraded"
    assert empty.pending_state is None
    assert "0 casos frente a 4" in empty.diagnostics.warnings[0]

    shrink = asyncio.run(run(_tmec_inventory(numbers[:1])))
    assert shrink.diagnostics.status == "degraded"
    assert shrink.pending_state is None

    recovered = asyncio.run(run(_tmec_inventory(numbers)))
    assert recovered.diagnostics.status == "certified"
    assert recovered.pending_state is not None
    assert set(recovered.pending_state) == set(numbers)
    assert json.loads(snapshot.read_text(encoding="utf-8")) == {
        number: "Active" for number in numbers
    }


def test_historical_transport_writes_only_permanent_artifacts(tmp_path: Path) -> None:
    generated_at = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    historical = _publication(_tmec_candidate(), generated_at)
    docs = tmp_path / "docs"
    publications_path = docs / "data/publications.json"

    manifest = write_site_artifacts_legacy(_empty_cut(historical), publications_path)

    publications = json.loads(publications_path.read_text(encoding="utf-8"))
    edition = json.loads((docs / "data/edition.json").read_text(encoding="utf-8"))
    assert publications["items"] == []
    assert publications["total_items"] == 0
    assert publications["edition"]["total_today"] == 0
    assert edition["signals"] == []
    assert "_historical_items" not in publications
    assert "_historical_items" not in edition

    item_id = "secretariado t-mec:MEX-USA-2024-31A-04"
    key = item_key(item_id)
    envelope = json.loads(
        (docs / "data/items" / f"{key}.json").read_text(encoding="utf-8")
    )
    item = envelope["item"]
    assert item["id"] == item_id
    assert item["case_status"] == "Active"
    assert "Adolfo Ciudad" not in item["case_status"]
    assert item["official_evidence"]["panel_composition"].startswith("Adolfo Ciudad")
    assert f"data/items/{key}.json" in manifest["artifacts"]


def test_legacy_case_ids_are_replaced_once_and_do_not_resurrect(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    publications_path = docs / "data/publications.json"
    legacy = _empty_cut()
    legacy["items"] = [
        {
            "id": "ciadi:49fc9ddbe9ff607f",
            "source_id": "49fc9ddbe9ff607f",
            "source": "CIADI",
            "url": (
                "https://icsid.worldbank.org/cases/case-database/case-detail"
                "?CaseNo=ARB%2F26%2F19"
            ),
            "official_title": (
                "Canadian Pacific Kansas City Limited v. United Mexican States "
                "(Caso CIADI No. ARB/26/19)"
            ),
            "description": "Tratado atribuido incorrectamente a T-MEC.",
            "published_at": "2026-07-03",
            "case_treaty": "T-MEC/TLCAN",
        },
        {
            "id": "secretariado t-mec:e095b482ffda104e",
            "source_id": "e095b482ffda104e",
            "source": "Secretariado T-MEC",
            "url": TmecCollector.urls[1],
            "official_title": (
                "Rapid Response Labor Panel (Panel MEX-USA-2024-31A-04)"
            ),
            "description": "Panel legado con columnas desplazadas.",
            "published_at": "2024-12-18",
            "case_status": "Adolfo Ciudad, Chair",
        },
    ]
    legacy["total_items"] = 2
    publications_path.parent.mkdir(parents=True)
    publications_path.write_text(json.dumps(legacy), encoding="utf-8")
    legacy_paths = [
        docs / "data/items" / f"{item_key('ciadi:49fc9ddbe9ff607f')}.json",
        docs / "notas" / f"{item_key('ciadi:49fc9ddbe9ff607f')}.html",
        docs
        / "data/items"
        / f"{item_key('secretariado t-mec:e095b482ffda104e')}.json",
        docs
        / "notas"
        / f"{item_key('secretariado t-mec:e095b482ffda104e')}.html",
    ]
    for legacy_item, item_path, note_path in zip(
        legacy["items"], legacy_paths[::2], legacy_paths[1::2], strict=True
    ):
        item_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        item_path.write_text(json.dumps({"item": legacy_item}), encoding="utf-8")
        note_path.write_text("nota legacy incorrecta", encoding="utf-8")
    (docs / "data/manifest.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    path.relative_to(docs).as_posix(): {} for path in legacy_paths
                }
            }
        ),
        encoding="utf-8",
    )
    assert all(path.exists() for path in legacy_paths)

    generated_at = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    corrected = _empty_cut(
        _publication(_icsid_candidate(), generated_at),
        _publication(_tmec_candidate(), generated_at),
    )
    write_site_artifacts_legacy(corrected, publications_path)
    # Una tercera corrida prueba que envelopes legacy huérfanos no vuelven a
    # entrar por el glob histórico.
    manifest = write_site_artifacts_legacy(corrected, publications_path)

    item_paths = [
        path
        for path in manifest["artifacts"]
        if path.startswith("data/items/") and path.endswith(".json")
    ]
    permanent = [
        json.loads((docs / path).read_text(encoding="utf-8"))["item"]
        for path in item_paths
    ]
    ids = {item["id"] for item in permanent}
    assert ids == {
        "ciadi:ARB/26/19",
        "secretariado t-mec:MEX-USA-2024-31A-04",
    }
    assert not any(item_id.endswith("49fc9ddbe9ff607f") for item_id in ids)
    assert not any(item_id.endswith("e095b482ffda104e") for item_id in ids)
    assert all(not path.exists() for path in legacy_paths)

    ciadi = next(item for item in permanent if item["source"] == "CIADI")
    tmec = next(item for item in permanent if item["source"] == "Secretariado T-MEC")
    assert ciadi["case_number"] == "ARB/26/19"
    assert ciadi["case_treaty"].endswith("(CPTPP)")
    assert "T-MEC" not in ciadi["case_treaty"]
    assert tmec["case_number"] == "MEX-USA-2024-31A-04"
    assert tmec["case_status"] == "Active"
    assert "Adolfo Ciudad" not in tmec["case_status"]

    archive_ids: list[str] = []
    for path in manifest["artifacts"]:
        if path.startswith("data/archive/") and path.endswith(".json"):
            archive = json.loads((docs / path).read_text(encoding="utf-8"))
            archive_ids.extend(item["id"] for item in archive["items"])
    assert sorted(archive_ids) == sorted(ids)
