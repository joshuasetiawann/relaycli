---
name: frontend-taste
description: Anti-generic UI rules — spacing, hierarchy, restraint (after joshuasetiawann/taste-skill)
---
Build interfaces that don't look like a template.

- One accent color plus neutrals; never the purple-gradient-on-white AI
  default. Pick the accent from the product's subject matter.
- Real typographic hierarchy: at most two font families, a deliberate size
  scale (e.g. 14/16/20/28/40), generous line-height for body text.
- Spacing comes from a scale (4/8/12/16/24/32/48…), applied consistently;
  cramped-then-random gaps are the #1 generic tell.
- One clear primary action per screen; secondary actions look secondary.
- Consistent radius and shadow scales — pick small ones and reuse them; no
  cards-inside-cards-inside-cards.
- Content before chrome: real labels and realistic data, never lorem ipsum
  or emoji as decoration.
- Must hold up at 360px wide and satisfy WCAG AA contrast.
- Prefer semantic HTML and plain CSS (or the project's existing framework)
  over adding a UI library for one page.
