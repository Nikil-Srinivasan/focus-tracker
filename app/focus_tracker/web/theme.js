"use strict";
// Applies the saved colour theme and sidebar state before the page paints (no flash).
// Both live in this browser only; theme "auto" follows Windows light/dark mode.
(function () {
  let theme = "auto";
  try { theme = localStorage.getItem("focus-theme") || "auto"; } catch (_e) { /* storage blocked */ }
  if (theme !== "auto") document.documentElement.dataset.theme = theme;
  let side = "";
  try { side = localStorage.getItem("focus-side") || ""; } catch (_e) { /* storage blocked */ }
  if (side === "collapsed") document.documentElement.dataset.side = "collapsed";
})();
