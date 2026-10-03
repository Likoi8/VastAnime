(function () {
  const form = document.getElementById("header-search");
  const gridEl = document.getElementById("ranobe-grid");
  if (!form || !gridEl) return;
  const originalGridHTML = gridEl.innerHTML;
  const params = new URLSearchParams(window.location.search);
  const initialQuery = params.get("q");
  const input = form.querySelector("input[name=q]");

  function renderCard(r) {
    return `
      <a class="feed-card" href="/ranobe/${r.id}">
        <img src="${r.image || "/static/images/no-poster.svg"}" alt="${r.title}">
        <div class="feed-card-body"><div class="feed-card-title">${r.title}</div></div>
      </a>
    `;
  }

  async function runSearch(query) {
    gridEl.innerHTML = '<p class="manga-empty">Ищу…</p>';
    try {
      const res = await fetch("/api/ranobe/search?q=" + encodeURIComponent(query));
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      renderResults(data.results || []);
    } catch (e) {
      gridEl.innerHTML = '<p class="manga-empty">Ошибка поиска: ' + e.message + '</p>';
    }
  }

  function renderResults(results) {
    if (!results.length) {
      gridEl.innerHTML = '<p class="manga-empty">Ничего не найдено.</p>';
      return;
    }
    gridEl.innerHTML = results.map(renderCard).join("");
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) runSearch(q);
    else gridEl.innerHTML = originalGridHTML;
  });

  input.addEventListener("input", () => {
    clearTimeout(window._ranobeSearchTimer);
    const q = input.value.trim();
    if (!q) { gridEl.innerHTML = originalGridHTML; return; }
    window._ranobeSearchTimer = setTimeout(() => runSearch(q), 300);
  });

  if (initialQuery) {
    input.value = initialQuery;
    runSearch(initialQuery);
  }
})();
