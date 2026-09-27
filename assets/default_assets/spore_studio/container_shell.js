/*
 * Live container page. Measures locked templates and gives unused space
 * to flexible ones. The iframe viewport of a locked template stays at its
 * designated size so percentage layouts do not collapse.
 */
(function () {
    var dataEl = document.getElementById("mc-container-data");
    if (!dataEl || !window.MycelianContainerLayout) {
        return;
    }
    var payload;
    try {
        payload = JSON.parse(dataEl.textContent || "");
    } catch (err) {
        return;
    }
    var container = payload.container;
    var explicit = {};
    var origins = {};
    var lastKey = "";
    var timer = null;

    function slotMeta(id) {
        var slots = container.slots || [];
        for (var i = 0; i < slots.length; i++) {
            if (slots[i].id === id) {
                return slots[i];
            }
        }
        return null;
    }

    function measureFrame(iframe) {
        try {
            var doc = iframe.contentDocument;
            if (!doc || !doc.body || !doc.defaultView) {
                return null;
            }
            var viewW = iframe.clientWidth || iframe.offsetWidth;
            var viewH = iframe.clientHeight || iframe.offsetHeight;
            if (viewW < 2 || viewH < 2) {
                return null;
            }
            var minX = Infinity;
            var minY = Infinity;
            var maxX = -Infinity;
            var maxY = -Infinity;
            var found = false;
            var nodes = doc.body.querySelectorAll("*");
            for (var i = 0; i < nodes.length; i++) {
                var el = nodes[i];
                var cs = doc.defaultView.getComputedStyle(el);
                if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) {
                    continue;
                }
                var rect = el.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) {
                    continue;
                }
                if (rect.width >= viewW - 1 && rect.height >= viewH - 1) {
                    continue;
                }
                found = true;
                if (rect.left < minX) minX = rect.left;
                if (rect.top < minY) minY = rect.top;
                if (rect.right > maxX) maxX = rect.right;
                if (rect.bottom > maxY) maxY = rect.bottom;
            }
            if (!found) {
                return null;
            }
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

    function collectUsed() {
        var used = {};
        var frames = document.querySelectorAll("iframe[data-slot]");
        for (var i = 0; i < frames.length; i++) {
            var iframe = frames[i];
            var id = iframe.getAttribute("data-slot");
            var meta = slotMeta(id);
            if (!meta || !meta.link || meta.link.role !== "locked" || meta.fit === "scale") {
                continue;
            }
            if (explicit[id]) {
                used[id] = explicit[id];
                origins[id] = { x: 0, y: 0 };
                continue;
            }
            var measured = measureFrame(iframe);
            if (!measured) {
                delete origins[id];
                continue;
            }
            used[id] = { w: measured.w, h: measured.h };
            origins[id] = { x: measured.originX, y: measured.originY };
        }
        return used;
    }

    function applySlot(slot) {
        var el = document.querySelector('.mc-slot[data-slot="' + slot.id + '"]');
        if (!el) {
            return;
        }
        el.style.left = slot.x + "px";
        el.style.top = slot.y + "px";
        el.style.width = slot.w + "px";
        el.style.height = slot.h + "px";
        el.style.zIndex = String(slot.z);
        el.style.opacity = String((slot.opacity || 0) / 100);
        var clip = el.querySelector(".mc-clip");
        var frame = el.querySelector("iframe");
        if (!clip || !frame || !slot.content || !slot.iframe) {
            return;
        }
        clip.style.left = slot.content.x + "px";
        clip.style.top = slot.content.y + "px";
        clip.style.width = slot.content.w + "px";
        clip.style.height = slot.content.h + "px";
        var origin = origins[slot.id] || { x: 0, y: 0 };
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

    function relayout() {
        var layout = window.MycelianContainerLayout.solve(container, collectUsed());
        var key = JSON.stringify(layout.slots.map(function (slot) {
            var origin = origins[slot.id] || { x: 0, y: 0 };
            return [slot.id, slot.x, slot.y, slot.w, slot.h, slot.content, origin.x, origin.y];
        }));
        if (key === lastKey) {
            return;
        }
        lastKey = key;
        layout.slots.forEach(applySlot);
    }

    function schedule() {
        if (timer) {
            clearTimeout(timer);
        }
        timer = setTimeout(relayout, 120);
    }

    function watchFrame(iframe) {
        iframe.addEventListener("load", function () {
            schedule();
            try {
                var doc = iframe.contentDocument;
                if (!doc || !doc.documentElement) {
                    return;
                }
                var obs = new MutationObserver(schedule);
                obs.observe(doc.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true
                });
            } catch (err) {
                /* cross-origin or not ready */
            }
        });
    }

    document.querySelectorAll("iframe[data-slot]").forEach(watchFrame);
    window.addEventListener("message", function (ev) {
        var data = ev.data;
        if (!data || data.type !== "mycelian-content-size") {
            return;
        }
        var frames = document.querySelectorAll("iframe[data-slot]");
        for (var i = 0; i < frames.length; i++) {
            if (frames[i].contentWindow !== ev.source) {
                continue;
            }
            var width = Number(data.width);
            var height = Number(data.height);
            if (!isFinite(width) || !isFinite(height)) {
                return;
            }
            explicit[frames[i].getAttribute("data-slot")] = {
                w: width,
                h: height,
                explicit: true
            };
            schedule();
            return;
        }
    });
    setInterval(schedule, 500);
    schedule();
})();
