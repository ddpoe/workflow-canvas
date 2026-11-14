/* Screenshots in the guide: click one to see it full size, and step through a
   gallery (a block marked {div} wfc-gallery holding several figures) with arrows. */
(function () {
    "use strict";

    function openLightbox(img) {
        var overlay = document.createElement("div");
        overlay.className = "wfc-lightbox";
        var big = document.createElement("img");
        big.src = img.currentSrc || img.src;
        big.alt = img.alt;
        overlay.appendChild(big);
        function close() {
            overlay.remove();
            document.removeEventListener("keydown", onKey);
        }
        function onKey(event) {
            if (event.key === "Escape") { close(); }
        }
        overlay.addEventListener("click", close);
        document.addEventListener("keydown", onKey);
        document.body.appendChild(overlay);
    }

    function setUpGallery(gallery) {
        var slides = Array.prototype.slice.call(gallery.querySelectorAll("figure"));
        if (slides.length < 2) { return; }
        var current = 0;

        var bar = document.createElement("div");
        bar.className = "wfc-gallery-bar";
        var prev = document.createElement("button");
        prev.type = "button";
        prev.textContent = "←";
        prev.setAttribute("aria-label", "Previous screenshot");
        var next = document.createElement("button");
        next.type = "button";
        next.textContent = "→";
        next.setAttribute("aria-label", "Next screenshot");
        var count = document.createElement("span");
        count.className = "wfc-gallery-count";
        bar.appendChild(prev);
        bar.appendChild(count);
        bar.appendChild(next);
        gallery.appendChild(bar);

        function show(index) {
            current = (index + slides.length) % slides.length;
            slides.forEach(function (slide, i) {
                slide.hidden = i !== current;
            });
            count.textContent = (current + 1) + " of " + slides.length;
        }
        prev.addEventListener("click", function () { show(current - 1); });
        next.addEventListener("click", function () { show(current + 1); });
        gallery.classList.add("wfc-gallery-ready");
        show(0);
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('.bd-article figure img, .bd-article p > img[src*="_images/"]').forEach(function (img) {
            img.addEventListener("click", function (event) {
                event.preventDefault();
                openLightbox(img);
            });
        });
        document.querySelectorAll(".bd-article .wfc-gallery").forEach(setUpGallery);
    });
})();
