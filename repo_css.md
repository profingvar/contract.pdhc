# PDHC Frontend Design System

Style guide for all subprojects under the PDHC (Patient-Driven Healthcare) platform. Follow these conventions to maintain visual consistency across services.

---

## Design Philosophy

**Clinical clarity meets modern design.** The interface should feel trustworthy and professional — like a well-designed medical instrument. Every element earns its place. White space is not wasted space; it is breathing room for decision-makers who carry cognitive load.

We draw from Scandinavian healthcare design principles: restrained colour, generous spacing, clear typography, and no visual noise.

---

## Colour Palette

| Token                | Hex       | Usage                                      |
|----------------------|-----------|---------------------------------------------|
| `--pdhc-navy`        | `#1a2332` | Primary text, navbar background, headings   |
| `--pdhc-teal`        | `#0d9488` | Primary action buttons, links, active states|
| `--pdhc-teal-dark`   | `#0a7a70` | Button hover, focus ring                    |
| `--pdhc-teal-light`  | `#e6f7f5` | Teal tint backgrounds (info cards, badges)  |
| `--pdhc-slate`       | `#475569` | Secondary text, labels, muted content       |
| `--pdhc-slate-light` | `#94a3b8` | Placeholder text, disabled states, borders  |
| `--pdhc-bg`          | `#f8fafc` | Page background                             |
| `--pdhc-surface`     | `#ffffff` | Cards, panels, modals, table rows           |
| `--pdhc-border`      | `#e2e8f0` | Card borders, dividers, table lines         |
| `--pdhc-success`     | `#059669` | Approved, active, connected                 |
| `--pdhc-warning`     | `#d97706` | Pending, caution                            |
| `--pdhc-danger`      | `#dc2626` | Rejected, error, destructive actions        |
| `--pdhc-info`        | `#2563eb` | Informational badges, notes                 |

### Rules
- Never use pure black (`#000`) for text. Use `--pdhc-navy` or `--pdhc-slate`.
- Background must always be `--pdhc-bg` or `--pdhc-surface`. No dark mode (clinical environments require high contrast on light backgrounds).
- Status colours (`success/warning/danger`) are used **only** for status indicators and action buttons — never for decoration.

---

## Typography

| Element         | Font                          | Size   | Weight | Line-height |
|-----------------|-------------------------------|--------|--------|-------------|
| Page title      | Inter, system-ui, sans-serif  | 1.75rem| 700    | 1.2         |
| Section heading | Inter, system-ui, sans-serif  | 1.25rem| 600    | 1.3         |
| Body text       | Inter, system-ui, sans-serif  | 0.9375rem| 400  | 1.6         |
| Small/label     | Inter, system-ui, sans-serif  | 0.8125rem| 500  | 1.4         |
| Monospace       | JetBrains Mono, monospace     | 0.8125rem| 400  | 1.5         |

### Rules
- Load Inter from Google Fonts (`wght@400;500;600;700`).
- Body font-size base: `15px` (0.9375rem). Readable on clinical displays without being oversized.
- Never use more than 3 font weights on a single page.
- Headings use `--pdhc-navy`. Body uses `--pdhc-navy` or `--pdhc-slate`.

---

## Spacing Scale

Based on `0.25rem` (4px) increments, following a `4 8 12 16 24 32 48 64` scale.

| Token  | Value  | Usage                          |
|--------|--------|--------------------------------|
| `xs`   | 0.25rem| Tight gaps, icon padding       |
| `sm`   | 0.5rem | Inline spacing, badge padding  |
| `md`   | 0.75rem| Input padding, small gaps      |
| `base` | 1rem   | Default element spacing        |
| `lg`   | 1.5rem | Card padding, section gaps     |
| `xl`   | 2rem   | Section separators             |
| `2xl`  | 3rem   | Page-level vertical rhythm     |
| `3xl`  | 4rem   | Hero/header spacing            |

---

## Component Patterns

### Cards
```css
.card {
    background: var(--pdhc-surface);
    border: 1px solid var(--pdhc-border);
    border-radius: 0.75rem;
    padding: 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
```
- No heavy drop shadows. Borders define edges; shadows are subtle depth cues only.
- Cards stack vertically with `1rem` gap.

### Buttons
```
Primary:    bg teal, white text, rounded-lg, medium shadow on hover
Secondary:  bg white, teal border+text, hover fills teal-light
Danger:     bg danger, white text — only for destructive actions
Ghost:      no bg/border, teal text, hover underline
```
- Minimum touch target: `2.5rem` height.
- All buttons have `font-weight: 600`.
- Loading state: reduce opacity to 0.7, add subtle pulse animation.

### Forms
- Labels above inputs, `font-weight: 500`, `--pdhc-slate` colour.
- Inputs: `0.75rem` padding, `1px solid var(--pdhc-border)`, `border-radius: 0.5rem`.
- Focus ring: `2px solid var(--pdhc-teal)` with `2px` offset.
- Error state: border becomes `--pdhc-danger`, message below in small red text.
- Group related fields in a card. Use 2-column grid for wide forms (`min 640px`).

### Tables
- Header row: `--pdhc-bg` background, `font-weight: 600`, uppercase small text.
- Alternating row colours: `--pdhc-surface` / `--pdhc-bg`.
- Cell padding: `0.75rem 1rem`.
- Horizontal borders only (no vertical cell dividers).
- Wrap in a card with overflow-x auto for mobile.

### Status Badges
```css
.badge { padding: 0.25rem 0.625rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
.badge-approved  { background: #dcfce7; color: #166534; }
.badge-pending   { background: #fef3c7; color: #92400e; }
.badge-rejected  { background: #fee2e2; color: #991b1b; }
```

### Navbar
- Fixed top, `--pdhc-navy` background, white text.
- Height: `3.5rem`.
- Logo/service name left-aligned, nav links right-aligned.
- Active link: teal underline (2px).
- Mobile: hamburger menu, slide-in from right.

---

## Layout

- Max content width: `72rem` (1152px), centred.
- Page padding: `1.5rem` horizontal, `2rem` vertical.
- Sidebar (if used): `16rem` fixed, content fills remainder.
- All layouts are responsive. Breakpoints: `640px` (sm), `768px` (md), `1024px` (lg).
- Mobile-first: default styles are for mobile; use `min-width` media queries to enhance.

---

## Icons

Use **Lucide** icons (MIT licensed, consistent stroke-based style). Load via CDN or inline SVG.

- Stroke width: `1.5px` (matches Inter's optical weight).
- Size: `1.25rem` for inline, `1.5rem` for buttons, `2rem` for feature cards.
- Colour inherits from parent text colour.

---

## Accessibility

- Minimum contrast ratio: 4.5:1 for body text, 3:1 for large text.
- All interactive elements must be keyboard-navigable with visible focus indicators.
- Form inputs must have associated `<label>` elements (not just placeholder text).
- Status information must not rely solely on colour — include text labels or icons.
- Use `aria-live="polite"` for flash messages and dynamic status updates.

---

## Animation

- Transitions: `150ms ease` for hover states, `200ms ease` for expanding/collapsing.
- No animation on page load. No parallax. No auto-playing carousels.
- Respect `prefers-reduced-motion`: disable all transitions when set.

---

## File Organisation

```
static/
  css/
    pdhc.css          ← Full design system (variables + components)
  js/
    pdhc.js           ← Shared utilities (flash dismiss, fetch helpers)
templates/
  base.html           ← Navbar, flash, footer, CSS/JS includes
  login.html
  dashboard.html
  admin.html
  ...
```

Subprojects should import `pdhc.css` (or its CSS variables block) and extend `base.html`. Page-specific styles go in `<style>` blocks within the template, never in separate per-page CSS files.

---

## Do / Don't

| Do | Don't |
|----|-------|
| Use the colour tokens | Invent new colours |
| Keep forms simple and vertical | Nest forms in tabs or accordions |
| Show status with badge + text | Rely on colour alone |
| Use one primary action per card | Clutter cards with multiple CTAs |
| Let tables scroll horizontally on mobile | Hide columns on small screens |
| Use real loading states | Block the UI without feedback |
