# Global style cascade

`../styles.css` is the only JavaScript-facing stylesheet. It imports every file in this
directory in the same order as the frozen V2 visual baseline.

- Put screen-specific rules in the matching feature file.
- Put shared shell, navigation, filter, or tag rules in `shell.css`.
- Put cross-screen disabled, network, session, and toast states in `runtime-states.css`.
- Keep responsive rules and late production overrides in their existing ordered files.
- Do not import feature CSS directly from React modules or add nested `@import` rules.
- A deliberate visual change must update the frozen source hash in
  `tests/styles-structure.test.mjs` after visual review.

The initial split is intentionally mechanical: selectors, declarations, media queries,
keyframes, and cascade order are unchanged.
