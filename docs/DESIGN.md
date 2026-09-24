# Glassfolio design language

How the Glassfolio UI looks and why, written so the style can be rebuilt in another
project. It is Apple's [Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/liquid-glass)
adapted for a data-dense app, where the numbers have to stay easy to read.

Source of truth: `web/src/index.css` (all tokens and components), `web/src/App.tsx`
(layout), `web/src/Segmented.tsx`, `web/src/icons.tsx`, `src-tauri/src/lib.rs`
(native window) and `src-tauri/splash/splash.css` (splash and onboarding).

## 1. Principles

1. **Two layers: glass for navigation, solid for content.** The sidebar, toolbar
   controls and assistant panel are glass floating above the content. Numbers,
   tables and charts sit on near-solid "sheets". Never put a number on glass: the
   blurred background shifts under it and hurts legibility.
2. **The material is light, not color.** Glass is a thin tint plus blur and
   saturation, a bright rim where light catches the edge, a soft sheen near the top
   and a float shadow. Its color comes from whatever is behind it, so there must
   always be something colorful behind it (see the backdrop in §3).
3. **Capsules and nested corners.** Every control is a capsule (`border-radius:
   999px`). Panels inside panels subtract their inset from the parent radius, so
   corners stay parallel.
4. **Quiet frame, meaningful color.** One accent blue, three ink shades, and status
   colors kept for status. Color only appears where it means something, such as
   held directly vs through funds, or the three causes of a change.
5. **The material carries the product's idea.** Glassfolio is about seeing
   *through* funds, so "held directly" is a solid bar and "held through funds" is
   a frosted glass bar. When you reuse this style, find your own product's reason
   for the glass. Don't copy this one.
6. **Native first.** System font (SF Pro), no web fonts, no CDNs. It follows the
   system's dark mode, Reduce Transparency, Increase Contrast and Reduce Motion
   settings.
7. **Plain words.** The page opens with a sentence stating the finding, not a
   dashboard of tiles. Labels use the user's words ("What you own", "Your money"),
   not the system's.

## 2. Tokens

### Color

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--ink` | `#1b232b` | `#eef2f6` | primary text |
| `--ink-2` | `#4f5d6b` | `#aeb9c4` | secondary text, labels, column heads |
| `--ink-3` | `#7d8997` | `#7c8894` | faint text, axis and crosshair lines |
| `--accent` | `#2b6cb0` | `#4a8fe0` | primary button, icons, focus ring, links |
| `--select` | accent @ 14% | accent @ 22% | selected nav item, pressed toggle, drop hover |
| `--direct` | `#2b4c7e` | `#86aee6` | solid "held directly" bar, line chart |
| `--lens` | `#0e7c86` | `#56c7cf` | outline of the glass "through funds" bar |
| `--lens-glass` | lens @ 22% | lens @ 22% | fill of the glass bar |
| `--sheet` | `#fbfcfd` | `#15191e` | content sheets |
| `--sheet-2` | `#f2f5f8` | `#1b2026` | inputs, nested panels, drop zone |
| `--sheet-line` | `#e4e9ef` | `#262e37` | row separators, input borders |
| `--sheet-hover` | `#eef3f7` | `#1e252c` | hovered or selected table row |
| `--pass` / `--warn` / `--fail` | `#2f7d4f` / `#9a6200` / `#b3261e` | `#6cc58f` / `#e0a84a` / `#f07b72` | status only |

Dark mode is its own palette, chosen step by step, not an automatic inversion:
the accents get lighter and the sheets become blue-black rather than grey.

**Chart categories** (checked for color-blind separation and contrast on both
surfaces with a palette validator):

| Token | Light | Dark | Meaning |
| --- | --- | --- | --- |
| `--c-price` | `#2a78d6` | `#3987e5` | price |
| `--c-money` | `#eb6834` | `#d95926` | your money (deposits, withdrawals) |
| `--c-rebal` | `#1baf7a` | `#199e70` | fund rebalancing |

### Glass

| Token | Light | Dark |
| --- | --- | --- |
| `--glass-tint` | `rgba(255,255,255,.34)` | `rgba(38,46,58,.42)` |
| `--glass-tint-strong` (buttons, sidebar selects) | `rgba(255,255,255,.58)` | `rgba(52,62,76,.66)` |
| `--glass-rim-hi` | `rgba(255,255,255,.95)` | `rgba(255,255,255,.34)` |
| `--glass-rim-lo` | `rgba(255,255,255,.18)` | `rgba(255,255,255,.04)` |
| `--glass-inner` (sheen, hover) | `rgba(255,255,255,.28)` | `rgba(255,255,255,.06)` |
| `--glass-shade` | `rgba(16,34,52,.16)` | `rgba(0,0,0,.45)` |
| `--glass-blur` | `blur(22px) saturate(1.9) brightness(1.06)` | `blur(22px) saturate(1.6) brightness(.96)` |

### Shape and spacing

| Token | Value | Use |
| --- | --- | --- |
| `--r-window` | 26px | floating sidebar, assistant panel |
| `--r-sheet` | 22px | content sheets |
| `--pad-sheet` | 18px | sheet side padding (22px top and bottom) |
| `--r-row` | 12px | hovered table rows, text areas |
| `--r-capsule` | 999px | every button, input, select, segmented control, nav item |
| nested panels | `calc(var(--r-sheet) - inset)` | drawers −8px, drop zone −6px |

The floating layer sits 10px from the window edges. Gaps: 16px between sheets,
12px inside control rows, 2px between nav items.

### Type

System stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue",
sans-serif`. The base is 15px with 1.45 line height and antialiased text.

| Role | Size / weight / tracking |
| --- | --- |
| Page title (toolbar) | 26 / 700 / −0.02em |
| Headline sentence | 28 / 650 / −0.022em, `max-width: 30ch`, `text-wrap: balance` |
| Section title | 19 / 650 / −0.01em |
| Fact value | 19 / 650 / −0.01em |
| Brand | 17 / 650 / −0.01em |
| Body | 15 / 400 |
| Controls | 14 / 550 |
| Labels, column heads, legends | 13 / 500–600, `--ink-2` |
| Notes, badges | 12 |

- **Every number** uses `font-variant-numeric: tabular-nums`, is right-aligned and
  never wraps (the `.num` class).
- Weights 550 and 650 rely on SF Pro being a variable font. They give slightly
  softer steps than 500/600/700.
- Negative tracking only on large type.
- Sentence case everywhere. No all caps, no small labels floating above headings.

## 3. The glass recipe

This is the whole material. Apply `.glass` to anything on the navigation layer.

```css
.glass {
  position: relative;
  background: var(--glass-tint);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  box-shadow:
    inset 0 1px 0.5px var(--glass-inner),          /* top inner highlight */
    inset 0 -8px 16px -12px var(--glass-shade),     /* thickness at the bottom */
    0 1px 2px var(--glass-shade),                   /* contact shadow */
    0 12px 36px -8px var(--glass-shade);            /* float shadow */
  isolation: isolate;
}
/* Specular rim: light catches the top-left edge and fades around the shape. */
.glass::before {
  content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  pointer-events: none;
  background: linear-gradient(145deg, var(--glass-rim-hi), var(--glass-rim-lo) 38%,
                              var(--glass-rim-lo) 62%, var(--glass-rim-hi));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;   /* keep only the 1px ring */
}
/* A soft sheen near the top, like light through a curved surface. */
.glass::after {
  content: ""; position: absolute; inset: 0; border-radius: inherit;
  pointer-events: none; z-index: -1;
  background: radial-gradient(120% 60% at 30% 0%, var(--glass-inner), transparent 70%);
}
```

**The backdrop matters as much as the glass.** Over a flat background, glass looks
like grey plastic. The page background is three large, soft radial gradients over
a cool base, fixed so they don't scroll:

```css
--backdrop:
  radial-gradient(900px 620px at 6% -4%,    #a9d2e3 0%, transparent 62%),  /* sky, top left */
  radial-gradient(760px 560px at 102% 104%, #c8bfe8 0%, transparent 58%),  /* lavender, bottom right */
  radial-gradient(620px 420px at 70% 10%,   #d8ecdf 0%, transparent 60%),  /* mint, top */
  #e3e9ee;
/* dark: #173a4a, #2b2548, #16302a over #0c1015 */
body { background: var(--backdrop); background-attachment: fixed; }
```

Rules:
- **No glass on glass.** A control inside a glass group drops its own track
  (`.capsule-group .segmented { background: transparent; border-color: transparent }`).
- **Two strengths only:** `--glass-tint` for surfaces, `--glass-tint-strong` for
  buttons and for selects on the sidebar, which need more contrast.
- **Reduce Transparency** turns glass opaque: no blur, sheet colors, no sheen.

## 4. Layout

```
╭──────────────╮                                          ╭──────────────╮
│ ● ● ●        │  What you own  (Before|After) (Date|✦)   │ Assistant    │
│ Glassfolio   │  ░░░░░░░░ scroll-edge frost ░░░░░░░░░░   │              │
│              │  ╭────────────── sheet ───────────────╮  │ ╭──────────╮ │
│(What you own)│  │ NVDA is your largest real holding… │  │ │ bubble   │ │
│ What changed │  │ Portfolio  After tax  Through funds│  │ ╰──────────╯ │
│ Questions    │  ╰────────────────────────────────────╯  │              │
│ …            │  ╭────────────── sheet ───────────────╮  │              │
│ Show         │  │ Company  ▇▇▇▇░░░░  Direct   Total  │  │              │
│ (Person    ⌄)│  │ ────────────────────────────────── │  │ ╭──────────╮ │
│ (Account   ⌄)│  │ …                                  │  │ │ Ask…     │ │
╰──────────────╯  ╰────────────────────────────────────╯  ╰──────────────╯
264px glass       fluid content on solid sheets           380px glass
```

- **Grid:** `264px 1fr`, plus `380px` when the assistant is open.
- **Floating inset sidebar:** sticky, 10px from the window edges,
  `height: calc(100vh - 20px)`, radius 26. From top to bottom: brand, nav, then the
  filters under a small "Show" heading.
- **Toolbar without a bar:** the page title on the left, then glass capsule groups
  holding the controls. It is sticky, and content scrolls beneath it. A
  **scroll-edge effect** keeps the title readable: a `::before` layer with
  `backdrop-filter: blur(10px)`, masked with `linear-gradient(#000 55%, transparent)`
  so the frost fades out rather than ending in a hard line.
- **Content:** a column of sheets with 16px gaps. The first sheet opens with a
  headline sentence and a row of key facts. The next holds the table or chart.
- **Assistant panel:** a second floating glass panel on the right, mirroring the
  sidebar.
- **Narrow windows:** under 820px the sidebar stacks above the content and columns
  marked `.hide-narrow` drop out. Under 1100px the assistant becomes an overlay.

## 5. Components

| Component | Recipe |
| --- | --- |
| **Nav item** | Capsule button, 36px min height, icon in `--accent`. Hover: `--glass-inner`. Selected: `--select` background, weight 600, `aria-current="page"`. Count badge on the right in `--warn`. |
| **Capsule group** | `.glass` capsule with 4px padding, holding a segmented control, a date field, or icon buttons separated by a 1px × 20px `--glass-rim-lo` divider. |
| **Button** | Capsule, 34px, weight 550, `--glass-tint-strong` with backdrop blur, top rim highlight. Press: `scale(.96)` with a spring curve and a 4px `--select` halo. |
| **Primary button** | 88% accent (via `color-mix`) with white text and a soft accent glow underneath (`0 2px 8px -2px var(--accent)`). |
| **Icon button** | 34px circle, transparent. Hover: `--glass-inner`. On (`aria-pressed`): `--select` with the icon in accent. |
| **Segmented control** | Capsule track on `--sheet-2`. A white **knob** slides under the chosen segment using CSS variables: `width: calc((100% - 6px) / var(--count)); transform: translateX(calc(100% * var(--index)))`. `role="tablist"` / `tab` / `aria-selected`. |
| **Inputs and selects** | Capsule on `--sheet-2` with a 1px `--sheet-line` border, 34px. Selects use `appearance: none` and an inline SVG up/down chevron in `--ink-3`. Text areas use radius 12. |
| **Sheet** | `--sheet`, radius 22, padding 22/18, two-part shadow: `0 1px 2px rgba(16,34,52,.06), 0 8px 24px -16px rgba(16,34,52,.18)`. No border. |
| **Headline and facts** | The headline is one sentence stating the most important thing. Facts are a `<dl>` row: 13px `--ink-2` label over a 19px/650 value, 32px apart. A secondary value is `--ink-3`. |
| **Table** | `border-collapse: separate`. Column heads 13px/500 `--ink-2`. Cells 11px/12px padding with a hairline top border (none on the first row). Hovered and selected rows become **rounded capsules**: `--sheet-hover`, radius 12 on the first and last cells, and the next row's border is hidden so the capsule reads clean. Rows open in place (`aria-expanded`), and Enter works too. |
| **Drawer** (expanded row) | Grid of `--sheet-2` panels, radius `sheet − 8`, `repeat(auto-fit, minmax(240px, 1fr))`. |
| **Drop zone** | 1.5px dashed `--sheet-line` on `--sheet-2`, radius `sheet − 6`. Dragging over it turns it accent (border, `--select` fill, darker text). The large variant has a centred accent icon and a 17px line. |
| **Status** | Icon, label and color together: `✓ Reconciled`, `! Check notes`, `✗ Doesn't match`. Never color alone. |
| **Approximation mark** | A faint `≈` after a value that relies on an estimate, with a tooltip saying which one. The legend explains it. |
| **Privacy mode** | A toolbar eye toggle blurs every amount (`filter: blur(7px)`, not selectable). Percentages and bars stay visible, so the page is still useful to screen-share. |
| **Chat bubbles** | User: accent fill, radius `18 18 6 18`. Assistant: `--sheet` with a hairline shadow, radius `18 18 18 6`. Proposed changes appear in a bordered `--sheet` card with a confirm button. |
| **Icons** | SF Symbols in spirit: 20px grid, 1.6px stroke, round caps and joins, `currentColor`, 18px by default. Always next to a text label or given an `aria-label`. |
| **Logo** | Three offset rounded squares: solid navy, then half-transparent teal glass, then pale glass, each with a white rim. It is the product idea in one mark. |

## 6. Charts

This follows standard data-visualization practice: color by what it means, thin
marks, a legend always shown, text in ink colors rather than series colors.

- **The signature bar (direct vs through funds):** 14px tall, width relative to
  the largest row. Solid `--direct` on the left. The right part is **glass**:
  `--lens-glass` fill, a white top-down gradient, and a 1px inset `--lens`
  outline. 2px gap between parts; only the outer ends are rounded (5px). The
  legend repeats both swatches.
- **Diverging bar (what changed):** a 1px `--ink-3` zero line; negative parts grow
  left, positive parts grow right. The colors are the three chart categories,
  2px gaps, 4px rounded outer ends. Each part has a tooltip, and the bar has an
  `aria-label` listing every value.
- **Line chart (value over time):** 2px `--direct` line
  (`vector-effect: non-scaling-stroke`). No grid and no axes. On hover: a 1px
  `--ink-3` crosshair and a 4px dot with a 2px ring in the surface color. The
  caption under the chart switches from the range to the hovered date and value,
  so there is no floating tooltip to cover the line.
- **Never** two y-axes, rainbow palettes, or status colors used as categories.

## 7. Motion

Motion only answers something the user did. There are no entrance animations and
no animation on load.

| What | Timing |
| --- | --- |
| Hover backgrounds | `background-color .18s ease` |
| Button press | `transform .2s cubic-bezier(.3,.7,.4,1.4)` (a slight overshoot, like a spring) |
| Segmented knob | `transform .32s cubic-bezier(.3,.7,.3,1.15)` |
| Privacy blur | `filter .25s ease` |
| Drop zone | `.2s ease` on border and background |

With Reduce Motion on, every transition is off.

## 8. Accessibility and system settings

```css
@media (prefers-reduced-transparency: reduce) {   /* glass becomes opaque */
  .glass, .btn, .toolbar::before { backdrop-filter: none; }
  .glass { background: var(--sheet); }  .btn { background: var(--sheet-2); }
  .glass::after { display: none; }
}
@media (prefers-contrast: more) { :root { --glass-tint: rgba(255,255,255,.85); --sheet-line: #b9c3cd; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

- Status and chart meaning never rely on color alone: status has an icon and a
  label, and every chart has a legend and text values.
- Every control is a real `<button>`, `<select>` or `<input>`, with ARIA state
  (`aria-current`, `aria-pressed`, `aria-selected`, `aria-expanded`).
- Keyboard: focusable table rows open with Enter; the focus ring is always visible.

## 9. Native shell (macOS, Tauri 2)

In the desktop app, the real glass is macOS's own, behind the web page.

- **Window:** `transparent(true)`, `TitleBarStyle::Overlay`, `hidden_title(true)`,
  traffic lights at `(24, 28)` so they sit inside the floating sidebar, 1280×820
  (minimum 900×600).
- **Material:** `tauri-plugin-liquid-glass` with `GlassMaterialVariant::Sidebar`.
  This is `NSGlassEffectView` on macOS 26 and `NSVisualEffectView` on earlier
  versions.
- **The page adapts** when served with `<html data-shell="tauri">`:
  - `body` becomes transparent, so the native glass fills the window.
  - `.glass` drops its own `backdrop-filter`, since a CSS blur on top of native
    glass looks muddy. It keeps a faint tint (`color-mix(... 45%, transparent)`),
    the rim and the float shadow.
  - The sidebar gets 52px top padding to clear the traffic lights. The brand and
    the toolbar title carry `data-tauri-drag-region`, so the window drags from
    them.
  - Sheets get a slightly deeper shadow, since nothing colorful sits behind them.
- **Splash and onboarding** pages sit straight on the native glass: centred 72%
  opaque cards with radius 26 and a hairline border, the app icon with a soft
  shadow, and a row of capsule step markers.

## 10. Words

The words follow the same rules as the visuals: plain and specific.

- **Name things by what the user sees:** "What you own", "What changed",
  "Through funds", "Your money", "Fund rebalancing". Not "exposure", "flows" or
  "attribution".
- **Open with a sentence** that states the finding: *"NVDA is your largest real
  holding: 18.2% of everything, and 64.0% of it sits inside funds."*
- **Loading says what is happening:** "Looking through your funds…"
- **Empty states say what to do**, with a button: "Nothing to show for 2026-09-18.
  Import a position statement and the holdings of the funds you own, then come
  back here." → **Import files**
- **Errors say what happened and how to fix it**, without apologizing: "No local
  model server answered at this address. Start Ollama or LM Studio, or leave the
  model on None."
- **Buttons say exactly what they do**, and the confirmation uses the same verb:
  **Import** → "Positions imported; …"

## 11. Don'ts

- Glass under numbers, tables or charts.
- Glass on glass.
- A flat background behind glass.
- Borders on sheets. Their shadow and the difference in surface are enough.
- Radii that don't nest, or square controls.
- Web fonts, icon fonts or CDNs.
- Entrance or scroll animations.
- Colored text for series, or status colors used as categories.
- Dashboards of identical tiles in place of a sentence and a table.

## 12. Rebuilding it elsewhere

1. Copy the tokens (§2) and the glass recipe (§3) into your root stylesheet, and
   set the backdrop on `body`.
2. Lay out the two layers: a floating glass sidebar and toolbar capsule groups,
   with content on solid sheets (§4).
3. Build the controls as capsules (§5). The segmented control needs only
   `--count` and `--index`.
4. Put `tabular-nums` on every number. Open each page with a sentence.
5. Choose your product's own reason for glass and use it in one signature element,
   as Glassfolio does with its solid vs glass bar (§1.5, §6).
6. Swap in your own accent and backdrop hues. Rerun a color-blind check on any
   chart palette, and pick dark values by hand.
7. Test with Reduce Transparency, Increase Contrast, Reduce Motion, dark mode and
   a 375px-wide window.
8. For a native Mac app: use a transparent overlay-title window with native glass
   behind it, and remove the CSS blur inside it (§9).
