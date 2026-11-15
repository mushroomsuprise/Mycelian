/*
 * Spore Studio container mode. Places existing templates on one route.
 * Layout math lives in container_layout.js.
 */
(function () {
    var Layout = window.MycelianContainerLayout;
    var Measure = window.MycelianContainerMeasure;
    var UI = window.SporeUI;
    /* One colour per shared space, in the order the spaces first appear. */
    var SPACE_COLORS = ["#4fc3f7", "#ffb74d", "#ba68c8", "#81c784", "#f06292", "#ffd54f"];
    var SIMULATE_LABELS = { empty: "Empty", half: "Half full", full: "Full" };
    var state = {
        mode: "templates",
        list: [],
        templates: { spore: [], legacy: [], builtin: [] },
        model: null,
        savedRoute: null,
        dirty: false,
        selectedId: null,
        history: [],
        future: [],
        placeholders: localStorage.getItem("mycelian-container-placeholders") === "1",
        used: {},
        origins: {},
        // Pretend sizes for "fits its content" boxes (editor only, never saved): id -> empty|half|full.
        simulate: {},
        explicit: Measure ? Measure.createExplicitSizes() : null,
        flagged: [],
        dragging: false,
        known: {},
        splitPadding: {}
    };

    function $(id) { return document.getElementById(id); }

    window.MycelianContainerEditor = {
        isDirty: function () { return !!state.dirty; }
    };

    /** In-page toast; only used if editor.js's notifier is missing. */
    function localToast(text, kind) {
        var el = $("ss-toast");
        if (!el) { return; }
        el.textContent = text;
        el.classList.remove("ss-toast--success", "ss-toast--error");
        if (kind === "success") { el.classList.add("ss-toast--success"); }
        if (kind === "error") { el.classList.add("ss-toast--error"); }
        el.classList.add("visible");
        clearTimeout(el._mcTimeout);
        el._mcTimeout = setTimeout(function () { el.classList.remove("visible"); }, kind === "error" ? 10000 : 3500);
    }

    /**
     * Same notifier as the template editor (editor.js): app pop-up and
     * notification centre in Mycelian, in-page toast in an external browser.
     * opts: { type, title, message, hint, details, dedupe }
     */
    function notify(opts) {
        if (typeof window.sporeNotify === "function") {
            window.sporeNotify(opts);
            return;
        }
        localToast([opts.title, opts.message, opts.hint].filter(function (s) { return !!s; }).join(" "), opts.type);
    }

    /** Error for a failed request: 4xx text is a plain message; 5xx text goes to details only. */
    function api(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                if (!response.ok) {
                    var serverText = body && body.error ? String(body.error) : "";
                    var err = new Error(serverText || ("Request failed (" + response.status + ")"));
                    err.status = response.status;
                    err.userText = response.status < 500 ? serverText : "";
                    err.details = [url, "HTTP " + response.status, serverText].filter(function (s) { return !!s; }).join(" | ");
                    throw err;
                }
                return body;
            });
        });
    }

    function errorDetails(err) {
        if (err && err.details) { return err.details; }
        return err ? String(err.message || err) : "";
    }

    /** Plain reason for a failed request (server validation text, or a generic line). */
    function failReason(err, doing) {
        if (err && err.userText) { return err.userText; }
        if (err && err.status) { return "Something went wrong while " + doing + "."; }
        return "Spore Studio couldn't reach Mycelian.";
    }

    function failHint(err) {
        return err && err.status
            ? "Try again. If it keeps failing, click Reload editor."
            : "Make sure Mycelian is running, then try again.";
    }

    function needContainerNotice() {
        notify({
            type: "info",
            title: "Create a container first",
            message: "Click + New to make a container, then add templates to it."
        });
    }

    function blankSlot(template, x, y) {
        return {
            id: "slot" + Math.random().toString(36).slice(2, 8),
            template: template,
            x: Math.round(x),
            y: Math.round(y),
            w: Math.max(80, Math.round((state.model.width || 1920) / 2)),
            h: Math.max(80, Math.round((state.model.height || 1080) / 3)),
            z: (state.model.slots || []).length + 1,
            opacity: 100,
            visible: true,
            locked_position: false,
            pointer_events: true,
            padding: { t: 0, r: 0, b: 0, l: 0 },
            fit: "native",
            design_width: null,
            design_height: null,
            audio: { muted: false, volume: 100 },
            link: null
        };
    }

    function selectedSlot() {
        if (!state.model) { return null; }
        for (var i = 0; i < state.model.slots.length; i++) {
            if (state.model.slots[i].id === state.selectedId) {
                return state.model.slots[i];
            }
        }
        return null;
    }

    function updateDirty() {
        var el = $("mc-dirty");
        if (el) { el.textContent = state.dirty ? "Unsaved changes" : ""; }
    }

    function pushHistory() {
        if (!state.model) { return; }
        var snap = JSON.stringify(state.model);
        if (state.history.length && state.history[state.history.length - 1] === snap) {
            return;
        }
        state.history.push(snap);
        if (state.history.length > 50) { state.history.shift(); }
        state.future = [];
    }

    function markDirty() {
        state.dirty = !state.savedSnap || JSON.stringify(state.model) !== state.savedSnap;
        updateDirty();
    }

    function restore(snap) {
        state.model = JSON.parse(snap);
        if (state.selectedId && !selectedSlot()) { state.selectedId = null; }
        markDirty();
        renderAll();
    }

    function undo() {
        if (!state.model || !state.history.length) { return; }
        state.future.push(JSON.stringify(state.model));
        restore(state.history.pop());
    }

    function redo() {
        if (!state.model || !state.future.length) { return; }
        state.history.push(JSON.stringify(state.model));
        restore(state.future.pop());
    }

    function snapValue(value) {
        var grid = state.model && state.model.snap ? state.model.snap : 0;
        if (!grid) { return Math.round(value); }
        return Math.round(value / grid) * grid;
    }

    function clamp(value, lo, hi) {
        return Math.max(lo, Math.min(hi, value));
    }

    function slotById(id) {
        var slots = state.model ? state.model.slots : [];
        for (var i = 0; i < slots.length; i++) {
            if (slots[i].id === id) { return slots[i]; }
        }
        return null;
    }

    /* Measured sizes, with any "Simulate" pretend sizes on top. */
    function effectiveUsed() {
        var used = {};
        if (!state.placeholders) {
            Object.keys(state.used).forEach(function (id) { used[id] = state.used[id]; });
        }
        Object.keys(state.simulate).forEach(function (id) {
            var slot = slotById(id);
            if (!slot || !slot.link || slot.link.role !== "locked") { return; }
            var mode = state.simulate[id];
            if (mode === "empty") {
                used[id] = { empty: true };
            } else if (mode === "half") {
                used[id] = { w: Math.round(slot.w / 2), h: Math.round(slot.h / 2) };
            } else if (mode === "full") {
                used[id] = { w: slot.w, h: slot.h };
            }
        });
        return used;
    }

    function isSimulating() {
        return Object.keys(state.simulate).length > 0;
    }

    function layoutNow() {
        if (!state.model || !Layout) { return null; }
        if (state.dragging) {
            // While moving or resizing, every box shows exactly where it is drawn.
            var drawn = JSON.parse(JSON.stringify(state.model));
            drawn.slots.forEach(function (slot) { slot.link = null; });
            return Layout.solve(drawn, null);
        }
        return Layout.solve(state.model, effectiveUsed());
    }

    function canvasScale() {
        var canvas = $("mc-canvas");
        if (!canvas || !state.model) { return 1; }
        var rect = canvas.getBoundingClientRect();
        return rect.width / state.model.width || 1;
    }

    function canvasPoint(ev) {
        var canvas = $("mc-canvas");
        var rect = canvas.getBoundingClientRect();
        var scale = canvasScale();
        return {
            x: (ev.clientX - rect.left) / scale,
            y: (ev.clientY - rect.top) / scale
        };
    }

    function embedSrc(slot) {
        var volume = slot.audio && slot.audio.muted ? 0 : (slot.audio ? slot.audio.volume : 100);
        var muted = (slot.audio && slot.audio.muted) || volume <= 0 ? "1" : "0";
        return "/" + encodeURIComponent(slot.template) +
            "?mycelian_embed=1&mycelian_volume=" + volume + "&mycelian_muted=" + muted;
    }

    /* Same measuring as the live container page (container_measure.js). */
    function measureFrame(iframe) {
        if (!Measure) { return null; }
        var id = iframe.getAttribute("data-slot");
        var reported = state.explicit ? state.explicit.get(id) : null;
        return reported || Measure.measure(iframe);
    }

    var measureTimer = null;
    function scheduleMeasure() {
        if (state.placeholders || state.dragging) { return; }
        // Throttle, not debounce: a template that animates every frame would otherwise
        // push the measurement back forever.
        if (measureTimer) { return; }
        measureTimer = setTimeout(function () {
            measureTimer = null;
            runMeasure();
        }, 150);
    }

    function runMeasure() {
        if (!state.model || state.placeholders || state.dragging) { return; }
        var next = {};
        var origins = {};
        var frames = document.querySelectorAll("#mc-canvas iframe[data-slot]");
        for (var i = 0; i < frames.length; i++) {
            var iframe = frames[i];
            var id = iframe.getAttribute("data-slot");
            var meta = null;
            for (var s = 0; s < state.model.slots.length; s++) {
                if (state.model.slots[s].id === id) { meta = state.model.slots[s]; }
            }
            if (!meta || !meta.link || meta.link.role !== "locked" || meta.fit === "scale") {
                continue;
            }
            var measured = measureFrame(iframe);
            if (!measured) { continue; }
            if (measured.empty) {
                next[id] = { empty: true };
                continue;
            }
            next[id] = { w: measured.w, h: measured.h };
            origins[id] = { x: measured.x, y: measured.y };
        }
        var same = JSON.stringify(next) === JSON.stringify(state.used) &&
            JSON.stringify(origins) === JSON.stringify(state.origins);
        state.used = next;
        state.origins = origins;
        if (!same) { renderCanvas(); }
    }

    function fitCanvas() {
        var scroll = $("mc-canvas-scroll");
        var canvas = $("mc-canvas");
        if (!scroll || !canvas || !state.model) { return; }
        var scale = Math.min(
            Math.max(80, scroll.clientWidth - 32) / state.model.width,
            Math.max(80, scroll.clientHeight - 32) / state.model.height,
            1
        );
        canvas.style.width = state.model.width + "px";
        canvas.style.height = state.model.height + "px";
        canvas.style.transform = "scale(" + scale + ")";
        // Indicator labels divide by this so they stay readable at any zoom.
        canvas.style.setProperty("--mc-zoom", String(scale));
        canvas.style.marginRight = (state.model.width * (scale - 1)) + "px";
        canvas.style.marginBottom = (state.model.height * (scale - 1)) + "px";
        var grid = state.model.snap || 0;
        canvas.style.backgroundSize = grid ? (grid + "px " + grid + "px") : "0 0";
        var bg = state.model.background === "transparent" ? "transparent" : state.model.background;
        canvas.style.backgroundColor = bg;
    }

    /* Briefly outline the boxes a warning is about, and select the first one. */
    function flagSlots(ids) {
        state.flagged = ids.slice();
        if (ids.length) { state.selectedId = ids[0]; }
        renderAll();
        clearTimeout(state.flagTimer);
        state.flagTimer = setTimeout(function () {
            state.flagged = [];
            renderCanvas();
        }, 2500);
    }

    function renderWarnings(layout) {
        var host = $("mc-warnings");
        if (!host) { return; }
        var items = (layout.warnings || []).map(function (item) {
            return { message: item.message, slots: item.slots || [] };
        });
        (state.model.slots || []).forEach(function (slot) {
            if (!state.known[slot.template]) {
                items.push({
                    message: "“" + slot.template + "” was not found. Its box will stay empty on the route.",
                    slots: [slot.id]
                });
            }
        });
        host.textContent = "";
        if (!items.length) {
            host.hidden = true;
            return;
        }
        host.hidden = false;
        items.forEach(function (item) {
            var row = document.createElement("div");
            row.className = "mc-warning";
            var text = document.createElement("span");
            text.textContent = item.message;
            row.appendChild(text);
            if (item.slots.length) {
                var show = document.createElement("button");
                show.type = "button";
                show.className = "ss-btn ss-btn--ghost mc-warning__show";
                show.textContent = item.slots.length > 1 ? "Show boxes" : "Show box";
                show.addEventListener("click", function () { flagSlots(item.slots); });
                row.appendChild(show);
            }
            host.appendChild(row);
        });
    }

    /*
     * Redraw the canvas. Slot boxes and their iframes stay in the page and are only
     * restyled: an iframe that is taken out and put back loads its page again, which
     * restarted every template's sounds and animations on each drag step.
     */
    function renderCanvas() {
        var canvas = $("mc-canvas");
        if (!canvas || !state.model) { return; }
        var layout = layoutNow();
        if (!layout) { return; }
        fitCanvas();
        renderWarnings(layout);
        var existing = {};
        Array.prototype.slice.call(canvas.children).forEach(function (child) {
            if (child.classList.contains("mc-slot") && child.dataset.slot) {
                existing[child.dataset.slot] = child;
            } else {
                child.remove();
            }
        });
        var live = {};
        layout.slots.forEach(function (slot) {
            live[slot.id] = true;
            var el = existing[slot.id];
            if (!el) {
                el = document.createElement("div");
                el.dataset.slot = slot.id;
                el.setAttribute("role", "button");
                el.addEventListener("mousedown", onSlotMouseDown);
                var label = document.createElement("div");
                label.className = "mc-slot__label";
                el.appendChild(label);
                canvas.appendChild(el);
            }
            el.className = "mc-slot" + (slot.id === state.selectedId ? " is-selected" : "") +
                (state.known[slot.template] ? "" : " is-missing");
            el.setAttribute("aria-label", slot.template);
            el.style.left = slot.x + "px";
            el.style.top = slot.y + "px";
            el.style.width = slot.w + "px";
            el.style.height = slot.h + "px";
            el.style.zIndex = String(slot.z || 1);
            el.style.opacity = String((slot.opacity == null ? 100 : slot.opacity) / 100);
            el.style.outline = slot.visible ? "" : "1px dashed var(--ss-text-muted)";
            el.querySelector(".mc-slot__label").textContent =
                slot.template + (state.known[slot.template] ? "" : " (missing)");
            Array.prototype.slice.call(el.querySelectorAll(".mc-handle")).forEach(function (h) { h.remove(); });

            var holder = el.querySelector(".mc-placeholder");
            var clip = el.querySelector(".mc-clip");
            if (state.placeholders || !slot.visible || !state.known[slot.template]) {
                if (clip) { clip.remove(); }
                if (state.placeholders || !slot.visible) {
                    if (!holder) {
                        holder = document.createElement("div");
                        holder.className = "mc-placeholder";
                        el.appendChild(holder);
                    }
                    holder.textContent = slot.template;
                } else if (holder) {
                    holder.remove();
                }
            } else {
                if (holder) { holder.remove(); }
                if (!clip) {
                    clip = document.createElement("div");
                    clip.className = "mc-clip";
                    el.appendChild(clip);
                }
                clip.style.left = slot.content.x + "px";
                clip.style.top = slot.content.y + "px";
                clip.style.width = slot.content.w + "px";
                clip.style.height = slot.content.h + "px";
                var src = embedSrc(slot);
                var frame = clip.querySelector("iframe[data-slot]");
                if (!frame || frame.getAttribute("data-src") !== src) {
                    if (frame) { frame.remove(); }
                    frame = createSlotFrame(slot.id, src);
                    clip.appendChild(frame);
                }
                applyFrame(frame, slot);
            }
            if (slot.id === state.selectedId) {
                addHandles(el);
            }
        });
        Object.keys(existing).forEach(function (id) {
            if (!live[id]) { existing[id].remove(); }
        });
        addGutters(canvas, layout);
        addSpaceIndicators(canvas, layout);
        renderSimulateControls();
    }

    function createSlotFrame(slotId, src) {
        var frame = document.createElement("iframe");
        frame.dataset.slot = slotId;
        frame.dataset.src = src;
        frame.src = src;
        frame.setAttribute("allow", "autoplay");
        if (Measure) {
            Measure.watch(frame, scheduleMeasure);
        }
        return frame;
    }

    function applyFrame(frame, slot) {
        var origin = (!state.simulate[slot.id] && state.origins[slot.id]) || { x: 0, y: 0 };
        frame.style.position = "absolute";
        frame.style.border = "0";
        if (slot.fit === "scale") {
            var scale = Math.min(
                slot.content.w / Math.max(1, slot.iframe.w),
                slot.content.h / Math.max(1, slot.iframe.h)
            );
            frame.style.width = slot.iframe.w + "px";
            frame.style.height = slot.iframe.h + "px";
            frame.style.transformOrigin = "top left";
            frame.style.transform = "scale(" + scale + ")";
            frame.style.left = ((slot.content.w - slot.iframe.w * scale) / 2) + "px";
            frame.style.top = ((slot.content.h - slot.iframe.h * scale) / 2) + "px";
            return;
        }
        frame.style.transform = "";
        frame.style.width = slot.iframe.w + "px";
        frame.style.height = slot.iframe.h + "px";
        if (slot.role === "locked") {
            // Only the linked axis is trimmed to the content; the other keeps its layout.
            frame.style.left = (slot.axis === "horizontal" ? -origin.x : 0) + "px";
            frame.style.top = (slot.axis === "vertical" ? -origin.y : 0) + "px";
        } else {
            frame.style.left = "0px";
            frame.style.top = "0px";
        }
    }

    /* Handles change the size a box is drawn at; while dragging, every box shows its drawn size. */
    function addHandles(el) {
        ["e", "s", "se", "w", "n"].forEach(function (dir) {
            var handle = document.createElement("div");
            handle.className = "mc-handle mc-handle--" + dir;
            handle.dataset.dir = dir;
            handle.addEventListener("mousedown", function (ev) {
                ev.stopPropagation();
                startResize(ev, dir);
            });
            el.appendChild(handle);
        });
    }

    function addGutters(canvas, layout) {
        var groups = {};
        layout.slots.forEach(function (slot) {
            if (!slot.link_id) { return; }
            if (!groups[slot.link_id]) { groups[slot.link_id] = []; }
            groups[slot.link_id].push(slot);
        });
        Object.keys(groups).forEach(function (id) {
            var members = groups[id];
            if (members.length < 2) { return; }
            var axis = members[0].axis;
            members.sort(function (a, b) {
                return axis === "vertical" ? a.y - b.y : a.x - b.x;
            });
            for (var i = 0; i < members.length - 1; i++) {
                var gutter = document.createElement("div");
                gutter.className = "mc-gutter " + (axis === "vertical" ? "mc-gutter--v" : "mc-gutter--h");
                if (axis === "vertical") {
                    var gap = Math.max(state.model.spacing, 8);
                    gutter.style.left = members[i].x + "px";
                    gutter.style.width = members[i].w + "px";
                    gutter.style.top = (members[i].y + members[i].h - (state.model.spacing ? 0 : 4)) + "px";
                    gutter.style.height = gap + "px";
                } else {
                    var gapH = Math.max(state.model.spacing, 8);
                    gutter.style.top = members[i].y + "px";
                    gutter.style.height = members[i].h + "px";
                    gutter.style.left = (members[i].x + members[i].w - (state.model.spacing ? 0 : 4)) + "px";
                    gutter.style.width = gapH + "px";
                }
                gutter.addEventListener("mousedown", function (ev) {
                    ev.stopPropagation();
                    startGutter(ev, axis);
                });
                canvas.appendChild(gutter);
            }
        });
    }

    function onSlotMouseDown(ev) {
        if (ev.button !== 0) { return; }
        var id = ev.currentTarget.dataset.slot;
        state.selectedId = id;
        var slot = selectedSlot();
        if (!slot || slot.locked_position) {
            renderInspector();
            renderCanvas();
            return;
        }
        ev.preventDefault();
        var start = canvasPoint(ev);
        var origin = { x: slot.x, y: slot.y };
        var pushed = false;
        state.dragging = true;
        function move(e) {
            if (!pushed) {
                pushHistory();
                pushed = true;
            }
            var pt = canvasPoint(e);
            var dx = pt.x - start.x;
            var dy = pt.y - start.y;
            slot.x = snapValue(origin.x + dx);
            if (!slot.link) {
                slot.y = snapValue(origin.y + dy);
            } else if (slot.link.axis === "horizontal") {
                slot.y = snapValue(origin.y + dy);
            } else {
                slot.y = snapValue(origin.y + dy);
            }
            state.dirty = true;
            updateDirty();
            renderCanvas();
        }
        function up() {
            state.dragging = false;
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            renderAll();
            scheduleMeasure();
        }
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
    }

    function startResize(ev, dir) {
        ev.preventDefault();
        var slot = selectedSlot();
        if (!slot) { return; }
        var start = canvasPoint(ev);
        var origin = { x: slot.x, y: slot.y, w: slot.w, h: slot.h };
        var pushed = false;
        state.dragging = true;
        function move(e) {
            if (!pushed) {
                pushHistory();
                pushed = true;
            }
            var pt = canvasPoint(e);
            var dx = pt.x - start.x;
            var dy = pt.y - start.y;
            if (dir.indexOf("e") !== -1) { slot.w = Math.max(20, snapValue(origin.w + dx)); }
            if (dir.indexOf("s") !== -1) { slot.h = Math.max(20, snapValue(origin.h + dy)); }
            if (dir.indexOf("w") !== -1) {
                var width = Math.max(20, snapValue(origin.w - dx));
                slot.x = origin.x + (origin.w - width);
                slot.w = width;
            }
            if (dir.indexOf("n") !== -1) {
                var height = Math.max(20, snapValue(origin.h - dy));
                slot.y = origin.y + (origin.h - height);
                slot.h = height;
            }
            state.dirty = true;
            updateDirty();
            renderCanvas();
        }
        function up() {
            state.dragging = false;
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            // Back from drawn sizes to the solved layout.
            renderAll();
            scheduleMeasure();
        }
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
    }

    function startGutter(ev, axis) {
        ev.preventDefault();
        var start = canvasPoint(ev);
        var origin = state.model.spacing || 0;
        var pushed = false;
        state.dragging = true;
        function move(e) {
            if (!pushed) {
                pushHistory();
                pushed = true;
            }
            var pt = canvasPoint(e);
            var delta = axis === "vertical" ? pt.y - start.y : pt.x - start.x;
            state.model.spacing = clamp(Math.round(origin + delta), 0, 200);
            state.dirty = true;
            updateDirty();
            renderCanvas();
        }
        function up() {
            state.dragging = false;
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            renderAll();
        }
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
    }

    function inspectorHeading(host, iconName, text) {
        var title = document.createElement("h3");
        title.className = "ss-pane-head";
        title.innerHTML = '<span class="ss-sic">' + UI.icon(iconName) + "</span>";
        var label = document.createElement("span");
        label.textContent = text;
        title.appendChild(label);
        host.appendChild(title);
    }

    function callout(host, text) {
        var box = document.createElement("p");
        box.className = "ss-callout";
        box.textContent = text;
        host.appendChild(box);
    }

    /* Small uppercase divider that groups the slot settings. */
    function groupLabel(host, iconName, text) {
        var label = document.createElement("div");
        label.className = "mc-sub";
        label.innerHTML = UI.icon(iconName);
        var span = document.createElement("span");
        span.textContent = text;
        label.appendChild(span);
        host.appendChild(label);
    }

    function fieldRow(parent, labelText) {
        var row = document.createElement("div");
        row.className = "ss-form-row";
        var label = document.createElement("label");
        label.textContent = labelText;
        row.appendChild(label);
        parent.appendChild(row);
        return row;
    }

    function addNumber(parent, label, value, onChange, extra) {
        var row = fieldRow(parent, label);
        var input = document.createElement("input");
        input.type = "number";
        input.value = value == null ? "" : value;
        if (extra) {
            Object.keys(extra).forEach(function (key) { input[key] = extra[key]; });
        }
        input.addEventListener("change", function () {
            var number = input.value === "" ? null : Number(input.value);
            commit(function () { onChange(number); });
        });
        row.appendChild(input);
        return input;
    }

    function addCheck(parent, label, checked, onChange) {
        var row = document.createElement("label");
        row.className = "mc-check";
        var input = document.createElement("input");
        input.type = "checkbox";
        input.checked = !!checked;
        input.addEventListener("change", function () {
            commit(function () { onChange(input.checked); });
        });
        row.appendChild(input);
        row.appendChild(document.createTextNode(label));
        parent.appendChild(row);
        return input;
    }

    /* Container backgrounds are opaque hex (the server rejects anything else). */
    function buildBackgroundSwatch() {
        var swatch = document.createElement("button");
        swatch.type = "button";
        swatch.className = "ss-color__swatch";
        swatch.title = "Pick the background color";
        swatch.setAttribute("aria-label", swatch.title);
        var checker = document.createElement("span");
        checker.className = "ss-color__checker";
        var fill = document.createElement("span");
        fill.className = "ss-color__fill";
        fill.style.background = state.model.background;
        swatch.appendChild(checker);
        swatch.appendChild(fill);
        swatch.addEventListener("click", function () {
            var P = window.MycelianColorPicker;
            if (!P) { return; }
            var start = state.model.background;
            function live(v) {
                state.model.background = v;
                fill.style.background = P.swatchBackground(v);
                renderCanvas();
            }
            P.open({
                anchor: swatch,
                value: start,
                allowAlpha: false,
                preferredFormat: "hex",
                title: "Container background",
                onInput: live,
                onDone: function (v) {
                    live(start);
                    if (v && v !== start && /^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(v)) {
                        commit(function () { state.model.background = v; });
                        fill.style.background = v;
                    }
                },
                onCancel: function () { live(start); }
            });
        });
        return swatch;
    }

    function commit(fn) {
        pushHistory();
        fn();
        state.dirty = true;
        updateDirty();
        renderCanvas();
    }

    function renderInspector() {
        var host = $("mc-inspector-host");
        if (!host) { return; }
        host.textContent = "";
        if (!state.model) {
            var empty = document.createElement("div");
            empty.className = "ss-empty";
            empty.textContent = "Create a container to combine templates on one route.";
            host.appendChild(empty);
            return;
        }
        var slot = selectedSlot();
        if (!slot) {
            renderContainerInspector(host);
        } else {
            renderSlotInspector(host, slot);
        }
        if (window.SporeStudioTips) { window.SporeStudioTips.upgrade(host); }
    }

    /* Container settings: every shared space, with rename / select / remove. */
    function renderSpacesList(host) {
        groupLabel(host, "fill_space", "Shared spaces");
        var list = spaces();
        if (!list.length) {
            callout(host, "Want boxes to make room for each other? Select a box (for example FF7) and pick " +
                "“Fits its content” under Sizing. The box next to it (for example chat) is set to " +
                "“Fills leftover space” so it shrinks when FF7 needs more room.");
            return;
        }
        list.forEach(function (space) {
            var card = document.createElement("div");
            card.className = "mc-space-card";
            card.style.setProperty("--mc-space-color", space.color);
            var head = document.createElement("div");
            head.className = "mc-space-card__head";
            var dot = document.createElement("span");
            dot.className = "mc-space-dot";
            dot.style.background = space.color;
            head.appendChild(dot);
            var name = document.createElement("input");
            name.type = "text";
            name.value = space.id;
            name.setAttribute("aria-label", "Shared space name");
            name.addEventListener("change", function () {
                var next = name.value.trim();
                if (!next || next.indexOf("/") !== -1 || next.length > 64 ||
                    (next !== space.id && linkIds().indexOf(next) !== -1)) {
                    name.value = space.id;
                    notify({ type: "warning", title: "Pick another name",
                        message: "Names must be unique, up to 64 characters, without “/”." });
                    return;
                }
                commit(function () {
                    space.members.forEach(function (slot) { slot.link.id = next; });
                });
                renderInspector();
            });
            head.appendChild(name);
            card.appendChild(head);
            var detail = document.createElement("div");
            detail.className = "mc-space-card__detail";
            detail.textContent = space.members.map(function (slot) {
                return slot.template + " (" + (roleOf(slot) === "fit" ? "fits content" : "fills leftover") + ")";
            }).join(" → ") + " · " + directionText(space.axis);
            card.appendChild(detail);
            var actions = document.createElement("div");
            actions.className = "mc-inline";
            var select = document.createElement("button");
            select.type = "button";
            select.className = "ss-btn";
            select.textContent = "Show on canvas";
            select.addEventListener("click", function () {
                flagSlots(space.members.map(function (slot) { return slot.id; }));
            });
            actions.appendChild(select);
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "ss-btn ss-btn--ghost";
            remove.textContent = "Stop sharing";
            remove.title = "Makes every box in this space Fixed again.";
            remove.addEventListener("click", function () {
                commit(function () {
                    space.members.forEach(function (slot) {
                        slot.link = null;
                        delete state.simulate[slot.id];
                    });
                });
                renderInspector();
            });
            actions.appendChild(remove);
            card.appendChild(actions);
            host.appendChild(card);
        });
    }

    function renderContainerInspector(host) {
        inspectorHeading(host, "grid", "Container settings");

        var name = document.createElement("input");
        name.type = "text";
        name.value = state.model.name;
        name.addEventListener("change", function () {
            commit(function () { state.model.name = name.value.trim() || state.model.name; });
        });
        fieldRow(host, "Name").appendChild(name);

        var route = document.createElement("input");
        route.type = "text";
        route.value = state.model.route;
        route.addEventListener("change", function () {
            commit(function () { state.model.route = route.value.trim(); });
        });
        fieldRow(host, "Route path").appendChild(route);

        var url = document.createElement("div");
        url.className = "mc-url";
        url.title = "The address to use as an OBS browser source";
        url.innerHTML = UI.icon("link");
        var urlText = document.createElement("span");
        urlText.textContent = location.origin + "/" + state.model.route;
        url.appendChild(urlText);
        host.appendChild(url);

        var preset = document.createElement("select");
        [
            ["Custom", 0, 0],
            ["1920 × 1080", 1920, 1080],
            ["1280 × 720", 1280, 720],
            ["1080 × 1920", 1080, 1920],
            ["720 × 1280", 720, 1280]
        ].forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item[1] + "x" + item[2];
            opt.textContent = item[0];
            if (item[1] === state.model.width && item[2] === state.model.height) {
                opt.selected = true;
            }
            preset.appendChild(opt);
        });
        preset.addEventListener("change", function () {
            var parts = preset.value.split("x");
            var w = Number(parts[0]);
            var h = Number(parts[1]);
            if (!w || !h) { return; }
            commit(function () {
                state.model.width = w;
                state.model.height = h;
            });
            renderInspector();
        });
        fieldRow(host, "Resolution").appendChild(preset);

        var dims = document.createElement("div");
        dims.className = "mc-inline";
        host.appendChild(dims);
        addNumber(dims, "Width", state.model.width, function (value) {
            state.model.width = value || state.model.width;
        }, { min: 50, max: 7680 });
        addNumber(dims, "Height", state.model.height, function (value) {
            state.model.height = value || state.model.height;
        }, { min: 50, max: 4320 });

        var bg = document.createElement("select");
        ["transparent", "custom"].forEach(function (value) {
            var opt = document.createElement("option");
            opt.value = value;
            opt.textContent = value === "transparent" ? "Transparent" : "Solid color";
            opt.selected = (state.model.background === "transparent") === (value === "transparent");
            bg.appendChild(opt);
        });
        bg.addEventListener("change", function () {
            commit(function () {
                state.model.background = bg.value === "transparent" ? "transparent" : "#111111";
            });
            renderInspector();
        });
        fieldRow(host, "Background").appendChild(bg);
        if (state.model.background !== "transparent") {
            fieldRow(host, "Color").appendChild(buildBackgroundSwatch());
        }

        var spacingRow = fieldRow(host, "Gap between boxes in a shared space");
        spacingRow.title = "Space left between boxes that share space. A box that has closed up takes no gap.";
        var spacing = document.createElement("input");
        spacing.type = "range";
        spacing.min = "0";
        spacing.max = "200";
        spacing.value = String(state.model.spacing || 0);
        var spacingNum = document.createElement("input");
        spacingNum.type = "number";
        spacingNum.min = "0";
        spacingNum.max = "200";
        spacingNum.value = String(state.model.spacing || 0);
        function setSpacing(value) {
            commit(function () { state.model.spacing = clamp(Math.round(value), 0, 200); });
            renderInspector();
        }
        spacing.addEventListener("change", function () { setSpacing(spacing.value); });
        spacingNum.addEventListener("change", function () { setSpacing(spacingNum.value); });
        spacingRow.appendChild(spacing);
        spacingRow.appendChild(spacingNum);

        addNumber(host, "Snap grid (px)", state.model.snap, function (value) {
            state.model.snap = clamp(Math.round(value || 0), 0, 64);
        }, { min: 0, max: 64 });
        addCheck(host, "Enabled (show on the About page and serve the route)", state.model.enabled, function (checked) {
            state.model.enabled = checked;
        });
        renderSpacesList(host);
    }

    function renderSlotInspector(host, slot) {
        inspectorHeading(host, "file", slot.template);

        var picker = document.createElement("select");
        allTemplateNames().forEach(function (name) {
            var opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            opt.selected = name === slot.template;
            picker.appendChild(opt);
        });
        if (!state.known[slot.template]) {
            var missing = document.createElement("option");
            missing.value = slot.template;
            missing.textContent = slot.template + " (missing)";
            missing.selected = true;
            picker.appendChild(missing);
        }
        picker.addEventListener("change", function () {
            commit(function () { slot.template = picker.value; });
        });
        fieldRow(host, "Template").appendChild(picker);

        renderSizingSection(host, slot);

        groupLabel(host, "move", "Position and size");
        var dims = document.createElement("div");
        dims.className = "mc-inline";
        host.appendChild(dims);
        [["X", "x", state.model.width], ["Y", "y", state.model.height], ["W", "w", state.model.width], ["H", "h", state.model.height]].forEach(function (spec) {
            var row = fieldRow(dims, spec[0]);
            var input = document.createElement("input");
            input.type = "number";
            input.value = slot[spec[1]];
            input.addEventListener("change", function () {
                commit(function () {
                    var next = Math.round(Number(input.value) || 0);
                    if (spec[1] === "w" || spec[1] === "h") { next = Math.max(1, next); }
                    slot[spec[1]] = next;
                });
            });
            var pct = document.createElement("div");
            pct.className = "mc-pct";
            pct.textContent = (Math.round(slot[spec[1]] / spec[2] * 1000) / 10) + "% of canvas";
            row.appendChild(input);
            row.appendChild(pct);
        });

        addNumber(host, "Z-order", slot.z, function (value) { slot.z = Math.round(value || 0); }, { min: 0, max: 999 });
        addNumber(host, "Opacity %", slot.opacity, function (value) {
            slot.opacity = clamp(Math.round(value || 0), 0, 100);
        }, { min: 0, max: 100 });
        groupLabel(host, "eye", "Behaviour");
        addCheck(host, "Visible", slot.visible, function (checked) { slot.visible = checked; });
        addCheck(host, "Lock position", slot.locked_position, function (checked) { slot.locked_position = checked; });
        addCheck(host, "Click-through", !slot.pointer_events, function (checked) { slot.pointer_events = !checked; });

        var uniform = slot.padding.t === slot.padding.r && slot.padding.r === slot.padding.b && slot.padding.b === slot.padding.l;
        // Unticking keeps the four boxes open even while the values still match.
        var splitPadding = !uniform || !!state.splitPadding[slot.id];
        addCheck(host, "Padding same on all sides", !splitPadding, function (checked) {
            if (checked) {
                var n = slot.padding.t;
                slot.padding = { t: n, r: n, b: n, l: n };
                delete state.splitPadding[slot.id];
            } else {
                state.splitPadding[slot.id] = true;
            }
            renderInspector();
        });
        if (!splitPadding) {
            addNumber(host, "Padding", slot.padding.t, function (value) {
                var n = clamp(Math.round(value || 0), 0, 400);
                slot.padding = { t: n, r: n, b: n, l: n };
            }, { min: 0, max: 400 });
        } else {
            var pads = document.createElement("div");
            pads.className = "mc-inline";
            host.appendChild(pads);
            [["Top", "t"], ["Right", "r"], ["Bottom", "b"], ["Left", "l"]].forEach(function (spec) {
                addNumber(pads, spec[0], slot.padding[spec[1]], function (value) {
                    slot.padding[spec[1]] = clamp(Math.round(value || 0), 0, 400);
                }, { min: 0, max: 400 });
            });
        }

        var fit = document.createElement("select");
        [["native", "Native (template fills the slot)"], ["scale", "Scale from design size"]].forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item[0];
            opt.textContent = item[1];
            opt.selected = slot.fit === item[0];
            fit.appendChild(opt);
        });
        fit.addEventListener("change", function () {
            commit(function () { slot.fit = fit.value; });
            renderInspector();
        });
        fieldRow(host, "Fit").appendChild(fit);
        if (slot.fit === "scale") {
            var design = document.createElement("div");
            design.className = "mc-inline";
            host.appendChild(design);
            addNumber(design, "Design width", slot.design_width || slot.w, function (value) {
                slot.design_width = Math.round(value || slot.w);
            }, { min: 1, max: 7680 });
            addNumber(design, "Design height", slot.design_height || slot.h, function (value) {
                slot.design_height = Math.round(value || slot.h);
            }, { min: 1, max: 4320 });
        }

        groupLabel(host, "volume", "Audio");
        addCheck(host, "Mute audio", slot.audio.muted, function (checked) { slot.audio.muted = checked; });
        addNumber(host, "Volume", slot.audio.volume, function (value) {
            slot.audio.volume = clamp(Math.round(value || 0), 0, 100);
        }, { min: 0, max: 100 });

        groupLabel(host, "layers", "Stacking");
        var layer = document.createElement("div");
        layer.className = "mc-inline";
        host.appendChild(layer);
        layer.appendChild(actionButton("Bring forward", function () { slot.z += 1; }));
        layer.appendChild(actionButton("Send backward", function () { slot.z = Math.max(0, slot.z - 1); }));
        var remove = actionButton("Remove from container", function () {
            state.model.slots = state.model.slots.filter(function (item) { return item.id !== slot.id; });
            state.selectedId = null;
            renderInspector();
        });
        remove.classList.add("ss-btn--danger", "ss-btn--block");
        host.appendChild(remove);
    }

    /* ---------------------------------------------------------------- shared spaces */

    function roleOf(slot) {
        if (!slot.link) { return "fixed"; }
        return slot.link.role === "locked" ? "fit" : "fill";
    }

    /** Shared spaces in the order they first appear: {id, axis, color, members (in stack order)}. */
    function spaces() {
        var out = [];
        var byId = {};
        (state.model ? state.model.slots : []).forEach(function (slot) {
            if (!slot.link) { return; }
            var space = byId[slot.link.id];
            if (!space) {
                space = { id: slot.link.id, axis: slot.link.axis, members: [] };
                space.color = SPACE_COLORS[out.length % SPACE_COLORS.length];
                byId[slot.link.id] = space;
                out.push(space);
            }
            space.members.push(slot);
        });
        out.forEach(function (space) {
            space.members.sort(function (a, b) {
                return space.axis === "vertical" ? a.y - b.y : a.x - b.x;
            });
        });
        return out;
    }

    function spaceById(id) {
        var list = spaces();
        for (var i = 0; i < list.length; i++) {
            if (list[i].id === id) { return list[i]; }
        }
        return null;
    }

    function directionText(axis) {
        return axis === "horizontal" ? "left to right" : "top to bottom";
    }

    function limitText(px, pct) {
        if (pct) { return pct + "% of the space"; }
        return px + "px";
    }

    function namesOf(slots) {
        var names = slots.map(function (slot) { return "“" + slot.template + "”"; });
        if (names.length <= 1) { return names.join(""); }
        return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
    }

    /** One plain sentence saying what will happen to this box. */
    function sizingSummary(slot) {
        var role = roleOf(slot);
        var name = "“" + slot.template + "”";
        if (role === "fixed") {
            return name + " stays exactly where and how big you drew it.";
        }
        var space = spaceById(slot.link.id);
        var others = space ? space.members.filter(function (item) { return item !== slot; }) : [];
        var link = slot.link;
        var minText = (link.min_px || link.min_pct) ? limitText(link.min_px, link.min_pct) : "";
        var maxText = (link.max_px != null || link.max_pct != null)
            ? limitText(link.max_px != null ? link.max_px : 0, link.max_pct) : "";
        if (role === "fit") {
            var parts = [name + " grows and shrinks with what it is showing, up to the size you drew it" +
                (maxText ? " (and never past " + maxText + ")" : "") + "."];
            if (link.when_empty === "keep") {
                parts.push("When it shows nothing it keeps its drawn size.");
            } else {
                parts.push("When it shows nothing it closes" + (minText ? " down to " + minText : "") + ".");
            }
            var fillers = others.filter(function (item) { return roleOf(item) === "fill"; });
            if (fillers.length) {
                parts.push(namesOf(fillers) + (fillers.length > 1 ? " get" : " gets") + " the space it isn't using.");
            } else {
                parts.push("Add a “Fills leftover space” box to this shared space to use the room it frees up.");
            }
            return parts.join(" ");
        }
        var fits = others.filter(function (item) { return roleOf(item) === "fit"; });
        var sentence = fits.length
            ? name + " takes the space " + namesOf(fits) + (fits.length > 1 ? " aren't" : " isn't") + " using"
            : name + " shares the space with the other boxes in it";
        if (minText) { sentence += ", never smaller than " + minText; }
        if (maxText) { sentence += ", never bigger than " + maxText; }
        return sentence + ". When space is tight its picture is cropped (the template sees a smaller window and re-flows).";
    }

    function freshSpaceId() {
        var n = 1;
        var ids = linkIds();
        while (ids.indexOf("Space " + n) !== -1) { n += 1; }
        return "Space " + n;
    }

    /**
     * The box this one most likely stacks with: the nearest box above/below it in the
     * same column, or left/right of it in the same row.
     */
    function stackNeighbour(slot) {
        var best = null;
        (state.model.slots || []).forEach(function (other) {
            if (other === slot) { return; }
            var colOverlap = Math.min(slot.x + slot.w, other.x + other.w) - Math.max(slot.x, other.x);
            var rowOverlap = Math.min(slot.y + slot.h, other.y + other.h) - Math.max(slot.y, other.y);
            var candidates = [];
            if (colOverlap > 0) {
                candidates.push({ axis: "vertical", gap: Math.max(other.y - (slot.y + slot.h), slot.y - (other.y + other.h), 0) });
            }
            if (rowOverlap > 0) {
                candidates.push({ axis: "horizontal", gap: Math.max(other.x - (slot.x + slot.w), slot.x - (other.x + other.w), 0) });
            }
            candidates.forEach(function (item) {
                // Prefer a box already in a space, then the closest.
                var score = item.gap - (other.link ? 0.5 : 0);
                if (!best || score < best.score) {
                    best = { other: other, axis: other.link ? other.link.axis : item.axis, score: score };
                }
            });
        });
        return best;
    }

    function newLink(id, axis, role, from) {
        return {
            id: id,
            axis: axis,
            role: role,
            when_empty: from && from.when_empty ? from.when_empty : "collapse",
            min_px: from ? from.min_px || 0 : 0,
            min_pct: from ? from.min_pct || 0 : 0,
            max_px: from && from.max_px != null ? from.max_px : null,
            max_pct: from && from.max_pct != null ? from.max_pct : null
        };
    }

    /** Switch a box between Fixed / Fits its content / Fills leftover space. */
    function setRole(slot, role) {
        var note = "";
        commit(function () {
            if (role === "fixed") {
                slot.link = null;
                delete state.simulate[slot.id];
                return;
            }
            var linkRole = role === "fit" ? "locked" : "flexible";
            if (slot.link) {
                slot.link.role = linkRole;
                if (linkRole !== "locked") { delete state.simulate[slot.id]; }
                return;
            }
            var near = stackNeighbour(slot);
            if (near && near.other.link) {
                slot.link = newLink(near.other.link.id, near.other.link.axis, linkRole);
            } else if (near) {
                // Pair it with the box next to it: one fits its content, the other fills the rest.
                var id = freshSpaceId();
                slot.link = newLink(id, near.axis, linkRole);
                near.other.link = newLink(id, near.axis, linkRole === "locked" ? "flexible" : "locked");
                note = "“" + near.other.template + "” joined this shared space as “" +
                    (linkRole === "locked" ? "Fills leftover space" : "Fits its content") + "”.";
            } else {
                slot.link = newLink(freshSpaceId(), "vertical", linkRole);
            }
        });
        state.sizingNote = note;
        renderInspector();
    }

    function segmented(options, current, onPick) {
        var wrap = document.createElement("div");
        wrap.className = "mc-seg";
        wrap.setAttribute("role", "radiogroup");
        options.forEach(function (opt) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "mc-seg__opt" + (opt.value === current ? " is-on" : "");
            button.setAttribute("role", "radio");
            button.setAttribute("aria-checked", opt.value === current ? "true" : "false");
            button.dataset.value = opt.value;
            if (opt.icon) { button.innerHTML = UI.icon(opt.icon); }
            var label = document.createElement("span");
            label.textContent = opt.label;
            button.appendChild(label);
            if (opt.tip) { button.title = opt.tip; }
            if (opt.disabled) {
                button.disabled = true;
                button.classList.add("is-disabled");
            }
            button.addEventListener("click", function () {
                if (!opt.disabled && opt.value !== current) { onPick(opt.value); }
            });
            wrap.appendChild(button);
        });
        return wrap;
    }

    /** Number + px/% toggle for a minimum or maximum. ``which`` is "min" or "max". */
    function limitField(host, slot, which, label, hint) {
        var link = slot.link;
        var pctKey = which + "_pct";
        var pxKey = which + "_px";
        var usePct = !!link[pctKey];
        var row = fieldRow(host, label);
        row.classList.add("mc-limit");
        if (hint) { row.title = hint; }
        var input = document.createElement("input");
        input.type = "number";
        input.min = "0";
        input.max = usePct ? "100" : "7680";
        input.placeholder = which === "max" ? "none" : "0";
        var value = usePct ? link[pctKey] : link[pxKey];
        if (which === "min") {
            input.value = String(value || 0);
        } else {
            input.value = value == null ? "" : String(value);
        }
        var unit = document.createElement("select");
        unit.className = "mc-unit";
        [["px", "px"], ["pct", "% of space"]].forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item[0];
            opt.textContent = item[1];
            opt.selected = (item[0] === "pct") === usePct;
            unit.appendChild(opt);
        });
        function apply() {
            var raw = input.value === "" ? null : Math.round(Number(input.value) || 0);
            var pct = unit.value === "pct";
            commit(function () {
                if (which === "min") {
                    link.min_px = pct ? 0 : clamp(raw || 0, 0, 7680);
                    link.min_pct = pct ? clamp(raw || 0, 0, 100) : 0;
                } else {
                    link.max_px = pct || raw == null ? null : clamp(raw, 0, 7680);
                    link.max_pct = !pct || raw == null ? null : clamp(raw, 0, 100);
                }
            });
            renderInspector();
        }
        input.addEventListener("change", apply);
        unit.addEventListener("change", apply);
        row.appendChild(input);
        row.appendChild(unit);
        return row;
    }

    function renderSizingSection(host, slot) {
        groupLabel(host, "fill_space", "Sizing");
        var role = roleOf(slot);
        var scaled = slot.fit === "scale";
        host.appendChild(segmented([
            { value: "fixed", icon: "pin", label: "Fixed", tip: "Never changes size or position." },
            {
                value: "fit", icon: "fit_content", label: "Fits its content",
                tip: scaled
                    ? "Not available while Fit is “Scale from design size”: a scaled template can't be measured."
                    : "Grows and shrinks with what the template is showing right now, up to the size you drew. Good for FF7, alerts or a goal bar.",
                disabled: scaled && role !== "fit"
            },
            {
                value: "fill", icon: "fill_space", label: "Fills leftover space",
                tip: "Gets whatever room the “Fits its content” boxes don't use. Good for chat."
            }
        ], role, function (value) { setRole(slot, value); }));

        var summary = document.createElement("p");
        summary.className = "mc-summary";
        summary.textContent = sizingSummary(slot);
        host.appendChild(summary);
        if (state.sizingNote) {
            var note = document.createElement("p");
            note.className = "mc-summary mc-summary--note";
            note.textContent = state.sizingNote;
            host.appendChild(note);
            state.sizingNote = "";
        }
        if (role === "fixed") { return; }

        var current = spaceById(slot.link.id);
        var spaceRow = fieldRow(host, "Shares space with");
        var pick = document.createElement("select");
        pick.className = "mc-space-pick";
        spaces().forEach(function (space) {
            var opt = document.createElement("option");
            opt.value = space.id;
            var others = space.members.filter(function (item) { return item !== slot; })
                .map(function (item) { return item.template; });
            opt.textContent = "● " + space.id + (others.length ? " – " + others.join(", ") : " (just this box)");
            opt.style.color = space.color;
            opt.selected = space.id === slot.link.id;
            pick.appendChild(opt);
        });
        var fresh = document.createElement("option");
        fresh.value = "__new__";
        fresh.textContent = "+ New shared space";
        pick.appendChild(fresh);
        pick.addEventListener("change", function () {
            commit(function () {
                if (pick.value === "__new__") {
                    slot.link.id = freshSpaceId();
                } else {
                    var target = spaceById(pick.value);
                    slot.link.id = pick.value;
                    if (target) { slot.link.axis = target.axis; }
                }
            });
            renderInspector();
        });
        spaceRow.appendChild(pick);
        if (current) {
            var swatch = document.createElement("span");
            swatch.className = "mc-space-dot";
            swatch.style.background = current.color;
            spaceRow.insertBefore(swatch, pick);
        }

        var dirRow = fieldRow(host, "Boxes stack");
        var dir = document.createElement("select");
        [["vertical", "Top to bottom"], ["horizontal", "Left to right"]].forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item[0];
            opt.textContent = item[1];
            opt.selected = slot.link.axis === item[0];
            dir.appendChild(opt);
        });
        dir.addEventListener("change", function () {
            commit(function () {
                var id = slot.link.id;
                state.model.slots.forEach(function (item) {
                    if (item.link && item.link.id === id) { item.link.axis = dir.value; }
                });
            });
            renderInspector();
        });
        dirRow.appendChild(dir);

        var limits = document.createElement("div");
        limits.className = "mc-limits";
        host.appendChild(limits);
        limitField(limits, slot, "min", "Never smaller than",
            "The smallest this box can get. % is a share of the whole shared space.");
        limitField(limits, slot, "max", "Never bigger than",
            role === "fit"
                ? "Optional. It never grows past the size you drew it anyway."
                : "Optional. Leave empty to let it take all the leftover space.");

        if (role === "fit") {
            var emptyRow = fieldRow(host, "When nothing shows");
            var whenEmpty = document.createElement("select");
            [["collapse", "Close up (down to its minimum)"], ["keep", "Keep its drawn size"]].forEach(function (item) {
                var opt = document.createElement("option");
                opt.value = item[0];
                opt.textContent = item[1];
                opt.selected = (slot.link.when_empty || "collapse") === item[0];
                whenEmpty.appendChild(opt);
            });
            whenEmpty.addEventListener("change", function () {
                commit(function () { slot.link.when_empty = whenEmpty.value; });
                renderInspector();
            });
            emptyRow.appendChild(whenEmpty);

            var simRow = fieldRow(host, "Preview as");
            simRow.title = "Pretend this template is showing nothing, some or all of its content to see how the other boxes react. Only in the editor; nothing is saved.";
            simRow.appendChild(segmented([
                { value: "live", label: "Live" },
                { value: "empty", label: "Empty" },
                { value: "half", label: "Half" },
                { value: "full", label: "Full" }
            ], state.simulate[slot.id] || "live", function (value) {
                setSimulate(slot.id, value);
            }));
        }

        if (current && current.members.length > 1) {
            var order = document.createElement("div");
            order.className = "mc-inline";
            host.appendChild(order);
            order.appendChild(actionButton("Move up in stack", function () { nudgeOrder(-1); }));
            order.appendChild(actionButton("Move down in stack", function () { nudgeOrder(1); }));
        }
    }

    /* ---------------------------------------------------------------- simulate */

    function setSimulate(id, mode) {
        if (!mode || mode === "live") {
            delete state.simulate[id];
        } else {
            state.simulate[id] = mode;
        }
        renderAll();
    }

    /** Toolbar: pretend every "fits its content" box is empty / full, or go back to live. */
    function setSimulateAll(mode) {
        state.simulate = {};
        if (mode && mode !== "live" && state.model) {
            state.model.slots.forEach(function (slot) {
                if (roleOf(slot) === "fit") { state.simulate[slot.id] = mode; }
            });
        }
        renderAll();
    }

    function clearSimulate() {
        state.simulate = {};
    }

    function renderSimulateControls() {
        var select = $("mc-simulate");
        var chip = $("mc-simulating");
        var fits = state.model ? state.model.slots.filter(function (slot) { return roleOf(slot) === "fit"; }) : [];
        if (select) {
            select.disabled = !fits.length;
            var modes = fits.map(function (slot) { return state.simulate[slot.id] || "live"; });
            var same = modes.length && modes.every(function (m) { return m === modes[0]; });
            select.value = !isSimulating() ? "live" : (same ? modes[0] : "mixed");
        }
        if (chip) {
            chip.hidden = !isSimulating();
            var label = chip.querySelector(".mc-simulating__text");
            if (label) {
                var names = Object.keys(state.simulate).map(function (id) {
                    var slot = slotById(id);
                    return slot ? slot.template + ": " + SIMULATE_LABELS[state.simulate[id]] : "";
                }).filter(function (s) { return !!s; });
                label.textContent = "Previewing pretend sizes (" + names.join(", ") + ") – not saved";
            }
        }
    }

    /* ---------------------------------------------------------------- canvas indicators */

    function indicator(canvas, className, rect, text) {
        var el = document.createElement("div");
        el.className = className;
        el.style.left = rect.x + "px";
        el.style.top = rect.y + "px";
        el.style.width = Math.max(0, rect.w) + "px";
        el.style.height = Math.max(0, rect.h) + "px";
        if (text) { el.setAttribute("data-label", text); }
        canvas.appendChild(el);
        return el;
    }

    /**
     * Editor-only drawings: each shared space's area, a badge on every box, the drawn
     * size and minimum of boxes in a space, and arrows where boxes give and take space.
     */
    function addSpaceIndicators(canvas, layout) {
        if (state.dragging) { return; }
        var solved = {};
        layout.slots.forEach(function (slot) { solved[slot.id] = slot; });
        layout.slots.forEach(function (slot) {
            var el = canvas.querySelector('.mc-slot[data-slot="' + slot.id + '"]');
            if (!el) { return; }
            var model = slotById(slot.id);
            var role = model ? roleOf(model) : "fixed";
            var badge = el.querySelector(".mc-badge");
            if (!badge) {
                badge = document.createElement("div");
                el.appendChild(badge);
            }
            badge.className = "mc-badge mc-badge--" + role + (state.simulate[slot.id] ? " is-simulated" : "");
            var text = { fixed: "Fixed", fit: "Fits content", fill: "Fills leftover" }[role];
            if (state.simulate[slot.id]) { text += " · pretend " + SIMULATE_LABELS[state.simulate[slot.id]].toLowerCase(); }
            badge.innerHTML = UI.icon({ fixed: "pin", fit: "fit_content", fill: "fill_space" }[role]);
            var span = document.createElement("span");
            span.textContent = text;
            badge.appendChild(span);
            var space = model && model.link ? spaceById(model.link.id) : null;
            if (space) { badge.style.borderColor = space.color; } else { badge.style.borderColor = ""; }
            el.classList.toggle("is-flagged", state.flagged.indexOf(slot.id) !== -1);
        });

        spaces().forEach(function (space) {
            var extent = Layout.groupExtent ? Layout.groupExtent(space.members, space.axis) : null;
            if (!extent) { return; }
            var crossStart = Infinity;
            var crossEnd = -Infinity;
            space.members.forEach(function (slot) {
                var a = space.axis === "vertical" ? slot.x : slot.y;
                var b = a + (space.axis === "vertical" ? slot.w : slot.h);
                crossStart = Math.min(crossStart, a);
                crossEnd = Math.max(crossEnd, b);
            });
            var area = space.axis === "vertical"
                ? { x: crossStart, y: extent[0], w: crossEnd - crossStart, h: extent[1] }
                : { x: extent[0], y: crossStart, w: extent[1], h: crossEnd - crossStart };
            var outline = indicator(canvas, "mc-space", area, space.id + " · " + directionText(space.axis));
            outline.style.setProperty("--mc-space-color", space.color);

            space.members.forEach(function (model, index) {
                var slot = solved[model.id];
                if (!slot) { return; }
                var vertical = space.axis === "vertical";
                var size = vertical ? slot.h : slot.w;
                var drawn = vertical ? model.h : model.w;
                var link = model.link;
                if (roleOf(model) === "fit" && size < drawn) {
                    indicator(canvas, "mc-designated", {
                        x: slot.x, y: slot.y,
                        w: vertical ? slot.w : drawn,
                        h: vertical ? drawn : slot.h
                    }, "can grow to " + drawn + "px (drawn size)");
                }
                if (roleOf(model) === "fill") {
                    var min = Math.max(link.min_px || 0, Math.round(extent[1] * (link.min_pct || 0) / 100));
                    if (min > 0 && min < size) {
                        indicator(canvas, "mc-min-line" + (vertical ? "" : " mc-min-line--h"), vertical
                            ? { x: slot.x, y: slot.y + min, w: slot.w, h: 0 }
                            : { x: slot.x + min, y: slot.y, w: 0, h: slot.h }, "min " + min + "px");
                    }
                    if (size < drawn) {
                        var crop = indicator(canvas, "mc-crop" + (vertical ? "" : " mc-crop--h"), vertical
                            ? { x: slot.x, y: slot.y + size - 6, w: slot.w, h: 6 }
                            : { x: slot.x + size - 6, y: slot.y, w: 6, h: slot.h }, "");
                        crop.title = "Cropped: " + size + "px of the " + drawn + "px it was drawn at";
                    }
                }
                var next = space.members[index + 1];
                var nextSlot = next ? solved[next.id] : null;
                if (nextSlot) {
                    var giver = roleOf(model) === "fit" ? model : (roleOf(next) === "fit" ? next : null);
                    var taker = giver === model ? next : model;
                    var edge = vertical
                        ? { x: slot.x + slot.w / 2 - 11, y: (slot.y + slot.h + nextSlot.y) / 2 - 11, w: 22, h: 22 }
                        : { x: (slot.x + slot.w + nextSlot.x) / 2 - 11, y: slot.y + slot.h / 2 - 11, w: 22, h: 22 };
                    var arrow = indicator(canvas, "mc-flow" + (vertical ? "" : " mc-flow--h"), edge, "");
                    arrow.style.setProperty("--mc-space-color", space.color);
                    arrow.textContent = vertical ? "↕" : "↔";
                    arrow.title = giver
                        ? "“" + taker.template + "” gets the room “" + giver.template + "” isn't using."
                        : "These boxes share the leftover space.";
                }
            });
        });
    }

    function actionButton(label, fn) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "ss-btn";
        button.textContent = label;
        button.addEventListener("click", function () {
            commit(fn);
            renderInspector();
        });
        return button;
    }

    function linkIds(axis) {
        var ids = [];
        (state.model.slots || []).forEach(function (slot) {
            if (!slot.link) { return; }
            if (axis && slot.link.axis !== axis) { return; }
            if (ids.indexOf(slot.link.id) === -1) { ids.push(slot.link.id); }
        });
        return ids;
    }

    function allTemplateNames() {
        return state.templates.spore.concat(state.templates.builtin, state.templates.legacy);
    }

    function renderPalette() {
        var list = $("mc-template-list");
        if (!list) { return; }
        var query = ($("mc-template-search").value || "").trim().toLowerCase();
        list.textContent = "";
        [
            ["Spore Studio", state.templates.spore],
            ["Built-in", state.templates.builtin],
            ["Other templates", state.templates.legacy]
        ].forEach(function (group) {
            var names = group[1].filter(function (name) {
                return !query || name.toLowerCase().indexOf(query) !== -1;
            });
            if (!names.length) { return; }
            var block = document.createElement("div");
            block.className = "mc-group";
            var heading = document.createElement("h4");
            heading.textContent = group[0];
            block.appendChild(heading);
            names.forEach(function (name) {
                var item = document.createElement("div");
                item.className = "mc-template";
                item.draggable = true;
                item.title = "Drag onto the canvas, or double-click to add it";
                item.innerHTML = '<span class="mc-template__icon">' + UI.icon("file") + "</span>";
                var itemName = document.createElement("span");
                itemName.className = "mc-template__name";
                itemName.textContent = name;
                item.appendChild(itemName);
                item.insertAdjacentHTML("beforeend", UI.icon("drag", "mc-template__drag"));
                item.addEventListener("dragstart", function (ev) {
                    ev.dataTransfer.setData("text/plain", name);
                    ev.dataTransfer.effectAllowed = "copy";
                });
                item.addEventListener("dblclick", function () {
                    if (!state.model) {
                        needContainerNotice();
                        return;
                    }
                    commit(function () {
                        var slot = blankSlot(name, 40, 40 + state.model.slots.length * 24);
                        state.model.slots.push(slot);
                        state.selectedId = slot.id;
                    });
                    renderInspector();
                });
                block.appendChild(item);
            });
            list.appendChild(block);
        });
    }

    function renderAll() {
        renderCanvas();
        renderInspector();
        renderPalette();
        var button = $("mc-btn-placeholders");
        if (button) { button.classList.toggle("is-on", state.placeholders); }
    }

    function fillSelect() {
        var sel = $("mc-container-select");
        var current = state.model ? state.model.route : "";
        sel.textContent = "";
        state.list.forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item.route;
            opt.textContent = item.name + "  /" + item.route;
            sel.appendChild(opt);
        });
        if (current && state.list.some(function (item) { return item.route === current; })) {
            sel.value = current;
        }
    }

    function refreshLists() {
        return Promise.all([
            api("/api/spore-studio/containers"),
            api("/api/spore-studio/container-templates")
        ]).then(function (results) {
            state.list = results[0].containers || [];
            state.templates = results[1] || { spore: [], legacy: [], builtin: [] };
            state.known = {};
            allTemplateNames().forEach(function (name) { state.known[name] = true; });
            fillSelect();
            renderPalette();
        });
    }

    function loadContainer(route) {
        return api("/api/spore-studio/containers/" + encodeURIComponent(route)).then(function (model) {
            state.model = model;
            state.savedRoute = model.route;
            state.selectedId = null;
            state.dirty = false;
            state.used = {};
            state.origins = {};
            clearSimulate();
            state.savedSnap = JSON.stringify(model);
            state.history = [];
            state.future = [];
            updateDirty();
            fillSelect();
            renderAll();
        });
    }

    function saveCurrent() {
        if (!state.model) { return Promise.resolve(); }
        var route = state.model.route || state.savedRoute || "";
        var saved = false;
        var sent = JSON.stringify(state.model);
        return api("/api/spore-studio/containers/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                container: state.model,
                previous_route: state.savedRoute
            })
        }).then(function (body) {
            state.savedRoute = body.container.route;
            state.savedSnap = JSON.stringify(body.container);
            if (JSON.stringify(state.model) === sent) {
                state.model = body.container;
                clearSimulate();
                state.dirty = false;
                state.history = [];
                state.future = [];
                updateDirty();
            } else {
                // Edits made while saving stay in the editor, still marked unsaved.
                state.model.route = body.container.route;
                markDirty();
            }
            // The inspector held the old slot objects; redraw so later edits reach the model.
            renderAll();
            saved = true;
            notify({ type: "success", title: "Saved container “/" + body.container.route + "”" });
            return refreshLists();
        }).catch(function (err) {
            if (saved) {
                notifyListFailed(err);
                return;
            }
            notify({
                type: "error",
                title: "Couldn't save container “/" + route + "”",
                message: failReason(err, "saving") + " Your changes are still in the editor.",
                hint: err && err.userText ? "Fix the problem, then click Save again." : failHint(err),
                details: errorDetails(err)
            });
        });
    }

    function notifyListFailed(err) {
        notify({
            type: "error",
            title: "Couldn't load your containers",
            message: failReason(err, "loading the container list"),
            hint: failHint(err),
            details: errorDetails(err),
            dedupe: "containers_list"
        });
    }

    function openForm(title, fields, onOk, opts) {
        opts = opts || {};
        var frame = UI.modalFrame({ icon: opts.icon || "grid", title: title, sub: opts.sub || "" });
        var overlay = frame.overlay;
        var inputs = {};
        fields.forEach(function (spec) {
            var row = fieldRow(frame.body, spec.label);
            row.classList.add("ss-field");
            var input = document.createElement(spec.kind === "select" ? "select" : "input");
            if (spec.kind !== "select") {
                input.type = spec.kind || "text";
                input.value = spec.value || "";
            } else {
                spec.options.forEach(function (opt) {
                    var option = document.createElement("option");
                    option.value = opt.value;
                    option.textContent = opt.label;
                    if (opt.value === spec.value) { option.selected = true; }
                    input.appendChild(option);
                });
            }
            inputs[spec.id] = input;
            if (spec.onInput) { input.addEventListener("input", function () { spec.onInput(input, inputs); }); }
            row.appendChild(input);
            if (spec.help) {
                var help = document.createElement("div");
                help.className = "ss-help";
                help.textContent = spec.help;
                row.appendChild(help);
            }
        });
        // Problems with the form (taken route, bad name) show here, not as pop-ups.
        var formError = document.createElement("div");
        formError.className = "ss-form-error";
        formError.setAttribute("role", "alert");
        formError.hidden = true;
        frame.body.appendChild(formError);
        function setError(text) {
            formError.textContent = text || "";
            formError.hidden = !text;
        }
        var release;
        function close() {
            if (release) { release(); }
            overlay.remove();
        }
        var cancel = document.createElement("button");
        cancel.className = "ss-btn";
        cancel.type = "button";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", close);
        var ok = document.createElement("button");
        ok.className = "ss-btn ss-btn--primary";
        ok.type = "button";
        ok.innerHTML = UI.icon(opts.okIcon || "plus") + "<span></span>";
        ok.lastChild.textContent = opts.ok || "Create";
        ok.addEventListener("click", function () {
            var values = {};
            Object.keys(inputs).forEach(function (key) { values[key] = inputs[key].value; });
            setError("");
            Promise.resolve(onOk(values, setError)).then(function (done) {
                if (done !== false) { close(); }
            });
        });
        frame.foot.appendChild(cancel);
        frame.foot.appendChild(ok);
        document.body.appendChild(overlay);
        release = UI.wireModal(overlay, close);
        Object.keys(inputs).forEach(function (key) {
            if (inputs[key].tagName === "INPUT") {
                inputs[key].addEventListener("keydown", function (ev) {
                    if (ev.key === "Enter") { ev.preventDefault(); ok.click(); }
                });
            }
        });
    }

    function suggestRoute(name) {
        var text = (name || "").trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_-]/g, "").replace(/^[_-]+|[_-]+$/g, "");
        if (!text || text === "api" || text === "assets" || text === "static") { text = "container"; }
        return text.slice(0, 48);
    }

    function createContainer() {
        if (state.dirty) {
            confirmDiscardContainer().then(function (ok) {
                if (!ok) { return; }
                discardContainerEdits();
                createContainer();
            });
            return;
        }
        openForm("New container", [
            { id: "name", label: "Name", value: "" },
            { id: "route", label: "Route path", value: "",
              help: "The address OBS loads, like /stream_layout. It follows the name until you edit it." },
            {
                id: "preset",
                label: "Resolution",
                kind: "select",
                value: "1920x1080",
                options: [
                    { value: "1920x1080", label: "1920 × 1080" },
                    { value: "1280x720", label: "1280 × 720" },
                    { value: "1080x1920", label: "1080 × 1920" },
                    { value: "720x1280", label: "720 × 1280" }
                ]
            }
        ], function (values, setError) {
            var parts = (values.preset || "1920x1080").split("x");
            var created = false;
            return api("/api/spore-studio/containers/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: values.name,
                    route: values.route || suggestRoute(values.name),
                    width: Number(parts[0]),
                    height: Number(parts[1])
                })
            }).then(function (body) {
                created = true;
                notify({ type: "success", title: "Created container “/" + body.container.route + "”" });
                return refreshLists().then(function () {
                    return loadContainer(body.container.route);
                });
            }).catch(function (err) {
                if (created) {
                    notifyListFailed(err);
                    return true;
                }
                return formFailure(err, setError, "Couldn't create the container", "creating the container");
            });
        }, { icon: "grid", sub: "A container puts several templates on one OBS browser source." });
        var nameInput = document.querySelector(".ss-create-overlay input");
        var routeInput = document.querySelectorAll(".ss-create-overlay input")[1];
        if (nameInput && routeInput) {
            nameInput.addEventListener("input", function () {
                if (!routeInput.dataset.touched) {
                    routeInput.value = suggestRoute(nameInput.value);
                }
            });
            routeInput.addEventListener("input", function () { routeInput.dataset.touched = "1"; });
        }
    }

    /**
     * A create/duplicate form request failed: validation text stays in the
     * form; anything else also gets a notification. Returns false (keep open).
     */
    function formFailure(err, setError, title, doing) {
        if (err && err.userText) {
            setError(err.userText);
            return false;
        }
        setError(title + ".");
        notify({
            type: "error",
            title: title,
            message: failReason(err, doing),
            hint: failHint(err),
            details: errorDetails(err)
        });
        return false;
    }

    function duplicateContainer() {
        if (!state.model) { return; }
        if (state.dirty) {
            notify({
                type: "warning",
                title: "Save this container first",
                message: "Duplicate copies the saved version, so your unsaved changes wouldn't be included.",
                hint: "Click Save, then Duplicate."
            });
            return;
        }
        openForm("Duplicate container", [
            { id: "name", label: "Name", value: state.model.name + " copy" },
            { id: "route", label: "Route path", value: suggestRoute(state.model.route + "_copy") }
        ], function (values, setError) {
            var copied = false;
            return api("/api/spore-studio/containers/duplicate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    route: state.savedRoute,
                    name: values.name,
                    new_route: values.route || suggestRoute(values.name)
                })
            }).then(function (body) {
                copied = true;
                notify({ type: "success", title: "Duplicated as “/" + body.container.route + "”" });
                return refreshLists().then(function () { return loadContainer(body.container.route); });
            }).catch(function (err) {
                if (copied) {
                    notifyListFailed(err);
                    return true;
                }
                return formFailure(err, setError, "Couldn't duplicate the container", "duplicating the container");
            });
        }, { icon: "layers", ok: "Duplicate", okIcon: "layers", sub: "Copies the saved version of this container to a new route." });
    }

    function deleteContainer() {
        if (!state.model) { return; }
        UI.confirm({
            danger: true,
            title: "Delete container /" + state.model.route + "?",
            message: "The route stops working in OBS. The templates inside it are not deleted.",
            ok: "Delete container"
        }).then(function (ok) {
            if (ok) { deleteContainerNow(); }
        });
    }

    function deleteContainerNow() {
        if (!state.model) { return; }
        var route = state.savedRoute || state.model.route;
        var deleted = false;
        api("/api/spore-studio/containers/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ route: route })
        }).then(function () {
            deleted = true;
            notify({ type: "success", title: "Deleted container “/" + route + "”" });
            state.model = null;
            state.savedRoute = null;
            state.dirty = false;
            state.selectedId = null;
            updateDirty();
            return refreshLists();
        }).then(function () {
            if (state.list.length) { return loadContainer(state.list[0].route); }
            renderAll();
        }).catch(function (err) {
            if (deleted) {
                notifyListFailed(err);
                return;
            }
            notify({
                type: "error",
                title: "Couldn't delete container “/" + route + "”",
                message: failReason(err, "deleting the container"),
                hint: failHint(err),
                details: errorDetails(err)
            });
        });
    }

    function nudgeOrder(dir) {
        var slot = selectedSlot();
        if (!slot || !slot.link) { return; }
        var axis = slot.link.axis;
        var siblings = state.model.slots.filter(function (item) {
            return item.link && item.link.id === slot.link.id;
        }).sort(function (a, b) {
            var av = axis === "vertical" ? a.y : a.x;
            var bv = axis === "vertical" ? b.y : b.x;
            return av - bv;
        });
        var index = siblings.indexOf(slot);
        var other = siblings[index + dir];
        if (!other) { return; }
        if (axis === "vertical") {
            var y = slot.y;
            slot.y = other.y - dir;
            other.y = y;
        } else {
            var x = slot.x;
            slot.x = other.x - dir;
            other.x = x;
        }
    }

    /* Go back to the saved copy, so discarded edits don't come back later. */
    function discardContainerEdits() {
        state.model = state.savedSnap ? JSON.parse(state.savedSnap) : null;
        clearSimulate();
        state.history = [];
        state.future = [];
        if (state.selectedId && !selectedSlot()) { state.selectedId = null; }
        state.dirty = false;
        updateDirty();
        renderAll();
    }

    function confirmDiscardContainer() {
        return UI.confirm({
            icon: "alert",
            danger: true,
            title: "Discard unsaved container changes?",
            message: state.model ? "Your changes to /" + state.model.route + " haven't been saved." : "Your changes haven't been saved.",
            ok: "Discard changes",
            cancel: "Keep editing"
        });
    }

    function setMode(mode) {
        if (mode === state.mode) { return; }
        if (state.mode === "containers" && state.dirty && mode !== "containers") {
            confirmDiscardContainer().then(function (ok) {
                if (!ok) { return; }
                discardContainerEdits();
                applyMode(mode);
            });
            return;
        }
        applyMode(mode);
    }

    function applyMode(mode) {
        state.mode = mode;
        var root = $("ss-root");
        root.classList.toggle("ss-root--containers", mode === "containers");
        $("ss-mode-templates").classList.toggle("is-active", mode === "templates");
        $("ss-mode-containers").classList.toggle("is-active", mode === "containers");
        $("ss-mode-templates").setAttribute("aria-selected", mode === "templates" ? "true" : "false");
        $("ss-mode-containers").setAttribute("aria-selected", mode === "containers" ? "true" : "false");
        if (mode === "containers") {
            refreshLists().then(function () {
                if (!state.model && state.list.length) { return loadContainer(state.list[0].route); }
                renderAll();
            }).catch(notifyListFailed);
        }
    }

    function init() {
        if (!$("ss-mode-containers") || !Layout) { return; }
        $("ss-mode-templates").addEventListener("click", function () { setMode("templates"); });
        $("ss-mode-containers").addEventListener("click", function () { setMode("containers"); });
        $("mc-btn-create").addEventListener("click", createContainer);
        $("mc-btn-duplicate").addEventListener("click", duplicateContainer);
        $("mc-btn-delete").addEventListener("click", deleteContainer);
        $("mc-btn-save").addEventListener("click", saveCurrent);
        $("mc-btn-undo").addEventListener("click", undo);
        $("mc-btn-redo").addEventListener("click", redo);
        $("mc-btn-placeholders").addEventListener("click", function () {
            state.placeholders = !state.placeholders;
            localStorage.setItem("mycelian-container-placeholders", state.placeholders ? "1" : "0");
            renderAll();
        });
        $("mc-btn-preview").addEventListener("click", function () {
            if (!state.model) { return; }
            if (!state.model.enabled) {
                notify({
                    type: "warning",
                    title: "Turn the container on first",
                    message: "A disabled container has no route to preview.",
                    hint: "Tick “Enabled” in the container settings, then click Preview."
                });
                return;
            }
            api("/api/spore-studio/containers/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ container: state.model })
            }).then(function (body) {
                window.open(body.url, "_blank");
                var dirtyEl = $("mc-dirty");
                if (state.dirty && dirtyEl) {
                    // Inline note instead of a pop-up on every Preview click.
                    dirtyEl.textContent = "Unsaved changes (preview shows this draft)";
                }
            }).catch(function (err) {
                notify({
                    type: "error",
                    title: "Couldn't open the preview",
                    message: failReason(err, "preparing the preview"),
                    hint: failHint(err),
                    details: errorDetails(err)
                });
            });
        });
        $("mc-container-select").addEventListener("change", function (ev) {
            var select = ev.target;
            var route = select.value;
            if (state.dirty) {
                select.value = state.savedRoute || "";
                confirmDiscardContainer().then(function (ok) {
                    if (!ok) { return; }
                    select.value = route;
                    openRoute();
                });
                return;
            }
            openRoute();
            function openRoute() {
                loadContainer(route).catch(function (err) {
                    select.value = state.savedRoute || "";
                    notify({
                        type: "error",
                        title: "Couldn't open container “/" + route + "”",
                        message: failReason(err, "opening the container") +
                            " It may have been deleted, or its file may be damaged.",
                        hint: failHint(err),
                        details: errorDetails(err),
                        dedupe: "container_open:" + route
                    });
                });
            }
        });
        $("mc-template-search").addEventListener("input", renderPalette);
        var simulate = $("mc-simulate");
        if (simulate) {
            simulate.addEventListener("change", function () { setSimulateAll(simulate.value); });
        }
        var backToLive = $("mc-btn-simulate-live");
        if (backToLive) {
            backToLive.addEventListener("click", function () { setSimulateAll("live"); });
        }
        // Templates may report their own content size (see container_measure.js).
        window.addEventListener("message", function (ev) {
            if (!state.explicit) { return; }
            var frames = Array.prototype.slice.call(document.querySelectorAll("#mc-canvas iframe[data-slot]"));
            if (state.explicit.handle(ev, frames)) { scheduleMeasure(); }
        });
        var canvas = $("mc-canvas");
        canvas.addEventListener("dragover", function (ev) { ev.preventDefault(); });
        canvas.addEventListener("drop", function (ev) {
            ev.preventDefault();
            if (!state.model) {
                needContainerNotice();
                return;
            }
            var name = ev.dataTransfer.getData("text/plain");
            if (!name) { return; }
            var pt = canvasPoint(ev);
            commit(function () {
                var slot = blankSlot(name, snapValue(pt.x), snapValue(pt.y));
                state.model.slots.push(slot);
                state.selectedId = slot.id;
            });
            renderInspector();
        });
        canvas.addEventListener("mousedown", function (ev) {
            if (ev.target !== canvas) { return; }
            state.selectedId = null;
            renderAll();
        });
        window.addEventListener("keydown", function (ev) {
            if (state.mode !== "containers") { return; }
            var tag = ev.target && ev.target.tagName;
            var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
            var meta = ev.ctrlKey || ev.metaKey;
            if (meta && ev.key === "s") {
                ev.preventDefault();
                saveCurrent();
            } else if (meta && ev.key === "z" && !ev.shiftKey) {
                ev.preventDefault();
                undo();
            } else if (meta && (ev.key === "Z" || (ev.key === "z" && ev.shiftKey) || ev.key === "y")) {
                ev.preventDefault();
                redo();
            } else if (!typing && (ev.key === "Delete" || ev.key === "Backspace")) {
                var slot = selectedSlot();
                if (!slot) { return; }
                ev.preventDefault();
                commit(function () {
                    state.model.slots = state.model.slots.filter(function (item) { return item.id !== slot.id; });
                    state.selectedId = null;
                });
                renderInspector();
            }
        });
        window.addEventListener("resize", function () {
            if (state.mode === "containers") { fitCanvas(); }
        });
        var scroll = $("mc-canvas-scroll");
        if (window.ResizeObserver && scroll) {
            new ResizeObserver(function () {
                if (state.mode === "containers") { fitCanvas(); }
            }).observe(scroll);
        }
        setInterval(scheduleMeasure, 500);
        if (state.placeholders) {
            var button = $("mc-btn-placeholders");
            if (button) { button.classList.add("is-on"); }
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
