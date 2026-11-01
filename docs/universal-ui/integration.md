# Integration contract

This document states what is public and what breaks it. It deliberately contains
no framework tutorials: how to write an effect in React or a lifecycle hook in
Vue is general knowledge, and a tutorial written today is wrong after the next
major release. An agent adapting this kit should translate the contract into
whatever the host project already does.

## Public API

Three things are promised. Everything else may change.

1. **CSS custom properties** — every `--ui-*` token in `tokens.css` and `motion.css`.
2. **Class names** — the `ui-*` classes listed in `docs/components.md`.
3. **Behaviour functions** — the exports of `packages/vanilla`, each returning a
   cleanup function and emitting a `ui-*` `CustomEvent`.

Anything not in those three lists is an implementation detail. If a project
needs more, extend the core and add a showcase state — do not fork a primitive
into application code.

## Minimal usage

```html
<link rel="stylesheet" href="/node_modules/@universal-ui/core/core.css">
<script type="module">
  import { bindDialog, bindSwitch } from '@universal-ui/core/vanilla';

  bindSwitch(document.querySelector('.ui-switch'), {
    onChange: checked => console.log('enabled', checked),
  });
  bindDialog(document.querySelector('#confirm'), {
    triggers: [document.querySelector('#delete'), document.querySelector('#deleteIcon')],
    initialFocus: () => document.querySelector('#cancel'),
  });
</script>
```

Markup uses the classes directly. There is no build step and no template
compiler, so this works unchanged in a plain page, a webview, or a framework.

## Invariants

These are properties of *this* system, not general framework advice. Breaking
them produces bugs that look like the kit's fault.

| Invariant | What breaks if violated |
|---|---|
| Every behaviour must be disposed; binders return the disposer | Duplicate listeners on remount, double-fire on the second mount |
| Bind once per element | Two Tab traps on one dialog; focus restored twice on close |
| Keep animating nodes mounted; toggle `hidden` | Transitions restart, the thumb jumps, focus is lost |
| Keep `dialog`, `popover` and tab panels in the DOM | Focus has nowhere to return to after close |
| The DOM attribute is the source of truth | State mirrored into framework state drifts; the kit reads the attribute |
| Give repeated rows a stable identity | Index keys reorder nodes, which replays entry animation and drops focus |
| Use `aria-controls` to link a tab to its panel | The tablist cannot find its panels and shows them all |

## Theming

Override tokens on `:root`; never override a component rule.

```css
:root {
  --ui-accent: #0f766e;
  --ui-accent-soft: #ccfbf1;
  --ui-space-6: 14px;      /* retunes density globally */
  --ui-text-base: 14px;
}
```

If a component needs a value that no token expresses, the token set is
incomplete — add the token rather than a one-off rule.

The dark palette follows the OS preference (`prefers-color-scheme`) with no
JavaScript. Three intents, all supported:

| Intent | How |
|---|---|
| Follow the system | default — no attribute, or `setTheme('system')` |
| Force dark or light | `setTheme('dark' \| 'light')`, which sets `data-ui-theme` |
| Pin light on a dark system | `data-ui-theme="light"` (the media query respects the pin) |

The theme crossfade re-resolves style for the whole tree (measured ~100ms on a
500-element page; the cost scales with DOM size). On a very large document pass
`setTheme(theme, { animate: false })` to switch instantly.

## Compact shell (narrow viewports)

Below 760px the two-column shell collapses: `.ui-sidebar` is hidden and
`.ui-topbar` — a sticky bar with a hamburger button — becomes the navigation
surface. The kit styles both, but **the drawer markup belongs to the host**,
because only the host knows its page structure:

```html
<header class="ui-topbar">
  <button type="button" class="ui-icon-button" data-shell-nav-trigger
          aria-haspopup="dialog" aria-controls="navSheet" aria-label="Open navigation">
    <svg class="ui-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 4h12M2 8h12M2 12h12"/></svg>
  </button>
  <span class="ui-topbar__brand">App name</span>
</header>

<dialog class="ui-sheet" data-side="left" id="navSheet" aria-label="Navigation">
  <div class="ui-sheet__header"><h2>App name</h2>
    <button type="button" class="ui-icon-button" data-ui-close aria-label="Close navigation">×</button></div>
  <div class="ui-sheet__body"><!-- the page list as .ui-nav-item buttons --></div>
</dialog>
```

```js
bindSheet(document.querySelector('#navSheet'), { trigger: document.querySelector('[data-shell-nav-trigger]') });
```

Choosing a page inside the sheet should close it — one line in the host's
navigation handler (`if (sheet.open) sheet.close()`). At desktop widths the
topbar renders nothing, so this markup is inert above the breakpoint.

## Behaviours worth knowing

- `createToast` without an explicit container uses a shared default stack, so
  repeated calls queue instead of overlapping at the viewport corner.
- `bindThumb` requires the control to establish a positioning context. If the
  control is `position: static` it is set to `relative` automatically (with a
  console warning); `.ui-segmented` and `.ui-tabs` already do this.
- `bindSheet` is the declarative counterpart of `openSheet`: trigger buttons
  open the same dialog repeatedly without stacking listeners.
- Every binder returns a disposer and every component emits a `ui-*` event; see
  `docs/components.md` for the full inventory.

## Motion

- One curve: `var(--ui-ease-standard)`. Never write a `cubic-bezier` in
  application code; the extraction audit fails the build if the kit does.
- Duration tiers only: `--ui-duration-press`, `--ui-duration-standard`,
  `--ui-duration-panel`, `--ui-duration-theme`.
- Every animation needs a `prefers-reduced-motion` path that lands on the
  correct **final state**, not merely a shorter transition.
- Call `startViewTransition()`; it already handles unsupported browsers,
  reduced motion, thrown errors and timeout.

## Accessibility baseline

Any change must keep these.

| Component | Requirement |
|---|---|
| switch | `role="switch"`, `aria-checked`, native click only |
| segmented | roving `tabIndex`, arrows/Home/End, skips disabled |
| tabs | `aria-selected`, panels resolved via `aria-controls` |
| dialog | `aria-labelledby` + `aria-describedby`, focus moves in, `Tab` cycles, Escape closes, focus returns |
| alert dialog | `role="alertdialog"`, initial focus on the safe action |
| popover / menu | `aria-expanded`, arrow keys, Escape/outside close, focus returns |
| command list | `aria-activedescendant`, highlight clamped after filtering |
| checkbox group | parent `aria-checked="mixed"` when partially selected |
| tooltip | `aria-describedby`, Escape dismisses |

## Verifying an integration

```bash
node tests/browser-check.mjs http://localhost:5173/
```

It drives a real browser, asserts behaviour (not appearance) and writes
`test-results/*.png`. Behaviour assertions catch logic regressions; the
screenshots catch what assertions cannot see — a dialog that is closed but still
painted, a thumb placed while its panel was hidden.
