# Writing help pages

Help topics are registered in `help_content.py` (id, title, category, summary,
keywords, related topics, `ui_context`). Each topic's body is an HTML fragment
in `content/<topic_id>.html`. The help browser renders it with `ui.html`, adds
heading anchors and the "On This Page" list, and `assets/help.js` turns on the
interactive pieces.

`content/twitch_setup.html` is the reference page. Copy its patterns.

## Rules

- **Colors come from the theme manager only.** Use the classes below. Never put
  hex/rgb colors in pages (inline `style` colors fail the tests). New CSS goes in
  `assets/css/` and may only use `var(--color-*)` (plus `#fff` for text on
  primary buttons, like Quasar does in the app).
- **No `<script>`**. Scripts in `ui.html` never run. Use the `help.js` contracts.
- **No `<h1>`**. The browser shows the title and summary. Start at `<h2>`; use
  `<h3>` under it. Every h2/h3 appears in "On This Page".
- **Mocks must match the real UI.** Read the code that builds the screen
  (`modules/uiwindows/...`) and copy real labels, button text, icons, order,
  placeholder text and status strings. Do not invent controls.
- Mocks are illustrations. Clicking a mock switch only flips the picture. Say so
  in the caption when it could be mistaken for a real control.
- Links: `<a href="help:topic_id">` for other topics; plain `https://` links
  open in the user's browser.
- Write plainly and briefly. Keep every fact from the old page unless the code
  proves it wrong.

## Tokens

| Token | Renders |
|---|---|
| `{{icon:settings}}` | Material icon (any name from fonts.google.com/icons) |
| `{{brand:twitch}}` | Service mark: `twitch youtube spotify psn obs discord streamlabs streamelements internet` |

## Document components (`hx-*`)

```html
<p class="hx-lead">Opening sentence, slightly larger.</p>

<div class="hx-meta">
  <span>{{icon:schedule}} About 5 minutes</span>
  <span>{{icon:place}} <span class="hx-path"><span>Settings</span><span>Twitch</span></span></span>
</div>

<span class="hx-path"><span>Settings</span><span>Twitch</span></span>   <!-- UI location -->
<span class="hx-copy" data-copy="http://localhost:17563">http://localhost:17563</span>  <!-- click to copy -->
<kbd>Ctrl</kbd>
<span class="hx-badge hx-ok">Connected</span>   <!-- hx-ok hx-warn hx-err hx-info -->

<div class="hx-callout hx-tip"><p>…</p></div>      <!-- hx-tip hx-note hx-warning hx-important -->

<div class="hx-table-wrap"><table>…</table></div>

<!-- Step-by-step guide with saved progress. data-guide must be unique across all pages. -->
<section class="hx-guide" data-guide="spotify_setup" data-title="Spotify setup">
  <div class="hx-step"><h3>Step title</h3><p>…</p></div>
  …
</section>

<!-- Tick-able checklist, saved. data-checklist must be unique. -->
<ul class="hx-checklist" data-checklist="spotify_prereqs"><li>…</li></ul>

<!-- Tabs (also works inside mocks) -->
<div class="hx-tabs">
  <div class="hx-tab-list"><button data-tab="win" class="is-active">Windows</button><button data-tab="mac">macOS</button></div>
  <div class="hx-tab-panel" data-panel="win">…</div>
  <div class="hx-tab-panel" data-panel="mac">…</div>
</div>

<details class="hx-details"><summary>Question</summary><p>Answer</p></details>

<div class="hx-cards">
  <a class="hx-card" href="help:topic">{{icon:bolt}}<span><strong>Title</strong><span class="hx-card-text">One line</span></span></a>
</div>

<div class="hx-split"><div>mock</div><div>explanation</div></div>   <!-- side by side, stacks when narrow -->

<figure class="hx-figure">           <!-- add hx-static when nothing in the mock is clickable -->
  <div class="hm">…mock…</div>
  <figcaption>What this shows / what to click.</figcaption>
</figure>
<ol class="hx-annotations"><li data-n="1">Explains marker 1</li></ol>
```

`pre` blocks get a Copy button automatically.

## Mock UI kit (`hm-*`)

Wrap every mock in `<div class="hm">`. Pieces mirror `modules/ui_styles.py`:

| Class | Real element |
|---|---|
| `hm-titlebar` | optional window bar (first child) |
| `hm-tabs` > `hm-tab[.is-active]`, `hm-tabs-tail` | main tab strip (Activity Feed … Settings) + help/bell icons |
| `hm-frame` | bordered content frame under a tab strip |
| `hm-subtabs` > `hm-subtab[.is-active]` | Settings/Alerts/Chatbot sub-tabs |
| `hm-panel`, `hm-panel-title` | `settings_inner_panel()` |
| `hm-card` | `content-card` |
| `hm-status-band` > `hm-metric` (label, value, optional sub) | `settings_status_band()` |
| `hm-btn` + `hm-outline` `hm-flat` `hm-danger` `hm-success` `hm-dense` `hm-round` `hm-icon-only` `is-disabled` | `primary_button`, `outline_button`, `destructive_button`, … |
| `hm-actions` | Discard / Save row |
| `hm-field[data-label]` > `hm-value` or `hm-placeholder`; modifiers `hm-select` `hm-textarea` `is-focused` `is-error` `hm-base` `hm-nolabel` | Quasar outlined dense input/select |
| `hm-secret` | masked value (use `••••`) |
| `hm-switch[.is-on]` > `<i></i>` + label text | `ui.switch` (clickable) |
| `hm-check[.is-on]` > `<i></i>` + label | checkbox (clickable) |
| `hm-slider` with `style="--hm-val:40%"` | slider |
| `hm-expansion[.is-open]` > `hm-expansion-head` + `hm-expansion-body` | `ui.expansion` (clickable) |
| `hm-chip` (`hm-plain`), `hm-badge` (`hm-outline` `hm-badge-ok/warn/err`), `hm-dot` (`hm-ok/warn/err/info`) | chips, badges, status dots |
| `hm-list` > `hm-list-item[.is-selected]`, `table.hm-table` | lists, tables |
| `hm-footer` > `hm-footer-item` | service status footer |
| `hm-toast` (`hm-ok/warn/err`) | one-line notification pop-up |
| `hm-gn-pop` (`is-error/warning/success`, `st-chip/minimal/tinted/glass`), `hm-gn-nc` | full pop-up and notification centre (see `content/notifications.html`) |
| `hm-browser` > `hm-browser-bar` > `hm-browser-url`, then `hm-browser-body` | a third-party web page (Twitch console, Spotify dashboard). Neutral colors, not brand palettes. |
| `hm-marker[data-n]` (`hm-pin` + `style="top/left"` for absolute) | numbered callout, pairs with `hx-annotations` |
| `hm-highlight`, `hm-pulse` | dashed ring / pulsing "click here" |

Layout: `hm-row` (`hm-nowrap hm-between hm-end hm-top`), `hm-col` (`hm-tight`),
`hm-grow`, `hm-grid-2`, `hm-grid-3`, `hm-gap-lg`, `hm-gap-xl`, `hm-stack`.
Text: `hm-h1 hm-h2 hm-label hm-muted hm-xs hm-sm hm-bold hm-mono`, colors
`hm-ok hm-warn hm-err hm-info hm-accent`.

Clickable mock tabs: put `data-tabs` on a container, `data-tab="k"` on the tab
elements and `data-panel="k"` on panels.

Area-specific mock pieces (an activity-feed row, an alert preview, a Spore
Studio canvas…) go in their own `assets/css/20_mock_<area>.css`, scoped under
`.help-doc .hm`, theme variables only. All CSS files load together, so every
area class needs its file's prefix:

| File | Prefix |
|---|---|
| `20_mock_general.css` | `hm-gn-`, `hm-sf-` (status footer), plus older unprefixed names |
| `20_mock_guides_a.css` | `hm-ga-` |
| `20_mock_guides_b.css` | (Stream Deck pieces) |
| `20_mock_integrations.css` | `hm-it-` (Settings service tabs: header with pills and Save, account card, credentials fold as `hm-expansion hm-it-fold`, rows, lists, steps; see `content/twitch_setup.html`. Game hook cards, choice cards, tool rows, dialogs and the Database Explorer: see `content/game_hooks.html`, `content/database.html`) |
| `20_mock_alerts.css` | `hma-` |
| `20_mock_chatbot.css` | `hm-cb-`, `hm-chat` |
| `20_mock_connectors.css` | `hm-cx-` |
| `20_mock_templates.css` | `hm-tp-`, `hm-sc-` |
| `20_mock_spore.css` | `hm-ss-` (Spore Studio editor kit, reuse it) |
| `20_mock_spore_b.css` | `hm-ssb-` |

Help CSS `color:` declarations are made `!important` automatically when loaded
(`help_render.help_page_css`) so they beat the app's light-mode rule that forces
text color on every p/span/div. Write plain declarations.

## Checking your work

```bash
uv run --with pytest python -m pytest tests/test_help_content.py -q
python scripts/help_preview.py <topic_id> --png --slice 1400            # dark theme
python scripts/help_preview.py <topic_id> --theme light --png --slice 1400
```

The preview writes standalone HTML (open it in a browser to click around) and,
with `--png`, screenshots in `logs/help_preview/` (`--out` to change).
