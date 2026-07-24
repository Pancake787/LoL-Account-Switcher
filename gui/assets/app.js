/**
 * LoL Account Switcher — app.js
 *
 * View controller for the pywebview GUI.  Listens for Python state pushes via
 * pywebview.state and renders the account list, status bar, and subline.
 *
 * All pywebview.api.* calls are gated inside the `pywebviewready` event handler
 * (Pitfall 3 / RESEARCH.md §3) — the bridge is undefined before that fires.
 *
 * D-14 (Phase boundary 4↔5):
 *   Rank tiles render ONLY Tier and Division from rank_cache.
 *   LP, WR%, and W-L fields are present in the DOM structure but remain
 *   empty/hidden until Phase 5 delivers the extended rank data.
 *
 * Plan 04-04: Added renderStatusBar state machine (idle/switching/error/pending),
 *   UI lock, inline Login-fertig confirm, retry, copy toast, refresh spinner +
 *   stale styling, and on_webview_ready timer start (D-01..D-04, D-16..D-19).
 */

/* ---- i18n (Plan 08-05, ONBOARD-04, D-15/D-16) ---- */

/**
 * Full DE/EN catalog fetched from the shared gui/assets/i18n/strings.json —
 * the SAME file gui/i18n.py reads Python-side (RESEARCH.md Pattern 6).
 * Populated by loadStrings(); stays `{}` on any fetch/parse failure so t()
 * degrades to raw-key fallback rather than leaving the UI blank (Assumption A1).
 * @type {Record<string, Record<string, string>>}
 */
let I18N = {};

/**
 * Currently active language ("de"/"en"), kept in sync with window.state.language
 * by render()'s language-change check below. Defaults to 'en' until the first
 * state push resolves it.
 * @type {string}
 */
let currentLang = 'en';

/**
 * Fetch the shared i18n/strings.json catalog (same-origin, served by
 * pywebview's http_server=True — same mechanism emblems_data.js/fonts already
 * use via relative URLs). Called once before the first render (gates startup
 * so status text/data-i18n labels resolve correctly from frame one).
 * @returns {Promise<void>}
 */
async function loadStrings() {
  try {
    const resp = await fetch('i18n/strings.json');
    I18N = await resp.json();
  } catch (err) {
    // A1 fallback: degrade to raw-key rendering rather than a blank UI.
    console.error('[app.js] loadStrings error — degrading to raw-key fallback', err);
    I18N = {};
  }
}

/**
 * Resolve `key` in the current-language catalog, with {param} interpolation.
 * Mirrors gui/i18n.py's t() — falls back to the raw key when the catalog or
 * the specific key is missing/not yet loaded; never throws.
 * @param {string} key     Dotted catalog key, e.g. "status.done_active".
 * @param {object} [params] Values substituted into {placeholder}s.
 * @returns {string}
 */
function t(key, params) {
  if (!key) return '';
  const template = (I18N[currentLang] && I18N[currentLang][key]) || key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? params[name] : match);
}

/**
 * Switch the active UI language live (D-16 — no restart): translates every
 * static `[data-i18n]` label, then re-renders all dynamic content (status
 * bar, account list, etc.) so the WHOLE UI reflects the new language.
 * @param {string} lang  "de" or "en".
 */
function applyLanguage(lang) {
  currentLang = lang || 'en';
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  // Re-render dynamic content (status bar text, rank-tile "Kein API-Key"
  // hint, etc.) now that currentLang has changed. render()'s own
  // language-change check below will see state.language === currentLang
  // on this call and proceed straight to the normal render path (no loop).
  if (window.pywebview && pywebview.state) {
    render(pywebview.state);
  }
}

/* ---- Design helpers (ported 1:1 from lol-switcher-mobalytics.html) ---- */

/**
 * Tier color map, keyed lowercase.
 *
 * Pitfall 1 fix: rank_service.parse_entries emits UPPERCASE tier strings
 * (e.g. 'GOLD') from the real Riot API, but this map originally used
 * capitalized keys copied from the mockup's demo data ('Gold') — a
 * case-sensitive lookup miss that silently fell back to gray (#888) for
 * every live account. Keys are lowercase; callers normalize via
 * .toLowerCase() before lookup (see rankRow()/emblemOrGem()).
 *
 * @type {Record<string, string>}
 */
const tierColor = {
  iron: '#6f6f6f', bronze: '#b07a4e', silver: '#a8b6c6', gold: '#e7b84f',
  platinum: '#3fc7c2', emerald: '#1fbf73', diamond: '#6fa8ff',
  master: '#c64fd0', grandmaster: '#e84b48', challenger: '#6fd6ff'
};

/**
 * Return the WR colour CSS value for a given win-rate percentage.
 * @param {number} p Win-rate percentage (0–100).
 * @returns {string} CSS colour string.
 */
const wrColor = p => p >= 55 ? 'var(--wr-good)' : p >= 50 ? 'var(--wr-ok)' : 'var(--wr-bad)';

/**
 * Escape a string for safe interpolation into innerHTML (CR-01).
 *
 * All account-derived fields (display_name, riot_id, username) originate from
 * free-text modal inputs and are persisted verbatim in accounts.json.  The
 * controller only rejects path separators — it does NOT strip angle brackets,
 * quotes, or ampersands.  Interpolating such a value into an innerHTML template
 * literal is a stored HTML/JS-injection sink (the WebView has full access to
 * pywebview.api).  Escaping the five HTML-significant characters neutralises
 * both tag injection and attribute breakout (the `"` in data-*="..."), while
 * leaving ordinary names rendered byte-for-byte identical.
 *
 * @param {*} s  Any value (null/undefined → '').
 * @returns {string} HTML-escaped string safe for innerHTML and quoted attributes.
 */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * Render a SVG tier gem for the given hex colour.
 * Ported 1:1 from the approved template.
 * @param {string} color Hex or CSS colour string.
 * @returns {string} SVG HTML string.
 */
function gem(color) {
  return `<svg class="gem" style="color:${color}" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 2 L20 9 L12 22 L4 9 Z" fill="${color}"/>
    <path d="M4 9 H20 M12 2 L8.5 9 M12 2 L15.5 9 M8.5 9 L12 22 M15.5 9 L12 22"
      stroke="rgba(255,255,255,.45)" stroke-width=".8" fill="none"/></svg>`;
}

/**
 * Title-case a tier string for display (e.g. 'GOLD' -> 'Gold', 'UNRANKED' -> 'Unranked').
 * Pitfall 1: the real API always returns uppercase tier strings; this fixes the
 * previously-live ALL-CAPS label bug for real accounts.
 * @param {string} tier Uppercase (or any-case) tier string.
 * @returns {string} Title-case tier string.
 */
function tierDisplayName(tier) {
  const t = String(tier || 'unranked');
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
}

/**
 * Return the official bundled tier emblem `<img>` when available, otherwise
 * fall back to the colored SVG gem (D-05 robustness — RANK-05).
 *
 * `EMBLEM_B64` is defined once by the separately-loaded emblems_data.js
 * <script> (Pitfall 3 — never routed through window.state/Python).
 *
 * @param {string} tier  Tier string in any case (normalized internally).
 * @param {string} color Hex/CSS color used only for the SVG-gem fallback.
 * @returns {string} HTML string for the emblem/gem element.
 */
function emblemOrGem(tier, color) {
  const key = (tier || 'unranked').toLowerCase();
  if (typeof EMBLEM_B64 !== 'undefined' && EMBLEM_B64[key]) {
    return `<img class="gem gem-img" src="data:image/webp;base64,${EMBLEM_B64[key]}" alt="${esc(key)}">`;
  }
  return gem(color); // D-05 fallback — asset missing/failed to load at build time
}

/** SVG icon strings (ported 1:1 from the approved template). */
const ICON = {
  copy:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  edit:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  trash:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>',
  switch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3l4 4-4 4M20 7H8M8 21l-4-4 4-4M4 17h12"/></svg>',
  check:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  // D-19: "Session neu aufnehmen" re-capture button — Feather-style "log-in"
  // glyph (arrow entering a door), matching the existing stroke-based icons.
  recapture: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>'
};

/* ---- UI lock helper (D-03) ---- */

/**
 * Toggle the locked / disabled state for all switch and CRUD controls.
 * Called from render() whenever status === 'switching'.
 *
 * @param {boolean} locked  True to disable controls, false to enable.
 */
function setUiLocked(locked) {
  const listEl = document.getElementById('list');
  if (!listEl) return;
  listEl.querySelectorAll('.btn-switch:not(.is-active), .act').forEach(btn => {
    btn.disabled = locked;
  });
  const addBtn = document.getElementById('addBtn');
  if (addBtn) addBtn.disabled = locked;
}

/* ---- Status-bar state machine (D-01..D-04) ---- */

/** Last username passed to switch_account — used by the retry button (D-04). */
let _lastSwitchUsername = null;

/**
 * Render the status bar as a four-state machine:
 *   idle     → mint dot, default "Bereit" text, no action buttons
 *   switching→ mint pulsing dot, step text from status_message, UI locked, no buttons
 *   error    → red dot, red error text, retry button (D-04)
 *   pending  → yellow dot, instruction text, Login-fertig + Abbrechen buttons (D-02)
 *
 * The step-by-step progress text comes verbatim from controller._set_status messages
 * via window.state.status_message — JS only renders, never invents steps.
 *
 * @param {object} state  Full pywebview.state (AppState serialised by Python).
 */
function renderStatusBar(state) {
  const bar = document.getElementById('statusbar');
  if (!bar) return;

  const status = state.status || 'idle';
  // Plan 08-05 (ONBOARD-04): resolve status text via the key+params contract
  // so it re-translates live on a language switch (D-16). Falls back to the
  // raw state.status_message only when status_key is entirely absent (safety
  // net for any state pushed before Plan 08-05 wiring — never expected once
  // both sides are on this contract).
  const statusMessage = state.status_key
    ? (t(state.status_key, state.status_params) || null)
    : (state.status_message || null);
  const pendingUser = state.pending_first_login || null;
  const activeUsername = state.active_username || null;

  // Resolve active account display name for idle fallback text
  let activeName = activeUsername;
  if (activeUsername && state.accounts) {
    const active = (state.accounts || []).find(a => a.username === activeUsername);
    if (active) activeName = active.display_name;
  }

  // Determine bar state attribute (drives CSS show/hide of action buttons)
  let barState;
  if (pendingUser) {
    barState = 'pending';
  } else if (status === 'error') {
    barState = 'error';
  } else if (status === 'switching') {
    barState = 'switching';
  } else {
    barState = 'idle';
  }
  bar.setAttribute('data-status', barState);

  // Status text
  const textEl = document.getElementById('status-text');
  if (textEl) {
    if (statusMessage) {
      textEl.textContent = statusMessage;
    } else if (barState === 'idle') {
      textEl.textContent = activeName
        ? `Bereit — ${activeName} ist aktiv.`
        : 'Bereit.';
    } else {
      textEl.textContent = '';
    }
  }
}

/**
 * Inline status-bar "Login fertig" handler (D-02).
 * Called by #btn-confirm-login onclick.
 */
function confirmLogin() {
  if (window.pywebview && pywebview.api) {
    pywebview.api.confirm_first_login()
      .catch(err => console.error('[app.js] confirm_first_login error', err));
  }
}

/**
 * Inline status-bar "Abbrechen" handler (D-02).
 * Called by #btn-cancel-login onclick.
 */
function cancelLogin() {
  if (window.pywebview && pywebview.api) {
    pywebview.api.cancel_first_login()
      .catch(err => console.error('[app.js] cancel_first_login error', err));
  }
}

/**
 * Retry the last switch attempt (D-04).
 * Called by #btn-retry onclick.
 */
function retrySwitch() {
  if (!_lastSwitchUsername || !window.pywebview || !pywebview.api) return;
  pywebview.api.switch_account(_lastSwitchUsername)
    .catch(err => console.error('[app.js] retry switch_account error', err));
}

/* ---- Empty-state renderer (D-18) ---- */

/**
 * Render a styled empty-state hint inside the list container.
 * Shows a German hint above the Account hinzufügen button when there
 * are no accounts.  Removed when accounts exist.
 *
 * @param {HTMLElement} listEl The #list container element.
 */
function renderEmptyState(listEl) {
  listEl.innerHTML = `<div class="empty-state">
    <div class="empty-icon">&#128100;</div>
    <p>Noch keine Accounts</p>
    <span>F&uuml;g deinen ersten Account mit dem Button unten hinzu.</span>
  </div>`;
}

/* ---- SortableJS instance (D-07/D-08) ---- */
let _sortable = null;

/**
 * Initialise (or re-initialise) SortableJS on the account list container.
 * Drag is restricted to the `.drag-handle` element (D-07).
 * @param {HTMLElement} listEl The #list container element.
 */
function initSortable(listEl) {
  if (_sortable) {
    _sortable.destroy();
    _sortable = null;
  }
  if (typeof Sortable === 'undefined') {
    return; // SortableJS not loaded (should not happen in production)
  }
  _sortable = new Sortable(listEl, {
    handle: '.drag-handle',   // D-07: only the ≡ grip initiates drag
    animation: 150,
    onEnd: function () {
      const newOrder = Array.from(listEl.querySelectorAll('.card'))
        .map(el => el.dataset.username);
      if (window.pywebview && pywebview.api) {
        pywebview.api.reorder_accounts(newOrder)
          .catch(err => console.error('[app.js] reorder_accounts error', err));
      }
    }
  });
}

/* ---- Rank row renderer (RANK-03/04/05: Tier, Division, LP, Winrate, W/L) ---- */

/**
 * Build a rank row HTML string.
 *
 * D-07: this function ALWAYS renders a full tile — an empty `{}` rankData
 * (queue has no ranked data) renders as "Unranked" with no LP/WR (caller in
 * card() passes `{}` rather than omitting the tile).
 * D-08: winrate is color-coded via the existing wrColor() thresholds
 * (>=55% green, >=50% yellow, <50% red) and the wr-bar width equals the %.
 * D-09: W/L record uses the English "124W 98L" form (LoL convention),
 * deliberately not translated despite the otherwise-German UI.
 * D-10: Apex tiers (Master/Grandmaster/Challenger) already carry
 * division === "" from the backend, so the label naturally omits it.
 * D-17/D-28: stale rank tiles get the `stale` CSS class for subtle grey-out.
 *
 * @param {{tier?: string, division?: string, lp?: number, wins?: number, losses?: number}} rankData
 *   Rank cache entry for one queue; `{}` means Unranked (D-07).
 * @param {string} queueLabel Human-readable queue label (e.g. "Solo/Duo").
 * @param {boolean} [stale] Whether the rank data is stale (from rank_cache.stale flag).
 * @param {boolean} [noApiKey] Plan 08-04 (D-01): when true, render a dezent
 *   "Kein API-Key" hint instead of a normal rank tile; click opens Settings.
 * @returns {string} HTML string for a `.rank` element.
 */
function rankRow(rankData, queueLabel, stale, noApiKey) {
  // Plan 08-04 (D-01): no key stored anywhere — show a dezent hint tile
  // instead of "Unranked", parallel to the empty-state branch below. Keeps
  // the two-tile grid layout intact (still a `.rank` element).
  if (noApiKey) {
    return `<div class="rank no-api-key" data-open-settings="1" title="API-Key in den Einstellungen hinterlegen">
      <div class="info">
        <div class="queue">${esc(queueLabel)}</div>
        <div class="tier">${esc(t('card.no_api_key'))}</div>
      </div>
    </div>`;
  }

  // Pitfall 1: real API tier strings are always UPPERCASE ('GOLD'); normalize
  // once here so both the color-map lookup and the emblem lookup agree.
  const key = (rankData.tier || 'UNRANKED').toUpperCase();
  const division = rankData.division || '';
  const color = tierColor[key.toLowerCase()] || '#888';
  const tierLabel = division ? `${tierDisplayName(key)} ${division}` : tierDisplayName(key);
  const staleClass = stale ? ' stale' : '';

  const hasRecord = rankData.wins != null && rankData.losses != null;
  let lpText = '';
  let barStyle = 'width:0%;background:var(--wr-ok)';
  let pctText = '';
  let recText = '';
  if (hasRecord) {
    const wins = Number(rankData.wins) || 0;
    const losses = Number(rankData.losses) || 0;
    const lp = Number(rankData.lp) || 0;
    const total = wins + losses;
    const pct = total > 0 ? Math.round((wins / total) * 100) : 0;
    lpText = `${lp} LP`;
    barStyle = `width:${pct}%;background:${wrColor(pct)}`;
    pctText = `${pct}%`;
    recText = `${wins}W ${losses}L`; // D-09: deliberately English, not translated
  }

  // The rank tile is clickable to open the Riot-ID edit modal (v1.0 RiotIdDialog affordance)
  return `<div class="rank editable${staleClass}" title="Riot-ID bearbeiten">
    ${emblemOrGem(key, color)}
    <div class="info">
      <div class="queue">${esc(queueLabel)}</div>
      <div class="tier">${esc(tierLabel)}</div>
      <div class="lp">${esc(lpText)}</div>
      <div class="wr-bar"><i style="${barStyle}"></i></div>
    </div>
    <div class="wr">
      <div class="pct">${esc(pctText)}</div>
      <div class="rec">${esc(recText)}</div>
    </div>
  </div>`;
}

/* ---- Card renderer ---- */

/**
 * Build an account card HTML string.
 *
 * @param {object} acc     Serialised account from window.state.accounts.
 * @param {string} activeUsername  The currently active Riot username.
 * @param {boolean} switching  True when a switch is in progress (disables buttons).
 * @param {boolean} [noApiKey] Plan 08-04 (D-01): true when no API key is stored
 *   anywhere — both rank tiles render the "Kein API-Key" hint instead.
 * @returns {string} HTML string for a `.card` element.
 */
function card(acc, activeUsername, switching, noApiKey) {
  const isActive = acc.username === activeUsername;
  const disabled = switching ? ' disabled' : '';

  const switchBtn = isActive
    ? `<button class="btn-switch is-active" disabled>${ICON.check} Aktiv</button>`
    : `<button class="btn-switch"${disabled} data-switch="${esc(acc.username)}">${ICON.switch} Wechseln</button>`;

  const statusPill = isActive
    ? `<span class="status"><span class="dot"></span> Aktiv</span>`
    : '';

  // IGN = riot_id if set, otherwise fall back to username
  const ign = acc.riot_id || acc.username;

  // D-21: subtle "session possibly expired" hint — driven SOLELY by the
  // acc.session_warning boolean pushed from window.state; never any
  // snapshot content/path (D-22). Visually distinct from both the header
  // #client-status indicator (STATUS-01) and the error-red status bar
  // (Pitfall 4) — muted/informational styling, not alarming.
  const sessionWarningHtml = acc.session_warning
    ? `<div class="session-warning">Session evtl. abgelaufen</div>`
    : '';

  // Build rank tiles — D-07: ALWAYS both tiles (Solo/Duo + Flex), regardless
  // of which queues have data; an empty {} renders as Unranked inside
  // rankRow() itself. D-17/D-28: stale flag applies to both tiles.
  const rankCache = acc.rank_cache || {};
  const isStale = rankCache.stale === true;
  const soloData = rankCache.solo || {};
  const flexData = rankCache.flex || {};
  const ranksBlock = `<div class="ranks">`
    + rankRow(soloData, 'Solo/Duo', isStale, noApiKey)
    + rankRow(flexData, 'Flex', isStale, noApiKey)
    + `</div>`;

  return `<div class="card${isActive ? ' active' : ''}" data-username="${esc(acc.username)}">
    <div class="card-top">
      <div style="display:flex;align-items:flex-start;gap:8px;min-width:0">
        <span class="drag-handle" title="Verschieben">&#8801;</span>
        <div class="who">
          <div class="name-row"><span class="name">${esc(acc.display_name)}</span>${statusPill}</div>
          <div class="ign">${esc(ign)} <span class="region-badge">${esc(acc.region)}</span></div>
          ${sessionWarningHtml}
        </div>
      </div>
      <div class="actions">
        ${switchBtn}
        <button class="act"${disabled} title="Passwort kopieren" aria-label="Passwort kopieren" data-copy="${esc(acc.username)}">${ICON.copy}</button>
        <button class="act"${disabled} title="Umbenennen" aria-label="Umbenennen" data-rename="${esc(acc.username)}">${ICON.edit}</button>
        <button class="act"${disabled} title="Session neu aufnehmen" aria-label="Session neu aufnehmen" data-recapture="${esc(acc.username)}">${ICON.recapture}</button>
        <button class="act del"${disabled} title="Löschen" aria-label="Löschen" data-delete="${esc(acc.username)}">${ICON.trash}</button>
      </div>
    </div>
    ${ranksBlock}
  </div>`;
}

/* ---- Modal helpers (D-05/D-06) ---- */

/**
 * Open a modal overlay by ID.
 * Clears any previous inline error before opening.
 * @param {string} id  The modal overlay element ID (e.g. 'add-account-modal').
 */
function openModal(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  // Clear error/success feedback state from previous open (Plan 08-04:
  // settings-modal carries both a .modal-error and a .modal-success).
  const errEl = overlay.querySelector('.modal-error');
  if (errEl) { errEl.textContent = ''; errEl.classList.remove('visible'); }
  const successEl = overlay.querySelector('.modal-success');
  if (successEl) { successEl.textContent = ''; successEl.classList.remove('visible'); }
  overlay.classList.add('open');
  // Focus first input
  const first = overlay.querySelector('input:not([type=hidden]),select');
  if (first) setTimeout(() => first.focus(), 50);
}

/**
 * Close a modal overlay by ID.
 * @param {string} id  The modal overlay element ID.
 */
function closeModal(id) {
  const overlay = document.getElementById(id);
  if (overlay) overlay.classList.remove('open');
}

/**
 * Show an inline error message inside a modal.
 * @param {string} modalId  The modal overlay element ID.
 * @param {string} message  The error text to display.
 */
function showModalError(modalId, message) {
  const overlay = document.getElementById(modalId);
  if (!overlay) return;
  // Plan 08-04: hide any lingering success feedback when an error is shown.
  const successEl = overlay.querySelector('.modal-success');
  if (successEl) { successEl.textContent = ''; successEl.classList.remove('visible'); }
  const errEl = overlay.querySelector('.modal-error');
  if (!errEl) return;
  errEl.textContent = message;
  errEl.classList.add('visible');
}

/**
 * Show an inline SUCCESS message inside a modal (Plan 08-04, D-03 live-key-validation
 * feedback and Settings delete/change confirmations). Mirrors showModalError.
 * @param {string} modalId  The modal overlay element ID.
 * @param {string} message  The success text to display.
 */
function showModalSuccess(modalId, message) {
  const overlay = document.getElementById(modalId);
  if (!overlay) return;
  // Hide any lingering error feedback when success is shown.
  const errEl = overlay.querySelector('.modal-error');
  if (errEl) { errEl.textContent = ''; errEl.classList.remove('visible'); }
  const successEl = overlay.querySelector('.modal-success');
  if (!successEl) return;
  successEl.textContent = message;
  successEl.classList.add('visible');
}

/* ---- Modal submit handlers ---- */

/**
 * Submit the add-account modal form.
 * Calls pywebview.api.add_account; on error shows inline message; on success closes.
 */
async function submitAddAccount() {
  const displayName = document.getElementById('add-display-name').value.trim();
  const username    = document.getElementById('add-username').value.trim();
  const password    = document.getElementById('add-password').value;
  const riotId      = document.getElementById('add-riot-id').value.trim();
  const region      = document.getElementById('add-region').value;

  if (!displayName || !username || !password) {
    showModalError('add-account-modal', 'Bitte Anzeigename, Benutzername und Passwort ausfüllen.');
    return;
  }

  try {
    const result = await pywebview.api.add_account(displayName, username, password, riotId, region);
    if (result.ok === false) {
      showModalError('add-account-modal', result.error || 'Unbekannter Fehler');
    } else {
      closeModal('add-account-modal');
      // Clear fields for next open
      ['add-display-name','add-username','add-password','add-riot-id'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
    }
  } catch (err) {
    showModalError('add-account-modal', String(err));
  }
}

/**
 * Submit the rename modal form.
 * Calls pywebview.api.rename_account; on error shows inline message; on success closes.
 */
async function submitRename() {
  const username    = document.getElementById('rename-username').value;
  const newName     = document.getElementById('rename-display-name').value.trim();

  if (!newName) {
    showModalError('rename-modal', 'Bitte einen neuen Anzeigenamen eingeben.');
    return;
  }

  try {
    const result = await pywebview.api.rename_account(username, newName);
    if (result.ok === false) {
      showModalError('rename-modal', result.error || 'Unbekannter Fehler');
    } else {
      closeModal('rename-modal');
    }
  } catch (err) {
    showModalError('rename-modal', String(err));
  }
}

/**
 * Submit the edit-riot-id modal form.
 * Calls pywebview.api.set_riot_id; on error shows inline message; on success closes.
 */
async function submitEditRiotId() {
  const username = document.getElementById('edit-riot-id-username').value;
  const riotId   = document.getElementById('edit-riot-id').value.trim();
  const region   = document.getElementById('edit-region').value;

  try {
    const result = await pywebview.api.set_riot_id(username, riotId, region);
    if (result.ok === false) {
      showModalError('edit-riot-id-modal', result.error || 'Unbekannter Fehler');
    } else {
      closeModal('edit-riot-id-modal');
    }
  } catch (err) {
    showModalError('edit-riot-id-modal', String(err));
  }
}

/* ---- Onboarding + Settings (Plan 08-04, ONBOARD-01/02) ---- */

/**
 * Populate the Settings modal fields from a get_settings() result.
 * @param {object} settings  {has_api_key, api_key_masked, language, update_check_enabled, disable_gpu}
 */
function populateSettingsModal(settings) {
  const keyField = document.getElementById('settings-api-key');
  if (keyField) keyField.value = settings.api_key_masked || '';
  const keyInput = document.getElementById('settings-api-key-input');
  if (keyInput) keyInput.value = '';
  const langSelect = document.getElementById('settings-language');
  if (langSelect && settings.language) langSelect.value = settings.language;
  const updateCheck = document.getElementById('settings-update-check');
  if (updateCheck) updateCheck.checked = settings.update_check_enabled !== false;
  // D-07: the GPU toggle shows "acceleration enabled", i.e. NOT disable_gpu.
  const gpuToggle = document.getElementById('settings-gpu');
  if (gpuToggle) gpuToggle.checked = !settings.disable_gpu;
}

/**
 * Open the Settings modal, pre-filled with the current settings (D-05/D-06).
 */
async function openSettings() {
  if (!window.pywebview || !pywebview.api) return;
  try {
    const settings = await pywebview.api.get_settings();
    populateSettingsModal(settings);
    openModal('settings-modal');
  } catch (err) {
    console.error('[app.js] get_settings error', err);
  }
}

/**
 * Submit a "save API key" form — shared by both the Settings modal and the
 * welcome dialog (D-03 live validation, same ok-dict shape as other modals).
 * On success: the Settings path shows inline success feedback + refreshes the
 * masked field; the welcome path closes and does NOT reopen (D-01/D-02).
 * @param {string} modalId  'settings-modal' or 'welcome-modal'.
 */
async function submitSaveApiKey(modalId) {
  const inputId = modalId === 'welcome-modal' ? 'welcome-api-key' : 'settings-api-key-input';
  const input = document.getElementById(inputId);
  const key = input ? input.value.trim() : '';

  if (!key) {
    showModalError(modalId, 'Bitte einen API-Key eingeben.');
    return;
  }

  try {
    const result = await pywebview.api.save_api_key(key);
    if (result.ok === false) {
      showModalError(modalId, result.error || 'Unbekannter Fehler');
      return;
    }
    if (input) input.value = '';
    if (modalId === 'welcome-modal') {
      closeModal('welcome-modal');
    } else {
      showModalSuccess(modalId, 'API-Key gespeichert.');
      const masked = await pywebview.api.get_api_key_masked();
      const keyField = document.getElementById('settings-api-key');
      if (keyField) keyField.value = masked;
    }
  } catch (err) {
    showModalError(modalId, String(err));
  }
}

/**
 * Delete the stored API key (Settings modal "Löschen" button) and refresh
 * the masked-key display in place.
 */
async function deleteApiKey() {
  if (!window.pywebview || !pywebview.api) return;
  try {
    await pywebview.api.delete_api_key();
    const masked = await pywebview.api.get_api_key_masked();
    const keyField = document.getElementById('settings-api-key');
    if (keyField) keyField.value = masked;
    showModalSuccess('settings-modal', 'API-Key gelöscht.');
  } catch (err) {
    showModalError('settings-modal', String(err));
  }
}

/**
 * First-run trigger (D-01/D-02): if get_settings() reports no stored key
 * anywhere (WCM nor DPAPI file), show the skippable welcome dialog. Called
 * once from the pywebviewready handler, after the initial state push.
 */
async function maybeShowWelcome() {
  if (!window.pywebview || !pywebview.api) return;
  try {
    const settings = await pywebview.api.get_settings();
    if (settings.has_api_key === false) {
      openModal('welcome-modal');
    }
  } catch (err) {
    console.error('[app.js] maybeShowWelcome error', err);
  }
}

/**
 * Skip the welcome dialog ("Später" button) — switching still works fully
 * without a key (D-01).
 */
function skipWelcome() {
  closeModal('welcome-modal');
}

/**
 * Execute account deletion after the confirm-delete modal was confirmed.
 * Calls pywebview.api.delete_account, then closes the modal.
 */
async function confirmDelete() {
  const username = document.getElementById('confirm-delete-username').value;
  try {
    await pywebview.api.delete_account(username);
    closeModal('confirm-delete-modal');
  } catch (err) {
    console.error('[app.js] delete_account error', err);
    closeModal('confirm-delete-modal');
  }
}

/* ---- Global modal keyboard / overlay-click handler ---- */
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  // Close the topmost open non-destructive modal (add / rename / edit-riot-id /
  // settings / welcome — Plan 08-04 extends this list with the two new modals,
  // both skippable/dismissable, not destructive)
  ['add-account-modal', 'rename-modal', 'edit-riot-id-modal', 'settings-modal', 'welcome-modal'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.classList.contains('open')) el.classList.remove('open');
  });
  // Confirm-delete is NOT closed by Escape (D-06: explicit destructive confirm required)
});

// Overlay-click-to-close for non-destructive modals (click the backdrop, not the dialog box)
['add-account-modal', 'rename-modal', 'edit-riot-id-modal', 'settings-modal', 'welcome-modal'].forEach(id => {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) overlay.classList.remove('open');
  });
});

/* ---- Wire modal submit buttons ---- */
(function wireModalButtons() {
  const addSubmit = document.getElementById('add-account-submit');
  if (addSubmit) addSubmit.addEventListener('click', submitAddAccount);

  const renameSubmit = document.getElementById('rename-submit');
  if (renameSubmit) renameSubmit.addEventListener('click', submitRename);

  const editRiotIdSubmit = document.getElementById('edit-riot-id-submit');
  if (editRiotIdSubmit) editRiotIdSubmit.addEventListener('click', submitEditRiotId);

  const confirmDeleteSubmit = document.getElementById('confirm-delete-submit');
  if (confirmDeleteSubmit) confirmDeleteSubmit.addEventListener('click', confirmDelete);

  // Plan 08-04: Settings modal (gear button, save/delete key, GPU toggle)
  const settingsBtn = document.getElementById('settingsBtn');
  if (settingsBtn) settingsBtn.addEventListener('click', openSettings);

  const settingsKeySave = document.getElementById('settings-key-save');
  if (settingsKeySave) settingsKeySave.addEventListener('click', () => submitSaveApiKey('settings-modal'));

  const settingsKeyDelete = document.getElementById('settings-key-delete');
  if (settingsKeyDelete) settingsKeyDelete.addEventListener('click', deleteApiKey);

  const settingsGpu = document.getElementById('settings-gpu');
  if (settingsGpu) {
    settingsGpu.addEventListener('change', () => {
      pywebview.api.set_gpu(settingsGpu.checked)
        .catch(err => console.error('[app.js] set_gpu error', err));
    });
  }

  // Plan 08-06 (D-07/D-14): update-check Settings toggle
  const settingsUpdateCheck = document.getElementById('settings-update-check');
  if (settingsUpdateCheck) {
    settingsUpdateCheck.addEventListener('change', () => {
      pywebview.api.set_update_check(settingsUpdateCheck.checked)
        .catch(err => console.error('[app.js] set_update_check error', err));
    });
  }

  // Plan 08-06 (D-13/D-14): header update pill — click opens the GitHub
  // release page (allowlisted host), the × control dismisses it per-version.
  const updatePillDismiss = document.getElementById('update-pill-dismiss');
  if (updatePillDismiss) {
    updatePillDismiss.addEventListener('click', (e) => {
      e.stopPropagation();
      const tag = window.pywebview && pywebview.state ? pywebview.state.update_tag : null;
      if (!tag) return;
      pywebview.api.dismiss_update(tag)
        .catch(err => console.error('[app.js] dismiss_update error', err));
    });
  }
  const updatePill = document.getElementById('update-pill');
  if (updatePill) {
    updatePill.addEventListener('click', () => {
      const url = window.pywebview && pywebview.state ? pywebview.state.update_url : null;
      if (!url) return;
      pywebview.api.open_external_url(url)
        .catch(err => console.error('[app.js] open_external_url error', err));
    });
  }

  // Plan 08-05 (D-15/D-16): language <select> — live re-render on change,
  // no restart. controller.set_language() persists + pushes window.state.language,
  // which render()'s language-change check picks up and applies via applyLanguage().
  const settingsLanguage = document.getElementById('settings-language');
  if (settingsLanguage) {
    settingsLanguage.addEventListener('change', () => {
      pywebview.api.set_language(settingsLanguage.value)
        .catch(err => console.error('[app.js] set_language error', err));
    });
  }

  // Plan 08-04: Welcome dialog (Speichern / Später / dev-portal link)
  const welcomeSubmit = document.getElementById('welcome-submit');
  if (welcomeSubmit) welcomeSubmit.addEventListener('click', () => submitSaveApiKey('welcome-modal'));

  const welcomeSkip = document.getElementById('welcome-skip');
  if (welcomeSkip) welcomeSkip.addEventListener('click', skipWelcome);

  const welcomeDevLink = document.getElementById('welcome-dev-link');
  if (welcomeDevLink) {
    welcomeDevLink.addEventListener('click', () => {
      pywebview.api.open_external_url('https://developer.riotgames.com/')
        .catch(err => console.error('[app.js] open_external_url error', err));
    });
  }

  // Plan 08-04: header expiry hint (D-09) opens Settings on click
  const apiKeyWarningHint = document.getElementById('api-key-warning-hint');
  if (apiKeyWarningHint) apiKeyWarningHint.addEventListener('click', openSettings);

  // Enter key on modal input fields triggers the primary action
  document.getElementById('add-display-name').addEventListener('keydown', e => { if (e.key === 'Enter') submitAddAccount(); });
  document.getElementById('add-username').addEventListener('keydown', e => { if (e.key === 'Enter') submitAddAccount(); });
  document.getElementById('add-password').addEventListener('keydown', e => { if (e.key === 'Enter') submitAddAccount(); });
  document.getElementById('add-riot-id').addEventListener('keydown', e => { if (e.key === 'Enter') submitAddAccount(); });
  document.getElementById('rename-display-name').addEventListener('keydown', e => { if (e.key === 'Enter') submitRename(); });
  document.getElementById('edit-riot-id').addEventListener('keydown', e => { if (e.key === 'Enter') submitEditRiotId(); });
  const settingsKeyInput = document.getElementById('settings-api-key-input');
  if (settingsKeyInput) settingsKeyInput.addEventListener('keydown', e => { if (e.key === 'Enter') submitSaveApiKey('settings-modal'); });
  const welcomeKeyInput = document.getElementById('welcome-api-key');
  if (welcomeKeyInput) welcomeKeyInput.addEventListener('keydown', e => { if (e.key === 'Enter') submitSaveApiKey('welcome-modal'); });
})();

/* ---- Render functions ---- */

/**
 * Render the full account list from state.
 * Wires all CRUD/switch/DnD interactions (Plan 04-03).
 *
 * @param {object[]} accounts       Serialised account array.
 * @param {string|null} activeUsername  Active Riot username or null.
 * @param {string} status           Switch status: 'idle' | 'switching' | 'error'.
 * @param {boolean} [noApiKey] Plan 08-04 (D-01): true when no API key is stored
 *   anywhere — rank tiles render the "Kein API-Key" hint instead of rank data.
 */
function renderAccountList(accounts, activeUsername, status, noApiKey) {
  const listEl = document.getElementById('list');
  if (!listEl) return;

  const switching = status === 'switching';

  if (!accounts || accounts.length === 0) {
    renderEmptyState(listEl);
    return;
  }

  listEl.innerHTML = accounts.map(acc => card(acc, activeUsername, switching, noApiKey)).join('');

  // Wire "Kein API-Key" rank-tile hint clicks — opens Settings (D-01)
  listEl.querySelectorAll('[data-open-settings]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      openSettings();
    });
  });

  // Wire switch buttons
  listEl.querySelectorAll('[data-switch]').forEach(btn => {
    btn.addEventListener('click', () => {
      const username = btn.dataset.switch;
      _lastSwitchUsername = username;  // track for retry (D-04)
      pywebview.api.switch_account(username)
        .catch(err => console.error('[app.js] switch_account error', err));
    });
  });

  // Wire copy buttons — calls copyPassword() for toast feedback (D-19/D-20)
  listEl.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', () => {
      const username = btn.dataset.copy;
      copyPassword(username);
    });
  });

  // Wire rename buttons — opens rename modal pre-filled with current display_name
  listEl.querySelectorAll('[data-rename]').forEach(btn => {
    btn.addEventListener('click', () => {
      const username = btn.dataset.rename;
      // CR-01: resolve the card via closest() — a username containing a `"`
      // would break a `.card[data-username="..."]` selector string.
      const cardEl = btn.closest('.card');
      const currentName = cardEl ? cardEl.querySelector('.name').textContent : '';
      document.getElementById('rename-username').value = username;
      const nameInput = document.getElementById('rename-display-name');
      nameInput.value = currentName;
      openModal('rename-modal');
    });
  });

  // Wire recapture buttons — triggers the session re-capture flow (D-19)
  listEl.querySelectorAll('[data-recapture]').forEach(btn => {
    btn.addEventListener('click', () => {
      const username = btn.dataset.recapture;
      pywebview.api.recapture_session(username)
        .catch(err => console.error('[app.js] recapture_session error', err));
    });
  });

  // Wire delete buttons — open confirm-delete modal
  listEl.querySelectorAll('[data-delete]').forEach(btn => {
    btn.addEventListener('click', () => {
      const username = btn.dataset.delete;
      // CR-01: resolve the card via closest() — see rename handler above.
      const cardEl = btn.closest('.card');
      const displayName = cardEl ? cardEl.querySelector('.name').textContent : username;
      document.getElementById('confirm-delete-username').value = username;
      document.getElementById('confirm-delete-name').textContent = displayName;
      openModal('confirm-delete-modal');
    });
  });

  // Wire rank row click — opens edit-riot-id modal (v1.0 RiotIdDialog affordance)
  listEl.querySelectorAll('.card').forEach(cardEl => {
    const username = cardEl.dataset.username;
    cardEl.querySelectorAll('.rank.editable').forEach(rankEl => {
      rankEl.addEventListener('click', () => {
        // Find current riot_id and region from the rendered IGN text
        const ignEl = cardEl.querySelector('.ign');
        const currentRiotId = ignEl ? ignEl.textContent : '';
        document.getElementById('edit-riot-id-username').value = username;
        document.getElementById('edit-riot-id').value = (currentRiotId !== username) ? currentRiotId : '';
        openModal('edit-riot-id-modal');
      });
    });
  });

  initSortable(listEl);
}

/* renderStatusBar is defined above in the status-bar state machine section (Plan 04-04). */

/* ---- Live client/match status indicator (STATUS-01: D-14/D-15/D-16/D-18) ---- */

/**
 * Render the header live-status indicator — three states plus a
 * switching-frozen neutral state.
 *
 * D-16: the `state.status === 'switching'` branch is checked FIRST and
 * short-circuits so the indicator never flickers during a switch — this is
 * a pure render-layer gate; the StatusPoller daemon keeps polling and
 * pushing throughout the switch (D-18 — fully decoupled from switch
 * orchestration and from the manual rank-refresh button).
 *
 * @param {object} state  Full pywebview.state (AppState serialised by Python).
 */
function renderClientStatus(state) {
  const el = document.getElementById('client-status');
  if (!el) return;

  if (state.status === 'switching') {
    el.className = 'client-status neutral';
    el.innerHTML = '<span class="dot" aria-hidden="true"></span>Wechsel läuft…';
    return;
  }
  if (state.game_live) {
    el.className = 'client-status live';
    el.innerHTML = '<span class="dot" aria-hidden="true"></span>Im Match';
  } else if (state.client_running) {
    el.className = 'client-status running';
    el.innerHTML = '<span class="dot" aria-hidden="true"></span>Client läuft';
  } else {
    el.className = 'client-status offline';
    el.innerHTML = '<span class="dot" aria-hidden="true"></span>Offline';
  }
}

/**
 * Render the header update pill (Plan 08-06, ONBOARD-03, D-13/D-14).
 *
 * Mirrors renderClientStatus's shape: driven solely by
 * state.update_available/state.update_tag/state.update_url (Python->JS via
 * window.state, same channel). Shown only for a newer, non-dismissed
 * release (D-13); clicking the body opens the GitHub release page, clicking
 * the dismiss control (×) hides it until the NEXT version (D-14).
 *
 * @param {object} state  Full pywebview.state (AppState serialised by Python).
 */
function renderUpdatePill(state) {
  const el = document.getElementById('update-pill');
  if (!el) return;
  if (state.update_available) {
    el.style.display = 'inline-flex';
    const label = document.getElementById('update-pill-label');
    if (label) label.textContent = t('update.available', { tag: state.update_tag });
  } else {
    el.style.display = 'none';
  }
}

/**
 * Render the subline ("N Accounts · X aktiv").
 *
 * @param {object[]} accounts       Serialised account array.
 * @param {string|null} activeUsername  Active Riot username or null.
 */
function renderSubline(accounts, activeUsername) {
  const subEl = document.getElementById('subline');
  if (!subEl) return;

  const count = accounts ? accounts.length : 0;
  let activeName = '';
  if (activeUsername && accounts) {
    const active = accounts.find(a => a.username === activeUsername);
    activeName = active ? active.display_name : activeUsername;
  }

  if (count === 0) {
    subEl.textContent = 'Keine Accounts';
    return;
  }

  // CR-01 / IN-03: activeName derives from display_name — escape it (this is an
  // innerHTML sink).  Only the count segment needs no escaping (it is a number).
  const activeStr = activeName ? ` · ${esc(activeName)} aktiv` : '';
  subEl.innerHTML = `${count} Account${count !== 1 ? 's' : ''}${activeStr}`;
}

/**
 * Top-level render: called on every state change.
 *
 * @param {object} state  The pywebview.state object (AppState serialised by Python).
 */
function render(state) {
  // Plan 08-05 (D-16): if the language changed, translate the static
  // [data-i18n] labels first, then let applyLanguage() re-invoke render()
  // with currentLang already caught up — avoids rendering once with the
  // stale language and again with the new one.
  if (state.language && state.language !== currentLang) {
    applyLanguage(state.language);
    return;
  }

  const accounts = state.accounts || [];
  const activeUsername = state.active_username || null;
  const status = state.status || 'idle';
  // Plan 08-04 (D-01): no key stored anywhere -> "Kein API-Key" rank-tile hint.
  const noApiKey = state.has_api_key === false;

  renderAccountList(accounts, activeUsername, status, noApiKey);
  renderStatusBar(state);           // full state machine (D-01..D-04) — Plan 04-04
  renderClientStatus(state);        // header live-status indicator (STATUS-01) — Plan 05-02
  renderUpdatePill(state);          // header update pill (ONBOARD-03) — Plan 08-06
  renderSubline(accounts, activeUsername);
  renderApiKeyWarningHint(state);   // Plan 08-04 (D-09): persistent 401/403 header hint
  // D-03: lock all switch/CRUD controls while a switch or first-login is in progress
  setUiLocked(status === 'switching');
  // D-17: clear refresh spinner on each state update (rank data may have changed)
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) refreshBtn.classList.remove('refreshing');
}

/**
 * Render the persistent header hint shown when a rank fetch hit 401/403
 * (Plan 08-04, D-09). Driven solely by state.api_key_warning; clicking it
 * opens the Settings modal.
 * @param {object} state  Full pywebview.state.
 */
function renderApiKeyWarningHint(state) {
  const el = document.getElementById('api-key-warning-hint');
  if (!el) return;
  el.classList.toggle('visible', state.api_key_warning === true);
}

/* ---- Copy-password handler (D-19/D-20) ---- */

/** Toast auto-hide timer ID. */
let _toastTimer = null;

/**
 * Show the #toast element with the given message for ~3 seconds.
 * @param {string} message  Text to display in the toast.
 */
function showToast(message) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('visible');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    toast.classList.remove('visible');
    _toastTimer = null;
  }, 3500);
}

/**
 * Copy the named account's password.
 * Result: toast "Passwort kopiert — wird in 30s gelöscht" on success (D-19).
 *         The actual clipboard write + 30s match-before-clear live in controller.copy_password (D-20).
 *         Password value NEVER enters JS.
 * @param {string} username  Riot username.
 */
async function copyPassword(username) {
  if (!window.pywebview || !pywebview.api) return;
  try {
    const result = await pywebview.api.copy_password(username);
    if (result && result.ok) {
      showToast('Passwort kopiert — wird in 30s gelöscht');
    } else {
      showToast('Kein Passwort gespeichert');
    }
  } catch (err) {
    console.error('[app.js] copy_password error', err);
    showToast('Fehler beim Kopieren');
  }
}

/* ---- Wire the refresh button (D-17/GUI-06) ---- */

/**
 * Trigger a manual rank refresh and show a spinner on the refresh button.
 * Spinner is removed on the next state change (rank data update).
 */
function triggerRefresh() {
  if (!window.pywebview || !pywebview.api) return;
  const btn = document.getElementById('refreshBtn');
  if (btn) btn.classList.add('refreshing');
  pywebview.api.refresh_ranks()
    .catch(err => console.error('[app.js] refresh_ranks error', err));
}

(function wireRefreshBtn() {
  const btn = document.getElementById('refreshBtn');
  if (btn) {
    btn.addEventListener('click', triggerRefresh);
  }
})();

/* ---- Wire the add-account button ---- */
(function wireAddBtn() {
  const btn = document.getElementById('addBtn');
  if (btn) {
    btn.addEventListener('click', () => {
      openModal('add-account-modal');
    });
  }
})();

/* ---- Maximize / restore toggle (D-09) ---- */

/**
 * Toggle window maximize/restore.
 * Awaits the bridge result and swaps the button glyph:
 *   □ (U+25A1)  when restored (default)
 *   ❐ (U+2750)  when maximised
 */
async function toggleMax() {
  if (!window.pywebview || !pywebview.api) return;
  try {
    const result = await pywebview.api.toggle_max();
    const btn = document.getElementById('winctl-max');
    if (btn && result) {
      // U+25A1 = □ (restore affordance shown when maximised)
      // U+2750 = ❐ (a "restore-down" square-within-square)
      btn.innerHTML = result.maximized ? '&#10064;' : '&#9633;';
    }
  } catch (err) {
    console.error('[app.js] toggle_max error', err);
  }
}

/* ---- App-driven resize grips (D-10) ---- */
/**
 * Wire pointer-based resize on the three fixed resize grip elements.
 * Resizes are driven by pywebview.api.resize_to(w, h) (bridge → window.resize).
 *
 * Grips sit on right edge (rg-e), bottom edge (rg-s), and SE corner (rg-se).
 * All three stopPropagation so they never trigger window drag or SortableJS DnD.
 * Pointer capture ensures smooth dragging when the cursor leaves the grip briefly.
 *
 * Throttled via requestAnimationFrame to avoid saturating the pywebview bridge.
 */
(function wireResizeGrips() {
  /** Minimum window dimensions matching min_size in webview_window.py (D-10). */
  const MIN_W = 480;
  const MIN_H = 400;

  /**
   * Attach resize behaviour to a grip element.
   * @param {HTMLElement} grip   The fixed-position grip div.
   * @param {boolean} resizeW   Whether this grip resizes width (E/SE grips).
   * @param {boolean} resizeH   Whether this grip resizes height (S/SE grips).
   */
  function attachGrip(grip, resizeW, resizeH) {
    if (!grip) return;

    let startScreenX = 0;
    let startScreenY = 0;
    let startW = 0;
    let startH = 0;
    let rafPending = false;
    // WR-06: latch the newest pointer coordinates on every move so the throttled
    // rAF callback reads the latest position, not the stale `e` from the event
    // that happened to schedule the frame.
    let lastScreenX = 0;
    let lastScreenY = 0;

    grip.addEventListener('pointerdown', function (e) {
      // Only primary button (left click); prevent window drag and card DnD
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();

      startScreenX = e.screenX;
      startScreenY = e.screenY;
      startW = window.innerWidth;
      startH = window.innerHeight;
      rafPending = false;

      // WR-05: a manual resize means the window is no longer maximised — reset
      // the maximize-button glyph to the □ affordance so it stays in sync with
      // js_api.resize_to (which also clears its _maximized mirror).
      const maxBtn = document.getElementById('winctl-max');
      if (maxBtn) maxBtn.innerHTML = '&#9633;';

      grip.setPointerCapture(e.pointerId);
    });

    grip.addEventListener('pointermove', function (e) {
      // Only act while the primary button is held (setPointerCapture is active)
      if ((e.buttons & 1) === 0) return;
      e.stopPropagation();

      // WR-06: latch the latest coordinates on EVERY move (before the throttle
      // gate) so no intermediate delta is lost when frames are dropped.
      lastScreenX = e.screenX;
      lastScreenY = e.screenY;

      if (!window.pywebview || !pywebview.api) return;
      if (rafPending) return; // throttle: one bridge call per animation frame

      rafPending = true;
      requestAnimationFrame(function () {
        rafPending = false;
        const newW = resizeW
          ? Math.max(MIN_W, startW + (lastScreenX - startScreenX))
          : startW;
        const newH = resizeH
          ? Math.max(MIN_H, startH + (lastScreenY - startScreenY))
          : startH;
        pywebview.api.resize_to(Math.round(newW), Math.round(newH))
          .catch(function (err) { console.error('[app.js] resize_to error', err); });
      });
    });

    grip.addEventListener('pointerup', function (e) {
      e.stopPropagation();
      // Pointer capture is released automatically on pointerup
    });

    grip.addEventListener('pointercancel', function (e) {
      e.stopPropagation();
    });
  }

  attachGrip(document.getElementById('rg-e'),  true,  false);
  attachGrip(document.getElementById('rg-s'),  false, true);
  attachGrip(document.getElementById('rg-se'), true,  true);
})();

/* ---- pywebviewready gate — ALL bridge calls happen here ---- */
window.addEventListener('pywebviewready', function () {
  // Open Question 2 precaution (RESEARCH.md): call on_webview_ready via
  // setTimeout(..., 0) to ensure pywebview.api is fully initialised before
  // the first call, avoiding any sub-tick race condition.
  setTimeout(function () {
    pywebview.api.on_webview_ready()
      .catch(function (err) { console.error('[app.js] on_webview_ready error', err); });
  }, 0);

  // Subscribe to all Python-side state pushes
  pywebview.state.addEventListener('change', function () {
    render(pywebview.state);
  });

  // Plan 08-05 (ONBOARD-04): fetch the shared i18n catalog BEFORE the first
  // render so status text / data-i18n labels resolve correctly from frame
  // one. A fetch failure degrades to raw-key fallback (A1) rather than
  // blocking startup — loadStrings() never rejects.
  loadStrings().then(function () {
    // Initial render from the state that on_webview_ready() just pushed
    render(pywebview.state);

    // Plan 08-04 (D-01/D-02): first-run welcome dialog — shown only when no
    // key is found anywhere (WCM nor DPAPI file). Checked once at startup.
    maybeShowWelcome();
  });
});
