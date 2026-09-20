(function () {
  const feed = document.getElementById("feed-anime");
  if (!feed) return;
  const input = document.querySelector("form input[type=text], form input[type=search], form input:not([type])");
  const NO_POSTER = "/static/images/no-poster.svg";
  let page = 4, loading = false, done = false;

  const sentinel = document.createElement("div");
  sentinel.style.height = "1px";
  feed.after(sentinel);

  function searching() { return input && input.value.trim() !== ""; }

  function existingHrefs() {
    return new Set(Array.from(feed.querySelectorAll("a[href^='/anime/']"))
      .map(a => a.getAttribute("href")));
  }

  async function loadMore() {
    if (loading || done || searching()) return;
    loading = true;
    try {
      const r = await fetch("/api/anime/updates?page=" + (page + 1));
      if (!r.ok) return;
      const data = await r.json();
      page++;
      const items = data.items || [];
      if (!items.length || data.has_more === false) done = true;
      const seen = existingHrefs();
      const frag = document.createDocumentFragment();
      items.forEach(function (it) {
        const href = "/anime/" + it.id;
        if (seen.has(href)) return;
        const a = document.createElement("a");
        a.className = "feed-card";
        a.href = href;
        const img = document.createElement("img");
        img.src = it.image || NO_POSTER;
        img.alt = it.title || "";
        img.loading = "lazy";
        img.decoding = "async";
        const body = document.createElement("div");
        body.className = "feed-card-body";
        const t = document.createElement("div");
        t.className = "feed-card-title";
        t.textContent = it.title || "";
        const m = document.createElement("div");
        m.className = "feed-card-meta";
        const parts = [];
        if (it.episodes) parts.push(it.episodes + " эп");
        if (it.score) parts.push(it.score + " ★");
        m.textContent = parts.join(" · ");
        body.appendChild(t);
        body.appendChild(m);
        a.appendChild(img);
        a.appendChild(body);
        frag.appendChild(a);
      });
      feed.appendChild(frag);
    } catch (e) {
    } finally {
      loading = false;
    }
  }

  new IntersectionObserver(function (es) {
    if (es[0].isIntersecting) loadMore();
  }, { rootMargin: "600px" }).observe(sentinel);
})();
