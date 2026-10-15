"""DEPRECATED: ReportLab parcel dossier from the read model and optional panel snapshot.

Superseded by the Typst dossier in :mod:`land_registry.parcel_dossier` (template
``land_registry/documents/parcel_dossier.typ``, endpoint ``/api/v1/enrichment/parcel/dossier/{ref}``). The
provenance register, locale-aware number formatting, parcel/profile matching, locator-map geometry and chart
helpers were ported there. This module is kept only so existing callers and tests keep working; it will be removed
once nothing depends on ``/parcel/report/``. Do not add features here.
"""

from __future__ import annotations

import html
import io
import re
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from land_registry.parcel_report_graphics import (
    bar_chart,
    geometry_perimeter_m,
    parcel_locator_drawing,
    range_bar_chart,
)
from land_registry.source_catalog import SourceCatalogEntry, find_source_catalog_entry

_BLOCK_TITLES = {
    "basic": "Parcel identity",
    "cadastral": "Cadastral details",
    "population": "Population",
    "demographics": "Demographics",
    "economics": "Economy and income",
    "buildings": "Buildings",
    "opendata": "Cadastral OpenData",
    "pvp": "PVP auctions",
    "valuation": "OMI valuation",
    "risk": "Risks",
    "subsidence": "Ground movement",
    "address": "Address",
}
_OMIT_KEYS = {"geometry", "bbox", "source_facts", "postgres_context"}
_MAX_VALUES_PER_BLOCK = 32
_MAX_LIST_ITEMS = 8
_PANEL_SECTION_TITLES = {
    "identity": "Cadastre", "omi": "OMI valuation", "pvp": "PVP auctions", "address": "Main address",
    "buildings": "Buildings", "opendata": "Cadastral OpenData", "risks": "Environmental risks",
    "parcel-hazards": "Parcel-scale hazards", "agenziademanio": "Agenzia Demanio concessions",
    "solar": "Municipal solar potential", "subsidence": "Ground movement (EGMS)",
    "bulletin": "Criticality bulletin", "fires": "Active fires",
    "municipality": "Municipality", "income": "Income (IRPEF)", "census": "Census 2021",
    "safety": "Safety", "demographics": "Demographic indicators", "quality": "Quality of life",
    "pois": "Points of interest", "coverage": "Data coverage",
}
_PANEL_PROVENANCE_FIELDS = (
    ("source", "Source"), ("dataset_version", "Dataset version"),
    ("data_vintage", "Dataset release / vintage"), ("model_version", "Model version"),
    ("updated_at", "Updated"), ("license", "Licence"), ("licence", "Licence"),
    ("spatial_resolution", "Spatial resolution"), ("match_method", "Match method"),
)


def _field_label(path: str, tr: Callable[[str], str]) -> str:
    parts = []
    for part in path.split(" / "):
        if part.startswith("#"):
            parts.append(part)
            continue
        readable = " ".join(part.replace("_", " ").replace("-", " ").split())
        readable = readable[:1].upper() + readable[1:]
        parts.append(tr(readable))
    return " / ".join(parts)


_UNGROUPED_KEY_HINTS = ("code", "codice", "istat", "cap", "zip", "phone", "version", "year", "anno", "fid", "ref", "id")


def format_number(value: Any, locale: str | None = None, decimals: int | None = None) -> str:
    """Group thousands and use the locale's separators (``1.298,56`` for Italian, ``1,298.56`` otherwise)."""
    number = float(value)
    if decimals is None:
        decimals = 0 if number == int(number) else 2
    text = f"{number:,.{decimals}f}"
    if (locale or "").lower().startswith("it"):
        text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return text


def _display(value: Any, tr: Callable[[str], str], locale: str | None = None, key: str = "") -> str:
    if value is None:
        return tr("Not reported")
    if isinstance(value, bool):
        return tr("Yes") if value else tr("No")
    if isinstance(value, float):
        return format_number(value, locale)
    if isinstance(value, int):
        hinted = any(hint in key.lower() for hint in _UNGROUPED_KEY_HINTS)
        return str(value) if hinted or abs(value) < 10_000 else format_number(value, locale)
    return str(value)[:500]


def _flatten_values(
    data: Any,
    tr: Callable[[str], str],
    locale: str | None = None,
    omit: frozenset[str] | set[str] = frozenset(),
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if len(values) >= _MAX_VALUES_PER_BLOCK:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                if key_text in _OMIT_KEYS or key_text in omit or child is None:
                    continue
                child_path = f"{path} / {key_text}" if path else key_text
                if isinstance(child, dict) and depth < 2:
                    visit(child, child_path, depth + 1)
                elif isinstance(child, list):
                    if not child:
                        continue
                    if all(not isinstance(item, (dict, list)) for item in child):
                        values.append((child_path, ", ".join(_display(item, tr, locale, key_text) for item in child[:_MAX_LIST_ITEMS])))
                    else:
                        for index, item in enumerate(child[:_MAX_LIST_ITEMS], start=1):
                            visit(item, f"{child_path} #{index}", depth + 1)
                        if len(child) > _MAX_LIST_ITEMS:
                            values.append((child_path, tr("{count} records; first {limit} listed").format(count=len(child), limit=_MAX_LIST_ITEMS)))
                elif isinstance(child, (str, int, float, bool)):
                    values.append((child_path, _display(child, tr, locale, key_text)))
        elif isinstance(value, list):
            for index, item in enumerate(value[:_MAX_LIST_ITEMS], start=1):
                visit(item, f"{path} #{index}", depth + 1)
            if len(value) > _MAX_LIST_ITEMS:
                values.append((path, tr("{count} records; first {limit} listed").format(count=len(value), limit=_MAX_LIST_ITEMS)))
        elif value is not None:
            values.append((path, _display(value, tr, locale, path.rsplit(' / ', 1)[-1])))

    visit(data, "")
    return [(_field_label(label, tr), value) for label, value in values]


def _paragraph(Paragraph, value: Any, style, tr: Callable[[str], str]) -> Any:
    text = html.escape(_display(value, tr), quote=True).replace("\n", "<br/>")
    return Paragraph(text, style)


def _identity_rows(
    parcel: dict[str, Any],
    profile: dict[str, Any],
    tr: Callable[[str], str],
    locale: str | None = None,
) -> list[tuple[str, str]]:
    properties = parcel.get("properties") or {}
    municipality = (
        properties.get("municipality_name")
        or properties.get("municipality")
        or properties.get("ADMINISTRATIVEUNIT")
        or (profile.get("municipality") or {}).get("name")
    )
    area = properties.get("computed_area_sqm") or properties.get("area_sqm")
    if area is None and properties.get("area_ha") is not None:
        try:
            area = float(properties["area_ha"]) * 10_000
        except (TypeError, ValueError):
            area = None
    identity = [
        (tr("National cadastral reference"), profile.get("national_reference") or properties.get("national_cadastral_reference")),
        (tr("Parcel"), properties.get("parcel_number") or properties.get("parcel") or properties.get("particella") or properties.get("LABEL")),
        (tr("Sheet"), properties.get("sheet_number") or properties.get("sheet") or properties.get("foglio")),
        (tr("Municipality"), municipality),
        (tr("Cadastral municipality code"), properties.get("municipality_code") or properties.get("ADMINISTRATIVEUNIT")),
        (tr("Province"), properties.get("province") or (profile.get("municipality") or {}).get("province_sigla")),
        (tr("Region"), properties.get("region") or (profile.get("municipality") or {}).get("region")),
        (tr("Area (m²)"), area),
    ]
    perimeter = geometry_perimeter_m(parcel.get("geometry"))
    if perimeter:
        identity.append((tr("Perimeter (m)"), round(perimeter, 1)))
    centroid = profile.get("centroid")
    if isinstance(centroid, dict):
        identity.append((tr("Centroid (latitude, longitude)"), f"{centroid.get('lat')}, {centroid.get('lng')}"))
    return [(label, _display(value, tr, locale, label)) for label, value in identity]


def _available_blocks(profile: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    blocks = profile.get("blocks") or {}
    return [
        (name, block)
        for name, block in blocks.items()
        if isinstance(block, dict) and block.get("available") is True
    ]


def profile_matches_parcel(parcel: dict[str, Any], profile: dict[str, Any] | None) -> bool:
    """Avoid attaching reference-keyed enrichment to a different duplicate polygon."""
    if not isinstance(profile, dict):
        return False
    modeled_parcel = profile.get("parcel")
    if not isinstance(modeled_parcel, dict):
        return False
    parcel_properties = parcel.get("properties") or {}
    modeled_properties = modeled_parcel.get("properties") or {}
    parcel_id = parcel.get("id", parcel_properties.get("id"))
    modeled_id = modeled_parcel.get("id", modeled_properties.get("id"))
    if parcel_id is not None and modeled_id is not None:
        return str(parcel_id) == str(modeled_id)

    parcel_geometry = parcel.get("geometry")
    modeled_geometry = modeled_parcel.get("geometry")
    if isinstance(parcel_geometry, dict) and isinstance(modeled_geometry, dict):
        try:
            from shapely.errors import ShapelyError
            from shapely.geometry import shape

            return shape(parcel_geometry).equals(shape(modeled_geometry))
        except (ShapelyError, TypeError, ValueError):
            return False
    return False


def _version_footer(
    blocks: list[tuple[str, dict[str, Any]]],
    tr: Callable[[str], str],
    sections: list[dict[str, Any]] | None = None,
) -> str:
    versions = []
    for name, block in blocks:
        dataset = block.get("dataset_version") or block.get("data_vintage")
        model = block.get("model_version")
        if dataset or model:
            version = "/".join(str(part) for part in (dataset, model) if part)
            versions.append(f"{name}={version}")
    for section in sections or []:
        metadata = section.get("metadata") or {}
        dataset = metadata.get("dataset_version") or metadata.get("data_vintage")
        model = metadata.get("model_version")
        if dataset or model:
            version = "/".join(str(part) for part in (dataset, model) if part)
            versions.append(f"{section.get('id', 'panel')}={version}")
    return "; ".join(versions) if versions else tr("No dataset or model version was reported by available blocks.")


def _catalog_license_status(
    entry: SourceCatalogEntry | None,
    license_reported: bool,
    tr: Callable[[str], str],
) -> str:
    if entry is None:
        if license_reported:
            return tr("No catalog match; reported licence has not been independently reviewed")
        return tr("No catalog match; provider and licence review required")
    status_messages = {
        "official_terms_reviewed": "Official provider terms reviewed",
        "official_methodology_reviewed": "Official provider methodology reviewed",
        "official_general_policy_reviewed": "Official general data policy reviewed; product licence pending",
        "official_policy_scope_reviewed": "Official policy scope reviewed; product-specific terms pending",
        "official_reuse_policy_reviewed": "Official reuse policy reviewed; no licence name asserted",
        "official_service_access_reviewed": "Official service access reviewed; data reuse terms pending",
        "official_public_access_reviewed": "Official public-access guide reviewed; reuse licence not stated",
        "provider_product_page_reviewed": "Provider product page reviewed",
        "upstream_contract_review_pending": "Upstream package contract; provider terms review pending",
        "adapter_declaration_review_pending": "Adapter declaration; official provider terms review pending",
        "citation_verified_license_pending": "Provider citation verified; licence review pending",
        "provider_terms_review_pending": "Provider terms review pending",
    }
    return tr(status_messages.get(entry.license_basis, "Provider terms review pending"))


def _source_register_rows(
    source: Any,
    metadata: dict[str, Any],
    tr: Callable[[str], str],
) -> list[tuple[str, str]]:
    """Combine response-specific provenance with catalog data without inventing vintages."""
    primary_metadata = {
        key: value for key, value in metadata.items()
        if not key.startswith("additional_")
    }
    rows = _single_source_register_rows(source, primary_metadata, tr)

    additional_source = metadata.get("additional_source")
    if isinstance(additional_source, str) and additional_source.strip():
        additional_metadata = {
            "model_version": metadata.get("additional_model_version"),
            "license": metadata.get("additional_license"),
            "dataset_version": metadata.get("additional_dataset_version"),
            "data_vintage": metadata.get("additional_data_vintage"),
            "updated_at": metadata.get("additional_updated_at"),
        }
        rows.extend(_single_source_register_rows(additional_source, additional_metadata, tr))
    return rows


def _single_source_register_rows(
    source: Any,
    metadata: dict[str, Any],
    tr: Callable[[str], str],
) -> list[tuple[str, str]]:
    """Render the catalog and response metadata for one provider."""
    source_text = source if isinstance(source, str) and source.strip() else tr("Not reported")
    entry = find_source_catalog_entry(source_text)
    reported_license = metadata.get("license") or metadata.get("licence")
    catalog_license = entry.license_name if entry is not None else None
    license_value = reported_license or catalog_license or tr("Not reported in source metadata")

    rows = [
        (tr("Source"), source_text),
        (tr("Catalog provider"), entry.display_name if entry else tr("No catalog match")),
        (tr("Provider URL"), entry.provider_url if entry else tr("Not available")),
        (tr("Dataset release / vintage"), metadata.get("data_vintage") or tr("Not reported by source metadata")),
        (tr("Dataset version"), metadata.get("dataset_version") or metadata.get("data_vintage") or tr("Not reported by source metadata")),
        (tr("Model version"), metadata.get("model_version") or tr("Not reported by source metadata")),
        (tr("Updated"), metadata.get("updated_at") or tr("Not reported by source metadata")),
        (tr("Licence"), license_value),
        (tr("Licence review status"), _catalog_license_status(entry, bool(reported_license), tr)),
    ]
    if entry is not None and entry.terms_url:
        rows.append((tr("Use terms URL"), entry.terms_url))
    if reported_license and catalog_license:
        rows.insert(-1, (tr("Catalog licence"), catalog_license))
    if entry is not None and entry.citation:
        rows.append((tr("Provider citation"), entry.citation))
    if entry is not None and entry.reuse_terms:
        rows.append((tr("Provider use terms"), tr(entry.reuse_terms)))
    if entry is not None and entry.attribution_text:
        rows.append((tr("Provider attribution"), tr(entry.attribution_text)))
    return rows


_BRAND = "#153f72"
_ACCENT = "#155e75"
_MUTED = "#475467"
_LINE = "#d0d5dd"
_SOFT = "#eef4f8"
_ZEBRA = "#f9fafb"
_STATE_COLORS = {"ready": "#067647", "empty": "#667085", "idle": "#667085", "loading": "#b54708", "error": "#b42318"}
_MAX_QUOTE_ROWS = 16
_MAX_QUOTE_CHART_ROWS = 10
_KV_LINE = re.compile(r"^([^:]{2,48}):\s+(\S.*)$")
_BRACKET_LABELS = {"over": "> ", "under": "< "}
_INCOME_LEVELS = (("municipality", "Municipality"), ("province", "Province"), ("region", "Region"), ("nation", "Italy"))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN


def _find_key(data: Any, key: str, depth: int = 0) -> Any:
    """First value stored under ``key`` anywhere in nested dict/list data (shallow search)."""
    if depth > 4:
        return None
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for child in data.values():
            found = _find_key(child, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data[:10]:
            found = _find_key(item, key, depth + 1)
            if found is not None:
                return found
    return None


def _bracket_label(raw: Any) -> str:
    """``10_15k`` → ``10–15 k€``, ``over_120k`` → ``> 120 k€``."""
    text = str(raw)
    for prefix, symbol in _BRACKET_LABELS.items():
        if text.startswith(prefix + "_"):
            text = symbol + text[len(prefix) + 1:]
            break
    text = text.replace("_", "–")
    return text[:-1] + " k€" if text.endswith("k") else text


def _block_title(name: str, tr: Callable[[str], str]) -> str:
    return tr(_BLOCK_TITLES.get(name, name.replace("_", " ").title()))


def _split_source_rows(rows: list[tuple[str, str]], source_label: str) -> list[list[tuple[str, str]]]:
    """One group of rows per provider (a block may list a primary and an additional source)."""
    groups: list[list[tuple[str, str]]] = []
    for key, value in rows:
        if key == source_label or not groups:
            groups.append([])
        groups[-1].append((key, value))
    return groups


def render_parcel_report_pdf(
    national_reference: str,
    parcel: dict[str, Any],
    profile: dict[str, Any] | None,
    report_id: str,
    translate: Callable[[str], str] | None = None,
    sections: list[dict[str, Any]] | None = None,
    *,
    neighbours: list[Any] | None = None,
    locale: str | None = None,
) -> bytes:
    """Render an A4 parcel dossier PDF: overview with locator map, tables and charts, source register.

    ``neighbours`` are the GeoJSON geometries of the adjacent parcels (drawn around the highlighted parcel);
    ``locale`` selects the number format (Italian uses ``1.298,56``).

    .. deprecated::
        Use :func:`land_registry.parcel_dossier.render_dossier_pdf` (Typst).
    """
    warnings.warn(
        "land_registry.parcel_report (ReportLab) is deprecated; use land_registry.parcel_dossier (Typst)",
        DeprecationWarning,
        stacklevel=2,
    )
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (
        CondPageBreak,
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    profile = profile or {}
    tr = translate or (lambda message: message)
    blocks = _available_blocks(profile)
    panel_sections = sections or []
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    versions = _version_footer(blocks, tr, panel_sections)
    output = io.BytesIO()
    page_width, page_height = A4
    margin = 15 * mm
    content_w = page_width - 2 * margin
    brand, accent, muted = colors.HexColor(_BRAND), colors.HexColor(_ACCENT), colors.HexColor(_MUTED)
    line_color, soft, zebra = colors.HexColor(_LINE), colors.HexColor(_SOFT), colors.HexColor(_ZEBRA)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("PBody", parent=styles["BodyText"], fontSize=8.5, leading=11, spaceAfter=3)
    small_style = ParagraphStyle("PSmall", parent=body_style, fontSize=7.5, leading=9.5)
    label_style = ParagraphStyle("PLabel", parent=small_style, textColor=muted)
    head_style = ParagraphStyle("PHead", parent=small_style, textColor=brand, fontName="Helvetica-Bold")
    h1_style = ParagraphStyle("PH1", parent=styles["Heading2"], textColor=brand, spaceBefore=10, spaceAfter=2, fontSize=13.5)
    h2_style = ParagraphStyle("PH2", parent=styles["Heading3"], textColor=accent, spaceBefore=8, spaceAfter=3, fontSize=10.5)
    title_style = ParagraphStyle("PTitle", parent=styles["Title"], alignment=TA_LEFT, textColor=colors.white, fontSize=19, leading=22, spaceAfter=2)
    band_sub_style = ParagraphStyle("PBandSub", parent=small_style, textColor=colors.HexColor("#d1e9ff"), fontSize=9, leading=12)
    kpi_label_style = ParagraphStyle("PKpiLabel", parent=small_style, textColor=muted, fontSize=7, leading=9)
    kpi_value_style = ParagraphStyle("PKpiValue", parent=body_style, fontName="Helvetica-Bold", fontSize=12, leading=14, textColor=brand, spaceAfter=0)
    caption_style = ParagraphStyle("PCaption", parent=small_style, textColor=muted, fontSize=6.8, leading=8.5)

    class _Heading(Paragraph):
        """A heading that also registers a PDF bookmark/outline entry when it is drawn."""

        def __init__(self, text: str, style, level: int = 0):
            super().__init__(html.escape(text), style)
            self._outline_title, self._outline_level = text, level

        def draw(self):
            key = f"sec-{id(self)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(self._outline_title, key, level=self._outline_level, closed=False)
            super().draw()

    def cell(value: Any, style=body_style) -> Paragraph:
        text = _display(value, tr, locale)
        escaped = html.escape(text, quote=True).replace("\n", "<br/>")
        if text.startswith(("http://", "https://")) and " " not in text:
            escaped = f'<link href="{html.escape(text, quote=True)}" color="{_ACCENT}">{escaped}</link>'
        return Paragraph(escaped, style)

    def kv_table(rows: list[tuple[str, Any]], label_w: float = 52 * mm, width: float = content_w, header: str | None = None):
        data = [[cell(label, label_style), cell(value)] for label, value in rows]
        commands = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#eaecf0")),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, zebra]),
            ("BOX", (0, 0), (-1, -1), 0.4, line_color),
        ]
        if header:
            data.insert(0, [Paragraph(html.escape(header), head_style), ""])
            commands += [("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), soft)]
        table = Table(data, colWidths=[label_w, width - label_w], hAlign="LEFT", repeatRows=1 if header else 0)
        table.setStyle(TableStyle(commands))
        return table

    def grid_table(header: list[str], rows: list[list[Any]], widths: list[float], align_right: set[int] = frozenset()):
        right_style = ParagraphStyle("PRight", parent=body_style, alignment=2)
        right_head = ParagraphStyle("PRightHead", parent=head_style, alignment=2)
        data = [[Paragraph(html.escape(h), right_head if i in align_right else head_style) for i, h in enumerate(header)]]
        for row in rows:
            data.append([cell(value, right_style if i in align_right else body_style) for i, value in enumerate(row)])
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), soft),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#eaecf0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, zebra]),
            ("BOX", (0, 0), (-1, -1), 0.4, line_color),
        ]))
        return table

    def section(title: str, keep: float = 55 * mm) -> list:
        """A level-0 heading with its rule; moves to the next page unless ``keep`` points of room are left."""
        return [CondPageBreak(keep), _Heading(title, h1_style, 0), HRFlowable(width="100%", thickness=0.8, color=brand, spaceAfter=5)]

    def callout(text: str) -> Table:
        box = Table([[Paragraph(html.escape(text), body_style)]], colWidths=[content_w])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffaeb")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fedf89")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return box

    # -- header band and at-a-glance strip -----------------------------------------------------------------------
    identity = _identity_rows(parcel, profile, tr, locale)
    properties = parcel.get("properties") or {}
    municipality = next((value for label, value in identity if label == tr("Municipality")), "")
    province = next((value for label, value in identity if label == tr("Province")), "")
    area_value = next((value for label, value in identity if label == tr("Area (m²)")), None)
    sheet = next((value for label, value in identity if label == tr("Sheet")), None)
    parcel_no = next((value for label, value in identity if label == tr("Parcel")), None)

    band = Table(
        [[[
            Paragraph(html.escape(tr("Parcel dossier")), title_style),
            Paragraph(html.escape(f"{tr('Reference')}: {national_reference}"), band_sub_style),
            Paragraph(
                html.escape(tr("Generated {date} · Report ID {report_id}").format(date=generated_at, report_id=report_id)),
                band_sub_style,
            ),
        ]]],
        colWidths=[content_w],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story: list = [band, Spacer(1, 4 * mm)]

    unreported = tr("Not reported")
    location = ", ".join(part for part in (municipality, province) if part and part != unreported) or unreported
    kpis = [
        (tr("Area (m²)"), f"{area_value} m²" if area_value not in (None, unreported) else unreported),
        (tr("Sheet"), sheet or unreported),
        (tr("Parcel"), parcel_no or unreported),
        (tr("Municipality"), location),
    ]
    kpi_table = Table(
        [[Paragraph(html.escape(label), kpi_label_style) for label, _ in kpis],
         [Paragraph(html.escape(str(value)), kpi_value_style) for _, value in kpis]],
        colWidths=[content_w / len(kpis)] * len(kpis),
    )
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, line_color),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, line_color),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
        ("TOPPADDING", (0, 1), (-1, 1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
    ]))
    story.append(kpi_table)

    # -- overview: identity table beside the locator map ---------------------------------------------------------
    story.extend(section(tr("Overview")))
    identity_w = 90 * mm if parcel.get("geometry") else content_w
    identity_table = kv_table(identity, label_w=40 * mm if identity_w < content_w else 52 * mm, width=identity_w)
    map_w = content_w - identity_w - 4 * mm
    locator = None
    if map_w > 40 * mm:
        locator = parcel_locator_drawing(
            parcel.get("geometry"),
            neighbours,
            map_w,
            max(map_w * 0.9, 62 * mm),
            area_label=f"{area_value} m²" if area_value not in (None, unreported) else None,
            reference_label=national_reference,
        )
    if locator is not None:
        caption = Paragraph(
            html.escape(
                tr(
                    "Outline of the selected parcel (blue) and neighbouring parcels (grey), drawn from the cadastral "
                    "geometry. Schematic, not a survey."
                )
            ),
            caption_style,
        )
        overview = Table([[identity_table, [locator, Spacer(1, 1.5 * mm), caption]]], colWidths=[identity_w + 4 * mm, map_w])
        overview.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 4 * mm),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(overview)
    else:
        story.append(identity_table)

    # -- data availability matrix --------------------------------------------------------------------------------
    all_blocks = [(name, block) for name, block in (profile.get("blocks") or {}).items() if isinstance(block, dict)]
    if all_blocks:
        story.extend(section(tr("Data availability")))
        rows = []
        for name, block in all_blocks:
            available = block.get("available") is True
            rows.append([
                _block_title(name, tr),
                tr("Available") if available else tr("Not available"),
                block.get("coverage_status") or block.get("coverage") or "—",
                str(block.get("source") or "—")[:90],
            ])
        availability = grid_table(
            [tr("Block"), tr("Status"), tr("Coverage"), tr("Source")],
            rows,
            [42 * mm, 28 * mm, 26 * mm, content_w - 96 * mm],
        )
        status_styles = []
        for index, row in enumerate(rows, start=1):
            positive = row[1] == tr("Available")
            status_styles.append(("BACKGROUND", (1, index), (1, index), colors.HexColor("#ecfdf3" if positive else "#f2f4f7")))
        availability.setStyle(TableStyle(status_styles))
        story.append(availability)

    # -- enrichment blocks ---------------------------------------------------------------------------------------
    story.extend(section(tr("Available enrichment")))
    if not blocks:
        story.append(callout(tr("No enrichment blocks were available in the server read-model snapshot.")))
    for name, block in blocks:
        data = block.get("data")
        title = _block_title(name, tr)
        provenance = [
            (tr("Source"), block.get("source")),
            (tr("Dataset version"), block.get("dataset_version") or block.get("data_vintage")),
            (tr("Model version"), block.get("model_version")),
            (tr("Spatial resolution"), block.get("spatial_resolution")),
            (tr("Match method"), block.get("match_method")),
        ]
        provenance_line = " · ".join(f"{label}: {_display(value, tr, locale)}" for label, value in provenance if value)
        block_flow: list = [_Heading(title, h2_style, 1)]
        if provenance_line:
            block_flow.append(Paragraph(html.escape(provenance_line), label_style))
        omit: set[str] = set()

        quotes = _find_key(data, "quotes")
        if isinstance(quotes, list) and any(isinstance(q, dict) for q in quotes):
            omit.add("quotes")
            block_flow.extend(_valuation_flowables(
                [q for q in quotes if isinstance(q, dict)], tr, locale, grid_table, content_w, caption_style, Paragraph,
            ))
        distribution = _find_key(data, "income_distribution")
        references = _find_key(data, "income_reference_averages")
        if isinstance(distribution, list) and distribution:
            omit.add("income_distribution")
            chart = bar_chart(
                [(_bracket_label(item.get("bracket")), _number(item.get("pct"))) for item in distribution if isinstance(item, dict)],
                content_w - 10 * mm,
                value_format=lambda v: f"{format_number(v, locale, 1)} %",
            )
            if chart is not None:
                block_flow.extend([
                    Spacer(1, 2 * mm),
                    Paragraph(html.escape(tr("Share of taxpayers by income bracket (%)")), label_style),
                    chart,
                ])
        if isinstance(references, dict) and references:
            omit.add("income_reference_averages")
            ref_rows = []
            for key, label in _INCOME_LEVELS:
                item = references.get(key)
                if isinstance(item, dict) and _number(item.get("mean_taxable_income_eur")) is not None:
                    ref_rows.append([
                        tr(label),
                        item.get("name") or "—",
                        f"{format_number(item['mean_taxable_income_eur'], locale, 0)} €",
                    ])
            if ref_rows:
                block_flow.extend([
                    Spacer(1, 2 * mm),
                    grid_table(
                        [tr("Level"), tr("Name"), tr("Mean taxable income")],
                        ref_rows,
                        [32 * mm, content_w - 32 * mm - 44 * mm, 44 * mm],
                        align_right={2},
                    ),
                ])

        values = _flatten_values(data, tr, locale, omit)
        if values:
            block_flow.append(Spacer(1, 1.5 * mm))
            block_flow.append(kv_table(values))
        elif not omit:
            block_flow.append(Paragraph(html.escape(tr("The block is available, but has no printable scalar attributes.")), small_style))
        story.append(KeepTogether(block_flow[:3]) if len(block_flow) > 3 else KeepTogether(block_flow))
        story.extend(block_flow[3:])
        story.append(Spacer(1, 2 * mm))

    # -- sections captured from the panel ------------------------------------------------------------------------
    if panel_sections:
        story.extend(section(tr("Panel sections")))
        story.append(Paragraph(html.escape(tr("These details were captured from the parcel panel when this report was requested.")), small_style))
        for panel in panel_sections:
            section_id = str(panel.get("id", ""))
            metadata = panel.get("metadata") or {}
            title = _PANEL_SECTION_TITLES.get(section_id, panel.get("title") or section_id)
            state = str(panel.get("state") or "unknown")
            state_color = _STATE_COLORS.get(state, _MUTED)
            head = [
                _Heading(tr(str(title)), h2_style, 1),
                Paragraph(
                    f"<font color='{_MUTED}'>{html.escape(tr('Panel state'))}:</font> "
                    f"<font color='{state_color}'><b>{html.escape(_display(state, tr))}</b></font>",
                    small_style,
                ),
            ]
            provenance = [
                f"{tr(label)}: {_display(metadata.get(key), tr, locale)}"
                for key, label in _PANEL_PROVENANCE_FIELDS
                if metadata.get(key)
            ]
            if provenance:
                head.append(Paragraph(html.escape(" · ".join(provenance)), label_style))
            parts: list = []
            body = str(panel.get("body") or "").strip()
            if not body:
                parts.append(Paragraph(html.escape(tr("No section text was available.")), small_style))
            else:
                pending: list[tuple[str, str]] = []

                def flush() -> None:
                    if pending:
                        parts.append(kv_table(list(pending), label_w=60 * mm))
                        parts.append(Spacer(1, 1.5 * mm))
                        pending.clear()

                for raw in body.splitlines():
                    text = raw.strip()
                    if not text:
                        continue
                    match = _KV_LINE.match(text)
                    if match and "://" not in match.group(1):
                        pending.append((match.group(1).strip(), match.group(2).strip()))
                    else:
                        flush()
                        parts.append(Paragraph(html.escape(text), body_style))
                flush()
            # the heading never ends a page on its own: it travels with the first table/paragraph
            story.append(KeepTogether(head + parts[:1]))
            story.extend(parts[1:])
            story.append(Spacer(1, 2 * mm))

    # -- source register -----------------------------------------------------------------------------------------
    story.append(CondPageBreak(90 * mm))
    story.extend(section(tr("Sources, dataset releases and licences")))
    story.append(Paragraph(html.escape(tr("This register covers source metadata attached to the server read model and captured panel sections.")), body_style))
    register: list[tuple[str, list[tuple[str, str]]]] = []
    _provenance_keys = ("source", "dataset_version", "data_vintage", "model_version", "license", "licence", "updated_at")
    for name, block in blocks:
        if any(block.get(key) for key in _provenance_keys):
            register.append((_block_title(name, tr), _source_register_rows(block.get("source"), block, tr)))
    for panel in panel_sections:
        metadata = panel.get("metadata") or {}
        if not any(metadata.get(key) for key in _provenance_keys):
            continue  # a section that reported no provenance has nothing to register
        section_id = str(panel.get("id", ""))
        title = _PANEL_SECTION_TITLES.get(section_id, panel.get("title") or section_id)
        register.append((tr(str(title)), _source_register_rows(metadata.get("source"), metadata, tr)))
    if not register:
        story.append(Paragraph(html.escape(tr("No source metadata was available in the read-model snapshot.")), body_style))

    source_label = tr("Source")
    summary_rows: list[list[Any]] = []
    detail: list[tuple[str, list[tuple[str, str]]]] = []
    not_reported = tr("Not reported by source metadata")
    seen: dict[tuple, int] = {}
    for title, rows in register:
        for group in _split_source_rows(rows, source_label):
            lookup = dict(group)
            vintage = lookup.get(tr("Dataset release / vintage"))
            if not vintage or vintage == not_reported:
                version = lookup.get(tr("Dataset version"))
                vintage = version if version and version != not_reported else "—"
            row = [
                title,
                lookup.get(source_label) or "—",
                vintage,
                lookup.get(tr("Licence")) or "—",
                lookup.get(tr("Licence review status")) or "—",
            ]
            signature = tuple(row[1:])
            if signature in seen:  # the same provider feeds a block and a panel section: one row, both titles
                existing = summary_rows[seen[signature]]
                if title not in existing[0].split(" · "):
                    existing[0] = f"{existing[0]} · {title}"
                continue
            seen[signature] = len(summary_rows)
            summary_rows.append(row)
            detail.append((f"{title} — {lookup.get(source_label) or '—'}", group))
    if summary_rows:
        story.append(Spacer(1, 2 * mm))
        story.append(grid_table(
            [tr("Section"), tr("Source"), tr("Release / version"), tr("Licence"), tr("Review status")],
            summary_rows,
            [30 * mm, 42 * mm, 30 * mm, 36 * mm, content_w - 138 * mm],
        ))
        story.append(Spacer(1, 3 * mm))
        story.append(_Heading(tr("Provider details"), h2_style, 1))
        for title, group in detail:
            story.append(KeepTogether([kv_table(group, label_w=44 * mm, header=title), Spacer(1, 2.5 * mm)]))

    story.extend([
        Spacer(1, 4 * mm),
        Paragraph(html.escape(tr("This report is an informational snapshot, not a cadastral certificate, appraisal, or legal determination. Panel details were captured from the browser response at export time. Verify source records and licences with the responsible providers.")), small_style),
    ])

    # -- page furniture -----------------------------------------------------------------------------------------
    page_label = tr("Page {page} of {total}")

    class _NumberedCanvas(rl_canvas.Canvas):
        """Two-pass canvas so each footer can say "Page X of Y"."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages: list[dict] = []

        def showPage(self):  # noqa: N802 - reportlab API
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self.setFont("Helvetica", 6.5)
                self.setFillColor(muted)
                self.drawRightString(
                    page_width - margin, 47, page_label.format(page=self._pageNumber, total=total)
                )
                super().showPage()
            super().save()

    def draw_footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(line_color)
        canvas.line(margin, 58, page_width - margin, 58)
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(muted)
        canvas.drawString(margin, 47, tr("Report ID {report_id} · {date}").format(report_id=report_id, date=generated_at))
        footer_label = tr("Dataset/model versions: {versions}").format(versions=versions)
        footer_lines = simpleSplit(footer_label, "Helvetica", 6, content_w)
        for index, line in enumerate(footer_lines[:3]):
            canvas.setFont("Helvetica", 6)
            canvas.drawString(margin, 36 - index * 7, line)
        if len(footer_lines) > 3:
            canvas.drawString(margin, 12, tr("Full version list is on the source register page."))
        canvas.restoreState()

    def first_page(canvas, document) -> None:
        draw_footer(canvas, document)

    def later_page(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(brand)
        canvas.drawString(margin, page_height - 10 * mm, f"{tr('Parcel dossier')} · {national_reference}")
        canvas.setStrokeColor(line_color)
        canvas.line(margin, page_height - 11.5 * mm, page_width - margin, page_height - 11.5 * mm)
        canvas.restoreState()
        draw_footer(canvas, document)

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=15 * mm,
        bottomMargin=26 * mm,
        title=f"{tr('Parcel dossier')} {national_reference}",
        author="Land Registry",
        subject=tr("Parcel dossier"),
    )
    document.build(story, onFirstPage=first_page, onLaterPages=later_page, canvasmaker=_NumberedCanvas)
    return output.getvalue()


def _valuation_flowables(
    quotes: list[dict[str, Any]],
    tr: Callable[[str], str],
    locale: str | None,
    grid_table: Callable[..., Any],
    content_w: float,
    caption_style: Any,
    Paragraph: Any,
) -> list[Any]:
    """OMI quotes as a table (sale/rent ranges, gross yield) plus a sale-price range chart."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer

    def euro(value: Any) -> str:
        number = _number(value)
        return format_number(number, locale, 0) if number is not None else ""

    def span(low: Any, high: Any) -> str:
        low_text, high_text = euro(low), euro(high)
        if low_text and high_text:
            return f"{low_text} – {high_text}"
        return low_text or high_text or "—"

    zones = {str(q.get("zona") or "").strip() for q in quotes if q.get("zona")}
    rows, chart_rows = [], []
    for quote in quotes[:_MAX_QUOTE_ROWS]:
        typology = str(quote.get("tipologia") or quote.get("cod_tipologia") or "—")
        condition = str(quote.get("stato_conservazione") or "—")
        zone = str(quote.get("zona") or "—")
        derived = quote.get("derived_metrics") if isinstance(quote.get("derived_metrics"), dict) else {}
        yield_pct = _number(derived.get("gross_rental_yield_pct"))
        rows.append([
            typology,
            condition,
            zone,
            span(quote.get("prezzo_min"), quote.get("prezzo_max")),
            span(quote.get("locazione_min"), quote.get("locazione_max")),
            f"{format_number(yield_pct, locale, 1)} %" if yield_pct is not None else "—",
        ])
        label = f"{typology} · {condition}" + (f" ({zone})" if len(zones) > 1 else "")
        chart_rows.append((label, _number(quote.get("prezzo_min")), _number(quote.get("prezzo_max"))))

    flow: list[Any] = [Spacer(1, 1.5 * mm)]
    flow.append(grid_table(
        [tr("Typology"), tr("Condition"), tr("Zone"), tr("Sale price (€/m²)"), tr("Rent (€/m² per month)"), tr("Gross yield")],
        rows,
        [36 * mm, 26 * mm, 14 * mm, 36 * mm, 36 * mm, content_w - 148 * mm],
        align_right={3, 4, 5},
    ))
    if len(quotes) > _MAX_QUOTE_ROWS:
        flow.append(Paragraph(
            html.escape(tr("{count} records; first {limit} listed").format(count=len(quotes), limit=_MAX_QUOTE_ROWS)),
            caption_style,
        ))
    chart = range_bar_chart(chart_rows[:_MAX_QUOTE_CHART_ROWS], content_w - 6 * mm, value_format=lambda v: format_number(v, locale, 0))
    if chart is not None:
        flow.extend([
            Spacer(1, 3 * mm),
            Paragraph(html.escape(tr("Sale price range (€/m²) by typology and condition")), caption_style),
            chart,
        ])
    return flow
