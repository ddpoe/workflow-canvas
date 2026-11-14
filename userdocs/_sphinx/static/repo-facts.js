/* Fill in the repository's stars and forks beside its name above the article.
   Asked of GitHub once per browser tab; when GitHub does not answer, the two
   counts simply stay hidden. */
(function () {
    "use strict";

    var REPO = "ddpoe/workflow-canvas";
    var STORE_KEY = "wfc-repo-facts";

    function show(facts) {
        ["stars", "forks"].forEach(function (name) {
            if (typeof facts[name] !== "number") { return; }
            document.querySelectorAll('.wfc-source [data-fact="' + name + '"]').forEach(function (el) {
                el.querySelector("span").textContent = facts[name].toLocaleString();
                el.hidden = false;
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (!document.querySelector(".wfc-source")) { return; }
        try {
            var cached = JSON.parse(sessionStorage.getItem(STORE_KEY));
            if (cached) { show(cached); return; }
        } catch (error) {
            /* no usable cache: ask GitHub */
        }
        fetch("https://api.github.com/repos/" + REPO)
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) {
                if (!data) { return; }
                var facts = { stars: data.stargazers_count, forks: data.forks_count };
                try { sessionStorage.setItem(STORE_KEY, JSON.stringify(facts)); } catch (error) { /* fine */ }
                show(facts);
            })
            .catch(function () { /* offline or blocked: leave the counts hidden */ });
    });
})();
