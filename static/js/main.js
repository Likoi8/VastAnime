// Общий JS, пока резерв


// Фикс "залипающего" :active на мобильном Firefox (и других тач-браузерах):
// CSS :active не всегда сбрасывается после touchend/touchcancel/скролла.
// Управляем состоянием вручную через класс .touch-active.
(function () {
  var ACTIVE_CLASS = "touch-active";
  var current = null;
  var moved = false;

  function clearActive() {
    if (current) {
      current.classList.remove(ACTIVE_CLASS);
      current = null;
    }
  }

  document.addEventListener("touchstart", function (e) {
    var card = e.target.closest(".card, .feed-card");
    if (!card) return;
    moved = false;
    current = card;
    card.classList.add(ACTIVE_CLASS);
  }, { passive: true });

  document.addEventListener("touchmove", function () {
    moved = true;
    clearActive();
  }, { passive: true });

  document.addEventListener("touchend", clearActive, { passive: true });
  document.addEventListener("touchcancel", clearActive, { passive: true });
})();
