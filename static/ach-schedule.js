/* Monthly ACH schedule: debit day, start, stop, and payment count stay in sync.
   Mirrors resolve_ach_schedule / ach_count_through / ach_stop_for_count in app.py. */
(function () {
  function clampDay(value) {
    var n = parseInt(value, 10);
    if (!n || n < 1) n = 1;
    if (n > 28) n = 28;
    return n;
  }

  function parseISO(text) {
    if (!text || !/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
    var parts = text.split("-");
    var year = +parts[0];
    var month = +parts[1];
    var day = +parts[2];
    var parsed = new Date(Date.UTC(year, month - 1, day));
    if (
      parsed.getUTCFullYear() !== year ||
      parsed.getUTCMonth() !== month - 1 ||
      parsed.getUTCDate() !== day
    ) {
      return null;
    }
    return parsed;
  }

  function iso(parsed) {
    var month = parsed.getUTCMonth() + 1;
    var day = parsed.getUTCDate();
    return (
      parsed.getUTCFullYear() +
      "-" +
      (month < 10 ? "0" : "") +
      month +
      "-" +
      (day < 10 ? "0" : "") +
      day
    );
  }

  function shiftMonth(year, month, delta) {
    var idx = year * 12 + (month - 1) + delta;
    var y = Math.floor(idx / 12);
    var m = idx - y * 12;
    return { y: y, m: m + 1 };
  }

  function dateOn(year, month, day) {
    return new Date(Date.UTC(year, month - 1, day));
  }

  function firstDebit(start, day) {
    var candidate = dateOn(start.getUTCFullYear(), start.getUTCMonth() + 1, day);
    if (candidate.getTime() < start.getTime()) {
      var next = shiftMonth(start.getUTCFullYear(), start.getUTCMonth() + 1, 1);
      candidate = dateOn(next.y, next.m, day);
    }
    return candidate;
  }

  function nthDebit(start, day, count) {
    if (count < 1) return null;
    var first = firstDebit(start, day);
    var shifted = shiftMonth(first.getUTCFullYear(), first.getUTCMonth() + 1, count - 1);
    return dateOn(shifted.y, shifted.m, day);
  }

  function countThrough(start, end, day) {
    var first = firstDebit(start, day);
    if (end.getTime() < first.getTime()) return 0;
    var last;
    if (end.getUTCDate() >= day) {
      last = dateOn(end.getUTCFullYear(), end.getUTCMonth() + 1, day);
    } else {
      var prev = shiftMonth(end.getUTCFullYear(), end.getUTCMonth() + 1, -1);
      last = dateOn(prev.y, prev.m, day);
    }
    if (last.getTime() < first.getTime()) return 0;
    return (
      (last.getUTCFullYear() - first.getUTCFullYear()) * 12 +
      (last.getUTCMonth() - first.getUTCMonth()) +
      1
    );
  }

  function bind(root) {
    var mode = root.querySelector("[data-ach-mode]");
    if (!mode) return;
    var schedule = root.querySelector("[data-ach-schedule]");
    var start = root.querySelector("[name=start_on]");
    var stop = root.querySelector("[name=end_on]");
    var count = root.querySelector("[name=payment_count]");
    var day = root.querySelector("[name=day_of_month]");
    var driver = root.querySelector("[name=schedule_driver]");
    var submit = root.querySelector("[data-ach-submit]");

    function syncVisibility() {
      var recurring = mode.value === "recurring";
      if (schedule) schedule.style.display = recurring ? "" : "none";
      if (submit) {
        submit.textContent = recurring
          ? submit.getAttribute("data-recurring-label") || submit.textContent
          : submit.getAttribute("data-single-label") || submit.textContent;
      }
    }

    function currentDriver() {
      return driver && driver.value === "count" ? "count" : "end";
    }

    function applyFromCount() {
      var startDate = parseISO(start && start.value);
      var payments = parseInt(count && count.value, 10);
      if (!startDate || !payments || payments < 1) return;
      var stopDate = nthDebit(startDate, clampDay(day && day.value), payments);
      if (stop && stopDate) stop.value = iso(stopDate);
    }

    function applyFromEnd() {
      var startDate = parseISO(start && start.value);
      var endDate = parseISO(stop && stop.value);
      if (!startDate || !endDate) return;
      var payments = countThrough(startDate, endDate, clampDay(day && day.value));
      if (count) count.value = payments > 0 ? String(payments) : "";
    }

    function onScheduleChange() {
      if (mode.value !== "recurring") return;
      if (currentDriver() === "count") applyFromCount();
      else if (stop && stop.value) applyFromEnd();
      else applyFromCount();
    }

    mode.addEventListener("change", function () {
      syncVisibility();
      if (mode.value === "recurring") onScheduleChange();
    });
    if (count) {
      count.addEventListener("input", function () {
        if (driver) driver.value = "count";
        applyFromCount();
      });
    }
    if (stop) {
      stop.addEventListener("input", function () {
        if (driver) driver.value = "end";
        applyFromEnd();
      });
      stop.addEventListener("change", function () {
        if (driver) driver.value = "end";
        applyFromEnd();
      });
    }
    if (start) {
      start.addEventListener("input", onScheduleChange);
      start.addEventListener("change", onScheduleChange);
    }
    if (day) {
      day.addEventListener("input", onScheduleChange);
      day.addEventListener("change", onScheduleChange);
    }
    syncVisibility();
    if (mode.value === "recurring") onScheduleChange();
  }

  function boot() {
    var forms = document.querySelectorAll("[data-ach-form]");
    for (var i = 0; i < forms.length; i++) bind(forms[i]);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
