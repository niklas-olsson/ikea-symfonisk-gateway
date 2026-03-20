# Web UI Design Guidelines

This module uses the **Impeccable** design system for frontend work. These guidelines apply to all UI components, pages, and styling in the `ui_web/` directory.

## Design Philosophy

**Commit to a BOLD aesthetic direction.** Avoid generic "AI slop" aesthetics:
- Pick a clear conceptual direction and execute with precision
- Choose distinctive fonts, not Inter/Roboto/system defaults
- Create visual rhythm through varied spacing, not uniform padding
- Embrace asymmetry—left-aligned text feels more designed than centered everything

**The AI Slop Test**: If you showed this UI to someone and said "AI made this," would they believe you immediately? If yes, that's the problem.

## Typography

| Guideline | Do | Don't |
|-----------|-----|-------|
| Fonts | Instrument Sans, Plus Jakarta Sans, Fraunces, Newsreader | Inter, Roboto, Arial, system defaults |
| Scale | Fluid `clamp()` for headings, fixed `rem` for UI | px for body text |
| Hierarchy | 5 sizes with high contrast (xs, sm, base, lg, xl+) | Too many sizes too close together |
| Numbers | `font-variant-numeric: tabular-nums` for data | Variable-width numbers in tables |

## Color & Theme

| Guideline | Do | Don't |
|-----------|-----|-------|
| Color Space | OKLCH for perceptually uniform palettes | HSL |
| Neutrals | Tint toward brand hue (chroma: 0.01) | Pure gray (#808080) |
| Contrast | WCAG AA minimum 4.5:1 body, 3:1 large text | Gray text on colored backgrounds |
| Dark Mode | Lighter surfaces for depth, reduce weight | Inverted light mode with shadows |
| Accents | Use sparingly (10% of visual weight) | Accent color everywhere |

**Never use pure black (#000) or pure white (#fff)**—always tint.

## Spatial Design

| Guideline | Do | Don't |
|-----------|-----|-------|
| Base Unit | 4pt grid (4, 8, 12, 16, 24, 32, 48, 64px) | Arbitrary spacing values |
| Layout | Container queries for components | Viewport-only responsiveness |
| Cards | Use only when content needs distinct boundaries | Wrap everything in cards |
| Nesting | Never nest cards inside cards | Visual noise, flattened hierarchy |

## Motion & Animation

| Guideline | Do | Don't |
|-----------|-----|-------|
| Duration | 100-150ms (instant), 200-300ms (state), 300-500ms (layout) | >500ms for UI feedback |
| Easing | `ease-out-quart` for entrances, `ease-in` for exits | `ease`, bounce, elastic |
| Properties | Animate `transform` and `opacity` only | Animate width, height, padding |
| Reduced Motion | Support `prefers-reduced-motion` | Ignore accessibility |
| Height Animations | Use `grid-template-rows: 0fr → 1fr` | Animate `height` directly |

## Interaction Design

| State | Implementation |
|-------|---------------|
| Default | Base styling |
| Hover | Subtle lift, color shift (pointer only) |
| Focus | Visible ring for keyboard (`:focus-visible`) |
| Active | Pressed appearance, darker |
| Disabled | Reduced opacity, `cursor: not-allowed` |
| Loading | Skeleton screens > spinners |
| Error | Red border, icon, message below field |

**Focus rings are mandatory**—never `outline: none` without `:focus-visible` alternative.

## UX Writing

| Guideline | Example |
|-----------|---------|
| Buttons | "Save changes" not "OK"; "Delete message" not "Yes" |
| Errors | "Email needs @ symbol" not "Invalid input" |
| Empty states | "No projects yet. Create your first one to get started." not "No items" |
| Consistency | Pick one term and stick with it (Delete, not Remove/Trash) |

**Never blame the user**: "This field is required" not "You made an error".

## Framework-Specific Notes

For **htmx** interfaces:
- Use semantic HTML with appropriate ARIA attributes
- Progressive enhancement: work without JS, enhance with htmx
- Skeleton loading states during htmx transitions
- Focus management after content swaps

For **Alpine.js** components:
- Keep logic minimal in `x-data`—delegate to functions
- Use `x-show` with CSS transitions for smooth state changes
- `x-init` for component initialization
- Prefer `@click.window` for global event handlers
