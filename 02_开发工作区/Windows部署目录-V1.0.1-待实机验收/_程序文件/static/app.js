(function () {
    "use strict";

    document.querySelectorAll(".flash-close").forEach(function (button) {
        button.addEventListener("click", function () {
            button.closest(".flash").remove();
        });
    });

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll("[data-open-dialog]").forEach(function (button) {
        button.addEventListener("click", function () {
            var dialog = document.getElementById(button.dataset.openDialog);
            if (dialog) {
                dialog.showModal();
            }
        });
    });

    document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
        button.addEventListener("click", function () {
            var dialog = button.closest("dialog");
            if (dialog) {
                dialog.close();
            }
        });
    });

    document.querySelectorAll("dialog.edit-dialog").forEach(function (dialog) {
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) {
                dialog.close();
            }
        });
    });

    var startSelect = document.getElementById("start-time");
    var endSelect = document.getElementById("end-time");
    if (startSelect && endSelect) {
        startSelect.addEventListener("change", function () {
            var parts = startSelect.value.split(":");
            var minutes = Number(parts[0]) * 60 + Number(parts[1]) + 30;
            var value = String(Math.floor(minutes / 60)).padStart(2, "0") + ":" + String(minutes % 60).padStart(2, "0");
            var matching = Array.from(endSelect.options).find(function (option) {
                return option.value === value;
            });
            if (matching) {
                endSelect.value = value;
            }
        });
    }
}());
