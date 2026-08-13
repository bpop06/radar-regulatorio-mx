from __future__ import annotations

import json
import re
import unittest
from collections import defaultdict
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

from app.edition import STATIC_ASSET_VERSION as EDITION_STATIC_ASSET_VERSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPOSITORY_ROOT / "docs"
HTML_PAGES = tuple(sorted(DOCS_ROOT.glob("*.html")))
STATIC_ASSET_VERSION = f"v={EDITION_STATIC_ASSET_VERSION}"
MAX_PRELOADED_IMAGE_BYTES = 300 * 1024
AMBIENT_ASSETS = {
    "assets/archive-field-20260813.webp",
    "assets/carbon-depth-20260813.webp",
}
ARIA_IDREF_ATTRIBUTES = ("aria-labelledby", "aria-controls", "aria-describedby")
COMPACT_LIVE_REGION_TAGS = frozenset({"output", "p", "span", "time"})
KNOWN_LIVE_COUNTER_IDS = frozenset(
    {
        "cal-month-label",
        "page-indicator",
        "reading-restantes",
        "result-count",
        "today-total",
    }
)
SIMPLE_DOCUMENT_ID_SELECTOR_RE = re.compile(
    r"document\.querySelector\(\s*([\"'])#([A-Za-z_][\w:.-]*)\1\s*\)"
)


class Element(NamedTuple):
    tag: str
    attributes: dict[str, str | None]
    line: int


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag, attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag, attrs)

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append(Element(tag.casefold(), dict(attrs), self.getpos()[0]))


def parse_page(page: Path) -> PageParser:
    parser = PageParser()
    parser.feed(page.read_text(encoding="utf-8"))
    parser.close()
    return parser


def classes(element: Element) -> set[str]:
    return set((element.attributes.get("class") or "").split())


def local_path(page: Path, reference: str) -> Path | None:
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc or not parts.path:
        return None

    path = unquote(parts.path)
    if path.startswith("/"):
        return DOCS_ROOT / path.lstrip("/")
    return page.parent / path


class PagesContractTests(unittest.TestCase):
    def test_each_page_has_one_decorative_ambient_field(self) -> None:
        for page in HTML_PAGES:
            fields = [
                element
                for element in parse_page(page).elements
                if "ambient-field" in classes(element)
            ]
            with self.subTest(page=page.name):
                self.assertEqual(len(fields), 1)
                self.assertEqual(fields[0].attributes.get("aria-hidden"), "true")

    def test_ambient_assets_exist_and_stay_compact(self) -> None:
        styles = (DOCS_ROOT / "styles.css").read_text(encoding="utf-8")
        for reference in AMBIENT_ASSETS:
            target = DOCS_ROOT / reference
            with self.subTest(reference=reference):
                self.assertTrue(target.is_file())
                self.assertIn(reference, styles)
                self.assertLessEqual(target.stat().st_size, MAX_PRELOADED_IMAGE_BYTES)

    def test_ambient_motion_has_accessible_fallbacks(self) -> None:
        styles = (DOCS_ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(styles, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertRegex(styles, r"\.ambient-layer\s*\{[^}]*animation:\s*none")
        self.assertRegex(styles, r"@media\s*\(forced-colors:\s*active\)")
        self.assertRegex(styles, r"\.ambient-field\s*\{[^}]*display:\s*none")

    def test_deprecated_ai_cliche_assets_are_not_public(self) -> None:
        for name in (
            "watercolor-blooms.webp",
            "watercolor-paper.png",
            "paper-card-deckled.webp",
            "editorial-signal-map-20260812.webp",
        ):
            with self.subTest(name=name):
                self.assertFalse((DOCS_ROOT / "assets" / name).exists())

    def test_dynamic_selection_and_filter_reset_keep_native_control_semantics(self) -> None:
        calendar = (DOCS_ROOT / "calendario.js").read_text(encoding="utf-8")
        archive = (DOCS_ROOT / "archive.js").read_text(encoding="utf-8")

        self.assertIn('cell.setAttribute("aria-pressed"', calendar)
        self.assertIn('c.setAttribute("aria-pressed"', calendar)
        self.assertNotIn('cell.setAttribute("aria-selected"', calendar)
        self.assertIn("elements.search.focus({ preventScroll: true })", archive)

    def test_key_date_surfaces_use_semantic_time_elements(self) -> None:
        required = {
            "index.html": {
                "edition-date",
                "edition-updated",
                "lead-date",
                "coverage-updated",
            },
            "archivo.html": {"archive-from", "archive-to", "archive-updated"},
            "ficha.html": {"detail-date", "detail-updated"},
            "calendario.html": {"calendar-updated", "cal-month-label"},
            "guardias.html": {"guide-date"},
        }
        for filename, element_ids in required.items():
            elements = parse_page(DOCS_ROOT / filename).elements
            by_id = {
                element.attributes.get("id"): element
                for element in elements
                if element.attributes.get("id")
            }
            with self.subTest(page=filename):
                self.assertEqual(element_ids - by_id.keys(), set())
                self.assertTrue(
                    all(by_id[element_id].tag == "time" for element_id in element_ids),
                    f"{filename} must expose key dates with <time>",
                )

    def test_public_artifacts_supply_valid_dates_for_every_record(self) -> None:
        publications = json.loads(
            (DOCS_ROOT / "data" / "publications.json").read_text(encoding="utf-8")
        )
        edition = json.loads(
            (DOCS_ROOT / "data" / "edition.json").read_text(encoding="utf-8")
        )
        calendars = json.loads(
            (DOCS_ROOT / "data" / "calendars.json").read_text(encoding="utf-8")
        )

        for artifact in (publications, edition, calendars):
            generated_at = artifact.get("generated_at")
            with self.subTest(artifact=artifact.get("schema_version", "calendar")):
                self.assertIsInstance(generated_at, str)
                parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                self.assertIsNotNone(parsed.tzinfo, "generated_at must carry a timezone")

        self.assertEqual(publications.get("schema_version"), 8)
        self.assertEqual(edition.get("schema_version"), 8)
        self.assertEqual(edition.get("cut_id"), publications.get("cut_id"))

        items_by_id = {item["id"]: item for item in publications["items"]}
        for item in publications["items"]:
            with self.subTest(publication=item["id"]):
                self.assertEqual(
                    date.fromisoformat(item["official_published_at"]).isoformat(),
                    item["official_published_at"],
                )
                self.assertEqual(item["published_at"], item["official_published_at"])
                # El índice público v8 es deliberadamente ligero. Las marcas
                # first/last_seen viven en el envelope permanente/SQLite.
                for field in ("detected_at",):
                    instant = datetime.fromisoformat(item[field].replace("Z", "+00:00"))
                    self.assertIsNotNone(
                        instant.tzinfo,
                        f"{field} must carry a timezone",
                    )

        for signal in edition["signals"]:
            with self.subTest(signal=signal["id"]):
                self.assertIn(signal["id"], items_by_id)
                item = items_by_id[signal["id"]]
                self.assertEqual(signal["published_at"], item["published_at"])
                self.assertEqual(
                    signal["official_published_at"],
                    item["official_published_at"],
                )
                self.assertEqual(signal["detected_at"], item["detected_at"])

        for organ, days in calendars["days_by_organ"].items():
            for day in days:
                with self.subTest(organ=organ, calendar_date=day["date"]):
                    parsed = date.fromisoformat(day["date"])
                    self.assertEqual(parsed.year, calendars["year"])

    def test_site_has_html_pages(self) -> None:
        self.assertTrue(HTML_PAGES, "docs/ must contain at least one HTML page")

    def test_each_page_has_exactly_one_main_and_h1(self) -> None:
        for page in HTML_PAGES:
            elements = parse_page(page).elements
            with self.subTest(page=page.name):
                for tag in ("main", "h1"):
                    lines = [element.line for element in elements if element.tag == tag]
                    self.assertEqual(
                        len(lines),
                        1,
                        f"{page.name} must contain exactly one <{tag}>; found lines {lines}",
                    )

    def test_ids_are_nonempty_and_unique_per_page(self) -> None:
        for page in HTML_PAGES:
            ids: defaultdict[str, list[int]] = defaultdict(list)
            for element in parse_page(page).elements:
                if "id" not in element.attributes:
                    continue
                element_id = element.attributes["id"]
                with self.subTest(page=page.name, line=element.line):
                    self.assertTrue(element_id, f"{page.name}:{element.line} has an empty id")
                if element_id:
                    ids[element_id].append(element.line)

            duplicates = {element_id: lines for element_id, lines in ids.items() if len(lines) > 1}
            with self.subTest(page=page.name):
                self.assertEqual(duplicates, {}, f"{page.name} has duplicate ids: {duplicates}")

    def test_skip_links_point_to_targets_focusable_by_script(self) -> None:
        for page in HTML_PAGES:
            elements = parse_page(page).elements
            ids = {
                element.attributes.get("id"): element
                for element in elements
                if element.attributes.get("id")
            }
            skip_links = [element for element in elements if "skip-link" in classes(element)]
            with self.subTest(page=page.name):
                self.assertEqual(len(skip_links), 1, f"{page.name} must have one skip link")

            for link in skip_links:
                href = link.attributes.get("href") or ""
                parts = urlsplit(href)
                target = ids.get(unquote(parts.fragment))
                with self.subTest(page=page.name, line=link.line, href=href):
                    self.assertTrue(parts.fragment, "skip link must include a fragment target")
                    self.assertIsNotNone(target, f"skip target {href!r} does not exist")
                    if target is not None:
                        self.assertEqual(
                            target.attributes.get("tabindex"),
                            "-1",
                            f"skip target {href!r} must have tabindex=\"-1\"",
                        )

    def test_local_href_and_src_references_exist(self) -> None:
        for page in HTML_PAGES:
            elements = parse_page(page).elements
            page_ids = {
                element.attributes["id"]
                for element in elements
                if element.attributes.get("id")
            }
            for element in elements:
                for attribute in ("href", "src"):
                    reference = element.attributes.get(attribute)
                    if not reference:
                        continue
                    parts = urlsplit(reference)
                    with self.subTest(
                        page=page.name,
                        line=element.line,
                        attribute=attribute,
                        reference=reference,
                    ):
                        if (
                            not parts.scheme
                            and not parts.netloc
                            and parts.fragment
                            and not parts.path
                        ):
                            self.assertIn(
                                unquote(parts.fragment),
                                page_ids,
                                f"fragment target {reference!r} does not exist in {page.name}",
                            )

                        target = local_path(page, reference)
                        if target is not None:
                            self.assertTrue(
                                target.is_file(),
                                f"local {attribute} {reference!r} resolves to missing {target}",
                            )

    def test_blank_targets_protect_the_opener(self) -> None:
        for page in HTML_PAGES:
            for element in parse_page(page).elements:
                if (element.attributes.get("target") or "").casefold() != "_blank":
                    continue
                rel = set((element.attributes.get("rel") or "").casefold().split())
                with self.subTest(page=page.name, line=element.line):
                    self.assertIn(
                        "noopener",
                        rel,
                        f"{page.name}:{element.line} target=_blank needs rel=noopener",
                    )

    def test_aria_id_references_exist_on_the_same_page(self) -> None:
        for page in HTML_PAGES:
            elements = parse_page(page).elements
            ids = {
                element.attributes["id"]
                for element in elements
                if element.attributes.get("id")
            }
            for element in elements:
                for attribute in ARIA_IDREF_ATTRIBUTES:
                    raw_references = element.attributes.get(attribute)
                    if raw_references is None:
                        continue
                    references = raw_references.split()
                    with self.subTest(page=page.name, line=element.line, attribute=attribute):
                        self.assertTrue(references, f"{attribute} must not be empty")
                        self.assertEqual(
                            set(references) - ids,
                            set(),
                            f"{page.name}:{element.line} {attribute} has missing ids",
                        )

    def test_live_regions_are_compact_statuses_or_known_counters(self) -> None:
        for page in HTML_PAGES:
            for element in parse_page(page).elements:
                if "aria-live" not in element.attributes:
                    continue
                element_id = element.attributes.get("id") or ""
                permitted_id = (
                    element_id.endswith("-status") or element_id in KNOWN_LIVE_COUNTER_IDS
                )
                with self.subTest(page=page.name, line=element.line, element_id=element_id):
                    self.assertIn(
                        element.tag,
                        COMPACT_LIVE_REGION_TAGS,
                        f"{page.name}:{element.line} aria-live must be on a compact element",
                    )
                    self.assertTrue(
                        permitted_id,
                        f"{page.name}:{element.line} aria-live id must end in -status "
                        "or be an approved counter",
                    )

    def test_preloaded_local_images_stay_within_the_size_budget(self) -> None:
        for page in HTML_PAGES:
            for element in parse_page(page).elements:
                rel = set((element.attributes.get("rel") or "").casefold().split())
                if (
                    element.tag != "link"
                    or "preload" not in rel
                    or (element.attributes.get("as") or "").casefold() != "image"
                ):
                    continue

                reference = element.attributes.get("href") or ""
                target = local_path(page, reference)
                if target is None:
                    continue
                with self.subTest(page=page.name, line=element.line, reference=reference):
                    self.assertTrue(target.is_file(), f"preloaded image {target} does not exist")
                    if target.is_file():
                        self.assertLessEqual(
                            target.stat().st_size,
                            MAX_PRELOADED_IMAGE_BYTES,
                            f"preloaded image {reference!r} exceeds the 300 KiB budget",
                        )

    def test_local_css_and_javascript_references_are_versioned(self) -> None:
        for page in HTML_PAGES:
            for element in parse_page(page).elements:
                for attribute in ("href", "src"):
                    reference = element.attributes.get(attribute)
                    if not reference or local_path(page, reference) is None:
                        continue
                    parts = urlsplit(reference)
                    if Path(parts.path).suffix.casefold() not in {".css", ".js"}:
                        continue
                    with self.subTest(
                        page=page.name,
                        line=element.line,
                        reference=reference,
                    ):
                        self.assertEqual(
                            parts.query,
                            STATIC_ASSET_VERSION,
                            f"local asset {reference!r} must use ?{STATIC_ASSET_VERSION}",
                        )

    def test_literal_document_id_selectors_match_each_page(self) -> None:
        for page in HTML_PAGES:
            elements = parse_page(page).elements
            page_ids = {
                element.attributes["id"]
                for element in elements
                if element.attributes.get("id")
            }
            scripts = [
                local_path(page, element.attributes["src"])
                for element in elements
                if element.tag == "script" and element.attributes.get("src")
            ]
            local_scripts = [script for script in scripts if script is not None]
            for script in local_scripts:
                if not script.is_file():
                    continue
                source = script.read_text(encoding="utf-8")
                selectors = {
                    match.group(2) for match in SIMPLE_DOCUMENT_ID_SELECTOR_RE.finditer(source)
                }
                with self.subTest(page=page.name, script=script.name):
                    self.assertTrue(
                        selectors,
                        f"{script.name} should expose at least one static document id selector",
                    )
                    self.assertEqual(
                        selectors - page_ids,
                        set(),
                        f"{script.name} queries ids absent from {page.name}",
                    )


if __name__ == "__main__":
    unittest.main()
