(function () {
  const gridEl = document.getElementById("manga-grid");
  if (!gridEl) return;
  const input = document.querySelector("form input[type=text], form input[type=search], form input:not([type])");
  const NO_POSTER = "/static/images/no-poster.svg";
  let page = 1, loading = false, done = false;

  const sentinel = document.createElement("div");
  sentinel.style.height = "1px";
  gridEl.after(sentinel);

  function searching() { return input && input.value.trim() !== ""; }

  function existingIds() {
    return new Set(Array.from(gridEl.querySelectorAll("a[href^='/manga/']"))
      .map(a => a.getAttribute("href")));
  }

  async function loadMore() {
    if (loading || done || searching()) return;
    loading = true;
    try {
      const r = await fetch("/api/manga/updates?page=" + (page + 1));
      if (!r.ok) return;
      const data = await r.json();
      const items = data.items || [];
      if (!items.length) { done = true; return; }
      const seen = existingIds();
      const frag = document.createDocumentFragment();
      items.forEach(function (it) {
        const href = "/manga/" + it.id;
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
        body.appendChild(t);
        a.appendChild(img);
        a.appendChild(body);
        frag.appendChild(a);
      });
      gridEl.appendChild(frag);
      page++;
      if (data.has_more === false) done = true;
    } catch (e) {
    } finally {
      loading = false;
    }
  }

  if (input) {
    input.addEventListener("input", function () {
      if (!searching()) { page = 1; done = false; }
    });
  }

  new IntersectionObserver(function (es) {
    if (es[0].isIntersecting) loadMore();
  }, { rootMargin: "600px" }).observe(sentinel);
})();
