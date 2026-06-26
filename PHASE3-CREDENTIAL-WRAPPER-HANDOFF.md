# Refinement handoff: credential-wrapper fill + path-less button detection

**For:** the agent maintaining the HubSpot Email MCP server.
**Status:** ready to build. The manifest data is already prepared (this branch); the remaining work is two code changes.
**Branch:** `phase3-credential-wrapper` (holds the manifest update this spec relies on).

Two refinements surfaced when the Phase 2 clone-and-fill loop was proven live. Both are small. The first is the one that matters for output quality.

---

## 1. `fill_email_draft`: honor per-slot wrappers (the credential-marker fix)

### The problem (verified live)
A live fill against the deployed connector confirmed the loop works: clone + fill, all 7 slots, both desktop and mobile twins, 0 skipped. But filling a **credential** slot with a title/body lost its marker. The credential widget is a 2-cell table (left cell = the number "1." in green, icon-ready; right cell = title + body). `fill_email_draft` writes a text slot's value as the whole `body.html`, so the supplied content **replaced the entire table**, dropping the marker cell and styling. Re-read confirmed: after fill, `module-cred-d-1` had no `<table>` and no `#a6ce39` numeral.

The straightforward slots (intro, tagline, CTA, next_up) are unaffected and already correct.

### The fix
Let a slot declare a `wrapper`: a fixed HTML scaffold (the marker + cell layout) with placeholders for the editable text. On fill, substitute the value into the wrapper instead of overwriting the whole widget. The marker becomes fixed furniture the caller never supplies.

The manifest is already updated for this (see `templates/capability-partner-intro.json` on this branch). The credential slots now look like:

```json
"cred_1": {
  "kind": "text",
  "desktop_id": "module-cred-d-1",
  "mobile_id": "module-cred-m-1",
  "content_kind": "title_body",
  "wrapper": {
    "desktop": "<table ...><td ...><p ...><em>1.</em></p></td><td valign=\"top\"><p ...><strong>{{title}}</strong></p><p ...>{{body}}</p></td>...</table>",
    "mobile":  "<table ...> ... 28px numeral ... {{title}} / {{body}} ...</table>"
  }
}
```

### Required logic in `fill_email_draft`
For each slot in `slot_values`:
- **If the slot has a `wrapper`:** take the per-breakpoint wrapper string (`wrapper.desktop` for `desktop_id`, `wrapper.mobile` for `mobile_id`), substitute the value's fields into the matching `{{field}}` placeholders, and set that as the twin's `body.html`. Do NOT write the raw value as the body.
  - Value shape follows `content_kind`. For `title_body` the value is `{ "title": "...", "body": "..." }` mapping to `{{title}}` / `{{body}}`. (A plain-string value would map to a single `{{content}}` placeholder; support that too for future simple wrapped slots.)
  - **HTML-escape** each value before substitution so caller text cannot break the markup. **Exception:** leave HubSpot personalization tokens (`{{ personalization_token(...) }}`) intact, matching how the existing renderer treats them. (Practically: escape `<`, `>`, `&` in the user text; the placeholders themselves are yours, not user input.)
  - Write the same value into both twins (each using its own breakpoint wrapper).
- **If the slot has no `wrapper`:** unchanged. Render the value as `body.html` via the existing markdown / `NAME`-token renderer, both twins. (intro, tagline, next_up, and the button/image kinds keep their current behavior.)

Backward compatible: a manifest with no `wrapper` on any slot behaves exactly as today.

### Acceptance
Clone `TEMPLATE - Capability (partner intro)` (`215880999597`), fill `cred_1 = {title, body}`, re-read: `module-cred-d-1` and `module-cred-m-1` both still contain the `<table>` marker scaffold and the `#a6ce39` numeral, with the new title/body in the right cell. The marker survives; the words change. Confirm on both breakpoints.

---

## 2. `get_template_manifest`: detect path-less button widgets

### The problem
Button detection keys off `body.path == "@hubspot/button_email"`. The primary-CTA button widgets in the Capability template have **no `path` field** (their body carries `text`, `destination`, `corner_radius`, `background_color`, `font_style`, but `path` is absent/None). So `get_template_manifest` misses them, which is partly why the auto-generated manifest omitted the CTA and credential pairings and needed hand-curation.

### The fix
In the widget-kind detector, treat a widget as a **button** if `body.path == "@hubspot/button_email"` **or** it has both `text` and `destination` (optionally also `corner_radius`/`background_color` to be safe). Apply the same broadened test anywhere kind is inferred (the manifest walker, and any kind check in the fill).

### Acceptance
`get_template_manifest("192423946168")` (or the Capability template) lists the primary-CTA widgets as `button` kind, not as unknown/skipped.

---

## Reference
- Architecture + the live-loop evidence: `CMO/Content-Automation/HubSpot-Polished-Email-Templates-Spike-2026-06-24.md` (vault) and the Phase 2 build doc `PHASE2-CLONE-AND-FILL-IMPLEMENTATION.md`.
- Team-facing behavior this enables: `CMO/Content-Automation/HubSpot-Template-Clone-and-Fill-Guide.md` (the credential-card section stops needing the "ask Claude to format it" caveat once #1 ships).
- Manifest with the prepared wrappers: `templates/capability-partner-intro.json` (this branch).
- Safety unchanged: draft-buffer only, never publish/send; reuse `get_hubspot_headers`, the existing renderer, and `resolve_data_uri_images`; no scope/config/transport change.
