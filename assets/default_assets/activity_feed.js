/* Activity Feed renderer shared by the Mycelian tab and the OBS dock.
 *
 * The page hands it state (settings, shared view, rows, queue state) and gets user actions
 * back through ``send(action, payload)``. The tab binds hosts rendered by NiceGUI
 * (.afx-host[data-afx-app] with .afx-slot data attributes); the dock creates one instance and
 * feeds it from Socket.IO. All text is escaped: names and viewer messages are never markup.
 */
(function () {
  'use strict';
  var VERSION = 4;
  if (window.MycelianFeed && window.MycelianFeed.version >= VERSION) { return; }

  var FILTERS = [
    ['all', 'All events', 'select_all'], ['follows', 'Follows', 'favorite'], ['subs', 'Subscriptions', 'star'],
    ['resubs', 'Resubscriptions', 'autorenew'], ['giftsubs', 'Gift subs', 'redeem'], ['bits', 'Bits', 'diamond'],
    ['points', 'Channel points', 'toll'], ['donations', 'Tips & Super Chats', 'payments'], ['raids', 'Raids', 'groups'],
    ['streaks', 'Watch streaks', 'local_fire_department'], ['modiversaries', 'Modiversaries', 'shield'],
    ['hype_train', 'Hype Train', 'rocket_launch']
  ];
  var DEFAULTS = {
    layout: 'rows', density: 'comfortable', color: 'icon', icons: true, tier: 'icon', platform: true, messages: true, sub_duration: true,
    wrap: false, times: 'relative', earlier_minutes: 30, order_numbers: true, dots: true, new_minutes: 5,
    now_playing: true, timer: 'strip', up_next: 1, text_size: 'medium', person_hours: 12,
    sub_duration_style: 'split', sub_duration_place: 'inline'
  };
  var CHOICES = {
    layout: ['rows', 'cards', 'ticker'], density: ['compact', 'comfortable', 'spacious'], color: ['icon', 'bar', 'tint', 'none'],
    tier: ['icon', 'name', 'hidden'], times: ['relative', 'clock', 'hidden'], timer: ['strip', 'item', 'off'],
    text_size: ['small', 'medium', 'large'], sub_duration_style: ['split', 'chip', 'calendar', 'dots'],
    sub_duration_place: ['inline', 'right', 'below']
  };
  // kind -> [icon, colour, label, filter key, verb used for queue-only rows]
  var KINDS = {
    follow: ['favorite', 'var(--color-primary)', 'Follow', 'follows', 'followed'],
    sub: ['star', 'var(--color-info)', 'Sub', 'subs', 'subscribed'],
    resub: ['autorenew', 'var(--color-info)', 'Resub', 'resubs', 'resubscribed'],
    giftsub: ['redeem', 'var(--color-success)', 'Gift', 'giftsubs', 'gifted subs'],
    bits: ['diamond', 'var(--color-warning)', 'Bits', 'bits', 'cheered'],
    points: ['toll', 'var(--color-error)', 'Points', 'points', 'redeemed a reward'],
    raid: ['groups', 'var(--color-error)', 'Raid', 'raids', 'raided'],
    donation: ['payments', 'var(--color-success)', 'Tip', 'donations', 'tipped'],
    streak: ['local_fire_department', 'var(--color-warning)', 'Streak', 'streaks', 'is on a watch streak'],
    hype: ['rocket_launch', 'var(--color-primary)', 'Hype Train', 'hype_train', 'Hype Train'],
    modiversary: ['shield', 'var(--color-info)', 'Modiversary', 'modiversaries', 'celebrated a modiversary'],
    member: ['workspace_premium', 'var(--color-info)', 'Member', 'subs', 'became a member'],
    milestone: ['military_tech', 'var(--color-info)', 'Milestone', 'resubs', 'reached a member milestone'],
    giftmember: ['card_giftcard', 'var(--color-success)', 'Gift', 'giftsubs', 'gifted memberships'],
    superchat: ['chat', 'var(--color-success)', 'Super Chat', 'donations', 'sent a Super Chat'],
    supersticker: ['sticky_note_2', 'var(--color-warning)', 'Super Sticker', 'donations', 'sent a Super Sticker'],
    alert: ['notifications', 'var(--color-primary)', 'Alert', '', 'alert']
  };
  var ALIASES = {
    follow: 'follow', sub: 'sub', subscription: 'sub', resub: 'resub', giftsub: 'giftsub', gift_sub: 'giftsub',
    bits: 'bits', bit: 'bits', cheer: 'bits', points: 'points', point: 'points', raid: 'raid', donation: 'donation',
    tip: 'donation', streak: 'streak', 'hype train': 'hype', hype_train: 'hype', hype_train_start: 'hype',
    hype_train_progress: 'hype', hype_train_end: 'hype', modiversary: 'modiversary', membership: 'member',
    member: 'member', 'member milestone': 'milestone', member_milestone: 'milestone', 'gift membership': 'giftmember',
    gift_membership: 'giftmember', 'super chat': 'superchat', superchat: 'superchat', 'super sticker': 'supersticker',
    supersticker: 'supersticker'
  };
  var TW = '<svg class="plat" viewBox="0 0 24 24" aria-label="Twitch"><path fill="#9146FF" d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z"/></svg>';
  var SL = '<svg class="plat" viewBox="0 0 24 24" aria-label="Streamlabs"><path fill="#80F5D2" d="M8.6878 1.3459a1.365 1.365 0 0 0-.2734.0058c-.528.066-1.0133.1616-1.4843.3086A10.0568 10.0568 0 0 0 .3208 8.2697c-.147.471-.2445.9583-.3105 1.4863-.091.734.431 1.4041 1.166 1.4961.734.091 1.404-.43 1.496-1.164.05-.406.119-.7316.209-1.0196A7.3736 7.3736 0 0 1 7.727 4.221c.288-.09.6145-.157 1.0195-.207.735-.092 1.255-.7631 1.164-1.4981a1.3394 1.3394 0 0 0-1.2226-1.17Zm4.0488 5.2226c-2.629 0-3.9432.0007-4.9472.5117A4.684 4.684 0 0 0 5.7406 9.131c-.512 1.004-.5117 2.3183-.5117 4.9473v4.289c0 1.502-.001 2.2542.291 2.8282.257.505.6679.9149 1.1719 1.1719.574.292 1.326.291 2.828.291h6.9706c2.628 0 3.9442.0012 4.9472-.5098a4.6883 4.6883 0 0 0 2.0507-2.0508c.512-1.004.5117-2.3182.5117-4.9472v-1.0723c0-2.629.0003-3.9433-.5117-4.9473a4.6883 4.6883 0 0 0-2.0507-2.0508c-1.003-.511-2.3193-.5117-4.9472-.5117zm.537 6.7051c.741 0 1.3399.5998 1.3399 1.3398v2.6836c0 .74-.5988 1.3399-1.3398 1.3399-.74 0-1.3418-.5999-1.3418-1.3399v-2.6836c0-.74.6018-1.3398 1.3418-1.3398zm5.3632 0c.74 0 1.3399.5998 1.3399 1.3398v2.6836c0 .74-.5999 1.3399-1.3399 1.3399-.741 0-1.3398-.5999-1.3398-1.3399v-2.6836c0-.74.5989-1.3398 1.3398-1.3398z"/></svg>';
  var SE = '<svg class="plat" viewBox="0 0 24 24" aria-label="StreamElements"><path fill="#2700FF" d="M12 0a.18.18 0 0 0-.157.088L9.825 3.713a.75.75 0 0 0-.089.486l.682 4.437-1.276 2.503a.6.6 0 0 0-.058.383l.467 2.611c.014.085.15.085.164 0l.252-1.424c.081-.463.308-.892.648-1.235l.524 1.333a.08.08 0 0 0 .076.052h.123l.333 1.237c-.378 1.61-1.166 3.898-2.9 3.898-1.364 0-3.204-2.088-3.471-3.057-.099.606-.231 1.422-.835 1.422-1.482 0-2.687-3.001-2.745-3.723-.318.434-.313 2.19.159 3.315 1.232 2.941 3.918 5.214 7.237 6.061.76.21 1.565.318 2.389.348h.002q.107.005.214.006l.063.002q.105.003.21.004.102-.001.202-.004l.1-.002.177-.005c.85-.03 1.682-.144 2.463-.365 3.29-.86 5.95-3.122 7.175-6.045.47-1.12.476-2.862.163-3.308-.064.732-1.266 3.716-2.744 3.716-.601 0-.736-.816-.835-1.422-.267.967-2.107 3.057-3.471 3.057-1.731 0-2.52-2.28-2.9-3.89l.336-1.245h.117c.034 0 .065-.02.077-.052l.527-1.338c.342.344.57.775.652 1.24l.252 1.426c.017.085.15.085.164 0l.467-2.613a.6.6 0 0 0-.058-.383l-1.281-2.515.68-4.425a.75.75 0 0 0-.09-.486l.008.002L12.156.088A.17.17 0 0 0 12.001 0m10.274 12.643.001-.007h-.005zM15.041 2.138l.652 1.174c4.32 1.435 7.29 5.298 7.29 9.576 0 5.61-4.927 10.172-10.985 10.172S1.015 18.497 1.015 12.888c0-4.28 2.969-8.141 7.29-9.576l.652-1.172C3.724 3.408 0 7.821 0 12.888 0 19.014 5.384 24 12 24s12-4.986 12-11.112c0-5.067-3.724-9.482-8.959-10.75"/></svg>';
  var YT = '<svg class="plat" viewBox="0 0 24 24" aria-label="YouTube"><path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>';
  var PLAT_MARKS = { twitch: TW, youtube: YT, streamlabs: SL, streamelements: SE };
  var EQ = '<span class="afx-eq"><i></i><i></i><i></i><i></i></span>';
  var LIVE_CAP = 500;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function mi(name) { return '<span class="mi">' + esc(name) + '</span>'; }
  function num(n) { var v = Number(n); return isFinite(v) ? v.toLocaleString() : String(n == null ? '' : n); }
  function ordinal(n) {
    n = Number(n) || 0;
    var s = (n % 100 >= 10 && n % 100 <= 20) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
    return n + s;
  }
  function kindOf(type) {
    var k = String(type || '').trim().toLowerCase();
    return ALIASES[k] || 'alert';
  }
  function meta(kind) { return KINDS[kind] || KINDS.alert; }
  function nowSec() { return Date.now() / 1000; }

  function normSettings(raw) {
    var out = {}, src = raw || {};
    Object.keys(DEFAULTS).forEach(function (k) {
      var d = DEFAULTS[k], v = src[k];
      if (v === undefined || v === null) { out[k] = d; return; }
      if (typeof d === 'boolean') { out[k] = !!v; return; }
      if (typeof d === 'number') { var n = parseInt(v, 10); out[k] = isFinite(n) ? n : d; return; }
      v = String(v).toLowerCase();
      out[k] = (CHOICES[k] && CHOICES[k].indexOf(v) === -1) ? d : v;
    });
    return out;
  }
  function normFilters(raw) {
    var out = {};
    FILTERS.forEach(function (f) { out[f[0]] = !(raw && raw[f[0]] === false); });
    return out;
  }
  function modeOf(view) {
    if (!view) { return 'live'; }
    if (view.mode === 'live' || view.mode === 'history' || view.mode === 'person') { return view.mode; }
    if (view.tab === 'previous') { return 'history'; }
    return view.condensed ? 'person' : 'live';
  }
  function normQueue(raw) {
    var src = raw || {};
    function entry(e) {
      if (!e || typeof e !== 'object') { return null; }
      return {
        alert_id: e.alert_id ? String(e.alert_id) : null, type: String(e.type || e.alert_type || ''),
        username: String(e.username || ''), is_replay: !!e.is_replay,
        started_at_ms: e.started_at_ms != null ? Number(e.started_at_ms) : null,
        duration_ms: e.duration_ms ? Number(e.duration_ms) : null
      };
    }
    var q = [];
    (src.queue || []).forEach(function (e) { var x = entry(e); if (x) { q.push(x); } });
    var serverNow = Number(src.server_now_ms) || Date.now();
    return {
      paused: !!src.paused, muted: !!src.muted, playing: entry(src.playing), queue: q,
      revision: src.revision == null ? -1 : Number(src.revision), offset: serverNow - Date.now(),
      contract: !!src.contract || (src.revision != null && src.server_now_ms != null)
    };
  }
  function filterMatch(row, filters, search) {
    if (!filters.all) {
      var key = row.filter || (meta(row.kind)[3]);
      if (!(key && filters[key] !== false)) { return false; }
    }
    var q = String(search || '').trim().toLowerCase();
    if (!q) { return true; }
    var hay = [row.user, row.what, row.msg, row.amount, row.label].join(' ').toLowerCase();
    return hay.indexOf(q) !== -1;
  }
  function tipPlatform(a) {
    var s = String(a.donation_source || a.source || (a.stored_alert_data || {}).donation_source || '').toLowerCase();
    return s === 'streamlabs' || s === 'streamelements' ? s : '';
  }
  // Multi-month tag captions: [full, short]; the short one only shows where a row runs out of room.
  var DURATION_CAPTIONS = {
    sub: ['months in advance', 'mo up front'], resub: ['months paid ahead', 'mo ahead'],
    gift: ['months gifted', 'mo gift'], each: ['months each', 'mo each']
  };
  /* Fallback for payloads without a precomputed row (older senders). */
  function rowFromAlert(a) {
    if (a && a.row && a.row.kind) { return a.row; }
    if (a && a.kind && a.icon && a.user !== undefined) { return a; }
    a = a || {};
    var kind = kindOf(a.badge_type) !== 'alert' ? kindOf(a.badge_type) : kindOf(a.type || a.alert_type);
    var m = meta(kind);
    var user = a.username || String(a.message || '').split(/\s+/)[0] || 'Someone';
    var what = String(a.message || '');
    if (what.toLowerCase().indexOf(user.toLowerCase()) === 0) { what = what.slice(user.length).trim(); }
    what = what.replace(/!+\s*$/, '') || m[4];
    var ts = Number(a.timestamp) || Number(a.created_at) || nowSec();
    var id = a.alert_id && String(a.alert_id).indexOf('generated_') !== 0 ? String(a.alert_id) : null;
    var months = parseInt(a.duration_months, 10);
    months = (kind === 'sub' || kind === 'resub' || kind === 'giftsub') && months > 1 ? months : 0;
    var capKind = !months ? '' : (kind === 'giftsub' ? (parseInt(a.gift_qty, 10) > 1 ? 'each' : 'gift') : (kind === 'resub' ? 'resub' : 'sub'));
    var dur = months ? months + ' ' + DURATION_CAPTIONS[capKind][0] : '';
    return {
      id: id || ('row-' + Math.round(ts * 1000)), alert_id: id, kind: kind, icon: m[0], color: m[1], label: m[2],
      filter: m[3], user: user, what: what, amount: '', tier: null, msg: a.user_message || '', ts: ts,
      months: months, duration: dur, duration_short: months ? months + ' ' + DURATION_CAPTIONS[capKind][1] : '', duration_kind: capKind,
      what_tagged: months ? what : '', what_full: months ? what + ' · ' + dur : '',
      platform: kind === 'donation' ? tipPlatform(a) : 'twitch', restored: !!a.is_restored, seq: a.seq != null ? a.seq : null
    };
  }
  function queueRow(e, pos) {
    var kind = kindOf(e.type), m = meta(kind);
    return {
      id: 'q-' + (e.alert_id || pos), alert_id: e.alert_id, kind: kind, icon: m[0], color: m[1], label: m[2], filter: m[3],
      user: e.username || (kind === 'hype' ? 'Hype Train' : 'Someone'), what: e.is_replay ? 'replay · ' + m[2].toLowerCase() : m[4],
      amount: '', tier: null, msg: '', ts: nowSec(), platform: '', restored: false, seq: null, replay: !!e.is_replay, synthetic: true
    };
  }
  function relTime(ts) {
    var d = nowSec() - Number(ts || 0);
    if (d < 60) { return 'just now'; }
    if (d < 3600) { return Math.floor(d / 60) + 'm'; }
    if (d < 86400) { return Math.floor(d / 3600) + 'h'; }
    return Math.floor(d / 86400) + 'd';
  }
  function clockTime(ts) {
    try { return new Date(Number(ts) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  }
  function minutesLabel(m) {
    if (m >= 60) { var h = m / 60; return h + (h === 1 ? ' hour' : ' hours'); }
    return m + ' min';
  }
  function dayLabel(ts) {
    var d = new Date(Number(ts) * 1000), t = new Date();
    var key = function (x) { return x.getFullYear() + '-' + x.getMonth() + '-' + x.getDate(); };
    if (key(d) === key(t)) { return 'Today'; }
    var y = new Date(t.getTime() - 86400000);
    if (key(d) === key(y)) { return 'Yesterday'; }
    try { return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }); } catch (e) { return key(d); }
  }
  function mmss(ms) {
    var s = Math.max(0, Math.ceil(ms / 1000));
    return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
  }

  var instances = [];

  function Feed(mount, opts) {
    opts = opts || {};
    this.mount = mount;
    this.dock = !!opts.dock;
    this.send = opts.send || function () {};
    this.s = {
      settings: normSettings(null),
      view: { mode: 'live', filters: normFilters(null), search: '', page: 1, revision: -1 },
      live: [], history: { rows: [], page: 1, total_pages: 1, total_count: 0, has_prev: false, has_next: false, loading: true },
      person: { groups: [], hours: 12, alert_count: 0, user_count: 0, loading: true },
      queue: normQueue(null), meta: { connected: true, dock_open: null, app: !opts.dock }
    };
    this.menuOpen = false;
    this.searchTimer = null;
    this.pendingSearch = null;
    this.tickN = 0;
    this.revs = {};
    this.build();
    instances.push(this);
  }

  Feed.prototype.build = function () {
    var dock = this.dock;
    var play = '<div class="afx-play"><button type="button" data-a="pause" data-r="pause"></button>' +
      '<button type="button" data-a="mute" data-r="mute"></button>' +
      '<button type="button" class="is-skip" data-a="skip" data-r="skip">' + mi('skip_next') + '<span class="lb">Skip</span></button></div>';
    var tabs = '<div class="afx-seg" role="tablist">' +
      '<button type="button" data-a="mode" data-mode="live">' + mi('bolt') + '<span class="lb">Live</span></button>' +
      '<button type="button" data-a="mode" data-mode="history">' + mi('history') + '<span class="lb">History</span></button>' +
      '<button type="button" data-a="mode" data-mode="person">' + mi('group_work') + '<span class="lb">By person</span></button></div>';
    var menu = FILTERS.map(function (f, i) {
      var k = KINDS[{ follows: 'follow', subs: 'sub', resubs: 'resub', giftsubs: 'giftsub', bits: 'bits', points: 'points', donations: 'donation', raids: 'raid', streaks: 'streak', modiversaries: 'modiversary', hype_train: 'hype' }[f[0]]];
      var color = k ? k[1] : 'var(--color-text-muted)';
      return '<label class="' + (i === 0 ? 'is-all' : '') + '" style="--tc:' + color + '"><input type="checkbox" data-filter="' + f[0] + '">' + mi(f[2]) + esc(f[1]) + '</label>';
    }).join('');
    var tools = '<label class="afx-search">' + mi('search') + '<input type="text" data-r="search" placeholder="Search people or messages" autocomplete="off" spellcheck="false">' +
      '<button type="button" class="afx-clear" data-a="clear-search" title="Clear search">' + mi('close') + '</button></label>' +
      '<div class="afx-filter-wrap" data-r="fwrap"><button type="button" class="afx-filter" data-a="filters" title="Choose which events show">' + mi('filter_list') + 'Filters<span class="n" data-r="fcount"></span></button>' +
      '<div class="afx-menu" data-r="menu">' + menu + '</div></div>';
    var html;
    if (dock) {
      html = '<div class="afx-shell"><div class="afx-head">' + play +
        '<button type="button" class="afx-ibtn" data-a="settings" title="Feed look: Mycelian → Settings → App Settings → Activity Feed">' + mi('tune') + '</button></div>' +
        '<div class="afx-bar">' + tabs + '</div><div class="afx-bar">' + tools + '</div>';
    } else {
      html = '<div class="afx-shell"><div class="afx-head"><span class="afx-tile">' + mi('dynamic_feed') + '</span>' +
        '<div><div class="afx-title">Activity Feed</div><div class="afx-sub">Everything that happened on stream · mirrored in the OBS dock</div></div>' +
        '<span class="afx-pill" data-r="pill"></span><span class="afx-spacer"></span>' + play +
        '<button type="button" class="afx-ibtn" data-a="settings" title="Feed look &amp; storage (App Settings)">' + mi('tune') + '</button>' +
        '<button type="button" class="afx-ibtn" data-a="help" title="Help">' + mi('help_outline') + '</button></div>' +
        '<div class="afx-bar">' + tabs + '<button type="button" class="afx-sync" data-a="copy-dock" data-r="sync"></button><span class="afx-spacer"></span>' + tools + '</div>';
    }
    html += '<div data-r="strip"></div><div class="afx-list" data-r="list"></div><div data-r="pager"></div></div>';
    var root = document.createElement('div');
    root.className = 'afx' + (dock ? ' is-dock' : '');
    root.innerHTML = html;
    this.root = root;
    this.r = {};
    var self = this;
    root.querySelectorAll('[data-r]').forEach(function (el) { self.r[el.getAttribute('data-r')] = el; });
    this.mount.innerHTML = '';
    this.mount.appendChild(root);
    root.addEventListener('click', function (e) { self.onClick(e); });
    root.addEventListener('change', function (e) { self.onChange(e); });
    this.r.search.addEventListener('input', function () { self.onSearchInput(); });
    this.r.search.addEventListener('keydown', function (e) { if (e.key === 'Escape') { self.r.search.value = ''; self.onSearchInput(); } });
    this.docClick = function (e) {
      if (self.menuOpen && !self.r.fwrap.contains(e.target)) { self.menuOpen = false; self.renderHead(); }
    };
    document.addEventListener('click', this.docClick, true);
    this.renderAll();
  };

  Feed.prototype.destroy = function () {
    document.removeEventListener('click', this.docClick, true);
    var i = instances.indexOf(this);
    if (i !== -1) { instances.splice(i, 1); }
  };

  Feed.prototype.isAlive = function () { return !!(this.root && this.root.isConnected); };

  /* ---- state ---- */
  Feed.prototype.set = function (name, value, opts) {
    var s = this.s;
    if (name === 'settings') {
      s.settings = normSettings(value);
    } else if (name === 'view') {
      var v = value || {};
      var rev = v.revision == null ? null : Number(v.revision);
      if (rev !== null && s.view.revision >= 0 && rev < s.view.revision) { return; }
      var prevMode = s.view.mode;
      s.view.mode = modeOf(v);
      s.view.filters = normFilters(v.filters);
      s.view.page = Number(v.page) || 1;
      if (rev !== null) { s.view.revision = rev; }
      var incoming = String(v.search == null ? '' : v.search);
      var typing = this.pendingSearch !== null || document.activeElement === this.r.search;
      if (!typing || incoming === this.r.search.value) {
        s.view.search = incoming;
        if (document.activeElement !== this.r.search) { this.r.search.value = incoming; }
      }
      if (prevMode !== s.view.mode && s.view.mode === 'history') { s.history.loading = true; }
    } else if (name === 'live') {
      s.live = (value || []).map(rowFromAlert).slice(0, LIVE_CAP);
    } else if (name === 'add') {
      var row = rowFromAlert(value);
      s.live = s.live.filter(function (r) { return r.id !== row.id; });
      s.live.unshift(row);
      if (s.live.length > LIVE_CAP) { s.live.length = LIVE_CAP; }
    } else if (name === 'history') {
      var h = value || {};
      s.history = {
        rows: (h.rows || h.alerts || []).map(rowFromAlert), page: Number(h.page) || 1, total_pages: Number(h.total_pages) || 1,
        total_count: Number(h.total_count) || 0, has_prev: !!h.has_prev, has_next: !!h.has_next, loading: !!h.loading, error: h.error || ''
      };
    } else if (name === 'person') {
      var p = value || {};
      s.person = {
        groups: p.groups || [], hours: Number(p.hours) || s.settings.person_hours || 12, alert_count: Number(p.alert_count) || 0,
        user_count: Number(p.user_count) || (p.groups || []).length, loading: !!p.loading, error: p.success === false ? (p.error || 'failed') : '',
        truncation: p.truncation || ''
      };
    } else if (name === 'queue') {
      s.queue = normQueue(value);
    } else if (name === 'flags') {
      // pause / mute updates from senders without the queue contract
      if (value && typeof value.paused === 'boolean') { s.queue.paused = value.paused; }
      if (value && typeof value.muted === 'boolean') { s.queue.muted = value.muted; }
      if (value && typeof value.playing === 'boolean' && !s.queue.contract) { s.queue.playingFlag = value.playing; }
    } else if (name === 'meta') {
      var m = value || {};
      Object.keys(m).forEach(function (k) { s.meta[k] = m[k]; });
    }
    if (!(opts && opts.defer)) { this.renderAll(); }
  };

  Feed.prototype.mode = function () { return this.s.view.mode; };

  /* ---- events ---- */
  Feed.prototype.onClick = function (e) {
    var t = e.target.closest('[data-a]');
    var s = this.s;
    if (!t || !this.root.contains(t)) {
      var row = e.target.closest('.afx-row.is-click');
      if (row && this.root.contains(row)) {
        this.send('replay', { alert_id: row.getAttribute('data-id'), user: row.getAttribute('data-user') || '', label: row.getAttribute('data-label') || '' });
      }
      return;
    }
    var a = t.getAttribute('data-a');
    e.stopPropagation();
    if (a === 'pause') { this.send('pause', { paused: !s.queue.paused }); }
    else if (a === 'resume') { this.send('pause', { paused: false }); }
    else if (a === 'mute') { this.send('mute', { muted: !s.queue.muted }); }
    else if (a === 'skip') { this.send('skip', { alert_id: null }); }
    else if (a === 'row-skip') { this.send('skip', { alert_id: t.getAttribute('data-id') || null }); }
    else if (a === 'mode') {
      var mode = t.getAttribute('data-mode');
      if (mode !== s.view.mode) {
        s.view.mode = mode;
        if (mode === 'history') { s.view.page = 1; s.history.loading = true; }
        if (mode === 'person') { s.person.loading = !s.person.groups.length; }
        this.renderAll();
        this.send('mode', { mode: mode });
      }
    }
    else if (a === 'filters') { this.menuOpen = !this.menuOpen; this.renderHead(); }
    else if (a === 'clear-search') { this.r.search.value = ''; this.onSearchInput(); this.r.search.focus(); }
    else if (a === 'clear-filters') {
      s.view.filters = normFilters(null); this.r.search.value = ''; this.onSearchInput();
      this.send('filter', { key: 'all', value: true });
    }
    else if (a === 'page') {
      var page = Number(t.getAttribute('data-page'));
      if (page >= 1 && page <= Math.max(1, s.history.total_pages)) {
        s.view.page = page; s.history.loading = true; this.renderAll(); this.send('page', { page: page });
      }
    }
    else if (a === 'settings' || a === 'help' || a === 'storage' || a === 'copy-dock') { this.send(a, {}); }
  };

  Feed.prototype.onChange = function (e) {
    var box = e.target;
    if (!box || !box.getAttribute || !box.hasAttribute('data-filter')) { return; }
    var key = box.getAttribute('data-filter'), val = !!box.checked, f = this.s.view.filters;
    f[key] = val;
    if (key === 'all' && val) { Object.keys(f).forEach(function (k) { f[k] = true; }); }
    else if (key !== 'all') {
      f.all = val && Object.keys(f).every(function (k) { return k === 'all' || k === key || f[k]; });
    }
    this.renderAll();
    this.send('filter', { key: key, value: val });
  };

  Feed.prototype.onSearchInput = function () {
    var self = this, text = this.r.search.value || '';
    this.s.view.search = text;
    this.pendingSearch = text;
    this.renderHead();
    this.renderList();
    if (this.searchTimer) { clearTimeout(this.searchTimer); }
    this.searchTimer = setTimeout(function () {
      self.searchTimer = null;
      var send = self.pendingSearch;
      self.pendingSearch = null;
      self.send('search', { text: send });
    }, 250);
  };

  /* ---- rendering ---- */
  Feed.prototype.renderAll = function () {
    var st = this.s.settings, root = this.root;
    root.setAttribute('data-layout', st.layout);
    root.setAttribute('data-density', st.density);
    root.setAttribute('data-color', st.color);
    root.setAttribute('data-tier', st.tier);
    root.setAttribute('data-dots', st.dots ? '1' : '0');
    root.setAttribute('data-text', st.text_size);
    root.setAttribute('data-mode', this.s.view.mode);
    root.classList.toggle('is-wrap', !!st.wrap);
    root.classList.toggle('no-icons', !st.icons);
    root.classList.toggle('is-paused-state', !!this.s.queue.paused);
    this.renderHead();
    this.renderStrip();
    this.renderList();
    this.renderPager();
  };

  Feed.prototype.playingInfo = function () {
    var q = this.s.queue;
    return q.playing || (q.playingFlag ? { alert_id: null, type: '', username: '' } : null);
  };

  Feed.prototype.renderHead = function () {
    var s = this.s, q = s.queue, r = this.r, playing = this.playingInfo();
    r.pause.className = q.paused ? 'is-paused' : (playing ? 'is-breath' : '');
    r.pause.innerHTML = mi(q.paused ? 'play_arrow' : 'pause') + '<span class="lb">' + (q.paused ? 'Resume' : 'Pause') + '</span>';
    r.pause.title = q.paused ? 'Resume alerts: waiting alerts play in order' : 'Pause alerts: new alerts wait until you resume';
    r.mute.className = q.muted ? 'is-muted' : '';
    r.mute.innerHTML = mi(q.muted ? 'volume_off' : 'volume_up') + '<span class="lb">' + (q.muted ? 'Unmute' : 'Mute') + '</span>';
    r.mute.title = q.muted ? 'Alert sound is off. Click to turn it back on' : 'Turn alert sound off (alerts still show)';
    r.skip.title = playing ? 'Stop the alert that is playing now' : 'Skip the alert that is playing (nothing is playing)';
    if (r.pill) {
      var waiting = q.queue.length;
      var pill = r.pill;
      if (q.paused) {
        pill.className = 'afx-pill is-warn';
        pill.innerHTML = '<span class="dot"></span>Alerts paused' + (waiting ? ' · ' + waiting + ' waiting' : '');
      } else if (playing) {
        pill.className = 'afx-pill is-ok is-live';
        pill.innerHTML = '<span class="dot"></span>Playing' + (waiting ? ' · ' + waiting + ' up next' : '');
      } else if (waiting) {
        pill.className = 'afx-pill is-ok is-live';
        pill.innerHTML = '<span class="dot"></span>' + waiting + ' up next';
      } else {
        pill.className = 'afx-pill is-ok';
        pill.innerHTML = '<span class="dot"></span>Ready' + (q.muted ? ' · muted' : '');
      }
    }
    this.root.querySelectorAll('[data-a="mode"]').forEach(function (b) {
      b.classList.toggle('is-on', b.getAttribute('data-mode') === s.view.mode);
    });
    var f = s.view.filters, on = 0, total = FILTERS.length - 1;
    FILTERS.forEach(function (x) { if (x[0] !== 'all' && f[x[0]] !== false) { on += 1; } });
    var shownAll = f.all || on === total;
    r.fcount.style.display = shownAll ? 'none' : '';
    r.fcount.textContent = on + '/' + total;
    this.root.querySelectorAll('[data-filter]').forEach(function (box) {
      box.checked = f[box.getAttribute('data-filter')] !== false;
    });
    r.menu.classList.toggle('is-open', this.menuOpen);
    r.search.parentNode.classList.toggle('has-text', !!r.search.value);
    if (r.sync) {
      var open = s.meta.dock_open;
      r.sync.classList.toggle('is-off', open === false);
      r.sync.innerHTML = open === false ? mi('link') + 'OBS dock not open · copy link' : mi('sync') + 'In sync with the OBS dock';
      r.sync.title = open === false ? 'Copy the OBS custom browser dock address' : 'Filters, search and the open view match the OBS dock. Click to copy the dock address.';
    }
  };

  Feed.prototype.findLive = function (alertId) {
    if (!alertId) { return null; }
    var rows = this.s.live;
    for (var i = 0; i < rows.length; i++) { if (rows[i].alert_id === alertId) { return rows[i]; } }
    var h = this.s.history.rows;
    for (var j = 0; j < h.length; j++) { if (h[j].alert_id === alertId) { return h[j]; } }
    return null;
  };

  Feed.prototype.upChips = function (limit) {
    var self = this, q = this.s.queue.queue;
    var shown = q.slice(0, limit);
    var chips = shown.map(function (e, i) {
      var row = self.findLive(e.alert_id) || queueRow(e, i + 1);
      return '<span class="afx-upi" style="--tc:' + row.color + '" title="' + esc(row.user + ' ' + row.what) + '"><b>' + (i + 1) + '</b>' + mi(row.icon) + esc(row.user) + '</span>';
    }).join('');
    var more = q.length > limit ? '<span class="afx-upmore">+' + (q.length - limit) + ' more</span>' : '';
    return chips + more;
  };

  Feed.prototype.renderStrip = function () {
    var s = this.s, q = s.queue, st = s.settings, el = this.r.strip, playing = this.playingInfo();
    if (this.dock && s.meta.connected === false) {
      el.innerHTML = '<div class="afx-now is-idle"><span class="lbl">' + mi('cloud_off') + 'Offline</span><span class="who">Reconnecting to Mycelian…</span></div>';
      return;
    }
    if (q.paused) {
      var chips = q.queue.length ? '<span class="afx-up"><span class="k">Waiting</span>' + this.upChips(5) + '</span>' : '';
      el.innerHTML = '<div class="afx-paused"><span class="lbl">' + mi('pause_circle') + 'Alerts paused</span>' +
        '<span class="txt">' + (q.queue.length ? 'These play in this order when you resume' : 'New alerts wait in order below and play when you resume') + '</span>' +
        chips + '<button type="button" class="afx-resume" data-a="resume">' + mi('play_arrow') + 'Resume</button></div>';
      return;
    }
    if (s.view.mode !== 'live' || !st.now_playing || (!playing && !q.queue.length)) { el.innerHTML = ''; return; }
    var up = st.up_next > 0 && q.queue.length ? '<span class="afx-up"><span class="k">Up next</span>' + this.upChips(st.up_next) + '</span>' : '';
    if (!playing) {
      el.innerHTML = '<div class="afx-now is-idle"><span class="lbl">' + mi('queue_music') + 'Starting next</span><span class="afx-spacer"></span>' + up + '</div>';
      return;
    }
    var row = this.findLive(playing.alert_id);
    var who = row ? '<b>' + esc(row.user) + '</b> ' + esc(this.s.settings.sub_duration && row.what_full ? row.what_full : row.what) +
      (row.amount ? ' ' + esc(row.amount) : '')
      : (playing.username ? '<b>' + esc(playing.username) + '</b> ' + esc(meta(kindOf(playing.type))[4]) : 'An alert');
    if (playing.is_replay) { who += ' <span class="afx-upmore">(replay)</span>'; }
    var timer = '';
    if (st.timer === 'strip') {
      timer = playing.duration_ms && playing.started_at_ms
        ? '<span class="bar"><i data-afx-bar></i></span><span class="left" data-afx-left></span>'
        : '<span class="bar is-indet"><i></i></span>';
    }
    el.innerHTML = '<div class="afx-now"><span class="lbl">' + EQ + 'Now playing</span><span class="who">' + who + '</span>' + timer +
      '<span class="afx-spacer"></span>' + up + '</div>';
    this.tickTimers();
  };

  Feed.prototype.tickTimers = function () {
    var q = this.s.queue, p = q.playing;
    var bar = this.root.querySelector('[data-afx-bar]'), left = this.root.querySelector('[data-afx-left]');
    if (!p || !p.duration_ms || !p.started_at_ms || (!bar && !left)) { return; }
    var elapsed = Date.now() + q.offset - p.started_at_ms;
    var pct = Math.min(100, Math.max(0, elapsed / p.duration_ms * 100));
    if (bar) { bar.style.width = pct.toFixed(1) + '%'; }
    if (left) { left.textContent = mmss(p.duration_ms - elapsed); }
  };

  Feed.prototype.timeHtml = function (ts) {
    var mode = this.s.settings.times;
    if (mode === 'hidden') { return ''; }
    return '<span class="time" data-ts="' + Number(ts || 0) + '">' + esc(mode === 'clock' ? clockTime(ts) : relTime(ts)) + '</span>';
  };

  /* Multi-month tag: the same look in every layout, chosen in App Settings. */
  function monthsTag(a, style) {
    var m = Number(a.months) || 0;
    if (m < 2 || !a.duration) { return ''; }
    var tip = a.duration_kind === 'resub' ? 'Renewed ' + m + ' months at once'
      : a.duration_kind === 'each' ? 'Each gifted sub lasts ' + m + ' months'
      : a.duration_kind === 'gift' ? 'This gift lasts ' + m + ' months' : 'Paid for ' + m + ' months at once';
    var full = esc(a.duration), brief = esc(a.duration_short || a.duration);
    function cap(withNum) {
      var strip = function (t) { return withNum ? t : t.replace(/^\d+\s/, ''); };
      return '<span class="cap"><span class="l">' + strip(full) + '</span><span class="s">' + strip(brief) + '</span></span>';
    }
    var inner;
    if (style === 'chip') {
      inner = cap(true);
    } else if (style === 'calendar') {
      inner = '<span class="cal"><i></i><b class="n">' + m + '</b></span>' + cap(false);
    } else if (style === 'dots') {
      var pips = '';
      for (var i = 0; i < Math.min(m, 12); i++) { pips += '<i></i>'; }
      inner = '<span class="pips">' + pips + '</span>' + cap(true);
    } else {
      inner = '<b class="n">' + m + '</b>' + cap(false);
    }
    return '<span class="mm mm-' + style + '" title="' + esc(tip) + '">' + inner + '</span>';
  }
  /* A tag sharing the text's line shortens when the text is cut off; one on its own line only if it doesn't fit. */
  function crampedTag(tag) {
    var row = tag.closest('.afx-row');
    var below = tag.closest('.mm-below');
    var what = !below && row && row.querySelector('.what');
    if (what && what.scrollWidth > what.clientWidth + 1) { return true; }
    var box = below || tag.closest('.line') || row;
    return !!box && box.scrollWidth > box.clientWidth + 1;
  }
  Feed.prototype.fitMonths = function () {
    var self = this, tags = this.r.list.querySelectorAll('.afx-row .mm');
    if (!this.mmObserver && window.ResizeObserver) {
      // Re-fit when the panel width changes (dock resized, window narrowed).
      this.mmObserver = new ResizeObserver(function () {
        var w = self.r.list.clientWidth;
        if (w !== self.mmWidth) { self.mmWidth = w; self.fitMonths(); }
      });
      this.mmObserver.observe(this.r.list);
    }
    Array.prototype.forEach.call(tags, function (tag) {
      tag.classList.remove('is-short');
      if (crampedTag(tag)) { tag.classList.add('is-short'); }
    });
  };

  Feed.prototype.rowHtml = function (it) {
    var a = it.row, st = this.s.settings, q = this.s.queue, tag = it.tag;
    var queued = tag && tag.state === 'queued', playing = tag && tag.state === 'playing';
    var replayable = !!a.alert_id && !a.synthetic;
    var isNew = !a.restored && !a.synthetic && st.new_minutes > 0 && (nowSec() - Number(a.ts || 0)) < st.new_minutes * 60;
    var tierTxt = a.tier ? esc(a.tier) : '';
    var ico = st.icons ? '<span class="ico">' + mi(a.icon) + (tierTxt ? '<span class="tb">' + tierTxt + '</span>' : '') + '</span>' : '';
    var seq = st.order_numbers && a.seq != null ? '<span class="seq">#' + esc(a.seq) + '</span>' : '';
    var state = queued ? '<span class="waittag">' + (q.paused ? 'Waiting' : 'Up next') + ' · ' + ordinal(tag.pos) + '</span>'
      : (playing ? '<span class="playtag">' + EQ + 'Playing</span>' : '');
    var plat = !st.platform ? '' : (PLAT_MARKS[a.platform] || '');
    var amt = a.amount ? '<span class="amt">' + esc(a.amount) + '</span>' : '';
    var tierName = tierTxt ? '<span class="tier">' + tierTxt + '</span>' : '';
    // Multi-month tag (Show multi-month subs): after the text, on the right, or on its own line.
    var mk = st.sub_duration ? monthsTag(a, st.sub_duration_style) : '';
    var place = st.sub_duration_place;
    // The ticker is one line per event; narrow dock rows would squeeze the text to nothing beside the tag.
    if (st.layout === 'ticker' && place === 'below') { place = 'inline'; }
    if (this.dock && st.layout === 'rows' && place === 'inline') { place = 'below'; }
    var mkInline = place === 'inline' ? mk : '', mkRight = place === 'right' ? mk : '';
    var mkBelow = place === 'below' && mk ? '<div class="mm-below">' + mk + '</div>' : '';
    var whatTxt = mk ? (a.what_tagged || a.what) : a.what;
    var extra = (a.replay ? '<span class="replaytag">Replay</span>' : '') + (it.newest ? '<span class="newest">Newest</span>' : '');
    var msg = st.messages && a.msg ? '<div class="msg">“' + esc(a.msg) + '”</div>' : '';
    var acts = '';
    if (playing) {
      acts = '<div class="acts is-always"><button type="button" data-a="row-skip" data-id="' + esc(a.alert_id || '') + '" title="Stop this alert now">' + mi('skip_next') + '<span class="lb">Skip</span></button></div>';
    } else if (!queued && replayable) {
      acts = '<div class="acts"><span class="hint">' + mi('replay') + 'Click to replay</span></div>';
    }
    var tip = queued ? (q.paused ? 'Waiting to play · alerts are paused' : 'Waiting to play')
      : (playing ? 'Playing now · click Skip to stop it'
        : (replayable ? 'Click to replay this alert for ' + a.user : 'This event has no saved alert, so it can\'t be replayed'));
    var cls = 'afx-row' + (isNew ? ' is-new' : '') + (playing ? ' is-playing' : '') + (queued ? ' is-waiting' : '') +
      (a.restored ? ' is-old' : '') + (!queued && !playing && replayable ? ' is-click' : '');
    var timer = '';
    if (st.timer === 'item' && playing) {
      var p = q.playing;
      if (p && p.duration_ms && p.started_at_ms) {
        var el = Math.max(0, Date.now() + q.offset - p.started_at_ms);
        timer = '<i class="tbar is-run" style="animation-duration:' + Math.round(p.duration_ms) + 'ms;animation-delay:-' + Math.round(Math.min(el, p.duration_ms)) + 'ms"></i>';
      } else {
        timer = '<i class="tbar is-indet"></i>';
      }
    } else if (st.timer === 'item' && queued) {
      timer = '<i class="tbar is-full"></i>';
    }
    var attrs = ' class="' + cls + '" style="--tc:' + esc(a.color) + '" title="' + esc(tip) + '" data-id="' + esc(a.alert_id || '') +
      '" data-user="' + esc(a.user) + '" data-label="' + esc(a.label) + '"';
    var time = this.timeHtml(a.ts);
    if (st.layout === 'cards') {
      return '<div' + attrs + '>' + timer + '<div class="top">' + ico + '<span class="user">' + esc(a.user) + '</span>' + plat + seq +
        (time ? time.replace('class="time"', 'class="time" style="margin-left:6px"') : '') + '</div>' +
        (state ? '<div>' + state + '</div>' : '') + '<div class="line"><span class="what">' + esc(whatTxt) + '</span>' + tierName + mkInline + amt +
        (mkRight ? '<span class="mm-push"></span>' + mkRight : '') + extra + '</div>' + mkBelow +
        msg + acts + '</div>';
    }
    var line = '<div class="line"><span class="user">' + esc(a.user) + '</span><span class="what">' + esc(whatTxt) + '</span>' + tierName + mkInline + extra + '</div>' + mkBelow + msg;
    if (this.dock && state) {
      // Narrow dock: the Up next / Playing tag goes under the name instead of beside it.
      line += '<div class="st">' + state + '</div>';
      state = '';
    }
    return '<div' + attrs + '>' + timer + ico + '<div class="body">' + line + '</div>' + state + mkRight + amt + plat + time + seq + acts + '</div>';
  };

  Feed.prototype.head = function (text, arrow, cls) {
    return '<div class="afx-day' + (cls ? ' ' + cls : '') + '">' + esc(text) + (arrow || '') + '</div>';
  };

  Feed.prototype.emptyHtml = function (icon, text, clear) {
    return '<div class="afx-empty">' + mi(icon) + esc(text) + (clear ? '<br><button type="button" data-a="clear-filters">Show everything</button>' : '') + '</div>';
  };

  Feed.prototype.filtering = function () {
    var v = this.s.view;
    return !v.filters.all || !!String(v.search || '').trim();
  };

  Feed.prototype.queueItems = function () {
    var self = this, q = this.s.queue, items = [];
    q.queue.forEach(function (e, i) {
      var row = self.findLive(e.alert_id);
      items.push({ row: row || queueRow(e, i + 1), tag: { state: 'queued', pos: i + 1 }, synthetic: !row });
    });
    var playingItem = null, p = q.playing;
    if (p) {
      var prow = this.findLive(p.alert_id);
      playingItem = { row: prow || queueRow(p, 0), tag: { state: 'playing', pos: 0 }, synthetic: !prow };
    }
    return { queued: items, playing: playingItem };
  };

  Feed.prototype.renderList = function () {
    this.renderListHtml();
    this.fitMonths();
  };

  Feed.prototype.renderListHtml = function () {
    var list = this.r.list, s = this.s, mode = s.view.mode, self = this;
    list.classList.toggle('is-feed', mode !== 'person');
    if (mode === 'person') { list.innerHTML = this.personHtml(); return; }
    var st = s.settings, filters = s.view.filters, search = s.view.search;
    var qi = this.queueItems();
    var tags = {};
    qi.queued.forEach(function (it) { if (it.row.alert_id && !it.synthetic) { tags[it.row.alert_id] = it.tag; } });
    if (qi.playing && qi.playing.row.alert_id && !qi.playing.synthetic) { tags[qi.playing.row.alert_id] = qi.playing.tag; }
    var source = mode === 'history' ? s.history.rows : s.live;
    var rows = source.filter(function (r) { return filterMatch(r, filters, search); });
    var html = '';
    if (mode === 'history') {
      if (s.history.loading && !rows.length) { list.innerHTML = this.emptyHtml('hourglass_empty', 'Loading history…'); return; }
      if (s.history.error) { list.innerHTML = this.emptyHtml('error_outline', 'Couldn\'t load stored alerts. Check Settings → Database.'); return; }
      if (!rows.length) {
        list.innerHTML = source.length ? this.emptyHtml('filter_alt_off', 'Nothing on this page matches your filters or search.', true)
          : this.emptyHtml('inventory_2', 'No stored alerts yet. Past events are kept here between streams.');
        return;
      }
      var lastDay = null;
      rows.forEach(function (r) {
        var d = dayLabel(r.ts);
        if (d !== lastDay) { html += self.head(d); lastDay = d; }
        html += self.rowHtml({ row: r, tag: (r.alert_id && tags[r.alert_id]) || null });
      });
      list.innerHTML = html;
      return;
    }
    var earlierSec = st.earlier_minutes > 0 ? st.earlier_minutes * 60 : 0;
    var now = nowSec();
    var isEarlier = function (r) { return earlierSec > 0 && (now - Number(r.ts || 0)) >= earlierSec; };
    if (st.layout === 'ticker') {
      var upItems = qi.queued;
      if (upItems.length) {
        html += this.head(s.queue.paused ? 'Waiting to play' : 'Up next', '<span class="afx-arrow">' + mi('south') + 'plays in this order</span>', 'is-block is-warn');
        upItems.forEach(function (it) { html += self.rowHtml(it); });
      }
      if (qi.playing) {
        html += this.head('Now playing', '', 'is-block is-ok');
        html += this.rowHtml(qi.playing);
      }
      var rest = rows.filter(function (r) { return !(r.alert_id && tags[r.alert_id]); });
      var recent = rest.filter(function (r) { return !isEarlier(r); });
      var older = rest.filter(isEarlier);
      if (rest.length) {
        html += this.head('Feed · newest first', '<span class="afx-arrow">' + mi('north') + 'latest at the top</span>', 'is-block');
        recent.forEach(function (r, i) { html += self.rowHtml({ row: r, tag: null, newest: i === 0 }); });
        if (older.length) {
          html += this.head('Earlier');
          older.forEach(function (r, i) { html += self.rowHtml({ row: r, tag: null, newest: !recent.length && i === 0 }); });
        }
      } else if (!upItems.length && !qi.playing) {
        html = this.filtering() && s.live.length ? this.emptyHtml('filter_alt_off', 'Nothing matches your filters or search.', true)
          : this.emptyHtml('dynamic_feed', 'No events yet this session. Follows, subs, cheers and raids show up here as they happen.');
      }
      list.innerHTML = html;
      return;
    }
    var top = [];
    for (var i = qi.queued.length - 1; i >= 0; i--) { if (qi.queued[i].synthetic) { top.push(qi.queued[i]); } }
    if (qi.playing && qi.playing.synthetic) { top.push(qi.playing); }
    var items = top.concat(rows.map(function (r) { return { row: r, tag: (r.alert_id && tags[r.alert_id]) || null }; }));
    if (!items.length) {
      list.innerHTML = this.filtering() && s.live.length ? this.emptyHtml('filter_alt_off', 'Nothing matches your filters or search.', true)
        : this.emptyHtml('dynamic_feed', 'No events yet this session. Follows, subs, cheers and raids show up here as they happen.');
      return;
    }
    var recentItems = items.filter(function (it) { return it.tag || it.synthetic || !isEarlier(it.row); });
    var olderItems = items.filter(function (it) { return !(it.tag || it.synthetic) && isEarlier(it.row); });
    var label = earlierSec ? 'Recent · last ' + minutesLabel(st.earlier_minutes) : 'This session';
    if (recentItems.length) {
      html += this.head(label, '<span class="afx-arrow">' + mi('north') + 'newest first</span>');
      recentItems.forEach(function (it) { html += self.rowHtml(it); });
    }
    if (olderItems.length) {
      html += this.head('Earlier', recentItems.length ? '' : '<span class="afx-arrow">' + mi('north') + 'newest first</span>');
      olderItems.forEach(function (it) { html += self.rowHtml(it); });
    }
    list.innerHTML = html;
  };

  Feed.prototype.personHtml = function () {
    var p = this.s.person, self = this;
    var hours = p.hours || this.s.settings.person_hours || 12;
    if (p.loading && !p.groups.length) { return this.emptyHtml('hourglass_empty', 'Grouping the last ' + hours + ' hours by person…'); }
    if (p.error) { return this.emptyHtml('error_outline', 'Couldn\'t group alerts by person. Showing nothing rather than a partial list.'); }
    if (!p.groups.length) {
      return this.filtering() ? this.emptyHtml('filter_alt_off', 'Nobody matches your filters or search in the last ' + hours + ' hours.', true)
        : this.emptyHtml('group_work', 'No alerts to group in the last ' + hours + ' hours.');
    }
    var head = '<div class="afx-cond-head">' + mi('history') + '<span>' + num(p.alert_count) + ' events from ' + num(p.user_count || p.groups.length) +
      (p.user_count === 1 ? ' person' : ' people') + ' in the last ' + hours + ' hours</span>' +
      (p.truncation ? '<span class="afx-arrow">' + esc(p.truncation) + '</span>' : '') + '</div>';
    var cards = p.groups.map(function (g) {
      var name = String(g.username || '');
      var events = Number(g.events) || (g.items || g.lines || []).length;
      var last = g.last_ts ? (events === 1 ? relTime(g.last_ts) + ' ago' : 'last ' + relTime(g.last_ts) + ' ago') : '';
      if (last.indexOf('just now ago') !== -1) { last = last.replace('just now ago', 'just now'); }
      var sub = events + (events === 1 ? ' event' : ' events') + (last ? ' · ' + last : '');
      var totals = (g.totals || []).map(function (t) {
        return '<span class="afx-total" style="--tc:' + esc(t.color || meta(t.kind)[1]) + '">' + mi(t.icon || meta(t.kind)[0]) + esc(t.text) + '</span>';
      }).join('');
      var lines = g.items ? g.items.map(function (l) {
        return '<div class="afx-ln" style="--tc:' + esc(l.color || meta(l.kind)[1]) + '">' + mi(l.icon || meta(l.kind)[0]) + '<span class="t">' + esc(l.text) + '</span>' +
          (l.count > 1 ? '<span class="x">×' + esc(l.count) + '</span>' : '') + '</div>';
      }).join('') : (g.lines || []).map(function (l) {
        return '<div class="afx-ln">' + mi('chevron_right') + '<span class="t">' + esc(l) + '</span></div>';
      }).join('');
      var pic = /^https:\/\//.test(String(g.avatar || '')) ? String(g.avatar) : '';
      var ava = '<span class="afx-ava' + (pic ? ' has-photo' : '') + '">' + esc((name.charAt(0) || '?').toUpperCase()) +
        (pic ? '<img src="' + esc(pic) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">' : '') + '</span>';
      return '<div class="afx-user"><div class="afx-user-head">' + ava +
        '<div class="afx-user-id"><div class="afx-user-name">' + esc(name) + '</div><div class="afx-user-sub">' + esc(sub) + '</div></div>' +
        '<div class="afx-totals">' + totals + '</div></div><div class="afx-lines">' + lines + '</div></div>';
    }).join('');
    void self;
    return head + '<div class="afx-cond">' + cards + '</div>';
  };

  Feed.prototype.renderPager = function () {
    var s = this.s, el = this.r.pager;
    if (s.view.mode !== 'history') { el.innerHTML = ''; return; }
    var h = s.history, page = s.view.page || h.page || 1, total = Math.max(1, h.total_pages || 1);
    var link = this.dock ? '' : '<button type="button" class="afx-link" data-a="storage" title="How many events are kept: Settings → App Settings → Storage">' + mi('inventory_2') + 'Storage settings</button>';
    el.innerHTML = '<div class="afx-pager"><button type="button" data-a="page" data-page="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') + ' title="Newer">' + mi('chevron_left') + '</button>' +
      '<span>Page ' + page + ' of ' + total + '</span><span class="tot">' + (h.total_count ? '(' + num(h.total_count) + ' alerts)' : '') + '</span>' +
      '<button type="button" data-a="page" data-page="' + (page + 1) + '"' + (page >= total ? ' disabled' : '') + ' title="Older">' + mi('chevron_right') + '</button>' + link + '</div>';
  };

  Feed.prototype.tick = function () {
    this.tickN += 1;
    this.tickTimers();
    if (this.tickN % 15 === 0) {
      var mode = this.s.settings.times;
      if (mode === 'relative') {
        this.root.querySelectorAll('.time[data-ts]').forEach(function (t) {
          var txt = relTime(t.getAttribute('data-ts'));
          if (t.textContent !== txt) { t.textContent = txt; }
        });
      }
    }
    if (this.tickN % 60 === 0 && this.s.view.mode !== 'person') { this.renderList(); }
  };

  setInterval(function () {
    for (var i = instances.length - 1; i >= 0; i--) {
      var inst = instances[i];
      if (!inst.isAlive() && inst.hostBound) { inst.destroy(); continue; }
      try { inst.tick(); } catch (e) { /* keep ticking the others */ }
    }
  }, 1000);

  /* ---- app tab hosts (NiceGUI) ---- */
  function appSend(action, payload) {
    var data = Object.assign({ action: action }, payload || {});
    if (typeof window.emitEvent === 'function') { window.emitEvent('mycelian-afx', data); }
  }
  function bindHosts() {
    var hosts = document.querySelectorAll('.afx-host[data-afx-app]');
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i], mount = null;
      for (var c = 0; c < host.children.length; c++) {
        if (host.children[c].classList.contains('afx-mount')) { mount = host.children[c]; break; }
      }
      if (!mount) { continue; }
      var inst = host.__afx;
      if (!inst || inst.mount !== mount || !mount.contains(inst.root)) {
        if (inst) { inst.destroy(); }
        inst = host.__afx = new Feed(mount, { dock: false, send: appSend });
        inst.hostBound = true;
        inst.revs = {};
      }
      var changed = false;
      for (var j = 0; j < host.children.length; j++) {
        var slot = host.children[j];
        if (!slot.classList.contains('afx-slot')) { continue; }
        var name = slot.getAttribute('data-slot'), rev = slot.getAttribute('data-rev');
        if (inst.revs[name] === rev) { continue; }
        inst.revs[name] = rev;
        try { inst.set(name, JSON.parse(slot.getAttribute('data-json') || 'null'), { defer: true }); changed = true; }
        catch (e) { if (window.console) { console.warn('activity feed: bad slot', name, e); } }
      }
      if (changed) { inst.renderAll(); }
      host.setAttribute('data-afx-bound', String(VERSION));
    }
  }
  function health() {
    var hosts = document.querySelectorAll('.afx-host[data-afx-app]'), out = { ok: true, version: VERSION, hosts: hosts.length, bound: 0, painted: 0, rows: 0, visible: 0 };
    for (var i = 0; i < hosts.length; i++) {
      var h = hosts[i], inst = h.__afx;
      var r = h.getBoundingClientRect();
      if (r.width > 2 && r.height > 2) { out.visible += 1; }
      if (inst && inst.isAlive()) {
        out.bound += 1;
        out.rows += inst.root.querySelectorAll('.afx-row, .afx-user, .afx-empty').length;
        if (inst.root.querySelector('.afx-shell')) { out.painted += 1; }
      }
    }
    out.ok = out.hosts > 0 && out.bound === out.hosts && out.painted === out.hosts;
    return out;
  }

  window.MycelianFeed = {
    version: VERSION, Feed: Feed, create: function (mount, opts) { return new Feed(mount, opts); },
    bindHosts: bindHosts, health: health, rowFromAlert: rowFromAlert, normSettings: normSettings, normQueue: normQueue,
    filterMatch: filterMatch, kindOf: kindOf, ordinal: ordinal, instances: instances, monthsTag: monthsTag
  };
  if (!window.__mycelianFeedBinder) {
    window.__mycelianFeedBinder = setInterval(function () { try { window.MycelianFeed.bindHosts(); } catch (e) { /* retry next tick */ } }, 250);
  }
  try { bindHosts(); } catch (e) { /* first bind may run before the host exists */ }
})();
