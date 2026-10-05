# PDF Axe — UI design system

Calm, professional desktop UI (in the spirit of Linear / VS Code / Fluent 2):
neutral slate surfaces, **one** accent colour, a 4/8 px spacing grid, line icons.
Everything lives in `ui/style.css` (tokens + shell + base components). Feature
stylesheets (`convert-tools.css`, `pdf-tools.css`, `media-tools.css`,
`rename-templates.css`, `account.css`) must only *use* tokens — never hard-code
colours, radii or shadows.

Works offline: system fonts (Segoe UI Variable → Segoe UI → system-ui), inline
SVG icons, no CDN.

---

## 1. Tokens (`:root`, overridden under `[data-theme="light"]`)

Dark is the default. The theme toggle (`App.applyTheme('light'|'dark')`) sets or
removes `data-theme="light"` on `<html>`. Every token below has a value in both
themes, so if you only use tokens, both themes work automatically.

| Group | Tokens | Notes |
|---|---|---|
| Surfaces | `--bg-app` < `--bg-sidebar` < `--bg-panel` < `--bg-surface` < `--bg-surface-2` | app background → raised. Cards/modals/inputs sit on `--bg-surface`; inputs use `--bg-panel` (dark) / white (light). |
| Overlays | `--bg-hover`, `--bg-active`, `--bg-selected`, `--bg-selected-2`, `--bg-overlay`, `--bg-preview` | translucent; stack on any surface |
| Borders | `--border` (hairline), `--border-strong` (controls), `--border-focus` | |
| Text | `--text-primary`, `--text-secondary`, `--text-muted`, `--text-disabled`, `--text-on-accent`, `--text-danger` | body text = primary, labels = secondary, hints = muted |
| Accent | `--accent`, `--accent-hover`, `--accent-active`, `--accent-subtle`, `--accent-text`, `--accent-ring` | `--accent-text` is the readable accent for text/icons on dark surfaces |
| Status | `--green`, `--yellow`, `--red`, `--orange`, `--purple`, `--teal` + `--green-subtle`, `--yellow-subtle`, `--red-subtle` | status only — never decoration |
| File types | `--ft-pdf --ft-doc --ft-xls --ft-ppt --ft-img --ft-zip --ft-media --ft-code --ft-txt --ft-folder --ft-file` | tint for file icons/badges |
| Spacing | `--space-0` 2 · `--space-1` 4 · `--space-2` 8 · `--space-3` 12 · `--space-4` 16 · `--space-5` 24 · `--space-6` 32 | use for padding/gaps |
| Type | `--fs-xs` 11 · `--fs-sm` 12 · `--fs-base` 13 · `--fs-md` 14 · `--fs-lg` 16 · `--fs-xl` 20 · `--fs-2xl` 24 | body = base, dialog title = lg, page title = xl |
| Weight | `--fw-regular` 400 · `--fw-medium` 500 · `--fw-semibold` 600 · `--fw-bold` 700 | |
| Line height | `--lh-tight` 1.25 · `--lh` 1.45 | |
| Fonts | `--font`, `--font-display` (headings), `--font-mono` | |
| Radius | `--radius-xs` 3 · `--radius-sm` 4 · `--radius` 6 · `--radius-lg` 8 · `--radius-xl` 12 · `--radius-full` | controls = `--radius`, cards/modals = `--radius-xl`, menus = `--radius-lg` |
| Elevation | `--shadow-xs`, `--shadow-sm`, `--shadow`, `--shadow-lg`, `--shadow-popup` (= `--elev-1..3`) | popups/menus/modals use `--shadow-popup` |
| Controls | `--control-h` 30 · `--control-h-sm` 26 · `--control-h-lg` 36 | all buttons/inputs share heights |
| Focus | `--focus-ring` | `box-shadow: var(--focus-ring)` |
| Motion | `--transition` (120 ms), `--transition-slow` (200 ms), `--ease` | |

Legacy names (`--font-size`, `--font-size-sm/lg`, `--hover-bg`, `--accent-muted`,
`--radius`, `--shadow`, `--w-*`, `--h-*`) still work.

---

## 2. Components (all in `style.css`)

### Buttons
```html
<button class="btn">Default</button>
<button class="btn btn-primary"><i data-icon="check"></i>Save</button>
<button class="btn btn-secondary">Secondary</button>
<button class="btn btn-danger"><i data-icon="trash"></i>Delete</button>
<button class="btn btn-ghost">Ghost</button>
<button class="btn btn-sm">Small</button>   <button class="btn btn-lg btn-block">Large, full width</button>
<button class="btn btn-link">Link-style</button>
<button class="icon-btn" title="Refresh" aria-label="Refresh"><i data-icon="refresh"></i></button>
<button class="icon-btn icon-btn-sm">…</button>  <button class="icon-btn icon-btn-danger">…</button>
<button class="btn btn-primary is-loading">Saving</button>   <!-- spinner, text hidden -->
```
- One primary button per view/dialog, placed last (right-most) in the footer.
- Icon + label: put the icon first; `.btn` has `gap: 6px`. Icons 16 px (14 px in `.btn-sm`).
- `.qa-btn` (+ `.primary`, `.danger`) = full-width list action, used in the details
  pane and “More actions”: `<button class="qa-btn"><span class="icon"><i data-icon="copy"></i></span> Copy path</button>`.
  Keep the label as the **last text node** (pdf-tools.js rewrites `lastChild`).

### Form controls
```html
<div class="field">
  <label class="field-label" for="x">Output folder</label>
  <div class="field-row"><input id="x" class="input-text"><button class="btn">Browse…</button></div>
  <div class="field-hint">Where new files go.</div>
  <div class="field-error">Required.</div>
</div>
<div class="input-group">                       <!-- leading icon / trailing button -->
  <span class="input-icon"><i data-icon="search" data-size="14"></i></span>
  <input class="input-text">
  <button class="icon-btn input-action"><i data-icon="eye"></i></button>
</div>
<select class="input-text">…</select>  <textarea class="input-text"></textarea>
<label class="check"><input type="checkbox"> Keep originals</label>
```
- Inside `.modal`, bare `select`, `textarea` and text/number/password/email inputs get the
  input look automatically (but not `width:100%` — add `.input-text` for full width).
- Invalid: add `.is-invalid` to the input and show a `.field-error`.
- Checkboxes/radios are native, tinted with `accent-color`.
- Settings-style two-column form: `.settings-form > .settings-group > .settings-row`.

### Segmented control
```html
<div class="segmented"><button class="seg-btn active">List</button><button class="seg-btn">Grid</button></div>
<div class="segmented segmented-block">…</div>   <!-- full width, equal buttons -->
```
(`.cloud-seg/.cloud-seg-btn` are aliases.) Use instead of radio rows for 2–4 modes.

### Modal
Use `Dialogs.openModal(id, title, bodyHtml, buttons)` or `Dialogs.modal(name, {title, width, body, footer})`.
`buttons: [{label, primary?, danger?, icon?, onClick}]`.
```
.modal-overlay > .modal(.modal-sm|.modal-lg|.modal-xl)
    > .modal-header (.modal-title, .modal-close)  > .modal-body (flex column, gap 12)  > .modal-footer
```
- Body padding 16/24, sections separated by `gap`, not `<br>`.
- Footer: secondary actions left of the primary; `.footer-left` pushes an item to the left.
- Close icon: `${Icons.svg('x', 16)}` (already done in `_openModal`/`_modal`).

### Surfaces & layout
- `.card` (surface, 1 px border, `--radius-xl`, 16 px padding); `.card-row`, `.card-title`,
  `.card-subtitle`, `.card-flush` (no padding, for lists).
- `.section-title` — small uppercase muted label above a group.
- `.page-panel > .page-inner > .page-header(.page-header-icon, .page-title, .page-subtitle)` for full-page panels.
- `.empty-state > .empty-state-icon + .empty-state-title + .empty-state-text (+ .btn)`; `.empty-state-sm`.
- `.callout(.info|.warning|.error|.success)` for inline notices; `.alert-strip` for a full-width bar.
- `.divider`, `.row`, `.col`, `.flex-1`, `.truncate`, `.hidden`, `.text-muted`, `.text-sm`, `.mono`, `.selectable`.

### Feedback
- Toasts: `App.toast(message, 'success'|'error'|'warning'|'info', ms)` — icon is added automatically.
- Badges/pills: `.badge` / `.pill` + `-blue|-green|-yellow|-red|-neutral`; `.pill-dot` adds a status dot.
- Progress: `<div class="progress-wrap"><div class="progress-bar" style="width:40%"></div></div>` (`.indeterminate`).
- Spinner: `<span class="spinner"></span>` (16 px), `.loading-spinner` (28 px block), `.spinner-on-accent` inside primary buttons.
- Logs: `.log-output` (mono, scroll) or `.mono-box`.
- `.kbd` / `<kbd>` for shortcuts; `data-tooltip="text"` for a CSS tooltip (native `title` is fine too).

### Feature-shared components (end of `style.css`)
Used by every feature dialog (convert, compress, merge/split/extract, arrange, QR, crop, AI, templates).

- **Dialog header with icon + subtitle** — `Dialogs.openModal(id, title, body, buttons, {icon, subtitle, tone?, size?})`
  or `Dialogs.modal(name, {title, icon, subtitle, ...})`. Dialogs that build their own overlay use
  `Dialogs.header(title, {icon, subtitle})`. Markup: `.modal-header > .modal-head-icon(.tone-warning|.tone-danger) + .modal-heading(.modal-title, .modal-subtitle) + .modal-close`.
  Subtitle = the file name or a count (“3 images”).
- `Dialogs.setBtn(button, label, icon)` relabels a button without losing its icon (use instead of `textContent`).
- Footer buttons: secondary (Cancel/Close) then the primary with an icon; `.footer-left` for an extra action
  (“Arrange pages…”, “Copy all results”). While working add `.is-loading` to the primary.
- `.field-grid` (two columns of `.field`), `.field-suffix` (“.pdf”, “KB”), `.section-head` (label + small actions),
  `.field.is-dim` (inactive option).
- **Option cards** (presets / modes):
  ```html
  <div class="option-cards">            <!-- .option-cards-1 for one column -->
    <label class="option-card"><input type="radio" name="x" value="a" checked>
      <span class="option-card-icon"><i data-icon="compress"></i></span>
      <span class="option-card-body"><span class="option-card-title">Smallest</span>
        <span class="option-card-desc">Images at 72 dpi…</span>
        <span class="option-card-meta"><span class="pill pill-green">Largest saving</span></span></span>
      <span class="option-card-check"><i data-icon="check" data-size="10"></i></span></label>
  </div>
  ```
- **Reorder list**: `.reorder-list > .reorder-row` with `.reorder-grip` (grip-vertical icon), `.reorder-num`,
  `.reorder-thumb` (image or `Icons.file()`), `.reorder-name` (name + `<small>` path), a pill, and
  `.reorder-actions` (`.icon-btn-sm` up/down/remove). Drag states: `.is-dragging`, `.drop-before`, `.drop-after`.
- **Progress**: `.progress-block > .progress-wrap + .progress-label` (status line; may hold a `.spinner`).
- **Results**: summary `.callout.success|warning|error` containing `svg + .callout-body(.callout-title + text)`,
  then `.result-list > .result-row(.ok|.kept|.warn|.err) > .result-icon + .result-name + .result-detail`;
  sizes as `.size-change` (“4.0 MB → **1.1 MB**”) plus a `.pill` with the percentage.
- **Switch**: `<label class="switch"><input type="checkbox"><span class="switch-track"></span>Label</label>`.
- **Swatches**: `.swatches > button.swatch(.active)` with `--swatch` set per option.
- **Keyboard hints**: `.kbd-hints > span > .kbd…` (bottom row of command-palette lists);
  `mark.hl` highlights the matched text.
- `.log-output.log-tall` — 220 px activity log for long jobs (Smart Split, OCR).
- Hidden-select pattern: a visible `.segmented`/swatch group can mirror a hidden `<select id>` so existing
  code reading `.value` / listening for `change` keeps working (convert-tools `_segSelect`, AI section).

### Menus
`.menu` / `.context-menu` container, `.menu-item`/`.ctx-item` rows (30 px) with
`.icon`/`.ctx-icon`, `.ctx-label`, `.ctx-shortcut`; `.menu-sep`/`.ctx-sep`; `.danger` rows.
`ContextMenu` items: `_item(menu, 'icon-name', 'Label', 'Shortcut', fn)`.

---

## 3. Icons — `ui/js/icons.js` (loaded first)

Lucide-style line icons (ISC, credited in the file): 24 viewBox, `stroke="currentColor"`,
stroke-width 1.75 — they inherit the text colour.

```js
Icons.svg('folder')            // '<svg class="i i-folder" width="16" …>'
Icons.svg('trash', 14, 'text-red')
Icons.file(entry, 16)          // tinted file-type icon for {is_dir, ext|name}
Icons.fileType('.pdf')         // { icon:'file-pdf', tone:'pdf', label:'PDF' }
Icons.logo(32)                 // PDF Axe brand mark
Icons.hydrate(el)              // usually not needed — see below
```
In HTML strings just write `<i data-icon="name" data-size="14"></i>`; a MutationObserver
hydrates placeholders as soon as they are inserted (static HTML and dynamic dialogs alike).

**Names:** folder, folder-open, folder-plus, folder-input, file, file-text, file-pdf,
file-image, file-sheet, file-slides, file-archive, file-video, file-audio, file-code, files,
image, images, hard-drive, home, archive, arrow-left/right/up/down, chevron-left/right/up/down,
chevrons-up-down, chevrons-down-up, external-link, link, search, list, grid, sidebar, maximize,
minimize, zoom-in, zoom-out, scan-fit, more-horizontal, more-vertical, grip-vertical, undo,
redo, copy, clipboard, paste, scissors, pencil, text-cursor, case, tag, templates, list-checks,
trash, restore, plus, minus, merge, combine, split, layers, extract, compress, convert,
arrow-left-right, qr-code, scan, scan-text, crop, rotate-ccw, rotate-cw, shirt, sparkles,
compare, app-window, sun, moon, settings, user, user-circle, log-out, log-in, mail, lock, key,
eye, eye-off, cloud, cloud-upload, cloud-download, cloud-off, download, upload, refresh, sync,
play, pause, stop, server, monitor, credit-card, shield-check, globe, palette, check,
check-circle, x, x-circle, alert-triangle, alert-circle, info, loader, clock.
Aliases: close, delete, remove, gear, reload, cut, rename, edit, arrange, pdf, warning, error,
success, more, repeat, suit, ai, qr, open, review, move, theme, account.

Sizes: 16 px default (buttons, menus, lists), 14 px in small buttons/inputs, 20 px in the
sidebar/page headers, 24–28 px inside empty-state tiles.

---

## 4. Do / Don't

**Do**
- Use tokens for every colour, radius, shadow and spacing value.
- Use one `btn-primary` per dialog; destructive confirmations use `btn-danger`.
- Use sentence case for labels (“Merge with other files…”, not “Merge With Other Files…”)
  and an ellipsis `…` when the action opens another step.
- Keep dialogs ≤ 640 px wide unless they show a page grid; use `.modal-lg/.modal-xl` for that.
- Give icon-only buttons a `title` and `aria-label`.
- Show progress with `.progress-wrap` + a status line, not a growing log, where possible.

**Don't**
- No emoji as icons (they render inconsistently and look unprofessional) — use `Icons`.
- No hard-coded hex colours (`#000`, `#fff` on accent excepted via `--text-on-accent`).
- No inline `font-size`/`padding` soup in JS templates — add a small class to the feature CSS.
- Don't show API cost figures anywhere in the UI. (The AI section shows only the output file name,
  “Saves as photo_suit.jpg”.)
- Don't rename element ids that JS queries.
