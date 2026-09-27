/*
 * Spore Studio container mode. Places existing templates on one route.
 * Layout math lives in container_layout.js.
 */
(function () {
    var Layout = window.MycelianContainerLayout;
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
        dragging: false,
        known: {}
    };

    function $(id) { return document.getElementById(id); }

    function toast(message, kind) {
        var el = $("ss-toast");
        if (!el) { return; }
        el.textContent = message;
        el.classList.remove("ss-toast--success", "ss-toast--error");
        if (kind === "success") { el.classList.add("ss-toast--success"); }
        if (kind === "error") { el.classList.add("ss-toast--error"); }
        el.classList.add("visible");
        clearTimeout(el._mcTimeout);
        el._mcTimeout = setTimeout(function () { el.classList.remove("visible"); }, 2200);
    }

    function api(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                if (!response.ok) {
                    throw new Error(body.error || ("Request failed (" + response.status + ")"));
                }
                return body;
            });
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

    function layoutNow() {
        if (!state.model || !Layout) { return null; }
        return Layout.solve(state.model, state.placeholders ? null : state.used);
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

    function measureFrame(iframe) {
        try {
            var doc = iframe.contentDocument;
            if (!doc || !doc.body || !doc.defaultView) { return null; }
            var viewW = iframe.clientWidth || iframe.offsetWidth;
            var viewH = iframe.clientHeight || iframe.offsetHeight;
            if (viewW < 2 || viewH < 2) { return null; }
            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, found = false;
            var nodes = doc.body.querySelectorAll("*");
            for (var i = 0; i < nodes.length; i++) {
                var node = nodes[i];
                var cs = doc.defaultView.getComputedStyle(node);
                if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) {
                    continue;
                }
                var rect = node.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) { continue; }
                if (rect.width >= viewW - 1 && rect.height >= viewH - 1) { continue; }
                found = true;
                minX = Math.min(minX, rect.left);
                minY = Math.min(minY, rect.top);
                maxX = Math.max(maxX, rect.right);
                maxY = Math.max(maxY, rect.bottom);
            }
            if (!found) { return null; }
            return {
                w: Math.ceil(maxX - minX),
                h: Math.ceil(maxY - minY),
                originX: minX,
                originY: minY
            };
        } catch (err) {
            return null;
        }
    }

    var measureTimer = null;
    function scheduleMeasure() {
        if (state.placeholders || state.dragging) { return; }
        clearTimeout(measureTimer);
        measureTimer = setTimeout(runMeasure, 150);
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
            next[id] = { w: measured.w, h: measured.h };
            origins[id] = { x: measured.originX, y: measured.originY };
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
        canvas.style.marginRight = (state.model.width * (scale - 1)) + "px";
        canvas.style.marginBottom = (state.model.height * (scale - 1)) + "px";
        var grid = state.model.snap || 0;
        canvas.style.backgroundSize = grid ? (grid + "px " + grid + "px") : "0 0";
        var bg = state.model.background === "transparent" ? "transparent" : state.model.background;
        canvas.style.backgroundColor = bg;
    }

    function renderWarnings(layout) {
        var host = $("mc-warnings");
        if (!host) { return; }
        var messages = (layout.warnings || []).map(function (item) { return item.message; });
        (state.model.slots || []).forEach(function (slot) {
            if (!state.known[slot.template]) {
                messages.push(slot.template + " was not found. Its slot will stay empty on the route.");
            }
        });
        if (!messages.length) {
            host.hidden = true;
            host.textContent = "";
            return;
        }
        host.hidden = false;
        host.textContent = messages.join(" ");
    }

    function renderCanvas() {
        var canvas = $("mc-canvas");
        if (!canvas || !state.model) { return; }
        var layout = layoutNow();
        if (!layout) { return; }
        fitCanvas();
        renderWarnings(layout);
        var kept = {};
        canvas.querySelectorAll("iframe[data-slot]").forEach(function (frame) {
            kept[frame.getAttribute("data-slot")] = frame;
        });
        canvas.textContent = "";
        layout.slots.forEach(function (slot) {
            if (slot.role === "locked") {
                var shorter = slot.axis === "vertical" ? slot.h < slot.designated_h : slot.w < slot.designated_w;
                if (shorter) {
                    var ghost = document.createElement("div");
                    ghost.className = "mc-designated";
                    ghost.style.left = slot.x + "px";
                    ghost.style.top = slot.y + "px";
                    ghost.style.width = (slot.axis === "horizontal" ? slot.designated_w : slot.w) + "px";
                    ghost.style.height = (slot.axis === "vertical" ? slot.designated_h : slot.h) + "px";
                    canvas.appendChild(ghost);
                }
            }
            var el = document.createElement("div");
            el.className = "mc-slot" + (slot.id === state.selectedId ? " is-selected" : "");
            el.setAttribute("role", "button");
            el.setAttribute("aria-label", slot.template);
            if (!state.known[slot.template]) { el.classList.add("is-missing"); }
            el.dataset.slot = slot.id;
            el.style.left = slot.x + "px";
            el.style.top = slot.y + "px";
            el.style.width = slot.w + "px";
            el.style.height = slot.h + "px";
            el.style.zIndex = String(slot.z || 1);
            el.style.opacity = String((slot.opacity == null ? 100 : slot.opacity) / 100);
            if (!slot.visible) { el.style.outline = "1px dashed var(--ss-text-muted)"; }
            var label = document.createElement("div");
            label.className = "mc-slot__label";
            label.textContent = slot.template + (state.known[slot.template] ? "" : " (missing)");
            el.appendChild(label);
            if (state.placeholders || !slot.visible) {
                var holder = document.createElement("div");
                holder.className = "mc-placeholder";
                holder.textContent = slot.template;
                el.appendChild(holder);
            } else if (state.known[slot.template]) {
                var clip = document.createElement("div");
                clip.className = "mc-clip";
                clip.style.left = slot.content.x + "px";
                clip.style.top = slot.content.y + "px";
                clip.style.width = slot.content.w + "px";
                clip.style.height = slot.content.h + "px";
                var src = embedSrc(slot);
                var frame = kept[slot.id];
                if (!frame || frame.getAttribute("data-src") !== src) {
                    frame = document.createElement("iframe");
                    frame.dataset.slot = slot.id;
                    frame.dataset.src = src;
                    frame.src = src;
                    frame.setAttribute("allow", "autoplay");
                    frame.addEventListener("load", function () {
                        scheduleMeasure();
                        try {
                            var doc = frame.contentDocument;
                            if (doc && doc.documentElement && window.MutationObserver) {
                                var obs = new MutationObserver(scheduleMeasure);
                                obs.observe(doc.documentElement, { childList: true, subtree: true, attributes: true });
                            }
                        } catch (err) { /* ignore */ }
                    });
                }
                applyFrame(frame, slot);
                clip.appendChild(frame);
                el.appendChild(clip);
            }
            if (slot.id === state.selectedId) {
                addHandles(el, slot);
            }
            el.addEventListener("mousedown", onSlotMouseDown);
            canvas.appendChild(el);
        });
        addGutters(canvas, layout);
    }

    function applyFrame(frame, slot) {
        var origin = state.origins[slot.id] || { x: 0, y: 0 };
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
            frame.style.left = (-origin.x) + "px";
            frame.style.top = (-origin.y) + "px";
        } else {
            frame.style.left = "0px";
            frame.style.top = "0px";
        }
    }

    function addHandles(el, slot) {
        var vertical = slot.axis === "vertical";
        var horizontal = slot.axis === "horizontal";
        var flex = slot.role === "flexible";
        var dirs = ["e", "s", "se"];
        if (!slot.axis) { dirs.push("w", "n"); }
        if (vertical && flex) { dirs = ["e"]; }
        if (horizontal && flex) { dirs = ["s"]; }
        dirs.forEach(function (dir) {
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
            renderInspector();
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
            renderInspector();
        }
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
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
    }

    function renderContainerInspector(host) {
        var title = document.createElement("h3");
        title.textContent = "Container";
        host.appendChild(title);
        var blurb = document.createElement("p");
        blurb.className = "mc-hint";
        blurb.textContent = "Locked templates keep the size you set and give unused space to flexible templates in the same link. Drag the bar between linked templates to space them out.";
        host.appendChild(blurb);

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

        var url = document.createElement("p");
        url.className = "mc-hint";
        url.textContent = location.origin + "/" + state.model.route;
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
            var color = document.createElement("input");
            color.type = "color";
            color.value = state.model.background;
            color.addEventListener("change", function () {
                commit(function () { state.model.background = color.value; });
            });
            fieldRow(host, "Color").appendChild(color);
        }

        var spacingRow = fieldRow(host, "Space between templates");
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
    }

    function renderSlotInspector(host, slot) {
        var title = document.createElement("h3");
        title.textContent = slot.template;
        host.appendChild(title);

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
        addCheck(host, "Visible", slot.visible, function (checked) { slot.visible = checked; });
        addCheck(host, "Lock position", slot.locked_position, function (checked) { slot.locked_position = checked; });
        addCheck(host, "Click-through", !slot.pointer_events, function (checked) { slot.pointer_events = !checked; });

        var uniform = slot.padding.t === slot.padding.r && slot.padding.r === slot.padding.b && slot.padding.b === slot.padding.l;
        addCheck(host, "Padding same on all sides", uniform, function (checked) {
            if (checked) {
                var n = slot.padding.t;
                slot.padding = { t: n, r: n, b: n, l: n };
            }
            renderInspector();
        });
        if (slot.padding.t === slot.padding.r && slot.padding.r === slot.padding.b && slot.padding.b === slot.padding.l) {
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

        addCheck(host, "Mute audio", slot.audio.muted, function (checked) { slot.audio.muted = checked; });
        addNumber(host, "Volume", slot.audio.volume, function (value) {
            slot.audio.volume = clamp(Math.round(value || 0), 0, 100);
        }, { min: 0, max: 100 });

        var axis = document.createElement("select");
        [
            ["", "No size link"],
            ["vertical", "Share leftover height"],
            ["horizontal", "Share leftover width"]
        ].forEach(function (item) {
            var opt = document.createElement("option");
            opt.value = item[0];
            opt.textContent = item[1];
            opt.selected = (slot.link ? slot.link.axis : "") === item[0];
            axis.appendChild(opt);
        });
        axis.addEventListener("change", function () {
            commit(function () {
                if (!axis.value) {
                    slot.link = null;
                    return;
                }
                var existing = linkIds(axis.value);
                slot.link = {
                    id: existing[0] || freshLinkId(),
                    axis: axis.value,
                    role: "locked",
                    min_px: slot.link ? slot.link.min_px : 0,
                    min_pct: slot.link ? slot.link.min_pct : 0,
                    max_px: slot.link ? slot.link.max_px : null,
                    max_pct: slot.link ? slot.link.max_pct : null
                };
            });
            renderInspector();
        });
        fieldRow(host, "Size link").appendChild(axis);

        if (slot.link) {
            var group = document.createElement("select");
            linkIds(slot.link.axis).concat(["__new__"]).forEach(function (id) {
                var opt = document.createElement("option");
                opt.value = id;
                opt.textContent = id === "__new__" ? "New link" : id;
                opt.selected = id === slot.link.id;
                group.appendChild(opt);
            });
            group.addEventListener("change", function () {
                commit(function () {
                    slot.link.id = group.value === "__new__" ? freshLinkId() : group.value;
                });
                renderInspector();
            });
            fieldRow(host, "Link group").appendChild(group);

            var role = document.createElement("select");
            [["locked", "Locked (priority, keeps this size)"], ["flexible", "Flexible (fills leftover space)"]].forEach(function (item) {
                var opt = document.createElement("option");
                opt.value = item[0];
                opt.textContent = item[1];
                opt.selected = slot.link.role === item[0];
                role.appendChild(opt);
            });
            role.addEventListener("change", function () {
                commit(function () { slot.link.role = role.value; });
            });
            fieldRow(host, "Size priority").appendChild(role);

            var mins = document.createElement("div");
            mins.className = "mc-inline";
            host.appendChild(mins);
            addNumber(mins, "Min px", slot.link.min_px, function (value) {
                slot.link.min_px = clamp(Math.round(value || 0), 0, 7680);
            }, { min: 0 });
            addNumber(mins, "Min %", slot.link.min_pct, function (value) {
                slot.link.min_pct = clamp(Math.round(value || 0), 0, 100);
            }, { min: 0, max: 100 });
            var maxes = document.createElement("div");
            maxes.className = "mc-inline";
            host.appendChild(maxes);
            addNumber(maxes, "Max px", slot.link.max_px, function (value) {
                slot.link.max_px = value == null || value === "" ? null : clamp(Math.round(value), 0, 7680);
            }, { min: 0 });
            addNumber(maxes, "Max %", slot.link.max_pct, function (value) {
                slot.link.max_pct = value == null || value === "" ? null : clamp(Math.round(value), 0, 100);
            }, { min: 0, max: 100 });

            var order = document.createElement("div");
            order.className = "mc-inline";
            host.appendChild(order);
            order.appendChild(actionButton("Move up in stack", function () { nudgeOrder(-1); }));
            order.appendChild(actionButton("Move down in stack", function () { nudgeOrder(1); }));
        }

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
        remove.classList.add("ss-btn--danger");
        host.appendChild(remove);
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

    function freshLinkId() {
        var n = 1;
        var ids = linkIds();
        while (ids.indexOf("link" + n) !== -1) { n += 1; }
        return "link" + n;
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
                item.textContent = name;
                item.addEventListener("dragstart", function (ev) {
                    ev.dataTransfer.setData("text/plain", name);
                    ev.dataTransfer.effectAllowed = "copy";
                });
                item.addEventListener("dblclick", function () {
                    if (!state.model) {
                        toast("Create a container first.", "error");
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
        return api("/api/spore-studio/containers/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                container: state.model,
                previous_route: state.savedRoute
            })
        }).then(function (body) {
            state.model = body.container;
            state.savedRoute = body.container.route;
            state.savedSnap = JSON.stringify(body.container);
            state.dirty = false;
            state.history = [];
            state.future = [];
            updateDirty();
            toast("Container saved.", "success");
            return refreshLists();
        }).catch(function (err) {
            toast(err.message, "error");
        });
    }

    function openForm(title, fields, onOk) {
        var overlay = document.createElement("div");
        overlay.className = "ss-create-overlay";
        var card = document.createElement("div");
        card.className = "ss-create-overlay__card";
        var heading = document.createElement("h2");
        heading.textContent = title;
        card.appendChild(heading);
        var inputs = {};
        fields.forEach(function (spec) {
            var row = fieldRow(card, spec.label);
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
        });
        var actions = document.createElement("div");
        actions.className = "ss-row";
        actions.style.justifyContent = "flex-end";
        var cancel = document.createElement("button");
        cancel.className = "ss-btn";
        cancel.type = "button";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", function () { overlay.remove(); });
        var ok = document.createElement("button");
        ok.className = "ss-btn ss-btn--primary";
        ok.type = "button";
        ok.textContent = "Create";
        ok.addEventListener("click", function () {
            var values = {};
            Object.keys(inputs).forEach(function (key) { values[key] = inputs[key].value; });
            Promise.resolve(onOk(values)).then(function (close) {
                if (close !== false) { overlay.remove(); }
            });
        });
        actions.appendChild(cancel);
        actions.appendChild(ok);
        card.appendChild(actions);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        var first = card.querySelector("input, select");
        if (first) { first.focus(); }
    }

    function suggestRoute(name) {
        var text = (name || "").trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_-]/g, "").replace(/^[_-]+|[_-]+$/g, "");
        if (!text || text === "api" || text === "assets" || text === "static") { text = "container"; }
        return text.slice(0, 48);
    }

    function createContainer() {
        openForm("New container", [
            { id: "name", label: "Name", value: "" },
            { id: "route", label: "Route path", value: "" },
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
        ], function (values) {
            var parts = (values.preset || "1920x1080").split("x");
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
                return refreshLists().then(function () {
                    return loadContainer(body.container.route);
                });
            }).catch(function (err) {
                toast(err.message, "error");
                return false;
            });
        });
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

    function duplicateContainer() {
        if (!state.model) { return; }
        if (state.dirty) {
            toast("Save this container before duplicating it.", "error");
            return;
        }
        openForm("Duplicate container", [
            { id: "name", label: "Name", value: state.model.name + " copy" },
            { id: "route", label: "Route path", value: suggestRoute(state.model.route + "_copy") }
        ], function (values) {
            return api("/api/spore-studio/containers/duplicate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    route: state.savedRoute,
                    name: values.name,
                    new_route: values.route || suggestRoute(values.name)
                })
            }).then(function (body) {
                return refreshLists().then(function () { return loadContainer(body.container.route); });
            }).catch(function (err) {
                toast(err.message, "error");
                return false;
            });
        });
    }

    function deleteContainer() {
        if (!state.model) { return; }
        if (!confirm("Delete container /" + state.model.route + "?")) { return; }
        api("/api/spore-studio/containers/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ route: state.savedRoute || state.model.route })
        }).then(function () {
            state.model = null;
            state.savedRoute = null;
            state.dirty = false;
            state.selectedId = null;
            updateDirty();
            return refreshLists();
        }).then(function () {
            if (state.list.length) { return loadContainer(state.list[0].route); }
            renderAll();
        }).catch(function (err) { toast(err.message, "error"); });
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

    function setMode(mode) {
        if (mode === state.mode) { return; }
        if (state.mode === "containers" && state.dirty && mode !== "containers") {
            if (!confirm("Discard unsaved container changes?")) { return; }
            state.dirty = false;
            updateDirty();
        }
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
            }).catch(function (err) { toast(err.message, "error"); });
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
                toast("Enable the container before previewing its route.", "error");
                return;
            }
            var open = function () { window.open("/" + state.model.route, "_blank"); };
            if (!state.dirty) { open(); return; }
            if (!confirm("Save this container and open its route?")) { return; }
            saveCurrent().then(open);
        });
        $("mc-container-select").addEventListener("change", function (ev) {
            var route = ev.target.value;
            if (state.dirty && !confirm("Discard unsaved container changes?")) {
                ev.target.value = state.savedRoute || "";
                return;
            }
            loadContainer(route).catch(function (err) { toast(err.message, "error"); });
        });
        $("mc-template-search").addEventListener("input", renderPalette);
        var canvas = $("mc-canvas");
        canvas.addEventListener("dragover", function (ev) { ev.preventDefault(); });
        canvas.addEventListener("drop", function (ev) {
            ev.preventDefault();
            if (!state.model) {
                toast("Create a container first.", "error");
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
