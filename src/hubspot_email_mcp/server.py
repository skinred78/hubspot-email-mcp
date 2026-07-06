"""HubSpot Email MCP Server - Create marketing emails from documents"""
import base64
import html as _html
import json
import os
import hashlib
import re
import secrets
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timezone
import logging

import requests
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
import markdown
from PIL import Image
from io import BytesIO
from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _SimpleOAuthProvider:
    """Minimal in-memory OAuth 2.0 authorization server for Claude.ai connector auth.

    Supports dynamic client registration and the authorization code + PKCE flow.
    The authorize step auto-approves — no login page — suitable for internal tools
    where Claude.ai workspace membership is the trust boundary.

    State lives in memory. Set HUBSPOT_EMAIL_MCP_STATE_PATH (to a file on a durable
    Railway volume) to persist registrations + tokens across restarts; otherwise they
    are lost on restart and every OAuth client must remove-and-re-add the connector.
    """

    def __init__(self) -> None:
        self._clients: dict = {}
        self._codes: dict = {}
        self._access_tokens: dict = {}
        self._refresh_tokens: dict = {}
        # Optional pre-shared static bearer for non-interactive clients (e.g. run-queue
        # sub-agents) that cannot complete a browser OAuth flow. When set, a request
        # presenting `Authorization: Bearer <this>` authenticates directly — no /authorize,
        # no /callback. The interactive Claude.ai OAuth path is unaffected. Read-only at the
        # transport layer; tool-level behaviour is identical to an OAuth-authenticated client.
        self._static_token = os.environ.get("HUBSPOT_EMAIL_MCP_STATIC_TOKEN", "").strip()
        # Optional durable store for OAuth registrations + tokens. Without it, the dicts
        # above live only in memory and every Railway restart/redeploy wipes them, forcing
        # each Claude.ai / Desktop user to remove-and-re-add the connector. Point
        # HUBSPOT_EMAIL_MCP_STATE_PATH at a file on a mounted Railway volume and the state
        # survives restarts (users stay connected). Unset = in-memory only (unchanged
        # local/stdio behaviour). Auth codes are intentionally NOT persisted: single-use,
        # 5-minute TTL, harmless to lose.
        self._state_path = os.environ.get("HUBSPOT_EMAIL_MCP_STATE_PATH", "").strip()
        self._load_state()

    def _load_state(self) -> None:
        """Rehydrate clients + tokens from the state file, if configured and present."""
        if not self._state_path:
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("OAuth state unreadable at %s (%s); starting empty", self._state_path, exc)
            return
        from mcp.shared.auth import OAuthClientInformationFull
        from mcp.server.auth.provider import AccessToken, RefreshToken
        try:
            self._clients = {
                cid: OAuthClientInformationFull(**c)
                for cid, c in data.get("clients", {}).items()
            }
            self._access_tokens = {
                tok: AccessToken(**a) for tok, a in data.get("access_tokens", {}).items()
            }
            self._refresh_tokens = {
                tok: RefreshToken(**r) for tok, r in data.get("refresh_tokens", {}).items()
            }
        except Exception as exc:  # never let malformed state crash startup
            logger.warning("OAuth state at %s failed to parse (%s); starting empty", self._state_path, exc)
            self._clients, self._access_tokens, self._refresh_tokens = {}, {}, {}
            return
        logger.info(
            "Loaded persisted OAuth state: %d client(s), %d access token(s), %d refresh token(s)",
            len(self._clients), len(self._access_tokens), len(self._refresh_tokens),
        )

    def _save_state(self) -> None:
        """Atomically write clients + tokens to the state file, if configured.

        Called after every mutation. The write is small and infrequent (one per connector
        auth / token refresh), so a synchronous write-through is fine for a single-replica
        service. os.replace makes the swap atomic, so a crash mid-write cannot corrupt the
        file. Assumes a single writer (one worker/replica).
        """
        if not self._state_path:
            return
        data = {
            "clients": {cid: c.model_dump(mode="json") for cid, c in self._clients.items()},
            "access_tokens": {tok: a.model_dump(mode="json") for tok, a in self._access_tokens.items()},
            "refresh_tokens": {tok: r.model_dump(mode="json") for tok, r in self._refresh_tokens.items()},
        }
        try:
            parent = os.path.dirname(self._state_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = f"{self._state_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self._state_path)
        except OSError as exc:
            logger.warning("Failed to persist OAuth state to %s: %s", self._state_path, exc)

    async def get_client(self, client_id: str):
        return self._clients.get(client_id)

    async def register_client(self, client_info) -> None:
        self._clients[client_info.client_id] = client_info
        self._save_state()

    async def authorize(self, client, params) -> str:
        from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, authorization_code: str):
        return self._codes.get(authorization_code)

    async def exchange_authorization_code(self, client, authorization_code):
        from mcp.server.auth.provider import AccessToken, RefreshToken
        from mcp.shared.auth import OAuthToken
        self._codes.pop(authorization_code.code, None)
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self._access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=None,
            resource=authorization_code.resource,
        )
        self._refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
        )
        self._save_state()
        return OAuthToken(
            access_token=access,
            token_type="bearer",
            refresh_token=refresh,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(self, client, refresh_token: str):
        return self._refresh_tokens.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        from mcp.server.auth.provider import AccessToken, RefreshToken
        from mcp.shared.auth import OAuthToken
        self._refresh_tokens.pop(refresh_token.token, None)
        access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        effective_scopes = scopes or refresh_token.scopes
        self._access_tokens[access] = AccessToken(
            token=access, client_id=client.client_id, scopes=effective_scopes, expires_at=None,
        )
        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh, client_id=client.client_id, scopes=effective_scopes,
        )
        self._save_state()
        return OAuthToken(access_token=access, token_type="bearer", refresh_token=new_refresh)

    async def load_access_token(self, token: str):
        existing = self._access_tokens.get(token)
        if existing is not None:
            return existing
        # Headless / service-token path: a pre-shared static bearer that bypasses the
        # interactive browser OAuth flow. Constant-time compare to avoid timing leaks.
        if self._static_token and secrets.compare_digest(token, self._static_token):
            from mcp.server.auth.provider import AccessToken
            return AccessToken(
                token=token,
                client_id="headless-service",
                scopes=[],
                expires_at=None,
            )
        return None

    async def revoke_token(self, token) -> None:
        from mcp.server.auth.provider import AccessToken
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
        else:
            self._refresh_tokens.pop(token.token, None)
        self._save_state()


# Initialize FastMCP — with OAuth if SERVER_URL is set (remote/Claude.ai), plain otherwise (stdio/local).
_SERVER_URL = os.environ.get("SERVER_URL", "")
if _SERVER_URL:
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
    from mcp.server.transport_security import TransportSecuritySettings
    _oauth_provider = _SimpleOAuthProvider()
    mcp = FastMCP(
        "hubspot-email",
        auth_server_provider=_oauth_provider,
        auth=AuthSettings(
            issuer_url=_SERVER_URL,
            resource_server_url=_SERVER_URL,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
        # Configuring auth auto-enables DNS-rebinding protection with a localhost-only
        # host allowlist, which 421s the Railway proxy's Host header. The real threat
        # model (a browser rebinding DNS to a localhost server) doesn't apply to a public
        # TLS server gated by OAuth, so disable it.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
else:
    mcp = FastMCP("hubspot-email")


@mcp.custom_route("/health", methods=["GET"])
async def _health_check(request):
    """Unauthenticated health check for Railway. custom_route bypasses OAuth.

    The OAuth-enabled streamable-http app only serves /mcp + the OAuth endpoints,
    so without this explicit route Railway's /health probe 404s and fails the deploy.
    """
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("ok")


# Global configuration
config = {}
brand_guidelines = {}


def load_config():
    """Load configuration from a JSON file (local) or individual env vars (remote/Railway)."""
    global config, brand_guidelines
    config_path = os.environ.get("HUBSPOT_EMAIL_MCP_CONFIG")

    if config_path:
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        # Env-var config path — used by Railway and other remote deployments.
        # Set HUBSPOT_API_KEY and optionally HUBSPOT_BRANDS_JSON (a JSON object string).
        api_key = os.environ.get("HUBSPOT_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Either HUBSPOT_EMAIL_MCP_CONFIG (file path) or HUBSPOT_API_KEY must be set"
            )
        config["hubspot_api_key"] = api_key
        brands_json = os.environ.get("HUBSPOT_BRANDS_JSON", "{}")
        try:
            config["brands"] = json.loads(brands_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"HUBSPOT_BRANDS_JSON is not valid JSON: {exc}") from exc
        config["audit_log_path"] = os.environ.get("HUBSPOT_AUDIT_LOG_PATH", "")
        config["file_folder_path"] = os.environ.get("HUBSPOT_FILE_FOLDER_PATH", "/email-images")

    # hubspot_api_key is the only hard requirement. file_folder_path is only used by
    # the legacy local-file path (create_marketing_email); the remote inline tool
    # (create_email_draft) does not need it, so default it rather than require it.
    if "hubspot_api_key" not in config:
        raise ValueError("Missing required config field: hubspot_api_key")
    config.setdefault("file_folder_path", "/email-images")

    # Optional: brand -> {business_unit_id, name_prefix?} map for multi-BU routing.
    # Optional: audit_log_path for the structured per-call attribution log (decision #6).
    config.setdefault("brands", {})
    config.setdefault("audit_log_path", "")

    # Load brand guidelines if specified
    brand_guidelines_path = config.get("brand_guidelines_path")
    if brand_guidelines_path:
        try:
            # Support both absolute and relative paths
            if not os.path.isabs(brand_guidelines_path) and config_path:
                config_dir = os.path.dirname(config_path)
                brand_guidelines_path = os.path.join(config_dir, brand_guidelines_path)

            with open(brand_guidelines_path, 'r') as f:
                brand_guidelines = json.load(f)
            logger.info(f"Brand guidelines loaded from: {brand_guidelines_path}")
        except Exception as e:
            logger.warning(f"Failed to load brand guidelines: {e}")
            brand_guidelines = {}
    else:
        logger.info("No brand guidelines specified, using default styles")
        brand_guidelines = {}

    logger.info("Configuration loaded successfully")


def get_hubspot_headers() -> Dict[str, str]:
    """Get HubSpot API headers"""
    return {
        "Authorization": f"Bearer {config['hubspot_api_key']}",
        "Content-Type": "application/json"
    }


def audit_log(event: Dict) -> None:
    """
    Append a structured JSON line to the audit log.

    Under the shared service-account token model (architecture decision #6), HubSpot's
    own audit trail attributes every draft to the service account. This log is the ONLY
    place per-user / per-brand attribution survives, so it is written on every draft call.

    Always stamps `ts` (UTC ISO 8601). Falls back to stderr logging if no audit_log_path
    is configured or the write fails — auditing must never silently disappear.
    """
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    line = json.dumps(event, ensure_ascii=False)
    path = config.get("audit_log_path")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        except Exception as e:
            logger.error(f"Audit log write failed ({path}): {e}")
    # Fallback: at least emit it to the process log so the host can capture it.
    logger.info(f"AUDIT {line}")


def resolve_business_unit(brand: Optional[str]) -> Optional[str]:
    """
    Map a brand key (e.g. 'edanz') to a HubSpot businessUnitId from config['brands'].

    Returns None when no brand is given or no mapping exists (single-BU / default portal).
    Raises if a brand IS given but unknown — fail loud rather than silently draft into the
    wrong business unit.
    """
    if not brand:
        return None
    brands = config.get("brands", {})
    if brand not in brands:
        raise ValueError(
            f"Unknown brand '{brand}'. Configured brands: {sorted(brands.keys()) or '(none)'}"
        )
    return brands[brand].get("business_unit_id")


_portal_id_cache: Optional[str] = None


def get_portal_id() -> str:
    """
    Return the HubSpot portal (hub) ID, used to build the draft edit URL.

    The marketing-email create response does NOT include portalId, which produced a broken
    '/email//edit/...' link. Resolve it once via the account-info API and cache it. Prefers
    an explicit config['portal_id'] if set; degrades to '' on failure (URL still usable minus
    the portal segment).
    """
    global _portal_id_cache
    if _portal_id_cache is not None:
        return _portal_id_cache
    if config.get("portal_id"):
        _portal_id_cache = str(config["portal_id"])
        return _portal_id_cache
    try:
        r = requests.get("https://api.hubapi.com/account-info/v3/details",
                         headers=get_hubspot_headers(), timeout=30)
        r.raise_for_status()
        _portal_id_cache = str(r.json().get("portalId", ""))
    except Exception as e:
        logger.warning(f"Could not resolve portal ID for draft URL: {e}")
        _portal_id_cache = ""
    return _portal_id_cache


def apply_iwt_content_formatting(html: str) -> str:
    """
    Apply content formatting patterns to HTML

    This includes:
    - Adding spacing between paragraphs
    - Adding spacing to list items (except last)
    - Replacing NAME with personalization tokens
    - Adding inline styles to headings
    - Adding spacing around headings

    Args:
        html: The HTML content string

    Returns:
        HTML with content formatting applied
    """
    import re

    # Replace "NAME" or "NAME," with personalization token
    # Pattern matches NAME at start of paragraph, case-insensitive
    # Use a placeholder to prevent escaping later
    html = re.sub(
        r'<p>\s*NAME\s*,?\s*</p>',
        r'<p>__PERSONALIZATION_TOKEN__,</p>',
        html,
        flags=re.IGNORECASE
    )

    # Add inline styles to headings based on brand guidelines
    if brand_guidelines:
        typography = brand_guidelines.get("typography", {})
        colors = brand_guidelines.get("colors", {})

        # H1 styling
        if typography.get("headingOneFont") and typography.get("headingOneSize"):
            h1_styles = [
                f"font-family: {typography['headingOneFont']}",
                f"font-size: {typography['headingOneSize']}px"
            ]
            if typography.get("headingLineHeight"):
                h1_styles.append(f"line-height: {typography['headingLineHeight']}")
            if colors.get("text"):
                h1_styles.append(f"color: {colors['text']}")

            h1_style_attr = "; ".join(h1_styles)
            html = re.sub(r'<h1>([^<]+)</h1>', rf'<h1 style="{h1_style_attr}">\1</h1>', html)

        # H2 styling
        if typography.get("headingTwoFont") and typography.get("headingTwoSize"):
            h2_styles = [
                f"font-family: {typography['headingTwoFont']}",
                f"font-size: {typography['headingTwoSize']}px"
            ]
            if typography.get("headingLineHeight"):
                h2_styles.append(f"line-height: {typography['headingLineHeight']}")
            if colors.get("text"):
                h2_styles.append(f"color: {colors['text']}")

            h2_style_attr = "; ".join(h2_styles)
            html = re.sub(r'<h2>([^<]+)</h2>', rf'<h2 style="{h2_style_attr}">\1</h2>', html)

    # Add spacing around headings first (before and after)
    # Add <p>&nbsp;</p> before h1/h2
    html = re.sub(r'(<h[12]\s)', r'<p>&nbsp;</p>\n\1', html)
    # Add <p>&nbsp;</p> after h1/h2
    html = re.sub(r'(</h[12]>)', r'\1\n<p>&nbsp;</p>', html)

    # Add spacing between paragraphs (insert <p>&nbsp;</p> between </p> and <p>)
    # But not if there's already a space, and not before/after headings
    html = re.sub(
        r'</p>\s*<p>(?!&nbsp;)',
        r'</p>\n<p>&nbsp;</p>\n<p>',
        html
    )

    # Add padding to list items (except the last one in each list)
    # Find all <ul> or <ol> blocks and process them
    def add_list_spacing(match):
        list_content = match.group(0)
        # Find all <li> tags
        li_tags = re.findall(r'<li[^>]*>.*?</li>', list_content, re.DOTALL)
        if len(li_tags) <= 1:
            return list_content  # Don't modify single-item lists

        # Add padding to all but the last item
        for i, li_tag in enumerate(li_tags[:-1]):  # All except last
            if 'style=' not in li_tag:
                # Add style attribute
                modified_li = li_tag.replace('<li>', '<li style="padding-bottom: 10px;">')
            else:
                # Append to existing style
                modified_li = re.sub(
                    r'style="([^"]*)"',
                    r'style="\1; padding-bottom: 10px;"',
                    li_tag
                )
            list_content = list_content.replace(li_tag, modified_li, 1)

        return list_content

    html = re.sub(r'<ul>.*?</ul>', add_list_spacing, html, flags=re.DOTALL)
    html = re.sub(r'<ol>.*?</ol>', add_list_spacing, html, flags=re.DOTALL)

    # Replace placeholder with actual personalization token (after all other processing)
    html = html.replace('__PERSONALIZATION_TOKEN__', "{{ personalization_token('contact.firstname', 'Hey') }}")

    return html


def apply_brand_styles_to_html(html: str) -> str:
    """
    Apply brand guidelines styling directly to HTML content

    Args:
        html: The HTML content string

    Returns:
        HTML wrapped with inline styles and content formatting
    """
    if not brand_guidelines:
        return html

    # First apply content formatting patterns
    html = apply_iwt_content_formatting(html)

    typography = brand_guidelines.get("typography", {})
    colors = brand_guidelines.get("colors", {})
    spacing = brand_guidelines.get("spacing", {})

    # Build inline style string
    styles = []
    if typography.get("primaryFont"):
        styles.append(f"font-family: {typography['primaryFont']}")
    if typography.get("bodySize"):
        styles.append(f"font-size: {typography['bodySize']}px")
    if typography.get("bodyLineHeight"):
        styles.append(f"line-height: {typography['bodyLineHeight']}")
    if colors.get("text"):
        styles.append(f"color: {colors['text']}")
    if spacing.get("modulePaddingTop"):
        styles.append(f"padding-top: {spacing['modulePaddingTop']}")
    if spacing.get("modulePaddingBottom"):
        styles.append(f"padding-bottom: {spacing['modulePaddingBottom']}")

    style_attr = "; ".join(styles)

    # Wrap HTML in div with inline styles
    return f'<div style="{style_attr}">{html}</div>'


def apply_brand_styles_to_text_module(widget: Dict) -> Dict:
    """
    Apply brand guidelines to a rich text module

    Args:
        widget: The widget dictionary

    Returns:
        Updated widget with brand styles applied
    """
    if not brand_guidelines:
        return widget

    # Apply inline styles to the HTML content
    html_content = widget["body"].get("html", "")
    widget["body"]["html"] = apply_brand_styles_to_html(html_content)

    return widget


def apply_brand_styles_to_image_module(widget: Dict) -> Dict:
    """
    Apply brand guidelines to an image module (default HubSpot email image module)

    Args:
        widget: The widget dictionary

    Returns:
        Updated widget with brand styles applied
    """
    if not brand_guidelines:
        return widget

    images_config = brand_guidelines.get("images", {})
    spacing = brand_guidelines.get("spacing", {})

    # Apply image width
    if images_config.get("defaultWidth"):
        widget["body"]["img"]["width"] = images_config["defaultWidth"]

    # Apply corner radius through the style object
    if images_config.get("cornerRadius"):
        widget["body"]["style"]["corner_radius"] = images_config["cornerRadius"]
        widget["body"]["style"]["corner_radius_unit"] = "px"

    # Apply padding through hs_wrapper_css
    wrapper_css = {}
    if spacing.get("modulePaddingTop"):
        wrapper_css["padding-top"] = spacing["modulePaddingTop"]
    if spacing.get("modulePaddingBottom"):
        wrapper_css["padding-bottom"] = spacing["modulePaddingBottom"]

    # Add left/right padding if specified in images config
    if images_config.get("padding"):
        # Parse padding like "10px 20px"
        padding_parts = images_config["padding"].split()
        if len(padding_parts) == 2:
            wrapper_css["padding-left"] = padding_parts[1]
            wrapper_css["padding-right"] = padding_parts[1]

    if wrapper_css:
        widget["body"]["hs_wrapper_css"] = wrapper_css

    return widget


def get_section_styles() -> Dict:
    """
    Get section styles from brand guidelines

    Returns:
        Dictionary of section style properties
    """
    if not brand_guidelines:
        return {
            "paddingTop": "40px",
            "paddingBottom": "40px"
        }

    spacing = brand_guidelines.get("spacing", {})
    colors = brand_guidelines.get("colors", {})

    styles = {
        "paddingTop": spacing.get("sectionPaddingTop", "40px"),
        "paddingBottom": spacing.get("sectionPaddingBottom", "40px")
    }

    if colors.get("background"):
        styles["backgroundColor"] = colors["background"]

    return styles


def parse_markdown(file_path: str) -> Tuple[str, List[Tuple[str, bytes, str]]]:
    """
    Parse markdown file and extract HTML and images

    Returns:
        Tuple of (html_content, list of (alt_text, image_bytes, filename))
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Convert markdown to HTML
    html = markdown.markdown(md_content)

    # Extract local image references
    images = []
    image_pattern = r'!\[(.*?)\]\((.*?)\)'
    matches = re.findall(image_pattern, md_content)

    base_path = Path(file_path).parent

    for alt_text, img_path in matches:
        # Skip URLs (already hosted)
        if img_path.startswith(('http://', 'https://')):
            continue

        # Handle local file
        full_path = base_path / img_path
        if full_path.exists():
            with open(full_path, 'rb') as img_file:
                img_bytes = img_file.read()
            filename = full_path.name
            images.append((alt_text, img_bytes, filename))
        else:
            logger.warning(f"Image not found: {full_path}")

    return html, images


def parse_docx_blocks(file_path: str) -> List[Tuple]:
    """
    Parse DOCX file and return content blocks in order

    Returns:
        List of tuples: ('text', html_content) or ('image', image_bytes, filename)
    """
    doc = Document(file_path)

    # Build a mapping of image relationship IDs to image data
    image_map = {}
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            try:
                image_part = rel.target_part
                image_bytes = image_part.blob
                content_type = image_part.content_type
                ext_map = {
                    'image/jpeg': 'jpg',
                    'image/png': 'png',
                    'image/gif': 'gif',
                    'image/bmp': 'bmp'
                }
                ext = ext_map.get(content_type, 'jpg')
                img_hash = hashlib.md5(image_bytes).hexdigest()[:8]
                image_map[rel.rId] = (image_bytes, ext, img_hash)
            except Exception as e:
                logger.warning(f"Failed to extract image: {e}")

    # Extract content blocks in order
    content_blocks = []
    image_index = 0
    text_buffer = []
    in_list = False
    list_items = []

    for element in doc.element.body:
        if element.tag.endswith('p'):
            para = None
            for p in doc.paragraphs:
                if p._element == element:
                    para = p
                    break

            if para:
                # Check if paragraph contains an image
                has_image = False
                for run in para.runs:
                    for drawing in run._element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing'):
                        blip = drawing.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip')
                        if blip is not None:
                            embed_id = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                            if embed_id and embed_id in image_map:
                                # Flush any open list
                                if in_list and list_items:
                                    text_buffer.append("<ul>")
                                    for item in list_items:
                                        text_buffer.append(f"<li>{item}</li>")
                                    text_buffer.append("</ul>")
                                    list_items = []
                                    in_list = False

                                # Flush text buffer before image
                                if text_buffer:
                                    content_blocks.append(('text', "\n".join(text_buffer)))
                                    text_buffer = []

                                image_bytes, ext, img_hash = image_map[embed_id]
                                filename = f"image_{image_index}_{img_hash}.{ext}"
                                content_blocks.append(('image', image_bytes, filename))
                                image_index += 1
                                has_image = True

                # Add text to buffer
                if para.text.strip() and not has_image:
                    # Check if this is a list item
                    is_list_item = False
                    if para.style and para.style.name in ['List Paragraph', 'List Bullet', 'List Number']:
                        is_list_item = True
                    # Also check for numbering properties
                    elif para._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr') is not None:
                        is_list_item = True

                    if is_list_item:
                        # This is a list item
                        if not in_list:
                            in_list = True
                        list_items.append(para.text)
                    else:
                        # Not a list item - flush any open list first
                        if in_list and list_items:
                            text_buffer.append("<ul>")
                            for item in list_items:
                                text_buffer.append(f"<li>{item}</li>")
                            text_buffer.append("</ul>")
                            list_items = []
                            in_list = False

                        # Add paragraph
                        if para.style.name.startswith('Heading'):
                            level = para.style.name.replace('Heading ', '')
                            try:
                                level = int(level)
                                text_buffer.append(f"<h{level}>{para.text}</h{level}>")
                            except ValueError:
                                text_buffer.append(f"<p>{para.text}</p>")
                        else:
                            text_buffer.append(f"<p>{para.text}</p>")

    # Flush any remaining list
    if in_list and list_items:
        text_buffer.append("<ul>")
        for item in list_items:
            text_buffer.append(f"<li>{item}</li>")
        text_buffer.append("</ul>")
        list_items = []

    # Flush remaining text
    if text_buffer:
        content_blocks.append(('text', "\n".join(text_buffer)))

    return content_blocks


def parse_docx(file_path: str) -> Tuple[str, List[Tuple[str, bytes, str]]]:
    """
    Parse DOCX file (backward compatibility wrapper)

    Returns:
        Tuple of (html_content, list of (alt_text, image_bytes, filename))
    """
    blocks = parse_docx_blocks(file_path)

    # Extract text and images separately for backward compatibility
    html_parts = [block[1] for block in blocks if block[0] == 'text']
    html = "\n".join(html_parts)

    images = [("Embedded image", block[1], block[2]) for block in blocks if block[0] == 'image']

    return html, images


def upload_image_to_hubspot(image_bytes: bytes, filename: str) -> Optional[str]:
    """
    Upload image to HubSpot File Manager

    Returns:
        CDN URL of uploaded image or None on failure
    """
    url = "https://api.hubapi.com/files/v3/files"

    # Prepare multipart form data
    files = {
        'file': (filename, BytesIO(image_bytes)),
    }

    data = {
        'folderPath': config['file_folder_path'],
        'options': json.dumps({
            "access": "PUBLIC_INDEXABLE",
            "overwrite": False
        })
    }

    headers = {
        "Authorization": f"Bearer {config['hubspot_api_key']}"
    }

    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        result = response.json()
        cdn_url = result.get('url')
        logger.info(f"Successfully uploaded image: {filename} -> {cdn_url}")
        return cdn_url
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to upload image {filename}: {e}")
        if hasattr(e.response, 'text'):
            logger.error(f"Response: {e.response.text}")
        return None


def create_hubspot_email_from_blocks(email_name: str, subject_line: str, content_blocks: List[Tuple], business_unit_id: Optional[str] = None) -> Dict:
    """
    Create a marketing email draft in HubSpot from content blocks

    This function creates an optimized email structure to prevent Gmail clipping:
    - Uses HubSpot's default modules (@hubspot/rich_text, @hubspot/email_linked_image)
    - Places all content modules in a SINGLE section to minimize HTML bloat
    - Only creates new sections when column layout changes (future enhancement)
    - Gmail clips emails over 102KB; each section adds significant HTML overhead

    Args:
        email_name: Name for the email
        subject_line: Email subject
        content_blocks: List of ('text', html) or ('image', cdn_url) tuples

    Returns:
        Dictionary with email_id, email_url, and status
    """
    url = "https://api.hubapi.com/marketing/v3/emails"
    headers = get_hubspot_headers()

    try:
        # Step 1: Create the email
        payload = {
            "name": email_name,
            "subject": subject_line,
            "emailType": "BATCH_EMAIL"
        }
        # Route the draft into a brand's Business Unit when configured (architecture decision #2).
        # NOTE: verify the exact field/placement for businessUnitId on POST /marketing/v3/emails
        # against the current HubSpot API before trusting multi-BU routing in production.
        if business_unit_id:
            payload["businessUnitId"] = business_unit_id

        logger.info(f"Creating email with name: {email_name}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        email_id = result.get('id')
        portal_id = result.get('portalId') or get_portal_id()

        logger.info(f"Email created with ID: {email_id}")

        # Step 2: Build sections and widgets from content blocks
        update_url = f"https://api.hubapi.com/marketing/v3/emails/{email_id}/draft"

        sections = []
        widgets = {}
        widget_index = 0

        # Create main content section with all content modules (single column)
        # IMPORTANT: All modules go in ONE section to prevent Gmail clipping
        # Multiple sections create HTML bloat that can push emails over 102KB limit
        main_section_widgets = []

        for block_type, block_data in content_blocks:
            widget_id = f"module-{widget_index}"
            main_section_widgets.append(widget_id)

            if block_type == 'text':
                # Create text module using HubSpot's default rich_text module
                widget = {
                    "id": widget_id,
                    "name": widget_id,
                    "type": "module",
                    "order": widget_index,
                    "body": {
                        "path": "@hubspot/rich_text",
                        "css_class": "dnd-module",
                        "html": block_data,
                        "schema_version": 2
                    },
                    "css": {},
                    "child_css": {},
                    "styles": {
                        "breakpointStyles": {
                            "default": {},
                            "mobile": {}
                        }
                    }
                }
                # Apply brand styles
                widget = apply_brand_styles_to_text_module(widget)
                widgets[widget_id] = widget

            elif block_type == 'image':
                # Create image module using HubSpot's default email image module (module_id: 1367093)
                # This is the same module that appears when you drag an image into the email editor
                widget = {
                    "id": widget_id,
                    "name": widget_id,
                    "type": "module",
                    "module_id": 1367093,
                    "order": widget_index,
                    "body": {
                        "hs_enable_module_padding": True,
                        "img": {
                            "src": block_data,
                            "alt": "Image",
                            "width": 600,
                            "height": 400
                        },
                        "style": {},
                        "hs_wrapper_css": {}
                    },
                    "css": {},
                    "child_css": {},
                    "styles": {}
                }
                # Apply brand styles
                widget = apply_brand_styles_to_image_module(widget)
                widgets[widget_id] = widget

            widget_index += 1

        # Add main content section with all modules
        # Apply brand styles to section
        section_styles = get_section_styles()
        sections.append({
            "id": "section-0",
            "columns": [{
                "id": "column-0-0",
                "width": 12,
                "widgets": main_section_widgets
            }],
            "style": section_styles
        })

        # Add footer section (separate section for footer)
        footer_widget_id = "module-footer"
        sections.append({
            "id": "section-1",
            "columns": [{
                "id": "column-1-0",
                "width": 12,
                "widgets": [footer_widget_id]
            }],
            "style": {
                "paddingTop": "0px",
                "paddingBottom": "0px"
            }
        })

        widgets[footer_widget_id] = {
            "id": footer_widget_id,
            "name": footer_widget_id,
            "type": "module",
            "order": 999,
            "body": {
                "path": "@hubspot/email_footer",
                "align": "center",
                "unsubscribe_link_type": "both",
                "schema_version": 2
            },
            "css": {},
            "child_css": {},
            "styles": {
                "breakpointStyles": {
                    "default": {},
                    "mobile": {}
                }
            }
        }

        # Build the complete payload
        update_payload = {
            "content": {
                "flexAreas": {
                    "main": {
                        "sections": sections
                    }
                },
                "widgets": widgets
            }
        }

        logger.info(f"Updating email content for ID: {email_id}")
        update_response = requests.patch(update_url, headers=headers, json=update_payload)

        logger.info(f"Update response status: {update_response.status_code}")
        if update_response.status_code != 200:
            logger.error(f"Update failed: {update_response.text[:500]}")
        else:
            logger.info(f"Email content updated successfully")

        update_response.raise_for_status()

        # Build email URL
        email_url = f"https://app.hubspot.com/email/{portal_id}/edit/{email_id}"

        return {
            "email_id": str(email_id),
            "email_url": email_url,
            "status": "draft"
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to create/update email: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logger.error(f"Response: {e.response.text}")
        raise Exception(f"Failed to create HubSpot email: {str(e)}")


def create_hubspot_email(email_name: str, subject_line: str, html_body: str, image_map: Dict[str, str] = None) -> Dict:
    """
    Create a marketing email draft in HubSpot

    Args:
        email_name: Name for the email
        subject_line: Email subject
        html_body: HTML content
        image_map: Dictionary mapping filenames to CDN URLs

    Returns:
        Dictionary with email_id, email_url, and status
    """
    if image_map is None:
        image_map = {}
    url = "https://api.hubapi.com/marketing/v3/emails"

    headers = get_hubspot_headers()

    try:
        # Step 1: Create the email
        payload = {
            "name": email_name,
            "subject": subject_line,
            "emailType": "BATCH_EMAIL"
        }

        logger.info(f"Creating email with name: {email_name}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        email_id = result.get('id')
        portal_id = result.get('portalId') or get_portal_id()

        logger.info(f"Email created with ID: {email_id}, template: {result.get('content', {}).get('templatePath')}")

        # Step 2: Update the email content using the PATCH endpoint
        # HubSpot emails use flexAreas with sections, columns, and widgets
        update_url = f"https://api.hubapi.com/marketing/v3/emails/{email_id}/draft"

        # Build sections and widgets
        sections = [
            {
                "id": "section-0",
                "columns": [
                    {
                        "id": "column-0-0",
                        "width": 12,
                        "widgets": ["module-0-0-0"]
                    }
                ],
                "style": {
                    "paddingTop": "40px",
                    "paddingBottom": "40px"
                }
            }
        ]

        widgets = {
            "module-0-0-0": {
                "id": "module-0-0-0",
                "name": "module-0-0-0",
                "type": "module",
                "module_id": 1155639,
                "order": 2,
                "body": {
                    "path": "@hubspot/rich_text",
                    "css_class": "dnd-module",
                    "html": html_body,
                    "schema_version": 2
                },
                "css": {},
                "child_css": {},
                "styles": {
                    "breakpointStyles": {
                        "default": {},
                        "mobile": {}
                    }
                }
            }
        }

        # Add image modules for each uploaded image
        image_section_index = 1
        for idx, (filename, cdn_url) in enumerate(image_map.items()):
            section_id = f"section-{image_section_index}"
            column_id = f"column-{image_section_index}-0"
            widget_id = f"module-image-{idx}"

            sections.append({
                "id": section_id,
                "columns": [
                    {
                        "id": column_id,
                        "width": 12,
                        "widgets": [widget_id]
                    }
                ],
                "style": {
                    "paddingTop": "20px",
                    "paddingBottom": "20px"
                }
            })

            widgets[widget_id] = {
                "id": widget_id,
                "name": widget_id,
                "type": "module",
                "order": 100 + idx,
                "body": {
                    "path": "@hubspot/email_linked_image",
                    "css_class": "dnd-module",
                    "img": {
                        "src": cdn_url,
                        "alt": "Image",
                        "loading": "disabled",
                        "width": 600,
                        "height": 400
                    },
                    "link": "",
                    "target": False,
                    "schema_version": 2
                },
                "css": {},
                "child_css": {},
                "styles": {
                    "breakpointStyles": {
                        "default": {},
                        "mobile": {}
                    }
                }
            }

            image_section_index += 1

        # Add footer section
        sections.append({
            "id": f"section-{image_section_index}",
            "columns": [
                {
                    "id": f"column-{image_section_index}-0",
                    "width": 12,
                    "widgets": ["module-footer"]
                }
            ],
            "style": {
                "paddingTop": "0px",
                "paddingBottom": "0px"
            }
        })

        widgets["module-footer"] = {
            "id": "module-footer",
            "name": "module-footer",
            "type": "module",
            "module_id": 2869621,
            "order": 999,
            "body": {
                "path": "@hubspot/email_footer",
                "align": "center",
                "unsubscribe_link_type": "both",
                "schema_version": 2
            },
            "css": {},
            "child_css": {},
            "styles": {
                "breakpointStyles": {
                    "default": {},
                    "mobile": {}
                }
            }
        }

        # Build the complete payload
        update_payload = {
            "content": {
                "flexAreas": {
                    "main": {
                        "sections": sections
                    }
                },
                "widgets": widgets
            }
        }

        logger.info(f"Updating email content for ID: {email_id}")

        update_response = requests.patch(update_url, headers=headers, json=update_payload)

        # Log the response even if it fails
        logger.info(f"Update response status: {update_response.status_code}")
        if update_response.status_code != 200:
            logger.error(f"Update failed: {update_response.text[:500]}")
        else:
            logger.info(f"Email content updated successfully")

        update_response.raise_for_status()

        # Build email URL
        email_url = f"https://app.hubspot.com/email/{portal_id}/edit/{email_id}"

        return {
            "email_id": str(email_id),
            "email_url": email_url,
            "status": "draft"
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to create/update email: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logger.error(f"Response: {e.response.text}")
        raise Exception(f"Failed to create HubSpot email: {str(e)}")


def replace_images_in_html(html: str, image_map: Dict[str, str]) -> str:
    """
    Replace local image references with HubSpot CDN URLs

    Args:
        html: HTML content
        image_map: Dictionary mapping original filenames to CDN URLs
    """
    # Add images at the end with proper styling
    if image_map:
        html += "\n<div style='margin-top: 20px;'>\n"
        for filename, cdn_url in image_map.items():
            html += f"<p style='text-align: center; margin: 10px 0;'><img src='{cdn_url}' alt='Image' style='max-width: 100%; height: auto;' /></p>\n"
        html += "</div>\n"

    return html


# ---------------------------------------------------------------------------
# Inline-Markdown → ordered blocks (text / image / button) and the native-module
# email-content builder. Reference shapes verified live (de-risk test 2026-06-02):
#   custom module  → body.path = "<design-manager path>" + field values
#   image          → body.path = "@hubspot/image_email" + body.img
# ---------------------------------------------------------------------------

# Button authored on its own line:  [[button: Label | https://url]]
_BUTTON_RE = re.compile(r'^\s*\[\[\s*button\s*:\s*(?P<text>.+?)\s*\|\s*(?P<url>\S+?)\s*\]\]\s*$',
                        re.IGNORECASE)
# Standalone hosted-image line:  ![alt](https://...)
_IMAGE_RE = re.compile(r'^\s*!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)\s*$')
# Standalone inline base64 data-URI image line:  ![alt](data:image/png;base64,iVBOR...)
# Lets Claude.ai attach generated images / pasted screenshots directly; uploaded to
# HubSpot in create_email_draft AFTER parsing (the parser stays network-free).
_DATA_IMAGE_RE = re.compile(
    r'^\s*!\[(?P<alt>[^\]]*)\]\(\s*(?P<uri>data:image/[^;,\s]+;base64,[A-Za-z0-9+/=\s]+?)\s*\)\s*$'
)

# Map an image MIME type to a sensible file extension for the HubSpot upload filename.
_DATA_IMAGE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
}

# A drafter writes the literal token NAME (uppercase) for the recipient's first name;
# it becomes a HubSpot personalization token with a safe fallback. Rendered inside rich_text.
_PERSONALIZATION_TOKEN = "{{ personalization_token('contact.firstname', 'there') }}"


def _apply_personalization(html: str) -> str:
    """Replace the standalone uppercase placeholder NAME with a first-name personalization token."""
    return re.sub(r'\bNAME\b', _PERSONALIZATION_TOKEN, html)


def parse_inline_markdown_to_blocks(body_markdown: str) -> List[Tuple]:
    """
    Split inline Markdown into ordered content blocks for native-module emission:
      ('text', html)              -> @hubspot/rich_text
      ('image', {'src','alt'})    -> @hubspot/image_email
      ('button', {'text','url'})  -> custom button module

    Standalone-line images and [[button: ...]] markers become their own native modules;
    everything else accumulates and renders to HTML as rich-text blocks. Two image forms
    are recognised on their own line: a hosted `https://` URL (used as-is) and an inline
    base64 data URI `data:image/...;base64,...` (kept verbatim in `src` here — the actual
    HubSpot upload happens in create_email_draft so this parser stays network-free).
    Other non-https images are left inside the text (remote mode cannot host local files).
    """
    blocks: List[Tuple] = []
    text_buf: List[str] = []

    def flush_text():
        if text_buf:
            md = "\n".join(text_buf).strip()
            if md:
                blocks.append(('text', _apply_personalization(markdown.markdown(md))))
            text_buf.clear()

    for line in body_markdown.splitlines():
        mbtn = _BUTTON_RE.match(line)
        mdata = _DATA_IMAGE_RE.match(line)
        mimg = _IMAGE_RE.match(line)
        if mbtn:
            flush_text()
            blocks.append(('button', {'text': mbtn.group('text'), 'url': mbtn.group('url')}))
        elif mdata:
            # Inline base64 image. Strip internal whitespace the renderer may have wrapped in;
            # the data URI is decoded + uploaded later in create_email_draft.
            flush_text()
            uri = re.sub(r'\s+', '', mdata.group('uri'))
            blocks.append(('image', {'src': uri, 'alt': mdata.group('alt') or 'Image'}))
        elif mimg:
            flush_text()
            blocks.append(('image', {'src': mimg.group('url'), 'alt': mimg.group('alt') or 'Image'}))
        else:
            text_buf.append(line)
    flush_text()
    if not blocks:
        blocks.append(('text', _apply_personalization(markdown.markdown(body_markdown))))
    return blocks


_DATA_URI_RE = re.compile(r'^data:(?P<ct>image/[^;,\s]+);base64,(?P<b64>.+)$', re.DOTALL)


def resolve_data_uri_images(blocks: List[Tuple]) -> List[Tuple]:
    """
    Upload any inline base64 data-URI image blocks to HubSpot and swap in the CDN URL.

    Iterates the ordered blocks from parse_inline_markdown_to_blocks. For each image block
    whose `src` is a `data:image/...;base64,...` URI, decode the base64, derive a filename
    (content-type extension + a short content hash) and upload via upload_image_to_hubspot.
    On success the block's `src` is replaced with the returned CDN URL; on failure (bad
    base64, unsupported type, or a None upload result) the image block is DROPPED with a
    warning so a `data:` URI never reaches HubSpot and the draft still succeeds.

    https:// image blocks and all non-image blocks pass through unchanged. This is the only
    network-touching step in the inline pipeline; the parser itself stays pure.
    """
    resolved: List[Tuple] = []
    for block in blocks:
        if block[0] != 'image':
            resolved.append(block)
            continue

        data = block[1]
        src = data.get('src', '')
        if not src.startswith('data:'):
            resolved.append(block)  # already-hosted https URL — pass through untouched
            continue

        m = _DATA_URI_RE.match(src)
        if not m:
            logger.warning("Dropping inline image: malformed data URI (not base64 image/*).")
            continue
        content_type = m.group('ct').lower()
        try:
            image_bytes = base64.b64decode(m.group('b64'), validate=True)
        except Exception as e:
            logger.warning(f"Dropping inline image: base64 decode failed ({e}).")
            continue
        if not image_bytes:
            logger.warning("Dropping inline image: decoded to zero bytes.")
            continue

        ext = _DATA_IMAGE_EXT.get(content_type, 'png')
        img_hash = hashlib.md5(image_bytes).hexdigest()[:8]
        filename = f"inline_{img_hash}.{ext}"

        cdn_url = upload_image_to_hubspot(image_bytes, filename)
        if not cdn_url:
            logger.warning(f"Dropping inline image {filename}: HubSpot upload returned no URL.")
            continue

        logger.info(f"Inline image uploaded: {filename} ({len(image_bytes)} bytes) -> {cdn_url}")
        resolved.append(('image', {'src': cdn_url, 'alt': data.get('alt', 'Image')}))
    return resolved


def build_native_email_content(email_name: str, subject_line: str, blocks: List[Tuple],
                               business_unit_id: Optional[str] = None,
                               office_location_id: Optional[str] = None,
                               button_color: Optional[str] = None,
                               footer_html: Optional[str] = None,
                               footer_bg: Optional[str] = None) -> Dict:
    """
    Create a marketing-email draft using the SAME native modules Edanz's production emails use:
      text   → @hubspot/rich_text
      image  → @hubspot/image_email
      button → native button module 1976948, styled with the brand colour (button_color)
      footer → @hubspot/email_footer; when footer_html is given, display=custom with branded
               HTML in a dark (footer_bg) section; otherwise the default account CAN-SPAM footer.
    Content lives in one section (Gmail 102KB-clip avoidance); the footer is its own section.
    Returns {email_id, email_url, status}.
    """
    headers = get_hubspot_headers()

    payload = {"name": email_name, "subject": subject_line, "emailType": "BATCH_EMAIL"}
    if business_unit_id:
        payload["businessUnitId"] = business_unit_id
    if office_location_id:
        payload["subscriptionDetails"] = {"officeLocationId": office_location_id}
    r = requests.post("https://api.hubapi.com/marketing/v3/emails", headers=headers, json=payload)
    r.raise_for_status()
    result = r.json()
    email_id = result.get("id")
    portal_id = result.get("portalId") or get_portal_id()

    btn_color = button_color or "#0B5394"

    # Content widgets (text / image / button) — one section.
    widgets: Dict[str, Dict] = {}
    content_ids: List[str] = []
    for i, (kind, data) in enumerate(blocks):
        wid = f"module-{i}"
        widget = {"id": wid, "name": wid, "type": "module", "order": i,
                  "css": {}, "child_css": {}, "styles": {}}
        if kind == 'text':
            widget["body"] = {"path": "@hubspot/rich_text", "css_class": "dnd-module",
                              "html": data, "schema_version": 2}
        elif kind == 'image':
            widget["body"] = {"path": "@hubspot/image_email",
                              "img": {"src": data["src"], "alt": data.get("alt", "Image")},
                              "schema_version": 2}
        elif kind == 'button':
            # HubSpot's native email button module (the same one Edanz production emails use),
            # styled with the brand colour. Referenced by module_id, no path.
            widget["module_id"] = 1976948
            widget["body"] = {"module_id": 1976948, "text": data["text"], "destination": data["url"],
                              "font_color": "#ffffff", "corner_radius": 6,
                              "style": {"background_color": {"color": btn_color, "opacity": 100}},
                              "schema_version": 2}
        else:
            continue
        content_ids.append(wid)
        widgets[wid] = widget

    # Footer: branded custom HTML in a dark section when provided, else the default footer.
    if footer_html:
        widgets["module-footer"] = {
            "id": "module-footer", "name": "module-footer", "type": "module", "order": 999,
            "body": {"path": "@hubspot/email_footer", "display": "custom", "footer_html": footer_html,
                     "align": "center", "unsubscribe_link_type": "both",
                     "font": {"color": "#ffffff", "font": "Arial, sans-serif",
                              "size": {"units": "px", "value": 12}},
                     "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {}}
        footer_style = {"backgroundColor": footer_bg or "#333333", "paddingTop": "24px", "paddingBottom": "24px"}
    else:
        widgets["module-footer"] = {
            "id": "module-footer", "name": "module-footer", "type": "module", "order": 999,
            "body": {"path": "@hubspot/email_footer", "align": "center",
                     "unsubscribe_link_type": "both", "schema_version": 2},
            "css": {}, "child_css": {}, "styles": {}}
        footer_style = {"paddingTop": "0px", "paddingBottom": "0px"}

    sections = [
        {"id": "section-content",
         "columns": [{"id": "col-content", "width": 12, "widgets": content_ids}],
         "style": {"paddingTop": "20px", "paddingBottom": "20px"}},
        {"id": "section-footer",
         "columns": [{"id": "col-footer", "width": 12, "widgets": ["module-footer"]}],
         "style": footer_style},
    ]

    patch = requests.patch(
        f"https://api.hubapi.com/marketing/v3/emails/{email_id}/draft",
        headers=headers,
        json={"content": {"flexAreas": {"main": {"sections": sections}}, "widgets": widgets}},
    )
    if patch.status_code != 200:
        logger.error(f"Draft content update failed: {patch.text[:500]}")
    patch.raise_for_status()

    return {"email_id": str(email_id),
            "email_url": f"https://app.hubspot.com/email/{portal_id}/edit/{email_id}",
            "status": "draft"}


@mcp.tool()
def create_email_draft(
    subject: str,
    body_markdown: str,
    brand: Optional[str] = None,
    email_name: Optional[str] = None,
    preheader: Optional[str] = None,
) -> Dict:
    """
    Create a HubSpot marketing email DRAFT from inline Markdown content.

    This is the remote / Claude.ai-web entry point: content is passed directly as a string
    (no local-file access, unlike create_marketing_email). Sending is never exposed — the
    result is always a draft for human review in HubSpot.

    Args:
        subject: Email subject line.
        body_markdown: Email body as Markdown, rendered into NATIVE HubSpot modules:
            text → rich_text; a standalone image line → @hubspot/image_email;
            a standalone `[[button: Label | https://url]]` line → the branded button module.
            Images on their own line may be EITHER an already-hosted `![alt](https://…)` URL
            (used as-is) OR an inline base64 data URI `![alt](data:image/png;base64,…)` — the
            latter (e.g. a generated image or pasted screenshot) is uploaded to HubSpot
            automatically and its CDN URL substituted. Local file paths are not supported in
            remote mode; an inline image that fails to upload is dropped from the draft.
        brand: Optional brand key (e.g. 'edanz') mapped to a HubSpot Business Unit via
            config['brands']. Omit for the default / single-BU portal.
        email_name: Optional internal name shown in the HubSpot dashboard. Defaults to subject.
        preheader: Optional preview text. NOT YET WIRED to the API (see TODO) — accepted now
            for a forward-compatible signature and recorded in the audit log.

    Returns:
        Dict with email_id, email_url, status ('draft'), and brand.
    """
    brand_cfg = (config.get("brands", {}).get(brand) or {}) if brand else {}
    business_unit_id = resolve_business_unit(brand)
    office_location_id = brand_cfg.get("office_location_id")
    button_color = brand_cfg.get("button_color")
    footer_html = brand_cfg.get("footer_html")
    footer_bg = brand_cfg.get("footer_bg")
    name = email_name or subject

    # Parse into ordered native-module blocks (text / image / button).
    blocks = parse_inline_markdown_to_blocks(body_markdown)
    # Upload any inline base64 data-URI images to HubSpot and swap in their CDN URLs
    # (https:// images pass through). Done AFTER parsing, BEFORE building content, so the
    # parser stays network-free. Failed uploads are dropped, not emitted as data: URIs.
    blocks = resolve_data_uri_images(blocks)
    counts: Dict[str, int] = {}
    for _kind, _ in blocks:
        counts[_kind] = counts.get(_kind, 0) + 1

    logger.info(f"Creating draft: name={name!r} brand={brand!r} bu={business_unit_id!r} blocks={counts}")
    try:
        result = build_native_email_content(name, subject, blocks, business_unit_id=business_unit_id,
                                            office_location_id=office_location_id, button_color=button_color,
                                            footer_html=footer_html, footer_bg=footer_bg)
    except Exception as e:
        audit_log({"event": "create_email_draft", "status": "error", "brand": brand,
                   "email_name": name, "subject": subject, "error": str(e)})
        raise

    result["brand"] = brand
    # TODO(preheader): set preview text once the correct HubSpot email field is confirmed.
    audit_log({
        "event": "create_email_draft",
        "status": result.get("status", "unknown"),
        "brand": brand,
        "business_unit_id": business_unit_id,
        "email_id": result.get("email_id"),
        "email_name": name,
        "subject": subject,
        "blocks": counts,
        "preheader": preheader,
    })
    logger.info(f"Draft created: {result.get('email_url')}")
    return result


@mcp.tool()
def create_marketing_email(doc_path: str, email_name: str, subject_line: str) -> Dict:
    """
    Create a HubSpot marketing email draft from a document file

    Args:
        doc_path: Absolute or relative path to document file (.md or .docx)
        email_name: Name for the email in HubSpot dashboard
        subject_line: Email subject line

    Returns:
        Dictionary with email_id, email_url, status, and images_uploaded count
    """
    # Convert to absolute path and validate file exists
    doc_path = os.path.abspath(os.path.expanduser(doc_path))
    logger.info(f"Attempting to read document: {doc_path}")
    logger.info(f"Current working directory: {os.getcwd()}")

    if not os.path.exists(doc_path):
        # Try to provide helpful error message with current directory
        cwd = os.getcwd()
        # List files in current directory for debugging
        try:
            files = os.listdir(cwd)
            logger.error(f"Files in {cwd}: {files}")
        except Exception as e:
            logger.error(f"Could not list directory: {e}")
        raise FileNotFoundError(f"Document not found: {doc_path}\nCurrent directory: {cwd}\nPlease provide an absolute path to the file on your local machine.")

    # Determine file type and parse
    file_ext = Path(doc_path).suffix.lower()

    if file_ext == '.md':
        logger.info(f"Parsing markdown file: {doc_path}")
        html, images = parse_markdown(doc_path)
        content_blocks = [('text', html)] + [('image', img[1], img[2]) for img in images]
    elif file_ext == '.docx':
        logger.info(f"Parsing DOCX file: {doc_path}")
        content_blocks = parse_docx_blocks(doc_path)
    else:
        raise ValueError(f"Unsupported file format: {file_ext}. Only .md and .docx are supported.")

    # Upload images and create content blocks with CDN URLs
    processed_blocks = []
    images_uploaded = 0

    for block in content_blocks:
        if block[0] == 'text':
            processed_blocks.append(('text', block[1]))
        elif block[0] == 'image':
            image_bytes, filename = block[1], block[2]
            cdn_url = upload_image_to_hubspot(image_bytes, filename)
            if cdn_url:
                processed_blocks.append(('image', cdn_url))
                images_uploaded += 1

    # Create email in HubSpot with interleaved content blocks
    logger.info(f"Creating HubSpot email: {email_name}")
    result = create_hubspot_email_from_blocks(email_name, subject_line, processed_blocks)

    # Add images_uploaded to result
    result['images_uploaded'] = images_uploaded

    logger.info(f"Successfully created email: {result['email_url']}")
    return result


# ---------------------------------------------------------------------------
# READ-ONLY review tools. These let Claude fetch and critique existing emails
# (drafts + sent) across the WHOLE portal, including emails authored by other
# people and in any Business Unit. They never create, update, or delete.
#
# Token efficiency is the governing constraint: the raw HubSpot email payload
# embeds full content/widgets/styleSettings (tens of KB per email). list_emails
# strips every email down to lightweight metadata; get_email extracts the body
# into readable ordered content (text/images/buttons) rather than dumping the
# raw module tree. Reference shapes verified live (2026-06-18):
#   list/get → top-level state, type, createdById, publishedById/Name,
#              primaryEmailCampaignId, businessUnitId, createdAt, updatedAt
#   content  → content.flexAreas.main + content.widgets{} (a dict keyed by
#              widget id; document order comes from each widget's `order` field)
#   widgets  → rich_text  body.html
#              image       body.img {src, alt}        (module_id 1367093 / @hubspot/image_email)
#              button       body.text + body.link_to  (module_id 1976948)
#              preview text body.value                (id "preview_text")
#   stats    → GET /marketing/v3/emails/{id}?includeStats=true → top-level `stats`
#              {counters{...}, ratios{...}, deviceBreakdown{...}}
# ---------------------------------------------------------------------------

_EMAILS_BASE = "https://api.hubapi.com/marketing/v3/emails"

# Lightweight metadata fields kept by list_emails. Everything else (notably
# content/widgets/styleSettings) is dropped so the model never sees the heavy tree.
_LIGHT_EMAIL_FIELDS = (
    "id", "name", "subject", "state", "type", "createdAt", "updatedAt",
    "publishedAt", "createdById", "updatedById", "publishedById",
    "publishedByName", "primaryEmailCampaignId", "businessUnitId",
    "isPublished", "archived",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")


def _html_to_text(html: str) -> str:
    """Collapse rich_text HTML to legible plain text, preserving link URLs and structure.

    Keeps anchor targets inline as `text (url)`, turns <br>/<p>/<li>/<h*> boundaries into
    line breaks, strips the remaining tags, unescapes entities, and trims runaway whitespace.
    This is for human review, not round-tripping, so it errs toward readability.
    """
    if not html:
        return ""
    import html as _htmllib

    s = html
    # Surface anchor URLs: <a href="X">label</a> -> label (X)
    s = re.sub(
        r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"{_TAG_RE.sub('', m.group(2)).strip()} ({m.group(1)})",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Block/line boundaries -> newlines.
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</\s*(p|div|li|tr|h[1-6]|ul|ol|table)\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*li\b[^>]*>", "- ", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = _htmllib.unescape(s)
    s = _WS_RE.sub(" ", s)
    # Collapse 3+ blank lines, trim each line.
    lines = [ln.strip() for ln in s.splitlines()]
    out: List[str] = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def _strip_to_light(email: Dict) -> Dict:
    """Project a raw email object down to the lightweight metadata fields only."""
    light = {k: email.get(k) for k in _LIGHT_EMAIL_FIELDS if email.get(k) is not None}
    # Author convenience: prefer a human name, fall back to the numeric id.
    light["author"] = email.get("publishedByName") or email.get("createdById")
    return light


def _extract_readable_content(content: Dict) -> Dict:
    """Pull the readable body out of an email `content` object in document order.

    Walks content.widgets{} ordered by each widget's `order` field (the section/column
    widget id lists are unreliable legacy refs, so we order by `order`). For each widget
    we emit a compact block: rich_text -> {type:text, text}; image -> {type:image, src, alt};
    button -> {type:button, text, url}; preview text is hoisted to `preheader`. The footer
    and unrecognised modules are summarised, not dumped. Returns {preheader, blocks}.
    """
    widgets = (content or {}).get("widgets", {}) or {}
    preheader = ""
    items: List[Tuple] = []  # (order, block)
    for wid, w in widgets.items():
        body = w.get("body", {}) or {}
        order = w.get("order")
        if order is None:
            order = body.get("order", 9999)
        path = body.get("path") or ""
        module_id = body.get("module_id") or w.get("module_id")

        if wid == "preview_text" or "value" in body and path == "" and module_id is None:
            preheader = (body.get("value") or "").strip() or preheader
            continue
        if path == "@hubspot/email_footer" or "footer" in str(wid).lower():
            items.append((order, {"type": "footer", "note": "standard unsubscribe / CAN-SPAM footer"}))
            continue
        if "html" in body:
            text = _html_to_text(body.get("html", ""))
            if text:
                items.append((order, {"type": "text", "text": text}))
            continue
        if "img" in body and isinstance(body["img"], dict):
            img = body["img"]
            items.append((order, {"type": "image", "src": img.get("src", ""), "alt": img.get("alt", "")}))
            continue
        if module_id == 1976948 or ("text" in body and "link_to" in body):
            # Native button module. `link_to` is often the literal field token "url"; the
            # actual destination lives in body['url'] when set. Surface whichever looks like a URL.
            url = body.get("url") or body.get("destination") or ""
            link_to = body.get("link_to")
            if isinstance(link_to, str) and link_to.startswith("http"):
                url = link_to
            items.append((order, {"type": "button", "text": body.get("text", ""), "url": url}))
            continue
        # Unknown module: note it without dumping the raw tree.
        items.append((order, {"type": "module", "note": path or (str(module_id) if module_id else "unknown")}))

    items.sort(key=lambda t: (t[0] if isinstance(t[0], (int, float)) else 9999))
    return {"preheader": preheader, "blocks": [b for _, b in items]}


@mcp.tool()
def list_emails(
    limit: int = 20,
    state: Optional[str] = None,
    brand: Optional[str] = None,
    search: Optional[str] = None,
    after: Optional[str] = None,
) -> Dict:
    """
    READ-ONLY. List existing HubSpot marketing emails as lightweight metadata.

    Spans the WHOLE Edanz portal across ALL authors and ALL Business Units (brands), including
    drafts and sent emails authored by other people. Returns only compact metadata per email
    (id, name, subject, state, type, dates, author, campaign id, businessUnitId) — the heavy
    content / widgets / styleSettings are stripped, so this is safe to call on large result sets.
    Use get_email(id) to read one email's actual body.

    Args:
        limit: Max emails to return (default 20, capped at 100).
        state: Optional case-insensitive state filter, e.g. DRAFT, PUBLISHED, AUTOMATED
            (the HubSpot list API has no state query param, so this is applied client-side).
        brand: Optional brand key (e.g. 'edanz') mapped to a Business Unit via config['brands'];
            only emails in that Business Unit are returned.
        search: Optional case-insensitive substring matched client-side against name and subject.
        after: Optional pagination cursor from a previous call's `next_after`.

    Returns:
        Dict {total, count, emails: [lightweight metadata...], next_after}. `next_after` is the
        cursor for the next page (None when exhausted). Note: client-side filters (state/brand/
        search) are applied to each fetched page, so a filtered page may return fewer than `limit`
        rows; follow `next_after` to continue.
    """
    limit = max(1, min(int(limit), 100))
    bu_id = resolve_business_unit(brand) if brand else None
    state_norm = state.strip().upper() if state else None
    search_norm = search.strip().lower() if search else None

    params: Dict[str, object] = {"limit": limit}
    if after:
        params["after"] = after

    r = requests.get(_EMAILS_BASE, headers=get_hubspot_headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    rows: List[Dict] = []
    for e in data.get("results", []):
        if state_norm and (e.get("state") or "").upper() != state_norm:
            continue
        if bu_id and str(e.get("businessUnitId") or "") != str(bu_id):
            continue
        if search_norm:
            hay = f"{e.get('name','')} {e.get('subject','')}".lower()
            if search_norm not in hay:
                continue
        rows.append(_strip_to_light(e))

    next_after = (data.get("paging", {}) or {}).get("next", {}).get("after")
    return {
        "total": data.get("total"),
        "count": len(rows),
        "emails": rows,
        "next_after": next_after,
    }


@mcp.tool()
def get_email(email_id: str, raw: bool = False) -> Dict:
    """
    READ-ONLY. Fetch ONE existing HubSpot marketing email. Works for any email in the
    portal regardless of author or Business Unit.

    Default (raw=False) — use this for normal copy / content review. Returns metadata
    (name, subject, preheader, state, type, author, dates, businessUnitId, campaign id) plus the
    body extracted into ordered, human-readable blocks — rich-text rendered to plain text (links
    preserved as `label (url)`), images as {src, alt}, buttons as {text, url} — rather than the
    raw widget JSON. This is the compact, token-efficient view.

    raw=True — opt-in structural source for CLONING, REVERSE-ENGINEERING, or AUDITING a
    template's layout. Returns the metadata header plus the full raw structural payload:
    `flexAreas` (the section/column/widget grid), `widgets` (raw widget definitions including
    their HTML bodies), `styleSettings`, and `templatePath` (+ template mode when present).
    This is the complete layout source you need to rebuild an email faithfully. It is MUCH
    larger than the default (tens of KB), so only request it when you actually need the
    structure, not for reading copy.

    Either way, for a DRAFT the latest in-progress buffer is read from the /draft endpoint;
    for any other state the published/base content is used, so raw reflects the same source
    as the readable view.

    Args:
        email_id: The HubSpot email id (from list_emails).
        raw: When True, return the full structural source (HTML, styleSettings, grid, template
            path) for cloning / reverse-engineering / auditing a layout instead of the readable
            blocks. Default False (compact readable view). Read-only either way.

    Returns:
        raw=False: Dict with metadata fields plus `content` = {preheader, blocks: [...]}.
        raw=True: Dict with metadata fields plus `content` = {flexAreas, widgets, styleSettings,
            templatePath, templateMode?, emailTemplateMode?}.
    """
    headers = get_hubspot_headers()
    base = requests.get(f"{_EMAILS_BASE}/{email_id}", headers=headers, timeout=30)
    base.raise_for_status()
    email = base.json()

    content = email.get("content", {}) or {}
    # For drafts, the live buffer lives at /draft; the base object returns published content.
    if (email.get("state") or "").upper() == "DRAFT":
        try:
            d = requests.get(f"{_EMAILS_BASE}/{email_id}/draft", headers=headers, timeout=30)
            if d.status_code == 200:
                dc = d.json().get("content")
                if dc:
                    content = dc
        except requests.exceptions.RequestException as exc:
            logger.warning(f"get_email: draft fetch failed for {email_id}, using base content: {exc}")

    result = _strip_to_light(email)
    if raw:
        # Full structural view: the grid, raw widget defs (incl. HTML bodies), styles, and
        # template path needed to clone / reverse-engineer the layout. Deliberately heavy.
        structural: Dict = {
            "flexAreas": content.get("flexAreas", {}) or {},
            "widgets": content.get("widgets", {}) or {},
            "styleSettings": content.get("styleSettings", {}) or {},
            "templatePath": content.get("templatePath"),
        }
        for k in ("templateMode", "emailTemplateMode"):
            if content.get(k) is not None:
                structural[k] = content.get(k)
        result["content"] = structural
    else:
        result["content"] = _extract_readable_content(content)
    return result


@mcp.tool()
def get_email_stats(email_id: str) -> Dict:
    """
    READ-ONLY. Performance statistics for a SENT marketing email.

    Spans the whole portal (any author / Business Unit). Reads HubSpot's own per-email stats via
    GET /marketing/v3/emails/{id}?includeStats=true (the same numbers shown on the email's
    Performance page). Returns the headline counters (sent, delivered, open, click, bounce,
    unsubscribed, spamreport, etc.), the computed ratios (open rate, click-through rate, etc.),
    and the device breakdown. For a DRAFT or never-sent email the counters will be zero / empty.

    Args:
        email_id: The HubSpot email id (from list_emails).

    Returns:
        Dict {id, name, subject, state, stats: {counters, ratios, deviceBreakdown}}.
    """
    r = requests.get(
        f"{_EMAILS_BASE}/{email_id}",
        headers=get_hubspot_headers(),
        params={"includeStats": "true"},
        timeout=30,
    )
    r.raise_for_status()
    email = r.json()
    stats = email.get("stats", {}) or {}
    return {
        "id": email.get("id"),
        "name": email.get("name"),
        "subject": email.get("subject"),
        "state": email.get("state"),
        "stats": {
            "counters": stats.get("counters", {}),
            "ratios": stats.get("ratios", {}),
            "deviceBreakdown": stats.get("deviceBreakdown", {}),
        },
    }


# --------------------------------------------------------------------------------------
# Phase 2: clone-and-fill template tooling.
#
# A curated template email is cloned (POST .../clone, which preserves widget ids) and its
# content-bearing widgets are filled from a slot manifest, leaving all brand furniture
# intact. Writes hit the DRAFT buffer ONLY (PATCH .../{id}/draft); nothing is published or
# sent. Architecture is settled and proven live (see PHASE2-CLONE-AND-FILL doc, Appendix A).
# --------------------------------------------------------------------------------------

# Widget body `path` -> slot kind. Only these three widget kinds are fillable.
_EDITABLE_WIDGET_KINDS = {
    "@hubspot/rich_text": "text",
    "@hubspot/button_email": "button",
    "@hubspot/image_email": "image",
}


def _infer_widget_kind(body: Dict) -> Optional[str]:
    """Infer a fillable widget's slot kind from its body.

    Primary signal is the widget `path` (@hubspot/rich_text|button_email|image_email).
    Some templates (e.g. the Capability primary-CTA buttons) carry no `path`: their body
    has the button shape (`text` + `destination`) directly. Treat that as a button too so
    the manifest walker does not miss path-less CTAs.

    Returns the slot kind ('text' | 'button' | 'image') or None if the widget is not fillable.
    """
    body = body or {}
    kind = _EDITABLE_WIDGET_KINDS.get(body.get("path"))
    if kind:
        return kind
    # Path-less button: a body that carries both the label and the link is a button.
    if body.get("text") is not None and body.get("destination") is not None:
        return "button"
    return None

# Manifests live in the repo, versioned with the server, one JSON file per template.
# Resolution is layout-robust: an explicit env override wins; otherwise we probe the
# repo-root `templates/` relative to the package (local/editable dev layout) and the
# current working directory (the Railway image runs from /app where the Dockerfile copies
# templates/ to /app/templates). First existing candidate wins; the package-relative path
# is the default when none exist yet.
def _resolve_templates_dir() -> Path:
    env = os.environ.get("HUBSPOT_EMAIL_MCP_TEMPLATES_DIR", "").strip()
    if env:
        return Path(env)
    pkg_relative = Path(__file__).resolve().parent.parent.parent / "templates"
    candidates = [pkg_relative, Path.cwd() / "templates"]
    for c in candidates:
        if c.is_dir():
            return c
    return pkg_relative


def _widget_breakpoint_role(widget: Dict) -> str:
    """Classify a widget's responsive role from styles.breakpointStyles.

    `default` visible + `mobile` hidden  -> 'desktop' twin
    `default` hidden  + `mobile` visible -> 'mobile' twin
    neither hidden (or no breakpoint styles) -> 'shared'
    """
    bp = (widget.get("styles", {}) or {}).get("breakpointStyles", {}) or {}
    default_hidden = bool((bp.get("default") or {}).get("hidden"))
    mobile_hidden = bool((bp.get("mobile") or {}).get("hidden"))
    if not default_hidden and mobile_hidden:
        return "desktop"
    if default_hidden and not mobile_hidden:
        return "mobile"
    return "shared"


def _widget_preview(kind: str, body: Dict, limit: int = 60) -> str:
    """A short, tag-stripped content preview for manifest curation."""
    if kind == "text":
        raw = body.get("html", "") or ""
        text = _TAG_RE.sub("", raw)
        import html as _htmllib
        text = _htmllib.unescape(text)
        text = _WS_RE.sub(" ", text).strip()
    elif kind == "button":
        text = (body.get("text") or "").strip()
    elif kind == "image":
        img = body.get("img") or {}
        text = (img.get("alt") or "").strip()
    else:
        text = ""
    return text[:limit]


@mcp.tool()
def get_template_manifest(email_id: str) -> Dict:
    """
    READ-ONLY curation aid. Inspect a template email and emit a DRAFT slot manifest a human
    curates once per template (then saves to templates/<name>.json for clone_email + fill_email_draft).

    Walks the email's raw widget tree and reports every fillable widget (rich_text / button_email /
    image_email) with: id, kind (text/button/image), order, breakpoint role (desktop / mobile / shared,
    read from styles.breakpointStyles.default.hidden vs mobile.hidden), and a ~60-char content preview.

    Then SUGGESTS desktop/mobile slot pairings: widgets of the same kind whose previews match and whose
    breakpoint roles are complementary (one desktop, one mobile) are almost certainly the same logical
    slot. Each suggested slot carries {kind, desktop_id, mobile_id}. This is a heuristic curation aid,
    NOT an authority: the human renames slots and confirms pairings before saving the manifest.

    Args:
        email_id: The HubSpot email id of the template (e.g. a curated TEMPLATE - ... email).

    Returns:
        Dict {template_id, name, editable_widgets: [{id, kind, order, breakpoint, preview}],
              suggested_slots: {slot_N: {kind, desktop_id, mobile_id}}, unpaired: [ids]}.
    """
    email = get_email(email_id, raw=True)
    widgets = (email.get("content", {}) or {}).get("widgets", {}) or {}

    editable: List[Dict] = []
    for wid, w in widgets.items():
        body = w.get("body", {}) or {}
        kind = _infer_widget_kind(body)
        if not kind:
            continue
        editable.append({
            "id": wid,
            "kind": kind,
            "order": w.get("order"),
            "breakpoint": _widget_breakpoint_role(w),
            "preview": _widget_preview(kind, body),
        })
    editable.sort(key=lambda d: (d["order"] if isinstance(d["order"], (int, float)) else 9999))

    # Suggest pairings: same kind + matching preview + complementary breakpoint roles.
    desktops = [e for e in editable if e["breakpoint"] == "desktop"]
    mobiles = [e for e in editable if e["breakpoint"] == "mobile"]
    used_mobile_ids: set = set()
    suggested: Dict[str, Dict] = {}
    paired_ids: set = set()
    slot_n = 0
    for d in desktops:
        match = None
        # Exact preview match first, then same-kind fallback by order proximity.
        for m in mobiles:
            if m["id"] in used_mobile_ids:
                continue
            if m["kind"] == d["kind"] and m["preview"] == d["preview"]:
                match = m
                break
        if match is None:
            for m in mobiles:
                if m["id"] in used_mobile_ids:
                    continue
                if m["kind"] == d["kind"]:
                    match = m
                    break
        if match is not None:
            used_mobile_ids.add(match["id"])
            slot_n += 1
            suggested[f"slot_{slot_n}"] = {
                "kind": d["kind"],
                "desktop_id": d["id"],
                "mobile_id": match["id"],
                "preview": d["preview"],
            }
            paired_ids.add(d["id"])
            paired_ids.add(match["id"])

    unpaired = [e["id"] for e in editable if e["id"] not in paired_ids]
    return {
        "template_id": str(email_id),
        "name": email.get("name"),
        "editable_widgets": editable,
        "suggested_slots": suggested,
        "unpaired": unpaired,
    }


@mcp.tool()
def clone_email(template_id: str, new_name: str, language: Optional[str] = None) -> Dict:
    """
    Clone a curated template email into a NEW DRAFT, preserving its layout and widget ids.

    POSTs to .../emails/clone (EmailCloneRequestVNext). The clone is always a DRAFT in the same
    Business Unit; nothing is published or sent. Because the clone preserves the template's widget
    ids, a slot manifest built once for the template applies unchanged to every clone. Follow this
    with fill_email_draft to populate the clone's content slots.

    Args:
        template_id: The HubSpot email id of the template to clone.
        new_name: Internal name for the new draft (shown in the HubSpot dashboard).
        language: Optional language code for the clone (passed through to HubSpot).

    Returns:
        Dict {id, edit_url}.
    """
    body: Dict = {"id": str(template_id), "cloneName": new_name}
    if language:
        body["language"] = language
    try:
        r = requests.post(f"{_EMAILS_BASE}/clone", headers=get_hubspot_headers(), json=body, timeout=30)
        r.raise_for_status()
    except Exception as e:
        audit_log({"event": "clone_email", "status": "error", "template_id": str(template_id),
                   "new_name": new_name, "error": str(e)})
        raise
    new_id = str(r.json().get("id"))
    edit_url = f"https://app.hubspot.com/email/{get_portal_id()}/edit/{new_id}"
    audit_log({"event": "clone_email", "status": "ok", "template_id": str(template_id),
               "new_id": new_id, "new_name": new_name, "language": language})
    return {"id": new_id, "edit_url": edit_url}


def _render_text_slot(value: str) -> str:
    """Render a text-slot value to rich_text HTML using the SAME path create_email_draft uses.

    Runs the value through parse_inline_markdown_to_blocks + resolve_data_uri_images (so NAME ->
    first-name token, inline markdown -> HTML, and any inline data-URI image is hosted), then
    concatenates the resulting text/image block HTML. No second renderer is introduced.
    """
    blocks = resolve_data_uri_images(parse_inline_markdown_to_blocks(value))
    parts: List[str] = []
    for kind, data in blocks:
        if kind == "text":
            parts.append(data)
        elif kind == "image":
            alt = (data.get("alt") or "Image").replace('"', "&quot;")
            parts.append(f'<p><img src="{data["src"]}" alt="{alt}"></p>')
        elif kind == "button":
            # A button marker inside a text slot degrades to a plain link; buttons belong in
            # button slots. Keep it visible rather than dropping content.
            parts.append(f'<p><a href="{data["url"]}">{data["text"]}</a></p>')
    return "".join(parts)


def _render_wrapped_value(value: str) -> str:
    """Render a single user-supplied text value for substitution into a wrapper placeholder.

    Wrapped slots (e.g. credential cards) hold plain title/body text inside a fixed HTML
    scaffold that already carries its own markup, so we must NOT run the value through the
    markdown -> <p> renderer (that would fight the wrapper's tags). Instead:
      1. HTML-escape the value (<, >, &) so caller text cannot break the wrapper markup,
      2. apply the NAME -> first-name personalization-token convenience (same as _render_text_slot).
    The personalization token emitted by step 2 contains no <, >, & so escaping it first then
    substituting is safe; a literal `{{ personalization_token(...) }}` the caller types survives
    because escape() leaves `{`, `}`, `(`, `)` untouched.
    """
    escaped = _html.escape(value if isinstance(value, str) else str(value), quote=False)
    return _apply_personalization(escaped)


def _render_wrapped_slot(wrapper_html: str, value, content_kind: Optional[str]) -> str:
    """Substitute a slot value into a per-breakpoint wrapper scaffold's {{field}} placeholders.

    The wrapper is fixed furniture (the marker cell + layout) authored in the manifest; its
    {{...}} placeholders are ours, never user input, so they are NOT escaped. Each user value
    is escaped + personalization-tokenised via _render_wrapped_value before substitution.

    Value shape follows content_kind:
      - 'title_body' (or a dict value): {title, body} -> {{title}}, {{body}}. Missing keys
        substitute empty. Any other dict keys map to their like-named {{key}} placeholders too.
      - plain string: -> a single {{content}} placeholder (future simple wrapped slots).
    """
    out = wrapper_html
    if isinstance(value, dict):
        for key, raw in value.items():
            out = out.replace("{{" + key + "}}", _render_wrapped_value(raw))
        # Any title_body placeholder the caller omitted collapses to empty rather than leaking literal {{title}}.
        for key in ("title", "body"):
            out = out.replace("{{" + key + "}}", "")
    else:
        out = out.replace("{{content}}", _render_wrapped_value(value))
    return out


def _read_draft_content(email_id: str) -> Dict:
    """Read the live DRAFT content buffer for an email. Raises if the email is not a DRAFT.

    Mirrors the /draft buffer read used by get_email, but enforces the hard rule that fill only
    ever writes to a draft: a non-DRAFT email is refused before any write is attempted.
    """
    headers = get_hubspot_headers()
    base = requests.get(f"{_EMAILS_BASE}/{email_id}", headers=headers, timeout=30)
    base.raise_for_status()
    email = base.json()
    state = (email.get("state") or "").upper()
    if state != "DRAFT":
        raise ValueError(
            f"fill_email_draft refuses to write to email {email_id}: state is {state!r}, not DRAFT. "
            "Clone the template first (clone_email) and fill the resulting draft."
        )
    content = email.get("content", {}) or {}
    d = requests.get(f"{_EMAILS_BASE}/{email_id}/draft", headers=headers, timeout=30)
    if d.status_code == 200 and d.json().get("content"):
        content = d.json()["content"]
    return content


@mcp.tool()
def fill_email_draft(email_id: str, slot_values: Dict, template_name: str) -> Dict:
    """
    Fill a cloned template DRAFT's content slots from a manifest, leaving all brand furniture intact.

    Loads templates/<template_name>.json (the curated slot manifest), reads the draft's live content
    buffer, and for each slot present in slot_values writes the value to BOTH its desktop and mobile
    widget twins, then PATCHes the full content back to the DRAFT buffer. HubSpot null-normalises
    flexAreas (benign, idempotent); nothing else changes. Anything not named in the manifest is fixed
    brand furniture and is never touched.

    Slot kinds and the slot_values shape they expect:
      - text:   a string. Rendered with the same markdown / NAME->first-name-token path as
                create_email_draft, written to widget body.html.
                If the manifest slot declares a `wrapper` (a per-breakpoint HTML scaffold with
                {{field}} placeholders, e.g. a credential card's marker + cell layout), the value
                is substituted INTO that wrapper instead of overwriting the widget. Value shape
                follows the slot's content_kind: 'title_body' -> {"title": ..., "body": ...}
                ({{title}}/{{body}}); a plain string -> a single {{content}} placeholder. Each
                value is HTML-escaped (NAME-token convenience preserved); the wrapper's own
                placeholders are not escaped, and the marker furniture survives untouched.
      - button: {"text": "...", "url": "..."}. Written to body.text + body.destination.
      - image:  {"src": "...", "alt": "..."}. A data:image/...;base64 src is uploaded to HubSpot
                first (via the existing image path) and the CDN URL substituted; written to body.img.

    HARD RULES: writes the DRAFT buffer only; never publishes or sends; refuses any non-DRAFT email;
    a slot with no manifest entry is reported in slots_skipped, never guessed.

    Args:
        email_id: The DRAFT email id to fill (typically a fresh clone_email result).
        slot_values: Map of slot name -> value (see kinds above). Keys must match manifest slot names.
        template_name: Manifest base name (templates/<template_name>.json) describing the template's slots.

    Returns:
        Dict {id, edit_url, slots_filled: [...], slots_skipped: [...]}.
    """
    manifest_path = _resolve_templates_dir() / f"{template_name}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No manifest at {manifest_path}. Curate one with get_template_manifest first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    slots = manifest.get("slots", {}) or {}

    content = _read_draft_content(email_id)
    widgets = content.get("widgets", {}) or {}

    slots_filled: List[str] = []
    slots_skipped: List[str] = []

    for slot_name, value in slot_values.items():
        spec = slots.get(slot_name)
        if not spec:
            slots_skipped.append(slot_name)
            continue
        kind = spec.get("kind")
        wrapper = spec.get("wrapper") or {}
        content_kind = spec.get("content_kind")
        # A wrapped slot fills BOTH twins, each using its own breakpoint wrapper, so its
        # body.html differs per twin. Map widget id -> the wrapper string to use for it.
        wrapper_by_id: Dict[str, str] = {}
        if wrapper:
            d_wrap = wrapper.get("desktop")
            m_wrap = wrapper.get("mobile")
            if spec.get("desktop_id") and d_wrap is not None:
                wrapper_by_id[spec["desktop_id"]] = d_wrap
            if spec.get("mobile_id") and m_wrap is not None:
                wrapper_by_id[spec["mobile_id"]] = m_wrap
        target_ids = [spec.get("desktop_id"), spec.get("mobile_id")]

        # Precompute the rendered payload once, then write to BOTH twins.
        # Wrapped text slots are computed per-twin in the write loop (each twin's wrapper differs).
        if kind == "text" and not wrapper:
            html = _render_text_slot(value if isinstance(value, str) else str(value))
        elif kind == "image":
            data = value if isinstance(value, dict) else {"src": value}
            src = data.get("src", "")
            alt = data.get("alt", "Image")
            # Route data-URI images through the existing host-first path.
            resolved = resolve_data_uri_images([("image", {"src": src, "alt": alt})])
            if not resolved:
                # Upload failed and the image block was dropped; skip rather than write a bad src.
                slots_skipped.append(slot_name)
                continue
            _, rdata = resolved[0]
            src, alt = rdata["src"], rdata.get("alt", alt)

        wrote_any = False
        for wid in target_ids:
            if not wid or wid not in widgets:
                continue
            body = widgets[wid].setdefault("body", {})
            if kind == "text":
                if wid in wrapper_by_id:
                    # Wrapped slot: substitute the value into THIS twin's breakpoint wrapper,
                    # preserving the marker furniture. Do not overwrite the whole widget body.
                    body["html"] = _render_wrapped_slot(wrapper_by_id[wid], value, content_kind)
                else:
                    body["html"] = html
            elif kind == "button":
                data = value if isinstance(value, dict) else {}
                if "text" in data:
                    body["text"] = data["text"]
                if "url" in data:
                    body["destination"] = data["url"]
            elif kind == "image":
                img = body.setdefault("img", {})
                img["src"] = src
                img["alt"] = alt
            wrote_any = True
        if wrote_any:
            slots_filled.append(slot_name)
        else:
            slots_skipped.append(slot_name)

    try:
        patch = requests.patch(
            f"{_EMAILS_BASE}/{email_id}/draft",
            headers=get_hubspot_headers(),
            json={"content": content},
            timeout=30,
        )
        patch.raise_for_status()
    except Exception as e:
        audit_log({"event": "fill_email_draft", "status": "error", "email_id": str(email_id),
                   "template_name": template_name, "slots_filled": slots_filled,
                   "slots_skipped": slots_skipped, "error": str(e)})
        raise

    edit_url = f"https://app.hubspot.com/email/{get_portal_id()}/edit/{email_id}"
    audit_log({"event": "fill_email_draft", "status": "ok", "email_id": str(email_id),
               "template_name": template_name, "slots_filled": slots_filled,
               "slots_skipped": slots_skipped})
    return {"id": str(email_id), "edit_url": edit_url,
            "slots_filled": slots_filled, "slots_skipped": slots_skipped}


def main():
    """Main entry point"""
    load_config()

    transport = os.environ.get("HUBSPOT_EMAIL_MCP_TRANSPORT", "stdio")
    logger.info(f"Starting hubspot-email MCP (transport={transport}, oauth={'yes' if _SERVER_URL else 'no'})")

    if transport == "streamable-http":
        import uvicorn
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", os.environ.get("FASTMCP_PORT", "8000")))
        uvicorn.run(mcp.streamable_http_app(), host=host, port=port)
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
