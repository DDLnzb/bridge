/* DDLnzb Bridge - Web UI: interactions
 *
 * No framework, no build. The script uses only what the browser brings along.
 * All requests are same-origin; the browser sends the session cookie along
 * automatically.
 */

(function () {
  "use strict";

  var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function $all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  /* ------------------------------------------------------- Show password */

  function initSecretToggles() {
    $all("[data-toggle-secret]").forEach(function (button) {
      var input = document.getElementById(button.getAttribute("data-toggle-secret"));
      if (!input) {
        return;
      }
      button.addEventListener("click", function () {
        var shown = input.type === "text";
        input.type = shown ? "password" : "text";
        button.classList.toggle("is-active", !shown);
        var label = button.querySelector("span");
        if (label) {
          label.textContent = shown ? "Show" : "Hide";
        }
        input.focus();
      });
    });
  }

  /* --------------------------------------------------- Loading state on button */

  function withLoading(button, promiseFactory) {
    if (button.dataset.busy === "1") {
      return;
    }
    button.dataset.busy = "1";
    button.disabled = true;
    button.classList.add("is-busy");

    var restore = function () {
      button.disabled = false;
      button.classList.remove("is-busy");
      delete button.dataset.busy;
    };

    promiseFactory().then(restore, restore);
  }

  /* --------------------------------------------------------------- Results */

  function showResult(kind, ok, detail) {
    var box = $('[data-result="' + kind + '"]');
    var value = $('[data-result-value="' + kind + '"]');
    var detailBox = $('[data-result-detail="' + kind + '"]');
    if (!box || !value) {
      return;
    }
    value.textContent = ok ? "reachable" : "not reachable";
    value.className = ok ? "badge badge-ok" : "badge badge-err";
    if (detailBox) {
      detailBox.textContent = detail || "";
    }
    box.classList.add("is-visible");
    box.classList.remove("is-revealed");
    /* Force a reflow so that the animation runs again. */
    void box.offsetWidth;
    box.classList.add("is-revealed");
  }

  function initTests() {
    $all("[data-test]").forEach(function (button) {
      var kind = button.getAttribute("data-test");
      var endpoint = kind === "jdownloader" ? "/ui/reconnect" : "/ui/test";
      button.addEventListener("click", function () {
        withLoading(button, function () {
          return fetch(endpoint, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
          })
            .then(function (response) {
              return response.json();
            })
            .then(function (payload) {
              if (kind === "jdownloader") {
                showResult("jdownloader", !!payload.ok, payload.detail);
                return;
              }
              var api = payload.api || {};
              showResult("api", !!api.ok, api.detail);
              var jd = payload.jdownloader || {};
              showResult("jdownloader", !!jd.ok, jd.detail);
            })
            .catch(function (error) {
              showResult(kind, false, String(error));
            });
        });
      });
    });
  }

  /* ------------------------------------------------------------------ Reset */

  function initReset() {
    $all("[data-reset]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!window.confirm("Discard all saved values and go back to the environment?")) {
          return;
        }
        withLoading(button, function () {
          return fetch("/ui/reset", { method: "POST", credentials: "same-origin" })
            .then(function () {
              window.location.href = "/ui?flash=reset";
            })
            .catch(function () {
              window.location.reload();
            });
        });
      });
    });
  }

  /* --------------------------------------------------- Unsaved changes */

  function initDirtyTracking() {
    var form = $("#settings-form");
    if (!form) {
      return;
    }
    var hint = $(".savebar-hint");
    var submit = $('.savebar-actions [type="submit"]');
    var baseline = {};
    var dirty = false;

    var snapshot = function () {
      var values = {};
      $all("input, select", form).forEach(function (field) {
        if (!field.name) {
          return;
        }
        values[field.name] = field.type === "checkbox" ? field.checked : field.value;
      });
      return values;
    };

    var evaluate = function () {
      var current = snapshot();
      dirty = Object.keys(baseline).some(function (key) {
        return String(current[key]) !== String(baseline[key]);
      });
      if (hint) {
        hint.textContent = dirty
          ? "Unsaved changes - saving applies them immediately."
          : "Changes apply immediately - no restart needed.";
        hint.classList.toggle("is-dirty", dirty);
      }
      if (submit) {
        submit.classList.toggle("is-dirty", dirty);
      }
    };

    baseline = snapshot();
    form.addEventListener("input", evaluate);
    form.addEventListener("change", evaluate);

    /* Ctrl+S / Cmd+S saves instead of opening the browser dialog. */
    document.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        form.submit();
      }
    });
  }

  /* ------------------------------------------------------------------ Start */

  function init() {
    initSecretToggles();
    initTests();
    initReset();
    initDirtyTracking();
    if (!REDUCED) {
      document.body.classList.add("has-motion");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
