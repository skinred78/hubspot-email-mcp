# HubSpot Email MCP Server

An MCP (Model Context Protocol) server that enables Claude Code to create HubSpot marketing email drafts directly from document files (`.md` and `.docx`).

## Features

- 📄 Parse Markdown (`.md`) and Word (`.docx`) documents
- 🖼️ Automatic image extraction and upload to HubSpot File Manager
- ✉️ Create marketing email drafts in HubSpot using default modules
- 🔄 Interleave text and images in the correct order from your document
- ⚡ Optimized section structure to prevent Gmail clipping
- 🎯 Uses HubSpot's default modules (`@hubspot/rich_text`, `@hubspot/email_linked_image`)
- 🎨 Brand guidelines support for consistent styling (colors, typography, spacing, images)
- 🛡️ Comprehensive error handling and logging

## How It Works

The MCP server intelligently parses your document and creates a properly structured HubSpot email:

1. **Content Extraction**: Parses your document in order, tracking both text and images
2. **Image Upload**: Uploads all images to HubSpot File Manager and gets CDN URLs
3. **Module Creation**: Creates HubSpot modules for each content block:
   - Text blocks → `@hubspot/rich_text` modules
   - Images → `@hubspot/email_linked_image` modules
4. **Brand Styling**: Applies your brand guidelines (if configured) to modules and sections
5. **Section Optimization**: Places all modules in a single section to minimize HTML bloat and prevent Gmail clipping
6. **Email Structure**:
   - **Section 0**: All content (text + images in order) in one column
   - **Section 1**: Footer module

This approach avoids creating excessive sections, which can cause Gmail to clip emails over 102KB.

## Installation

### Prerequisites

- Python 3.10 or higher
- HubSpot account with Private App access
- `uv` package manager (recommended) or `pip`

### Install Dependencies

Using `uv` (recommended):
```bash
cd hubspot-email-mcp
uv sync
```

Using `pip`:
```bash
cd hubspot-email-mcp
pip install -e .
```

## HubSpot Setup

### 1. Create a Private App

1. Go to your HubSpot account
2. Navigate to **Settings** → **Integrations** → **Private Apps**
3. Click **Create a private app**
4. Give it a name (e.g., "Email Creator MCP")

### 2. Configure Scopes

The app needs the following scopes:

- **Content**:
  - `content` (Read and write access to marketing emails)
- **Files**:
  - `files` (Read and write access to File Manager)

### 3. Get API Token

1. After creating the app, click **Show token**
2. Copy the token (starts with `pat-...`)
3. Save this token securely - you'll need it for configuration

## Configuration

### 1. Create Config File

Copy the example configuration:
```bash
cp config.example.json config.json
```

### 2. Edit Configuration

Edit `config.json` with your settings:

```json
{
  "hubspot_api_key": "pat-na1-xxxx-xxxx-xxxx",
  "file_folder_path": "/email-images",
  "brand_guidelines_path": "brand-guidelines.json"
}
```

**Configuration Fields:**

- `hubspot_api_key`: Your HubSpot Private App token
- `file_folder_path`: Folder path in HubSpot File Manager where images will be uploaded
- `brand_guidelines_path`: (Optional) Path to brand guidelines JSON file for consistent styling

### 3. No Template Required!

This MCP server creates emails using HubSpot's default modules, so you **don't need a custom template**. The server automatically:
- Creates drag-and-drop email structure
- Uses standard HubSpot modules from Design Manager
- Builds optimized sections to prevent Gmail clipping

The `template_path` configuration field has been removed as it's not needed.

### 4. Brand Guidelines (Optional)

The server supports brand guidelines for consistent styling across all emails. Create a `brand-guidelines.json` file to define your brand's colors, typography, spacing, and image styles.

**📖 See [BRAND-GUIDELINES-REFERENCE.md](BRAND-GUIDELINES-REFERENCE.md) for complete documentation of all available fields.**

#### Create Brand Guidelines File

Copy the example:
```bash
cp brand-guidelines.example.json brand-guidelines.json
```

Edit the file and customize for your brand. The example includes all available fields with explanations.

#### Brand Guidelines Structure

```json
{
  "colors": {
    "primary": "#197FC4",
    "secondary": "#00a4bd",
    "text": "#23496d",
    "background": "#ffffff",
    "border": "#000000"
  },
  "typography": {
    "primaryFont": "Arial, sans-serif",
    "secondaryFont": "Arial, sans-serif",
    "headingOneSize": 28,
    "headingTwoSize": 22,
    "bodySize": 15,
    "linkColor": "#00a4bd",
    "linkUnderline": true
  },
  "spacing": {
    "sectionPaddingTop": "40px",
    "sectionPaddingBottom": "40px",
    "modulePaddingTop": "20px",
    "modulePaddingBottom": "20px"
  },
  "images": {
    "defaultWidth": 600,
    "maxWidth": "100%",
    "alignment": "center",
    "cornerRadius": 0,
    "addBorder": false,
    "borderWidth": 1,
    "borderColor": "#000000"
  },
  "buttons": {
    "backgroundColor": "#197FC4",
    "textColor": "#ffffff",
    "cornerRadius": 8,
    "fontSize": 16
  }
}
```

#### What Gets Styled

When brand guidelines are configured, the server automatically applies:

**Text Modules:**
- Font family and size
- Text color
- Module padding (top/bottom)

**Image Modules:**
- Default image width
- Image alignment (left/center/right)
- Border radius for rounded corners
- Optional borders with color and width
- Module padding (top/bottom)

**Sections:**
- Section padding (top/bottom)
- Background color

**Path Configuration:**
- Use absolute paths: `"brand_guidelines_path": "/absolute/path/to/brand-guidelines.json"`
- Or relative paths (relative to config.json): `"brand_guidelines_path": "brand-guidelines.json"`

#### Working with Multiple Brands/Portals (Recommended)

If you work with multiple HubSpot portals or brands, the best approach is to configure **multiple MCP servers** in Claude Desktop. This gives you:
- Separate API keys for each portal
- Separate brand guidelines for each brand
- No need to restart when switching
- Clear isolation between portals

**Example Setup:**

Create separate config files for each brand:
```bash
hubspot-email-mcp/
├── config-brand-a.json                # Brand A portal config
├── config-brand-b.json               # Brand B portal config
├── brand-a-guidelines.json      # Brand A styles
└── brand-b-guidelines.json     # ACME brand styles
```

**config-brand-a.json:**
```json
{
  "hubspot_api_key": "pat-na1-xxxx-iwt-key",
  "file_folder_path": "/iwt-email-images",
  "brand_guidelines_path": "brand-a-guidelines.json"
}
```

**config-brand-b.json:**
```json
{
  "hubspot_api_key": "pat-na1-yyyy-acme-key",
  "file_folder_path": "/acme-email-images",
  "brand_guidelines_path": "brand-b-guidelines.json"
}
```

Then configure multiple servers in Claude Desktop (see next section for details).

**Single Brand Setup (Alternative):**

If you only work in one portal but want to switch brand guidelines occasionally:
```json
{
  "hubspot_api_key": "pat-na1-xxxx",
  "file_folder_path": "/email-images",
  "brand_guidelines_path": "brand-guidelines.json"
}
```

Update `brand_guidelines_path` to switch styles, then restart Claude Desktop.

## Claude Desktop Integration

### Single Server Setup

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS/Linux) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "hubspot-email": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/hubspot-email-mcp",
        "run",
        "hubspot-email-mcp"
      ],
      "env": {
        "HUBSPOT_EMAIL_MCP_CONFIG": "/absolute/path/to/config.json"
      }
    }
  }
}
```

### Multiple Portal/Brand Setup (Recommended)

If you work with multiple HubSpot portals or brands, configure one server per portal:

```json
{
  "mcpServers": {
    "hubspot-email-brand-a": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/hubspot-email-mcp",
        "run",
        "hubspot-email-mcp"
      ],
      "env": {
        "HUBSPOT_EMAIL_MCP_CONFIG": "/absolute/path/to/config-brand-a.json"
      }
    },
    "hubspot-email-brand-b": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/hubspot-email-mcp",
        "run",
        "hubspot-email-mcp"
      ],
      "env": {
        "HUBSPOT_EMAIL_MCP_CONFIG": "/absolute/path/to/config-brand-b.json"
      }
    }
  }
}
```

**How to Use:**

When you ask Claude to create an email, it will know which brand/portal you're targeting based on context:

```
# For Brand A portal
Create a marketing email from newsletter.md for the Brand A portal

# For Brand B portal
Create a marketing email from announcement.docx for the Brand B portal
```

Claude Code will automatically use the correct server based on your request.

**Benefits:**
- ✅ No need to restart when switching between portals
- ✅ Each portal has its own API key and brand guidelines
- ✅ Can work on multiple brands simultaneously
- ✅ Clear separation and isolation

### Important Notes

- Use **absolute paths** for both the directory and config file
- Restart Claude Desktop after modifying the config
- Check Claude Desktop logs if the server doesn't appear: `~/Library/Logs/Claude/` (macOS) or `%APPDATA%\Claude\logs\` (Windows)

## Usage

Once installed, you can use the tool from Claude Code:

### Basic Usage

```
Create a marketing email from newsletter.md named "Monthly Newsletter"
with subject line "Your Monthly Update - January 2025"
```

### With DOCX Files

```
Create a marketing email from product-announcement.docx named
"New Product Launch" with subject "Introducing Our Latest Innovation"
```

### Example Workflow

1. **Prepare your document** (`.md` or `.docx`) with:
   - Email content (text, headings, paragraphs)
   - Images (embedded or referenced)

2. **Ask Claude Code** to create the email:
   ```
   Create a HubSpot marketing email from /path/to/my-newsletter.md
   named "Newsletter Q1" with subject "Quarterly Update"
   ```

3. **Claude will**:
   - Parse the document
   - Extract and upload any images to HubSpot
   - Create the email draft
   - Provide you with the HubSpot email editor URL

4. **You can then**:
   - Open the URL in your browser
   - Review and edit the email in HubSpot
   - Schedule or send the email

## Document Format Guidelines

### Markdown Files (`.md`)

```markdown
# Heading 1

## Heading 2

Paragraph text with **bold** and *italic*.

![Alt text](./images/my-image.png)

- Bullet point 1
- Bullet point 2
```

**Image References:**
- Local images: `![alt](./path/to/image.png)` - will be uploaded
- External images: `![alt](https://example.com/image.png)` - will be skipped

### DOCX Files (`.docx`)

- Use standard Word formatting (headings, paragraphs, bold, italic)
- Embedded images will be extracted automatically
- Heading styles (Heading 1, 2, 3) will be converted to HTML headings

## Technical Details

### HubSpot Modules Used

The server uses HubSpot's default modules from Design Manager:

- **`@hubspot/rich_text`**: For text content (paragraphs, headings)
- **`@hubspot/email_linked_image`**: For images with optional links
- **`@hubspot/email_footer`**: For the unsubscribe footer

These modules are available by default in all HubSpot accounts and don't require any custom development.

### Section Optimization

**Why This Matters:**
- Gmail clips emails over 102KB
- Each section adds HTML bloat to the email
- Multiple sections can quickly push emails over the limit

**Our Approach:**
- Places all content modules (text + images) in a **single section**
- Only creates a new section when the column layout changes (e.g., 1-column → 2-column)
- For single-column emails, this means only 2 sections total: content + footer

**Future Enhancement:**
The server can be extended to support multi-column layouts by detecting layout changes and creating new sections only when necessary.

## Troubleshooting

### Server Not Appearing in Claude Desktop

1. Check that paths are absolute (not relative)
2. Verify `uv` is installed: `uv --version`
3. Check Claude Desktop logs:
   - macOS: `~/Library/Logs/Claude/`
   - Windows: `%APPDATA%\Claude\logs\`

### API Errors

**401 Unauthorized:**
- Verify your API key is correct in `config.json`
- Ensure the Private App is not deactivated

**403 Forbidden:**
- Check that the Private App has the required scopes (`content`, `files`)

**Image Upload Failures:**
- Images will be skipped with a warning
- Check file size limits (HubSpot has a 50MB limit per file)
- Verify the `file_folder_path` exists in your File Manager

### Content Not Appearing

**Images not showing:**
- Check that images were uploaded successfully (look for success messages in logs)
- Verify the CDN URLs are accessible
- Ensure the HubSpot account has the `files` scope enabled

**Text formatting issues:**
- The server converts Word styles to HTML (Heading 1 → `<h1>`, etc.)
- Verify your document has proper heading styles applied

## Development

### Running Tests

```bash
# Install dev dependencies
uv sync --dev

# Run tests (when implemented)
pytest
```

### Testing the Server

Create a test document:

**test.md:**
```markdown
# Test Email

This is a test email with an image.

![Test](./test-image.png)
```

Then use Claude Code to create an email from it.

## API Rate Limits

HubSpot has rate limits on their APIs:
- **Marketing Emails API**: 250 requests per 10 seconds
- **Files API**: 100 requests per 10 seconds

The server includes basic error handling for rate limits (429 errors).

## Security Notes

- **Never commit** your `config.json` file with real API keys
- Store API keys securely (use environment variables in production)
- The `config.example.json` is safe to commit (contains no real keys)

## License

MIT

## Support

For issues or questions:
- Check the [MCP Documentation](https://modelcontextprotocol.io/)
- Review [HubSpot API Documentation](https://developers.hubspot.com/docs/api/overview)
- Open an issue in this repository
