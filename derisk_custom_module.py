#!/usr/bin/env python3
"""
De-risk test (Phase 0.5): confirm how the marketing-email module-tree references
a CUSTOM module and a NATIVE image module — BEFORE wiring create_email_draft.

Posts ONE draft into the Edanz portal (BU 0) containing:
  1. a rich_text intro
  2. a native email image module  (@hubspot/image_email)
  3. our custom button module      (edanz-email-modules/edanz-button.module, by path)
  4. the standard footer

If the draft is created AND renders the button + image in the HubSpot editor, the
`path`-based custom-module reference is correct and we build the emitter on it.

Run:
  export HUBSPOT_EMAIL_MCP_CONFIG=./config.local.json
  ./.venv/bin/python derisk_custom_module.py
"""
import json
import requests

CFG = json.load(open("config.local.json"))
TOKEN = CFG["hubspot_api_key"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BU = "0"
CUSTOM_MODULE_PATH = "edanz-email-modules/edanz-button.module"
# A real hosted image so we can see the native image module render.
IMG = "https://www.edanz.com/hubfs/raw_assets/public/Edanz_2021/images/edanz-logo.png"


def main():
    # 1) Create the email shell
    r = requests.post("https://api.hubapi.com/marketing/v3/emails", headers=H, json={
        "name": "DERISK — custom button + image module",
        "subject": "Edanz — module de-risk test",
        "emailType": "BATCH_EMAIL",
        "businessUnitId": BU,
    })
    r.raise_for_status()
    email_id = r.json()["id"]
    print("created email_id:", email_id)

    widgets = {
        "m-text": {
            "id": "m-text", "name": "m-text", "type": "module", "order": 0,
            "body": {"path": "@hubspot/rich_text", "css_class": "dnd-module",
                     "html": "<p>De-risk test: a native image module and a custom button module below.</p>",
                     "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {},
        },
        "m-img": {
            "id": "m-img", "name": "m-img", "type": "module", "order": 1,
            "body": {"path": "@hubspot/image_email",
                     "img": {"src": IMG, "alt": "Edanz", "width": 200, "height": 60},
                     "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {},
        },
        "m-btn": {
            "id": "m-btn", "name": "m-btn", "type": "module", "order": 2,
            "body": {
                "path": CUSTOM_MODULE_PATH,
                "button_text": "Start your manuscript",
                "button_url": {"type": "EXTERNAL", "href": "https://www.edanz.com/manuscript-development", "content_id": None},
                "button_width": 240,
                "bg_color": {"color": "#0B5394", "opacity": 100},
                "text_color": {"color": "#FFFFFF", "opacity": 100},
                "align": "center",
                "border_radius": 4,
                "schema_version": 2,
            },
            "css": {}, "child_css": {}, "styles": {},
        },
        "m-footer": {
            "id": "m-footer", "name": "m-footer", "type": "module", "order": 999,
            "body": {"path": "@hubspot/email_footer", "align": "center",
                     "unsubscribe_link_type": "both", "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {},
        },
    }
    sections = [{
        "id": "section-0",
        "columns": [{"id": "col-0", "width": 12, "widgets": ["m-text", "m-img", "m-btn", "m-footer"]}],
        "style": {"paddingTop": "20px", "paddingBottom": "20px"},
    }]

    patch = requests.patch(
        f"https://api.hubapi.com/marketing/v3/emails/{email_id}/draft",
        headers=H,
        json={"content": {"flexAreas": {"main": {"sections": sections}}, "widgets": widgets}},
    )
    print("PATCH status:", patch.status_code)
    if patch.status_code != 200:
        print("PATCH body (first 800 chars):", patch.text[:800])
        patch.raise_for_status()

    # Resolve portal id for a clickable link
    acc = requests.get("https://api.hubapi.com/account-info/v3/details", headers=H)
    pid = acc.json().get("portalId", "") if acc.ok else ""
    print("\n✓ Draft updated. Open and check the button + image render:")
    print(f"   https://app.hubspot.com/email/{pid}/edit/{email_id}")


if __name__ == "__main__":
    main()
