# Phase 2 implementation handoff: clone-and-fill email templates

**For:** the agent implementing the MCP server changes.
**Status:** architecture validated live (2026-06-25). Ready to build.
**Goal:** let a Claude chat produce a polished, on-brand HubSpot email *draft* by cloning a curated template and filling only its content-bearing widgets, leaving all brand furniture intact. Never publish, never send.

Background and rationale live in the vault spike findings: `CMO/Content-Automation/HubSpot-Polished-Email-Templates-Spike-2026-06-24.md`. This document is the build spec. You should not need to re-derive the architecture; it is settled and proven.

---

## 1. What is already proven (do not re-litigate)

A live round-trip was run against the portal (BU `2410306`, the `content`-scoped private-app token already in `config.local.json` / Railway env). Findings, all verified against the API, not docs:

- **`POST /marketing/v3/emails/clone` works.** Cloning `192423946168` (Capability-Tim) returned a new DRAFT email, `clonedFrom` set, same BU.
- **The clone preserves widget IDs.** The new email's `widgets` keys are the *same* ids as the source (`module-1-0-0`, `module_17509287529653`, etc.). **This is the key enabler: a slot manifest built once from a template applies unchanged to every clone of it.**
- **`PATCH /marketing/v3/emails/{id}/draft` with the full `content` object is safe.** Read the draft content, modify only the widget bodies you want, PATCH the whole `content` back:
  - Widget ID set unchanged (39 to 39, identical set). No re-normalisation.
  - Every other widget byte-identical (0 unintended changes).
  - `styleSettings` and `templatePath` identical. Section count and per-widget `order` map identical.
  - `flexAreas` is the only thing HubSpot rewrites, and it is **benign**: it expands absent style keys (border/margin/radius) to explicit `null`s. No structural change, and it is **idempotent** (a second identical PATCH produces zero diffs). Do not fight it; just PATCH the full content and let it settle.
- **Read path:** `GET /marketing/v3/emails/{id}`; for a DRAFT the live buffer is at `GET /marketing/v3/emails/{id}/draft` (`get_email` in `server.py` already does this). Use that buffer as the read side of the fill.

A reference implementation of this exact round-trip (clone, read, modify two widgets, PATCH, diff-verify) is in Appendix A. Build the tools from it.

There is a throwaway test draft from this probe in the portal: id `215878711456` ("ZZZ DELETE - Phase2 clone test"). Delete it (`DELETE /marketing/v3/emails/215878711456`) or leave it; it is junk.

---

## 2. Endpoints and shapes

- **Clone:** `POST https://api.hubapi.com/marketing/v3/emails/clone`, body `EmailCloneRequestVNext`: `{ "id": "<template_id>", "cloneName": "<name>", "language": "<optional>" }`. Returns the full `PublicEmail` (use `.id`).
- **Read draft content:** `GET .../emails/{id}` then, if `state == DRAFT`, `GET .../emails/{id}/draft` and use `.content`.
- **Write draft:** `PATCH .../emails/{id}/draft`, body `{ "content": { "flexAreas": ..., "widgets": ..., "styleSettings": ..., "templatePath": ... } }` (the `PublicEmailContent` shape). Send the full content object back, not a partial.
- **Edit URL** (for human review) is constructed, not returned: `https://app.hubspot.com/email/{portalId}/edit/{id}` (portal `7844696`; resolve via the existing `get_portal_id()` helper).

All under scope `content`, which the token already has. No new scope, config, or transport change.

---

## 3. The three things to build

### 3a. `get_template_manifest(email_id)` (read-only helper)
Wraps `get_email(email_id, raw=True)`, walks `content.widgets`, and emits a **draft manifest** for a human to curate once per template. For each editable widget (paths `@hubspot/rich_text`, `@hubspot/button_email`, `@hubspot/image_email`) output: widget id, `kind` (text/button/image), `order`, breakpoint role (read `styles.breakpointStyles.default.hidden` / `mobile.hidden`: `default` visible + `mobile` hidden = desktop twin; the inverse = mobile twin; neither hidden = shared), and a short content preview (first ~60 chars of `body.html` / `body.text` / `body.img.alt`).

Then **suggest desktop/mobile pairings**: widgets of the same `kind` with matching (or near-matching) content/preview and complementary breakpoint roles are almost certainly the same slot. Emit suggested slot entries (`slot_1`, `slot_2`, ...) with `{kind, desktop_id, mobile_id}` for the human to rename. This is a curation aid, not an authority; the human finalises the manifest.

### 3b. `clone_email(template_id, new_name, language=None)`
`POST .../clone` with `EmailCloneRequestVNext`. Return `{ id, edit_url }`. ~15 lines, mirrors the existing POST helpers and `get_hubspot_headers()`. Write an audit-log entry (match the existing `audit.jsonl` attribution pattern).

### 3c. `fill_email_draft(email_id, slot_values, template_name)`
The core (~80 to 120 lines).
1. Load the manifest `templates/<template_name>.json` from the repo (see section 4).
2. Read the draft content (`GET .../{id}/draft`; full `content`).
3. For each `slot` present in `slot_values`, look it up in the manifest and write the value to **both** `desktop_id` and `mobile_id` widgets:
   - `kind: text` -> set `widgets[id].body.html`. Reuse the existing markdown/inline-HTML and personalization-token rendering (the same path `create_email_draft` uses for rich_text, e.g. `parse_inline_markdown_to_blocks` / the `NAME` to first-name-token logic). Do not invent a new renderer.
   - `kind: button` -> set `widgets[id].body.text` and `widgets[id].body.destination` from `{text, url}`.
   - `kind: image` -> set `widgets[id].body.img.src` and `.alt` from `{src, alt}`; route data-URI images through the existing `resolve_data_uri_images` / `upload_image_to_hubspot` path so pasted/generated images get hosted first.
4. `PATCH .../{id}/draft` with the full (modified) `content`. Let HubSpot normalise `flexAreas` (benign, see section 1).
5. Return `{ id, edit_url, slots_filled: [...], slots_skipped: [...] }`. Audit-log the write.

Hard rule: this writes to a **draft buffer only**. Never call any publish/send endpoint. Never PATCH a non-draft. A slot in `slot_values` with no manifest entry is reported in `slots_skipped`, never guessed.

---

## 4. Manifest format and storage (in the repo)

Store one JSON file per template at `templates/<template_name>.json`, versioned with the server. Schema:

```json
{
  "template_id": "192423946168",
  "name": "capability-partner-intro",
  "description": "Editorial-partner introduction (Capability series).",
  "slots": {
    "intro":        { "kind": "text",   "desktop_id": "module-1-0-0",            "mobile_id": "module-1-0-1" },
    "cred_1_title": { "kind": "text",   "desktop_id": "<desktop widget id>",     "mobile_id": "<mobile widget id>" },
    "cred_1_body":  { "kind": "text",   "desktop_id": "...",                     "mobile_id": "..." },
    "cta_primary":  { "kind": "button", "desktop_id": "module_17509287529653",   "mobile_id": "module_17509295141737" },
    "partner_photo":{ "kind": "image",  "desktop_id": "...",                     "mobile_id": "..." }
  }
}
```

- The fill keys off **slot names** (durable), and within a template the **widget ids are stable across clones** (proven), so one manifest serves every clone. If a future HubSpot change ever rewrites ids on clone, the slot-name indirection is the safety margin: regenerate the manifest with `get_template_manifest`.
- Anything not named in `slots` is fixed brand furniture and is never written.

`slot_values` passed to `fill_email_draft` (the chat-supplied object):

```json
{
  "intro": "Dear NAME, ...",
  "cred_1_title": "...",
  "cred_1_body": "...",
  "cta_primary": { "text": "サポート一覧を見る", "url": "https://jp.edanz.com/services" },
  "partner_photo": { "src": "https://.../photo.png", "alt": "..." }
}
```

---

## 5. The first template to curate (the proof)

Recommended: create ONE dedicated template email in the HubSpot UI seeded from a Tim-style **live-widget** card (never a Carol-style flattened-PNG card, see the spike). Practical path:
1. Clone Capability-Tim (`192423946168`) once in the UI, rename it `TEMPLATE - Capability (partner intro)`, and blank the person-specific copy to neutral placeholders. This is the canonical template; do not send it.
2. Run `get_template_manifest` on it, curate `templates/capability-partner-intro.json` (rename slots, confirm every desktop/mobile pair).
3. Operating loop per campaign: `clone_email(template_id, "Capability - <partner>")` then `fill_email_draft(new_id, slot_values, "capability-partner-intro")` then hand the edit URL to the marketer.

Capability-Tim's editable inventory to expect: ~18 `rich_text` widgets (intro, the 1/2/3 credential title+body pairs, taglines, rep message) and 2 `button_email` widgets (primary + secondary CTA), each with a desktop and a mobile twin. The photo and partner-strip remain images (the photo is a real per-partner image slot; the strip is fixed furniture).

---

## 6. Out of scope for Phase 2 (defer to Phase 3)

- **Repeaters** (variable-length credential lists, case-study lists, partner grids). These require cloning a widget block N times with new ids and rewriting the column `widgets` id list + each `order`. Phase 2 is **fixed-arity slots only**. Do not scope-creep into this.
- Preheader (`preview_text` widget) write field, subject/from/BU controls, A/B. Phase 4.
- `list_templates`. Phase 3.

---

## 7. Acceptance test

A marketer describes a new editorial-partner email in chat; the loop produces a review-ready draft with **furniture intact and content swapped on both desktop and mobile**. Concretely: `clone_email` the template, `fill_email_draft` a sample partner's content, open the draft edit URL, confirm (a) header/footer/social/palette/section grid unchanged, (b) every filled slot shows the new content on **both** breakpoints (toggle the mobile preview), (c) no widget ids dropped, (d) nothing published or sent. Compare side by side against Capability-Carol to confirm the polish bar is met.

---

## 8. Reuse map (functions already in `server.py`)

- `get_hubspot_headers()` (auth header).
- `_EMAILS_BASE` (the `.../marketing/v3/emails` base).
- `get_email(email_id, raw=True)` (read path, incl. the `/draft` buffer logic; reuse or factor out its draft-read).
- `create_email_draft` / `build_native_email_content` and the rich_text markdown + personalization rendering it uses (reuse for text slots; do not write a second renderer).
- `resolve_data_uri_images` + `upload_image_to_hubspot` (for image slots).
- `get_portal_id()` (for the edit URL).
- the `audit.jsonl` logging pattern (attribution under the shared service-account token).
- Do not touch the `_SimpleOAuthProvider` / static-bearer transport, scopes, or config loader.

---

## Appendix A: validated round-trip reference (the probe that proved this)

This ran live and verified clone + full-content PATCH is safe (section 1). Build `clone_email` + `fill_email_draft` from it; it is not production code (no manifest, no helper reuse, hardcoded ids), it is the proof of the mechanics.

```python
import json, copy, requests
cfg = json.load(open("config.local.json")); TOK = cfg["hubspot_api_key"]
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com/marketing/v3/emails"

def get_content(eid):
    e = requests.get(f"{BASE}/{eid}", headers=H, timeout=30).json()
    c = e.get("content", {}) or {}
    if (e.get("state") or "").upper() == "DRAFT":
        d = requests.get(f"{BASE}/{eid}/draft", headers=H, timeout=30)
        if d.status_code == 200 and d.json().get("content"):
            c = d.json()["content"]
    return c

# clone
r = requests.post(f"{BASE}/clone", headers=H,
                  json={"id": "192423946168", "cloneName": "ZZZ DELETE - test"}, timeout=30)
new_id = str(r.json()["id"])

# read, modify only chosen widget bodies, PATCH the FULL content back
c = get_content(new_id)
c["widgets"]["module-1-0-0"]["body"]["html"] = "<p>new intro</p>"          # text slot
c["widgets"]["module-10-1-1"]["body"]["text"] = "new CTA"                  # button slot
requests.patch(f"{BASE}/{new_id}/draft", headers=H, json={"content": c}, timeout=30)
# re-read get_content(new_id) to verify: ids stable, other widgets untouched, flexAreas null-normalised only
```

Note: the desktop/mobile twin requirement (write BOTH ids per slot) is the single most likely bug if missed; the manifest exists to enforce it. Build the real `fill_email_draft` to iterate `slot_values` against the manifest and write each slot to both `desktop_id` and `mobile_id`.
