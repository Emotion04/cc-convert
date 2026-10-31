const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';
const FOCUSABLE = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.(REDUCED_MOTION_QUERY).matches === true;

function emit(target, name, detail) {
  target.dispatchEvent(new CustomEvent(name, { bubbles: true, detail }));
}

function focusableWithin(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(element =>
    !element.hidden && element.getAttribute('aria-hidden') !== 'true' && element.getClientRects().length > 0,
  );
}

export function setTheme(theme, { root = document.documentElement, duration = 360, animate = true } = {}) {
  if (!['light', 'dark', 'system'].includes(theme)) throw new TypeError(`Unsupported theme: ${theme}`);
  const previous = root.dataset.uiTheme;
  if (previous === theme || (!previous && theme === 'system')) return;

  const canAnimate = animate && !prefersReducedMotion();
  if (canAnimate) root.classList.add('ui-theme-transition');
  if (theme === 'system') delete root.dataset.uiTheme;
  else root.dataset.uiTheme = theme;

  if (canAnimate) {
    // The class comes off on whichever happens first. A bare `{ once: true }`
    // listener only detaches when transitionend fires; if the timer removes the
    // class first, no transition ends and the listener stays on the root for the
    // rest of the session — one more per theme change. Both paths settle here.
    let settled = false;
    let timer;
    const settle = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      root.classList.remove('ui-theme-transition');
      root.removeEventListener('transitionend', onEnd);
    };
    const onEnd = event => { if (event.target === root) settle(); };
    timer = window.setTimeout(settle, duration);
    root.addEventListener('transitionend', onEnd);
  }
  emit(root, 'ui-theme-change', { theme });
}

export function bindSwitch(button, { onChange } = {}) {
  if (!(button instanceof Element)) throw new TypeError('bindSwitch expects an Element');
  button.setAttribute('role', 'switch');
  if (!button.hasAttribute('aria-checked')) button.setAttribute('aria-checked', 'false');

  // Native buttons already synthesize click for Space/Enter. Do not add a
  // second keydown toggle or keyboard activation will toggle twice.
  const onClick = () => {
    const checked = button.getAttribute('aria-checked') !== 'true';
    button.setAttribute('aria-checked', String(checked));
    onChange?.(checked, button);
    emit(button, 'ui-change', { checked });
  };
  button.addEventListener('click', onClick);
  return () => button.removeEventListener('click', onClick);
}

export function bindSegmented(root, { itemSelector = 'button', onChange } = {}) {
  if (!(root instanceof Element)) throw new TypeError('bindSegmented expects an Element');
  const items = [...root.querySelectorAll(itemSelector)];
  if (!items.length) return () => {};
  const enabled = () => items.filter(item => !item.disabled && item.getAttribute('aria-disabled') !== 'true');
  const thumb = bindThumb(root);
  thumb.follow(`${itemSelector}[aria-pressed="true"]`);

  root.setAttribute('role', root.getAttribute('role') || 'group');
  let current = items.find(item => item.getAttribute('aria-pressed') === 'true' || item.getAttribute('aria-checked') === 'true') || enabled()[0];
  const sync = selected => {
    current = selected;
    items.forEach(item => {
      const active = item === selected;
      item.setAttribute('aria-pressed', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    thumb.place(selected);
  };
  const select = item => {
    if (!enabled().includes(item)) return;
    sync(item);
    const value = item.dataset.value ?? item.textContent.trim();
    onChange?.(value, item);
    emit(root, 'ui-change', { value, item });
  };
  const onClick = event => {
    const item = event.target.closest(itemSelector);
    if (item && root.contains(item)) select(item);
  };
  const onKeyDown = event => {
    const item = event.target.closest(itemSelector);
    if (!item || !root.contains(item)) return;
    const available = enabled();
    const index = available.indexOf(item);
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = available[(index + 1) % available.length];
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = available[(index - 1 + available.length) % available.length];
    if (event.key === 'Home') next = available[0];
    if (event.key === 'End') next = available.at(-1);
    if (next) {
      event.preventDefault();
      select(next);
      next.focus();
    }
  };
  sync(current);
  root.addEventListener('click', onClick);
  root.addEventListener('keydown', onKeyDown);
  return () => {
    root.removeEventListener('click', onClick);
    root.removeEventListener('keydown', onKeyDown);
    thumb.destroy();
  };
}

export function openPopover(popover, anchor, {
  placement = 'bottom-start',
  focus = true,
  closeOnOutside = true,
  closeOnEscape = true,
} = {}) {
  if (!(popover instanceof HTMLElement) || !(anchor instanceof HTMLElement)) {
    throw new TypeError('openPopover expects HTMLElement popover and anchor');
  }
  if (!popover.hidden) return () => {};

  popover.hidden = false;
  popover.style.position = 'fixed';
  popover.style.maxHeight = `min(70vh, ${Math.max(120, window.innerHeight - 24)}px)`;
  const place = () => {
    const rect = anchor.getBoundingClientRect();
    const box = popover.getBoundingClientRect();
    const gap = 6;
    const left = placement.endsWith('end') ? rect.right - box.width : rect.left;
    let top = placement.startsWith('top') ? rect.top - box.height - gap : rect.bottom + gap;
    if (top + box.height > window.innerHeight - 12 && rect.top > box.height + gap) top = rect.top - box.height - gap;
    top = Math.max(12, Math.min(window.innerHeight - box.height - 12, top));
    popover.style.left = `${Math.max(12, Math.min(window.innerWidth - box.width - 12, left))}px`;
    popover.style.top = `${top}px`;
  };
  place();
  let closed = false;
  // Scroll and resize fire far faster than the frame rate, and each run forces
  // two layout reads. Coalescing into one frame keeps a scroll from queueing a
  // reposition per event.
  let frame = 0;
  const schedule = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; if (!closed) place(); });
  };
  anchor.setAttribute('aria-expanded', 'true');
  if (!anchor.hasAttribute('aria-controls') && popover.id) anchor.setAttribute('aria-controls', popover.id);

  const focusables = () => focusableWithin(popover);
  if (focus) (focusables()[0] || popover).focus?.();
  const closePopover = ({ restoreFocus = false, reason = 'programmatic' } = {}) => {
    if (closed) return;
    closed = true;
    popover.hidden = true;
    anchor.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', onOutside, true);
    document.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('resize', schedule);
    window.removeEventListener('scroll', schedule, true);
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    if (restoreFocus && anchor.isConnected) anchor.focus();
    emit(popover, 'ui-popover-close', { reason });
  };
  const onOutside = event => {
    if (closeOnOutside && !popover.contains(event.target) && !anchor.contains(event.target)) closePopover({ reason: 'outside' });
  };
  const onKeyDown = event => {
    const list = focusables();
    if (event.key === 'Escape' && closeOnEscape) {
      event.preventDefault();
      closePopover({ restoreFocus: true, reason: 'escape' });
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!list.length) return;
      event.preventDefault();
      const index = list.indexOf(document.activeElement);
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      list[(index + delta + list.length) % list.length].focus();
    } else if (event.key === 'Home' && list.length) {
      event.preventDefault(); list[0].focus();
    } else if (event.key === 'End' && list.length) {
      event.preventDefault(); list.at(-1).focus();
    } else if (event.key === 'Tab') {
      closePopover({ reason: 'tab' });
    }
  };
  document.addEventListener('pointerdown', onOutside, true);
  document.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('resize', schedule);
  window.addEventListener('scroll', schedule, true);
  emit(popover, 'ui-popover-open', { anchor });
  return closePopover;
}

export function bindDialog(dialog, { trigger, triggers, close, initialFocus } = {}) {
  if (!(dialog instanceof HTMLDialogElement)) throw new TypeError('bindDialog expects a <dialog> element');
  let opener = null;
  let disposed = false;
  // Several buttons may open the same dialog. Accepting them in one call is
  // what prevents binding the dialog twice — two Tab traps would both run and
  // focus would be restored twice on close.
  const openers = [...(trigger ? [trigger] : []), ...(triggers ?? [])].filter(Boolean);
  const focusInside = () => focusableWithin(dialog);
  const open = event => {
    event?.preventDefault();
    if (dialog.open) return;
    opener = event?.currentTarget instanceof HTMLElement ? event.currentTarget : openers[0] ?? document.activeElement;
    dialog.showModal();
    queueMicrotask(() => {
      const target = typeof initialFocus === 'function' ? initialFocus(dialog) : initialFocus;
      (target || focusInside()[0] || dialog).focus?.();
    });
    emit(dialog, 'ui-dialog-open', {});
  };
  const closeDialog = () => { if (dialog.open) dialog.close(); };
  const onCancel = event => { event.preventDefault(); closeDialog(); };
  const onClick = event => { if (event.target === dialog) closeDialog(); };
  const onKeyDown = event => {
    if (event.key !== 'Tab') return;
    const list = focusInside();
    if (!list.length) { event.preventDefault(); dialog.focus(); return; }
    const first = list[0];
    const last = list.at(-1);
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  const onClose = () => {
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    emit(dialog, 'ui-dialog-close', { returnValue: dialog.returnValue });
  };
  openers.forEach(element => element.addEventListener('click', open));
  close?.addEventListener('click', closeDialog);
  const closeButtons = [...dialog.querySelectorAll('[data-ui-close]')];
  closeButtons.forEach(button => button.addEventListener('click', closeDialog));
  dialog.addEventListener('cancel', onCancel);
  dialog.addEventListener('click', onClick);
  dialog.addEventListener('keydown', onKeyDown);
  dialog.addEventListener('close', onClose);
  return () => {
    if (disposed) return;
    disposed = true;
    openers.forEach(element => element.removeEventListener('click', open));
    close?.removeEventListener('click', closeDialog);
    closeButtons.forEach(button => button.removeEventListener('click', closeDialog));
    dialog.removeEventListener('cancel', onCancel);
    dialog.removeEventListener('click', onClick);
    dialog.removeEventListener('keydown', onKeyDown);
    dialog.removeEventListener('close', onClose);
    closeDialog();
  };
}

export function startViewTransition(update, options = {}) {
  const timeout = options.timeout ?? 1500;
  const documentRef = options.documentRef ?? (typeof document === 'undefined' ? null : document);
  const motionPreference = options.motionPreference ?? prefersReducedMotion;
  const timers = options.timers ?? (typeof window === 'undefined' ? globalThis : window);
  let didUpdate = false;
  let updateResult;
  const updateOnce = () => {
    if (didUpdate) return updateResult;
    didUpdate = true;
    updateResult = update();
    return updateResult;
  };
  if (motionPreference() || typeof documentRef.startViewTransition !== 'function') {
    return Promise.resolve().then(updateOnce);
  }

  let timer;
  let transition;
  try {
    transition = documentRef.startViewTransition(updateOnce);
  } catch {
    return Promise.resolve().then(updateOnce);
  }
  const fallback = new Promise((resolve, reject) => {
    timer = timers.setTimeout(() => Promise.resolve().then(updateOnce).then(resolve, reject), timeout);
  });
  return Promise.race([transition.finished, fallback])
    .catch(error => {
      if (didUpdate) throw error;
      return updateOnce();
    })
    .finally(() => timers.clearTimeout(timer));
}

/* A module-level stack backs the default `createToast` container. `.ui-toast`
   is position:fixed, so bare toasts appended to the body would all land on the
   same viewport coordinates; the stack is what makes repeated calls readable. */
let defaultToastStack = null;

export function createToast(message, { timeout = 3200, tone = 'default', container } = {}) {
  if (!container) {
    if (!defaultToastStack?.element.isConnected) defaultToastStack = createToastStack();
    container = defaultToastStack.element;
  }
  const toast = document.createElement('div');
  toast.className = `ui-toast ui-toast--${tone}`;
  toast.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
  toast.textContent = String(message);
  container.append(toast);
  const timer = window.setTimeout(() => toast.remove(), timeout);
  return { element: toast, dismiss() { window.clearTimeout(timer); toast.remove(); } };
}

/* ── menu ───────────────────────────────────────────────────────────────────
   Implements the menu pattern used by tray and context menus: roving focus,
   arrow/Home/End navigation, typeahead, Enter/Space activation, Escape to
   close, and menuitemcheckbox / menuitemradio state. Non-menu rows (separators,
   group labels) are skipped rather than being focus targets.                */

export function bindMenu(root, { onSelect, onClose, typeahead = true } = {}) {
  if (!(root instanceof Element)) throw new TypeError('bindMenu expects an Element');
  const all = () => [...root.querySelectorAll('[role^="menuitem"]')];
  const items = () => all().filter(item => item.getAttribute('aria-disabled') !== 'true' && !item.hidden);
  if (!root.getAttribute('role')) root.setAttribute('role', 'menu');

  const focusAt = index => {
    const list = items();
    if (!list.length) return;
    const next = list[(index + list.length) % list.length];
    list.forEach(item => { item.tabIndex = item === next ? 0 : -1; });
    next.focus();
  };
  const activate = item => {
    if (!item || item.getAttribute('aria-disabled') === 'true') return;
    const role = item.getAttribute('role');
    if (role === 'menuitemcheckbox' || role === 'menuitemradio') {
      if (role === 'menuitemradio') {
        all().filter(other => other.getAttribute('role') === 'menuitemradio').forEach(other => other.setAttribute('aria-checked', 'false'));
      }
      item.setAttribute('aria-checked', item.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    }
    onSelect?.(item, { checked: item.getAttribute('aria-checked') });
    emit(root, 'ui-select', { item, value: item.dataset.value ?? item.textContent.trim(), checked: item.getAttribute('aria-checked') });
    if (role === 'menuitem') onClose?.('select');
  };

  let buffer = '';
  let bufferTimer = null;
  const onKeyDown = event => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    if (event.key === 'ArrowDown') { event.preventDefault(); focusAt(index + 1); return; }
    if (event.key === 'ArrowUp') { event.preventDefault(); focusAt(index - 1); return; }
    if (event.key === 'Home') { event.preventDefault(); focusAt(0); return; }
    if (event.key === 'End') { event.preventDefault(); focusAt(list.length - 1); return; }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(document.activeElement); return; }
    if (event.key === 'Escape') { event.preventDefault(); onClose?.('escape'); return; }
    if (typeahead && event.key.length === 1 && /\S/.test(event.key)) {
      buffer += event.key.toLowerCase();
      window.clearTimeout(bufferTimer);
      bufferTimer = window.setTimeout(() => { buffer = ''; }, 500);
      const match = list.find(item => (item.textContent ?? '').trim().toLowerCase().startsWith(buffer));
      if (match) match.focus();
    }
  };
  const onClick = event => {
    const item = event.target.closest('[role^="menuitem"]');
    if (item && root.contains(item)) activate(item);
  };

  root.addEventListener('keydown', onKeyDown);
  root.addEventListener('click', onClick);
  // Roving tabindex: exactly one item is tabbable, the rest are reachable with
  // the arrow keys. Leaving the others at the default 0 makes Tab walk through
  // every entry instead of leaving the menu.
  const first = items()[0];
  all().forEach(item => { item.tabIndex = item === first ? 0 : -1; });
  return () => {
    window.clearTimeout(bufferTimer);
    root.removeEventListener('keydown', onKeyDown);
    root.removeEventListener('click', onClick);
  };
}

/* ── toast stack ──────────────────────────────────────────────────────────── */

export function createToastStack({ container = document.body, position = 'bottom-end' } = {}) {
  const stack = document.createElement('div');
  stack.className = 'ui-toast-stack';
  stack.dataset.position = position;
  stack.setAttribute('role', 'region');
  stack.setAttribute('aria-label', 'Notifications');
  container.append(stack);
  return {
    element: stack,
    push(message, { timeout = 3200, tone = 'default' } = {}) {
      const toast = document.createElement('div');
      toast.className = `ui-toast ui-toast--${tone}`;
      toast.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
      toast.textContent = String(message);
      stack.append(toast);
      const timer = window.setTimeout(() => toast.remove(), timeout);
      return { element: toast, dismiss() { window.clearTimeout(timer); toast.remove(); } };
    },
    clear() { stack.replaceChildren(); },
    destroy() { stack.remove(); },
  };
}

/* ── sheet ----------------------------------------------------------------
   A <dialog> in modal mode, so focus trapping, Escape and inert background
   come from the platform rather than from hand-written key handling.       */

export function openSheet(element, { trigger, onClose } = {}) {
  if (!(element instanceof HTMLDialogElement)) throw new TypeError('openSheet expects a <dialog> element');
  const opener = trigger ?? document.activeElement;
  const close = () => element.close();
  const onClick = event => { if (event.target === element) close(); };
  // Same close-button contract as bindDialog, so a sheet body can carry a
  // header "×" without the consumer wiring it by hand.
  const closeButtons = [...element.querySelectorAll('[data-ui-close]')];
  const onButtonClose = () => close();
  const onPlatformClose = () => {
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    onClose?.();
    emit(element, 'ui-sheet-close', {});
  };
  element.addEventListener('click', onClick);
  closeButtons.forEach(button => button.addEventListener('click', onButtonClose));
  element.addEventListener('close', onPlatformClose);
  if (!element.open) element.showModal();
  emit(element, 'ui-sheet-open', {});
  return () => {
    element.removeEventListener('click', onClick);
    closeButtons.forEach(button => button.removeEventListener('click', onButtonClose));
    element.removeEventListener('close', onPlatformClose);
    if (element.open) element.close();
  };
}

/* ── sheet (declarative) ---------------------------------------------------
   bindDialog's counterpart: trigger buttons open the same dialog repeatedly
   without stacking openSheet instances, and [data-ui-close] buttons close it.
   Use openSheet for a one-shot imperative open instead.                     */

export function bindSheet(element, { trigger, triggers, onClose } = {}) {
  if (!(element instanceof HTMLDialogElement)) throw new TypeError('bindSheet expects a <dialog> element');
  const openers = [...(trigger ? [trigger] : []), ...(triggers ?? [])].filter(Boolean);
  let running = null;
  const open = event => {
    event?.preventDefault();
    if (element.open) return;
    // Disposing the previous run first keeps at most one live listener set on
    // the dialog no matter how often it opens and closes.
    running?.();
    running = openSheet(element, {
      trigger: event?.currentTarget instanceof HTMLElement ? event.currentTarget : openers[0] ?? document.activeElement,
      onClose,
    });
  };
  openers.forEach(button => button.addEventListener('click', open));
  return () => {
    openers.forEach(button => button.removeEventListener('click', open));
    running?.();
    running = null;
  };
}

/* ── slider ---------------------------------------------------------------
   The platform range input keeps keyboard support and value announcement; we
   only mirror the value into an <output> and a CSS custom property so the
   filled track can be drawn without a second source of truth.              */

export function bindSlider(input, { output, format = value => String(value) } = {}) {
  if (!(input instanceof HTMLInputElement) || input.type !== 'range') {
    throw new TypeError('bindSlider expects an <input type="range">');
  }
  const target = typeof output === 'string' ? document.querySelector(output) : output;
  const sync = () => {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const value = Number(input.value);
    const ratio = max === min ? 0 : (value - min) / (max - min);
    input.style.setProperty('--ui-range-fill', `${ratio * 100}%`);
    input.setAttribute('aria-valuetext', format(value));
    if (target) target.textContent = format(value);
  };
  input.addEventListener('input', sync);
  sync();
  return () => input.removeEventListener('input', sync);
}

/* ── resizer --------------------------------------------------------------
   Pointer drag for the mouse, arrow keys for the keyboard. Keyboard support
   is not optional: a drag-only splitter is unusable without a pointer.      */

export function bindResizer(handle, target, { min = 180, max = 480, step = 16, storageKey, onChange } = {}) {
  if (!(handle instanceof Element) || !(target instanceof HTMLElement)) {
    throw new TypeError('bindResizer expects a handle Element and a target HTMLElement');
  }
  let dragging = false;
  // `localStorage.setItem` is synchronous and hits disk, so it must not run on
  // every pointermove — that is a write per frame for the whole drag. The value
  // is held in memory during the gesture and committed once when it ends.
  let pending = null;
  const clamp = width => Math.max(min, Math.min(max, width));
  const persist = () => {
    if (!storageKey || pending === null) return;
    try { localStorage.setItem(storageKey, String(pending)); } catch { /* private mode */ }
    pending = null;
  };
  const apply = width => {
    const next = clamp(width);
    target.style.width = `${next}px`;
    if (storageKey) pending = next;
    handle.setAttribute('aria-valuenow', String(next));
    onChange?.(next);
    return next;
  };

  if (storageKey) {
    const saved = Number(localStorage.getItem(storageKey));
    // Restoring the saved width is not an edit, so nothing to write back.
    if (Number.isFinite(saved) && saved > 0) apply(saved);
    pending = null;
  }
  if (!handle.hasAttribute('role')) handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-valuemin', String(min));
  handle.setAttribute('aria-valuemax', String(max));
  handle.setAttribute('aria-valuenow', String(target.getBoundingClientRect().width || min));
  handle.tabIndex = 0;

  const onPointerDown = event => {
    dragging = true;
    handle.dataset.dragging = 'true';
    // Throws NotFoundError if the pointer is already gone (a synthetic event, a
    // cancelled touch). Losing capture is recoverable; letting the throw escape
    // would skip preventDefault and leave the drag half-started.
    try { handle.setPointerCapture?.(event.pointerId); } catch { /* capture is best-effort */ }
    event.preventDefault();
  };
  const onPointerMove = event => {
    if (!dragging) return;
    const rect = target.getBoundingClientRect();
    apply(rect.left <= 0 ? rect.width + event.movementX : event.clientX - rect.left);
  };
  const onPointerUp = () => {
    if (!dragging) return;
    dragging = false;
    delete handle.dataset.dragging;
    persist();
  };
  const onKeyDown = event => {
    const current = target.getBoundingClientRect().width;
    if (event.key === 'ArrowLeft') { event.preventDefault(); apply(current - step); persist(); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); apply(current + step); persist(); }
    else if (event.key === 'Home') { event.preventDefault(); apply(min); persist(); }
    else if (event.key === 'End') { event.preventDefault(); apply(max); persist(); }
  };

  handle.addEventListener('pointerdown', onPointerDown);
  handle.addEventListener('pointermove', onPointerMove);
  handle.addEventListener('pointerup', onPointerUp);
  handle.addEventListener('pointercancel', onPointerUp);
  handle.addEventListener('keydown', onKeyDown);
  return () => {
    // A drag interrupted by unmount still leaves an uncommitted width; write it
    // before detaching so the next mount restores what the user last saw.
    persist();
    handle.removeEventListener('pointerdown', onPointerDown);
    handle.removeEventListener('pointermove', onPointerMove);
    handle.removeEventListener('pointerup', onPointerUp);
    handle.removeEventListener('pointercancel', onPointerUp);
    handle.removeEventListener('keydown', onKeyDown);
  };
}

/* ── drag sort ------------------------------------------------------------
   Pointer-based reordering so it works on touch, where HTML5 drag events do
   not fire. Order is committed on drop and emitted once.                   */

export function bindDragSort(container, { itemSelector = '[data-drag-item]', handleSelector = '[data-drag-handle]', onOrder } = {}) {
  if (!(container instanceof Element)) throw new TypeError('bindDragSort expects an Element');
  let dragged = null;
  // Pointermove fires continuously, but the drop indicator only changes when
  // the pointer crosses into another row or the other half of one. Tracking
  // the last point and the last mark keeps elementFromPoint and the DOM writes
  // off the frames in between.
  let lastPoint = null;
  let marked = null;
  const items = () => [...container.querySelectorAll(itemSelector)];
  const mark = (item, position) => {
    if (marked?.item === item && marked?.position === position) return;
    if (marked) delete marked.item.dataset.drop;
    marked = item ? { item, position } : null;
    if (item) item.dataset.drop = position;
  };
  const clear = () => { mark(null, null); };

  const onPointerDown = event => {
    const handle = event.target.closest(handleSelector);
    if (!handle) return;
    const item = handle.closest(itemSelector);
    if (!item) return;
    dragged = item;
    lastPoint = { x: event.clientX, y: event.clientY };
    item.dataset.dragging = 'true';
    try { handle.setPointerCapture?.(event.pointerId); } catch { /* capture is best-effort */ }
    event.preventDefault();
  };
  const onPointerMove = event => {
    if (!dragged) return;
    if (lastPoint && lastPoint.x === event.clientX && lastPoint.y === event.clientY) return;
    lastPoint = { x: event.clientX, y: event.clientY };
    const under = document.elementFromPoint(event.clientX, event.clientY)?.closest(itemSelector);
    if (!under || under === dragged) { clear(); return; }
    const rect = under.getBoundingClientRect();
    mark(under, event.clientY < rect.top + rect.height / 2 ? 'before' : 'after');
  };
  const onPointerUp = () => {
    if (!dragged) return;
    const target = marked?.item ?? null;
    if (target && target.dataset.drop) {
      const after = target.dataset.drop === 'after';
      target.insertAdjacentElement(after ? 'afterend' : 'beforebegin', dragged);
      onOrder?.(items().map(item => item.dataset.dragItem));
      emit(container, 'ui-reorder', { order: items().map(item => item.dataset.dragItem) });
    }
    delete dragged.dataset.dragging;
    dragged = null;
    lastPoint = null;
    clear();
  };

  container.addEventListener('pointerdown', onPointerDown);
  container.addEventListener('pointermove', onPointerMove);
  container.addEventListener('pointerup', onPointerUp);
  container.addEventListener('pointercancel', onPointerUp);
  return () => {
    container.removeEventListener('pointerdown', onPointerDown);
    container.removeEventListener('pointermove', onPointerMove);
    container.removeEventListener('pointerup', onPointerUp);
    container.removeEventListener('pointercancel', onPointerUp);
  };
}

/* ── arm button -----------------------------------------------------------
   First click arms, second click fires. Disarms on timeout, blur or Escape,
   so a destructive action cannot complete by accident several seconds later. */

export function bindArmButton(button, {
  armLabel = 'Click again to confirm',
  timeout = 4000,
  onConfirm,
} = {}) {
  if (!(button instanceof Element)) throw new TypeError('bindArmButton expects an Element');
  // Keep the original child nodes rather than an HTML string: replacing
  // textContent would destroy icons and any listeners attached to them, and
  // re-parsing markup cannot restore listeners at all.
  const originalNodes = [...button.childNodes];
  let timer = null;
  const showLabel = text => button.replaceChildren(document.createTextNode(text));
  const disarm = () => {
    window.clearTimeout(timer);
    delete button.dataset.armed;
    button.replaceChildren(...originalNodes);
    button.setAttribute('aria-pressed', 'false');
  };
  const onClick = () => {
    if (button.dataset.armed === 'true') {
      disarm();
      onConfirm?.();
      emit(button, 'ui-confirm', {});
      return;
    }
    button.dataset.armed = 'true';
    showLabel(armLabel);
    button.setAttribute('aria-pressed', 'true');
    emit(button, 'ui-arm', {});
    timer = window.setTimeout(disarm, timeout);
  };
  const onBlur = () => disarm();
  const onKeyDown = event => { if (event.key === 'Escape') disarm(); };

  button.addEventListener('click', onClick);
  button.addEventListener('blur', onBlur);
  button.addEventListener('keydown', onKeyDown);
  return () => {
    disarm();
    button.removeEventListener('click', onClick);
    button.removeEventListener('blur', onBlur);
    button.removeEventListener('keydown', onKeyDown);
  };
}

/* ── sparkline ------------------------------------------------------------
   A polyline is enough for the shape; the area fill is what makes it read as
   a chart rather than a scribble. Values are normalised to the box, with a
   flat series centred instead of dividing by zero.                         */

export function drawSparkline(svg, values, { width = 120, height = 34, padding = 2 } = {}) {
  const ns = 'http://www.w3.org/2000/svg';
  const data = values.map(Number).filter(Number.isFinite);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.replaceChildren();
  if (data.length < 2) return;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = (width - padding * 2) / (data.length - 1);
  const points = data.map((value, index) => {
    const x = padding + index * stepX;
    const y = max === min
      ? height / 2
      : height - padding - ((value - min) / span) * (height - padding * 2);
    return [x, y];
  });

  const line = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
  const area = `${line} L${points.at(-1)[0].toFixed(2)} ${height} L${points[0][0].toFixed(2)} ${height} Z`;

  const areaPath = document.createElementNS(ns, 'path');
  areaPath.setAttribute('class', 'area');
  areaPath.setAttribute('d', area);
  const linePath = document.createElementNS(ns, 'path');
  linePath.setAttribute('class', 'line');
  linePath.setAttribute('d', line);
  const dot = document.createElementNS(ns, 'circle');
  dot.setAttribute('cx', points.at(-1)[0].toFixed(2));
  dot.setAttribute('cy', points.at(-1)[1].toFixed(2));
  dot.setAttribute('r', '2');
  svg.append(areaPath, linePath, dot);
}

/* ── copy button ─────────────────────────────────────────────────────────────
   Clipboard writes need a user gesture and can be blocked by permission or a
   non-secure origin, so the fallback path matters: select the text and tell
   the user instead of failing silently.                                     */

export async function copyText(text) {
  const value = String(text);
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch { /* fall through to the legacy path */ }
  }
  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.append(field);
  field.select();
  let ok = false;
  try { ok = document.execCommand?.('copy') === true; } catch { ok = false; }
  field.remove();
  return ok;
}

export function bindCopyButton(button, { text, resetAfter = 1500 } = {}) {
  if (!(button instanceof Element)) throw new TypeError('bindCopyButton expects an Element');
  let timer = null;
  const onClick = async () => {
    const value = typeof text === 'function' ? text() : text ?? button.dataset.copy ?? button.textContent;
    const ok = await copyText(value);
    button.dataset.copied = ok ? 'true' : 'false';
    emit(button, 'ui-copy', { ok, value });
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { delete button.dataset.copied; }, resetAfter);
  };
  button.addEventListener('click', onClick);
  return () => {
    window.clearTimeout(timer);
    button.removeEventListener('click', onClick);
    delete button.dataset.copied;
  };
}

/* ── checkbox group ──────────────────────────────────────────────────────────
   A parent checkbox summarises its children: checked when all are checked,
   indeterminate when some are, unchecked when none. Clicking the parent sets
   every child to the parent's new value; clicking a child recomputes the
   parent. Without this the parent drifts out of sync, which is the classic
   "select all says checked but two rows are off" bug.                      */

export function bindCheckboxGroup(parent, children, { onChange } = {}) {
  if (!(parent instanceof HTMLInputElement) || parent.type !== 'checkbox') {
    throw new TypeError('bindCheckboxGroup expects a checkbox <input> as the parent');
  }
  const list = [...children];
  const items = () => list.filter(item => item instanceof HTMLInputElement && !item.disabled);
  const sync = () => {
    const enabled = items();
    const on = enabled.filter(item => item.checked).length;
    parent.checked = enabled.length > 0 && on === enabled.length;
    parent.indeterminate = on > 0 && on < enabled.length;
    parent.setAttribute('aria-checked', parent.indeterminate ? 'mixed' : String(parent.checked));
  };
  const onParent = () => {
    items().forEach(item => { item.checked = parent.checked; });
    parent.indeterminate = false;
    onChange?.(parent.checked, null);
    emit(parent, 'ui-change', { checked: parent.checked, scope: 'group' });
  };
  const onChild = event => {
    sync();
    onChange?.(event.target.checked, event.target);
    emit(parent, 'ui-change', { checked: event.target.checked, scope: 'item', item: event.target });
  };

  parent.addEventListener('change', onParent);
  list.forEach(child => child.addEventListener('change', onChild));
  sync();
  return () => {
    parent.removeEventListener('change', onParent);
    list.forEach(child => child.removeEventListener('change', onChild));
  };
}

/* ── sliding thumb ─────────────────────────────────────────────────────────
   A single thumb glides between options instead of each option lighting up on
   its own. Details that matter and are easy to get wrong:

   - The same element survives selection changes, so the motion is continuous.
   - A control that is hidden (offsetParent null) or zero-width is skipped;
     a ResizeObserver re-places the thumb *still* when it becomes laid out, so
     a hidden panel never animates in from a stale position.
   - Wrapping strips (multi-row) move on both axes and take the option's
     height, so the thumb does not stretch across two rows.
   - The easing is read from the motion token; no curve is hard-coded here.
   - Reduced motion places the thumb instead of animating it.              */

const THUMB_CLASS = 'ui-thumb';

function standardEase() {
  if (typeof getComputedStyle !== 'function') return 'ease';
  const value = getComputedStyle(document.documentElement).getPropertyValue('--ui-ease-standard').trim();
  return value || 'ease';
}

function applyThumb(thumb, g) {
  thumb.style.transform = g.y ? `translate(${g.x}px, ${g.y}px)` : `translateX(${g.x}px)`;
  thumb.style.width = `${g.w}px`;
  thumb.style.height = `${g.h}px`;
}

export function bindThumb(control, { duration = 280, reduced = prefersReducedMotion } = {}) {
  // The thumb is absolutely positioned against the control; a static control
  // would anchor it to the nearest positioned ancestor instead. Setting
  // `relative` on a static element does not move it in the flow, so this is
  // safe to apply — but say so, because it is the host's DOM being touched.
  if (typeof getComputedStyle === 'function' && getComputedStyle(control).position === 'static') {
    control.style.position = 'relative';
    console.warn('bindThumb: the control was position:static; set to relative so the thumb anchors to the control.');
  }
  let thumb = control.querySelector(`:scope > .${THUMB_CLASS}`);
  if (!thumb) {
    thumb = document.createElement('span');
    thumb.className = THUMB_CLASS;
    thumb.setAttribute('aria-hidden', 'true');
    control.prepend(thumb);
  }

  let last = null;
  let observer = null;

  const geometry = target => {
    if (!target || !target.offsetParent || !target.offsetWidth) return null;
    const wraps = control.clientHeight > target.offsetHeight + 8;
    return {
      x: target.offsetLeft,
      y: wraps ? target.offsetTop : 0,
      w: target.offsetWidth,
      h: target.offsetHeight,
    };
  };

  const place = (target, { animate = true } = {}) => {
    const to = geometry(target);
    if (!to) return;
    thumb.getAnimations().forEach(animation => { if (animation.id === THUMB_CLASS) animation.cancel(); });

    if (!last || !animate || reduced()) {
      thumb.dataset.still = 'true';
      applyThumb(thumb, to);
      delete thumb.dataset.still;
    } else {
      const animation = thumb.animate([
        {
          transform: last.y ? `translate(${last.x}px, ${last.y}px)` : `translateX(${last.x}px)`,
          width: `${last.w}px`,
          height: `${last.h}px`,
        },
        {
          transform: to.y ? `translate(${to.x}px, ${to.y}px)` : `translateX(${to.x}px)`,
          width: `${to.w}px`,
          height: `${to.h}px`,
        },
      ], { duration, easing: standardEase(), fill: 'none' });
      animation.id = THUMB_CLASS;
      animation.onfinish = () => applyThumb(thumb, to);
    }
    last = to;
  };

  const follow = selectedSelector => {
    observer?.disconnect();
    observer = new ResizeObserver(() => {
      const target = control.querySelector(selectedSelector);
      if (target) place(target, { animate: false });
    });
    observer.observe(control);
  };

  return {
    place,
    follow,
    destroy() {
      observer?.disconnect();
      thumb.remove();
    },
  };
}

/* ── tabs ──────────────────────────────────────────────────────────────────
   Implements the ARIA tabs pattern with automatic activation: moving focus
   with the arrow keys also selects, which is the behaviour a desktop header
   switch expects. Because selection follows focus, arrow navigation must
   never skip a disabled tab silently — it wraps over the enabled set only. */

export function bindTabs(root, { tabSelector = '[role="tab"]', onChange } = {}) {
  if (!(root instanceof Element)) throw new TypeError('bindTabs expects an Element');
  const tabs = [...root.querySelectorAll(tabSelector)];
  if (!tabs.length) return () => {};
  if (!root.getAttribute('role')) root.setAttribute('role', 'tablist');
  const thumb = bindThumb(root);
  thumb.follow(`${tabSelector}[aria-selected="true"]`);

  // Panels are commonly siblings of the tablist, not children — the ARIA
  // pattern allows either. Resolve each panel by the tab's aria-controls so
  // the component works in both layouts instead of silently finding nothing.
  const owners = tabs[0].getRootNode?.() ?? document;
  const panelFor = tab => {
    const id = tab.getAttribute('aria-controls');
    if (!id) return null;
    const scope = owners.querySelector ? owners : document;
    return scope.getElementById?.(id) ?? document.getElementById(id) ?? null;
  };
  const allPanels = () => [...new Set(tabs.map(panelFor).filter(Boolean))];

  const enabled = () => tabs.filter(tab => !tab.disabled && tab.getAttribute('aria-disabled') !== 'true');

  const select = (tab, { focus = false } = {}) => {
    if (!enabled().includes(tab)) return;
    tabs.forEach(candidate => {
      const active = candidate === tab;
      candidate.setAttribute('aria-selected', String(active));
      candidate.tabIndex = active ? 0 : -1;
    });
    const target = panelFor(tab);
    allPanels().forEach(panel => {
      const active = panel === target;
      panel.hidden = !active;
      panel.setAttribute('aria-hidden', String(!active));
    });
    thumb.place(tab);
    if (focus) tab.focus();
    const value = tab.dataset.value ?? tab.id ?? tab.textContent.trim();
    onChange?.(value, tab);
    emit(root, 'ui-change', { value, tab });
  };

  const onKeyDown = event => {
    const tab = event.target.closest(tabSelector);
    if (!tab || !root.contains(tab)) return;
    const list = enabled();
    const index = list.indexOf(tab);
    if (index < 0) return;
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = list[(index + 1) % list.length];
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = list[(index - 1 + list.length) % list.length];
    else if (event.key === 'Home') next = list[0];
    else if (event.key === 'End') next = list.at(-1);
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(tab, { focus: true }); return; }
    if (next) { event.preventDefault(); select(next, { focus: true }); }
  };
  const onClick = event => {
    const tab = event.target.closest(tabSelector);
    if (tab && root.contains(tab)) select(tab);
  };

  root.addEventListener('keydown', onKeyDown);
  root.addEventListener('click', onClick);
  const current = tabs.find(tab => tab.getAttribute('aria-selected') === 'true') ?? enabled()[0];
  if (current) select(current);
  return () => {
    root.removeEventListener('keydown', onKeyDown);
    root.removeEventListener('click', onClick);
    thumb.destroy();
  };
}

/* ── section navigation ────────────────────────────────────────────────────
   The scroll counterpart to tabs: every section is visible, the nav reports
   position. Clicking moves the page; the observer keeps the marker honest as
   the user scrolls.

   Deliberately not a tablist. `aria-current="location"` means "you are here",
   which is true while several sections are on screen; `aria-selected` would
   claim one is chosen while the others are equally visible.               */

export function bindSectionNav(nav, {
  itemSelector = '[data-section-target]',
  rootMargin = '-15% 0px -70% 0px',
  smooth = true,
  reduced = prefersReducedMotion,
  onActivate,
} = {}) {
  if (!(nav instanceof Element)) throw new TypeError('bindSectionNav expects an Element');
  const items = [...nav.querySelectorAll(itemSelector)];
  if (!items.length) return () => {};

  const targets = items
    .map(item => ({ item, section: document.getElementById(item.dataset.sectionTarget) }))
    .filter(entry => entry.section);
  const byId = new Map(targets.map(entry => [entry.section.id, entry]));

  const mark = id => {
    for (const { item, section } of targets) {
      if (section.id === id) item.setAttribute('aria-current', 'location');
      else item.removeAttribute('aria-current');
    }
  };

  const onClick = event => {
    const item = event.target.closest(itemSelector);
    if (!item || !nav.contains(item)) return;
    const section = document.getElementById(item.dataset.sectionTarget);
    if (!section) return;
    event.preventDefault();
    section.scrollIntoView({ behavior: smooth && !reduced() ? 'smooth' : 'auto', block: 'start' });
    mark(section.id);
    onActivate?.(section.id, section);
    emit(nav, 'ui-section-change', { id: section.id });
  };

  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver(entries => {
        const visible = entries
          .filter(entry => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) mark(visible[0].target.id);
      }, { rootMargin })
    : null;

  for (const { section } of targets) {
    section.classList.add('ui-section');
    observer?.observe(section);
  }
  nav.addEventListener('click', onClick);

  // Mark the first section before any scrolling happens, so the nav is never
  // blank on load.
  if (targets[0]) mark(targets[0].section.id);

  return () => {
    nav.removeEventListener('click', onClick);
    observer?.disconnect();
  };
}

/* ── disclosure ──────────────────────────────────────────────────────────── */

export function bindDisclosure(root, { triggerSelector = '[data-ui-disclosure-trigger]', contentSelector = '[data-ui-disclosure-content]', onChange } = {}) {
  if (!(root instanceof Element)) throw new TypeError('bindDisclosure expects an Element');
  const triggers = [...root.querySelectorAll(triggerSelector)];
  if (!triggers.length) return () => {};

  const toggle = (trigger, open) => {
    const content = trigger.parentElement?.querySelector(contentSelector);
    if (!content) return;
    trigger.setAttribute('aria-expanded', String(open));
    content.hidden = !open;
    if (trigger.id && content.id) trigger.setAttribute('aria-controls', content.id);
    onChange?.(open, trigger);
    emit(trigger, 'ui-change', { open });
  };

  const onKeyDown = event => {
    const trigger = event.target.closest(triggerSelector);
    if (!trigger || !root.contains(trigger)) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle(trigger, trigger.getAttribute('aria-expanded') !== 'true');
    }
  };
  const onClick = event => {
    const trigger = event.target.closest(triggerSelector);
    if (trigger && root.contains(trigger)) toggle(trigger, trigger.getAttribute('aria-expanded') !== 'true');
  };

  root.addEventListener('click', onClick);
  root.addEventListener('keydown', onKeyDown);
  return () => {
    root.removeEventListener('click', onClick);
    root.removeEventListener('keydown', onKeyDown);
  };
}

/* ── command list ──────────────────────────────────────────────────────────
   A filterable list used by pickers and command menus. The state machine is
   intentionally explicit: filter → recompute the visible set → clamp the
   active index → only then handle Enter. The clamp is what prevents the
   classic bug where filtering leaves the highlight pointing at a row that
   is no longer rendered, so Enter selects something the user cannot see.   */

export function bindCommandList(root, { input, itemSelector = '[role="option"]', emptySelector = '[data-ui-command-empty]', onChange } = {}) {
  if (!(root instanceof Element)) throw new TypeError('bindCommandList expects an Element');
  if (!(input instanceof HTMLInputElement)) throw new TypeError('bindCommandList expects an input element');
  const allItems = () => [...root.querySelectorAll(itemSelector)];
  let active = -1;

  const visible = () => allItems().filter(item => !item.hidden);
  const clamp = () => {
    const list = visible();
    if (!list.length) { active = -1; return; }
    if (active < 0 || active >= list.length) active = 0;
  };
  const paint = () => {
    const list = visible();
    list.forEach((item, index) => {
      const on = index === active;
      item.setAttribute('aria-selected', String(on));
      if (on) item.id ||= `ui-command-${Math.random().toString(36).slice(2, 8)}`;
    });
    input.setAttribute('aria-activedescendant', list[active]?.id ?? '');
    const empty = root.querySelector(emptySelector) ?? (root.parentElement?.querySelector(emptySelector) ?? null);
    if (empty) empty.hidden = list.length > 0;
    empty?.setAttribute('aria-hidden', String(list.length > 0));
  };
  const filter = () => {
    const query = input.value.trim().toLowerCase();
    allItems().forEach(item => {
      const text = (item.dataset.value ?? item.textContent ?? '').toLowerCase();
      item.hidden = query.length > 0 && !text.includes(query);
    });
    active = -1;
    clamp();
    paint();
    emit(root, 'ui-filter', { query, count: visible().length });
  };
  const choose = item => {
    if (!item) return;
    const value = item.dataset.value ?? item.textContent.trim();
    onChange?.(value, item);
    emit(root, 'ui-choose', { value, item });
  };

  const onInput = () => filter();
  const onKeyDown = event => {
    const list = visible();
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!list.length) return;
      active = (active + 1) % list.length;
      paint();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (!list.length) return;
      active = (active - 1 + list.length) % list.length;
      paint();
    } else if (event.key === 'Home') {
      if (!list.length) return;
      event.preventDefault(); active = 0; paint();
    } else if (event.key === 'End') {
      if (!list.length) return;
      event.preventDefault(); active = list.length - 1; paint();
    } else if (event.key === 'Enter') {
      if (active < 0) return;
      event.preventDefault();
      choose(list[active]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      emit(root, 'ui-command-dismiss', {});
    }
  };
  const onClick = event => {
    const item = event.target.closest(itemSelector);
    if (item && root.contains(item) && !item.hidden) choose(item);
  };

  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-autocomplete', 'list');
  if (!input.hasAttribute('aria-controls') && root.id) input.setAttribute('aria-controls', root.id);

  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKeyDown);
  root.addEventListener('click', onClick);
  clamp();
  paint();
  return () => {
    input.removeEventListener('input', onInput);
    input.removeEventListener('keydown', onKeyDown);
    root.removeEventListener('click', onClick);
  };
}

/* ── tooltip ─────────────────────────────────────────────────────────────── */

export function createTooltip(anchor, content, { delay = 350, placement = 'top' } = {}) {
  if (!(anchor instanceof Element)) throw new TypeError('createTooltip expects an Element anchor');
  let node = null;
  let timer = null;

  const hide = () => {
    window.clearTimeout(timer);
    document.removeEventListener('scroll', hide, true);
    node?.remove();
    node = null;
    anchor.removeAttribute('aria-describedby');
  };
  const show = () => {
    if (node) return;
    node = document.createElement('div');
    node.className = 'ui-tooltip';
    node.id = `ui-tooltip-${Math.random().toString(36).slice(2, 8)}`;
    node.setAttribute('role', 'tooltip');
    node.textContent = String(content);
    document.body.append(node);

    const rect = anchor.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    const top = placement === 'bottom' ? rect.bottom + 6 : rect.top - box.height - 6;
    node.style.left = `${Math.max(8, Math.min(window.innerWidth - box.width - 8, rect.left + rect.width / 2 - box.width / 2))}px`;
    node.style.top = `${Math.max(8, Math.min(window.innerHeight - box.height - 8, top))}px`;
    anchor.setAttribute('aria-describedby', node.id);
    // A fixed tooltip cannot follow its anchor through a scroll; hiding on
    // scroll is what the native title does and avoids per-frame repositioning.
    document.addEventListener('scroll', hide, { capture: true, passive: true });
  };
  const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(show, delay); };
  const onKeyDown = event => { if (event.key === 'Escape') hide(); };

  anchor.addEventListener('mouseenter', schedule);
  anchor.addEventListener('mouseleave', hide);
  anchor.addEventListener('focus', schedule);
  anchor.addEventListener('blur', hide);
  anchor.addEventListener('keydown', onKeyDown);
  return () => {
    hide();
    anchor.removeEventListener('mouseenter', schedule);
    anchor.removeEventListener('mouseleave', hide);
    anchor.removeEventListener('focus', schedule);
    anchor.removeEventListener('blur', hide);
    anchor.removeEventListener('keydown', onKeyDown);
  };
}
