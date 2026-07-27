(function () {
    "use strict";

    document.querySelectorAll(".flash-close").forEach(function (button) {
        button.addEventListener("click", function () {
            button.closest(".flash").remove();
        });
    });

    document.querySelectorAll(".flash[data-auto-dismiss]").forEach(function (flash) {
        var delay = Number(flash.dataset.autoDismiss) || 4500;
        window.setTimeout(function () {
            flash.classList.add("flash-leaving");
            window.setTimeout(function () {
                flash.remove();
            }, 180);
        }, delay);
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

    var reservationDetailsDialog = document.getElementById(
        "reservation-details-dialog"
    );
    if (reservationDetailsDialog) {
        var reservationDetailFields = {};
        reservationDetailsDialog
            .querySelectorAll("[data-reservation-detail]")
            .forEach(function (detail) {
                reservationDetailFields[detail.dataset.reservationDetail] = detail;
            });

        document
            .querySelectorAll("[data-reservation-details]")
            .forEach(function (button) {
                button.addEventListener("click", function () {
                    var values = {
                        room: button.dataset.reservationRoom,
                        date: button.dataset.reservationDate,
                        time: (
                            button.dataset.reservationStart +
                            "–" +
                            button.dataset.reservationEnd
                        ),
                        person: button.dataset.reservationPerson,
                        party: button.dataset.reservationParty,
                        case: button.dataset.reservationCase
                    };
                    Object.keys(reservationDetailFields).forEach(function (key) {
                        var detail = reservationDetailFields[key];
                        detail.textContent = values[key] || "—";
                    });
                    reservationDetailsDialog.showModal();
                });
            });
    }

    var startSelect = document.getElementById("start-time");
    var endSelect = document.getElementById("end-time");
    if (startSelect && endSelect) {
        var reservationForm = startSelect.closest("form");
        var dateInput = reservationForm.querySelector('input[name="reserve_date"]');
        var submitButton = document.getElementById("reserve-submit");
        var timeHelp = document.getElementById("time-help");
        var today = reservationForm.dataset.today;
        var nowMinutes = Number(reservationForm.dataset.nowMinutes);

        function toMinutes(value) {
            var parts = value.split(":");
            return Number(parts[0]) * 60 + Number(parts[1]);
        }

        function firstEnabledOption(select) {
            return Array.from(select.options).find(function (option) {
                return !option.disabled;
            });
        }

        function refreshEndTimes(preferredDuration) {
            var startMinutes = toMinutes(startSelect.value);
            Array.from(endSelect.options).forEach(function (option) {
                option.disabled = toMinutes(option.value) <= startMinutes;
            });

            var desiredEnd = startMinutes + Math.max(30, preferredDuration || 30);
            var matching = Array.from(endSelect.options).find(function (option) {
                return !option.disabled && toMinutes(option.value) === desiredEnd;
            });
            if (!matching || endSelect.selectedOptions[0].disabled) {
                matching = firstEnabledOption(endSelect);
            }
            if (matching) {
                endSelect.value = matching.value;
            }
        }

        function refreshStartTimes() {
            var selectedDate = dateInput.value;
            var currentDuration = Math.max(
                30,
                toMinutes(endSelect.value) - toMinutes(startSelect.value)
            );
            Array.from(startSelect.options).forEach(function (option) {
                var optionMinutes = toMinutes(option.value);
                option.disabled = (
                    selectedDate < today ||
                    (selectedDate === today && optionMinutes <= nowMinutes)
                );
            });

            if (startSelect.selectedOptions[0].disabled) {
                var availableStart = firstEnabledOption(startSelect);
                if (availableStart) {
                    startSelect.value = availableStart.value;
                }
            }

            var hasAvailableStart = Boolean(firstEnabledOption(startSelect));
            submitButton.disabled =
                !selectedDate || !hasAvailableStart || selectedDate < today;
            if (!selectedDate) {
                timeHelp.textContent = "请选择预约日期。";
            } else if (!hasAvailableStart && selectedDate === today) {
                timeHelp.textContent = "今天已没有可预约时段，请选择其他日期。";
            } else if (selectedDate < today) {
                timeHelp.textContent = "不能预约过去的日期，请重新选择。";
            } else {
                timeHelp.textContent = "";
            }
            refreshEndTimes(currentDuration);
        }

        startSelect.addEventListener("change", function () {
            refreshEndTimes(30);
        });
        dateInput.addEventListener("change", refreshStartTimes);
        refreshStartTimes();
    }

    var calendarFocus = document.querySelector("[data-calendar-focus]");
    if (calendarFocus) {
        window.requestAnimationFrame(function () {
            var scrollArea = calendarFocus.closest(".calendar-scroll");
            if (scrollArea && scrollArea.scrollHeight > scrollArea.clientHeight + 8) {
                scrollArea.scrollTop = Math.max(
                    0,
                    calendarFocus.offsetTop - scrollArea.clientHeight / 3
                );
            } else if (window.innerWidth <= 900) {
                calendarFocus.scrollIntoView({ block: "center" });
            }
        });
    }
}());
