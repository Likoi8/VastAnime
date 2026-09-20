(function () {
  if (!window.matchMedia("(hover: none)").matches) return;
  var SEL = ".card, .feed-card, .manga-card, .chapter-item";
  var el = null, t0 = 0, x0 = 0, y0 = 0, moved = false;

  document.addEventListener("touchstart", function (e) {
    el = e.target.closest ? e.target.closest(SEL) : null;
    var t = e.touches[0];
    t0 = Date.now(); x0 = t.clientX; y0 = t.clientY; moved = false;
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    var t = e.touches[0];
    if (Math.abs(t.clientX - x0) > 8 || Math.abs(t.clientY - y0) > 8) moved = true;
  }, { passive: true });

  document.addEventListener("touchend", function () {
    if (!el || moved || Date.now() - t0 > 250) { el = null; return; }
    var c = el; el = null;
    c.classList.add("touch-active");
    setTimeout(function () { c.classList.remove("touch-active"); }, 220);
  }, { passive: true });
})();
