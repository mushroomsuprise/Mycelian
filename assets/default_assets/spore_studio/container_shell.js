/*
 * Live container page. Measures "fits its content" templates with
 * container_measure.js and gives the space they don't use to "fills leftover
 * space" templates in the same shared space. A fits-content iframe keeps its
 * drawn size (the box around it is clipped) so measuring can't feed back into
 * its own layout; a fills-leftover iframe gets its real new size, so the
 * template's resolution changes and its picture is cropped or reflows.
 * Changes apply instantly, without animation.
 */
(function () {
    var dataEl = document.getElementById("mc-container-data");
    var Layout = window.MycelianContainerLayout;
    var Measure = window.MycelianContainerMeasure;
    if (!dataEl || !Layout || !Measure) {
        return;
    }
    var payload;
    try {
        payload = JSON.parse(dataEl.textContent || "");
    } catch (err) {
        return;
    }
    var container = payload.container;
    var explicit = Measure.createExplicitSizes();
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

    function frames() {
        return Array.prototype.slice.call(document.querySelectorAll("iframe[data-slot]"));
    }

    function collectUsed() {
        var used = {};
        frames().forEach(function (iframe) {
            var id = iframe.getAttribute("data-slot");
            var meta = slotMeta(id);
            if (!meta || !meta.link || meta.link.role !== "locked" || meta.fit === "scale") {
                return;
            }
            var measured = explicit.get(id) || Measure.measure(iframe);
            if (!measured) {
                // Not readable yet: the box keeps its drawn size.
                delete origins[id];
                return;
            }
            if (measured.empty) {
                used[id] = { empty: true };
                delete origins[id];
                return;
            }
            used[id] = { w: measured.w, h: measured.h };
            origins[id] = { x: measured.x, y: measured.y };
        });
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
            // Only the shared-space axis is trimmed to the content.
            frame.style.left = (slot.axis === "horizontal" ? -origin.x : 0) + "px";
            frame.style.top = (slot.axis === "vertical" ? -origin.y : 0) + "px";
        } else {
            frame.style.left = "0px";
            frame.style.top = "0px";
        }
    }

    function relayout() {
        var layout = Layout.solve(container, collectUsed());
        var key = JSON.stringify(layout.slots.map(function (slot) {
            var origin = origins[slot.id] || { x: 0, y: 0 };
            return [slot.id, slot.x, slot.y, slot.w, slot.h, slot.content, slot.iframe, origin.x, origin.y];
        }));
        if (key === lastKey) {
            return;
        }
        lastKey = key;
        layout.slots.forEach(applySlot);
    }

    function schedule() {
        // Coalesce bursts (one relayout per frame); never hold back behind constant animation.
        if (timer) {
            return;
        }
        timer = setTimeout(function () {
            timer = null;
            relayout();
        }, 0);
    }

    frames().forEach(function (iframe) {
        Measure.watch(iframe, schedule);
    });
    window.addEventListener("message", function (ev) {
        if (explicit.handle(ev, frames())) {
            schedule();
        }
    });
    schedule();
})();
