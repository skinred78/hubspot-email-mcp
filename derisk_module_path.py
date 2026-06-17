#!/usr/bin/env python3
"""
De-risk: which custom-module reference format actually RENDERS in a marketing email?
Builds one draft with three labelled button variants. Whichever shows a blue button
is the correct format for build_native_email_content.
"""
import json, requests

CFG = json.load(open("config.local.json"))
H = {"Authorization": f"Bearer {CFG['hubspot_api_key']}", "Content-Type": "application/json"}
BU = "0"

VARIANTS = [
    ("A — path WITHOUT .module", "edanz-email-modules/edanz-button"),
    ("B — leading slash, no .module", "/edanz-email-modules/edanz-button"),
    ("C — original (with .module)", "edanz-email-modules/edanz-button.module"),
]

def btn_body(path, label):
    return {"path": path, "button_text": f"BUTTON {label[:1]}",
            "button_url": {"type": "EXTERNAL", "href": "https://www.edanz.com", "content_id": None},
            "schema_version": 2}

def text_widget(wid, html, order):
    return {"id": wid, "name": wid, "type": "module", "order": order,
            "body": {"path": "@hubspot/rich_text", "css_class": "dnd-module", "html": html, "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {}}

def main():
    r = requests.post("https://api.hubapi.com/marketing/v3/emails", headers=H, json={
        "name": "DERISK — custom module path formats", "subject": "Edanz — module path de-risk",
        "emailType": "BATCH_EMAIL", "businessUnitId": BU})
    r.raise_for_status()
    eid = r.json()["id"]

    widgets, order_ids, o = {}, [], 0
    for label, path in VARIANTS:
        tw = f"t-{o}"; bw = f"b-{o}"
        widgets[tw] = text_widget(tw, f"<p><strong>{label}</strong> &nbsp; <code>{path}</code></p>", o)
        widgets[bw] = {"id": bw, "name": bw, "type": "module", "order": o + 1,
                       "body": btn_body(path, label), "css": {}, "child_css": {}, "styles": {}}
        order_ids += [tw, bw]; o += 2
    widgets["m-footer"] = {"id": "m-footer", "name": "m-footer", "type": "module", "order": 999,
                           "body": {"path": "@hubspot/email_footer", "align": "center",
                                    "unsubscribe_link_type": "both", "schema_version": 2},
                           "css": {}, "child_css": {}, "styles": {}}
    order_ids.append("m-footer")

    sections = [{"id": "section-0", "columns": [{"id": "c0", "width": 12, "widgets": order_ids}],
                 "style": {"paddingTop": "20px", "paddingBottom": "20px"}}]
    p = requests.patch(f"https://api.hubapi.com/marketing/v3/emails/{eid}/draft", headers=H,
                       json={"content": {"flexAreas": {"main": {"sections": sections}}, "widgets": widgets}})
    print("PATCH", p.status_code, p.text[:300] if p.status_code != 200 else "")
    p.raise_for_status()
    pid = requests.get("https://api.hubapi.com/account-info/v3/details", headers=H).json().get("portalId","")
    print(f"\nOpen and report which of A/B/C shows a blue button:\n   https://app.hubspot.com/email/{pid}/edit/{eid}")

if __name__ == "__main__":
    main()
