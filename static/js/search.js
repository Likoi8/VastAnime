(function () {
  const form = document.getElementById("header-search");
  const feedEl = document.getElementById("feed-anime");
  if (!form || !feedEl) return;

  const originalFeedHTML = feedEl.innerHTML;
  const params = new URLSearchParams(window.location.search);
  const initialQuery = params.get("q");
  const input = form.querySelector("input[name=q]");

  async function runSearch(query) {
    feedEl.innerHTML = '<p class="status">Ищу…</p>';
    try {
      const res = await fetch("/api/search?q=" + encodeURIComponent(query));
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      renderResults(data.results);
    } catch (e) {
      feedEl.innerHTML = '<p class="status">Ошибка поиска: ' + e.message + '</p>';
    }
  }

  function renderResults(results) {
    if (!results.length) {
      feedEl.innerHTML = '<p class="status">Ничего не найдено.</p>';
      return;
    }
    feedEl.innerHTML = results.map(r => `
      <a class="feed-card" href="/anime/${r.id}">
        <img src="${r.image || "/static/images/no-poster.svg"}" alt="${r.title}">
        <div class="feed-card-body">
          <div class="feed-card-title">${r.title}</div>
          <div class="feed-card-meta">${r.status === "Анонс" ? "Анонс" : ((r.year ? r.year : "") + (r.type ? " · " + r.type : "") + (r.rating && r.rating !== "0.0" ? " · ★ " + r.rating : ""))}</div>
        </div>
      </a>
    `).join("");
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) runSearch(q);
    else feedEl.innerHTML = originalFeedHTML;
  });

  if (initialQuery) {
    input.value = initialQuery;
    runSearch(initialQuery);
  }
})();
