# Brand Guidelines Reference

This document explains all available fields in the brand guidelines JSON file and how they're applied to your emails.

## Complete Example

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
    "headingOneFont": "Georgia, serif",
    "headingTwoFont": "Georgia, serif",
    "headingOneSize": 28,
    "headingTwoSize": 22,
    "headingLineHeight": 1.5,
    "bodySize": 15,
    "bodyLineHeight": 1.5,
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
    "borderColor": "#000000",
    "padding": "10px 20px"
  },
  "buttons": {
    "backgroundColor": "#197FC4",
    "textColor": "#ffffff",
    "cornerRadius": 8,
    "fontSize": 16
  }
}
```

## Field Reference

### Colors

| Field | Type | Description | Applied To |
|-------|------|-------------|------------|
| `primary` | Hex color | Primary brand color | Buttons (default), future features |
| `secondary` | Hex color | Secondary brand color | Future features |
| `text` | Hex color | **Body text color** | All paragraphs, headings, list items |
| `background` | Hex color | **Background color** | Email sections |
| `border` | Hex color | Border color | Future features |

**Example:**
```json
"colors": {
  "text": "#000000",
  "background": "#ffffff"
}
```

### Typography

| Field | Type | Description | Applied To |
|-------|------|-------------|------------|
| `primaryFont` | Font family | **Main body font** | All paragraph text |
| `secondaryFont` | Font family | Secondary font | Future features |
| `headingOneFont` | Font family | **H1 heading font** | All `<h1>` tags |
| `headingTwoFont` | Font family | **H2 heading font** | All `<h2>` tags |
| `headingOneSize` | Number (px) | **H1 font size** | All `<h1>` tags |
| `headingTwoSize` | Number (px) | **H2 font size** | All `<h2>` tags |
| `headingLineHeight` | Number | **Heading line height** | All headings (H1, H2) |
| `bodySize` | Number (px) | **Body font size** | All paragraph text |
| `bodyLineHeight` | Number | **Body line height** | All paragraph text |
| `linkColor` | Hex color | Link color | Future features |
| `linkUnderline` | Boolean | Underline links | Future features |

**Example:**
```json
"typography": {
  "primaryFont": "Arial, sans-serif",
  "headingOneFont": "Georgia, serif",
  "headingTwoFont": "Georgia, serif",
  "headingOneSize": 36,
  "headingTwoSize": 28,
  "headingLineHeight": 1.5,
  "bodySize": 18,
  "bodyLineHeight": 1.75
}
```

**Font Family Format:**
- Always include fallbacks: `"Georgia, serif"` or `"Arial, sans-serif"`
- Use web-safe fonts or custom fonts available in HubSpot

### Spacing

| Field | Type | Description | Applied To |
|-------|------|-------------|------------|
| `sectionPaddingTop` | CSS value | **Top padding for sections** | All email sections |
| `sectionPaddingBottom` | CSS value | **Bottom padding for sections** | All email sections |
| `modulePaddingTop` | CSS value | **Top padding for content** | Text and image modules |
| `modulePaddingBottom` | CSS value | **Bottom padding for content** | Text and image modules |

**Example:**
```json
"spacing": {
  "sectionPaddingTop": "20px",
  "sectionPaddingBottom": "20px",
  "modulePaddingTop": "10px",
  "modulePaddingBottom": "10px"
}
```

**CSS Value Format:**
- Use px units: `"20px"`
- Can use other CSS units: `"1.5em"`, `"2rem"`

### Images

| Field | Type | Description | Applied To |
|-------|------|-------------|------------|
| `defaultWidth` | Number (px) | **Default image width** | All images |
| `maxWidth` | CSS value | Maximum image width | Future features |
| `alignment` | String | **Image alignment** | All images |
| `cornerRadius` | Number (px) | **Border radius for rounded corners** | All images |
| `addBorder` | Boolean | Add border to images | Future features |
| `borderWidth` | Number (px) | Border width | Future features |
| `borderColor` | Hex color | Border color | Future features |
| `padding` | CSS value | **Image padding (top/bottom left/right)** | All images |

**Example:**
```json
"images": {
  "defaultWidth": 500,
  "alignment": "center",
  "cornerRadius": 10,
  "padding": "10px 20px"
}
```

**Alignment Options:**
- `"left"` - Align images to the left
- `"center"` - Center images (default)
- `"right"` - Align images to the right

**Padding Format:**
- Two values: `"10px 20px"` = 10px top/bottom, 20px left/right
- Single value: `"10px"` = 10px all sides

### Buttons

| Field | Type | Description | Applied To |
|-------|------|-------------|------------|
| `backgroundColor` | Hex color | Button background color | Future features |
| `textColor` | Hex color | Button text color | Future features |
| `cornerRadius` | Number (px) | Button border radius | Future features |
| `fontSize` | Number (px) | Button font size | Future features |

**Note:** Button styling is not yet implemented in the current version but is included for future expansion.

## What Gets Styled Automatically

### Text Content
- ✅ **Font family**: Applied from `typography.primaryFont`
- ✅ **Font size**: Applied from `typography.bodySize`
- ✅ **Line height**: Applied from `typography.bodyLineHeight`
- ✅ **Text color**: Applied from `colors.text`
- ✅ **Padding**: Applied from `spacing.modulePaddingTop/Bottom`

### Headings (H1, H2)
- ✅ **Font family**: Applied from `typography.headingOneFont` / `headingTwoFont`
- ✅ **Font size**: Applied from `typography.headingOneSize` / `headingTwoSize`
- ✅ **Line height**: Applied from `typography.headingLineHeight`
- ✅ **Text color**: Applied from `colors.text`
- ✅ **Spacing**: Automatic `<p>&nbsp;</p>` before and after

### Images
- ✅ **Width**: Applied from `images.defaultWidth`
- ✅ **Border radius**: Applied from `images.cornerRadius`
- ✅ **Padding**: Applied from `images.padding`
- ✅ **Alignment**: Applied from `images.alignment` (via CSS text-align)

### Sections
- ✅ **Top padding**: Applied from `spacing.sectionPaddingTop`
- ✅ **Bottom padding**: Applied from `spacing.sectionPaddingBottom`
- ✅ **Background color**: Applied from `colors.background`

### Lists
- ✅ **List item spacing**: Automatic 10px padding between items (except last)
- ✅ **Font styling**: Inherits from body text settings

## Content Formatting (Automatic)

In addition to visual styling, the following content formatting is applied automatically:

1. **Personalization Tokens**: `NAME` → `{{ personalization_token('contact.firstname', 'Hey') }}`
2. **Paragraph Spacing**: Automatic `<p>&nbsp;</p>` between paragraphs
3. **List Spacing**: 10px padding between list items (except last)
4. **Heading Spacing**: `<p>&nbsp;</p>` before and after headings

## How to Create Your Brand Guidelines

### 1. Get Your Brand's Visual Specs

Collect these from your brand guidelines or design team:
- Logo colors (primary, secondary)
- Text color
- Background color
- Font families (with web-safe fallbacks)
- Font sizes (body, headings)
- Line heights
- Spacing/padding standards

### 2. Extract from Existing Emails

If you have existing HubSpot emails that match your brand:
1. Open an email in HubSpot editor
2. Click on Design → Settings
3. Note down:
   - Font families
   - Font sizes
   - Colors
   - Padding values

### 3. Start with the Example

Copy `brand-guidelines.example.json` and customize:
```bash
cp brand-guidelines.example.json your-brand-guidelines.json
```

### 4. Test and Refine

1. Create a test email with your brand guidelines
2. Review in HubSpot email editor
3. Adjust values as needed
4. Update your brand guidelines file
5. Recreate the email to see changes

## Tips

### Font Families
- **Web-safe fonts**: Arial, Georgia, Times New Roman, Courier, Verdana, Helvetica
- **Always include fallbacks**: `"Georgia, serif"` not just `"Georgia"`
- **Custom fonts**: Must be available in your HubSpot account

### Colors
- Use 6-digit hex codes: `"#000000"` not `"#000"`
- Include the `#` symbol
- Use lowercase for consistency

### Sizing
- **Font sizes**: 14-18px for body is typical for emails
- **Headings**: Usually 1.5-2x body size
- **Line height**: 1.5-1.75 for readability
- **Images**: 500-600px width works well for most email clients

### Spacing
- **Module padding**: 10-20px is typical
- **Section padding**: 20-40px creates good visual separation
- Keep values consistent with your brand

## Troubleshooting

**Styles not applying?**
1. Check that `brand_guidelines_path` is set in your config.json
2. Verify the JSON is valid (no syntax errors)
3. Restart Claude Desktop to reload the guidelines
4. Check Claude Desktop logs for errors

**Colors look wrong?**
- Verify hex codes are correct (6 digits + #)
- Check that you're using the right field (`text` for body text, not `primary`)

**Fonts not changing?**
- Ensure font names are spelled correctly
- Include fallback fonts: `"Arial, sans-serif"`
- Verify the font is web-safe or available in HubSpot
