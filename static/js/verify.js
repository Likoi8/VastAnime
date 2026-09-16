document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('verify_pending') !== '1') return;

  const overlay = document.getElementById('verify-modal');
  const emailSpan = document.getElementById('verify-email');
  const input = document.getElementById('verify-code-input');
  const errorBox = document.getElementById('verify-error');
  const submitBtn = document.getElementById('verify-submit-btn');
  const resendBtn = document.getElementById('verify-resend-btn');

  overlay.style.display = 'flex';
  input.focus();

  fetch('/api/auth/pending-email')
    .then(r => r.json())
    .then(data => {
      if (data.email) emailSpan.textContent = data.email;
    })
    .catch(() => {});

  function showError(msg) {
    errorBox.textContent = msg;
  }

  function submitCode() {
    const code = input.value.trim();
    if (code.length !== 6) {
      showError('Введите 6-значный код');
      return;
    }
    submitBtn.disabled = true;
    showError('');
    fetch('/api/auth/verify-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (ok && data.success) {
          const url = new URL(window.location.href);
          url.searchParams.delete('verify_pending');
          window.location.href = url.toString();
        } else {
          const messages = {
            code_expired: 'Код истёк, запросите новый',
            too_many_attempts: 'Слишком много попыток, запросите новый код',
            wrong_code: 'Неверный код',
            no_pending_login: 'Сессия входа истекла, попробуйте войти снова'
          };
          showError(messages[data.error] || 'Ошибка, попробуйте снова');
          submitBtn.disabled = false;
        }
      })
      .catch(() => {
        showError('Ошибка сети, попробуйте снова');
        submitBtn.disabled = false;
      });
  }

  submitBtn.addEventListener('click', submitCode);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitCode();
  });
  input.addEventListener('input', () => {
    input.value = input.value.replace(/\D/g, '');
  });

  resendBtn.addEventListener('click', () => {
    resendBtn.disabled = true;
    showError('');
    fetch('/api/auth/resend-code', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          showError('Код отправлен повторно');
        } else {
          showError('Не удалось отправить код');
        }
      })
      .catch(() => showError('Ошибка сети'))
      .finally(() => {
        setTimeout(() => { resendBtn.disabled = false; }, 30000);
      });
  });
});
