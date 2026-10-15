// Parcel dossier ("scheda tecnica particella") for land-registry.
// Rendered by land_registry.parcel_dossier through aecs4u-documents. All user-facing text arrives in data.json
// (`text` dictionary), so this file only decides layout. Block types: kv, table, image, note, unavailable.

#let data = json("data.json")
#let t = data.text

#let ink = rgb("#101828")
#let muted = rgb("#667085")
#let line-col = rgb("#d0d5dd")
#let brand = rgb("#153f72")
#let accent = rgb("#0f766e")
#let accent-soft = rgb("#e6f4f2")
#let soft = rgb("#f6f8fb")
#let warn = rgb("#b54708")

#set document(title: t.doc_title + " " + data.parcel.reference, author: "AECS4U")
#set text(font: ("Inter", "Liberation Sans", "DejaVu Sans"), size: 9.5pt, fill: ink, lang: data.locale, hyphenate: false)
#set par(leading: 0.62em, spacing: 0.9em)

#let tone-color(tone) = {
  if tone == "high" { rgb("#d92d20") } else if tone == "medium" { rgb("#f79009") } else if tone == "low" { rgb("#12b76a") } else { accent }
}

#let logo-mark(size: 9mm) = box(width: size, height: size, radius: 1.2mm, clip: true, fill: brand)[
  #place(bottom + right, polygon(fill: accent, (0pt, size), (size, size), (size, 0pt)))
]

#let kpi-card(item) = block(
  width: 100%, height: 19mm, stroke: 0.6pt + line-col, radius: 4pt, inset: (x: 9pt, y: 8pt), fill: white, breakable: false,
)[
  #text(size: 7.5pt, fill: muted)[#item.label]
  #v(2pt)
  #text(size: 15pt, weight: "bold", fill: ink)[#item.value]
  #if item.at("hint", default: none) != none [
    #linebreak()
    #text(size: 7.5pt, fill: muted)[#item.hint €/m²]
  ]
]

#let chip(c) = box(inset: (x: 6pt, y: 3pt), radius: 8pt, fill: tone-color(c.tone).lighten(82%), stroke: 0.6pt + tone-color(c.tone).lighten(40%))[
  #box(width: 6pt, height: 6pt, radius: 3pt, fill: tone-color(c.tone), baseline: 0pt)
  #h(3pt)
  #text(size: 8pt, weight: "bold")[#c.label]
]

#let callout(title, body, tone: accent) = block(
  width: 100%, breakable: false, radius: 4pt, stroke: (left: 3pt + tone, rest: 0.6pt + line-col), inset: 10pt, fill: white,
)[
  #text(weight: "bold", size: 10pt)[#title]
  #v(3pt)
  #body
]

// --- section headings: sticky so a heading never ends a page on its own
#show heading.where(level: 1): it => block(sticky: true, above: 16pt, below: 8pt, width: 100%)[
  #text(size: 13pt, weight: "bold", fill: ink)[#it.body]
  #v(-2pt)
  #line(length: 100%, stroke: 0.8pt + line-col)
]

#let parse-width(spec) = {
  if spec.ends-with("fr") { float(spec.slice(0, -2)) * 1fr } else if spec.ends-with("mm") { float(spec.slice(0, -2)) * 1mm } else { auto }
}

#let kv-table(rows) = if rows.len() > 0 {
  block(width: 100%, breakable: false)[
    #table(
      columns: (38%, 1fr),
      stroke: (x, y) => (bottom: 0.5pt + line-col),
      inset: (x: 7pt, y: 5.5pt),
      fill: (x, y) => if calc.even(y) { soft } else { white },
      ..rows.map(r => (text(weight: "bold", size: 9pt, fill: muted)[#r.at(0)], [#r.at(1)])).flatten()
    )
  ]
}

#let data-table(b) = block(width: 100%, breakable: true)[
  #table(
    columns: b.columns.map(c => parse-width(c.width)),
    align: b.columns.map(c => if c.align == "right" { right } else { left }),
    stroke: (x, y) => (bottom: 0.5pt + line-col),
    inset: (x: 6pt, y: 5pt),
    fill: (x, y) => if y == 0 { rgb("#e8eef5") } else if calc.even(y) { soft } else { white },
    table.header(..b.columns.map(c => text(weight: "bold", size: 8.5pt)[#c.title])),
    ..b.rows.flatten().map(cell => text(size: 8.5pt)[#cell]),
  )
]

#let render-block(b) = {
  if b.type == "kv" {
    kv-table(b.rows)
  } else if b.type == "table" {
    v(4pt)
    data-table(b)
  } else if b.type == "image" {
    v(4pt)
    block(width: 100%, breakable: false)[
      #image(b.file, width: 100%)
      #v(2pt)
      #text(size: 7.5pt, fill: muted)[#b.caption]
    ]
  } else if b.type == "note" {
    v(2pt)
    text(size: 7.5pt, fill: muted)[#b.text]
  } else if b.type == "unavailable" {
    block(width: 100%, inset: 8pt, radius: 3pt, fill: soft, stroke: 0.5pt + line-col)[
      #text(size: 8.5pt, fill: muted, style: "italic")[#b.text]
    ]
  }
  v(6pt)
}

// =====================================================================================================================
// Cover
// =====================================================================================================================
#page(paper: "a4", margin: (x: 16mm, top: 14mm, bottom: 14mm), header: none, footer: none)[
  #grid(
    columns: (auto, 1fr, auto),
    column-gutter: 8pt,
    align: (left + horizon, left + horizon, right + horizon),
    logo-mark(),
    [
      #text(size: 17pt, weight: "bold", fill: brand)[AECS4U] \
      #text(size: 9pt, fill: muted)[#t.doc_title]
    ],
    text(size: 8pt, fill: muted)[
      #data.report.date \
      #data.report.url \
      #t.report #data.report.id
    ],
  )
  #v(4pt)
  #line(length: 100%, stroke: 0.6pt + line-col)
  #v(8pt)
  #if data.has_cover_map [
    #block(radius: 4pt, clip: true, width: 100%)[#image("cover_map.svg", width: 100%)]
  ]
  #v(12pt)
  #text(size: 8pt, weight: "bold", fill: accent)[#t.eyebrow]
  #v(2pt)
  #text(size: 26pt, weight: "bold")[#data.parcel.municipality #if data.parcel.province != "" [(#data.parcel.province)]]
  #v(2pt)
  #text(size: 11pt, fill: muted)[#data.parcel.subline]
  #v(10pt)
  #grid(columns: (1fr, 1fr, 1fr), gutter: 8pt, ..data.kpis.map(kpi-card))
  #v(8pt)
  #if data.chips.len() > 0 [
    #data.chips.map(chip).join(h(8pt))
    #v(6pt)
  ]
  #text(size: 8pt, weight: "bold", fill: accent)[#t.contents]
  #v(4pt)
  #grid(
    columns: (1fr, 1fr), row-gutter: 6pt, column-gutter: 12pt,
    ..data.contents.map(c => [#box(width: 5pt, height: 5pt, radius: 1pt, fill: accent, baseline: 0pt) #h(4pt) #text(size: 9pt)[#c]]),
  )
  #v(1fr)
  #grid(
    columns: (1fr, auto), column-gutter: 12pt, align: (left + bottom, right + bottom),
    callout(t.purpose_title, text(size: 8.5pt, fill: muted)[#t.purpose_text]),
    if data.has_qr [
      #block(stroke: 0.6pt + line-col, radius: 4pt, inset: 5pt)[#image("qr.svg", width: 24mm)]
      #v(1pt)
      #align(center, text(size: 6.5pt, fill: muted)[#t.qr_caption])
    ],
  )
  #v(4pt)
  #text(size: 7pt, fill: muted)[#data.parcel.reference]
]

// =====================================================================================================================
// Body pages: running header and footer
// =====================================================================================================================
#set page(
  paper: "a4",
  margin: (x: 16mm, top: 24mm, bottom: 20mm),
  header: context [
    #grid(
      columns: (auto, 1fr, auto), column-gutter: 6pt, align: (left + horizon, left + horizon, right + horizon),
      logo-mark(size: 5mm),
      text(size: 8pt, fill: muted)[AECS4U · #t.doc_title],
      text(size: 8pt, fill: muted)[#data.parcel.reference],
    )
    #v(-3pt)
    #line(length: 100%, stroke: 0.5pt + line-col)
  ],
  footer: context [
    #line(length: 100%, stroke: 0.5pt + line-col)
    #v(2pt)
    #grid(
      columns: (1fr, auto), align: (left, right),
      text(size: 7pt, fill: muted)[#t.footer_generated · #t.report #data.report.id],
      text(size: 7pt, fill: muted)[#t.page_of.replace("{n}", str(here().page())).replace("{total}", str(counter(page).final().first()))],
    )
    #text(size: 6.5pt, fill: muted)[#t.footer_disclaimer]
  ],
)

// --- table of contents
#heading(level: 1, outlined: false)[#t.toc]
#v(4pt)
#show outline.entry: it => block(above: 0pt, below: 0pt, inset: (y: 6pt), stroke: (bottom: 0.5pt + line-col), width: 100%)[
  #link(it.element.location())[#text(size: 10pt)[#it.element.body]#h(1fr)#text(fill: muted)[#it.page()]]
]
#outline(title: none, depth: 1, indent: 0pt)
#v(10pt)

// --- summary
#heading(level: 1)[#t.summary]
#grid(columns: (1fr, 1fr, 1fr), gutter: 8pt, ..data.kpis.map(kpi-card))
#v(8pt)
#kv-table((
  (t.f_reference, data.parcel.reference),
  (t.f_municipality, data.parcel.municipality),
  (t.f_province_code, data.parcel.province),
  (t.f_sheet, data.parcel.sheet),
  (t.f_parcel, data.parcel.parcel),
).filter(r => r.at(1) != ""))
#v(6pt)
#if data.chips.len() > 0 [
  #data.chips.map(chip).join(h(8pt))
  #v(6pt)
]
#callout(
  t.not_replace_title,
  [
    #list(..data.not_replace_items.map(i => text(size: 8.5pt, fill: muted)[#i]))
    #text(size: 8pt, fill: muted)[#t.not_replace_footer]
  ],
  tone: warn,
)

// --- thematic sections
#for s in data.sections [
  #heading(level: 1)[#s.title]
  #for b in s.blocks [#render-block(b)]
]

// --- source register
#heading(level: 1)[#t.sec_sources]
#text(size: 8pt, fill: muted)[#t.r_register_note]
#v(4pt)
#for src in data.sources [
  #block(sticky: true, above: 8pt, below: 3pt)[#text(weight: "bold", size: 9pt, fill: brand)[#src.title]]
  #block(width: 100%, breakable: false)[
    #table(
      columns: (34%, 1fr),
      stroke: (x, y) => (bottom: 0.4pt + line-col),
      inset: (x: 6pt, y: 3.6pt),
      ..src.rows.map(r => (text(size: 7.5pt, fill: muted, weight: "bold")[#r.at(0)], text(size: 7.5pt)[#r.at(1)])).flatten()
    )
  ]
]
#v(8pt)
#text(size: 7.5pt, fill: muted)[#t.r_versions: #data.versions]

// --- personal data appendix
#if data.personal.len() > 0 [
  #heading(level: 1)[#t.sec_personal]
  #for b in data.personal [#render-block(b)]
]
