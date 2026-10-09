"""Server-rendered parcel dossier from the read model and optional panel snapshot."""

from __future__ import annotations

import html
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

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


def _display(value: Any, tr: Callable[[str], str]) -> str:
    if value is None:
        return tr("Not reported")
    if isinstance(value, bool):
        return tr("Yes") if value else tr("No")
    if isinstance(value, (str, int, float)):
        text = str(value)
    else:
        text = str(value)
    return text[:500]


def _flatten_values(data: Any, tr: Callable[[str], str]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if len(values) >= _MAX_VALUES_PER_BLOCK:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                if key_text in _OMIT_KEYS or child is None:
                    continue
                child_path = f"{path} / {key_text}" if path else key_text
                if isinstance(child, dict) and depth < 2:
                    visit(child, child_path, depth + 1)
                elif isinstance(child, list):
                    if not child:
                        continue
                    if all(not isinstance(item, (dict, list)) for item in child):
                        values.append((child_path, ", ".join(_display(item, tr) for item in child[:_MAX_LIST_ITEMS])))
                    else:
                        for index, item in enumerate(child[:_MAX_LIST_ITEMS], start=1):
                            visit(item, f"{child_path} #{index}", depth + 1)
                        if len(child) > _MAX_LIST_ITEMS:
                            values.append((child_path, tr("{count} records; first {limit} listed").format(count=len(child), limit=_MAX_LIST_ITEMS)))
                elif isinstance(child, (str, int, float, bool)):
                    values.append((child_path, _display(child, tr)))
        elif isinstance(value, list):
            for index, item in enumerate(value[:_MAX_LIST_ITEMS], start=1):
                visit(item, f"{path} #{index}", depth + 1)
            if len(value) > _MAX_LIST_ITEMS:
                values.append((path, tr("{count} records; first {limit} listed").format(count=len(value), limit=_MAX_LIST_ITEMS)))
        elif value is not None:
            values.append((path, _display(value, tr)))

    visit(data, "")
    return [(_field_label(label, tr), value) for label, value in values]


def _paragraph(Paragraph, value: Any, style, tr: Callable[[str], str]) -> Any:
    text = html.escape(_display(value, tr), quote=True).replace("\n", "<br/>")
    return Paragraph(text, style)


def _identity_rows(parcel: dict[str, Any], profile: dict[str, Any], tr: Callable[[str], str]) -> list[tuple[str, str]]:
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
        (tr("Geometry"), (parcel.get("geometry") or {}).get("type") if isinstance(parcel.get("geometry"), dict) else None),
    ]
    centroid = profile.get("centroid")
    if isinstance(centroid, dict):
        identity.append((tr("Centroid (latitude, longitude)"), f"{centroid.get('lat')}, {centroid.get('lng')}"))
    return [(label, _display(value, tr)) for label, value in identity]


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


def render_parcel_report_pdf(
    national_reference: str,
    parcel: dict[str, Any],
    profile: dict[str, Any] | None,
    report_id: str,
    translate: Callable[[str], str] | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> bytes:
    """Render an A4 parcel dossier PDF with version footer and source register."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    profile = profile or {}
    tr = translate or (lambda message: message)
    blocks = _available_blocks(profile)
    panel_sections = sections or []
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    versions = _version_footer(blocks, tr, panel_sections)
    output = io.BytesIO()
    page_width, _ = A4
    margin = 17 * mm
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ParcelTitle", parent=styles["Title"], alignment=TA_LEFT, textColor=colors.HexColor("#153f72"), spaceAfter=5)
    heading_style = ParagraphStyle("ParcelHeading", parent=styles["Heading2"], textColor=colors.HexColor("#153f72"), spaceBefore=9, spaceAfter=4)
    subheading_style = ParagraphStyle("ParcelSubheading", parent=styles["Heading3"], textColor=colors.HexColor("#155e75"), spaceBefore=6, spaceAfter=3)
    body_style = ParagraphStyle("ParcelBody", parent=styles["BodyText"], fontSize=8.5, leading=11, spaceAfter=3)
    small_style = ParagraphStyle("ParcelSmall", parent=body_style, fontSize=7.5, leading=9)
    label_style = ParagraphStyle("ParcelLabel", parent=small_style, textColor=colors.HexColor("#475467"))
    story = [
        Paragraph(html.escape(tr("Parcel dossier")), title_style),
        Paragraph(f"{html.escape(tr('Reference'))}: {html.escape(national_reference)}", styles["Heading3"]),
        Paragraph(html.escape(tr("Generated {date} · Report ID {report_id}").format(date=generated_at, report_id=report_id)), small_style),
        Spacer(1, 5 * mm),
        Paragraph(html.escape(tr("Parcel identity")), heading_style),
    ]

    identity_rows = [[_paragraph(Paragraph, tr("Field"), label_style, tr), _paragraph(Paragraph, tr("Value"), label_style, tr)]]
    identity_rows.extend([
        [_paragraph(Paragraph, label, label_style, tr), _paragraph(Paragraph, value, body_style, tr)]
        for label, value in _identity_rows(parcel, profile, tr)
    ])
    identity_table = Table(identity_rows, colWidths=[52 * mm, page_width - 2 * margin - 52 * mm], repeatRows=1, hAlign="LEFT")
    identity_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4f8")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d5dd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([identity_table, Spacer(1, 4 * mm), Paragraph(html.escape(tr("Available enrichment")), heading_style)])

    if not blocks:
        story.append(Paragraph(html.escape(tr("No enrichment blocks were available in the server read-model snapshot.")), body_style))
    for name, block in blocks:
        title = _BLOCK_TITLES.get(name, name.replace("_", " ").title())
        story.append(Paragraph(html.escape(tr(title)), subheading_style))
        provenance = [
            (tr("Source"), block.get("source")),
            (tr("Dataset version"), block.get("dataset_version") or block.get("data_vintage")),
            (tr("Model version"), block.get("model_version")),
            (tr("Spatial resolution"), block.get("spatial_resolution")),
            (tr("Match method"), block.get("match_method")),
        ]
        provenance = [(key, value) for key, value in provenance if value]
        for key, value in provenance:
            story.append(Paragraph(f"<font color='#475467'>{html.escape(key)}:</font> {html.escape(_display(value, tr))}", small_style))

        values = _flatten_values(block.get("data"), tr)
        if values:
            data_rows = [[_paragraph(Paragraph, key, label_style, tr), _paragraph(Paragraph, value, body_style, tr)] for key, value in values]
            data_table = Table(data_rows, colWidths=[52 * mm, page_width - 2 * margin - 52 * mm], hAlign="LEFT")
            data_table.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#eaecf0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(data_table)
        else:
            story.append(Paragraph(html.escape(tr("The block is available, but has no printable scalar attributes.")), small_style))
        story.append(Spacer(1, 2 * mm))

    if panel_sections:
        story.append(Paragraph(html.escape(tr("Panel sections")), heading_style))
        story.append(Paragraph(html.escape(tr("These details were captured from the parcel panel when this report was requested.")), small_style))
        for section in panel_sections:
            section_id = str(section.get("id", ""))
            metadata = section.get("metadata") or {}
            title = _PANEL_SECTION_TITLES.get(section_id, section.get("title") or section_id)
            story.append(Paragraph(html.escape(tr(str(title))), subheading_style))
            state = str(section.get("state") or "unknown")
            story.append(Paragraph(
                f"<font color='#475467'>{html.escape(tr('Panel state'))}:</font> {html.escape(_display(state, tr))}",
                small_style,
            ))
            for key, label in _PANEL_PROVENANCE_FIELDS:
                value = metadata.get(key)
                if value:
                    story.append(Paragraph(
                        f"<font color='#475467'>{html.escape(tr(label))}:</font> {html.escape(_display(value, tr))}",
                        small_style,
                    ))
            body = str(section.get("body") or "").strip()
            if body:
                for line in body.splitlines():
                    line = line.strip()
                    if line:
                        story.append(Paragraph(html.escape(line), body_style))
            else:
                story.append(Paragraph(html.escape(tr("No section text was available.")), small_style))
            story.append(Spacer(1, 2 * mm))

    story.extend([
        PageBreak(),
        Paragraph(html.escape(tr("Sources, dataset releases and licences")), title_style),
        Paragraph(html.escape(tr("This register covers source metadata attached to the server read model and captured panel sections.")), body_style),
    ])
    if not blocks:
        story.append(Paragraph(html.escape(tr("No source metadata was available in the read-model snapshot.")), body_style))
    for name, block in blocks:
        title = _BLOCK_TITLES.get(name, name.replace("_", " ").title())
        attribution = _source_register_rows(block.get("source"), block, tr)
        story.append(Paragraph(html.escape(tr(title)), subheading_style))
        for key, value in attribution:
            story.append(Paragraph(f"<font color='#475467'>{html.escape(key)}:</font> {html.escape(_display(value, tr))}", small_style))

    for section in panel_sections:
        section_id = str(section.get("id", ""))
        metadata = section.get("metadata") or {}
        title = _PANEL_SECTION_TITLES.get(section_id, section.get("title") or section_id)
        attribution = _source_register_rows(metadata.get("source"), metadata, tr)
        story.append(Paragraph(html.escape(tr(str(title))), subheading_style))
        for key, value in attribution:
            story.append(Paragraph(f"<font color='#475467'>{html.escape(key)}:</font> {html.escape(_display(value, tr))}", small_style))

    story.extend([
        Spacer(1, 4 * mm),
        Paragraph(html.escape(tr("This report is an informational snapshot, not a cadastral certificate, appraisal, or legal determination. Panel details were captured from the browser response at export time. Verify source records and licences with the responsible providers.")), small_style),
    ])

    def draw_page_footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d0d5dd"))
        canvas.line(margin, 58, page_width - margin, 58)
        canvas.setFont("Helvetica", 6)
        canvas.setFillColor(colors.HexColor("#475467"))
        canvas.drawString(margin, 47, tr("Report ID {report_id} · {date}").format(report_id=report_id, date=generated_at))
        footer_label = tr("Dataset/model versions: {versions}").format(versions=versions)
        footer_lines = simpleSplit(footer_label, "Helvetica", 6, page_width - 2 * margin)
        for index, line in enumerate(footer_lines[:3]):
            canvas.drawString(margin, 36 - index * 7, line)
        if len(footer_lines) > 3:
            canvas.drawString(margin, 12, tr("Full version list is on the source register page."))
        canvas.restoreState()

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=15 * mm,
        bottomMargin=26 * mm,
        title=f"Parcel dossier {national_reference}",
        author="Land Registry",
    )
    document.build(story, onFirstPage=draw_page_footer, onLaterPages=draw_page_footer)
    return output.getvalue()
