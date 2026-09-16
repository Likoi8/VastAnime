(function () {
  const section = document.querySelector(".watch-section");
  if (!section) return;
  const animeId = section.dataset.animeId;
  const episode = section.dataset.episode;
  const video = document.getElementById("player");
  const iframe = document.getElementById("iframe-player");
  const status = document.getElementById("player-status");
  const buttons = document.querySelectorAll(".voice-btn");
  const voicePrefKey = "voice_pref_" + animeId;
  function getSavedVoiceLabel() {
    try {
      return localStorage.getItem(voicePrefKey);
    } catch (e) {
      return null;
    }
  }
  function saveVoiceLabel(label) {
    try {
      localStorage.setItem(voicePrefKey, label);
    } catch (e) {}
  }
  function moveButtonFirst(btn) {
    const parent = btn.parentNode;
    if (parent && parent.firstChild !== btn) {
      parent.insertBefore(btn, parent.firstChild);
    }
  }
  let dashPlayer = null;
  let hlsPlayer = null;
  function resetPlayers() {
    if (dashPlayer) { dashPlayer.reset(); dashPlayer = null; }
    if (hlsPlayer) { hlsPlayer.destroy(); hlsPlayer = null; }
    video.style.display = "none";
    iframe.style.display = "none";
    video.removeAttribute("src");
    iframe.removeAttribute("src");
  }
  function playDash(url) {
    resetPlayers();
    video.style.display = "block";
    dashPlayer = dashjs.MediaPlayer().create();
    dashPlayer.initialize(video, url, true);
  }
  function playHls(url) {
    resetPlayers();
    video.style.display = "block";
    if (Hls.isSupported()) {
      hlsPlayer = new Hls();
      hlsPlayer.loadSource(url);
      hlsPlayer.attachMedia(video);
      video.play().catch(() => {});
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      video.play().catch(() => {});
    } else {
      status.textContent = "Ваш браузер не поддерживает HLS.";
    }
  }
  function playMp4(url) {
    resetPlayers();
    video.style.display = "block";
    video.src = url;
    video.play().catch(() => {});
  }
  function playEmbed(url) {
    resetPlayers();
    iframe.style.display = "block";
    iframe.src = url;
  }
  async function selectVoice(btn, isInitial) {
    status.textContent = "Загрузка потока…";
    buttons.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    moveButtonFirst(btn);
    if (!isInitial) {
      saveVoiceLabel(btn.dataset.label);
    }
    const player = btn.dataset.player;
    const translationId = btn.dataset.translationId;
    const label = btn.dataset.label;
    const cvhId = btn.dataset.cvhId;
    const embed = btn.dataset.embed;
// Kodik: встроенный iframe-плеер (соблюдение условий бесплатного API — показ рекламы)
    if (player === "Sibnet" || player === "Kodik") {
      status.textContent = "";
      playEmbed(embed);
      return;
    }
    const params = new URLSearchParams({
      anime_id: animeId, episode: episode, player: player,
    });
    if (player === "AniBoom") params.set("translation_id", translationId);
    if (player === "CVH") { params.set("cvh_id", cvhId); params.set("label", label); }
    try {
      const res = await fetch("/api/stream-direct?" + params.toString());
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      status.textContent = "";
      if (data.type === "dash") playDash(data.url);
      else if (data.type === "hls") playHls(data.url);
      else if (data.type === "mp4") playMp4(data.url);
    } catch (e) {
      status.textContent = "Не удалось получить поток: " + e.message + (embed && embed !== "None" ? ". Пробуем встроенный плеер…" : "");
      if (embed && embed !== "None") playEmbed(embed);
    }
  }
  buttons.forEach(btn => btn.addEventListener("click", () => selectVoice(btn, false)));
  if (buttons.length > 0) {
    const savedLabel = getSavedVoiceLabel();
    let initialBtn = buttons[0];
    if (savedLabel) {
      const found = Array.from(buttons).find(b => b.dataset.label === savedLabel);
      if (found) initialBtn = found;
    }
    selectVoice(initialBtn, true);
  }
})();
