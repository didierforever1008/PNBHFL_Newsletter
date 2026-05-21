from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models.schemas import ReportBundle, WeeklySignal

PRIMARY_BLUE = colors.HexColor("#1F3A5F")
SECONDARY_BLUE = colors.HexColor("#E8F1FA")
ACCENT_TEAL = colors.HexColor("#2CA6A4")
HIGHLIGHT_GREY = colors.HexColor("#F5F7FA")
BRAND_RED = colors.HexColor("#C8102E")
BRAND_YELLOW = colors.HexColor("#F7C600")

NEWSLETTER_INDEX_ITEMS = [
    "Industry Pulse",
    "Regulatory Watch",
    "Competitor Intelligence",
    "Key Takeaways",
]

# Sub-index entries shown under their parent items on the Index page.
NEWSLETTER_INDEX_SUBITEMS: Dict[str, List[str]] = {
    "Competitor Intelligence": [
        "Growth & Strategy",
        "Funding & Capital",
        "Risk & Governance",
        "Operational Signals",
    ],
}

# Consolidated colour palette.
INDEX_SECTION_COLORS: Dict[str, str] = {
    "Industry Pulse":          "#1B7A3A",   # forest green
    "Regulatory Watch":        "#C8102E",   # brand red
    "Competitor Intelligence": "#0F2D63",   # deep navy
    "Key Takeaways":           "#D97706",   # amber
}

CI_SUBSECTION_COLORS: Dict[str, str] = {
    "Growth & Strategy":   "#0E7C7B",   # teal
    "Funding & Capital":   "#7E22CE",   # purple
    "Risk & Governance":   "#9F1239",   # burgundy
    "Operational Signals": "#475569",   # slate
}

# FIX 4: Per-section soft background tones for _insight_box
SECTION_BG_COLORS: Dict[str, str] = {
    "Industry Pulse":    "#F0F7F0",   # pale green
    "Regulatory Watch":  "#FEF3F2",   # warm rose
    "Key Takeaways":     "#FFFBEB",   # warm amber tint
    # CI sub-sections
    "Growth & Strategy":   "#F0FAFA",  # pale teal
    "Funding & Capital":   "#FAF5FF",  # pale purple
    "Risk & Governance":   "#FFF1F3",  # pale burgundy
    "Operational Signals": "#F8FAFC",  # pale slate
}
DEFAULT_BG = "#F5F7FA"


def _build_styles():
    base = getSampleStyleSheet()
    return {
        "TitleStyle": ParagraphStyle(
            "TitleStyle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            alignment=TA_LEFT,
            spaceAfter=12,
            textColor=PRIMARY_BLUE,
        ),
        "SubtitleStyle": ParagraphStyle(
            "SubtitleStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            alignment=TA_LEFT,
            spaceAfter=8,
            textColor=colors.HexColor("#2F3A4A"),
        ),
        "SectionHeaderStyle": ParagraphStyle(
            "SectionHeaderStyle",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            alignment=TA_LEFT,
            spaceBefore=12,
            spaceAfter=10,
            textColor=PRIMARY_BLUE,
        ),
        "SubsectionHeaderStyle": ParagraphStyle(
            "SubsectionHeaderStyle",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            alignment=TA_LEFT,
            spaceBefore=8,
            spaceAfter=6,
            textColor=PRIMARY_BLUE,
        ),
        "BodyStyle": ParagraphStyle(
            "BodyStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            alignment=TA_LEFT,
            spaceAfter=4,
            textColor=colors.HexColor("#1F2937"),
        ),
        "BulletStyle": ParagraphStyle(
            "BulletStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            alignment=TA_LEFT,
            leftIndent=14,
            bulletIndent=4,
            spaceAfter=3,
            textColor=colors.HexColor("#1F2937"),
        ),
        "IndentedBodyStyle": ParagraphStyle(
            "IndentedBodyStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=15,
            alignment=TA_LEFT,
            leftIndent=26,
            spaceAfter=2,
            textColor=colors.HexColor("#1F2937"),
        ),
        "TableCellStyle": ParagraphStyle(
            "TableCellStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            alignment=TA_LEFT,
        ),
        "CoverMetaStyle": ParagraphStyle(
            "CoverMetaStyle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1F3C88"),
            spaceAfter=6,
        ),
        "IndexPrimaryStyle": ParagraphStyle(
            "IndexPrimaryStyle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_LEFT,
            leftIndent=0,
            spaceBefore=0,
            spaceAfter=4,
            textColor=PRIMARY_BLUE,
        ),
        "IndexSubStyle": ParagraphStyle(
            "IndexSubStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=17,
            alignment=TA_LEFT,
            leftIndent=18,
            bulletIndent=6,
            spaceAfter=2,
            textColor=colors.HexColor("#2F3A4A"),
        ),
        # FIX 3: smaller style for competitor names listed under sub-cards in the index
        "IndexCompetitorStyle": ParagraphStyle(
            "IndexCompetitorStyle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            alignment=TA_LEFT,
            leftIndent=0,
            spaceBefore=0,
            spaceAfter=1,
            textColor=colors.HexColor("#374151"),
        ),
    }


def _normalize_currency(text: str) -> str:
    return (text or "").replace("₹", "Rs")


def _sanitize(text: str) -> str:
    normalized = _normalize_currency(text or "")
    return normalized.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clean_markdown_line_noise(text: str) -> str:
    lines = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^#{1,6}\s*c\d{2}[-\s].*", line.lower()):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _direction_icon(direction: str) -> str:
    val = (direction or "").strip().lower()
    if val in {"positive", "up", "bullish", "improving"}:
        return "🟢"
    if val in {"negative", "down", "bearish", "weakening"}:
        return "🔴"
    return "🟡"


def _signal_table(signals: Iterable[WeeklySignal], styles: dict) -> Table:
    rows: List[List[Paragraph]] = [
        [
            Paragraph("<b>Signal Type</b>", styles["TableCellStyle"]),
            Paragraph("<b>Direction</b>", styles["TableCellStyle"]),
            Paragraph("<b>Impact</b>", styles["TableCellStyle"]),
            Paragraph("<b>Headline</b>", styles["TableCellStyle"]),
            Paragraph("<b>Summary</b>", styles["TableCellStyle"]),
        ]
    ]

    for signal in signals:
        icon = _direction_icon(signal.direction)
        rows.append(
            [
                Paragraph(_sanitize(signal.signal_type or "N/A"), styles["TableCellStyle"]),
                Paragraph(_sanitize(f"{icon} {signal.direction or 'mixed'}"), styles["TableCellStyle"]),
                Paragraph(_sanitize(signal.impact or "N/A"), styles["TableCellStyle"]),
                Paragraph(_sanitize(signal.headline or "Not found in source reviewed"), styles["TableCellStyle"]),
                Paragraph(_sanitize(signal.summary or "Not found in source reviewed"), styles["TableCellStyle"]),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                Paragraph("N/A", styles["TableCellStyle"]),
                Paragraph("🟡 mixed", styles["TableCellStyle"]),
                Paragraph("N/A", styles["TableCellStyle"]),
                Paragraph("Not found in source reviewed", styles["TableCellStyle"]),
                Paragraph("No key signals were extracted for this section.", styles["TableCellStyle"]),
            ]
        )

    table = Table(rows, colWidths=[0.9 * inch, 0.95 * inch, 0.8 * inch, 2.0 * inch, 2.35 * inch], hAlign="LEFT")
    _apply_standard_table_style(table)
    return table


def _apply_standard_table_style(table: Table) -> None:
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EDF5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111111")),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C7CDD9")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFD")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )


def _split_long_bullet(text: str, max_chunk_len: int = 180) -> List[str]:
    clean = _clean_markdown_line_noise(text)
    if len(clean) <= max_chunk_len:
        return [clean]
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(f"{current} {sentence}") > max_chunk_len:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    return chunks or [clean]


def _bullets(lines: List[str], styles: dict, empty_text: str = "Not found in source reviewed") -> List[Paragraph]:
    items = [_clean_markdown_line_noise(str(line)) for line in lines if str(line).strip()]
    items = [item for item in items if item]
    if not items:
        items = [empty_text]
    paragraphs: List[Paragraph] = []
    for item in items:
        chunks = _split_long_bullet(item)
        if len(chunks) == 1:
            paragraphs.append(Paragraph(_sanitize(chunks[0]), styles["BulletStyle"], bulletText="•"))
            continue
        paragraphs.append(Paragraph(_sanitize(chunks[0]), styles["BulletStyle"], bulletText="•"))
        for chunk in chunks[1:]:
            paragraphs.append(Paragraph(_sanitize(chunk), styles["BodyStyle"]))
    return paragraphs


def _separator() -> HRFlowable:
    return HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#C9D8EA"), spaceBefore=8, spaceAfter=12)


# FIX 1: subtle thin divider between consecutive insight boxes
def _box_divider() -> HRFlowable:
    """Very light hairline divider between consecutive callout boxes.
    Provides visual separation without adding excessive whitespace."""
    return HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#E2E8F0"), spaceBefore=4, spaceAfter=4)


# FIX: single, correct _header_footer — previous version had stray Table code injected
def _header_footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    width, height = A4
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(doc.leftMargin, height - 28, "Weekly Intelligence Report")
    canvas.drawRightString(width - doc.rightMargin, 20, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _company_display_name(raw_company: str, idx: int) -> str:
    clean = (raw_company or "").strip()
    clean = re.sub(r"^c\d{2}\s*-\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^#+\s*", "", clean).strip()
    return f"C{idx:02d} - {clean}" if clean else f"C{idx:02d}"


def _reference_paragraphs(section, styles: dict) -> List[Paragraph]:
    if not section.references:
        return [Paragraph("Not found in source reviewed", styles["BodyStyle"])]

    items = []
    for ref in section.references:
        title = _sanitize(ref.title or "Source")
        source = _sanitize(ref.source or "Unknown source")
        date = _sanitize(ref.date or "Unknown date")
        canonical_url = _sanitize(getattr(ref, "canonical_url", "") or ref.url or "")
        original_url = _sanitize(getattr(ref, "original_url", "") or "")
        if canonical_url:
            line = f"• <b>{title}</b> | {source} | {date} | <link href='{canonical_url}' color='blue'>{canonical_url}</link>"
            if original_url and original_url != canonical_url:
                line += f" | original: {original_url}"
        elif original_url:
            line = (
                f"• <b>{title}</b> | {source} | {date} | "
                f"<link href='{original_url}' color='blue'>{original_url}</link> | aggregator"
            )
        else:
            line = f"• <b>{title}</b> | {source} | {date}"
        items.append(Paragraph(line, styles["BodyStyle"]))
    return items


def render_report_pdf(bundle: ReportBundle, out_path: str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    styles = _build_styles()
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.7 * inch,
        title=bundle.title or "Weekly Intelligence Report",
    )

    story = []

    # Cover page
    story.append(Spacer(1, 1.2 * inch))
    story.append(Paragraph(_sanitize(bundle.title or "Weekly Intelligence Report"), styles["TitleStyle"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(
        Paragraph(
            _sanitize(bundle.time_period or "Reporting period not specified"),
            styles["CoverMetaStyle"],
        )
    )
    story.append(
        Paragraph(
            "Consulting-grade competitive intelligence brief for weekly market and company signals",
            styles["SubtitleStyle"],
        )
    )
    if bundle.news_sources:
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph("<b>Source Scope</b>", styles["SectionHeaderStyle"]))
        for source in bundle.news_sources:
            story.append(Paragraph(_sanitize(source), styles["BulletStyle"], bulletText="•"))

    story.append(PageBreak())

    # Executive summary page
    story.append(Paragraph("Executive Summary", styles["TitleStyle"]))
    story.append(_separator())
    cleaned_bundle_summary = _clean_markdown_line_noise(bundle.executive_summary or "")
    summary_parts = [chunk.strip() for chunk in cleaned_bundle_summary.split("\n") if chunk.strip()]
    if not summary_parts:
        summary_parts = ["Not found in source reviewed"]
    for paragraph in summary_parts:
        story.append(Paragraph(_sanitize(paragraph), styles["BodyStyle"]))
        story.append(Spacer(1, 0.06 * inch))

    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("Key Signals Across Coverage", styles["SectionHeaderStyle"]))
    story.append(_signal_table(bundle.top_signals, styles))
    if bundle.policy_watch:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_separator())
        story.append(Paragraph("Policy Watch (RBI/NHB)", styles["SectionHeaderStyle"]))
        policy_rows = [
            [
                Paragraph("<b>Source</b>", styles["TableCellStyle"]),
                Paragraph("<b>Date</b>", styles["TableCellStyle"]),
                Paragraph("<b>Title</b>", styles["TableCellStyle"]),
            ]
        ]
        for row in bundle.policy_watch[:12]:
            policy_rows.append(
                [
                    Paragraph(_sanitize(str(row.get("source", ""))), styles["TableCellStyle"]),
                    Paragraph(_sanitize(str(row.get("date", ""))), styles["TableCellStyle"]),
                    Paragraph(_sanitize(str(row.get("title", ""))), styles["TableCellStyle"]),
                ]
            )
        policy_table = Table(policy_rows, colWidths=[0.9 * inch, 0.9 * inch, 4.4 * inch], hAlign="LEFT")
        _apply_standard_table_style(policy_table)
        story.append(policy_table)

    if bundle.rating_watch:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_separator())
        story.append(Paragraph("Rating Watch (CRISIL/ICRA)", styles["SectionHeaderStyle"]))
        rating_rows = [
            [
                Paragraph("<b>Agency</b>", styles["TableCellStyle"]),
                Paragraph("<b>Date</b>", styles["TableCellStyle"]),
                Paragraph("<b>Action / Note</b>", styles["TableCellStyle"]),
            ]
        ]
        for row in bundle.rating_watch[:12]:
            rating_rows.append(
                [
                    Paragraph(_sanitize(str(row.get("source", ""))), styles["TableCellStyle"]),
                    Paragraph(_sanitize(str(row.get("date", ""))), styles["TableCellStyle"]),
                    Paragraph(_sanitize(str(row.get("title", ""))), styles["TableCellStyle"]),
                ]
            )
        rating_table = Table(rating_rows, colWidths=[0.9 * inch, 0.9 * inch, 4.4 * inch], hAlign="LEFT")
        _apply_standard_table_style(rating_table)
        story.append(rating_table)

    # Company sections, one page each
    for idx, section in enumerate(bundle.weekly_company_sections, start=1):
        story.append(PageBreak())
        story.append(Paragraph(_sanitize(_company_display_name(section.company, idx)), styles["TitleStyle"]))
        story.append(Paragraph("Executive Takeaway", styles["SectionHeaderStyle"]))

        cleaned_takeaway = _clean_markdown_line_noise(section.executive_summary or "")
        takeaway_lines = [line.strip() for line in cleaned_takeaway.split("\n") if line.strip()]
        if not takeaway_lines:
            takeaway_lines = ["Not found in source reviewed"]
        for line in takeaway_lines:
            story.append(Paragraph(_sanitize(line), styles["BodyStyle"]))

        story.append(_separator())
        story.append(Paragraph("Key Signals", styles["SectionHeaderStyle"]))
        story.append(_signal_table(section.top_signals, styles))

        story.append(Spacer(1, 0.12 * inch))
        story.append(_separator())
        story.append(Paragraph("Highlights", styles["SectionHeaderStyle"]))
        story.extend(_bullets(section.company_highlights, styles))

        if section.sector_macro_context:
            story.append(Spacer(1, 0.06 * inch))
            story.append(_separator())
            story.append(Paragraph("Macro Context", styles["SectionHeaderStyle"]))
            story.extend(_bullets(section.sector_macro_context, styles))

        story.append(Spacer(1, 0.06 * inch))
        story.append(_separator())
        story.append(Paragraph("Caveats", styles["SectionHeaderStyle"]))
        story.extend(_bullets(section.caveats, styles, empty_text="None noted"))

        story.append(Spacer(1, 0.06 * inch))
        story.append(_separator())
        story.append(Paragraph("References", styles["SectionHeaderStyle"]))
        story.extend(_reference_paragraphs(section, styles))

        if idx < len(bundle.weekly_company_sections) - 1:
            story.append(Spacer(1, 0.12 * inch))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)


def render_agentic_digest_pdf(
    *,
    title: str,
    time_period: str,
    industry_summary: List[str],
    regulatory_updates: List[Dict[str, Any] | str],
    competitor_rows: List[Dict[str, Any]],
    out_path: str,
) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    styles = _build_styles()
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.7 * inch,
        title=title,
    )

    story = [
        Paragraph(_sanitize(title), styles["TitleStyle"]),
        Paragraph(_sanitize(time_period or "Reporting period not specified"), styles["CoverMetaStyle"]),
        _separator(),
        Paragraph("Industry Summary", styles["SectionHeaderStyle"]),
    ]
    summary_lines = [_clean_markdown_line_noise(line) for line in (industry_summary or []) if str(line).strip()]
    if not summary_lines:
        summary_lines = ["Not found in source reviewed"]
    for line in summary_lines:
        story.append(Paragraph(_sanitize(line), styles["BulletStyle"], bulletText="•"))

    story.extend([Spacer(1, 0.1 * inch), _separator(), Paragraph("Regulatory Updates (Policies / Ratings)", styles["SectionHeaderStyle"])])
    updates = list(regulatory_updates or [])
    if not updates:
        updates = ["Not found in source reviewed"]
    for update in updates:
        if isinstance(update, dict):
            line = _clean_markdown_line_noise(str(update.get("line", "")).strip())
            summary = _clean_markdown_line_noise(str(update.get("summary", "")).strip())
            signal = _clean_markdown_line_noise(str(update.get("signal", "")).strip())
            canonical_url = str(update.get("canonical_url", "")).strip() or str(update.get("url", "")).strip()
            original_url = str(update.get("original_url", "")).strip()
            if not line:
                line = "Not found in source reviewed"
            if not summary:
                summary = "Not found in source reviewed"
            if not signal:
                signal = "Not found in source reviewed"
            if canonical_url:
                safe_url = _sanitize(canonical_url)
                content = (
                    f"{_sanitize(line)}<br/>"
                    f"<b>Summary:</b> {_sanitize(summary)}<br/>"
                    f"<b>Signal:</b> {_sanitize(signal)} "
                    f"(<link href='{safe_url}' color='blue'>source</link>)"
                )
                if original_url and original_url != canonical_url:
                    content += f"<br/><b>Original aggregator URL:</b> {_sanitize(original_url)}"
            elif original_url:
                safe_url = _sanitize(original_url)
                content = (
                    f"{_sanitize(line)}<br/>"
                    f"<b>Summary:</b> {_sanitize(summary)}<br/>"
                    f"<b>Signal:</b> {_sanitize(signal)} "
                    f"(<link href='{safe_url}' color='blue'>aggregator source</link>)<br/>"
                    f"<b>URL type:</b> Aggregator (canonical publisher URL unavailable)"
                )
            else:
                content = (
                    f"{_sanitize(line)}<br/>"
                    f"<b>Summary:</b> {_sanitize(summary)}<br/>"
                    f"<b>Signal:</b> {_sanitize(signal)}"
                )
        else:
            line = _clean_markdown_line_noise(str(update).strip()) or "Not found in source reviewed"
            content = _sanitize(line)
        story.append(Paragraph(content, styles["BulletStyle"], bulletText="•"))

    story.extend([Spacer(1, 0.1 * inch), _separator(), Paragraph("Competitor Summary Table", styles["SectionHeaderStyle"])])

    rows: List[List[Paragraph]] = [
        [
            Paragraph("<b>Competitor Name</b>", styles["TableCellStyle"]),
            Paragraph("<b>This Week News Precise Summary</b>", styles["TableCellStyle"]),
            Paragraph("<b>Signal Types</b>", styles["TableCellStyle"]),
        ]
    ]
    for row in competitor_rows:
        rows.append(
            [
                Paragraph(_sanitize(str(row.get("company", "Unknown"))), styles["TableCellStyle"]),
                Paragraph(_sanitize(str(row.get("weekly_summary", "Not found in source reviewed"))), styles["TableCellStyle"]),
                Paragraph(_sanitize(", ".join(row.get("signal_types", [])) or "Not found in source reviewed"), styles["TableCellStyle"]),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                Paragraph("No source-backed competitor updates found", styles["TableCellStyle"]),
                Paragraph("Not found in source reviewed", styles["TableCellStyle"]),
                Paragraph("Not found in source reviewed", styles["TableCellStyle"]),
            ]
        )

    table = Table(rows, colWidths=[1.6 * inch, 3.7 * inch, 1.5 * inch], hAlign="LEFT")
    _apply_standard_table_style(table)
    story.append(table)
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)


# FIX 4: accent_hex drives left-edge colour; section_key drives soft background tone
def _insight_box(
    title: str,
    body: str,
    styles: dict,
    accent_hex: Optional[str] = None,
    section_key: Optional[str] = None,
    variant: str = "normal",
) -> Table:
    """Tinted callout box with a coloured left-edge accent bar and section-specific
    soft background.  `section_key` is the section or CI sub-section name used to
    look up the per-section background colour from SECTION_BG_COLORS.
    """
    rows = [[Paragraph(f"<b>{_sanitize(title)}</b>", styles["BodyStyle"])]]
    body_clean = (body or "").strip()
    if body_clean:
        rows.append([Paragraph(_sanitize(body_clean), styles["BodyStyle"])])

    bg_hex = SECTION_BG_COLORS.get(section_key or "", DEFAULT_BG) if section_key else DEFAULT_BG
    bg_color = colors.HexColor(bg_hex)
    if variant == "summary":
        box_width = 6.5 * inch
        border_width = 1.4
        accent_width = 5
        left_padding = 14
        top_padding = 10
        bottom_padding = 10
    else:
        box_width = 6.5 * inch
        border_width = 0.7
        accent_width = 4
        left_padding = 10
        top_padding = 6
        bottom_padding = 6

    # table = Table(rows, colWidths=[6.5 * inch], hAlign="LEFT")
    # table_style = [
    #     ("BACKGROUND", (0, 0), (-1, -1), bg_color),
    #     ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#C9D8EA")),
    #     ("LEFTPADDING", (0, 0), (-1, -1), 10 if accent_hex else 8),
    #     ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    #     ("TOPPADDING", (0, 0), (-1, -1), 6),
    #     ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    # ]
    table = Table(rows, colWidths=[box_width], hAlign="LEFT")

    table_style = [
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), border_width, colors.HexColor("#C9D8EA")),
        ("LEFTPADDING", (0, 0), (-1, -1), left_padding if accent_hex else 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), top_padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), bottom_padding),
]
    if accent_hex:
        table_style.append(("LINEBEFORE", (0, 0), (0, -1), accent_width, colors.HexColor(accent_hex)))
    table.setStyle(TableStyle(table_style))
    return table

def _summary_hero_box(body, styles, accent_hex):
    header = Paragraph(
        f"<font color='{accent_hex}'><b>EXECUTIVE SUMMARY</b></font>",
        ParagraphStyle(
            "SummaryHeader",
            parent=styles["BodyStyle"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            alignment=TA_LEFT,
        )
    )

    body_para = Paragraph(
        _sanitize(body),
        ParagraphStyle(
            "SummaryBody",
            parent=styles["BodyStyle"],
            fontSize=10.5,
            leading=15,
        )
    )

    table = Table(
        [[header], [body_para]],
        colWidths=[6.5 * inch],
        hAlign="LEFT"
    )

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1),
         SECTION_BG_COLORS.get("Industry Pulse", DEFAULT_BG)),
        ("LINEBEFORE", (0, 0), (0, -1), 5, colors.HexColor(accent_hex)),
        ("BOX", (0, 0), (-1, -1), 1.1, colors.HexColor("#BFCEDB")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#D6E0EA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    return table
# ----- Industry Pulse helpers -----

def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _best_sentence_for_highlight(highlight: str, sentences: List[str], used: set) -> str:
    if not highlight or not sentences:
        return ""
    h_words = {w for w in re.findall(r"\w{4,}", highlight.lower())}
    best, best_score = "", 0
    for s in sentences:
        if s in used:
            continue
        s_words = {w for w in re.findall(r"\w{4,}", s.lower())}
        score = len(h_words & s_words)
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 1 else ""


def _lerp_color(start: colors.Color, end: colors.Color, ratio: float) -> colors.Color:
    r = start.red + (end.red - start.red) * ratio
    g = start.green + (end.green - start.green) * ratio
    b = start.blue + (end.blue - start.blue) * ratio
    return colors.Color(r, g, b)


def _cover_banner(width: float = 6.5 * inch, height: float = (6.5 / 3.0) * inch) -> Drawing:
    drawing = Drawing(width, height)

    light_blue = colors.HexColor("#6EA7E8")
    medium_blue = colors.HexColor("#2F6FB6")
    stripe_count = 120
    stripe_width = width / stripe_count
    for idx in range(stripe_count):
        blend = idx / max(stripe_count - 1, 1)
        stripe_color = _lerp_color(light_blue, medium_blue, blend)
        drawing.add(Rect(idx * stripe_width, 0, stripe_width + 0.8, height, fillColor=stripe_color, strokeColor=None))

    watermark_color = colors.Color(1, 1, 1, alpha=0.12)

    # House watermark (left)
    house_x = 0.85 * inch
    house_y = 0.36 * inch
    drawing.add(Polygon([house_x - 34, house_y + 30, house_x, house_y + 58, house_x + 34, house_y + 30], fillColor=watermark_color, strokeColor=None))
    drawing.add(Rect(house_x - 24, house_y - 6, 48, 34, fillColor=watermark_color, strokeColor=None))
    drawing.add(Rect(house_x - 6, house_y - 6, 12, 18, fillColor=colors.Color(1, 1, 1, alpha=0.16), strokeColor=None))

    # Finance chart watermark (center)
    chart_x = width * 0.52
    chart_y = 0.5 * inch
    drawing.add(Line(chart_x - 52, chart_y - 6, chart_x + 50, chart_y - 6, strokeColor=watermark_color, strokeWidth=2))
    drawing.add(Line(chart_x - 52, chart_y - 6, chart_x - 52, chart_y + 40, strokeColor=watermark_color, strokeWidth=2))
    drawing.add(Polygon([chart_x - 46, chart_y + 2, chart_x - 20, chart_y + 14, chart_x + 4, chart_y + 10, chart_x + 26, chart_y + 24, chart_x + 44, chart_y + 36], fillColor=None, strokeColor=watermark_color, strokeWidth=3))
    drawing.add(Polygon([chart_x + 44, chart_y + 36, chart_x + 34, chart_y + 34, chart_x + 40, chart_y + 28], fillColor=watermark_color, strokeColor=None))

    # Rupee watermark (right)
    rupee_x = width - 0.95 * inch
    rupee_y = 0.62 * inch
    drawing.add(String(rupee_x, rupee_y, "₹", fontName="Helvetica-Bold", fontSize=60, fillColor=watermark_color, textAnchor="middle"))
    drawing.add(Circle(rupee_x, rupee_y + 4, 36, fillColor=None, strokeColor=colors.Color(1, 1, 1, alpha=0.08), strokeWidth=2))

    drawing.add(
        String(
            0.42 * inch,
            height - 0.86 * inch,
            "Housing Finance Weekly Digest",
            fontName="Helvetica-Bold",
            fontSize=24,
            fillColor=colors.Color(1, 1, 1, alpha=0.97),
        )
    )
    drawing.add(
        String(
            0.42 * inch,
            height - 1.28 * inch,
            "Regulatory, Industry & Competitive Intelligence",
            fontName="Helvetica",
            fontSize=12,
            fillColor=colors.Color(1, 1, 1, alpha=0.92),
        )
    )
    return drawing


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        cleaned = _clean_markdown_line_noise(value).strip().lower()
        return (not cleaned) or ("not found in source reviewed" in cleaned)
    if isinstance(value, list):
        return len([v for v in value if not _is_empty_value(v)]) == 0
    if isinstance(value, dict):
        return all(_is_empty_value(v) for v in value.values())
    return False


def _split_date_range(date_range: str) -> tuple[str, str]:
    raw = (date_range or "").strip()
    for sep in (" to ", " - ", "–"):
        if sep in raw:
            left, right = raw.split(sep, 1)
            return left.strip(), right.strip()
    return raw or "Week start", raw or "Week end"


def _newsletter_page_decor(week_start: str, week_end: str):  # type: ignore[no-untyped-def]
    candidate_paths = [
        Path("pnb_housing_finance_ltd_logo.jpeg"),
        Path("/assets/pnb_housing_finance_ltd_logo.jpeg"),
        Path("image_bca198f4.png"),
        Path("/assets/header_banner.png"),
    ]
    logo_path = next((path for path in candidate_paths if path.exists()), None)
    title = "Housing Finance Weekly Digest"
    subtitle = "Regulatory, Industry & Competitive Intelligence"
    date_line = f"{week_start} to {week_end}"

    def _draw(canvas, doc) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        width, height = A4
        banner_height = 98
        banner_y = height - banner_height
        canvas.setFillColor(BRAND_RED)
        canvas.rect(0, banner_y, width, banner_height, stroke=0, fill=1)
        canvas.setFillColor(BRAND_YELLOW)
        canvas.rect(0, banner_y, width, 18, stroke=0, fill=1)

        if logo_path:
            canvas.drawImage(
                ImageReader(str(logo_path)),
                doc.leftMargin,
                banner_y + 22,
                width=95,
                height=62,
                preserveAspectRatio=True,
                mask="auto",
            )

        text_x = doc.leftMargin + 108 if logo_path else doc.leftMargin
        canvas.setFillColor(BRAND_YELLOW)
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawString(text_x, height - 40, title)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 11)
        canvas.drawString(text_x, height - 57, subtitle)
        canvas.setFont("Helvetica", 11)
        canvas.drawString(text_x, height - 73, date_line)
        canvas.setFillColor(colors.HexColor("#444444"))
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(width - doc.rightMargin, 20, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return _draw


def _auto_generate_takeaways(newsletter: Dict[str, Any]) -> List[str]:
    industry = newsletter.get("industry_pulse", {}) if isinstance(newsletter.get("industry_pulse", {}), dict) else {}
    regulatory = newsletter.get("regulatory_watch", []) if isinstance(newsletter.get("regulatory_watch", []), list) else []
    competitor = newsletter.get("competitor_intelligence", {}) if isinstance(newsletter.get("competitor_intelligence", {}), dict) else {}
    grouped = competitor.get("grouped_insights", []) if isinstance(competitor.get("grouped_insights", []), list) else []

    industry_summary = str(industry.get("summary_paragraph", "")).strip()
    regulatory_count = len([r for r in regulatory if isinstance(r, dict) and not _is_empty_value(r)])
    competitor_count = 0
    for group in grouped:
        if isinstance(group, dict):
            competitor_count += len([i for i in group.get("items", []) if isinstance(i, dict) and not _is_empty_value(i)])

    points = [
        "Rising regulatory oversight alongside active lender moves suggests the sector is entering a compliance-led consolidation phase.",
        "Industry demand signals and competitor execution indicate firms are balancing growth ambitions with tighter governance expectations.",
        "Cross-sectional evidence points to stronger differentiation for players combining transparent disclosures with distribution expansion.",
        "Regulatory triggers, operating shifts, and market pattern changes together imply higher execution premiums over the next 1–2 quarters.",
        "Winning institutions are likely to be those coordinating risk, capital, and go-to-market decisions as one integrated strategy.",
    ]
    if not industry_summary and regulatory_count == 0 and competitor_count == 0:
        return []
    return points[:5]


def _short_heading(text: str, max_words: int = 10) -> str:
    """Compress a full headline into a punchy 6-10 word callout title."""
    if not text:
        return ""
    t = text.strip().rstrip(".:!?")
    for sep in (":", ";", " – ", " — ", ","):
        idx = t.find(sep)
        if 4 <= idx <= 80:
            t = t[:idx].strip()
            break
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words])
    return t


def _is_near_duplicate(a: str, b: str, threshold: float = 0.78) -> bool:
    """True when two strings are essentially the same."""
    if not a or not b:
        return False
    import difflib
    def _norm(s: str) -> str:
        s = re.sub(r"\s+", " ", s.lower().strip())
        s = re.sub(r"[\.,;:!\?]", "", s)
        return s
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= threshold


def _polish_event_text(event: str) -> str:
    """Capitalise first letter, strip trailing dots/ellipses, ensure single terminal period."""
    if not event:
        return ""
    s = event.strip()
    s = re.sub(r"[\.…]{2,}\s*$", "", s)
    s = re.sub(r"[,;:\s]+$", "", s)
    if s and s[0].isalpha() and s[0] != s[0].upper():
        s = s[0].upper() + s[1:]
    if s and s[-1] not in ".!?":
        s = s + "."
    return s


def _competitor_narrative(item: Dict[str, Any], styles: dict) -> Paragraph:
    company = _sanitize(str(item.get("company", "Unknown")))
    event = _polish_event_text(_sanitize(str(item.get("event", ""))))
    narrative = f"<b>{company}:</b> {event}" if event else f"<b>{company}.</b>"
    return Paragraph(narrative, styles["BodyStyle"])


# FIX 3: helper to extract deduplicated competitor names per CI sub-section
def _extract_ci_competitor_names(grouped_insights: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Returns {category: [company, ...]} with duplicates removed, skipping empty items."""
    result: Dict[str, List[str]] = {}
    for group in grouped_insights:
        if not isinstance(group, dict):
            continue
        category = str(group.get("category", "")).strip()
        if not category:
            continue
        seen: set = set()
        names: List[str] = []
        for item in group.get("items", []):
            if not (isinstance(item, dict) and not _is_empty_value(item)):
                continue
            company = str(item.get("company", "")).strip()
            if company and company.lower() not in seen:
                seen.add(company.lower())
                names.append(company)
        if names:
            result[category] = names
    return result


def render_newsletter_pdf(newsletter: Dict[str, Any], out_path: str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    styles = _build_styles()

    cover = newsletter.get("cover", {}) if isinstance(newsletter, dict) else {}
    industry_pulse = newsletter.get("industry_pulse", {}) if isinstance(newsletter.get("industry_pulse", {}), dict) else {}
    regulatory_watch = newsletter.get("regulatory_watch", []) if isinstance(newsletter.get("regulatory_watch", []), list) else []
    competitor_intel = (
        newsletter.get("competitor_intelligence", {})
        if isinstance(newsletter.get("competitor_intelligence", {}), dict)
        else {}
    )
    grouped_insights = competitor_intel.get("grouped_insights", []) if isinstance(competitor_intel.get("grouped_insights", []), list) else []
    market_patterns = newsletter.get("market_patterns", []) if isinstance(newsletter.get("market_patterns", []), list) else []
    key_takeaways = newsletter.get("key_takeaways", []) if isinstance(newsletter.get("key_takeaways", []), list) else []
    method_caveats = newsletter.get("method_caveats", [])
    if not isinstance(method_caveats, list):
        method_caveats = []

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=1.9 * inch,
        bottomMargin=0.7 * inch,
        title=str(cover.get("title", "Weekly Housing Finance Intelligence")),
    )
    story = []

    week_start, week_end = _split_date_range(str(cover.get("date_range", "Reporting period not specified")))
    on_page = _newsletter_page_decor(week_start, week_end)

    sections_to_render: List[str] = []
    if not _is_empty_value(industry_pulse):
        sections_to_render.append("Industry Pulse")
    valid_regulatory = [row for row in regulatory_watch if isinstance(row, dict) and not _is_empty_value(row)]
    if valid_regulatory:
        sections_to_render.append("Regulatory Watch")
    valid_groups = [group for group in grouped_insights if isinstance(group, dict) and not _is_empty_value(group)]
    if valid_groups:
        sections_to_render.append("Competitor Intelligence")
    valid_patterns = [row for row in market_patterns if isinstance(row, dict) and not _is_empty_value(row)]
    if valid_patterns:
        sections_to_render.append("Market Signals & Patterns")
    valid_takeaways = [str(v) for v in key_takeaways if not _is_empty_value(v)]
    if len(valid_takeaways) < 3:
        valid_takeaways = _auto_generate_takeaways(newsletter)
    if valid_takeaways:
        sections_to_render.append("Key Takeaways")
    valid_caveats = [str(v) for v in method_caveats if not _is_empty_value(v)]
    if valid_caveats:
        sections_to_render.append("Method & Caveats")

    # Cover page
    story.append(Spacer(1, 0.9 * inch))
    story.append(Paragraph(_sanitize(str(cover.get("title", "Housing Finance Weekly Digest"))), styles["TitleStyle"]))
    story.append(Paragraph(_sanitize(str(cover.get("date_range", "Reporting period not specified"))), styles["CoverMetaStyle"]))
    story.append(Paragraph("Regulatory, Industry & Competitive Intelligence", styles["SubtitleStyle"]))
    story.append(PageBreak())

    # ------------------------------------------------------------------ #
    # Index page                                                           #
    # FIX 3: Competitor Intelligence sub-cards now list actual competitor  #
    # names drawn dynamically from grouped_insights.                       #
    # ------------------------------------------------------------------ #
    story.append(Paragraph("Index", styles["TitleStyle"]))
    story.append(_separator())
    story.append(Spacer(1, 0.1 * inch))

    # Pre-compute competitor names per sub-section for the index
    ci_competitor_names = _extract_ci_competitor_names(valid_groups)

    def _index_card(label: str, accent_hex: str, *, width: float = 6.5 * inch,
                    bg: str = "#F7F9FC") -> Table:
        label_para = Paragraph(
            f"<font color='{accent_hex}'><b>{_sanitize(label)}</b></font>",
            styles["IndexPrimaryStyle"],
        )
        strip_w = 0.18 * inch
        card = Table(
            [["", label_para]],
            colWidths=[strip_w, width - strip_w],
            hAlign="LEFT",
        )
        card.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), colors.HexColor(accent_hex)),
            ("BACKGROUND",   (1, 0), (1, 0), colors.HexColor(bg)),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING",  (1, 0), (1, 0), 14),
            ("RIGHTPADDING", (1, 0), (1, 0), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BOX",          (0, 0), (-1, -1), 0.5, colors.HexColor("#D2D7E2")),
        ]))
        return card

    def _index_card_with_names(label: str, accent_hex: str, names: List[str],
                               *, width: float = 6.15 * inch,
                               bg: str = "#F7F9FC") -> Table:
        """Sub-section card that also lists competitor names below the label."""
        cell_content: List[Paragraph] = [
            Paragraph(
                f"<font color='{accent_hex}'><b>{_sanitize(label)}</b></font>",
                styles["IndexPrimaryStyle"],
            )
        ]
        for name in names:
            cell_content.append(
                Paragraph(
                    f"<font color='#374151'>– {_sanitize(name)}</font>",
                    styles["IndexCompetitorStyle"],
                )
            )
        strip_w = 0.18 * inch
        card = Table(
            [["", cell_content]],
            colWidths=[strip_w, width - strip_w],
            hAlign="LEFT",
        )
        card.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), colors.HexColor(accent_hex)),
            ("BACKGROUND",   (1, 0), (1, 0), colors.HexColor(bg)),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING",  (1, 0), (1, 0), 14),
            ("RIGHTPADDING", (1, 0), (1, 0), 14),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
            ("BOX",          (0, 0), (-1, -1), 0.5, colors.HexColor("#D2D7E2")),
        ]))
        return card

    def _indent_flowable(flow: Any, indent: float = 0.35 * inch) -> Table:
        wrap = Table([["", flow]], colWidths=[indent, 6.5 * inch - indent], hAlign="LEFT")
        wrap.setStyle(TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ]))
        return wrap

    for item in [it for it in NEWSLETTER_INDEX_ITEMS if not _is_empty_value(it)]:
        accent = INDEX_SECTION_COLORS.get(item, "#0F2D63")

        story.append(_index_card(item, accent))
        story.append(Spacer(1, 0.08 * inch))

        if item == "Competitor Intelligence":
            ci_blocks = []

            for sub in NEWSLETTER_INDEX_SUBITEMS.get("Competitor Intelligence", []):
                sub_color = CI_SUBSECTION_COLORS.get(sub, "#475569")
                names_for_sub = ci_competitor_names.get(sub, [])

                if names_for_sub:
                    sub_card = _index_card_with_names(sub, sub_color, names_for_sub)
                else:
                    sub_card = _index_card(sub, sub_color, width=6.15 * inch)

                ci_blocks.append(_indent_flowable(sub_card))
                ci_blocks.append(Spacer(1, 0.05 * inch))

            story.append(KeepTogether(ci_blocks))
            story.append(Spacer(1, 0.04 * inch))

    story.append(PageBreak())

    section_started = False
    _SECTION_BREAK_THRESHOLD = 8 * inch

    def _start_section() -> None:
        nonlocal section_started
        if section_started:
            story.append(CondPageBreak(_SECTION_BREAK_THRESHOLD))
        section_started = True

    def _bullet_from_highlight(h: Any) -> str:
        if isinstance(h, dict):
            return str(h.get("pointer") or h.get("headline") or "").strip()
        return str(h or "").strip()

    industry_bullets = [
        b for b in (_bullet_from_highlight(v) for v in (industry_pulse.get("highlights", []) or []))
        if b and not _is_empty_value(b)
    ][:3]
    reg_bullets = []
    for reg in valid_regulatory[:3]:
        title = str(reg.get("title", "")).strip()
        impact = str(reg.get("impact", "")).strip()
        if title and impact:
            reg_bullets.append(f"{title}: {impact}")
    comp_bullets = []
    for group in valid_groups[:2]:
        for item in group.get("items", [])[:2]:
            if isinstance(item, dict) and not _is_empty_value(item):
                comp_bullets.append(f"{item.get('company', 'Unknown')}: {item.get('event', 'Not found in source reviewed')}")
    comp_bullets = comp_bullets[:3]

    if industry_bullets or reg_bullets or comp_bullets:
        _start_section()
        story.append(Paragraph("At a Glance", styles["SectionHeaderStyle"]))
        glance_rows: List[List[List[Paragraph]]] = []
        for heading, bullets in [("Industry", industry_bullets), ("Regulatory", reg_bullets), ("Competitor", comp_bullets)]:
            clean_bullets = [b for b in (bullets or []) if not _is_empty_value(b)]
            if not clean_bullets:
                continue
            cell: List[Paragraph] = [Paragraph(f"<b>{heading}</b>", styles["SubsectionHeaderStyle"])]
            for b in clean_bullets[:3]:
                cell.append(Paragraph(_sanitize(b), styles["BulletStyle"], bulletText="•"))
            glance_rows.append([cell])
        if glance_rows:
            glance_table = Table(glance_rows, colWidths=[6.24 * inch], hAlign="LEFT")
            glance_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOX", (0, 0), (-1, -1), 0.8, BRAND_RED),
                        ("INNERGRID", (0, 0), (-1, -1), 0.6, BRAND_YELLOW),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8D6")),
                    ]
                )
            )
            story.append(glance_table)
            story.append(Spacer(1, 0.2 * inch))

    # ------------------------------------------------------------------ #
    # Industry Pulse                                                        #
    # FIX 1: _box_divider() between consecutive callout boxes              #
    # FIX 4: section_key passed to _insight_box for per-section bg colour  #
    # ------------------------------------------------------------------ #
    if not _is_empty_value(industry_pulse):
        _start_section()
        ip_color = INDEX_SECTION_COLORS.get("Industry Pulse", "#1B7A3A")
        story.append(Paragraph(
            f"<font color='{ip_color}'>Industry Pulse</font>",
            styles["SectionHeaderStyle"],
        ))
        story.append(_separator())
        summary = str(industry_pulse.get("summary_paragraph", "")).strip()
        if not _is_empty_value(summary):
            story.append(_summary_hero_box(summary, styles, accent_hex=ip_color))
            story.append(Spacer(1, 0.25 * inch))  

        raw_highlights = industry_pulse.get("highlights", []) or []
        if raw_highlights:
            story.append(Spacer(1, 0.20 * inch))
            sentences = _split_sentences(summary)
            used_sentences: set = set()

            for h_idx, h in enumerate(raw_highlights[:3]):
                # FIX 1: add subtle divider between consecutive highlight boxes
                if h_idx > 0:
                    story.append(_box_divider())

                if isinstance(h, dict):
                    pointer = str(h.get("pointer") or h.get("headline") or "").strip()
                    impact = str(h.get("impact") or "").strip()
                    why = str(h.get("why_it_matters") or h.get("why") or "").strip()
                else:
                    pointer = str(h).strip()
                    impact = ""
                    why = ""

                if not pointer:
                    continue

                box_heading = _short_heading(pointer) or "Industry Update"
                # FIX 2: wrap entire highlight block (box + impact + why) in KeepTogether
                highlight_group: List[Any] = [
                    _insight_box(
                        box_heading, pointer, styles,
                        accent_hex=ip_color,
                        section_key="Industry Pulse",
                    ),
                    Spacer(1, 0.08 * inch),
                ]

                if impact and not _is_near_duplicate(pointer, impact):
                    highlight_group.append(Paragraph(
                        f"<font color='#2CA6A4'><b>Impact</b></font>: {_sanitize(impact)}",
                        styles["BodyStyle"],
                    ))
                if why and not _is_near_duplicate(pointer, why) and not _is_near_duplicate(impact, why):
                    highlight_group.append(Paragraph(
                        f"<font color='#2CA6A4'><b>Why it matters</b></font>: {_sanitize(why)}",
                        styles["BodyStyle"],
                    ))

                if not isinstance(h, dict):
                    deeper = _best_sentence_for_highlight(pointer, sentences, used_sentences)
                    if deeper:
                        used_sentences.add(deeper)
                        if not _is_near_duplicate(pointer, deeper):
                            highlight_group.append(Paragraph(_sanitize(deeper), styles["BodyStyle"]))

                # FIX 2: KeepTogether prevents the box splitting across pages
                story.append(KeepTogether(highlight_group))
                story.append(Spacer(1, 0.18 * inch))

    # ------------------------------------------------------------------ #
    # Regulatory Watch                                                      #
    # FIX 1: _box_divider() between entries                                #
    # FIX 2: each entry fully wrapped in KeepTogether                      #
    # FIX 4: section_key="Regulatory Watch" for bg colour                  #
    # ------------------------------------------------------------------ #
    if valid_regulatory:
        _start_section()
        rw_color = INDEX_SECTION_COLORS.get("Regulatory Watch", "#C8102E")
        story.append(Paragraph(
            f"<font color='{rw_color}'>Regulatory Watch</font>",
            styles["SectionHeaderStyle"],
        ))
        story.append(_separator())
        for reg_idx, row in enumerate(valid_regulatory):
            # FIX 1: divider between consecutive entries
            if reg_idx > 0:
                story.append(_box_divider())

            title_text = str(row.get("title", "")).strip()
            what = str(row.get("what_happened", "")).strip()
            impact = str(row.get("impact", "")).strip()
            why = str(row.get("why_it_matters", "")).strip()
            paragraph_1 = " ".join([part for part in [title_text, what] if part and not _is_empty_value(part)]).strip()
            impact_line = (
                f"<font color='#2CA6A4'><b>Impact</b></font>: {_sanitize(impact)}"
                if impact and not _is_empty_value(impact) else ""
            )
            why_line = (
                f"<font color='#2CA6A4'><b>Why it matters</b></font>: {_sanitize(why)}"
                if why and not _is_empty_value(why) else ""
            )

            # FIX 2: entire entry in KeepTogether to prevent page-split mid-card
            entry_group: List[Any] = []
            if paragraph_1:
                box_heading = _short_heading(title_text) or "Regulatory Update"
                entry_group.append(_insight_box(
                    box_heading, paragraph_1, styles,
                    accent_hex=rw_color,
                    section_key="Regulatory Watch",
                ))
                entry_group.append(Spacer(1, 0.08 * inch))
            if impact_line:
                entry_group.append(Paragraph(impact_line, styles["BodyStyle"]))
            if why_line:
                entry_group.append(Paragraph(why_line, styles["BodyStyle"]))
            if title_text and (what or impact or why):
                narrative = (
                    f"<b>Narrative:</b> {_sanitize(title_text)} reflects an active supervisory posture. "
                    f"Institutions should map this to compliance execution, disclosure quality, and funding strategy."
                )
                entry_group.append(Paragraph(narrative, styles["BodyStyle"]))
            if entry_group:
                story.append(KeepTogether(entry_group))
            story.append(Spacer(1, 0.22 * inch))

    # ------------------------------------------------------------------ #
    # Competitor Intelligence                                               #
    # FIX 1: _box_divider() between consecutive competitor boxes           #
    # FIX 2: each competitor item fully in KeepTogether                    #
    # FIX 4: section_key=category for sub-section bg colours               #
    # ------------------------------------------------------------------ #
    if valid_groups:
        _start_section()
        ci_color = INDEX_SECTION_COLORS.get("Competitor Intelligence", "#0F2D63")
        story.append(Paragraph(
            f"<font color='{ci_color}'>Competitor Intelligence</font>",
            styles["SectionHeaderStyle"],
        ))
        story.append(_separator())
        for group in valid_groups:
            category = str(group.get("category", "Category"))
            accent = CI_SUBSECTION_COLORS.get(category)
            story.append(CondPageBreak(2.8 * inch))

            if accent:
                story.append(Paragraph(
                    f"<font color='{accent}'>{_sanitize(category)}</font>",
                    styles["SubsectionHeaderStyle"],
                ))
            else:
                story.append(Paragraph(
                    _sanitize(category),
                    styles["SubsectionHeaderStyle"]
                ))
            valid_items = [
                item for item in group.get("items", [])
                if isinstance(item, dict) and not _is_empty_value(item)
            ]
            for item_idx, item in enumerate(valid_items):
                # FIX 1: subtle hairline divider between consecutive boxes
                if item_idx > 0:
                    story.append(_box_divider())

                company = str(item.get("company", "Competitor"))
                event = _polish_event_text(str(item.get("event", "")))
                narrative_raw = str(item.get("narrative", "")).strip()
                narrative = _polish_event_text(narrative_raw) if narrative_raw else ""
                if narrative and _is_near_duplicate(event, narrative):
                    narrative = ""

                # FIX 2: box + narrative in KeepTogether to prevent orphaned narrative
                item_group: List[Any] = [
                    _insight_box(
                        company,
                        event or "Not found in source reviewed",
                        styles,
                        accent_hex=accent,
                        section_key=category,  # FIX 4
                    ),
                    Spacer(1, 0.08 * inch),
                ]
                if narrative:
                    item_group.append(Paragraph(_sanitize(narrative), styles["BodyStyle"]))

                story.append(KeepTogether(item_group))
                story.append(Spacer(1, 0.18 * inch))

            story.append(Spacer(1, 0.1 * inch))

    if valid_patterns:
        _start_section()
        story.append(Paragraph("Market Signals & Patterns", styles["SectionHeaderStyle"]))
        story.append(_separator())
        for pattern in valid_patterns:
            name = str(pattern.get("pattern_name", "Pattern")).strip()
            observation = str(pattern.get("observation", "")).strip()
            insight = str(pattern.get("insight", "")).strip()
            risk = str(pattern.get("risk", "")).strip()
            if not _is_empty_value(name):
                story.append(Paragraph(_sanitize(name), styles["SubsectionHeaderStyle"]))
            if not _is_empty_value(observation):
                story.append(Paragraph(_sanitize(f"Observation: {observation}"), styles["BodyStyle"]))
            if not _is_empty_value(insight):
                story.append(Paragraph(_sanitize(f"Insight: {insight}"), styles["BodyStyle"]))
            if not _is_empty_value(risk):
                story.append(Paragraph(_sanitize(f"Risk: {risk}"), styles["BodyStyle"]))
            story.append(Spacer(1, 0.08 * inch))

    if valid_takeaways:
        _start_section()
        kt_color = INDEX_SECTION_COLORS.get("Key Takeaways", "#D97706")
        story.append(Paragraph(
            f"<font color='{kt_color}'>Key Takeaways</font>",
            styles["SectionHeaderStyle"],
        ))
        story.append(_separator())
        story.extend(_bullets(valid_takeaways[:5], styles))

    if valid_caveats:
        _start_section()
        story.append(Paragraph("Method & Caveats", styles["SectionHeaderStyle"]))
        story.append(_separator())
        story.extend(_bullets(valid_caveats, styles))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
