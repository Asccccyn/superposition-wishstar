# Superposition · 许愿星 — Design System

## Direction

The interface is a quiet night observatory / ephemeris desk for two people, not a neon-space dashboard and not a stack of generic cards. The UI should feel like an instrument for locating private and shared moments: ink-blue surfaces, thin measuring lines, restrained orbit geometry, instrument blue for actions, and warm gold only for states that have become shared.

The local Impeccable concept-seed script was invoked during this build but returned no seed text; the direction contract records that degraded run as `local-roll-no-output-20260914` rather than inventing a seed value.

## Product Hierarchy

1. **Three bottles first.** The opening workspace shows my private bottle, the partner bottle's count-only state, and our shared bottle in one continuous deck.
2. **Ritual actions second.** Writing, requesting, offering, accepting, and special-session actions sit in dedicated work views instead of modal stacks.
3. **Shared history last.** Revealed stars become a chronological star-track with source/date filters and a detail inspector for responses.

## Palette

- Night 0 `#070b15`: page ground.
- Night 1 `#0b1020`: primary dark surface.
- Night 2 `#11182b`: elevated work surface.
- Ink `#edf2fb`: primary text.
- Muted `#9eacc6`: supporting text.
- Line `#28344f`: structural rules and bottle geometry.
- Instrument blue `#72a9ff` / `#4f91fa`: focus and actionable state.
- Shared gold `#e7c474`: shared-history markers only.
- Danger `#ff8b8b`: destructive/error state.

Do not add decorative glass, rainbow gradients, neon glow fields, or soft card shadows as page structure.

## Typography

Body and controls use the platform UI sans stack for legibility. Display hierarchy uses an archival serif stack (`Palatino Linotype`, `Book Antiqua`, `Songti SC`, `STSong`, serif) to separate the observatory/archive voice from operational UI. Headings carry hierarchy directly; do not add eyebrow/kicker labels above them.

## Layout

- Desktop: fixed left rail, fluid central workspace, optional right notification drawer.
- Three-bottle deck: one continuous ruled surface divided into three zones, not three floating cards.
- Mobile: rail disappears and the four primary views become a fixed bottom navigation; bottle zones stack vertically.
- Content actions stay near the content they affect. Use the notification drawer only for lightweight cues, not hidden metadata.

## Geometry and Materials

Use 1px rules, restrained 10–14px control radii, authored SVG line icons, and simple bottle/orbit geometry. The world is clean vector instrumentation, not fake paper, embossed metal, or CSS-generated illustration. Shared-star nodes may use small gold points; private states stay blue/neutral.

## Interaction

- One authored reveal moment is enough; avoid repeated entrance animation on every block.
- Every interactive control requires keyboard focus, hover/active where applicable, disabled state, and human-readable error recovery.
- Browser surfaces are themed: selection, caret, scrollbars, focus rings, and tabular numerals.
- Respect `prefers-reduced-motion`.

## Privacy as Visual Truth

The partner private bottle may expose only count and generic status. Never render hidden star ids, dates, moods, ordering, previews, or decorative pseudo-data. A reveal only becomes gold/shared after the backend has completed a legitimate request, offer, or mutually confirmed session path.

## Responsive Quality Bar

The first desktop viewport should read immediately as three connected bottle zones rather than analytics cards. On phone width, the same hierarchy must survive without horizontal scrolling: stacked bottle zones, thumb-reachable bottom navigation, full-width forms, and the shared-star inspector flowing beneath the timeline.
