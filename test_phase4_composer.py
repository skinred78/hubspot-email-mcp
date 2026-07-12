#!/usr/bin/env python3
"""
Phase 4 acceptance test-gate for compose_email_draft().

Encodes the three blend templates at ~/obsidian-vault/CMO/Content-Automation/Email-Design/
(01-nurture-cs.html, 02-case-study.html, 03-newsletter.html) as layout specs and runs them
end-to-end against THROWAWAY "ZZZ DELETE" drafts in the edanz Business Unit:

  1. compose_email_draft(layout_01) -> PATCH 200 -> GET /draft raw, assert every section /
     column / widget / twin / styleSettings value survived.
  2. get_template_manifest(email_id) -> assert every slot's desktop_id/mobile_id is detected
     and (for twinned slots) auto-paired.
  3. clone_email -> fill_email_draft with sample values (a text slot, a wrapped card, a
     button, an image) -> GET confirms content swapped on BOTH desktop and mobile twins;
     furniture (header/figure/footer) untouched.
  4. Mint templates 02 and 03 too, to exercise generality.
  5. Delete every throwaway EXCEPT the template-01 draft, which is left for a human to
     eyeball desktop + mobile in the HubSpot editor.

Run:
    export HUBSPOT_EMAIL_MCP_CONFIG=./config.local.json
    ./.venv/bin/python test_phase4_composer.py

Nothing is ever published or sent — every write hits a DRAFT only. This script prints a
PASS/FAIL transcript and never prints the API token.
"""
import base64
import json
import os
import sys
from io import BytesIO

import requests
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

BRAND = "edanz"
CREATED_EMAIL_IDS = []  # every draft this script creates, for cleanup bookkeeping


def avatar_data_uri(color=(2, 55, 98)) -> str:
    """A tiny solid-colour circle PNG as a data: URI, standing in for a real headshot."""
    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=color + (255,))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------------------
# Layout spec: Template 01 — Nurture (CS / 査読対策)
# ---------------------------------------------------------------------------------------

def step_wrapper(n: int, mobile: bool = False) -> str:
    eyebrow_size, title_size, body_size, pad = (12, 13, 11.5, "16px 14px") if mobile else (13, 15, 12.5, "20px 18px")
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#F5F5F5;border-radius:12px;"><tr><td style="padding:' + pad + ';">'
        f'<div style="font-family:Arial,sans-serif;font-size:{eyebrow_size}px;font-weight:700;'
        f'color:#BA2532;letter-spacing:1px;">STEP {n}</div>'
        f'<div style="font-size:{title_size}px;font-weight:700;color:#023762;line-height:1.5;'
        'margin-top:8px;">{{title}}</div>'
        f'<div style="font-size:{body_size}px;color:#5b6b7a;line-height:1.75;margin-top:6px;">'
        '{{body}}</div></td></tr></table>'
    )


def signoff_wrapper(mobile: bool = False) -> str:
    quote_size, name_size = (13, 12) if mobile else (14, 13)
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-left:3px solid #BA2532;"><tr><td style="padding:2px 0 0 20px;">'
        f'<p style="margin:0 0 10px 0;font-size:{quote_size}px;line-height:1.9;color:#272424;">'
        '{{body}}</p>'
        f'<p style="margin:0;font-size:{name_size}px;color:#5b6b7a;">{{title}}</p>'
        '</td></tr></table>'
    )


HEADER_HTML = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    '<td valign="middle"><span style="font-family:Arial,sans-serif;font-size:27px;font-weight:700;'
    'letter-spacing:.5px;color:#808080;">edanz</span>'
    '<span style="display:inline-block;width:2px;height:16px;background:#BA2532;margin:0 9px -2px 9px;"></span>'
    '<span style="font-family:Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:1.5px;'
    'color:#BA2532;">INNOVATIVE SCIENTIFIC SOLUTIONS</span></td>'
    '<td valign="middle" align="right" style="font-size:11px;color:#808080;letter-spacing:.5px;">'
    '{tagline}</td></tr></table>'
)

FIGURE_HTML_01 = (
    '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:2px;'
    'color:#8fb0d6;text-align:center;margin-bottom:16px;">FROM COMMENTS TO A CLEAR PATH</div>'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    '<td valign="middle" align="center" width="30%" style="color:#ffffff;">'
    '<div style="font-size:13px;font-weight:700;">査読コメント</div>'
    '<div style="font-size:11px;color:#a9c0da;margin-top:3px;">断片的・優先度不明</div></td>'
    '<td valign="middle" align="center" width="6%" style="color:#BA2532;font-size:22px;font-weight:700;">&rarr;</td>'
    '<td valign="middle" align="center" width="28%" style="color:#ffffff;">'
    '<div style="display:inline-block;background:#BA2532;border-radius:20px;padding:7px 16px;'
    'font-size:12px;font-weight:700;">第三者の視点で整理</div></td>'
    '<td valign="middle" align="center" width="6%" style="color:#BA2532;font-size:22px;font-weight:700;">&rarr;</td>'
    '<td valign="middle" align="center" width="30%" style="color:#ffffff;">'
    '<div style="font-size:13px;font-weight:700;">アクセプトへの道筋</div>'
    '<div style="font-size:11px;color:#a9c0da;margin-top:3px;">優先順位＋アクション</div></td>'
    '</tr></table>'
    '<div style="text-align:center;color:#ffffff;font-size:16px;font-weight:700;margin-top:20px;">'
    '推測から、確信へ。</div>'
)


def layout_01() -> dict:
    return {
        "name": "ZZZ DELETE - Phase4 Composer Test 01 Nurture",
        "subject": "査読コメントを、次の一手に。",
        "theme": {"page_bg": "#F5F5F5", "body_bg": "#FFFFFF", "button_color": "#BA2532",
                  "primary_color": "#272424", "h1_size": 28, "h2_size": 22},
        "sections": [
            {"id": "header", "fixed": True, "padding": [22, 6],
             "columns": [{"widgets": [{"type": "richtext", "html": HEADER_HTML.format(tagline="Custom Science")}]}]},
            {"id": "hero", "padding": [10, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "hero",
                 "html": (
                     '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
                     'letter-spacing:2px;color:#BA2532;margin-bottom:12px;">査読サポート</div>'
                     '<div style="font-size:28px;font-weight:700;line-height:1.45;color:#023762;'
                     'letter-spacing:.5px;">査読コメントを、次の一手に。</div>'
                     '<div style="font-size:15px;line-height:1.8;color:#5b6b7a;margin-top:14px;">'
                     '第三者の視点で、対応の優先順位と再投稿への道筋を整理します。</div>'
                 ),
             }]}]},
            {"id": "intro", "padding": [8, 8],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "intro",
                 "html": (
                     '<p style="margin:0 0 14px 0;font-size:15px;color:#272424;">〇〇先生</p>'
                     '<p style="margin:0;font-size:15px;line-height:1.9;color:#272424;">'
                     '査読結果が返ってきたとき、コメントの量が多く、どこから着手すべきか迷われることは'
                     '少なくありません。指摘の意図も、査読者ごとに異なります。出版実績のある専門家が'
                     '査読内容を客観的に読み解き、<strong style="color:#023762;">'
                     '「アクセプトに向けて、今、何をすべきか」</strong>を整理してご提案します。</p>'
                 ),
             }]}]},
            {"id": "steps", "padding": [8, 8],
             "columns": [
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "step_1", "content_kind": "title_body",
                     "content": {"title": "優先順位づけ", "body": "採否を分けるクリティカルな指摘を特定します。"},
                     "wrapper": {"desktop": step_wrapper(1), "mobile": step_wrapper(1, mobile=True)},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "step_2", "content_kind": "title_body",
                     "content": {"title": "論点の整理", "body": "査読者の意図を読み解き、対応の方向性を明確にします。"},
                     "wrapper": {"desktop": step_wrapper(2), "mobile": step_wrapper(2, mobile=True)},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "step_3", "content_kind": "title_body",
                     "content": {"title": "具体的アクション", "body": "再投稿に向けて「何を・どの順で」をプラン化します。"},
                     "wrapper": {"desktop": step_wrapper(3), "mobile": step_wrapper(3, mobile=True)},
                 }]},
             ]},
            {"id": "figure", "fixed": True, "bg": "#023762", "radius": 14, "padding": [26, 26],
             "columns": [{"widgets": [{"type": "richtext", "html": FIGURE_HTML_01}]}]},
            {"id": "signoff", "padding": [10, 6],
             "columns": [{"widgets": [
                 {"type": "image", "slot": "signoff_photo", "src": avatar_data_uri(), "alt": "パブリケーション・サポートチーム"},
                 {"type": "richtext", "slot": "signoff_text", "content_kind": "title_body",
                  "content": {"title": "滝沢　パブリケーション・サポートチーム",
                              "body": "論文は、書いた本人にはいちばん見えにくいものです。だからこそ、外の目が"
                                      "役に立ちます。まずは気になる査読コメントを、一緒に見てみませんか。"},
                  "wrapper": {"desktop": signoff_wrapper(), "mobile": signoff_wrapper(mobile=True)}},
             ]}]},
            {"id": "cta", "padding": [30, 30],
             "columns": [{"widgets": [
                 {"type": "button", "slot": "cta_primary", "text": "無料で査読内容を診断する", "url": "https://www.edanz.com/#test"},
                 {"type": "richtext", "slot": "cta_caption",
                  "html": '<div style="font-size:12.5px;color:#8a97a4;line-height:1.7;margin-top:14px;">'
                          '査読コメントと投稿時の原稿をご返信いただければ、対応の方向性をご案内します。</div>'},
             ]}]},
            {"id": "footer", "bg": "#272424", "padding": [24, 24],
             "columns": [{"widgets": [{"type": "footer"}]}]},
        ],
    }


def layout_02() -> dict:
    """Case study: same system, 3-col row = STATS, figure area = journal/result panel."""
    return {
        "name": "ZZZ DELETE - Phase4 Composer Test 02 Case Study",
        "subject": "リバイス後4日で、アクセプトへ。",
        "theme": {"page_bg": "#F5F5F5", "body_bg": "#FFFFFF", "button_color": "#BA2532"},
        "sections": [
            {"id": "header", "fixed": True, "padding": [22, 6],
             "columns": [{"widgets": [{"type": "richtext", "html": HEADER_HTML.format(tagline="Custom Science")}]}]},
            {"id": "hero", "padding": [10, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "hero",
                 "html": (
                     '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
                     'letter-spacing:2px;color:#BA2532;margin-bottom:12px;">掲載事例</div>'
                     '<div style="font-size:28px;font-weight:700;line-height:1.45;color:#023762;">'
                     'リバイス後4日で、アクセプトへ。</div>'
                     '<div style="font-size:15px;line-height:1.8;color:#5b6b7a;margin-top:14px;">'
                     '乳がん臨床解析を、スピード出版に導いた支援の舞台裏。</div>'
                 ),
             }]}]},
            {"id": "stats", "padding": [8, 6],
             "columns": [
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "stat_1", "content_kind": "title_body",
                     "content": {"title": "4 日", "body": "リバイス後、アクセプトまで"},
                     "wrapper": {"desktop": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:22px 12px;"><div style="font-family:Arial,sans-serif;font-size:32px;'
                         'font-weight:700;color:#BA2532;line-height:1;">{{title}}</div>'
                         '<div style="font-size:12px;color:#5b6b7a;line-height:1.6;margin-top:10px;">{{body}}'
                         '</div></td></tr></table>'),
                                 "mobile": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:18px 12px;"><div style="font-family:Arial,sans-serif;font-size:26px;'
                         'font-weight:700;color:#BA2532;line-height:1;">{{title}}</div>'
                         '<div style="font-size:11px;color:#5b6b7a;line-height:1.6;margin-top:8px;">{{body}}'
                         '</div></td></tr></table>')},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "stat_2", "content_kind": "title_body",
                     "content": {"title": "The Breast", "body": "掲載誌（国際誌）"},
                     "wrapper": {"desktop": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:22px 12px;"><div style="font-family:Arial,sans-serif;font-size:20px;'
                         'font-weight:700;color:#023762;">{{title}}</div>'
                         '<div style="font-size:12px;color:#5b6b7a;line-height:1.6;margin-top:12px;">{{body}}'
                         '</div></td></tr></table>'),
                                 "mobile": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:18px 12px;"><div style="font-family:Arial,sans-serif;font-size:17px;'
                         'font-weight:700;color:#023762;">{{title}}</div>'
                         '<div style="font-size:11px;color:#5b6b7a;line-height:1.6;margin-top:10px;">{{body}}'
                         '</div></td></tr></table>')},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "stat_3", "content_kind": "title_body",
                     "content": {"title": "1 回", "body": "の査読対応で採択"},
                     "wrapper": {"desktop": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:22px 12px;"><div style="font-family:Arial,sans-serif;font-size:32px;'
                         'font-weight:700;color:#BA2532;line-height:1;">{{title}}</div>'
                         '<div style="font-size:12px;color:#5b6b7a;line-height:1.6;margin-top:10px;">{{body}}'
                         '</div></td></tr></table>'),
                                 "mobile": (
                         '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         'style="background:#F5F5F5;border-radius:12px;"><tr><td align="center" '
                         'style="padding:18px 12px;"><div style="font-family:Arial,sans-serif;font-size:26px;'
                         'font-weight:700;color:#BA2532;line-height:1;">{{title}}</div>'
                         '<div style="font-size:11px;color:#5b6b7a;line-height:1.6;margin-top:8px;">{{body}}'
                         '</div></td></tr></table>')},
                 }]},
             ]},
            {"id": "figure", "bg": "#F5F5F5", "radius": 14, "padding": [18, 6],
             "columns": [{"widgets": [
                 {"type": "image", "slot": "journal_cover", "src": avatar_data_uri((186, 37, 50)), "alt": "Journal cover"},
                 {"type": "richtext", "slot": "figure_caption",
                  "html": '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
                          'letter-spacing:1.5px;color:#BA2532;">掲載論文</div>'
                          '<div style="font-size:15px;font-weight:700;color:#023762;line-height:1.6;'
                          'margin-top:8px;">中止後の治療をどうまとめたか：乳がん臨床解析の報告</div>'},
             ]}]},
            {"id": "body", "padding": [8, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "body_copy",
                 "html": '<p style="margin:0;font-size:15px;line-height:1.9;color:#272424;">'
                         'エダンズは、査読者の視点から論理構成を見直し、指摘の優先順位と再投稿の一手を'
                         '整理しました。データそのものではなく、<strong style="color:#023762;">'
                         '「どう伝われば査読者が納得するか」</strong>に焦点を当てたことが、短期間での'
                         '採択につながりました。</p>',
             }]}]},
            {"id": "signoff", "padding": [10, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "signoff_text", "content_kind": "title_body",
                 "content": {"title": "滝沢　パブリケーション・サポートチーム",
                             "body": "同じ視点は、先生のご研究にもお力になれるかもしれません。掲載を見据えた"
                                     "構成について、一度お話ししませんか。"},
                 "wrapper": {"desktop": signoff_wrapper(), "mobile": signoff_wrapper(mobile=True)},
             }]}]},
            {"id": "cta", "padding": [30, 30],
             "columns": [{"widgets": [
                 {"type": "button", "slot": "cta_primary", "text": "自分の論文について相談する", "url": "https://www.edanz.com/#test"},
             ]}]},
            {"id": "footer", "bg": "#272424", "padding": [24, 24],
             "columns": [{"widgets": [{"type": "footer"}]}]},
        ],
    }


def layout_03() -> dict:
    """Newsletter: masthead + feature card + 3-col topic row + editorial sign-off + CTA."""
    topic_card = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#F5F5F5;border-radius:12px;"><tr><td style="padding:{pad};">'
        '<div style="font-family:Arial,sans-serif;font-size:{eyebrow}px;font-weight:700;letter-spacing:1px;'
        'color:#BA2532;">{{{{title}}}}</div>'
        '<div style="font-size:{title}px;font-weight:700;color:#023762;line-height:1.5;margin-top:8px;">'
        '{{{{body}}}}</div></td></tr></table>'
    )
    return {
        "name": "ZZZ DELETE - Phase4 Composer Test 03 Newsletter",
        "subject": "査読を、味方にする。",
        "theme": {"page_bg": "#F5F5F5", "body_bg": "#FFFFFF", "button_color": "#BA2532"},
        "sections": [
            {"id": "header", "fixed": True, "padding": [22, 6],
             "columns": [{"widgets": [{"type": "richtext", "html": HEADER_HTML.format(tagline="メールマガジン")}]}]},
            {"id": "masthead", "padding": [10, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "masthead",
                 "html": (
                     '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;'
                     'letter-spacing:2px;color:#BA2532;margin-bottom:12px;">エダンズ メールマガジン ｜ 2026年7月号</div>'
                     '<div style="font-size:28px;font-weight:700;line-height:1.45;color:#023762;">査読を、味方にする。</div>'
                     '<div style="font-size:15px;line-height:1.8;color:#5b6b7a;margin-top:14px;">'
                     '今月は、査読対応の実践的なヒントと、最新の掲載事例をお届けします。</div>'
                 ),
             }]}]},
            {"id": "editors_note", "padding": [8, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "editors_note",
                 "html": '<p style="margin:0 0 12px 0;font-size:15px;color:#272424;">〇〇先生</p>'
                         '<p style="margin:0;font-size:15px;line-height:1.9;color:#272424;">'
                         '投稿から採択までの道のりで、査読は大きな山場です。今月は、その査読を前に進める'
                         'ための考え方と、実際の支援事例をまとめました。</p>',
             }]}]},
            {"id": "feature", "bg": "#F5F5F5", "radius": 14, "padding": [10, 6],
             "columns": [{"widgets": [
                 {"type": "image", "slot": "feature_image", "src": avatar_data_uri((2, 55, 98)), "alt": "Feature image"},
                 {"type": "richtext", "slot": "feature_card", "content_kind": "title_body",
                  "content": {"title": "効果的な査読対応、3つのヒント",
                              "body": "指摘の優先順位づけから、査読者の意図の読み解き方まで。再投稿を"
                                      "「推測」ではなく「確信」で進めるための考え方を整理しました。"},
                  "wrapper": {"desktop": (
                      '<div style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:1.5px;'
                      'color:#BA2532;">特集 ｜ 査読対応</div>'
                      '<div style="font-size:17px;font-weight:700;color:#023762;line-height:1.55;margin-top:8px;">'
                      '{{title}}</div>'
                      '<div style="font-size:13px;color:#5b6b7a;line-height:1.8;margin-top:8px;">{{body}}</div>'),
                              "mobile": (
                      '<div style="font-family:Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:1.5px;'
                      'color:#BA2532;">特集 ｜ 査読対応</div>'
                      '<div style="font-size:15px;font-weight:700;color:#023762;line-height:1.5;margin-top:8px;">'
                      '{{title}}</div>'
                      '<div style="font-size:12px;color:#5b6b7a;line-height:1.75;margin-top:8px;">{{body}}</div>')},
                  },
             ]}]},
            {"id": "topics_label", "fixed": True, "padding": [6, 2],
             "columns": [{"widgets": [{
                 "type": "richtext",
                 "html": '<div style="font-family:Arial,sans-serif;font-size:12px;font-weight:700;'
                         'letter-spacing:1px;color:#023762;">今月のトピック</div>',
             }]}]},
            {"id": "topics", "padding": [2, 8],
             "columns": [
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "topic_1", "content_kind": "title_body",
                     "content": {"title": "掲載事例", "body": "リバイス後4日で採択"},
                     "wrapper": {"desktop": topic_card.format(pad="18px 16px", eyebrow=10, title=14),
                                 "mobile": topic_card.format(pad="14px 14px", eyebrow=9, title=13)},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "topic_2", "content_kind": "title_body",
                     "content": {"title": "新ツール", "body": "査読者の視点をプレビュー"},
                     "wrapper": {"desktop": topic_card.format(pad="18px 16px", eyebrow=10, title=14),
                                 "mobile": topic_card.format(pad="14px 14px", eyebrow=9, title=13)},
                 }]},
                 {"width": 4, "widgets": [{
                     "type": "richtext", "slot": "topic_3", "content_kind": "title_body",
                     "content": {"title": "無料診断", "body": "論文の現在地を可視化"},
                     "wrapper": {"desktop": topic_card.format(pad="18px 16px", eyebrow=10, title=14),
                                 "mobile": topic_card.format(pad="14px 14px", eyebrow=9, title=13)},
                 }]},
             ]},
            {"id": "signoff", "padding": [10, 6],
             "columns": [{"widgets": [{
                 "type": "richtext", "slot": "signoff_text", "content_kind": "title_body",
                 "content": {"title": "滝沢　パブリケーション・サポートチーム",
                             "body": "研究のいちばん近くで、査読者の視点をお届けしていきます。気になる"
                                     "テーマやご相談があれば、いつでもこのメールにご返信ください。"},
                 "wrapper": {"desktop": signoff_wrapper(), "mobile": signoff_wrapper(mobile=True)},
             }]}]},
            {"id": "cta", "padding": [30, 30],
             "columns": [{"widgets": [
                 {"type": "button", "slot": "cta_primary", "text": "研究支援サービスを見る", "url": "https://www.edanz.com/#test"},
             ]}]},
            {"id": "footer", "bg": "#272424", "padding": [24, 24],
             "columns": [{"widgets": [{"type": "footer"}]}]},
        ],
    }


# ---------------------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------------------

PASS = []
FAIL = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(label)
        print(f"  PASS  {label}")
    else:
        FAIL.append(f"{label} :: {detail}")
        print(f"  FAIL  {label} :: {detail}")


def walk_widgets(raw_content: dict) -> dict:
    return raw_content.get("widgets", {}) or {}


def walk_sections(raw_content: dict) -> list:
    return (raw_content.get("flexAreas", {}) or {}).get("main", {}).get("sections", []) or []


def main() -> int:
    if not os.environ.get("HUBSPOT_EMAIL_MCP_CONFIG"):
        print("ERROR: export HUBSPOT_EMAIL_MCP_CONFIG=./config.local.json first", file=sys.stderr)
        return 2

    from hubspot_email_mcp import server
    server.load_config()

    print("=" * 88)
    print("STEP 1-3: compose_email_draft(layout_01) end-to-end against a throwaway draft")
    print("=" * 88)

    result_01 = server.compose_email_draft(layout_01(), brand=BRAND)
    email_id_01 = result_01["email_id"]
    CREATED_EMAIL_IDS.append(email_id_01)
    print(f"composed email_id={email_id_01} manifest={result_01['manifest_path']}")
    print(f"slots: {sorted(result_01['slots'].keys())}")

    # ---- Assertion 1: PATCH survived. Re-GET the raw draft and check structure. ----
    raw = server.get_email(email_id_01, raw=True)
    content = raw["content"]
    widgets = walk_widgets(content)
    sections = walk_sections(content)
    section_ids = [s["id"] for s in sections]

    check("styleSettings.backgroundColor == theme.page_bg",
          content.get("styleSettings", {}).get("backgroundColor") == "#F5F5F5",
          str(content.get("styleSettings")))
    check("styleSettings.bodyColor == theme.body_bg",
          content.get("styleSettings", {}).get("bodyColor") == "#FFFFFF",
          str(content.get("styleSettings")))
    check("templatePath is Start_from_scratch",
          content.get("templatePath") == "@hubspot/email/dnd/Start_from_scratch.html",
          str(content.get("templatePath")))
    check("all 9 logical sections present (header/hero/intro/steps-d/steps-m/figure/signoff/cta/footer)",
          {"header", "hero", "intro", "steps-d", "steps-m", "figure", "signoff", "cta", "footer"} <= set(section_ids),
          str(section_ids))

    steps_d = next(s for s in sections if s["id"] == "steps-d")
    check("steps-d section has 3 columns", len(steps_d["columns"]) == 3, str(len(steps_d["columns"])))
    check("steps-d columns are width 4", all(c["width"] == 4 for c in steps_d["columns"]),
          str([c["width"] for c in steps_d["columns"]]))
    check("steps-d style.stack == NONE", steps_d["style"].get("stack") == "NONE", str(steps_d["style"]))
    steps_m = next(s for s in sections if s["id"] == "steps-m")
    check("steps-m section has 1 column (width 12)",
          len(steps_m["columns"]) == 1 and steps_m["columns"][0]["width"] == 12,
          str(steps_m["columns"]))
    check("steps-m column holds all 3 mobile step widgets",
          len(steps_m["columns"][0]["widgets"]) == 3, str(steps_m["columns"][0]["widgets"]))

    figure_section = next(s for s in sections if s["id"] == "figure")
    check("figure section backgroundColor == #023762", figure_section["style"].get("backgroundColor") == "#023762",
          str(figure_section["style"]))
    check("figure section radius == 14px on all 4 corners (lives in breakpointStyles, not top-level style)",
          all(figure_section["style"].get("breakpointStyles", {}).get("default", {}).get(k) == "14px"
              and figure_section["style"].get("breakpointStyles", {}).get("mobile", {}).get(k) == "14px"
              for k in ("borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius", "borderBottomRightRadius")),
          str(figure_section["style"]))
    check("figure section breakpointStyles bg echo present",
          figure_section["style"].get("breakpointStyles", {}).get("default", {}).get("backgroundColor") == "#023762"
          and figure_section["style"].get("breakpointStyles", {}).get("mobile", {}).get("backgroundColor") == "#023762",
          str(figure_section["style"].get("breakpointStyles")))

    slots = result_01["slots"]
    for slot_name in ("step_1", "step_2", "step_3", "signoff_text"):
        spec = slots[slot_name]
        d_id, m_id = spec["desktop_id"], spec["mobile_id"]
        check(f"{slot_name}: desktop widget id present in draft widgets", d_id in widgets, d_id)
        check(f"{slot_name}: mobile widget id present in draft widgets", m_id in widgets, m_id)
        d_bp = widgets.get(d_id, {}).get("styles", {}).get("breakpointStyles", {})
        m_bp = widgets.get(m_id, {}).get("styles", {}).get("breakpointStyles", {})
        check(f"{slot_name}: desktop widget hidden-on-mobile",
              d_bp.get("default", {}).get("hidden") is False and d_bp.get("mobile", {}).get("hidden") is True,
              str(d_bp))
        check(f"{slot_name}: mobile widget hidden-on-desktop",
              m_bp.get("default", {}).get("hidden") is True and m_bp.get("mobile", {}).get("hidden") is False,
              str(m_bp))
        d_html = widgets[d_id]["body"]["html"]
        m_html = widgets[m_id]["body"]["html"]
        check(f"{slot_name}: desktop widget html has NO leftover {{{{ }}}} placeholders",
              "{{" not in d_html, d_html[:120])
        check(f"{slot_name}: mobile widget html has NO leftover {{{{ }}}} placeholders",
              "{{" not in m_html, m_html[:120])

    shared_photo = slots["signoff_photo"]
    check("signoff_photo: desktop_id == mobile_id (shared, not twinned)",
          shared_photo["desktop_id"] == shared_photo["mobile_id"], str(shared_photo))
    check("signoff_photo widget src is a real hosted HubSpot CDN URL (data: URI uploaded)",
          widgets[shared_photo["desktop_id"]]["body"]["img"]["src"].startswith("http"),
          widgets[shared_photo["desktop_id"]]["body"]["img"]["src"][:80])

    cta_spec = slots["cta_primary"]
    btn_body = widgets[cta_spec["desktop_id"]]["body"]
    check("cta_primary is native button module 1976948", widgets[cta_spec["desktop_id"]].get("module_id") == 1976948,
          str(widgets[cta_spec["desktop_id"]].get("module_id")))
    check("cta_primary corner_radius == 40", btn_body.get("corner_radius") == 40, str(btn_body.get("corner_radius")))
    check("cta_primary background_color == theme.button_color",
          btn_body.get("style", {}).get("background_color", {}).get("color") == "#BA2532",
          str(btn_body.get("style")))

    footer_widgets = [w for w in widgets.values() if w.get("body", {}).get("path") == "@hubspot/email_footer"]
    check("exactly one footer widget emitted", len(footer_widgets) == 1, str(len(footer_widgets)))
    check("footer widget carries branded footer_html (from brand config, not the layout spec)",
          bool(footer_widgets[0]["body"].get("footer_html")), "")
    check("no slot recorded for the footer", "footer" not in slots, str(list(slots.keys())))
    check("no slot recorded for the fixed header/figure furniture",
          not any("header" in k or "figure" in k for k in slots.keys()), str(list(slots.keys())))

    print(f"\nStep 1 subtotal: {len([p for p in PASS])} pass / {len(FAIL)} fail so far\n")

    # ---- Assertion 2: get_template_manifest detects + pairs every slot. ----
    print("=" * 88)
    print("STEP 2: get_template_manifest detects all fillable slots and pairs every twin")
    print("=" * 88)
    detected = server.get_template_manifest(email_id_01)
    detected_ids = {e["id"]: e for e in detected["editable_widgets"]}
    for slot_name, spec in slots.items():
        check(f"detector sees {slot_name}.desktop_id ({spec['desktop_id']})", spec["desktop_id"] in detected_ids,
              spec["desktop_id"])
        check(f"detector sees {slot_name}.mobile_id ({spec['mobile_id']})", spec["mobile_id"] in detected_ids,
              spec["mobile_id"])
    # Auto-pairing: every twinned slot (desktop_id != mobile_id) should appear as a pair in suggested_slots.
    suggested_pairs = {(v["desktop_id"], v["mobile_id"]) for v in detected["suggested_slots"].values()}
    twinned_slots = {k: v for k, v in slots.items() if v["desktop_id"] != v["mobile_id"]}
    for slot_name, spec in twinned_slots.items():
        check(f"detector auto-pairs twinned slot {slot_name}",
              (spec["desktop_id"], spec["mobile_id"]) in suggested_pairs,
              f"expected pair {(spec['desktop_id'], spec['mobile_id'])} not in {suggested_pairs}")
    print(f"unpaired (expected: the {len(slots) - len(twinned_slots)} shared, single-widget slots + brand furniture): "
          f"{detected['unpaired']}")

    # ---- Assertion 3: clone_email -> fill_email_draft -> GET confirms both twins swapped. ----
    print("=" * 88)
    print("STEP 3: clone_email -> fill_email_draft -> verify BOTH desktop+mobile twins swapped")
    print("=" * 88)
    slug = os.path.splitext(os.path.basename(result_01["manifest_path"]))[0]
    clone = server.clone_email(email_id_01, "ZZZ DELETE - Phase4 Composer Test 01 CLONE")
    clone_id = clone["id"]
    CREATED_EMAIL_IDS.append(clone_id)
    print(f"cloned -> {clone_id}")

    sample_values = {
        "intro": "TEST-TEXT-SLOT-VALUE 差し替え後の本文です。",
        "step_1": {"title": "TEST-CARD-TITLE", "body": "TEST-CARD-BODY 差し替え後のステップ本文。"},
        "cta_primary": {"text": "TEST-BUTTON-LABEL", "url": "https://example.com/test-fill"},
        "signoff_photo": {"src": avatar_data_uri((186, 37, 50)), "alt": "TEST-ALT-TEXT"},
    }
    fill_result = server.fill_email_draft(clone_id, sample_values, slug)
    check("fill_email_draft filled all 4 sample slots",
          set(fill_result["slots_filled"]) == set(sample_values.keys()), str(fill_result))
    check("fill_email_draft skipped nothing", fill_result["slots_skipped"] == [], str(fill_result["slots_skipped"]))

    filled_raw = server.get_email(clone_id, raw=True)
    filled_widgets = walk_widgets(filled_raw["content"])

    intro_spec = slots["intro"]
    check("intro (shared) widget body.html contains the new text",
          "TEST-TEXT-SLOT-VALUE" in filled_widgets[intro_spec["desktop_id"]]["body"]["html"],
          filled_widgets[intro_spec["desktop_id"]]["body"]["html"][:150])

    step1_spec = slots["step_1"]
    d_html = filled_widgets[step1_spec["desktop_id"]]["body"]["html"]
    m_html = filled_widgets[step1_spec["mobile_id"]]["body"]["html"]
    check("step_1 DESKTOP twin shows new card title+body (wrapper substitution applied)",
          "TEST-CARD-TITLE" in d_html and "TEST-CARD-BODY" in d_html, d_html[:200])
    check("step_1 MOBILE twin ALSO shows new card title+body (both twins written)",
          "TEST-CARD-TITLE" in m_html and "TEST-CARD-BODY" in m_html, m_html[:200])
    check("step_1 DESKTOP twin still carries its OWN (larger) font-size, not the mobile wrapper's",
          "font-size:15px" in d_html, d_html[:200])
    check("step_1 MOBILE twin still carries its OWN (smaller) font-size, not the desktop wrapper's",
          "font-size:13px" in m_html, m_html[:200])
    check("step_1 fixed 'STEP 1' marker furniture survived the fill (untouched)",
          "STEP 1" in d_html and "STEP 1" in m_html, "")

    btn_spec = slots["cta_primary"]
    btn = filled_widgets[btn_spec["desktop_id"]]["body"]
    check("cta_primary text updated", btn.get("text") == "TEST-BUTTON-LABEL", str(btn.get("text")))
    check("cta_primary destination updated", btn.get("destination") == "https://example.com/test-fill",
          str(btn.get("destination")))

    photo_spec = slots["signoff_photo"]
    img = filled_widgets[photo_spec["desktop_id"]]["body"]["img"]
    check("signoff_photo src re-uploaded to a new hosted URL", img.get("src", "").startswith("http"), img.get("src", ""))
    check("signoff_photo alt updated", img.get("alt") == "TEST-ALT-TEXT", str(img.get("alt")))

    # Furniture untouched: header/figure fixed widgets, and the OTHER step cards, unchanged.
    header_widget = next(w for w in filled_widgets.values()
                          if w.get("body", {}).get("path") == "@hubspot/rich_text" and "edanz" in w["body"].get("html", "")
                          and "INNOVATIVE" in w["body"].get("html", ""))
    check("header furniture text unchanged after fill", "Custom Science" in header_widget["body"]["html"], "")
    step2_spec = slots["step_2"]
    check("step_2 (NOT filled) desktop widget unchanged",
          "優先順位づけ" not in filled_widgets[step2_spec["desktop_id"]]["body"]["html"]
          and "論点の整理" in filled_widgets[step2_spec["desktop_id"]]["body"]["html"], "")

    print(f"\nAfter step 3: {len(PASS)} pass / {len(FAIL)} fail cumulative\n")

    # ---- Step 4: multi-column mobile-stacking note (structural only; no browser access) ----
    print("=" * 88)
    print("STEP 4: multi-column MOBILE-STACKING finding")
    print("=" * 88)
    print(
        "compose_email_draft implements the spec's DEFAULT (explicit stacked mobile-twin section) "
        "unconditionally: every multi-column row emits BOTH a desktop section (style.stack=NONE, "
        "N columns, each widget hidden on mobile) AND a separate mobile-twin section (one width-12 "
        "column, the same widgets' mobile twins, hidden on desktop) -- verified structurally above "
        "(steps-d / steps-m assertions). This sidesteps the open question (does a bare stack:NONE "
        "row auto-stack acceptably on mobile?) rather than resolving it: we did not test omitting "
        "the mobile-twin section, since the spec marks the explicit twin as the safe default and "
        "this environment cannot open the HubSpot mobile preview to compare the two approaches. "
        "The template-01 draft is left live (undeleted) below for a human to eyeball both the "
        "desktop and mobile preview in the HubSpot editor and confirm the twin section renders as "
        "3 stacked cards, not a squeezed 3-column row."
    )

    # ---- Step 5: mint templates 02 + 03 for generality ----
    print("=" * 88)
    print("STEP 5: mint templates 02 (case study) and 03 (newsletter)")
    print("=" * 88)
    result_02 = server.compose_email_draft(layout_02(), brand=BRAND)
    CREATED_EMAIL_IDS.append(result_02["email_id"])
    print(f"02 case-study -> {result_02['email_url']}  slots={sorted(result_02['slots'].keys())}")
    raw_02 = server.get_email(result_02["email_id"], raw=True)
    sections_02 = walk_sections(raw_02["content"])
    check("02: stats-d/stats-m twin sections present", {"stats-d", "stats-m"} <= {s["id"] for s in sections_02}, "")

    result_03 = server.compose_email_draft(layout_03(), brand=BRAND)
    CREATED_EMAIL_IDS.append(result_03["email_id"])
    print(f"03 newsletter -> {result_03['email_url']}  slots={sorted(result_03['slots'].keys())}")
    raw_03 = server.get_email(result_03["email_id"], raw=True)
    sections_03 = walk_sections(raw_03["content"])
    check("03: topics-d/topics-m twin sections present", {"topics-d", "topics-m"} <= {s["id"] for s in sections_03}, "")
    check("03: feature (2-widget single-column: image + wrapped card) composed",
          "feature_image" in result_03["slots"] and "feature_card" in result_03["slots"], "")

    print("\n" + "=" * 88)
    print(f"TOTAL: {len(PASS)} PASS / {len(FAIL)} FAIL")
    print("=" * 88)
    if FAIL:
        print("\nFAILURES:")
        for f in FAIL:
            print(f"  - {f}")

    # ---- Cleanup: delete every throwaway EXCEPT the template-01 draft (left for eyeballing). ----
    print("\n" + "=" * 88)
    print("CLEANUP")
    print("=" * 88)
    keep_id = email_id_01
    headers = server.get_hubspot_headers()
    for eid in CREATED_EMAIL_IDS:
        if eid == keep_id:
            print(f"KEEPING {eid} for visual inspection: "
                  f"https://app.hubspot.com/email/{server.get_portal_id()}/edit/{eid}")
            continue
        r = requests.delete(f"https://api.hubapi.com/marketing/v3/emails/{eid}", headers=headers, timeout=30)
        print(f"deleted {eid}: HTTP {r.status_code}")

    print(json.dumps({
        "result_01": {k: v for k, v in result_01.items() if k != "slots"},
        "result_02": {k: v for k, v in result_02.items() if k != "slots"},
        "result_03": {k: v for k, v in result_03.items() if k != "slots"},
        "kept_for_inspection": keep_id,
    }, indent=2))

    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
