/* Left sidebar: each top-level group (Overview, Tutorials, ...) is a toggle.
   Overview, Installation and Project start open, as does the group holding the page
   being read; the reader's own choices are kept while the tab stays open. */
(function () {
    "use strict";

    var OPEN_BY_DEFAULT = ["Overview", "Installation", "Project"];
    var STORE_KEY = "wfc-sidebar-groups";

    function readStore() {
        try {
            return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {};
        } catch (error) {
            return {};
        }
    }

    function writeStore(state) {
        try {
            sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
        } catch (error) {
            /* storage unavailable: the groups still toggle, unremembered */
        }
    }

    /* The page's place in the guide, above its title: the group, any parent
       pages (as links), then the page itself, read from the sidebar. */
    function addBreadcrumbs() {
        var title = document.querySelector(".bd-article h1");
        var links = document.querySelectorAll(".bd-sidebar-primary nav.bd-links a.current");
        if (!title || !links.length) { return; }
        var current = links[links.length - 1];
        var trail = [];
        var item = current.closest("li");
        var topList = null;
        while (item) {
            var link = item.querySelector(":scope > a");
            if (link) { trail.unshift(link); }
            topList = item.parentElement;
            item = topList.closest("li");
        }
        var caption = topList && topList.previousElementSibling;
        var nav = document.createElement("nav");
        nav.className = "wfc-crumbs";
        nav.setAttribute("aria-label", "Breadcrumb");
        var parts = [];
        if (caption && caption.matches("p.caption")) {
            var group = document.createElement("span");
            group.textContent = caption.textContent.trim();
            parts.push(group);
        }
        trail.forEach(function (link, index) {
            var last = index === trail.length - 1;
            var part = document.createElement(last ? "span" : "a");
            part.textContent = link.textContent.trim();
            if (last) {
                part.className = "wfc-crumbs-here";
            } else {
                part.href = link.href;
            }
            parts.push(part);
        });
        parts.forEach(function (part, index) {
            if (index > 0) {
                var arrow = document.createElement("span");
                arrow.className = "wfc-crumbs-sep";
                arrow.textContent = "›";
                arrow.setAttribute("aria-hidden", "true");
                nav.appendChild(arrow);
            }
            nav.appendChild(part);
        });
        title.parentElement.insertBefore(nav, title);
    }

    document.addEventListener("DOMContentLoaded", function () {
        addBreadcrumbs();
        var state = readStore();
        document.querySelectorAll(".bd-sidebar-primary p.caption").forEach(function (caption) {
            var list = caption.nextElementSibling;
            if (!list || list.tagName !== "UL") { return; }
            var name = caption.textContent.trim();
            var holdsCurrent = list.querySelector(".current, .active") !== null;

            var open;
            if (holdsCurrent) {
                open = true;
            } else if (Object.prototype.hasOwnProperty.call(state, name)) {
                open = state[name];
            } else {
                open = OPEN_BY_DEFAULT.indexOf(name) !== -1;
            }

            caption.classList.add("wfc-group-toggle");
            caption.setAttribute("role", "button");
            caption.setAttribute("tabindex", "0");

            function apply() {
                list.hidden = !open;
                caption.classList.toggle("wfc-group-open", open);
                caption.setAttribute("aria-expanded", String(open));
            }

            function toggle() {
                open = !open;
                state[name] = open;
                writeStore(state);
                apply();
            }

            caption.addEventListener("click", toggle);
            caption.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggle();
                }
            });
            apply();
        });
    });
})();
