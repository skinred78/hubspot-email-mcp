"""HubSpot Email MCP Server - Create marketing emails from documents"""
import json
import os
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional
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

# Initialize FastMCP server
mcp = FastMCP("hubspot-email")

# Global configuration
config = {}
brand_guidelines = {}


def load_config():
    """Load configuration from environment variable"""
    global config, brand_guidelines
    config_path = os.environ.get("HUBSPOT_EMAIL_MCP_CONFIG")
    if not config_path:
        raise ValueError("HUBSPOT_EMAIL_MCP_CONFIG environment variable not set")

    with open(config_path, 'r') as f:
        config = json.load(f)

    required_fields = ["hubspot_api_key", "file_folder_path"]
    for field in required_fields:
        if field not in config:
            raise ValueError(f"Missing required config field: {field}")

    # Load brand guidelines if specified
    brand_guidelines_path = config.get("brand_guidelines_path")
    if brand_guidelines_path:
        try:
            # Support both absolute and relative paths
            if not os.path.isabs(brand_guidelines_path):
                # If relative, make it relative to the config file directory
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


def apply_iwt_content_formatting(html: str) -> str:
    """
    Apply IWT-specific content formatting patterns to HTML

    This includes:
    - Adding spacing between paragraphs
    - Adding spacing to list items (except last)
    - Replacing NAME with personalization tokens
    - Adding inline styles to headings
    - Adding spacing around headings

    Args:
        html: The HTML content string

    Returns:
        HTML with IWT content formatting applied
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
        HTML wrapped with inline styles and IWT content formatting
    """
    if not brand_guidelines:
        return html

    # First apply IWT content formatting patterns
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


def create_hubspot_email_from_blocks(email_name: str, subject_line: str, content_blocks: List[Tuple]) -> Dict:
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

        logger.info(f"Creating email with name: {email_name}")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        email_id = result.get('id')
        portal_id = result.get('portalId', '')

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
        portal_id = result.get('portalId', '')

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


def main():
    """Main entry point"""
    # Load configuration
    load_config()

    # Run the MCP server
    mcp.run()


if __name__ == "__main__":
    main()
