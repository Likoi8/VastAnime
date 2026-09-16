// Закладки: хранятся на сервере (Google)

async function getBookmarks() {
  const resp = await fetch('/api/bookmarks');
  if (resp.status === 401) {
    return { authenticated: false, items: [] };
  }
  if (!resp.ok) {
    return { authenticated: true, items: [] };
  }
  const data = await resp.json();
  return { authenticated: true, items: data.bookmarks || [] };
}

async function isBookmarked(id) {
  const result = await getBookmarks();
  if (!result.authenticated) return false;
  return result.items.some(function (b) { return b.id === id; });
}

async function toggleBookmark(item) {
  const already = await isBookmarked(item.id);
  if (already) {
    const resp = await fetch('/api/bookmarks/' + encodeURIComponent(item.id), { method: 'DELETE' });
    if (resp.status === 401) return { authenticated: false };
    return { authenticated: true, added: false };
  } else {
    const resp = await fetch('/api/bookmarks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(item)
    });
    if (resp.status === 401) return { authenticated: false };
    return { authenticated: true, added: true };
  }
}
