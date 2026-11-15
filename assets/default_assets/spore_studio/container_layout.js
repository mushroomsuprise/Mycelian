/*
 * Container size-link solver.
 * Mirrors modules/spore_studio/containers.py solve_layout.
 * Input containers must already be normalized.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.MycelianContainerLayout = api;
    }
})(typeof window !== "undefined" ? window : globalThis, function () {
    function pyRound(n) {
        var floor = Math.floor(n);
        var frac = n - floor;
        if (frac > 0.5) {
            return floor + 1;
        }
        if (frac < 0.5) {
            return floor;
        }
        return floor % 2 === 0 ? floor : floor + 1;
    }

    function effMin(link, span) {
        /* span * pct / 100 in this order, like containers.py: 55% of 1390 is 764.5, not 764.5000000000001. */
        var pct = pyRound(span * (link.min_pct || 0) / 100);
        return Math.max(link.min_px || 0, pct);
    }

    function effMax(link, span) {
        var caps = [];
        if (link.max_px !== null && link.max_px !== undefined) {
            caps.push(link.max_px);
        }
        if (link.max_pct !== null && link.max_pct !== undefined) {
            caps.push(pyRound(span * link.max_pct / 100));
        }
        if (!caps.length) {
            return null;
        }
        return Math.min.apply(null, caps);
    }

    function axisPads(slot, axis) {
        var pad = slot.padding;
        if (axis === "vertical") {
            return [pad.t, pad.b];
        }
        return [pad.l, pad.r];
    }

    function designated(slot, axis) {
        return axis === "vertical" ? slot.h : slot.w;
    }

    function measuredContent(slot, axis, used) {
        if (slot.fit === "scale") {
            return null;
        }
        if (!used) {
            return null;
        }
        var entry = used[slot.id];
        if (!entry || typeof entry !== "object") {
            return null;
        }
        if (entry.empty) {
            return 0;
        }
        var key = axis === "vertical" ? "h" : "w";
        if (entry[key] === undefined || entry[key] === null) {
            return null;
        }
        var number = Number(entry[key]);
        if (Number.isNaN(number)) {
            return null;
        }
        return number;
    }

    function lockedOuter(slot, axis, span, used, warnings) {
        var link = slot.link;
        var size = designated(slot, axis);
        var pads = axisPads(slot, axis);
        var before = pads[0];
        var after = pads[1];
        var contentCap = Math.max(0, size - before - after);
        var measured = measuredContent(slot, axis, used);
        var outer;
        if (measured === null || (measured <= 0 && link.when_empty === "keep")) {
            outer = pyRound(contentCap) + before + after;
        } else if (measured <= 0) {
            // Nothing showing: the box closes completely (padding too), down to its minimum.
            outer = 0;
        } else {
            outer = pyRound(Math.min(contentCap, measured)) + before + after;
        }
        var lo = effMin(link, span);
        var hi = size;
        var cap = effMax(link, span);
        if (cap !== null) {
            hi = Math.min(hi, cap);
        }
        if (lo > hi) {
            warnings.push({
                code: "min_above_designated",
                link: link.id,
                slots: [slot.id],
                message: "In shared space \u201c" + link.id + "\u201d, the minimum of \u201c" + slot.template +
                    "\u201d is bigger than the size it was drawn at, so it stays at its drawn size."
            });
            return hi;
        }
        return Math.max(lo, Math.min(hi, outer));
    }

    function splitInt(total, weights) {
        var out = {};
        if (!weights.length) {
            return out;
        }
        if (total <= 0) {
            weights.forEach(function (item) {
                out[item[0]] = 0;
            });
            return out;
        }
        var weightSum = 0;
        weights.forEach(function (item) {
            weightSum += Math.max(1, item[1]);
        });
        var used = 0;
        var remainders = [];
        weights.forEach(function (item) {
            var exact = (total * Math.max(1, item[1])) / weightSum;
            var base = Math.trunc(exact);
            out[item[0]] = base;
            remainders.push([exact - base, item[0]]);
            used += base;
        });
        var leftover = total - used;
        remainders.sort(function (a, b) {
            if (a[0] !== b[0]) {
                return b[0] - a[0];
            }
            if (a[1] < b[1]) {
                return -1;
            }
            if (a[1] > b[1]) {
                return 1;
            }
            return 0;
        });
        for (var i = 0; i < leftover; i++) {
            out[remainders[i % remainders.length][1]] += 1;
        }
        return out;
    }

    function distributeFlexible(remaining, items) {
        if (!items.length) {
            return [{}, false];
        }
        var mins = {};
        var minSum = 0;
        items.forEach(function (item) {
            mins[item.id] = Math.max(0, item.min);
            minSum += mins[item.id];
        });
        if (minSum > remaining) {
            return [mins, true];
        }
        var maxes = {};
        var weights = {};
        items.forEach(function (item) {
            maxes[item.id] = item.max === null || item.max === undefined ? null : item.max;
            weights[item.id] = Math.max(1, item.weight);
        });
        var assigned = {};
        var active = items.map(function (item) {
            return item.id;
        });
        var budget = remaining;
        for (var guard = 0; guard < 24; guard++) {
            if (!active.length) {
                break;
            }
            var shares = splitInt(budget, active.map(function (id) {
                return [id, weights[id]];
            }));
            // Freeze slots under their minimum first; only when none are, freeze the ones
            // over their maximum (same order as containers.py).
            var frozen = active.filter(function (id) {
                return shares[id] < mins[id];
            });
            if (frozen.length) {
                frozen.forEach(function (id) {
                    assigned[id] = mins[id];
                });
            } else {
                frozen = active.filter(function (id) {
                    return maxes[id] !== null && shares[id] > maxes[id];
                });
                frozen.forEach(function (id) {
                    assigned[id] = maxes[id];
                });
            }
            var still = active.filter(function (id) {
                return frozen.indexOf(id) < 0;
            });
            if (!frozen.length) {
                still.forEach(function (id) {
                    assigned[id] = shares[id];
                });
                break;
            }
            frozen.forEach(function (id) {
                budget -= assigned[id];
            });
            active = still;
            if (budget < 0) {
                return [assigned, true];
            }
        }
        return [assigned, false];
    }

    function contentBox(slot, outerW, outerH) {
        var pad = slot.padding;
        return {
            x: pad.l,
            y: pad.t,
            w: Math.max(0, outerW - pad.l - pad.r),
            h: Math.max(0, outerH - pad.t - pad.b)
        };
    }

    function iframeBox(slot, axis, content) {
        var pad = slot.padding;
        var role = slot.link && slot.link.role;
        var fit = slot.fit || "native";
        if (fit === "scale") {
            return {
                w: Math.max(1, slot.design_width || slot.w),
                h: Math.max(1, slot.design_height || slot.h),
                left: pad.l,
                top: pad.t
            };
        }
        var iframeW = content.w;
        var iframeH = content.h;
        if (role === "locked" && axis && fit === "native") {
            if (axis === "vertical") {
                iframeH = Math.max(0, slot.h - pad.t - pad.b);
            } else {
                iframeW = Math.max(0, slot.w - pad.l - pad.r);
            }
        }
        return { w: iframeW, h: iframeH, left: content.x, top: content.y };
    }

    function groupExtent(members, axis) {
        var start = Infinity;
        var end = -Infinity;
        members.forEach(function (slot) {
            var a = axis === "vertical" ? slot.y : slot.x;
            var b = a + (axis === "vertical" ? slot.h : slot.w);
            if (a < start) start = a;
            if (b > end) end = b;
        });
        return [start, Math.max(0, end - start)];
    }

    function warnGroupAlignment(linkId, members, axis, warnings) {
        for (var i = 0; i + 1 < members.length; i++) {
            var prev = members[i];
            var next = members[i + 1];
            var a0 = axis === "vertical" ? prev.x : prev.y;
            var a1 = a0 + (axis === "vertical" ? prev.w : prev.h);
            var b0 = axis === "vertical" ? next.x : next.y;
            var b1 = b0 + (axis === "vertical" ? next.w : next.h);
            if (Math.min(a1, b1) <= Math.max(a0, b0)) {
                warnings.push({
                    code: "not_aligned",
                    link: linkId,
                    slots: members.map(function (slot) { return slot.id; }),
                    message: "The boxes in shared space \u201c" + linkId + "\u201d aren't in the same " +
                        (axis === "vertical" ? "column" : "row") + ". Line them up so they stack."
                });
                return;
            }
        }
    }

    function solve(container, used) {
        var warnings = [];
        var spacing = container.spacing || 0;
        var solved = {};
        var groups = {};
        (container.slots || []).forEach(function (slot) {
            if (!slot.link) {
                solved[slot.id] = { x: slot.x, y: slot.y, w: slot.w, h: slot.h };
                return;
            }
            if (!groups[slot.link.id]) {
                groups[slot.link.id] = [];
            }
            groups[slot.link.id].push(slot);
        });
        Object.keys(groups).forEach(function (linkId) {
            var members = groups[linkId].slice();
            var axis = members[0].link.axis;
            members.sort(function (a, b) {
                var av = axis === "vertical" ? a.y : a.x;
                var bv = axis === "vertical" ? b.y : b.x;
                if (av !== bv) {
                    return av - bv;
                }
                if (a.id < b.id) {
                    return -1;
                }
                if (a.id > b.id) {
                    return 1;
                }
                return 0;
            });
            // A shared space covers only the area its boxes were drawn in.
            var extent = groupExtent(members, axis);
            var start = extent[0];
            var span = extent[1];
            warnGroupAlignment(linkId, members, axis, warnings);
            var lockedSizes = {};
            var flexible = [];
            members.forEach(function (slot) {
                if (slot.link.role === "locked" && slot.fit === "scale") {
                    warnings.push({
                        code: "fit_scale_in_group",
                        link: linkId,
                        slots: [slot.id],
                        message: "\u201c" + slot.template + "\u201d is set to scale from its design size, so it " +
                            "can't fit its content. It keeps the size it was drawn at."
                    });
                }
                if (slot.link.role === "locked") {
                    lockedSizes[slot.id] = lockedOuter(slot, axis, span, used, warnings);
                } else {
                    flexible.push({
                        id: slot.id,
                        weight: designated(slot, axis),
                        min: effMin(slot.link, span),
                        max: effMax(slot.link, span)
                    });
                }
            });
            // Closed (0px) boxes take no gap either.
            var lockedSum = 0;
            var openCount = flexible.length;
            Object.keys(lockedSizes).forEach(function (id) {
                lockedSum += lockedSizes[id];
                if (lockedSizes[id] > 0) {
                    openCount += 1;
                }
            });
            var gapTotal = spacing * Math.max(0, openCount - 1);
            var remaining = span - gapTotal - lockedSum;
            var distributed = distributeFlexible(remaining, flexible);
            var flexSizes = distributed[0];
            var overflow = distributed[1];
            if (overflow || remaining < 0) {
                warnings.push({
                    code: "min_overflow",
                    link: linkId,
                    slots: members.map(function (slot) { return slot.id; }),
                    message: "The boxes in shared space \u201c" + linkId + "\u201d need more room than the space " +
                        "has (their minimum sizes add up to more), so they spill past it. Lower a minimum or " +
                        "make the space bigger."
                });
            }
            var cursor = start;
            var placedAny = false;
            members.forEach(function (slot) {
                var size = lockedSizes[slot.id];
                if (size === undefined) {
                    size = flexSizes[slot.id] || 0;
                }
                if (size > 0 && placedAny) {
                    cursor += spacing;
                }
                if (axis === "vertical") {
                    solved[slot.id] = { x: slot.x, y: cursor, w: slot.w, h: size };
                } else {
                    solved[slot.id] = { x: cursor, y: slot.y, w: size, h: slot.h };
                }
                cursor += size;
                if (size > 0) {
                    placedAny = true;
                }
            });
        });
        var slots = (container.slots || []).map(function (slot) {
            var rect = solved[slot.id];
            var content = contentBox(slot, rect.w, rect.h);
            var axis = slot.link ? slot.link.axis : null;
            return {
                id: slot.id,
                template: slot.template,
                visible: slot.visible,
                x: rect.x,
                y: rect.y,
                w: rect.w,
                h: rect.h,
                z: slot.z,
                opacity: slot.opacity,
                pointer_events: slot.pointer_events,
                fit: slot.fit,
                design_width: slot.design_width,
                design_height: slot.design_height,
                audio: slot.audio,
                padding: slot.padding,
                role: slot.link ? slot.link.role : null,
                axis: axis,
                link_id: slot.link ? slot.link.id : null,
                designated_w: slot.w,
                designated_h: slot.h,
                content: content,
                iframe: iframeBox(slot, axis, content)
            };
        });
        var seen = {};
        var unique = [];
        warnings.forEach(function (warning) {
            var key = warning.code + "\0" + (warning.link || "");
            if (seen[key]) {
                return;
            }
            seen[key] = true;
            unique.push(warning);
        });
        return {
            width: container.width,
            height: container.height,
            spacing: spacing,
            background: container.background,
            warnings: unique,
            slots: slots
        };
    }

    return { solve: solve, groupExtent: groupExtent };
});
