#!/usr/bin/env python3
"""
Phase 0 smoke test for the Edanz remote inline-content tool.

Proves the end-to-end pipe BEFORE any remote/transport work: load config -> call
create_email_draft with inline Markdown -> confirm a real draft lands in HubSpot.
This exercises the inherited module-tree assembly + the new inline tool over a direct
Python call (no MCP transport involved yet).

Prerequisites
-------------
1.  pip install -e .            (from the repo root; pulls mcp, markdown, requests, etc.)
2.  A HubSpot private app in the Edanz portal/BU with scopes:
        content, files, marketing-email
3.  A config JSON (copy config.example.json), with the real token, e.g. ./config.local.json:
        { "hubspot_api_key": "pat-...", "audit_log_path": "./audit.jsonl" }
    For multi-BU routing, add a "brands" map and pass --brand edanz.

Run
---
    export HUBSPOT_EMAIL_MCP_CONFIG=./config.local.json
    python smoke_test.py
    # or:
    python smoke_test.py samples/edanz-sample-email.md --subject "Edanz — smoke test" --brand edanz

Success = a draft appears in HubSpot (open the printed email_url) and an audit line
is written. NOTHING is sent — create_email_draft only ever creates drafts.
"""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
DEFAULT_DOC = REPO / "samples" / "edanz-sample-email.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test create_email_draft against HubSpot.")
    ap.add_argument("doc", nargs="?", default=str(DEFAULT_DOC),
                    help="Markdown file to send as the email body (default: the Edanz sample).")
    ap.add_argument("--subject", default="Edanz Editorial — smoke test draft")
    ap.add_argument("--name", default=None, help="Internal HubSpot email name (default: subject).")
    ap.add_argument("--brand", default=None,
                    help="Brand key mapped to a Business Unit in config['brands'] (e.g. edanz). "
                         "Omit to draft into the default portal.")
    ap.add_argument("--preheader", default=None)
    args = ap.parse_args()

    if not os.environ.get("HUBSPOT_EMAIL_MCP_CONFIG"):
        print("ERROR: set HUBSPOT_EMAIL_MCP_CONFIG to your config JSON path first.\n"
              "  export HUBSPOT_EMAIL_MCP_CONFIG=./config.local.json", file=sys.stderr)
        return 2

    doc_path = Path(args.doc)
    if not doc_path.exists():
        print(f"ERROR: markdown file not found: {doc_path}", file=sys.stderr)
        return 2

    # Import after the env check so config errors surface cleanly.
    try:
        from hubspot_email_mcp import server
    except ModuleNotFoundError:
        print("ERROR: package not importable. Run `pip install -e .` in the repo root first.",
              file=sys.stderr)
        return 2

    server.load_config()
    body_markdown = doc_path.read_text(encoding="utf-8")

    print(f"→ Drafting from {doc_path.name}  (brand={args.brand!r}, subject={args.subject!r})")
    try:
        result = server.create_email_draft(
            subject=args.subject,
            body_markdown=body_markdown,
            brand=args.brand,
            email_name=args.name,
            preheader=args.preheader,
        )
    except Exception as e:
        print(f"\n✗ FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        print("  Check: token scopes (content/files/marketing-email), portal tier "
              "(Marketing Hub Pro/Enterprise), and — if --brand was used — the businessUnitId.",
              file=sys.stderr)
        return 1

    print("\n✓ Draft created (NOT sent):")
    for k in ("email_id", "status", "brand", "email_url"):
        if k in result:
            print(f"    {k}: {result[k]}")

    audit_path = server.config.get("audit_log_path")
    if audit_path and Path(audit_path).exists():
        last = Path(audit_path).read_text(encoding="utf-8").splitlines()[-1:]
        if last:
            print(f"\n  audit ({audit_path}): {last[0]}")

    print("\nNext: open email_url, send a test to your inbox from the HubSpot UI, "
          "and check rendering in Gmail + Outlook web.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
