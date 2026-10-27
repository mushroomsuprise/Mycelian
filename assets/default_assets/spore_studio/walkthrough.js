/* Short spotlight tour for the Spore Studio editor. */
(function () {
    "use strict";

    var SEEN_KEY = "mycelian-spore-tour-seen";
    var PREFS_URL = "/api/spore-studio/ui-prefs";

    /* The desktop window does not keep localStorage across launches, so the
       "already offered" flag is also stored on the server. */
    function markSeen() {
        try { localStorage.setItem(SEEN_KEY, "1"); } catch (err) {}
        try {
            var body = {};
            body[SEEN_KEY] = true;
            fetch(PREFS_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            }).catch(function () {});
        } catch (err) {}
    }

    function checkSeen() {
        var local = "";
        try { local = localStorage.getItem(SEEN_KEY) || ""; } catch (err) {}
        if (local) { return Promise.resolve(true); }
        return fetch(PREFS_URL)
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (prefs) { return !!(prefs && prefs[SEEN_KEY]); })
            .catch(function () { return false; });
    }
    var STEPS = [
        {
            mode: "templates",
            sel: ".ss-mode",
            title: "Templates and containers",
            body: "Templates designs one overlay. Containers combines several overlays onto a single browser-source route."
        },
        {
            mode: "templates",
            sels: ["#ss-template-select", "#ss-btn-create", "#ss-btn-delete"],
            title: "Open or create a template",
            body: "Pick a template from the list, start a new one, or delete the Spore template that is open. Legacy templates stay protected."
        },
        {
            mode: "templates",
            sels: ["#ss-btn-undo", "#ss-btn-redo"],
            title: "Undo and redo",
            body: "Undo and redo apply to the template you are editing. Keyboard shortcuts are Ctrl+Z and Ctrl+Shift+Z."
        },
        {
            mode: "templates",
            sel: "#ss-blocks-list",
            title: "Blocks",
            body: "Drag a block onto the canvas. Category headers collapse so the list takes less space, and that choice is remembered."
        },
        {
            mode: "templates",
            sel: "#ss-outline-list",
            title: "Outline",
            body: "Every element is listed under its category. Click a row to select that element on the canvas and in the inspector."
        },
        {
            mode: "templates",
            sel: "#ss-asset-grid",
            title: "Assets",
            body: "Upload images, video, and audio for this template. Drag a file onto the canvas, or double-click it to set the selected element's source."
        },
        {
            mode: "templates",
            sel: "#ss-stage",
            title: "Canvas",
            body: "Drag to move an element and use the corner handle to resize it. Drop onto a container to nest it. Click empty space to deselect."
        },
        {
            mode: "templates",
            tab: "properties",
            sel: "#ss-pane-properties",
            title: "Properties",
            body: "Position, size, text, colors, counters, and the checkbox that exposes a value in Source Settings all live on this tab. Every block also has a Data feed section for live events, values, and other templates' outputs."
        },
        {
            mode: "templates",
            tab: "bindings",
            sel: "#ss-pane-bindings",
            title: "Bindings",
            body: "Attach a websocket event to an element and choose an action: show, hide, set text, adjust a counter, and more."
        },
        {
            mode: "templates",
            tab: "streamdeck",
            sel: "#ss-pane-streamdeck",
            title: "Stream Deck",
            body: "Define actions this template exposes to the Stream Deck plugin. Bindings can listen for those actions."
        },
        {
            mode: "templates",
            tab: "sourcecontrols",
            sel: "#ss-pane-sourcecontrols",
            title: "Source Controls",
            body: "Buttons and inputs here show up on Mycelian's Source Controls tab while you are live."
        },
        {
            mode: "templates",
            tab: "advanced",
            sel: "#ss-pane-advanced",
            title: "Advanced JS",
            body: "Custom JavaScript is saved between the user-begin and user-end markers. Expand editor opens a larger editor when the script is long."
        },
        {
            mode: "templates",
            tab: "canvas",
            sel: "#ss-pane-canvas",
            title: "Canvas settings",
            body: "Set the design size, queue or instant alerts, the title, and how long a queued alert should hold."
        },
        {
            mode: "templates",
            sel: "#ss-btn-preview",
            title: "Preview",
            body: "Opens a live preview of unsaved changes and buttons that fire mock alerts, chat, and Stream Deck actions."
        },
        {
            mode: "templates",
            sel: "#ss-btn-save",
            title: "Save",
            body: "Save writes the HTML overlay, the JSON config, and the Spore sidecar. Source Settings then sees any fields you exposed."
        },
        {
            mode: "templates",
            sels: ["#ss-btn-tour", "#ss-btn-reload"],
            title: "Tour and reload",
            body: "Tour starts this walkthrough again. Reload editor fetches a fresh copy of the editor if the page looks stale."
        },
        {
            mode: "containers",
            sel: "#mc-template-list",
            title: "Container library",
            body: "Drag any template onto the container canvas, including overlays that were not built in Spore Studio."
        },
        {
            mode: "containers",
            sel: "#mc-canvas",
            title: "Container canvas",
            body: "Arrange templates in a grid. Dynamic sizing lets one overlay grow while the others keep a minimum size."
        },
        {
            mode: "containers",
            sel: "#mc-inspector-host",
            title: "Container inspector",
            body: "Name the route, set the canvas size, and tune each slot's priority, minimum size, and whether its audio is muted."
        }
    ];

    var index = 0;
    var running = false;
    var startMode = "templates";
    var startTab = "properties";
    var blocker = null;
    var spot = null;
    var card = null;

    function $(sel) { return document.querySelector(sel); }

    function currentMode() {
        var root = document.getElementById("ss-root");
        return root && root.classList.contains("ss-root--containers") ? "containers" : "templates";
    }

    function goMode(mode) {
        if (currentMode() === mode) { return; }
        var id = mode === "containers" ? "ss-mode-containers" : "ss-mode-templates";
        var btn = document.getElementById(id);
        if (btn) { btn.click(); }
    }

    function showTab(name) {
        if (!name) { return; }
        var tab = document.querySelector('.ss-tab[data-tab="' + name + '"]');
        if (tab && !tab.classList.contains("active")) { tab.click(); }
    }

    function visibleRect(el) {
        if (!el) { return null; }
        var r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) { return null; }
        return r;
    }

    function unionRect(selectors) {
        var rect = null;
        (selectors || []).forEach(function (sel) {
            var el = $(sel);
            if (el && el.scrollIntoView) {
                try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (err) {}
            }
            var r = visibleRect(el);
            /* An empty list collapses to nothing; spotlight its section instead. */
            if (!r && el && el.parentElement) { r = visibleRect(el.parentElement); }
            if (!r) { return; }
            if (!rect) {
                rect = { top: r.top, left: r.left, right: r.right, bottom: r.bottom };
                return;
            }
            rect.top = Math.min(rect.top, r.top);
            rect.left = Math.min(rect.left, r.left);
            rect.right = Math.max(rect.right, r.right);
            rect.bottom = Math.max(rect.bottom, r.bottom);
        });
        if (!rect) { return null; }
        return {
            top: rect.top,
            left: rect.left,
            width: rect.right - rect.left,
            height: rect.bottom - rect.top,
            bottom: rect.bottom,
            right: rect.right
        };
    }

    function ensureNodes() {
        if (blocker) { return; }
        blocker = document.createElement("div");
        blocker.className = "ss-tour-blocker";
        blocker.addEventListener("mousedown", function (ev) { ev.preventDefault(); ev.stopPropagation(); });
        blocker.addEventListener("click", function (ev) { ev.preventDefault(); ev.stopPropagation(); });
        spot = document.createElement("div");
        spot.className = "ss-tour-spot";
        card = document.createElement("div");
        card.className = "ss-tour-card";
        card.innerHTML =
            '<h2 data-tour="title"></h2>' +
            '<p data-tour="body"></p>' +
            '<div class="ss-tour-card__nav">' +
            '<span class="ss-tour-card__count" data-tour="count"></span>' +
            '<button type="button" class="ss-btn ss-btn--ghost" data-tour="back">Back</button>' +
            '<button type="button" class="ss-btn ss-btn--ghost" data-tour="skip">Skip</button>' +
            '<button type="button" class="ss-btn ss-btn--primary" data-tour="next">Next</button>' +
            "</div>";
        card.querySelector("[data-tour=back]").addEventListener("click", function () { move(-1); });
        card.querySelector("[data-tour=skip]").addEventListener("click", endTour);
        card.querySelector("[data-tour=next]").addEventListener("click", function () { move(1); });
        document.body.appendChild(blocker);
        document.body.appendChild(spot);
        document.body.appendChild(card);
    }

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    /* Pick a card position that does not overlap the highlighted rectangle. */
    function placeCard(rect) {
        var gap = 14;
        var margin = 8;
        var vw = window.innerWidth;
        var vh = window.innerHeight;
        var width = card.offsetWidth || 340;
        var height = card.offsetHeight || 160;
        var centerX = rect.left + rect.width / 2;
        var rightFirst = centerX < vw / 2;
        var cy = clamp(rect.top + rect.height / 2 - height / 2, margin, vh - height - margin);
        var cx = clamp(rect.left, margin, vw - width - margin);
        var options = {
            right: { left: rect.right + gap, top: cy, ok: rect.right + gap + width <= vw - margin },
            left: { left: rect.left - gap - width, top: cy, ok: rect.left - gap - width >= margin },
            below: { left: cx, top: rect.bottom + gap, ok: rect.bottom + gap + height <= vh - margin },
            above: { left: cx, top: rect.top - gap - height, ok: rect.top - gap - height >= margin }
        };
        var order = rightFirst ? ["right", "left", "below", "above"] : ["left", "right", "below", "above"];
        var pick = null;
        for (var i = 0; i < order.length; i += 1) {
            if (options[order[i]].ok) { pick = options[order[i]]; break; }
        }
        if (!pick) {
            /* Highlight is too large to sit beside: dock in its bottom-right corner, inside the frame. */
            pick = {
                left: clamp(rect.right - width - gap, margin, vw - width - margin),
                top: clamp(rect.bottom - height - gap, margin, vh - height - margin)
            };
        }
        card.style.transform = "none";
        card.style.left = pick.left + "px";
        card.style.top = pick.top + "px";
    }

    function place(step) {
        var sels = step.sels || (step.sel ? [step.sel] : []);
        if (step.tab) {
            /* Spotlight the tab strip together with the pane so the tabs stay visible. */
            sels = [".ss-tabs-wrap"].concat(sels);
        }
        var rect = unionRect(sels);
        if (!rect) {
            spot.style.display = "none";
            card.style.top = "80px";
            card.style.left = "50%";
            card.style.transform = "translateX(-50%)";
            return;
        }
        spot.style.display = "block";
        var pad = 6;
        spot.style.top = (rect.top - pad) + "px";
        spot.style.left = (rect.left - pad) + "px";
        spot.style.width = (rect.width + pad * 2) + "px";
        spot.style.height = (rect.height + pad * 2) + "px";
        placeCard({
            top: rect.top - pad,
            left: rect.left - pad,
            right: rect.right + pad,
            bottom: rect.bottom + pad,
            width: rect.width + pad * 2,
            height: rect.height + pad * 2
        });
    }

    function renderStep() {
        var step = STEPS[index];
        goMode(step.mode || "templates");
        showTab(step.tab || "");
        card.querySelector("[data-tour=title]").textContent = step.title;
        card.querySelector("[data-tour=body]").textContent = step.body;
        card.querySelector("[data-tour=count]").textContent = (index + 1) + " of " + STEPS.length;
        var next = card.querySelector("[data-tour=next]");
        next.textContent = index === STEPS.length - 1 ? "Done" : "Next";
        card.querySelector("[data-tour=back]").disabled = index === 0;
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { place(step); });
        });
    }

    function move(dir) {
        var next = index + dir;
        if (next < 0) { return; }
        if (next >= STEPS.length) { endTour(); return; }
        index = next;
        renderStep();
    }

    function onKey(ev) {
        if (!running) { return; }
        if (ev.key === "Escape") { ev.preventDefault(); endTour(); }
        else if (ev.key === "ArrowRight") { ev.preventDefault(); move(1); }
        else if (ev.key === "ArrowLeft") { ev.preventDefault(); move(-1); }
    }

    function onReflow() {
        if (!running) { return; }
        place(STEPS[index]);
    }

    function startTour() {
        dismissPrompt();
        markSeen();
        if (running) { return; }
        ensureNodes();
        startMode = currentMode();
        var active = document.querySelector("#ss-tabs-scroll .ss-tab.active");
        startTab = (active && active.dataset.tab) || "properties";
        index = 0;
        running = true;
        document.body.classList.add("ss-tour-running");
        blocker.style.display = "block";
        card.style.display = "block";
        document.addEventListener("keydown", onKey, true);
        window.addEventListener("resize", onReflow);
        renderStep();
    }

    function endTour() {
        if (!running) { return; }
        running = false;
        document.body.classList.remove("ss-tour-running");
        document.removeEventListener("keydown", onKey, true);
        window.removeEventListener("resize", onReflow);
        /* Inline display must be cleared explicitly; the `hidden` attribute loses to it. */
        if (blocker) { blocker.style.display = "none"; }
        if (spot) { spot.style.display = "none"; }
        if (card) { card.style.display = "none"; }
        goMode(startMode);
        if (startMode === "templates") { showTab(startTab); }
    }

    function dismissPrompt() {
        var node = document.getElementById("ss-tour-prompt");
        if (node) { node.remove(); }
    }

    function offerFirstRun() {
        checkSeen().then(function (seen) {
            if (seen || document.getElementById("ss-tour-prompt")) { return; }
            showPrompt();
        });
    }

    function showPrompt() {
        var box = document.createElement("div");
        box.id = "ss-tour-prompt";
        box.className = "ss-tour-prompt";
        box.innerHTML =
            "<p>New here? A short tour points at the main parts of Spore Studio. You can open it later from the Tour button.</p>" +
            '<div class="ss-tour-prompt__actions">' +
            '<button type="button" class="ss-btn ss-btn--ghost" data-act="later">Not now</button>' +
            '<button type="button" class="ss-btn ss-btn--primary" data-act="start">Start tour</button>' +
            "</div>";
        box.querySelector("[data-act=later]").addEventListener("click", function () {
            markSeen();
            dismissPrompt();
        });
        box.querySelector("[data-act=start]").addEventListener("click", startTour);
        document.body.appendChild(box);
        /* Showing it once is enough; don't nag if it is simply ignored. */
        markSeen();
    }

    function init() {
        var btn = document.getElementById("ss-btn-tour");
        if (btn) { btn.addEventListener("click", startTour); }
        setTimeout(offerFirstRun, 700);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
