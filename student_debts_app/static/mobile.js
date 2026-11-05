(function () {
    "use strict";

    /* ---------- Выезжающее меню ---------- */
    var body = document.body;
    var toggle = document.getElementById("nav-toggle");
    var closeBtn = document.getElementById("nav-close");
    var overlay = document.getElementById("nav-overlay");
    var sidebar = document.getElementById("sidebar");

    function setOpen(open) {
        body.classList.toggle("nav-open", open);
        if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (overlay) overlay.hidden = !open;
    }

    if (toggle && sidebar) {
        toggle.addEventListener("click", function () {
            setOpen(!body.classList.contains("nav-open"));
        });
        if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
        if (overlay) overlay.addEventListener("click", function () { setOpen(false); });

        sidebar.addEventListener("click", function (e) {
            if (e.target.closest("a")) setOpen(false);
        });

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") setOpen(false);
        });

        window.addEventListener("resize", function () {
            if (window.innerWidth > 1100) setOpen(false);
        });

        // возврат на страницу кнопкой «назад» не должен оставлять меню открытым
        window.addEventListener("pageshow", function () { setOpen(false); });
    }

    /* ---------- Короткий placeholder поиска на узком экране ---------- */
    var search = document.getElementById("navsearch-input");
    if (search && window.matchMedia("(max-width:720px)").matches) {
        search.placeholder = "Поиск по сайту…";
    }

    /* ---------- Таблицы → карточки на телефоне ----------
       Подписываем каждую ячейку названием её колонки (data-label),
       чтобы CSS мог показать строку таблицы как карточку. */
    document.querySelectorAll(".table-wrap table").forEach(function (table) {
        var headCells = table.querySelectorAll("thead th");
        if (!headCells.length) return;

        var labels = Array.prototype.map.call(headCells, function (th) {
            return th.textContent.replace(/\s+/g, " ").trim();
        });

        table.querySelectorAll("tbody tr").forEach(function (row) {
            var col = 0;
            Array.prototype.forEach.call(row.children, function (cell) {
                if (cell.tagName !== "TD") return;
                var span = parseInt(cell.getAttribute("colspan") || "1", 10);

                if (span === 1 && labels[col]) {
                    cell.setAttribute("data-label", labels[col]);

                    // оборачиваем содержимое, чтобы подпись и значение лежали в двух колонках
                    var wrap = document.createElement("div");
                    wrap.className = "td-val";
                    while (cell.firstChild) wrap.appendChild(cell.firstChild);
                    cell.appendChild(wrap);

                    if (wrap.querySelector(".actions")) cell.classList.add("td-actions");
                }
                col += span;
            });
        });
    });
})();
