"use strict";
// Applies the saved colour theme before the page paints (no flash of the wrong theme).
// The choice lives in this browser only; "auto" follows Windows light/dark mode.
(function () {
  let theme = "auto";
  try { theme = localStorage.getItem("focus-theme") || "auto"; } catch (_e) { /* storage blocked */ }
  if (theme !== "auto") document.documentElement.dataset.theme = theme;
})();
