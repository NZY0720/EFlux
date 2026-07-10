# Accessibility & keyboard audit — Phase A tail

Audited 2026-07-10 against the `docs/PRODUCT_PLAN_V1.md` §11 checklist. This is
a source review of the named flows, not a substitute for assistive-technology and
automated-browser testing at the supported breakpoints.

| Flow / criterion | Result | Evidence and follow-up |
| --- | --- | --- |
| Login: labels, keyboard submit, focus | Pass | Semantic `label` elements, required inputs, and native submit buttons in [`Login.tsx`](../frontend/src/pages/Login.tsx). Global `:focus-visible` styling is in [`index.css`](../frontend/src/styles/index.css). Error text should gain `role="alert"` in the future. |
| Deploy wizard: labels and keyboard path | Partial — trivial fixes applied | Added accessible names for the two name inputs, pressed state for preset choices, and expanded state for disclosure controls in [`VppDeploy.tsx`](../frontend/src/pages/VppDeploy.tsx). Step changes do not move focus to the new step heading, and Continue does not validate the current step before changing it; treat both as a later interaction pass. |
| Submit flow: source selection and confirmation | Partial | Native buttons/radios and clear text labels in [`CompetitionSubmit.tsx`](../frontend/src/pages/CompetitionSubmit.tsx) are keyboard-operable. The source-card buttons should expose selected state (`aria-pressed` or radio semantics), and errors should be announced with a live region. |
| Filters and sortable data | Partial | Filter controls are native buttons/selects in [`Participants.tsx`](../frontend/src/pages/Participants.tsx) and [`Leaderboard.tsx`](../frontend/src/pages/Leaderboard.tsx). Add `aria-pressed` to active filter chips, an accessible label for the session select, and `aria-sort` to sortable table headers. |
| Drawer | Pass | [`NavBar.tsx`](../frontend/src/components/NavBar.tsx) provides a labelled modal drawer, initial focus, Escape close, Tab containment, and focus restoration. The desktop Explore popup still needs arrow-key/menuitem navigation if it remains a `role="menu"`. |
| Tabs | Partial | [`Leaderboard.tsx`](../frontend/src/pages/Leaderboard.tsx) has `tablist`, `tab`, and `aria-selected`; it needs roving arrow-key navigation plus `aria-controls` / associated tabpanels. |
| Focus visibility | Pass | Shared focus-visible outline and input focus ring cover native interactive controls in [`index.css`](../frontend/src/styles/index.css). Verify chart canvas controls separately when they are introduced. |
| Dark/light contrast tokens | Review required | The semantic theme tokens in [`index.css`](../frontend/src/styles/index.css) support both themes and preserve a visible ring. No measured WCAG 2.2 AA contrast calculation was run for every token/composited `color-mix()` state; verify status text, muted text, and accent-on-soft combinations with a contrast tool before release. |

## Scope notes

- Participants and Arena implementation were not modified (parallel work scope).
- No changes were made to market or landing pages.
- The remaining findings are intentionally documented rather than expanded into a
  broader component/API migration in this engineering-tail task.
