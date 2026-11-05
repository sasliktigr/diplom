(function () {
    var root = document.getElementById("navsearch");
    if (!root) return;

    var input = document.getElementById("navsearch-input");
    var list = document.getElementById("navsearch-list");
    var kbd = document.getElementById("navsearch-kbd");

    var items = [];
    var active = -1;
    var timer = null;
    var seq = 0;

    var TYPE_LABELS = {
        page: "Раздел",
        student: "Студент",
        group: "Группа",
        subject: "Предмет",
        teacher: "Преподаватель"
    };

    function escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function highlight(text, query) {
        var safe = escapeHtml(text);
        var tokens = query.trim().split(/\s+/).filter(Boolean);
        tokens.forEach(function (t) {
            var re = new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
            safe = safe.replace(re, "<mark>$1</mark>");
        });
        return safe;
    }

    function open() {
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        root.classList.add("is-open");
    }

    function close() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        root.classList.remove("is-open");
        active = -1;
    }

    function setActive(i) {
        var nodes = list.querySelectorAll(".navsearch-item");
        if (!nodes.length) return;
        active = (i + nodes.length) % nodes.length;
        nodes.forEach(function (n, idx) {
            n.classList.toggle("active", idx === active);
        });
        nodes[active].scrollIntoView({ block: "nearest" });
    }

    function render(query) {
        if (!items.length) {
            list.innerHTML = '<div class="navsearch-empty">Ничего не найдено</div>';
            return;
        }

        list.innerHTML = items.map(function (it, i) {
            return (
                '<a class="navsearch-item" role="option" data-i="' + i + '" href="' + escapeHtml(it.url) + '">' +
                    '<span class="navsearch-tag t-' + it.type + '">' + (TYPE_LABELS[it.type] || "") + "</span>" +
                    '<span class="navsearch-text">' +
                        "<strong>" + highlight(it.title, query) + "</strong>" +
                        "<small>" + escapeHtml(it.desc) + "</small>" +
                    "</span>" +
                    '<span class="navsearch-go">↵</span>' +
                "</a>"
            );
        }).join("");

        setActive(0);
    }

    function load(query) {
        var mine = ++seq;
        fetch("/api/nav-search?q=" + encodeURIComponent(query), {
            headers: { "Accept": "application/json" },
            credentials: "same-origin"
        })
            .then(function (r) {
                if (!r.ok) throw new Error("bad status");
                return r.json();
            })
            .then(function (data) {
                if (mine !== seq) return; // устаревший ответ
                items = data;
                render(query);
                open();
            })
            .catch(function () {
                if (mine !== seq) return;
                list.innerHTML = '<div class="navsearch-empty">Не удалось выполнить поиск</div>';
                open();
            });
    }

    function go(i) {
        if (i < 0 || !items[i]) return;
        window.location.href = items[i].url;
    }

    input.addEventListener("input", function () {
        clearTimeout(timer);
        var q = input.value;
        timer = setTimeout(function () { load(q); }, 120);
    });

    input.addEventListener("focus", function () {
        kbd.style.display = "none";
        load(input.value);
    });

    input.addEventListener("blur", function () {
        if (!input.value) kbd.style.display = "";
    });

    input.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
        else if (e.key === "Enter") { e.preventDefault(); go(active); }
        else if (e.key === "Escape") { close(); input.blur(); }
    });

    list.addEventListener("mousemove", function (e) {
        var node = e.target.closest(".navsearch-item");
        if (node) setActive(parseInt(node.dataset.i, 10));
    });

    document.addEventListener("click", function (e) {
        if (!root.contains(e.target)) close();
    });

    // Горячие клавиши: «/» или Ctrl/Cmd + K
    document.addEventListener("keydown", function (e) {
        var tag = (e.target.tagName || "").toLowerCase();
        var typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;

        if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            input.focus();
            input.select();
        } else if (e.key === "/" && !typing) {
            e.preventDefault();
            input.focus();
        }
    });
})();
