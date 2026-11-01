# Component manifest

This file is the checklist. `tests/contract.test.mjs` parses it and fails when a
row marked `done` has no matching `ui-*` class in the core stylesheet, or when a
behaviour column names a function that is not exported. Adding a component means
adding a row; shipping it means flipping the status.

Status: `done` = styled + behaviour + showcase + browser assertion.
`style` = styled only (no behaviour needed). `todo` = not implemented.

| Component | Core class | Behaviour | Status |
|---|---|---|---|
| App shell | `ui-shell` | — | done |
| View (page) | `ui-view` | — | done |
| Panel (tray) | `ui-panel` | — | done |
| Sidebar | `ui-sidebar` | `bindResizer` | done |
| Sidebar section label | `ui-sidebar__section` | — | style |
| Sidebar brand | `ui-sidebar__brand` | — | style |
| Nav item | `ui-nav-item` | — | done |
| Sub-navigation | `ui-subnav` | — | done |
| Sliding thumb | `ui-thumb` | `bindThumb` | done |
| Page header | `ui-page-header` | — | style |
| Topbar (compact shell) | `ui-topbar` | — | style |
| Toolbar | `ui-toolbar` | — | done |
| Grid | `ui-grid` | — | style |
| Stack | `ui-stack` | — | style |
| Cluster | `ui-cluster` | — | style |
| Breadcrumb | `ui-breadcrumb` | — | done |
| Card | `ui-card` | — | done |
| Row | `ui-row` | — | done |
| Badge | `ui-badge` | — | done |
| Chip | `ui-chip` | — | done |
| Avatar | `ui-avatar` | — | style |
| Icon | `ui-icon` | — | done |
| Link | `ui-link` | — | done |
| Text truncation | `ui-truncate` | — | done |
| Line clamp | `ui-clamp-2` | — | done |
| Empty state | `ui-empty` | — | done |
| Skeleton | `ui-skeleton` | — | done |
| Spinner | `ui-spinner` | — | done |
| Meter | `ui-meter` | — | done |
| Progress ring | `ui-ring` | — | done |
| Status line | `ui-status` | — | done |
| Alert | `ui-alert` | — | done |
| Field error | `ui-field__error` | — | done |
| Keyboard hint | `ui-kbd` | — | done |
| Divider | `ui-divider` | — | done |
| Tile | `ui-tile` | — | done |
| Stat tile | `ui-stat` | — | done |
| Sparkline | `ui-sparkline` | `drawSparkline` | done |
| Button | `ui-button` | — | done |
| Icon button | `ui-icon-button` | — | done |
| Input | `ui-input` | — | done |
| Textarea | `ui-textarea` | — | done |
| Select | `ui-select` | — | done |
| Checkbox | `ui-check` | `bindCheckboxGroup` | done |
| Radio | `ui-radio` | — | done |
| Switch | `ui-switch` | `bindSwitch` | done |
| Segmented | `ui-segmented` | `bindSegmented` | done |
| Slider | `ui-slider` | `bindSlider` | done |
| Tabs | `ui-tabs` | `bindTabs` | done |
| Section nav | `ui-section-nav` | `bindSectionNav` | done |
| Section anchor | `ui-section` | `bindSectionNav` | done |
| Disclosure | `ui-disclosure` | `bindDisclosure` | done |
| Menu | `ui-menu` | `bindMenu` | done |
| Popover | `ui-popover` | `openPopover` | done |
| Dialog | `ui-dialog` | `bindDialog` | done |
| Sheet | `ui-sheet` | `bindSheet`, `openSheet` | done |
| Tooltip | `ui-tooltip` | `createTooltip` | done |
| Toast | `ui-toast` | `createToast` | done |
| Toast stack | `ui-toast-stack` | `createToastStack` | done |
| Command list | `ui-command` | `bindCommandList` | done |
| Copy button | `ui-copy` | `bindCopyButton` | done |
| Arm button | `ui-arm` | `bindArmButton` | done |
| Resizer | `ui-resizer` | `bindResizer` | done |
| Drag sort item | `ui-drag` | `bindDragSort` | done |
| Scroll container | `ui-scroll` | — | style |
| Screen-reader only | `ui-sr-only` | — | style |
| Host chrome | — | host adapter | todo |

## Utilities

Functions without a component of their own. They are still public API, so they
are listed here and the contract test checks that each one is exported.

| Utility | Behaviour |
|---|---|
| Theme setter | `setTheme` |
| Reduced-motion probe | `prefersReducedMotion` |
| View transition runner | `startViewTransition` |
| Clipboard write | `copyText` |

## Host surfaces that cannot be web UI

Native tray menus, window buttons and taskbar jump lists belong to the host
(Go/Wails, Electron, Tauri). The kit provides the *menu* component so an app can
render an equivalent in-window menu, and documents the mapping. It cannot render
the OS menu itself. See `docs/host-surfaces.md`.
