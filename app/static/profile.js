// Profile page: photo, name, date of birth and Google status.
(() => {
  const $ = id => document.getElementById(id);
  const form = $('profileForm');
  const nameInput = $('nameInput');
  const birthInput = $('birthInput');
  const saveButton = $('saveButton');
  const cancelButton = $('cancelButton');

  let saved = {display_name: '', date_of_birth: ''};

  function setStatus(id, message, isError = false) {
    const element = $(id);
    element.textContent = message;
    element.classList.toggle('error', isError);
  }

  async function call(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    if (response.status === 401) {
      window.location.assign('/login');
      throw new Error('Your session has ended. Please sign in again.');
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Something went wrong. Please try again.');
    }
    return data;
  }

  function initialsFor(name) {
    const words = (name || '').trim().split(/\s+/).filter(Boolean);
    if (!words.length) return '';
    return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase();
  }

  function setPill(element, text, tone) {
    element.textContent = text;
    element.dataset.tone = tone;  // green | grey
  }

  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
  }

  function showPhoto(user) {
    const img = $('photo');
    img.onerror = () => { img.hidden = true; };
    if (user.avatar_url) {
      img.src = user.avatar_url;
      img.hidden = false;
    } else {
      img.removeAttribute('src');
      img.hidden = true;
    }
    $('initials').textContent = initialsFor(user.display_name);
    $('removePhoto').hidden = !user.has_custom_avatar;
    $('photoSource').textContent = user.has_custom_avatar
      ? 'Your uploaded photo.'
      : user.avatar_url
        ? 'Your Google profile picture.'
        : 'No photo yet. Your initials are shown instead.';
  }

  function showUser(user) {
    if (user.settings && window.lifeInboxTheme) window.lifeInboxTheme.apply(user.settings.theme);
    saved = {display_name: user.display_name || '', date_of_birth: user.date_of_birth || ''};
    nameInput.value = saved.display_name;
    birthInput.value = saved.date_of_birth;
    $('emailInput').value = user.email || '';
    $('emailHint').textContent = user.google_connected
      ? 'Comes from your Google account, so it changes there.'
      : "Your sign-in email. It can't be changed here.";

    if (user.google_connected) {
      $('googleText').textContent = `Signed in with Google as ${user.email}`;
      setPill($('googlePill'), 'Connected', 'green');
      const last = formatDate(user.last_login_at);
      $('googleMeta').textContent = last ? `Last signed in ${last}.` : '';
    } else {
      $('googleText').textContent = 'This account does not use Google sign-in.';
      setPill($('googlePill'), 'Not connected', 'grey');
      $('googleMeta').textContent = '';
    }

    showPhoto(user);
    refreshButtons();
  }

  function changes() {
    const result = {};
    if (nameInput.value.trim() !== saved.display_name) result.display_name = nameInput.value;
    if (birthInput.value !== saved.date_of_birth) result.date_of_birth = birthInput.value;
    return result;
  }

  function refreshButtons() {
    const dirty = Object.keys(changes()).length > 0;
    saveButton.disabled = !dirty;
    cancelButton.disabled = !dirty;
  }

  async function load() {
    try {
      showUser(await call('/api/auth/me'));
    } catch (error) {
      setStatus('formStatus', error.message, true);
    }
  }

  form.addEventListener('input', () => {
    refreshButtons();
    setStatus('formStatus', '');
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = changes();
    if (!Object.keys(body).length) return;
    saveButton.disabled = true;
    saveButton.textContent = 'Saving…';
    try {
      const user = await call('/api/me/profile', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      showUser(user);
      setStatus('formStatus', 'Profile saved.');
    } catch (error) {
      setStatus('formStatus', error.message, true);
      refreshButtons();
    } finally {
      saveButton.textContent = 'Save changes';
    }
  });

  cancelButton.addEventListener('click', () => {
    nameInput.value = saved.display_name;
    birthInput.value = saved.date_of_birth;
    refreshButtons();
    setStatus('formStatus', '');
  });

  async function photoRequest(options, busyText, doneText) {
    const button = $('changePhoto');
    const idle = button.textContent;
    button.disabled = true;
    button.textContent = busyText;
    setStatus('formStatus', '');
    try {
      const user = await call('/api/me/avatar', options);
      showPhoto(user);
      setStatus('formStatus', doneText);
    } catch (error) {
      setStatus('formStatus', error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = idle;
    }
  }

  $('changePhoto').addEventListener('click', () => $('photoInput').click());
  $('photoInput').addEventListener('change', event => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';  // picking the same file again still works
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    photoRequest({method: 'POST', body}, 'Uploading…', 'Profile photo updated.');
  });
  $('removePhoto').addEventListener('click', () => {
    photoRequest({method: 'DELETE'}, 'Removing…', 'Photo removed.');
  });

  const today = new Date();
  birthInput.max = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, '0'),
    String(today.getDate()).padStart(2, '0'),
  ].join('-');

  load();
})();
