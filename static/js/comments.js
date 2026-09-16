async function getComments(animeId) {
  const resp = await fetch('/api/comments/' + encodeURIComponent(animeId));
  if (!resp.ok) {
    return { comments: [], current_user_id: null };
  }
  const data = await resp.json();
  return { comments: data.comments || [], current_user_id: data.current_user_id || null };
}

async function addComment(animeId, content, parentId) {
  const resp = await fetch('/api/comments/' + encodeURIComponent(animeId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: content, parent_id: parentId || null })
  });
  if (resp.status === 401) return { authenticated: false };
  if (!resp.ok) {
    const data = await resp.json().catch(function () { return {}; });
    return { authenticated: true, ok: false, error: data.error || 'unknown_error' };
  }
  const data = await resp.json();
  return { authenticated: true, ok: true, id: data.id };
}

async function updateComment(commentId, content) {
  const resp = await fetch('/api/comments/item/' + encodeURIComponent(commentId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: content })
  });
  if (resp.status === 401) return { authenticated: false };
  if (resp.status === 403) return { authenticated: true, ok: false, error: 'forbidden' };
  if (!resp.ok) {
    const data = await resp.json().catch(function () { return {}; });
    return { authenticated: true, ok: false, error: data.error || 'unknown_error' };
  }
  return { authenticated: true, ok: true };
}

async function deleteComment(commentId) {
  const resp = await fetch('/api/comments/item/' + encodeURIComponent(commentId), {
    method: 'DELETE'
  });
  if (resp.status === 401) return { authenticated: false };
  if (resp.status === 403) return { authenticated: true, ok: false, error: 'forbidden' };
  if (!resp.ok) {
    return { authenticated: true, ok: false, error: 'unknown_error' };
  }
  return { authenticated: true, ok: true };
}


async function likeComment(commentId) {
  const resp = await fetch('/api/comments/item/' + encodeURIComponent(commentId) + '/like', {
    method: 'POST'
  });
  if (resp.status === 401) return { authenticated: false };
  if (!resp.ok) return { authenticated: true, ok: false };
  return { authenticated: true, ok: true };
}

async function unlikeComment(commentId) {
  const resp = await fetch('/api/comments/item/' + encodeURIComponent(commentId) + '/like', {
    method: 'DELETE'
  });
  if (resp.status === 401) return { authenticated: false };
  if (!resp.ok) return { authenticated: true, ok: false };
  return { authenticated: true, ok: true };
}
