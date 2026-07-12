# Phase 4 — Layout Composer (build designed emails from scratch)

## Goal
Add an MCP tool that mints a **polished, custom-designed** marketing-email draft (multi-column rows, colored/rounded section panels, styled cards, pill button, themed styleSettings, desktop/mobile twins) **programmatically from a structured layout spec** — so a designed template no longer has to be hand-assembled in the HubSpot UI. The output must be a first-class, **clone-and-fillable** draft: `get_template_manifest` detects its slots and `clone_email` + `fill_email_draft` work on it unchanged. This FEEDS the existing clone-and-fill runtime; it does not replace it.

Target deliverables: the three blend templates at `~/obsidian-vault/CMO/Content-Automation/Email-Design/` (`01-nurture-cs.html`, `02-case-study.html`, `03-newsletter.html`).

## De-risk findings (proven 2026-07-12 against the live portal — build on these, do not re-litigate)
A hand-constructed designed payload was PATCHed to a throwaway draft and read back. Confirmed:
- **Widget ids are preserved** on `PATCH /marketing/v3/emails/{id}/draft` (no re-normalization of ids). Structure, column widths, section styles, and widget bodies all survive.
- **3-column rows survive**: `columns:[{width:4}×3]`, section `style.stack:"NONE"`.
- **Section background + rounded panel survive**: `style.backgroundColor` (+ `borderT/BLeft/Right Radius`), echoed at `style.breakpointStyles.{default,mobile}.backgroundColor`.
- **Inline-table styled cards survive** verbatim inside a `@hubspot/rich_text` `body.html` (bg + border-radius intact) — this is how cards / figure diagram / left-border sign-off are carried (the established live convention).
- **Pill button survives**: native module `1976948`, `corner_radius:40`, `inner_horizontal_padding`, `body.style.background_color:{color,opacity}`.
- **Themed `styleSettings` accepted**: a partial `{backgroundColor, bodyColor}` is preserved and HubSpot fills the rest with defaults (returned 6 keys).
- **Desktop/mobile twins survive** via widget-level `styles.breakpointStyles.{default,mobile}.hidden`, and `get_template_manifest` **auto-pairs** the twins into one slot (matched by kind + complementary breakpoint + preview).
- HubSpot injects only benign defaults (`backgroundType`, a default `stack`, default `breakpointStyles`) — harmless.

**Residual unknown to verify in the test-gate:** actual *mobile rendering* of multi-column rows (does a 3-col row need an explicit stacked mobile-twin section, or does HubSpot auto-stack acceptably?). Default to the proven live pattern: emit multi-column rows as a **desktop section (stack NONE) + a mobile twin section (single column, stacked)**, and verify on the HubSpot mobile preview.

## The proven create→PATCH mechanics (reuse exactly)
`build_native_email_content` (`src/hubspot_email_mcp/server.py:1440`) is the model:
1. `POST https://api.hubapi.com/marketing/v3/emails` `{name, subject, emailType:"BATCH_EMAIL", businessUnitId}` → `id`, `portalId` (fallback `get_portal_id()`).
2. `PATCH https://api.hubapi.com/marketing/v3/emails/{id}/draft` `{content:{templatePath, styleSettings, flexAreas:{main:{sections}}, widgets}}`.
- Widget shapes (copy from lines 1476-1514): rich_text `{path:"@hubspot/rich_text", css_class:"dnd-module", html, schema_version:2}`; image `{path:"@hubspot/image_email", img:{src,alt}, schema_version:2}`; button module `1976948` `{module_id, text, destination, font_color, corner_radius, inner_horizontal_padding, inner_vertical_padding, style:{background_color:{color,opacity}}, schema_version:2}`; footer `@hubspot/email_footer` `{display:"custom", footer_html, align, unsubscribe_link_type:"both", font, schema_version:2}`.
- Each widget: `{id, name, type:"module", order, css:{}, child_css:{}, styles:{}, body:{...}}`. Twin toggle lives in `styles.breakpointStyles`.
- Section: `{id, columns:[{id, width, widgets:[ids]}], style:{paddingTop, paddingBottom, backgroundColor?, stack?, breakpointStyles?}}`.
- `templatePath = "@hubspot/email/dnd/Start_from_scratch.html"`.

## Reusable helpers (do not re-implement)
`get_hubspot_headers()` (:316), `resolve_business_unit(brand)` (:349), `parse_inline_markdown_to_blocks()` (:1335), `_apply_personalization()` (:1330), `resolve_data_uri_images()`+`upload_image_to_hubspot()` (:1386,:820), `_render_text_slot`/`_render_wrapped_slot` (:2208-2267), `get_template_manifest()` (:2089), `clone_email()`/`fill_email_draft()` (:2174,:2294). Brand config (`config['brands']['edanz']`) already carries `business_unit_id`, `button_color`, `footer_bg`, `footer_html`.

## New tool: `compose_email_draft(layout, brand=None, email_name=None)`
Takes a **layout spec** (below), emits the designed draft via create→PATCH, and — because it knows every slot id and twin pairing — **writes the slot manifest** to `templates/<slug>.json` in the same shape `fill_email_draft` consumes (so the minted template is immediately clone-and-fillable). Returns `{email_id, email_url, status, manifest_path, slots}`.

### Layout spec (JSON)
```
{
  "name": "TEMPLATE - CS Nurture (査読対策)",   // "TEMPLATE - " prefix = discoverable via list_emails
  "subject": "...",
  "theme": { "page_bg":"#F5F5F5", "body_bg":"#FFFFFF", "button_color":"#BA2532",
             "primary_color":"#272424", "h1_size":28, "h2_size":22 },   // -> styleSettings
  "sections": [ Section, ... ]
}
Section = {
  "id": str,
  "bg": "#023762"?,           // section backgroundColor (also emitted into breakpointStyles twins)
  "radius": 14?,              // rounded panel (all four corners)
  "padding": [top,bottom]?,   // px
  "fixed": bool?,             // whole section is furniture (never a slot)
  "columns": [ Column, ... ]  // len 1 = single-col (shared, no twin); len>1 = multi-col (auto desktop+mobile twin)
}
Column = { "width": int(1..12), "widgets": [ Widget, ... ] }
Widget =
  { "type":"richtext", "slot":str?, "html":str,
    "wrapper":{"desktop":str,"mobile":str}?, "content_kind":"title_body"? }   // wrapper = card/border scaffold w/ {{title}}/{{body}} or {{content}}
  | { "type":"button", "slot":str?, "text":str, "url":str, "color":"#BA2532"?, "radius":40? }
  | { "type":"image",  "slot":str?, "src":str?, "alt":str? }                  // data: URIs uploaded via existing path
  | { "type":"footer" }                                                       // emit branded footer from brand config
```
- `slot` present → fillable (goes in the manifest). No `slot` (or `fixed`) → brand furniture, never written.
- Stable ids: derive from slot/section, e.g. `w-<slot>` (single-col/shared) or `w-<slot>-d` / `w-<slot>-m` (twinned). Manifest keys on these.

### Emit algorithm
1. Resolve brand → BU (`resolve_business_unit`); pull `button_color`/`footer_html`/`footer_bg` from brand config as defaults.
2. Build `styleSettings` from `theme`.
3. `POST` create email.
4. Walk sections in order (assign widget `order` monotonically):
   - **Single-column section:** emit widgets shared (no `breakpointStyles.hidden`); apply section `style` (bg/radius/padding, + `breakpointStyles` bg twins when `bg` set).
   - **Multi-column section:** emit a **desktop** section (`stack:"NONE"`, its widgets `breakpointStyles={default:{hidden:false},mobile:{hidden:true}}`) AND a **mobile twin** section (single width-12 column, widgets duplicated with `{default:{hidden:true},mobile:{hidden:false}}`). Pair each `-d`/`-m` widget in the manifest.
   - `wrapper` widgets: store the per-breakpoint wrapper + `content_kind` in the manifest slot (so `fill_email_draft`'s existing wrapper-substitution path applies).
   - `image` widgets with `data:` src → upload via `resolve_data_uri_images` first.
   - `footer` widget → branded `@hubspot/email_footer` in a dark (`footer_bg`) section.
5. `PATCH` `{content:{templatePath, styleSettings, flexAreas:{main:{sections}}, widgets}}`.
6. **Emit manifest** `templates/<slug>.json`: `{template_id, name, slots:{<slot>:{kind, desktop_id, mobile_id, [wrapper, content_kind]}}}`. Cross-check by calling `get_template_manifest(email_id)` and reconciling ids (belt-and-suspenders: the composer knows the pairs; the detector confirms them).
7. Return.

## Acceptance test-gate (MUST pass before the tool is considered done)
Encode template 01's blend as a layout spec and run end-to-end against a **throwaway `ZZZ DELETE` draft in the edanz BU**:
1. `compose_email_draft` → `PATCH` 200; `GET /draft` shows every section/column/widget/twin/styleSettings preserved (assert programmatically).
2. `get_template_manifest` detects all fillable slots and pairs every twin.
3. `clone_email` the throwaway → `fill_email_draft` with sample `slot_values` (text, a wrapped card, a button, an image) → `GET` confirms content swapped on **both** desktop and mobile twins; furniture untouched.
4. **Visual check**: open the draft in HubSpot and confirm it renders like `01-nurture-cs.html` on **desktop and mobile** preview (this catches the multi-col mobile-stacking unknown). Report screenshots or a precise description.
5. Delete all throwaways.
Provide the passing transcript (ids, assertions) in the final report.

## Scope
- **In:** the tool + manifest emit; encode & mint the 3 blend templates (01/02/03); edanz brand; fixed-arity slots; the blend components (hero, intro, N-col row, figure/image panel, sign-off, CTA, footer).
- **Out (defer):** variable-length repeaters; coded-HTML route; non-edanz brands; per-breakpoint font-size tuning beyond column stacking.

## Guardrails for the implementer
- Work on branch `phase4-layout-composer` **in `~/hubspot-email-mcp`** (NOT a fresh worktree — you need the gitignored `config.local.json` + `.venv` to test). **Never** commit to `main`, **never** push to any remote, **never** deploy to Railway, **never** touch credentials/config secrets. Do not print the API token.
- All HubSpot writes are throwaway `ZZZ DELETE` drafts, deleted after the test-gate. Never publish or send.
- Additive only: do not change `create_email_draft`, `clone_email`, `fill_email_draft`, or the existing manifest behavior. The new tool is a sibling.
