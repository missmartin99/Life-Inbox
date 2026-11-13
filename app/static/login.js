// Sign-in page: the same wordmark animation and ribbon as the main page.
(() => {
  const title = document.querySelector('.hero-title[data-split]');
  if (title) {
    const text = title.textContent.trim();
    title.setAttribute('aria-label', text);
    title.textContent = '';
    let index = 0;
    for (const glyph of Array.from(text)) {
      if (/\s/.test(glyph)) {
        title.append(' ');
        continue;
      }
      const span = document.createElement('span');
      span.className = 'sp';
      span.setAttribute('aria-hidden', 'true');
      span.style.setProperty('--i', String(index++));
      span.textContent = glyph;
      title.append(span);
    }
  }

  requestAnimationFrame(() => {
    title?.classList.add('in');
    document.querySelectorAll('[data-rev]').forEach(element => element.classList.add('in'));
  });

  const canvas = document.getElementById('ribbon');
  if (canvas && window.LifeInboxRibbon) window.LifeInboxRibbon.create(canvas).sync();

  // Email + password sign-in, with a "Create account" mode.
  const form = document.getElementById('passwordSignIn');
  if (form) {
    const status = document.getElementById('signInStatus');
    const submit = form.querySelector('button[type="submit"]');
    const password = document.getElementById('signInPassword');
    const modeButtons = Array.from(document.querySelectorAll('.login-mode'));

    const setMode = mode => {
      form.dataset.mode = mode;
      const registering = mode === 'register';
      form.querySelectorAll('.register-only').forEach(el => { el.hidden = !registering; });
      modeButtons.forEach(button => {
        const active = button.dataset.mode === mode;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', String(active));
      });
      submit.textContent = registering ? 'Create account' : 'Sign in';
      password.autocomplete = registering ? 'new-password' : 'current-password';
      password.placeholder = registering ? 'Choose a password' : 'Your password';
      status.textContent = '';
    };
    modeButtons.forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));

    form.addEventListener('submit', async event => {
      event.preventDefault();
      const registering = form.dataset.mode === 'register';
      const email = document.getElementById('signInEmail').value.trim();
      const pass = password.value;
      const name = document.getElementById('signInName').value.trim();
      if (!email || !email.includes('@')) {
        status.textContent = 'Enter a valid email address.';
        return;
      }
      if (!pass) {
        status.textContent = 'Enter your password.';
        return;
      }
      if (registering) {
        if (pass.length < 8) {
          status.textContent = 'Password must be at least 8 characters.';
          return;
        }
        if (pass !== document.getElementById('signInConfirm').value) {
          status.textContent = 'The passwords do not match.';
          return;
        }
      }
      status.textContent = '';
      submit.disabled = true;
      try {
        const body = registering ? {email, password: pass, display_name: name} : {email, password: pass};
        const response = await fetch(registering ? '/api/auth/register' : '/api/auth/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
          credentials: 'same-origin'
        });
        if (!response.ok) {
          let detail = 'Sign-in failed. Please try again.';
          try {
            const data = await response.json();
            if (typeof data.detail === 'string') detail = data.detail;
          } catch {}
          throw new Error(detail);
        }
        // Start with this account's own theme (light unless they chose
        // otherwise), not whatever the last person on this browser used.
        try {
          const user = await response.json();
          const theme = (user && user.settings && user.settings.theme) || 'light';
          window.localStorage.setItem('lifeInbox.theme', theme);
        } catch {}
        window.location.assign('/');
      } catch (error) {
        status.textContent = error.message;
        password.value = '';
        submit.disabled = false;
      }
    });
  }

  window.lifeInboxLoginReady = true;
})();
