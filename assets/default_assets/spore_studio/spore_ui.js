/*
 * Spore Studio shared UI: line icons, modal dialogs and the confirm prompt.
 * Loaded before editor.js, container_editor.js and walkthrough.js, which use window.SporeUI.
 */
(function () {
    "use strict";

    var PATHS = {
        layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
        grid: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
        plus: '<path d="M12 5v14M5 12h14"/>',
        trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
        undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
        redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
        compass: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5 5-2Z"/>',
        refresh: '<path d="M20 11a8 8 0 0 0-14.9-3.5M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.9 3.5M20 20v-4h-4"/>',
        external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
        play: '<path d="M7 4v16l13-8L7 4Z"/>',
        check: '<path d="m5 12 5 5 9-10"/>',
        search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
        chevron: '<path d="m9 6 6 6-6 6"/>',
        x: '<path d="M6 6l12 12M18 6 6 18"/>',
        file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/>',
        upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
        folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
        blocks: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>',
        tree: '<path d="M4 5h6M8 12h6M8 19h6M6 5v14h2M6 12h2"/>',
        image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
        sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
        zap: '<path d="M13 3 4 14h7l-1 7 9-11h-7l1-7Z"/>',
        keypad: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
        toggle: '<rect x="2" y="7" width="20" height="10" rx="5"/><circle cx="16" cy="12" r="3"/>',
        code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
        frame: '<path d="M7 3v18M17 3v18M3 7h18M3 17h18"/>',
        tag: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9Z"/><circle cx="8" cy="8" r="1.5"/>',
        move: '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
        eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
        rotate: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
        feed: '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1.5"/>',
        output: '<path d="M14 4h6v6M20 4l-8 8"/><circle cx="7" cy="17" r="3"/>',
        palette: '<path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 2-2s-1-1.5-1-2.5 1-1.5 2-1.5h2a4 4 0 0 0 4-4c0-4.5-4-8-9-8Z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10.5" cy="7" r="1.2"/><circle cx="15.5" cy="7.5" r="1.2"/>',
        sparkles: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
        type: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
        film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
        volume: '<path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
        box: '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5" stroke-dasharray="3 2.5"/>',
        square: '<rect x="4" y="6" width="16" height="12" rx="2.5"/>',
        minus: '<path d="M4 12h16"/>',
        blend: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor"/>',
        marquee: '<path d="M4 9h14l-3-3M20 15H6l3 3"/>',
        clock: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 3h6"/>',
        timer: '<circle cx="12" cy="13" r="8"/><path d="M12 13V9M10 2h4M19 6l1.5-1.5"/>',
        list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
        wheel: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/>',
        music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
        qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 17h4v4h-4"/>',
        globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
        badge: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 15V9l2 3 2-3v6M15 9v6h3"/>',
        poll: '<path d="M4 6h10M4 12h16M4 18h7"/>',
        party: '<path d="m4 20 4-11 7 7-11 4Z"/><path d="M14 4v2M20 10h-2M17 7l1.5-1.5M11 5c.5 1.5 0 3-1 4M19 13c-1.5-.5-3 0-4 1"/>',
        hash: '<path d="M5 9h15M4 15h15M10 3 8 21M16 3l-2 18"/>',
        progress: '<rect x="2" y="9" width="20" height="6" rx="3"/><path d="M5 12h8" stroke-width="3"/>',
        braces: '<path d="M8 4H7a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h1M16 4h1a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-1"/>',
        alert: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/>',
        info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
        queue: '<path d="M4 6h16M4 12h16M4 18h10"/><path d="m18 16 2 2-2 2"/>',
        drag: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
        link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'
    };

    var TYPE_ICONS = {
        text: "type", image: "image", video: "film", audio: "volume", container: "box", shape: "square", divider: "minus",
        gradient: "blend", marquee: "marquee", clock: "clock", timer: "timer", list: "list", wheel: "wheel",
        now_playing: "music", qr: "qr", embed: "globe", stat_card: "badge", poll: "poll", confetti: "party",
        counter: "hash", progress_bar: "progress", data_display: "braces"
    };

    function icon(name, extraClass) {
        var cls = "ss-ic" + (extraClass ? " " + extraClass : "");
        return '<span class="' + cls + '" aria-hidden="true"><svg viewBox="0 0 24 24">' +
            (PATHS[name] || PATHS.box) + "</svg></span>";
    }

    function iconNode(name, extraClass) {
        var wrap = document.createElement("span");
        wrap.innerHTML = icon(name, extraClass);
        return wrap.firstChild;
    }

    function typeIcon(type, extraClass) {
        return icon(TYPE_ICONS[String(type || "").toLowerCase()] || "box", extraClass);
    }

    /* Fill every [data-ss-icon] placeholder (static markup in editor.html). */
    function hydrate(root) {
        Array.prototype.forEach.call((root || document).querySelectorAll("[data-ss-icon]"), function (node) {
            if (node.querySelector(":scope > .ss-ic")) { return; }
            node.insertAdjacentHTML("afterbegin", icon(node.getAttribute("data-ss-icon")));
        });
    }

    var openModals = [];

    function topModal() { return openModals[openModals.length - 1] || null; }

    document.addEventListener("keydown", function (ev) {
        var top = topModal();
        if (!top) { return; }
        if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            top.cancel();
        }
    }, true);

    /* True while a dialog is open, so editor shortcuts (Delete, undo, arrows) stay out of the way. */
    function modalOpen() { return openModals.length > 0; }

    /*
     * Make an overlay node (.ss-create-overlay holding an .ss-modal) behave like a dialog:
     * Escape and a click on the backdrop call onCancel, the close button too, and the
     * first field gets focus. Returns a release function to call when it closes.
     */
    function wireModal(overlay, onCancel) {
        var entry = { overlay: overlay, cancel: onCancel };
        openModals.push(entry);
        overlay.addEventListener("mousedown", function (ev) {
            if (ev.target === overlay) { onCancel(); }
        });
        Array.prototype.forEach.call(overlay.querySelectorAll("[data-modal-close]"), function (btn) {
            btn.addEventListener("click", onCancel);
        });
        setTimeout(function () {
            var first = overlay.querySelector("input:not([type=hidden]):not([disabled]), select, textarea, .ss-btn--primary");
            if (first) { try { first.focus(); } catch (err) {} }
        }, 30);
        return function release() {
            var i = openModals.indexOf(entry);
            if (i >= 0) { openModals.splice(i, 1); }
        };
    }

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (text != null) { node.textContent = text; }
        return node;
    }

    /* The shared dialog frame: icon, title, subtitle, close button, body and footer. */
    function modalFrame(opts) {
        var overlay = el("div", "ss-create-overlay");
        var card = el("div", "ss-modal" + (opts.wide ? " ss-modal--wide" : ""));
        card.setAttribute("role", "dialog");
        card.setAttribute("aria-modal", "true");
        var head = el("div", "ss-modal__head");
        var badge = el("div", "ss-modal__icon" + (opts.tone === "danger" ? " is-danger" : ""));
        badge.innerHTML = icon(opts.icon || "info");
        var titles = el("div", "ss-modal__titles");
        var title = el("h2", "ss-modal__title", opts.title || "");
        titles.appendChild(title);
        if (opts.sub) { titles.appendChild(el("div", "ss-modal__sub", opts.sub)); }
        var close = el("button", "ss-modal__x");
        close.type = "button";
        close.title = "Close";
        close.setAttribute("aria-label", "Close");
        close.setAttribute("data-modal-close", "");
        close.innerHTML = icon("x");
        head.appendChild(badge);
        head.appendChild(titles);
        head.appendChild(close);
        var body = el("div", "ss-modal__body");
        var foot = el("div", "ss-modal__foot");
        card.appendChild(head);
        card.appendChild(body);
        card.appendChild(foot);
        overlay.appendChild(card);
        return { overlay: overlay, card: card, body: body, foot: foot };
    }

    /*
     * Styled replacement for window.confirm. Resolves true when the user confirms.
     * opts: title, message, detail (text shown in a callout), ok, cancel, danger, icon.
     */
    function confirmDialog(opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            var frame = modalFrame({
                icon: opts.icon || (opts.danger ? "trash" : "info"),
                tone: opts.danger ? "danger" : "",
                title: opts.title || "Are you sure?",
                sub: opts.message || ""
            });
            if (opts.detail) {
                var callout = el("div", "ss-callout ss-callout--warn");
                callout.innerHTML = icon("alert");
                callout.appendChild(el("span", "", opts.detail));
                frame.body.appendChild(callout);
            } else {
                frame.body.classList.add("is-empty");
            }
            var cancel = el("button", "ss-btn", opts.cancel || "Cancel");
            cancel.type = "button";
            var ok = el("button", "ss-btn " + (opts.danger ? "ss-btn--danger-solid" : "ss-btn--primary"), opts.ok || "OK");
            ok.type = "button";
            frame.foot.appendChild(cancel);
            frame.foot.appendChild(ok);
            document.body.appendChild(frame.overlay);
            var release;
            function done(value) {
                if (release) { release(); }
                frame.overlay.remove();
                resolve(value);
            }
            release = wireModal(frame.overlay, function () { done(false); });
            cancel.addEventListener("click", function () { done(false); });
            ok.addEventListener("click", function () { done(true); });
            setTimeout(function () { try { (opts.danger ? cancel : ok).focus(); } catch (err) {} }, 40);
        });
    }

    /* Long help text: show three lines with a "Show more" toggle. */
    function clampHint(node, minChars) {
        if (!node || (node.textContent || "").length < (minChars || 160)) { return; }
        node.classList.add("ss-clamp");
        var more = el("button", "ss-more", "Show more");
        more.type = "button";
        more.addEventListener("click", function () {
            var clamped = node.classList.toggle("ss-clamp");
            more.textContent = clamped ? "Show more" : "Show less";
        });
        node.insertAdjacentElement("afterend", more);
    }

    /* ``code`` spans in registry descriptions, escaped, for innerHTML. */
    function richHint(text) {
        return String(text == null ? "" : text)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/``([^`]+)``/g, "<code>$1</code>");
    }

    /* Dashed empty-state card with an icon, a title and one line of help. */
    function emptyCard(iconName, title, text) {
        var card = el("div", "ss-empty-card");
        card.innerHTML = icon(iconName);
        card.appendChild(el("b", "", title));
        card.appendChild(el("span", "", text));
        return card;
    }

    window.SporeUI = {
        icon: icon,
        iconNode: iconNode,
        typeIcon: typeIcon,
        hydrate: hydrate,
        wireModal: wireModal,
        modalFrame: modalFrame,
        modalOpen: modalOpen,
        confirm: confirmDialog,
        clampHint: clampHint,
        richHint: richHint,
        emptyCard: emptyCard
    };
})();
