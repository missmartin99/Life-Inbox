// Settings page: notifications, theme and connected apps. Saves on change.
(() => {
  const $ = id => document.getElementById(id);
  const status = $('settingsStatus');
  let current = {};

  function setStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle('error', isError);
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

  function showSettings(settings) {
    current = settings;
    $('bellBadge').checked = settings.bell_badge;
    $('bellOverdue').checked = settings.bell_overdue_alert;
    for (const input of document.querySelectorAll('input[name="bell_scope"]')) {
      input.checked = input.value === settings.bell_scope;
    }
    for (const input of document.querySelectorAll('input[name="theme"]')) {
      input.checked = input.value === settings.theme;
    }
    if (window.lifeInboxTheme) window.lifeInboxTheme.apply(settings.theme);
  }

  async function save(changes) {
    const before = current;
    setStatus('Saving…');
    if (changes.theme && window.lifeInboxTheme) window.lifeInboxTheme.apply(changes.theme);
    try {
      showSettings(await call('/api/me/settings', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(changes),
      }));
      setStatus('Saved.');
    } catch (error) {
      showSettings(before);  // put the controls back the way they were
      setStatus(error.message, true);
    }
  }

  const STATUS_TEXT = {
    connected: ['Connected', 'green'],
    active: ['On', 'green'],
    not_connected: ['Not connected', 'grey'],
    coming_soon: ['Coming soon', 'grey'],
    needs_reconnect: ['Reconnect', 'amber'],
    unavailable: ['Not available', 'grey'],
    off: ['Off', 'grey'],
  };

  // --- Google Calendar ------------------------------------------------------
  const CONNECT_URL = '/api/auth/google/calendar/start';
  const CALENDAR_RESULTS = {
    connected: ['Google Calendar is connected. Tasks with a due date are being added to the \u201cLife Inbox\u201d calendar.', 'ok'],
    denied: ['Google Calendar was not connected: permission was not given on Google\u2019s page.', 'error'],
    missing_scope: ['Google Calendar was not connected because the calendar permission was unticked. Try again and leave it ticked.', 'error'],
    wrong_user: ['That connection was started from a different Life Inbox account. Please try again.', 'error'],
    state: ['That link expired. Please try again.', 'error'],
    failed: ['Couldn\u2019t connect Google Calendar. Please try again.', 'error'],
    unavailable: ['Google Calendar isn\u2019t available on this server.', 'error'],
  };
  let calendarNotice = null;  // [text, tone], shown in the calendar card
  let confirmingDisconnect = false;
  let pollTimer = null;

  // Coming back from Google: ?calendar=<result>. Show it once, tidy the URL.
  const params = new URLSearchParams(window.location.search);
  if (params.has('calendar')) {
    calendarNotice = CALENDAR_RESULTS[params.get('calendar')] || CALENDAR_RESULTS.failed;
    params.delete('calendar');
    const query = params.toString();
    history.replaceState(null, '', window.location.pathname + (query ? `?${query}` : '') + window.location.hash);
  }

  function timeAgo(iso) {
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return '';
    const seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 45) return 'just now';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    return new Date(then).toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
  }

  function makeButton(text, className, onClick, disabled = false) {
    const button = document.createElement('button');
    button.type = 'button';
    if (className) button.className = className;
    button.textContent = text;
    button.disabled = disabled;
    button.addEventListener('click', onClick);
    return button;
  }

  function makeNote(text, tone) {
    const note = document.createElement('p');
    note.className = 'calendar-note';
    note.dataset.tone = tone;
    note.textContent = text;
    return note;
  }

  function calendarControls(cal) {
    const box = document.createElement('div');
    box.className = 'calendar-box';
    if (calendarNotice) box.append(makeNote(...calendarNotice));
    if (cal.status === 'connected') {
      const count = `${cal.event_count} task${cal.event_count === 1 ? '' : 's'} on your calendar`;
      const line = document.createElement('p');
      line.className = 'app-detail calendar-sync';
      if (cal.syncing) line.textContent = `Syncing\u2026 \u00b7 ${count}`;
      else if (cal.last_synced_at) line.textContent = `${count} \u00b7 Last synced ${timeAgo(cal.last_synced_at)}`;
      else line.textContent = count;
      box.append(line);
      if (cal.last_error) box.append(makeNote(cal.last_error, 'warn'));
    }
    if (confirmingDisconnect) {
      box.append(makeNote(
        'Disconnect Google Calendar? The \u201cLife Inbox\u201d calendar and its task events will be removed from Google Calendar. Your tasks stay in Life Inbox.',
        'warn',
      ));
    }
    const row = document.createElement('div');
    row.className = 'calendar-actions';
    if (confirmingDisconnect) {
      row.append(
        makeButton('Yes, disconnect', 'danger', disconnectCalendar),
        makeButton('Cancel', 'secondary', () => { confirmingDisconnect = false; refreshApps(); }),
      );
    } else if (cal.status === 'not_connected' || cal.status === 'needs_reconnect') {
      const label = cal.status === 'needs_reconnect' ? 'Reconnect Google Calendar' : 'Connect Google Calendar';
      row.append(makeButton(label, '', () => window.location.assign(CONNECT_URL)));
    } else if (cal.status === 'connected') {
      row.append(makeButton(cal.syncing ? 'Syncing\u2026' : 'Sync now', 'secondary', syncCalendar, cal.syncing));
      const open = document.createElement('a');
      open.className = 'app-link';
      open.href = cal.calendar_url;
      open.target = '_blank';
      open.rel = 'noopener noreferrer';
      open.textContent = 'Open Google Calendar \u2197';
      row.append(open);
    }
    if (!confirmingDisconnect && (cal.status === 'connected' || cal.status === 'needs_reconnect')) {
      row.append(makeButton('Disconnect', 'danger', () => {
        confirmingDisconnect = true;
        calendarNotice = null;
        refreshApps();
      }));
    }
    if (row.childElementCount) box.append(row);
    return box;
  }

  async function refreshApps() {
    try {
      showApps((await call('/api/me/connections')).apps);
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  async function syncCalendar() {
    calendarNotice = null;
    try {
      await call('/api/calendar/google/sync', {method: 'POST'});
    } catch (error) {
      calendarNotice = [error.message, 'error'];
    }
    refreshApps();
  }

  async function disconnectCalendar() {
    confirmingDisconnect = false;
    try {
      const result = await call('/api/calendar/google', {method: 'DELETE'});
      calendarNotice = result.calendar_removed
        ? ['Disconnected. The \u201cLife Inbox\u201d calendar was removed from Google Calendar.', 'ok']
        : ['Disconnected. Life Inbox couldn\u2019t remove the \u201cLife Inbox\u201d calendar, so you can delete it in Google Calendar.', 'warn'];
    } catch (error) {
      calendarNotice = [error.message, 'error'];
    }
    refreshApps();
  }

  function showApps(apps) {
    const list = $('appsList');
    list.replaceChildren();
    for (const app of apps) {
      const card = document.createElement('div');
      card.className = 'app-card';
      const head = document.createElement('div');
      head.className = 'app-head';
      const name = document.createElement('div');
      const title = document.createElement('p');
      title.className = 'app-name';
      title.textContent = app.name;
      const purpose = document.createElement('p');
      purpose.className = 'hint';
      purpose.textContent = app.purpose;
      name.append(title, purpose);
      const [label, tone] = STATUS_TEXT[app.status] || [app.status, 'grey'];
      const pill = document.createElement('span');
      pill.className = 'status-pill';
      pill.dataset.tone = tone;
      pill.textContent = label;
      head.append(name, pill);
      const detail = document.createElement('p');
      detail.className = 'app-detail';
      detail.textContent = app.detail;
      card.append(head, detail);
      if (app.id === 'google_calendar' && app.calendar) {
        card.id = 'calendarCard';
        card.append(calendarControls(app.calendar));
      }
      if (app.manage_url) {
        const link = document.createElement('a');
        link.className = 'app-link';
        link.href = app.manage_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = `Manage access in your ${app.name} Account ↗`;
        card.append(link);
      }
      list.append(card);
    }
    // Keep the calendar card current while a sync runs.
    clearTimeout(pollTimer);
    const calendar = apps.find(app => app.id === 'google_calendar');
    if (calendar && calendar.calendar && calendar.calendar.syncing) {
      pollTimer = setTimeout(refreshApps, 1500);
    }
  }

  for (const id of ['bellBadge', 'bellOverdue']) {
    $(id).addEventListener('change', event => {
      save({[event.target.dataset.setting]: event.target.checked});
    });
  }
  for (const name of ['bell_scope', 'theme']) {
    for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
      input.addEventListener('change', () => {
        if (input.checked) save({[name]: input.value});
      });
    }
  }

  (async () => {
    try {
      const [user, connections] = await Promise.all([
        call('/api/auth/me'),
        call('/api/me/connections'),
      ]);
      showSettings(user.settings);
      showApps(connections.apps);
      if (calendarNotice) {
        const card = $('calendarCard');
        if (card) card.scrollIntoView({block: 'center'});
      }
    } catch (error) {
      setStatus(error.message, true);
    }
  })();
})();
