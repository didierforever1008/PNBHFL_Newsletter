# Claude Design brief — Housing Finance Bi-Weekly Digest

Paste this **entire document** into Claude Design alongside the attached PDF
`newsletter_2026-05-01_to_2026-05-15_v18_agentic_analysis.pdf`. The brief is
written so a single round of generation produces a final, on-brand artefact.

---

## 1. What you are designing

A **single self-contained HTML artefact** (one file, inline CSS, no external
assets except web fonts via `@import`) that re-presents the content of the
attached PDF as a jazzy, magazine-style competitive-intelligence newsletter
for **PNB Housing Finance Ltd**'s internal audience (senior management,
strategy team, treasury, risk).

Output format: a complete `<!DOCTYPE html>` document, A4-print-friendly
(`@media print { @page { size: A4; margin: 14mm; } }`), but primarily designed
for on-screen reading at desktop widths (1000–1400 px). Use modern CSS
(grid, flexbox, custom properties). No JavaScript. No CDN images.

The audience is **executive / boardroom**, not consumer. The tone is
consulting-grade, confident, dense with insight but visually airy. Think
*Bloomberg Terminal × McKinsey Insights × FT Weekend* — not Mailchimp.

---

## 2. Source content

All editorial content must be lifted verbatim or near-verbatim from the
attached PDF. **Do not invent facts, numbers, dates, or company names.** Do
not paraphrase numerical figures (₹ values, percentages, dates). If the PDF
says "₹15,000 crore" or "7.15%", the HTML says exactly the same.

The document is organised as five top-level sections (in this order):

1. **Cover** — title, date range, tagline
2. **Index** — section list, used as a sticky-on-scroll sidebar in the HTML
3. **Industry Pulse** — one summary paragraph + ~6 highlight cards (each with
   `pointer`, `impact`, `why_it_matters`)
4. **Regulatory Watch** — ~2 items (each with `title`, `what_happened`,
   `impact`, `why_it_matters`)
5. **Competitor Intelligence** — 4 sub-sections in fixed order:
   - Growth & Strategy
   - Funding & Capital
   - Risk & Governance
   - Operational Signals
   Each item: company name, event sentence, narrative (2–3 lines).
6. **Patterns** — 2 cross-cutting themes (each with `pattern_name` +
   `observation`)
7. **Key Takeaways** — 3 boardroom-ready takeaway statements

If the PDF contains a section with zero items after filtering, omit that
section entirely from the HTML — do not show empty placeholders.

---

## 3. Brand identity

This is published under the **PNB Housing Finance Ltd** masthead. Logo and
wordmark in the page footer (bottom-right), small (≈ 110 × 32 px). Do not
recreate the logo — reference the existing PNG via a `<img>` placeholder
comment: `<!-- Place pnb_housing_finance_ltd_logo.png in bottom-right of every printed page -->`.

### Colour palette — use EXACTLY these hex values

| Role | Hex | Use |
|---|---|---|
| Brand red | `#C8102E` | Masthead banner, Regulatory Watch accents, PNB-HFL branding |
| Brand yellow | `#F7C600` | Masthead headline text, hover states, decorative accents |
| Primary navy | `#1F3A5F` | Body copy headings, default link colour |
| Deep navy | `#0F2D63` | Competitor Intelligence section accent strip |
| Forest green | `#1B7A3A` | Industry Pulse section accent strip |
| Amber | `#D97706` | Key Takeaways section accent strip |
| CI · Growth & Strategy | `#0E7C7B` (teal) | Sub-section accent strip + chip |
| CI · Funding & Capital | `#7E22CE` (purple) | Sub-section accent strip + chip |
| CI · Risk & Governance | `#9F1239` (burgundy) | Sub-section accent strip + chip |
| CI · Operational Signals | `#475569` (slate) | Sub-section accent strip + chip |
| Pale green tint | `#F0F7F0` | Industry Pulse card background |
| Warm rose tint | `#FEF3F2` | Regulatory Watch card background |
| Warm amber tint | `#FFFBEB` | Key Takeaways card background |
| Highlight grey | `#F5F7FA` | Generic card background, alternate stripes |
| Body text | `#1F2937` | All paragraph text |
| Muted text | `#5B6B82` (slate) | Captions, dates, metadata |
| Hairline rule | `#C9D8EA` | Dividers, card borders |

Use these as CSS custom properties:

```css
:root {
  --brand-red: #C8102E;
  --brand-yellow: #F7C600;
  --primary-navy: #1F3A5F;
  --deep-navy: #0F2D63;
  --forest: #1B7A3A;
  --amber: #D97706;
  --teal: #0E7C7B;
  --purple: #7E22CE;
  --burgundy: #9F1239;
  --slate: #475569;
  --bg-pulse: #F0F7F0;
  --bg-regulatory: #FEF3F2;
  --bg-takeaway: #FFFBEB;
  --bg-card: #F5F7FA;
  --ink: #1F2937;
  --ink-muted: #5B6B82;
  --rule: #C9D8EA;
}
```

### Typography

- Body & UI: **Inter** (`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap')`)
- Display / masthead / section titles: **Playfair Display**
  (`@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800;900&display=swap')`)
- Numbers / data / dates: **JetBrains Mono** for callout figures
  (`@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&display=swap')`)

Scale:
- Masthead title: Playfair Display 800, 44–52 px, brand-yellow on brand-red
- Section title (Industry Pulse, etc.): Playfair Display 800, 32 px, primary-navy with a 6 px coloured underline in that section's accent
- Card headline: Inter 700, 18 px, ink
- Card body: Inter 400, 14.5 px, ink, line-height 1.55
- Metadata / chips: Inter 600, 11 px UPPERCASE, letter-spacing 0.08em
- Pull-quotes: Playfair Display 700 italic, 22 px, primary-navy with a 4 px left border in the section's accent colour

---

## 4. Layout

### Page structure

Single-column hero masthead → two-column body (sticky index on left,
content on right) → full-width takeaways → footer.

```
┌──────────────────────────────────────────────────────────┐
│  MASTHEAD (red banner, yellow title, white subtitle)     │
│  HOUSING FINANCE BI-WEEKLY DIGEST                        │
│  Regulatory, Industry & Competitive Intelligence         │
│  May 1 — May 15, 2026                                    │
└──────────────────────────────────────────────────────────┘
┌──────────────┬───────────────────────────────────────────┐
│  INDEX       │  INDUSTRY PULSE                           │
│  (sticky,    │  ────── (forest underline)                │
│   coloured   │  Summary paragraph in pull-quote style    │
│   chips,     │                                           │
│   one per    │  ┌─card─┐  ┌─card─┐  ┌─card─┐             │
│   section)   │  │      │  │      │  │      │             │
│              │  └──────┘  └──────┘  └──────┘             │
│              │                                           │
│              │  REGULATORY WATCH                         │
│              │  ────── (red underline)                   │
│              │  ...                                      │
│              │                                           │
│              │  COMPETITOR INTELLIGENCE                  │
│              │  ────── (navy underline)                  │
│              │  [Growth & Strategy chip]                 │
│              │  [Funding & Capital chip]                 │
│              │  ...                                      │
└──────────────┴───────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│  KEY TAKEAWAYS (amber band, 3 numbered cards)            │
└──────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│  PNB Housing Finance Ltd logo (bottom-right)             │
│  Page 1 of N (bottom-left)                               │
└──────────────────────────────────────────────────────────┘
```

### Cards (Industry Pulse + Regulatory Watch)

Each card is a flex column with:
- **Top:** 4 px left border in the section accent colour
- **Headline:** the `pointer` (Industry Pulse) or `title` (Regulatory Watch)
  — bold, 18 px, max 2 lines
- **Three labelled stat blocks** stacked: `IMPACT`, `WHY IT MATTERS`, and
  (where present) `POINTER`. Labels are 11 px uppercase chips in the section
  accent colour at 12% alpha, with bold accent-colour text.
- Background: the section's tint colour (pale-green / warm-rose)
- Padding: 20 px; border-radius: 10 px; subtle shadow:
  `0 1px 3px rgba(15,45,99,0.06), 0 6px 18px rgba(15,45,99,0.04)`
- Cards arranged in a CSS grid: `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))`, gap 16 px.

### Competitor Intelligence

One sub-section per category, in fixed order. Each sub-section has:
- A **chip-shaped header** in the sub-section's accent colour (filled, white
  text, uppercase, 12 px, letter-spacing 0.1em, border-radius 999 px,
  padding 4×12 px) — e.g. `[ GROWTH & STRATEGY ]`
- Below it, a vertical list of company cards. Each card:
  - 4 px left border in the sub-section accent
  - Company name in bold (16 px, ink)
  - Event sentence on the next line (15 px, ink, no italics)
  - Narrative paragraph below (14 px, ink, line-height 1.55) — only for
    Growth/Funding/Risk sub-sections. **Operational Signals shows event
    only — no narrative.**
  - Card background: white; border 1 px solid `var(--rule)`; radius 8 px;
    padding 16 px; vertical gap between cards: 12 px.

### Patterns section

Render as two side-by-side "insight panels":
- Each panel: full-width white card, 1 px slate border, radius 10 px
- Eyebrow: `PATTERN` chip in slate
- Pattern name: Playfair Display 700, 24 px, primary-navy
- Observation: 15 px ink, 1.6 line-height
- Decorative diagonal accent stripe in the top-right corner using the
  section's accent (deep-navy at 8% alpha)

### Key Takeaways

A full-width amber-tinted band (`var(--bg-takeaway)` background).
Three numbered cards in a 3-column grid. Each card:
- Giant numeral (Playfair Display 900, 72 px, amber, line-height 1) in
  the top-left
- Takeaway statement (Inter 600, 17 px, ink) to its right
- Hairline divider below the numeral block

### Footer / running header

- Top of every printed page: a 6 px brand-red bar (decorative only)
- Bottom of every printed page: brand-red 4 px bar, with the PNB-HFL logo
  bottom-right and `Page N` bottom-left. Use `position: running()` if you
  emit print CSS.

---

## 5. Editorial rules (carry these forward verbatim from source)

These rules already governed the PDF generation. Honour them in the HTML:

1. **No ellipses anywhere** (`...` or `…`). If a sentence runs long, keep it
   short or break to a new sentence. Never truncate with ellipsis.
2. **Numbers and dates are sacred.** Don't paraphrase `₹15,000 crore` as
   `~Rs 15 thousand crore`. Preserve the rupee symbol.
3. **Competitor Intelligence Operational Signals** rule: every item has a
   named individual + a recognised action (Appointment / Resignation /
   Retirement / Role Change / Death) + an event date. Render as-is. No
   narrative below — event sentence only.
4. **One canonical title** at the top: `Housing Finance Bi-Weekly Digest`.
   Tagline: `Regulatory, Industry & Competitive Intelligence`.
5. **Date range in masthead**: format as `May 01 – May 15, 2026` (en-dash,
   not hyphen).
6. **Section order is fixed.** Do not re-order Industry Pulse / Regulatory
   Watch / Competitor Intelligence / Patterns / Key Takeaways.
7. **Drop-if-no-narrative.** If a competitor intelligence item (outside
   Operational Signals) has no substantive narrative, omit it.

---

## 6. Sample content from the latest PDF

Use these verbatim as anchors (the PDF contains the full content):

**Cover**
- Title: `Housing Finance Bi-Weekly Digest`
- Tagline: `Regulatory, Industry & Competitive Intelligence`
- Date range: `May 01 – May 15, 2026`

**Industry Pulse — summary paragraph**
> Funding conditions for Indian NBFCs improved as corporate bond yields
> eased, supporting a pickup in primary issuance. Mortgage pricing stayed
> aggressive with starting rates near 7.15%, while listed housing lenders
> reported strong profits on lower credit costs amid steady affordable-
> housing demand.

**Industry Pulse — sample highlights (6 total in PDF)**
- Corporate bond yields softened, supporting a return of NBFC primary
  issuance with planned debt sales of about ₹15,000 crore.
- Home-loan pricing remained highly competitive, with advertised starting
  rates around 7.15% across several lenders.
- Shubham Housing Finance initiated talks for a ~₹2,000-crore IPO amid
  improved valuations and easing funding conditions.
- Affordable and mass-market housing demand stayed steady, with Aadhar
  Housing targeting ~20% growth in FY27 and ₹50,000 crore AUM over three
  years.
- Listed housing lenders reported strong profitability aided by lower
  credit costs, including LIC Housing Finance's Q4 profit growth on
  reduced provisions.
- RBI monetary penalties for KYC and governance lapses reinforced active
  compliance scrutiny across housing finance entities.

**Regulatory Watch (2 total)**
- RBI penalised Hinduja Housing Finance for KYC and governance lapses.
- RBI cancelled registrations of 150 NBFCs and noted additional voluntary
  surrenders.

**Competitor Intelligence — group sizes**
- Growth & Strategy: 5 items
- Funding & Capital: 6 items
- Risk & Governance: 3 items
- Operational Signals: 6 items

**Patterns (2 total)**
- *Bond-market reopening for housing lenders.*
- *Compliance and governance bar rising alongside growth.*

**Key Takeaways (3 total)**
1. Easing bond yields are improving NBFC funding conditions, supporting
   renewed NCD issuance and growth capacity for housing lenders.
2. Mortgage rates remain highly competitive near 7.15%, increasing the
   need for risk-based pricing and cost discipline to protect margins.
3. RBI enforcement and NBFC clean-up actions are raising the compliance
   bar, making KYC and governance investments non-negotiable.

---

## 7. Acceptance criteria

The artefact is "done" when:

- [ ] It is a single self-contained HTML file (no external image URLs;
      Google Fonts via `@import` allowed).
- [ ] The masthead reads exactly `Housing Finance Bi-Weekly Digest` in
      yellow on a red banner.
- [ ] The five top-level sections appear in the correct order with the
      correct accent colours from §3.
- [ ] Industry Pulse cards display all 6 highlights from the PDF (or
      however many the latest PDF contains).
- [ ] Competitor Intelligence shows 4 chip-headed sub-sections (Growth &
      Strategy → Funding & Capital → Risk & Governance → Operational
      Signals), each with the correct sub-section accent colour.
- [ ] Operational Signals items have **no** narrative below the event
      sentence; all other CI items do have a 2–3 line narrative.
- [ ] Key Takeaways is a full-width amber band with 3 large-numeral cards.
- [ ] Sticky left-rail index reflects every section actually rendered
      (not a hard-coded list).
- [ ] The page prints to A4 with no orphan headers or split callout cards
      (use `break-inside: avoid` on `.card`).
- [ ] No ellipses, no `Lorem ipsum`, no placeholder names.
- [ ] PNB-HFL logo placeholder comment is in the bottom-right footer.

---

## 8. Tone of voice (one paragraph)

Confident, precise, sparing of adjectives. Sounds like an internal McKinsey
brief written for a CFO. No marketing prose, no "we are excited to share".
Every sentence should either state a fact, a number, or a clear strategic
implication. The visual design should match: lots of white space, sharp
typography, deliberate use of colour to navigate — not to decorate.
