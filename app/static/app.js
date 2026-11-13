let isSavingNote = false;
let isWatchingAnalysis = false;
let rewatchAnalysis = false;
let notesReloadQueued = false;
let searchDebounce = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingNoteActions = new Set();
const pendingTaskActions = new Set();
// What the AI made of each note (topic, priority), keyed by note id.
const noteInsights = new Map();
// Dashboard view state that must survive a re-render.
const dashboardState = {
  rendered: '',
  expanded: new Set(),
  undo: null,
  undoTimer: 0
};
const itemFilterFields = {
  q: 'itemSearch',
  item_type: 'itemTypeFilter',
  category: 'categoryFilter',
  priority: 'priorityFilter',
  processing_status: 'processingStatusFilter'
};

function setStatus(elementId, message, isError = false) {
  const element = document.getElementById(elementId);
  element.textContent = message;
  element.classList.toggle('error', isError);
}

function setButtonBusy(buttonId, busy, busyLabel, idleLabel) {
  const button = document.getElementById(buttonId);
  button.disabled = busy;
  button.textContent = busy ? busyLabel : idleLabel;
  button.setAttribute('aria-busy', String(busy));
}

function itemFilterParams() {
  const params = new URLSearchParams();
  for (const [name, elementId] of Object.entries(itemFilterFields)) {
    const value = document.getElementById(elementId).value.trim();
    if (value) params.set(name, value);
  }
  return params;
}

function hasItemFilters() {
  return itemFilterParams().size > 0;
}

function updateClearFiltersButton() {
  const button = document.getElementById('clearFiltersButton');
  const slot = button ? button.closest('.search-actions') : null;
  if (slot) slot.hidden = !hasItemFilters();
}

function updateUrl(updates, {replace = false} = {}) {
  const url = new URL(window.location.href);
  for (const [name, value] of Object.entries(updates)) {
    if (value) {
      url.searchParams.set(name, value);
    } else {
      url.searchParams.delete(name);
    }
  }
  if (replace) {
    window.history.replaceState(window.history.state, '', url);
  } else {
    window.history.pushState({}, '', url);
  }
}

/* ---------- Dates and times, the way people say them ---------- */
function parseIsoDate(iso) {
  const [year, month, day] = String(iso).split('-').map(Number);
  return new Date(year, month - 1, day);
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function daysBetween(from, to) {
  return Math.round((startOfDay(to) - startOfDay(from)) / 86400000);
}

function clockLabel(hhmm) {
  const [hours, minutes] = String(hhmm).split(':').map(Number);
  const suffix = hours < 12 ? 'AM' : 'PM';
  return `${hours % 12 || 12}:${String(minutes).padStart(2, '0')} ${suffix}`;
}

// "Just now", "12 min ago", "Today, 2:05 PM", "Yesterday, 9:10 AM", "12 Nov, 9:10 AM".
function friendlyTime(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return iso || '';
  const now = new Date();
  const minutes = Math.floor((now - date) / 60000);
  if (minutes >= 0 && minutes < 1) return 'Just now';
  if (minutes >= 0 && minutes < 60) return `${minutes} min ago`;
  const clock = date.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  const days = daysBetween(date, now);
  if (days === 0) return `Today, ${clock}`;
  if (days === 1) return `Yesterday, ${clock}`;
  const sameYear = date.getFullYear() === now.getFullYear();
  const day = date.toLocaleDateString(
    undefined,
    sameYear
      ? {day: 'numeric', month: 'short'}
      : {day: 'numeric', month: 'short', year: 'numeric'}
  );
  return sameYear ? `${day}, ${clock}` : day;
}

// "Today, 12:15 PM", "Tomorrow", "Fri 20 Nov"; `todayIso` is the server's today.
function dueLabel(task, todayIso) {
  if (!task.normalized_due_on) return 'No date';
  const due = parseIsoDate(task.normalized_due_on);
  const today = parseIsoDate(todayIso);
  const days = daysBetween(today, due);
  let day;
  if (days === 0) day = 'Today';
  else if (days === 1) day = 'Tomorrow';
  else if (days === -1) day = 'Yesterday';
  else {
    day = due.toLocaleDateString(
      undefined,
      due.getFullYear() === today.getFullYear()
        ? {weekday: 'short', day: 'numeric', month: 'short'}
        : {day: 'numeric', month: 'short', year: 'numeric'}
    );
  }
  return task.due_time ? `${day}, ${clockLabel(task.due_time)}` : day;
}

function syncControlsFromUrl() {
  const params = new URLSearchParams(window.location.search);
  for (const [name, elementId] of Object.entries(itemFilterFields)) {
    const element = document.getElementById(elementId);
    const value = params.get(name) || '';
    if (
      name === 'category'
      && value
      && !Array.from(element.options).some(option => option.value === value)
    ) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      element.appendChild(option);
    }
    element.value = value;
  }
  updateClearFiltersButton();
}

// Remember each note's topic and priority from the saved analysis, then
// refresh the tags on any note cards that are already on screen.
function rememberNoteInsights(data) {
  noteInsights.clear();
  for (const bucket of data.buckets || []) {
    for (const note of bucket.notes || []) {
      noteInsights.set(note.id, {
        category: bucket.name,
        priority: note.priority,
        title: note.title
      });
    }
  }
  document.querySelectorAll('.note-tags[data-note-id]').forEach(tags => {
    const note = itemPagination.loadedItems.find(
      item => String(item.id) === tags.dataset.noteId
    );
    if (note) fillNoteTags(tags, note);
  });
}

function populateCategoryFilter(data) {
  rememberNoteInsights(data);
  const select = document.getElementById('categoryFilter');
  const selected =
    new URLSearchParams(window.location.search).get('category')
    || select.value;
  const categories = Array.from(
    new Set((data.buckets || []).map(bucket => bucket.name))
  ).sort((first, second) => first.localeCompare(second));

  select.replaceChildren();
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = 'All';
  select.appendChild(allOption);

  if (selected && !categories.includes(selected)) {
    categories.push(selected);
    categories.sort((first, second) => first.localeCompare(second));
  }
  for (const category of categories) {
    const option = document.createElement('option');
    option.value = category;
    option.textContent = category;
    select.appendChild(option);
  }
  select.value = selected;
}

function applyItemFilters(event, {replace = false} = {}) {
  if (event) event.preventDefault();
  window.clearTimeout(searchDebounce);
  const values = {};
  const params = itemFilterParams();
  for (const name of Object.keys(itemFilterFields)) {
    values[name] = params.get(name) || '';
  }
  updateUrl(values, {replace});
  updateClearFiltersButton();
  loadNotes();
}

// Open the Notes tab showing one topic (the tab switch itself is done by the
// data-goto attribute on the element that was clicked).
function showTopicNotes(name) {
  for (const elementId of Object.values(itemFilterFields)) {
    document.getElementById(elementId).value = '';
  }
  const select = document.getElementById('categoryFilter');
  if (!Array.from(select.options).some(option => option.value === name)) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.value = name;
  applyItemFilters();
}

function clearItemFilters() {
  for (const elementId of Object.values(itemFilterFields)) {
    document.getElementById(elementId).value = '';
  }
  updateUrl(
    Object.fromEntries(
      Object.keys(itemFilterFields).map(name => [name, ''])
    )
  );
  window.clearTimeout(searchDebounce);
  updateClearFiltersButton();
  loadNotes();
}

let authRedirecting = false;

function redirectToLogin() {
  if (authRedirecting) return;
  authRedirecting = true;
  window.location.assign('/login');
}

async function fetchJson(url, options, friendlyError) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error(friendlyError);
  }

  if (response.status === 401) {
    // Session missing, expired, or revoked (e.g. "sign out everywhere").
    redirectToLogin();
    throw new Error('Your session has ended. Please sign in again.');
  }

  if (!response.ok) {
    throw new Error(friendlyError);
  }

  if (response.status === 204) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    throw new Error(friendlyError);
  }
}

async function addNote() {
  if (isSavingNote) return;

  const textarea = document.getElementById('noteText');
  const text = textarea.value.trim();
  if (!text) {
    setStatus('noteStatus', 'Write a note before saving.', true);
    textarea.focus();
    return;
  }

  isSavingNote = true;
  setButtonBusy('addNoteButton', true, 'Saving…', 'Add Note');
  setStatus('noteStatus', 'Saving your note…');

  try {
    await fetchJson(
      '/api/items',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({raw_text: text})
      },
      'Unable to save note.'
    );

    if (textarea.value.trim() === text) {
      textarea.value = '';
    }
    setStatus('noteStatus', 'Note saved.');
    await loadNotes(
      'Your note was saved, but the list could not refresh. Reload to try again.',
      {statusId: 'noteStatus'}
    );
    await loadLatestAnalysis();
  } catch {
    setStatus(
      'noteStatus',
      'We could not save your note. Your text is still here—please try again.',
      true
    );
  } finally {
    isSavingNote = false;
    setButtonBusy('addNoteButton', false, 'Saving…', 'Add Note');
  }
}

async function addPdf() {
  const input = document.getElementById('pdfFile');
  const file = input.files && input.files[0];
  if (!file) {
    setStatus('noteStatus', 'Choose a PDF file before uploading.', true);
    input.focus();
    return;
  }
  setButtonBusy('addPdfButton', true, 'Uploading…', 'Upload PDF');
  setStatus('noteStatus', 'Saving PDF and extracting text…');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const item = await fetchJson(
      '/api/items/pdf',
      {method: 'POST', body: formData},
      'Unable to upload PDF.'
    );
    input.value = '';
    setStatus(
      'noteStatus',
      item.processing_status === 'ready'
        ? 'PDF saved and processed.'
        : `PDF saved but processing failed: ${item.error_message || 'unknown error'}`,
      item.processing_status !== 'ready'
    );
    await loadNotes();
    await loadLatestAnalysis();
    await loadDashboard();
  } catch {
    setStatus('noteStatus', 'We could not upload that PDF.', true);
  } finally {
    setButtonBusy('addPdfButton', false, 'Uploading…', 'Upload PDF');
  }
}

async function addImage() {
  const input = document.getElementById('imageFile');
  const file = input.files && input.files[0];
  if (!file) {
    setStatus('noteStatus', 'Choose an image before uploading.', true);
    input.focus();
    return;
  }
  setButtonBusy('addImageButton', true, 'Uploading…', 'Upload image');
  setStatus('noteStatus', 'Saving image and running OCR…');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const item = await fetchJson(
      '/api/items/image',
      {method: 'POST', body: formData},
      'Unable to upload image.'
    );
    input.value = '';
    setStatus(
      'noteStatus',
      item.processing_status === 'ready'
        ? 'Image saved and processed.'
        : `Image saved but processing failed: ${item.error_message || 'unknown error'}`,
      item.processing_status !== 'ready'
    );
    await loadNotes();
    await loadLatestAnalysis();
    await loadDashboard();
  } catch {
    setStatus('noteStatus', 'We could not upload that image.', true);
  } finally {
    setButtonBusy('addImageButton', false, 'Uploading…', 'Upload image');
  }
}

let voiceRecorder = null;
let voiceChunks = [];
let voiceStream = null;

async function addVoice() {
  const input = document.getElementById('voiceFile');
  const file = input.files && input.files[0];
  if (!file) {
    setStatus('noteStatus', 'Choose an audio file or record one first.', true);
    input.focus();
    return;
  }
  await uploadVoiceFile(file, 'Uploading voice note…');
  input.value = '';
}

async function uploadVoiceFile(file, statusMessage) {
  setButtonBusy('addVoiceButton', true, 'Uploading…', 'Upload audio');
  setStatus('noteStatus', statusMessage);
  const formData = new FormData();
  formData.append('file', file, file.name || 'voice-note.webm');
  try {
    const item = await fetchJson(
      '/api/items/voice',
      {method: 'POST', body: formData},
      'Unable to upload voice note.'
    );
    setStatus(
      'noteStatus',
      item.processing_status === 'ready'
        ? (item.extracted_text
          ? 'Voice note saved and transcribed.'
          : 'Voice note saved. Add or edit the transcript if needed.')
        : `Voice note saved but processing failed: ${item.error_message || 'unknown error'}`,
      item.processing_status !== 'ready'
    );
    setStatus('voiceStatus', '');
    await loadNotes();
    await loadLatestAnalysis();
    await loadDashboard();
  } catch {
    setStatus('noteStatus', 'We could not upload that voice note.', true);
  } finally {
    setButtonBusy('addVoiceButton', false, 'Uploading…', 'Upload audio');
  }
}

async function toggleVoiceRecording() {
  // Same pipeline as tapping the cat: record, transcribe, Jin answers aloud.
  if (window.JinVoice && typeof window.JinVoice.tap === 'function') {
    window.JinVoice.tap();
    return;
  }
  if (voiceRecorder && voiceRecorder.state === 'recording') {
    voiceRecorder.stop();
    setButtonBusy('recordVoiceButton', false, 'Recording…', 'Record');
    setStatus('voiceStatus', 'Processing recording…');
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus('voiceStatus', 'Browser recording is not available here. Upload an audio file instead.', true);
    return;
  }

  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({audio: true});
    voiceChunks = [];
    voiceRecorder = new MediaRecorder(voiceStream);
    voiceRecorder.addEventListener('dataavailable', event => {
      if (event.data && event.data.size > 0) voiceChunks.push(event.data);
    });
    voiceRecorder.addEventListener('stop', async () => {
      if (voiceStream) {
        voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
      }
      const blob = new Blob(voiceChunks, {type: voiceRecorder.mimeType || 'audio/webm'});
      voiceRecorder = null;
      if (!blob.size) {
        setStatus('voiceStatus', 'Recording was empty.', true);
        return;
      }
      const file = new File([blob], `recording-${Date.now()}.webm`, {
        type: blob.type || 'audio/webm'
      });
      await uploadVoiceFile(file, 'Saving recorded voice note…');
    });
    voiceRecorder.start();
    setButtonBusy('recordVoiceButton', true, 'Stop', 'Record');
    document.getElementById('recordVoiceButton').textContent = 'Stop';
    document.getElementById('recordVoiceButton').disabled = false;
    setStatus('voiceStatus', 'Recording… click Stop when finished.');
  } catch {
    setStatus(
      'voiceStatus',
      'Could not access the microphone. You can still upload an audio file.',
      true
    );
  }
}

async function addUrl() {
  const input = document.getElementById('urlText');
  const url = input.value.trim();
  if (!url) {
    setStatus('noteStatus', 'Paste a URL before saving.', true);
    input.focus();
    return;
  }
  setButtonBusy('addUrlButton', true, 'Saving…', 'Save URL');
  setStatus('noteStatus', 'Saving URL and fetching article text…');
  try {
    const item = await fetchJson(
      '/api/items/url',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url})
      },
      'Unable to save URL.'
    );
    if (input.value.trim() === url) input.value = '';
    setStatus(
      'noteStatus',
      item.processing_status === 'ready'
        ? 'URL saved and processed.'
        : `URL saved but processing failed: ${item.error_message || 'unknown error'}`,
      item.processing_status !== 'ready'
    );
    await loadNotes();
    await loadLatestAnalysis();
    await loadDashboard();
  } catch {
    setStatus('noteStatus', 'We could not save that URL.', true);
  } finally {
    setButtonBusy('addUrlButton', false, 'Saving…', 'Save URL');
  }
}

function esc(str) {
  return String(str ?? '').replace(/[&<>'"]/g, tag => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
  }[tag]));
}

function actionButton(label, className, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.className = className;
  button.addEventListener('click', onClick);
  return button;
}

function captureTypeLabel(itemType) {
  if (itemType === 'pdf') return 'PDF';
  if (itemType === 'image') return 'Image';
  if (itemType === 'voice') return 'Voice';
  if (itemType === 'url') return 'URL';
  return 'Item';
}

// Priority and deadline under each note. Times are in India time (IST), as
// sent by the server, so they read the same on every device.
function renderNoteMeta(note) {
  const meta = document.createElement('div');
  meta.className = 'note-details';
  const chip = (text, className) => {
    const span = document.createElement('span');
    span.className = `note-chip ${className}`;
    span.textContent = text;
    meta.append(span);
  };
  if (note.priority) {
    chip(`${note.priority} priority`, `prio prio-${note.priority.toLowerCase()}`);
  }
  if (note.task_status === 'done' && !note.deadline) {
    chip('Done', 'done');
  } else if (note.deadline) {
    const {status, label} = note.deadline;
    const text = status === 'overdue' ? `Overdue \u00b7 was due ${label}`
      : status === 'today' ? `Due today \u00b7 ${label}`
      : status === 'snoozed' ? `Snoozed \u00b7 due ${label}`
      : `Due ${label}`;
    chip(text, `deadline deadline-${status}`);
  } else if (note.priority) {
    chip('No deadline', 'deadline deadline-none');
  }
  return meta;
}

function renderNote(note) {
  const root = document.createElement('div');
  root.className = 'note';
  root.dataset.noteId = String(note.id);

  const text = document.createElement('div');
  text.className = 'note-text';
  if (note.item_type === 'url') {
    const title = note.title || note.source_url || 'Saved URL';
    const body = note.extracted_text || note.raw_text || '';
    text.innerHTML = `<strong>${esc(title)}</strong><div class="muted">${esc(note.source_url || '')}</div>${body ? `<div>${esc(body.slice(0, 280))}${body.length > 280 ? '…' : ''}</div>` : ''}`;
  } else if (note.item_type === 'pdf') {
    const title = note.title || note.original_filename || 'PDF document';
    const body = note.extracted_text || note.raw_text || '';
    const preview = note.storage_path
      ? `<a href="/api/items/${note.id}/file" target="_blank" rel="noopener">Open PDF</a>`
      : '';
    text.innerHTML = `<strong>${esc(title)}</strong><div class="muted">${esc(note.original_filename || '')}${preview ? ` · ${preview}` : ''}</div>${body ? `<div>${esc(body.slice(0, 280))}${body.length > 280 ? '…' : ''}</div>` : ''}`;
  } else if (note.item_type === 'image') {
    const title = note.title || note.original_filename || 'Image';
    const ocr = note.extracted_text || '';
    const ai = note.ai_description || '';
    const thumb = note.storage_path
      ? `<img class="capture-thumb" src="/api/items/${note.id}/file?variant=thumb" alt="Thumbnail for ${esc(title)}" />`
      : '';
    const open = note.storage_path
      ? `<a href="/api/items/${note.id}/file" target="_blank" rel="noopener">Open image</a>`
      : '';
    text.innerHTML = [
      `<strong>${esc(title)}</strong>`,
      `<div class="muted">${esc(note.original_filename || '')}${open ? ` · ${open}` : ''}</div>`,
      thumb,
      `<div class="provenance"><span class="label">OCR (extracted)</span>: ${ocr ? esc(ocr.slice(0, 280)) + (ocr.length > 280 ? '…' : '') : '<em class="muted">none</em>'}</div>`,
      ai ? `<div class="provenance"><span class="label">AI description (inferred)</span>: ${esc(ai.slice(0, 320))}${ai.length > 320 ? '…' : ''}</div>` : ''
    ].join('');
  } else if (note.item_type === 'voice') {
    const title = note.title || note.original_filename || 'Voice note';
    const transcript = note.extracted_text || '';
    const player = note.storage_path
      ? `<audio controls preload="none" src="/api/items/${note.id}/file"></audio>`
      : '';
    text.innerHTML = [
      `<strong>${esc(title)}</strong>`,
      `<div class="muted">${esc(note.original_filename || '')}</div>`,
      player,
      `<div class="provenance"><span class="label">Transcript (extracted)</span>: ${transcript ? esc(transcript.slice(0, 320)) + (transcript.length > 320 ? '…' : '') : '<em class="muted">none yet — edit to add</em>'}</div>`
    ].join('');
  } else {
    text.textContent = note.raw_text;
  }

  // Footer: what it is (topic, priority, type), when it was saved, and actions.
  const meta = document.createElement('div');
  meta.className = 'note-meta';
  const tags = document.createElement('span');
  tags.className = 'note-tags';
  tags.dataset.noteId = String(note.id);
  fillNoteTags(tags, note);
  const time = document.createElement('span');
  time.className = 'time';
  const edited = note.item_type === 'text' && note.updated_at;
  time.textContent = friendlyTime(note.created_at) + (edited ? ' · edited' : '');
  const savedAt = new Date(note.created_at);
  time.title = Number.isNaN(savedAt.getTime())
    ? `Note #${note.id}`
    : (note.saved_label ? `Note #${note.id} · saved ${note.saved_label}` : `Note #${note.id} · saved ${savedAt.toLocaleString()}`);
  meta.append(tags, time);

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  const isCapture = ['url', 'pdf', 'image', 'voice'].includes(note.item_type);
  if (isCapture && note.processing_status === 'failed') {
    actions.append(
      actionButton('Retry', '', async () => {
        try {
          const updated = await fetchJson(
            `/api/items/${note.id}/retry`,
            {method: 'POST'},
            'Unable to retry capture processing.'
          );
          LifeInboxPagination.afterLoadedNoteUpdated(itemPagination, updated);
          root.replaceWith(renderNote(updated));
          const label = captureTypeLabel(note.item_type);
          setStatus(
            'notesStatus',
            updated.processing_status === 'ready'
              ? `${label} processed.`
              : `${label} still failed.`
          );
          await loadLatestAnalysis();
          await loadDashboard();
        } catch {
          setStatus('notesStatus', 'Retry failed.', true);
        }
      })
    );
  }

  // Applied AI feature toolbar on each note
  const featureBar = document.createElement('div');
  featureBar.className = 'note-feature-bar';

  if (note.is_sensitive) {
    const sensBadge = document.createElement('span');
    sensBadge.className = 'feature-badge sensitive';
    sensBadge.textContent = '🔒 Sensitive';
    sensBadge.title = 'Content is never sent to external AI';
    featureBar.appendChild(sensBadge);
  }

  if (note.kind) {
    const kindBadge = document.createElement('span');
    kindBadge.className = `feature-badge ${note.kind}`;
    kindBadge.textContent = `🏷️ ${note.kind.toUpperCase()}`;
    featureBar.appendChild(kindBadge);
  }

  const makeCardBtn = document.createElement('button');
  makeCardBtn.type = 'button';
  makeCardBtn.className = 'note-tool-btn';
  makeCardBtn.textContent = '✨ Smart Card';
  makeCardBtn.title = 'Convert to structured recipe, book, contact, or receipt card';
  makeCardBtn.onclick = async () => {
    makeCardBtn.textContent = 'Formatting…';
    try {
      const res = await fetchJson(`/api/items/${note.id}/card`, {method: 'POST'});
      note.kind = res.kind;
      note.kind_fields = res.kind_fields;
      root.replaceWith(renderNote(note));
      if (window.toast) window.toast(`Converted to ${res.kind} card!`, 'success');
    } catch {
      makeCardBtn.textContent = '✨ Smart Card';
    }
  };

  const extractEntBtn = document.createElement('button');
  extractEntBtn.type = 'button';
  extractEntBtn.className = 'note-tool-btn';
  extractEntBtn.textContent = '👤 Entities';
  extractEntBtn.title = 'Extract people, places, and organizations mentioned';
  extractEntBtn.onclick = async () => {
    extractEntBtn.textContent = 'Scanning…';
    try {
      const res = await fetchJson(`/api/items/${note.id}/entities`, {method: 'POST'});
      extractEntBtn.textContent = `👤 ${res.length} Entities`;
      if (window.toast) window.toast(`Extracted ${res.length} entities! Check 05 Entities Treemap tab.`, 'success');
    } catch {
      extractEntBtn.textContent = '👤 Entities';
    }
  };

  const sensBtn = document.createElement('button');
  sensBtn.type = 'button';
  sensBtn.className = `note-tool-btn ${note.is_sensitive ? 'active' : ''}`;
  sensBtn.textContent = note.is_sensitive ? '🔒 Private (Redacted)' : '🔓 Mark Sensitive';
  sensBtn.title = 'Toggle sensitive flag to bypass external AI';
  sensBtn.onclick = async () => {
    try {
      const updated = await fetchJson(`/api/items/${note.id}/sensitive`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sensitive: !note.is_sensitive})
      });
      note.is_sensitive = updated.is_sensitive;
      root.replaceWith(renderNote(note));
    } catch (e) {
      console.error(e);
    }
  };

  featureBar.append(makeCardBtn, extractEntBtn, sensBtn);

  let editButton = null;
  if (note.item_type === 'text') {
    editButton = actionButton('Edit', 'secondary', () => {
      showEditNote(note, root);
    });
  } else if (
    (note.item_type === 'image' || note.item_type === 'voice') &&
    note.processing_status === 'ready'
  ) {
    editButton = actionButton(
      note.item_type === 'voice' ? 'Edit transcript' : 'Edit OCR text',
      'secondary',
      () => {
        showEditExtractedText(note, root);
      }
    );
  }
  if (editButton) actions.append(editButton);

  const deleteButton = actionButton('Delete', 'danger', () => {
    deleteExistingNote(note, root, editButton, deleteButton);
  });
  actions.append(deleteButton);

  const foot = document.createElement('div');
  foot.className = 'note-foot';
  foot.append(meta, actions);

  root.append(text);
  if (note.error_message) {
    const error = document.createElement('div');
    error.className = 'note-error';
    error.textContent = note.error_message;
    root.append(error);
  }
  root.append(renderNoteMeta(note), featureBar, foot);
  return root;
}

function fillNoteTags(root, note) {
  root.replaceChildren();
  const add = (label, kind) => {
    const tag = document.createElement('span');
    tag.className = `tag tag-${kind}`;
    tag.textContent = label;
    root.appendChild(tag);
  };
  if (note.item_type !== 'text') add(captureTypeLabel(note.item_type), 'type');
  if (note.processing_status === 'failed') {
    add('Failed', 'failed');
  } else if (note.processing_status && note.processing_status !== 'ready') {
    add('Processing', 'busy');
  }
  const insight = noteInsights.get(note.id);
  if (insight) {
    add(insight.category, 'topic');
  }
}

// After notes are organized, update the priority and deadline of the notes on
// screen without reloading the list (keeps "Load more" pages and scroll).
async function refreshNoteDetails() {
  const cards = [...document.querySelectorAll('#notes .note[data-note-id]')];
  if (!cards.length) return;
  const params = itemFilterParams();
  params.set('limit', String(Math.min(500, Math.max(cards.length, itemPagination.loadedItems.length || 0, 1))));
  params.set('offset', '0');
  let notes;
  try {
    notes = await fetchJson(`/api/items?${params}`, undefined, 'Unable to load notes.');
  } catch {
    return;  // the cards keep what they show
  }
  const byId = new Map(notes.map(note => [String(note.id), note]));
  for (const card of cards) {
    const fresh = byId.get(card.dataset.noteId);
    const meta = card.querySelector(':scope > .note-details');
    if (fresh && meta) meta.replaceWith(renderNoteMeta(fresh));
  }
}

function showEditExtractedText(note, root) {
  if (pendingNoteActions.has(note.id)) return;

  const textarea = document.createElement('textarea');
  textarea.className = 'note-edit-input';
  textarea.maxLength = 20000;
  textarea.value = note.extracted_text || '';

  const hint = document.createElement('div');
  hint.className = 'provenance';
  const isVoice = note.item_type === 'voice';
  hint.innerHTML = isVoice
    ? '<span class="label">Editing transcript</span> (extracted text from the original audio).'
    : '<span class="label">Editing extracted / OCR text</span> (facts). AI description stays separate.';

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  const saveButton = actionButton(isVoice ? 'Save transcript' : 'Save OCR text', '', async () => {
    if (pendingNoteActions.has(note.id)) return;
    pendingNoteActions.add(note.id);
    saveButton.disabled = true;
    cancelButton.disabled = true;
    saveButton.textContent = 'Saving…';
    setStatus('notesStatus', 'Saving extracted text…');
    try {
      const updated = await fetchJson(
        `/api/items/${note.id}/extracted-text`,
        {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({extracted_text: textarea.value})
        },
        'Unable to update extracted text.'
      );
      root.replaceWith(renderNote(updated));
      setStatus('notesStatus', isVoice ? 'Transcript updated.' : 'Extracted text updated.');
      await loadLatestAnalysis();
    } catch {
      setStatus('notesStatus', 'Could not update extracted text.', true);
      saveButton.disabled = false;
      cancelButton.disabled = false;
      saveButton.textContent = isVoice ? 'Save transcript' : 'Save OCR text';
    } finally {
      pendingNoteActions.delete(note.id);
    }
  });

  const cancelButton = actionButton('Cancel', 'secondary', () => {
    if (pendingNoteActions.has(note.id)) return;
    root.replaceWith(renderNote(note));
  });

  actions.append(saveButton, cancelButton);
  root.replaceChildren(hint, textarea, actions);
}

function showEditNote(note, root) {
  if (pendingNoteActions.has(note.id)) return;

  const textarea = document.createElement('textarea');
  textarea.className = 'note-edit-input';
  textarea.maxLength = 2000;
  textarea.value = note.raw_text;

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  const saveButton = actionButton('Save', '', async () => {
    const text = textarea.value.trim();
    if (!text) {
      setStatus('notesStatus', 'Edited note text cannot be empty.', true);
      textarea.focus();
      return;
    }
    if (pendingNoteActions.has(note.id)) return;

    pendingNoteActions.add(note.id);
    saveButton.disabled = true;
    cancelButton.disabled = true;
    saveButton.textContent = 'Saving…';
    setStatus('notesStatus', 'Saving your changes…');

    try {
      const updatedNote = await fetchJson(
        `/api/items/${note.id}`,
        {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({raw_text: text})
        },
        'Unable to update note.'
      );
      LifeInboxPagination.afterLoadedNoteUpdated(itemPagination, updatedNote);
      root.replaceWith(renderNote(updatedNote));
      setStatus('notesStatus', 'Note updated.');
      await loadLatestAnalysis();
    } catch {
      setStatus(
        'notesStatus',
        'We could not update this note. Your changes are still here—please try again.',
        true
      );
      saveButton.disabled = false;
      cancelButton.disabled = false;
      saveButton.textContent = 'Save';
    } finally {
      pendingNoteActions.delete(note.id);
    }
  });

  const cancelButton = actionButton('Cancel', 'secondary', () => {
    if (pendingNoteActions.has(note.id)) return;
    root.replaceWith(renderNote(note));
    setStatus('notesStatus', 'Edit cancelled.');
  });

  actions.append(saveButton, cancelButton);
  root.replaceChildren(textarea, actions);
  textarea.focus();
}

// Deleting asks once, in place: the card's buttons become
// "Delete this note?  [Delete] [Cancel]" instead of a browser pop-up.
function deleteExistingNote(note, root, editButton, deleteButton) {
  if (pendingNoteActions.has(note.id)) return;
  const actions = deleteButton.parentElement;
  if (!actions || actions.querySelector('.confirm-delete')) return;

  const others = Array.from(actions.children);
  const confirmRow = document.createElement('span');
  confirmRow.className = 'confirm-delete';
  const question = document.createElement('span');
  question.className = 'confirm-question';
  question.textContent = 'Delete this note?';

  const restore = () => {
    confirmRow.remove();
    others.forEach(button => { button.hidden = false; });
  };
  const cancelButton = actionButton('Cancel', 'secondary', () => {
    if (pendingNoteActions.has(note.id)) return;
    restore();
    deleteButton.focus();
  });
  const confirmButton = actionButton('Delete', 'danger', async () => {
    if (pendingNoteActions.has(note.id)) return;
    pendingNoteActions.add(note.id);
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    confirmButton.textContent = 'Deleting…';
    setStatus('notesStatus', 'Deleting note…');

    try {
      await fetchJson(
        `/api/items/${note.id}`,
        {method: 'DELETE'},
        'Unable to delete note.'
      );
      LifeInboxPagination.afterLoadedNoteDeleted(itemPagination, note.id);
      root.remove();
      setStatus('notesStatus', 'Note deleted.');
      updateNotesSummary();
      showEmptyNotesIfNeeded();
      await loadLatestAnalysis('quiet');
      // The note's tasks and topic counts go with it.
      await Promise.allSettled([loadTopics(), loadDashboard()]);
    } catch {
      setStatus(
        'notesStatus',
        'We could not delete this note. It is still here—please try again.',
        true
      );
      restore();
    } finally {
      pendingNoteActions.delete(note.id);
    }
  });

  others.forEach(button => { button.hidden = true; });
  confirmRow.append(question, confirmButton, cancelButton);
  actions.append(confirmRow);
  cancelButton.focus();
}

// New notes are analyzed automatically by the server right after they are
// saved. These helpers wait for that to finish, then refresh every view that
// shows analysis results (tasks, topics, dashboard, attention, filters).
async function refreshAfterAnalysis() {
  try {
    const data = await fetchJson(
      '/api/analyze/latest',
      undefined,
      'Unable to load saved analysis.'
    );
    if (data) populateCategoryFilter(data);
  } catch {
    // The category filter keeps its current options.
  }
  await Promise.allSettled([
    loadTopics(),
    loadDashboard(),
    refreshNoteDetails(),
  ]);
}

async function waitForAutoAnalysis(force) {
  let sawPending = false;
  let idlePolls = 0;
  for (let attempt = 0; attempt < 120; attempt++) {
    let state;
    try {
      state = await fetchJson(
        '/api/analyze/pending',
        undefined,
        'Unable to check for new notes.'
      );
    } catch {
      await sleep(2000);
      continue;
    }

    if (state.pending_count === 0) {
      if (sawPending || force) await refreshAfterAnalysis();
      if (sawPending) setStatus('noteStatus', 'Organized with AI.');
      return;
    }

    if (!sawPending) {
      setStatus('noteStatus', 'Organizing your new note with AI…');
    }
    sawPending = true;

    // If nothing is working on the pending notes (for example a background
    // run failed), ask the server to analyze them now so none are left behind.
    idlePolls = state.analyzing ? 0 : idlePolls + 1;
    if (idlePolls >= 2) {
      idlePolls = 0;
      try {
        await fetchJson(
          '/api/analyze?scope=new',
          {method: 'POST'},
          'Unable to analyze new notes.'
        );
      } catch {
        // Retried on the next poll.
      }
    }
    await sleep(1500);
  }
  setStatus(
    'noteStatus',
    'Your notes are saved but not organized yet. They will be analyzed automatically shortly.',
    true
  );
}

async function watchAnalysis(force) {
  if (isWatchingAnalysis) {
    rewatchAnalysis = rewatchAnalysis || force;
    return;
  }
  isWatchingAnalysis = true;
  try {
    let needed = force;
    do {
      rewatchAnalysis = false;
      await waitForAutoAnalysis(needed);
      needed = rewatchAnalysis;
    } while (rewatchAnalysis);
  } finally {
    isWatchingAnalysis = false;
  }
}

// mode 'saved': a note was just saved, so wait for its analysis and refresh.
// mode 'quiet': only refresh the category filter (and rescue leftovers).
async function loadLatestAnalysis(mode = 'saved') {
  try {
    const data = await fetchJson(
      '/api/analyze/latest',
      undefined,
      'Unable to load saved analysis.'
    );
    if (data) populateCategoryFilter(data);
  } catch {
    // The category filter keeps its current options.
  }
  // Not awaited: saving must stay instant while the analysis runs behind it.
  watchAnalysis(mode === 'saved').catch(() => {});
}

const itemPagination = LifeInboxPagination.createState(
  LifeInboxPagination.PAGE_SIZE
);

function updateLoadMoreButton(failureMessage) {
  const existingMore = document.getElementById('loadMoreItems');
  if (existingMore) existingMore.remove();
  if (!itemPagination.hasMore) return;

  const root = document.getElementById('notes');
  const more = document.createElement('button');
  more.id = 'loadMoreItems';
  more.type = 'button';
  more.className = 'secondary';
  more.textContent = itemPagination.listRequestInFlight
    ? 'Loading…'
    : 'Load more';
  more.disabled = itemPagination.listRequestInFlight;
  more.setAttribute(
    'aria-busy',
    String(itemPagination.listRequestInFlight)
  );
  more.addEventListener('click', () => {
    if (itemPagination.listRequestInFlight) return;
    loadNotes(failureMessage, {append: true});
  });
  root.appendChild(more);
}

function notesEmptyState(filtered) {
  return filtered
    ? '<div class="empty-state"><strong>Nothing matches those filters.</strong><span>Try a different word, or clear the filters.</span></div>'
    : '<div class="empty-state"><strong>No notes yet.</strong><span>Capture a thought and it shows up here, sorted and ready to search.</span><button type="button" class="secondary" data-goto="capture">Capture a note</button></div>';
}

// The line under the "Notes" heading: how many notes are showing.
function updateNotesSummary() {
  const summary = document.getElementById('notesSummary');
  if (!summary) return;
  const count = itemPagination.loadedItems.length;
  const shown = `${count}${itemPagination.hasMore ? '+' : ''}`;
  if (hasItemFilters()) {
    summary.textContent = count
      ? `${shown} ${count === 1 ? 'note matches' : 'notes match'} your filters.`
      : 'No notes match your filters.';
  } else {
    summary.textContent = count
      ? `${shown} ${count === 1 ? 'note' : 'notes'}, newest first.`
      : 'Everything you capture shows up here, newest first.';
  }
}

function showEmptyNotesIfNeeded() {
  const root = document.getElementById('notes');
  if (root.querySelector('.note') || itemPagination.hasMore) return;
  root.innerHTML = notesEmptyState(hasItemFilters());
}

async function loadNotes(
  failureMessage = 'We could not load your notes. Reload to try again.',
  {append = false, statusId = 'notesStatus'} = {}
) {
  if (!LifeInboxPagination.beginListRequest(itemPagination)) {
    // A reload asked for while another list request is running is not lost:
    // it runs as soon as that request finishes.
    if (!append) notesReloadQueued = true;
    return false;
  }

  const params = itemFilterParams();
  const filtered = params.size > 0;
  if (!append) {
    LifeInboxPagination.resetForReload(itemPagination);
  }
  params.set('sort', 'newest');
  params.set('limit', String(itemPagination.pageSize));
  params.set('offset', String(itemPagination.loadedOffset));
  const query = params.toString();

  if (append) {
    updateLoadMoreButton(failureMessage);
  }

  try {
    const notes = await fetchJson(
      `/api/items?${query}`,
      undefined,
      'Unable to load notes.'
    );
    const root = document.getElementById('notes');
    if (!append) {
      root.innerHTML = '';
    } else {
      const existingMore = document.getElementById('loadMoreItems');
      if (existingMore) existingMore.remove();
    }
    setStatus('searchStatus', '');

    if (!append && !notes.length) {
      root.innerHTML = notesEmptyState(filtered);
      updateNotesSummary();
      return true;
    }

    const {items, hasMore} = LifeInboxPagination.applyFetchedPage(
      itemPagination,
      notes,
      {append}
    );

    for (const note of items) {
      root.appendChild(renderNote(note));
    }

    if (hasMore) {
      updateLoadMoreButton(failureMessage);
    }
    updateNotesSummary();
    return true;
  } catch {
    if (append) {
      setStatus(
        'notesStatus',
        'We could not load more notes. Your current notes are still here—please try again.',
        true
      );
      updateLoadMoreButton(failureMessage);
    } else if (filtered) {
      setStatus(
        'searchStatus',
        'Search failed. Please try again.',
        true
      );
    } else {
      setStatus(statusId, failureMessage, true);
    }
    return false;
  } finally {
    LifeInboxPagination.endListRequest(itemPagination);
    if (itemPagination.hasMore) {
      updateLoadMoreButton(failureMessage);
    }
    if (notesReloadQueued) {
      notesReloadQueued = false;
      loadNotes(failureMessage, {statusId});
    }
  }
}

const AUTO_TOPIC_NOTE = 'Created from AI organization';

async function loadTopics() {
  try {
    const topics = await fetchJson(
      '/api/topics',
      undefined,
      'Unable to load topics.'
    );
    const root = document.getElementById('topics');
    root.innerHTML = '';
    if (!topics.length) {
      root.innerHTML = '<div class="panel muted">No topics yet.</div>';
      return;
    }
    // Topics that hold notes come first; empty ones stay visible but quiet.
    const ordered = [...topics].sort(
      (first, second) =>
        second.item_count - first.item_count
        || first.name.localeCompare(second.name)
    );
    const most = Math.max(1, ...ordered.map(topic => topic.item_count));
    for (const topic of ordered) {
      const hasNotes = topic.item_count > 0;
      const row = document.createElement('div');
      row.className = hasNotes ? 'topic' : 'topic topic-empty';
      const count = `${topic.item_count} ${topic.item_count === 1 ? 'note' : 'notes'}`;
      const description =
        topic.description && topic.description !== AUTO_TOPIC_NOTE
          ? ` · ${esc(topic.description)}`
          : '';
      const share = Math.round((topic.item_count / most) * 100);
      row.innerHTML = `<strong>${esc(topic.name)}</strong><div class="muted">${count}${description}</div><span class="topic-bar" aria-hidden="true"><i style="width: ${share}%"></i></span>`;
      if (hasNotes) {
        // Opens the Notes tab (data-goto, handled in ui.js) filtered to this topic.
        row.setAttribute('role', 'button');
        row.tabIndex = 0;
        row.dataset.goto = 'notes';
        row.title = `Show ${topic.name} notes`;
        row.addEventListener('click', () => showTopicNotes(topic.name));
        row.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            row.click();
          }
        });
      }
      root.appendChild(row);
    }
  } catch {
    setStatus('topicStatus', 'We could not load topics.', true);
  }
}

async function createTopic(event) {
  event.preventDefault();
  const input = document.getElementById('topicName');
  const name = input.value.trim();
  if (!name) return;
  try {
    await fetchJson(
      '/api/topics',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name})
      },
      'Unable to create topic.'
    );
    input.value = '';
    setStatus('topicStatus', 'Topic added.');
    await loadTopics();
    await loadDashboard();
  } catch {
    setStatus('topicStatus', 'Could not create that topic. It may already exist.', true);
  }
}

// ---------- Life Inbox Calendar Dashboard State & Handlers ----------
let calCurrentYear = 2026;
let calCurrentMonth = 10; // 0-indexed: 10 = November
let calTodayDay = 12;     // Nov 12, 2026
let calSelectedDay = 12;  // Default to today

const defaultPinnedNotes = [
  { id: 'pin-1', title: 'Wi-Fi details', detail: 'Network: LifeInbox_5G · Pass: stayorganized' },
  { id: 'pin-2', title: 'Gym routine', detail: 'Mon: Push · Wed: Pull · Fri: Legs' }
];

// Unified reactive dashboard state
let dashState = {
  todayTasks: [],
  overdueTasks: [],
  upcomingTasks: [],
  agenda: {}
};

async function loadDashboard() {
  try {
    // 1. Fetch dashboard and attention data in parallel
    const [dashData, attentionData] = await Promise.allSettled([
      fetchJson('/api/dashboard', undefined, 'Unable to load dashboard.'),
      fetchJson('/api/attention', undefined, 'Unable to load attention.')
    ]);

    const data = dashData.status === 'fulfilled' ? dashData.value : {};
    const attention = attentionData.status === 'fulfilled' ? attentionData.value : {};

    if (data.pending_analysis_count > 0) watchAnalysis(false);

    // 2. Setup Today Tasks
    const rawTodayTasks = (attention.today && attention.today.length) 
      ? attention.today 
      : (data.open_tasks && data.open_tasks.slice(0, 3).length) 
        ? data.open_tasks.slice(0, 3) 
        : [
            { id: 'demo-today-1', text: 'Pay bill', time: '11:00 AM' },
            { id: 'demo-today-2', text: 'Call the bank', time: '2:30 PM' },
            { id: 'demo-today-3', text: 'Submit assignment', time: '5:00 PM' }
          ];

    dashState.todayTasks = rawTodayTasks.map(t => ({
      id: String(t.id),
      text: t.text || t.title || 'Task',
      time: t.time || 'Today',
      done: Boolean(t.done || t.status === 'done' || t.status === 'completed')
    }));

    // 3. Setup Overdue Tasks
    const rawOverdueTasks = (attention.overdue && attention.overdue.length)
      ? attention.overdue
      : [
          { id: 'demo-overdue-1', text: 'Renew insurance', priority: 'High', time: 'Overdue' },
          { id: 'demo-overdue-2', text: 'Reply to Amit', priority: 'High', time: 'Overdue' }
        ];

    dashState.overdueTasks = rawOverdueTasks.map(t => ({
      id: String(t.id),
      text: t.text || t.title || 'Task',
      time: t.time || 'Overdue',
      done: Boolean(t.done || t.status === 'done' || t.status === 'completed')
    }));

    // 4. Setup Upcoming Tasks
    dashState.upcomingTasks = [
      { id: 'demo-up-1', day: 'Sat', dayNum: 14, text: 'Dentist visit', time: '10:00 AM', done: false },
      { id: 'demo-up-2', day: 'Mon', dayNum: 16, text: "Mom's birthday", time: 'All day', done: false },
      { id: 'demo-up-3', day: 'Tue', dayNum: 17, text: 'Rent due', time: '9:00 AM', done: false }
    ];

    // 5. Connect Calendar Agenda with shared task models
    dashState.agenda = {
      10: [
        dashState.overdueTasks[0]
      ].filter(Boolean),
      11: [
        dashState.overdueTasks[1]
      ].filter(Boolean),
      12: [
        ...dashState.todayTasks
      ],
      14: [
        dashState.upcomingTasks[0],
        { id: 'demo-cal-14-2', text: 'Pay rent', time: '6:00 PM', done: false }
      ].filter(Boolean),
      16: [
        dashState.upcomingTasks[1]
      ].filter(Boolean),
      17: [
        dashState.upcomingTasks[2]
      ].filter(Boolean)
    };

    // Render left-side task lists
    renderDueTodayList();
    renderOverdueList();

    // 6. Populate Coming up
    const upcomingEl = document.getElementById('upcomingList');
    if (upcomingEl) {
      if (!dashState.upcomingTasks || dashState.upcomingTasks.length === 0) {
        upcomingEl.innerHTML = `
          <li class="cal-empty-task-card">
            <span class="cal-empty-icon">📅</span>
            <div class="cal-empty-text">
              <strong>No upcoming tasks</strong>
              <span>Schedule future tasks in the calendar.</span>
            </div>
          </li>
        `;
      } else {
        upcomingEl.innerHTML = dashState.upcomingTasks.map(item => `
          <li class="cal-upcoming-item" id="upcoming-${item.id}">
            <span class="cal-day-tag mono">${esc(item.day || 'Soon')}</span>
            <span class="cal-upcoming-text">${esc(item.text)}</span>
          </li>
        `).join('');
      }
    }

    // 7. Populate Pinned notes
    const pinnedEl = document.getElementById('pinnedList');
    let pins = defaultPinnedNotes;
    try {
      const stored = window.localStorage.getItem('lifeInbox.pinned');
      if (stored) pins = JSON.parse(stored);
    } catch {}

    if (pinnedEl) {
      if (!pins || pins.length === 0) {
        pinnedEl.innerHTML = `
          <li class="cal-empty-task-card">
            <span class="cal-empty-icon">📌</span>
            <div class="cal-empty-text">
              <strong>No pinned notes yet</strong>
              <span>Click + above to pin quick thoughts here.</span>
            </div>
          </li>
        `;
      } else {
        pinnedEl.innerHTML = pins.map((p, idx) => `
          <li class="cal-pinned-item" title="${esc(p.detail || '')}">
            <span class="cal-star">★</span>
            <span class="cal-pinned-title">${esc(p.title)}</span>
            <button type="button" class="cal-pin-del" onclick="removePin(${idx})" title="Unpin">×</button>
          </li>
        `).join('');
      }
    }

    // 8. Populate Latest notes
    const latestEl = document.getElementById('latestNotesList');
    const rawNotes = (data.recently_captured && data.recently_captured.length)
      ? data.recently_captured
      : [
          { id: 'n1', text: 'Idea for the project demo', timeAgo: '2 min ago' },
          { id: 'n2', text: 'Spent ₹450 on groceries', timeAgo: 'Today' }
        ];

    if (latestEl) {
      latestEl.innerHTML = rawNotes.slice(0, 4).map(n => `
        <li class="cal-note-row" data-goto="notes">
          <div class="cal-note-left">
            <span class="cal-dot">•</span>
            <span class="cal-note-txt">${esc(n.text || n.title || n.raw_text || ('Note #' + n.id))}</span>
          </div>
          <span class="cal-note-time mono">${esc(n.timeAgo || 'Recently')}</span>
        </li>
      `).join('');
    }

    // 9. Render Calendar and Day Agenda
    renderCalendar();

    setStatus('dashboardStatus', 'Dashboard synchronized.');
  } catch (err) {
    console.error(err);
    setStatus('dashboardStatus', 'We could not load the dashboard.', true);
  } finally {
    loadNotifications();
  }
}

function renderDueTodayList() {
  const dueTodayEl = document.getElementById('dueTodayList');
  if (!dueTodayEl) return;
  if (!dashState.todayTasks || dashState.todayTasks.length === 0) {
    dueTodayEl.innerHTML = `
      <li class="cal-empty-task-card celebration">
        <span class="cal-empty-icon">✨</span>
        <div class="cal-empty-text">
          <strong>All clear for today</strong>
          <span>No pending tasks due today. Great job!</span>
        </div>
      </li>
    `;
  } else {
    dueTodayEl.innerHTML = dashState.todayTasks.map(task => `
      <li class="cal-task-item ${task.done ? 'completed' : ''}" id="task-${task.id}">
        <label class="cal-checkbox-label">
          <input type="checkbox" data-task-id="${task.id}" ${task.done ? 'checked' : ''} onchange="toggleTask('${task.id}', event.target.checked)" />
          <span class="cal-custom-box"></span>
          <span class="cal-task-name">${esc(task.text)}</span>
        </label>
      </li>
    `).join('');
  }
  updateDueTodayCount();
}

function updateDueTodayCount() {
  const remaining = dashState.todayTasks.filter(t => !t.done).length;
  const todayBadge = document.getElementById('todayCountBadge');
  if (todayBadge) {
    todayBadge.textContent = `(${remaining})`;
    todayBadge.classList.toggle('all-done', remaining === 0);
  }
}

function renderOverdueList() {
  const overdueEl = document.getElementById('overdueList');
  if (!overdueEl) return;
  if (!dashState.overdueTasks || dashState.overdueTasks.length === 0) {
    overdueEl.innerHTML = `
      <li class="cal-empty-task-card clean">
        <span class="cal-empty-icon">🎉</span>
        <div class="cal-empty-text">
          <strong>No overdue tasks</strong>
          <span>You're completely caught up!</span>
        </div>
      </li>
    `;
  } else {
    overdueEl.innerHTML = dashState.overdueTasks.map(task => `
      <li class="cal-task-item overdue ${task.done ? 'completed' : ''}" id="task-${task.id}">
        <label class="cal-checkbox-label">
          <input type="checkbox" data-task-id="${task.id}" ${task.done ? 'checked' : ''} onchange="toggleTask('${task.id}', event.target.checked)" />
          <span class="cal-custom-box rose"></span>
          <span class="cal-task-name">${esc(task.text)}</span>
        </label>
      </li>
    `).join('');
  }
  updateOverdueCount();
}

function updateOverdueCount() {
  const remaining = dashState.overdueTasks.filter(t => !t.done).length;
  const overdueBadge = document.getElementById('overdueCountBadge');
  if (overdueBadge) {
    overdueBadge.textContent = `(${remaining})`;
    overdueBadge.classList.toggle('all-done', remaining === 0);
  }
}

// Checkbox interactive toggler — fully bidirectional across 2x2 grid and calendar
async function toggleTask(taskId, isChecked) {
  // Update state in all collections
  for (const t of dashState.todayTasks) {
    if (t.id === taskId) t.done = isChecked;
  }
  for (const t of dashState.overdueTasks) {
    if (t.id === taskId) t.done = isChecked;
  }
  for (const t of dashState.upcomingTasks) {
    if (t.id === taskId) t.done = isChecked;
  }
  for (const day in dashState.agenda) {
    for (const t of dashState.agenda[day]) {
      if (t.id === taskId) t.done = isChecked;
    }
  }

  // Synchronize all DOM checkboxes with this ID
  document.querySelectorAll(`input[type="checkbox"][data-task-id="${taskId}"]`).forEach(cb => {
    cb.checked = isChecked;
  });

  // Toggle list item visual state
  const taskEl = document.getElementById(`task-${taskId}`);
  if (taskEl) taskEl.classList.toggle('completed', isChecked);

  const agendaEl = document.getElementById(`agenda-task-${taskId}`);
  if (agendaEl) agendaEl.classList.toggle('done', isChecked);

  // Recalculate badge counts
  updateDueTodayCount();
  updateOverdueCount();
  updateSelectedDayPending();
  renderCalendarDaysGrid();

  // Audio feedback
  if (typeof playSound === 'function') {
    playSound(isChecked ? 'ping' : 'click');
  }

  // Backend persistence if real database task
  if (!String(taskId).startsWith('demo-') && isChecked) {
    try {
      await fetch(`/api/tasks/${taskId}/complete`, { method: 'POST' });
    } catch {}
  }
}

// Legacy compatibility aliases
function toggleTaskCheck(taskId, event) {
  toggleTask(taskId, event.target.checked);
}
function toggleAgendaTask(taskId, event) {
  toggleTask(taskId, event.target.checked);
}

// Quick Add Note
async function dashAddNote(event) {
  event.preventDefault();
  const input = document.getElementById('calNoteInput');
  const btn = document.getElementById('calNoteAddBtn');
  const text = input ? input.value.trim() : '';
  if (!text) return;

  btn.disabled = true;
  btn.textContent = '...';

  try {
    await fetchJson(
      '/api/items',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_text: text })
      },
      'Unable to save note.'
    );

    input.value = '';
    setStatus('dashboardStatus', 'Note added.');

    // Prepend to Latest notes stream immediately
    const latestEl = document.getElementById('latestNotesList');
    if (latestEl) {
      const newRow = document.createElement('li');
      newRow.className = 'cal-note-row new-entry';
      newRow.innerHTML = `
        <div class="cal-note-left">
          <span class="cal-dot">•</span>
          <span class="cal-note-txt">${esc(text)}</span>
        </div>
        <span class="cal-note-time mono">Just now</span>
      `;
      latestEl.prepend(newRow);
    }
  } catch (err) {
    setStatus('dashboardStatus', 'Failed to add note.', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Add';
  }
}

// Quick Search / Ask
async function dashSearchAsk(event) {
  event.preventDefault();
  const input = document.getElementById('calSearchInput');
  const btn = document.getElementById('calSearchBtn');
  const drawer = document.getElementById('calAskDrawer');
  const content = document.getElementById('calAskContent');
  const query = input ? input.value.trim() : '';
  if (!query) return;

  btn.disabled = true;
  btn.textContent = '...';

  try {
    const res = await fetchJson(
      '/api/ask',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: query })
      },
      'Unable to answer question.'
    );

    drawer.hidden = false;
    content.innerHTML = `
      <p class="cal-answer-query"><strong>Q: ${esc(query)}</strong></p>
      <div class="cal-answer-text">${esc(res.answer || 'No grounded answer found in inbox.')}</div>
      ${res.cited_items && res.cited_items.length ? `
        <div class="cal-answer-citations mono">
          <span>Sources:</span>
          ${res.cited_items.map(id => `<span class="dash-tag">#${id}</span>`).join(' ')}
        </div>
      ` : ''}
    `;
    setStatus('dashboardStatus', 'Answer ready.');
  } catch (err) {
    drawer.hidden = false;
    content.innerHTML = `<p class="muted error">${esc(err.message || 'Error querying notes.')}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Ask ↵';
  }
}

function closeCalAskDrawer() {
  const drawer = document.getElementById('calAskDrawer');
  if (drawer) drawer.hidden = true;
}

// Pinning notes — non-blocking inline input
function promptAddPin(directTitle, directDetail) {
  if (directTitle) {
    savePinData(directTitle, directDetail || '');
    return;
  }
  const pinnedList = document.getElementById('pinnedList');
  if (!pinnedList) return;

  const existingForm = document.getElementById('calPinInlineForm');
  if (existingForm) {
    const titleInput = document.getElementById('calPinTitleInput');
    if (titleInput) titleInput.focus();
    return;
  }

  const inlineForm = document.createElement('li');
  inlineForm.id = 'calPinInlineForm';
  inlineForm.className = 'cal-pin-inline-form';
  inlineForm.innerHTML = `
    <input type="text" id="calPinTitleInput" placeholder="Note title to pin..." class="cal-pin-input" />
    <input type="text" id="calPinDetailInput" placeholder="Optional details / notes..." class="cal-pin-input" />
    <div class="cal-pin-inline-actions">
      <button type="button" class="secondary chip" onclick="cancelAddPin()">Cancel</button>
      <button type="button" class="primary chip" onclick="saveInlinePin()">Pin Note</button>
    </div>
  `;
  pinnedList.prepend(inlineForm);

  const titleInput = document.getElementById('calPinTitleInput');
  const detailInput = document.getElementById('calPinDetailInput');
  if (titleInput) {
    titleInput.focus();
    titleInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') saveInlinePin();
      if (e.key === 'Escape') cancelAddPin();
    });
  }
  if (detailInput) {
    detailInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') saveInlinePin();
      if (e.key === 'Escape') cancelAddPin();
    });
  }
}

function cancelAddPin() {
  const inlineForm = document.getElementById('calPinInlineForm');
  if (inlineForm) inlineForm.remove();
}

function saveInlinePin() {
  const titleInput = document.getElementById('calPinTitleInput');
  const detailInput = document.getElementById('calPinDetailInput');
  const title = titleInput ? titleInput.value.trim() : '';
  const detail = detailInput ? detailInput.value.trim() : '';

  if (!title) {
    if (window.toast) window.toast('Please enter a note title to pin', 'warn');
    if (titleInput) titleInput.focus();
    return;
  }

  savePinData(title, detail);
  cancelAddPin();
}

function savePinData(title, detail) {
  let pins = defaultPinnedNotes;
  try {
    const stored = window.localStorage.getItem('lifeInbox.pinned');
    if (stored) pins = JSON.parse(stored);
  } catch {}

  pins.push({ id: 'pin-' + Date.now(), title: title, detail: detail });
  try {
    window.localStorage.setItem('lifeInbox.pinned', JSON.stringify(pins));
  } catch {}

  loadDashboard();
  if (window.toast) window.toast('Note pinned to dashboard!', 'success');
}

function removePin(index) {
  let pins = defaultPinnedNotes;
  try {
    const stored = window.localStorage.getItem('lifeInbox.pinned');
    if (stored) pins = JSON.parse(stored);
  } catch {}

  pins.splice(index, 1);
  try {
    window.localStorage.setItem('lifeInbox.pinned', JSON.stringify(pins));
  } catch {}

  loadDashboard();
}

// Right Calendar Sidebar Logic
const monthNames = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];

function jumpToToday() {
  calCurrentYear = 2026;
  calCurrentMonth = 10; // November
  calSelectedDay = calTodayDay;
  renderCalendar();
  if (typeof playSound === 'function') playSound('click');
}

function navMonth(dir) {
  calCurrentMonth += dir;
  if (calCurrentMonth < 0) {
    calCurrentMonth = 11;
    calCurrentYear -= 1;
  } else if (calCurrentMonth > 11) {
    calCurrentMonth = 0;
    calCurrentYear += 1;
  }
  renderCalendar();
}

function selectCalTab(tab) {
  if (tab === 'home') jumpToToday();
}

function selectCalDay(day) {
  calSelectedDay = day;
  renderCalendar();
}

function renderCalendar() {
  const monthTitle = document.getElementById('calMonthTitle');
  if (monthTitle) {
    monthTitle.textContent = `${monthNames[calCurrentMonth]} ${calCurrentYear}`;
  }
  renderCalendarDaysGrid();
  renderSelectedDayAgenda();
}

function renderCalendarDaysGrid() {
  const daysGrid = document.getElementById('calDaysGrid');
  if (!daysGrid) return;

  // First day of month (0 = Sun, 1 = Mon, ..., 6 = Sat)
  const firstDay = new Date(calCurrentYear, calCurrentMonth, 1).getDay();
  // Monday-based (0 = Mon, ..., 6 = Sun)
  const startingCol = (firstDay + 6) % 7;
  const totalDays = new Date(calCurrentYear, calCurrentMonth + 1, 0).getDate();

  let html = '';

  // Leading empty dots
  for (let i = 0; i < startingCol; i++) {
    html += `<span class="cal-empty-dot">·</span>`;
  }

  // Days
  for (let d = 1; d <= totalDays; d++) {
    const isSelected = (d === calSelectedDay);
    const isToday = (d === calTodayDay && calCurrentMonth === 10 && calCurrentYear === 2026);
    const items = dashState.agenda[d] || [];
    
    let badgeHtml = '';
    if (items.length > 0) {
      const pendingCount = items.filter(t => !t.done).length;
      if (pendingCount > 0) {
        badgeHtml = `<span class="cal-day-badge-pip pending">${pendingCount}</span>`;
      } else {
        badgeHtml = `<span class="cal-day-badge-pip all-done">✓</span>`;
      }
    }

    html += `
      <button type="button" 
        class="cal-day-cell ${isSelected ? 'selected' : ''} ${isToday ? 'is-today' : ''}" 
        onclick="selectCalDay(${d})"
        title="Day ${d}">
        <span class="day-num">${d}</span>
        ${badgeHtml}
      </button>
    `;
  }

  daysGrid.innerHTML = html;
}

function renderSelectedDayAgenda() {
  const headTitle = document.getElementById('calSelectedDayTitle');
  const todayTag = document.getElementById('calTodayTag');
  const agendaList = document.getElementById('calSelectedDayList');
  if (!headTitle || !agendaList) return;

  const dateObj = new Date(calCurrentYear, calCurrentMonth, calSelectedDay);
  const weekdayShort = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][dateObj.getDay()];
  
  headTitle.textContent = `${weekdayShort} ${calSelectedDay}`;
  
  const isToday = (calSelectedDay === calTodayDay && calCurrentMonth === 10 && calCurrentYear === 2026);
  if (todayTag) todayTag.hidden = !isToday;

  updateSelectedDayPending();

  const tasks = dashState.agenda[calSelectedDay] || [];
  if (tasks.length === 0) {
    agendaList.innerHTML = `<li class="cal-empty-agenda muted">No tasks scheduled for this day.</li>`;
    return;
  }

  agendaList.innerHTML = tasks.map(t => `
    <li class="cal-agenda-item ${t.done ? 'done' : ''}" id="agenda-task-${t.id}">
      <label class="cal-checkbox-label">
        <input type="checkbox" data-task-id="${t.id}" ${t.done ? 'checked' : ''} onchange="toggleTask('${t.id}', event.target.checked)" />
        <span class="cal-custom-box"></span>
        <span class="cal-task-name">${esc(t.text)}</span>
      </label>
    </li>
  `).join('');
}

function updateSelectedDayPending() {
  const headPending = document.getElementById('calSelectedDayPending');
  if (!headPending) return;

  const tasks = dashState.agenda[calSelectedDay] || [];
  const pending = tasks.filter(t => !t.done).length;

  if (tasks.length === 0) {
    headPending.textContent = '0 pending';
    headPending.classList.remove('all-done');
  } else if (pending === 0) {
    headPending.textContent = '✓ All done!';
    headPending.classList.add('all-done');
  } else {
    headPending.textContent = `${pending} pending`;
    headPending.classList.remove('all-done');
  }
}

function addAgendaItem(event) {
  event.preventDefault();
  const input = document.getElementById('calAgendaInput');
  const text = input ? input.value.trim() : '';
  if (!text) return;

  if (!dashState.agenda[calSelectedDay]) {
    dashState.agenda[calSelectedDay] = [];
  }

  const newItem = {
    id: 'cal-' + calSelectedDay + '-' + Date.now(),
    text: text,
    time: 'Scheduled',
    done: false
  };

  dashState.agenda[calSelectedDay].push(newItem);

  // If adding to today (12), also push to todayTasks and re-render due today list
  if (calSelectedDay === calTodayDay && calCurrentMonth === 10 && calCurrentYear === 2026) {
    dashState.todayTasks.push(newItem);
    renderDueTodayList();
  }

  input.value = '';
  renderCalendar();
  if (typeof playSound === 'function') playSound('click');
}

/* ---------- Notification bell: pending tasks, high to low priority ---------- */
const PRIORITY_GROUPS = [
  ['High', 'High priority'],
  ['Medium', 'Medium priority'],
  ['Low', 'Low priority'],
];

function shortDate(isoDate) {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short'
  });
}

function whenLabel(task, today) {
  const at = task.time_label ? `, ${task.time_label}` : (task.due_time ? ` at ${task.due_time}` : '');
  const day = iso => (iso === today ? 'today' : shortDate(iso));
  switch (task.urgency) {
    case 'overdue':
      return task.due_on ? `Overdue · was due ${day(task.due_on)}${at}` : 'Overdue';
    case 'today':
      return `Due today${at}`;
    case 'upcoming':
      return task.due_on ? `Due ${shortDate(task.due_on)}${at}` : 'Upcoming';
    case 'snoozed':
      return task.snoozed_until ? `Snoozed until ${shortDate(task.snoozed_until)}` : 'Snoozed';
    default:
      return task.timing_text || 'No due date';
  }
}

function renderNotifications(data) {
  const list = document.getElementById('bellList');
  list.replaceChildren();
  for (const [priority, label] of PRIORITY_GROUPS) {
    const tasks = data.tasks.filter(task => task.priority === priority);
    if (!tasks.length) continue;
    const group = document.createElement('section');
    group.className = 'bell-group';
    const heading = document.createElement('h4');
    heading.className = `bell-group-title prio-${priority.toLowerCase()}`;
    heading.textContent = `${label} · ${tasks.length}`;
    const items = document.createElement('ol');
    items.className = 'bell-items';
    for (const task of tasks) {
      const item = document.createElement('li');
      item.className = `bell-item urgency-${task.urgency}`;
      const text = document.createElement('p');
      text.className = 'bell-text';
      text.textContent = task.text;
      const when = document.createElement('p');
      when.className = 'bell-when';
      when.textContent = whenLabel(task, data.reference_date);
      item.append(text, when);
      items.append(item);
    }
    group.append(heading, items);
    list.append(group);
  }

  const count = data.count;
  const overdue = data.overdue_count;
  const scopeNote = {due_soon: 'Overdue and due today', high: 'High priority only'}[data.scope] || '';
  const empty = document.getElementById('bellEmpty');
  empty.hidden = count > 0;
  empty.textContent = {
    due_soon: "Nothing overdue or due today.",
    high: "No high-priority tasks pending.",
  }[data.scope] || "You're all caught up. No pending tasks.";
  document.getElementById('bellSummary').textContent = [
    count ? `${count} pending${overdue ? ` · ${overdue} overdue` : ''}` : '',
    scopeNote,
  ].filter(Boolean).join(' · ');
  const badge = document.getElementById('bellCount');
  badge.hidden = count === 0 || data.show_badge === false;
  badge.textContent = count > 99 ? '99+' : String(count);
  badge.classList.toggle('has-overdue', overdue > 0 && data.overdue_alert !== false);
  const toggle = document.getElementById('bellToggle');
  const label = count
    ? `Pending tasks: ${count}${overdue ? `, ${overdue} overdue` : ''}`
    : 'Pending tasks: none';
  toggle.setAttribute('aria-label', label);
  toggle.title = label;
}

async function loadNotifications() {
  try {
    renderNotifications(
      await fetchJson('/api/notifications', undefined, 'Unable to load your pending tasks.')
    );
    setStatus('bellStatus', '');
  } catch {
    setStatus('bellStatus', 'Could not load your pending tasks.', true);
  }
}

// Fresh list every time the bell is clicked, and every few minutes so
// "due today" turns into "overdue" without a reload.
document.getElementById('bellToggle').addEventListener('click', loadNotifications);
window.setInterval(() => {
  if (document.visibilityState === 'visible') loadNotifications();
}, 5 * 60 * 1000);

function fillAskExample(text) {
  const input = document.getElementById("askQuestion");
  if (input) {
    input.value = text;
    const form = document.getElementById("askForm");
    if (form) form.requestSubmit();
  }
}

function askExample(text) {
  fillAskExample(text);
}

function renderAskResponse(data) {
  const root = document.getElementById('askOutput');
  root.innerHTML = '';
  const enough = data.confidence !== 'insufficient';
  const fromTasks = data.mode === 'task_board';

  const badge = document.createElement('div');
  badge.className = enough ? 'confidence' : 'confidence is-weak';
  if (!enough) badge.textContent = 'Not enough to go on';
  else if (fromTasks) badge.textContent = 'From your tasks';
  else badge.textContent = `From your notes · ${data.confidence} confidence`;
  root.appendChild(badge);

  // Task answers arrive as "summary:", then "1. task — when · details" lines,
  // then an optional closing line. Show the numbered lines as a real list.
  const lines = String(data.answer || '')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  let list = null;
  for (const line of lines) {
    const numbered = fromTasks ? line.match(/^\d+\.\s+(.*)$/) : null;
    if (!numbered) {
      list = null;
      const paragraph = document.createElement('p');
      paragraph.className = 'answer-text';
      paragraph.textContent = line;
      root.appendChild(paragraph);
      continue;
    }
    if (!list) {
      list = document.createElement('ol');
      list.className = 'answer-tasks';
      root.appendChild(list);
    }
    const cut = numbered[1].lastIndexOf(' — ');
    const title = cut === -1 ? numbered[1] : numbered[1].slice(0, cut);
    const detail = cut === -1 ? '' : numbered[1].slice(cut + 3);
    const item = document.createElement('li');
    if (/\boverdue\b/.test(detail)) item.classList.add('is-overdue');
    const name = document.createElement('strong');
    name.textContent = title;
    item.appendChild(name);
    if (detail) {
      const extra = document.createElement('span');
      extra.textContent = detail;
      item.appendChild(extra);
    }
    list.appendChild(item);
  }

  if (!enough) {
    const hint = document.createElement('p');
    hint.className = 'answer-hint';
    hint.textContent = 'Try words you used in the note, or ask about your tasks, for example “what is overdue?”';
    root.appendChild(hint);
  }

  // A task answer already lists every task, so its sources would only repeat it.
  if (!fromTasks && data.citations && data.citations.length) {
    const heading = document.createElement('h3');
    heading.textContent = data.citations.length === 1 ? 'Source' : 'Sources';
    root.appendChild(heading);
    for (const cite of data.citations) {
      const block = document.createElement('div');
      block.className = 'citation';
      const kind = String(cite.source_kind || cite.item_type || 'note').replaceAll('_', ' ');
      const link = cite.preview_url
        ? `<a href="${esc(cite.preview_url)}" target="_blank" rel="noopener">Open source</a>`
        : '';
      block.innerHTML = `<strong>${esc(cite.title)}</strong><span class="tag tag-type">${esc(kind)}</span>${link}<div>${esc(cite.excerpt)}</div>`;
      root.appendChild(block);
    }
  }
}

async function askInbox(event) {
  event.preventDefault();
  const input = document.getElementById('askQuestion');
  const question = input.value.trim();
  if (!question) return;
  setButtonBusy('askButton', true, 'Thinking…', 'Ask');
  setStatus('askStatus', 'Looking through your inbox…');
  try {
    const data = await fetchJson(
      '/api/ask',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question})
      },
      'Unable to answer from your inbox.'
    );
    renderAskResponse(data);
    setStatus('askStatus', '');
  } catch {
    setStatus('askStatus', 'We could not answer that just now. Please try again.', true);
  } finally {
    setButtonBusy('askButton', false, 'Thinking…', 'Ask');
  }
}

async function loadAuthOptions() {
  try {
    return await fetchJson('/api/auth/options', {}, 'Unable to load sign-in options.');
  } catch {
    return {google_enabled: false, dev_login_enabled: false, local_anonymous: false};
  }
}

let authOptions = {google_enabled: false};

function initialsFor(name) {
  const words = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '';
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : '';
  return (first + last).toUpperCase();
}

function setPhoto(img, url, onShown) {
  // If the picture fails to load (e.g. an old Google link), fall back to the
  // dot on the button and initials in the menu.
  img.onerror = () => {
    img.hidden = true;
    onShown(false);
  };
  if (url) {
    img.src = url;
    img.hidden = false;
  } else {
    img.removeAttribute('src');
    img.hidden = true;
  }
  onShown(Boolean(url));
}

function showProfilePhoto(user) {
  // user is null when signed out or the anonymous Local Owner.
  const url = user ? user.avatar_url : null;
  const wrap = document.getElementById('accountAvatarWrap');
  setPhoto(document.getElementById('accountAvatar'), url, shown => {
    wrap.classList.toggle('has-photo', shown);
  });
  setPhoto(document.getElementById('profileAvatar'), url, () => {});
  document.getElementById('profileInitials').textContent = user ? initialsFor(user.display_name) : '';
  document.getElementById('photoActions').hidden = !user;
}

function showAccountName(fullName) {
  // The Account button shows the signed-in person's first name.
  const toggle = document.getElementById('accountToggle');
  const label = document.getElementById('accountName');
  const firstName = (fullName || '').trim().split(/\s+/)[0];
  if (firstName) {
    label.textContent = firstName;
    toggle.setAttribute('aria-label', `Account, signed in as ${fullName.trim()}`);
  } else {
    label.textContent = 'Account';
    toggle.removeAttribute('aria-label');
  }
}

async function loadAuthUser() {
  authOptions = await loadAuthOptions();
  document.getElementById('devLoginForm').hidden = !authOptions.dev_login_enabled;
  const actions = document.getElementById('authActions');
  try {
    const user = await fetchJson('/api/auth/me', {}, 'Unable to load session.');
    document.getElementById('authUser').textContent = `Signed in as ${user.display_name}`;
    // The anonymous Local Owner (plain local dev) has no session to end.
    const isLocalOwner = user.auth_provider === 'local';
    actions.hidden = isLocalOwner;
    showAccountName(isLocalOwner ? '' : user.display_name);
    showProfilePhoto(isLocalOwner ? null : user);
    // The saved theme may have been changed on another device.
    if (user.settings && window.lifeInboxTheme) window.lifeInboxTheme.apply(user.settings.theme);
  } catch {
    document.getElementById('authUser').textContent = 'Not signed in';
    actions.hidden = true;
    showAccountName('');
    showProfilePhoto(null);
  }
}

async function devLogin(event) {
  event.preventDefault();
  const email = document.getElementById('devLoginEmail').value.trim();
  if (!email) return;
  try {
    const user = await fetchJson(
      '/api/auth/dev-login',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email})
      },
      'Unable to sign in.'
    );
    setStatus('authStatus', `Signed in as ${user.email}`);
    await loadAuthUser();
    await loadNotes();
    await loadDashboard();
  } catch {
    setStatus('authStatus', 'Sign-in failed (dev login may be disabled).', true);
  }
}

async function logoutSession() {
  try {
    await fetchJson('/api/auth/logout', {method: 'POST'}, 'Unable to sign out.');
    // Reload: Google mode lands on /login, local dev falls back to Local Owner.
    window.location.assign('/');
  } catch {
    setStatus('authStatus', 'Sign-out failed.', true);
  }
}

async function switchAccount() {
  try {
    await fetchJson('/api/auth/logout', {method: 'POST'}, 'Unable to sign out.');
    // Google shows its account picker, so a different account can be chosen.
    window.location.assign(authOptions.google_enabled ? '/api/auth/google/start' : '/');
  } catch {
    setStatus('authStatus', 'Could not switch accounts. Please try again.', true);
  }
}

syncControlsFromUrl();
loadAuthUser();
loadNotes();
loadLatestAnalysis('quiet');
loadTopics();
loadDashboard();

window.addEventListener('popstate', () => {
  syncControlsFromUrl();
  loadNotes();
});

window.addEventListener('jin-mode', event => {
  const button = document.getElementById('recordVoiceButton');
  if (!button) return;
  const mode = event.detail;
  button.textContent = mode === 'listening' ? 'Stop'
    : mode === 'thinking' ? 'Thinking…'
    : mode === 'speaking' ? 'Stop talking' : 'Record';
  setStatus('voiceStatus', mode === 'listening' ? 'Listening… pause when you are done.' : '');
});

/* =========================================================================
   Applied AI UI Extensions: Entities Treemap, Smart Cards, API & MCP
   ========================================================================= */

// --- 05 · Entities Treemap & Timeline Scrubber --------------------------
let allEntitiesCache = [];
let currentEntityTypeFilter = '';
let currentTimelineStartDate = null;
let currentTimelineEndDate = null;
let currentDossierEntityId = null;

const TIMELINE_MONTHS = [
  { key: '2025-11', label: "Nov '25", start: '2025-11-01', end: '2025-11-30' },
  { key: '2025-12', label: "Dec '25", start: '2025-12-01', end: '2025-12-31' },
  { key: '2026-01', label: "Jan '26", start: '2026-01-01', end: '2026-01-31' },
  { key: '2026-02', label: "Feb '26", start: '2026-02-01', end: '2026-02-28' },
  { key: '2026-03', label: "Mar '26", start: '2026-03-01', end: '2026-03-31' },
  { key: '2026-04', label: "Apr '26", start: '2026-04-01', end: '2026-04-30' },
  { key: '2026-05', label: "May '26", start: '2026-05-01', end: '2026-05-31' },
  { key: '2026-06', label: "Jun '26", start: '2026-06-01', end: '2026-06-30' },
  { key: '2026-07', label: "Jul '26", start: '2026-07-01', end: '2026-07-31' },
  { key: '2026-08', label: "Aug '26", start: '2026-08-01', end: '2026-08-31' },
  { key: '2026-09', label: "Sep '26", start: '2026-09-01', end: '2026-09-30' },
  { key: '2026-10', label: "Oct '26", start: '2026-10-01', end: '2026-10-31' },
  { key: '2026-11', label: "Nov '26", start: '2026-11-01', end: '2026-11-12' },
];

function updateTimelineSliderVisual(startIdx, endIdx) {
  const highlight = document.getElementById('timelineSliderHighlight');
  if (highlight) {
    const leftPct = (startIdx / 12) * 100;
    const rightPct = 100 - (endIdx / 12) * 100;
    highlight.style.left = `${leftPct}%`;
    highlight.style.right = `${rightPct}%`;
  }
}

function onTimelineSliderChange() {
  const sInput = document.getElementById('timelineSliderStart');
  const eInput = document.getElementById('timelineSliderEnd');
  if (!sInput || !eInput) return;

  let sVal = parseInt(sInput.value, 10);
  let eVal = parseInt(eInput.value, 10);

  if (sVal > eVal) {
    if (document.activeElement === sInput) {
      eInput.value = sVal;
      eVal = sVal;
    } else {
      sInput.value = eVal;
      sVal = eVal;
    }
  }

  updateTimelineSliderVisual(sVal, eVal);

  const startEntry = TIMELINE_MONTHS[sVal];
  const endEntry = TIMELINE_MONTHS[eVal];

  const dateFrom = document.getElementById('timelineDateFrom');
  const dateTo = document.getElementById('timelineDateTo');
  if (dateFrom) dateFrom.value = startEntry.start;
  if (dateTo) dateTo.value = endEntry.end;

  // Clear active pills
  document.querySelectorAll('.entity-time-chip').forEach(p => p.classList.remove('active'));

  applyTimelineRange(startEntry.start, endEntry.end, `${startEntry.label} – ${endEntry.label}`);
}

function onTimelineDateInputChange() {
  const dateFrom = document.getElementById('timelineDateFrom');
  const dateTo = document.getElementById('timelineDateTo');
  if (!dateFrom || !dateTo) return;

  const startVal = dateFrom.value;
  const endVal = dateTo.value;

  // Sync sliders to closest months
  const sIdx = Math.max(0, TIMELINE_MONTHS.findIndex(m => m.end >= startVal));
  const eIdx = Math.max(0, TIMELINE_MONTHS.findIndex(m => m.end >= endVal));
  const sInput = document.getElementById('timelineSliderStart');
  const eInput = document.getElementById('timelineSliderEnd');
  if (sInput) sInput.value = sIdx;
  if (eInput) eInput.value = eIdx !== -1 ? eIdx : 12;
  updateTimelineSliderVisual(sIdx, eIdx !== -1 ? eIdx : 12);

  document.querySelectorAll('.entity-time-chip').forEach(p => p.classList.remove('active'));
  applyTimelineRange(startVal, endVal, `${startVal} to ${endVal}`);
}

function setTimelinePreset(preset) {
  document.querySelectorAll('.entity-time-chip').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('onclick').includes(preset));
  });

  const sInput = document.getElementById('timelineSliderStart');
  const eInput = document.getElementById('timelineSliderEnd');
  const dateFrom = document.getElementById('timelineDateFrom');
  const dateTo = document.getElementById('timelineDateTo');

  let s = null, e = null, sIdx = 0, eIdx = 12, label = 'All Time';

  if (preset === 'all') {
    s = null;
    e = null;
    sIdx = 0;
    eIdx = 12;
    label = 'All Time (Nov 2025 – Nov 2026)';
  } else if (preset === 'sep2026') {
    s = '2026-09-01';
    e = '2026-09-30';
    sIdx = 10;
    eIdx = 10;
    label = 'September 2026 (Jaipur Trip)';
  } else if (preset === '30d') {
    s = '2026-10-13';
    e = '2026-11-12';
    sIdx = 11;
    eIdx = 12;
    label = 'Past 30 Days';
  } else if (preset === '90d') {
    s = '2026-08-14';
    e = '2026-11-12';
    sIdx = 9;
    eIdx = 12;
    label = 'Past 90 Days';
  } else if (preset === 'thisyear') {
    s = '2026-01-01';
    e = '2026-11-12';
    sIdx = 2;
    eIdx = 12;
    label = 'Year 2026';
  }

  if (sInput) sInput.value = sIdx;
  if (eInput) eInput.value = eIdx;
  updateTimelineSliderVisual(sIdx, eIdx);

  if (dateFrom) dateFrom.value = s || '2025-11-01';
  if (dateTo) dateTo.value = e || '2026-11-12';

  applyTimelineRange(s, e, label);
}

function applyTimelineRange(start, end, displayLabel) {
  currentTimelineStartDate = start;
  currentTimelineEndDate = end;

  const displayEl = document.getElementById('timelineRangeDisplay');
  if (displayEl) displayEl.textContent = displayLabel;

  loadEntities();

  // If dossier is active, refresh it under new time period
  if (currentDossierEntityId) {
    showDossier(currentDossierEntityId);
  }
}

async function loadEntities(typeFilter = undefined) {
  if (typeFilter !== undefined) currentEntityTypeFilter = typeFilter;

  const statusEl = document.getElementById('entitiesStatus');
  const listEl = document.getElementById('entitiesList');
  if (!listEl) return;
  try {
    const params = new URLSearchParams();
    if (currentEntityTypeFilter) params.append('type', currentEntityTypeFilter);
    if (currentTimelineStartDate) params.append('start_date', currentTimelineStartDate);
    if (currentTimelineEndDate) params.append('end_date', currentTimelineEndDate);
    const qs = params.toString();
    const url = qs ? `/api/entities?${qs}` : '/api/entities';

    const entities = await fetchJson(url, {}, 'Failed to load entities');
    allEntitiesCache = entities;
    if (entities.length === 0) {
      statusEl.textContent = 'No entities found for this timeframe. Try adjusting the timeline slider or clearing filters.';
      listEl.innerHTML = '';
      renderEntityTreemap([]);
      return;
    }

    const rangeHint = (currentTimelineStartDate || currentTimelineEndDate)
      ? ` for ${currentTimelineStartDate || 'start'} to ${currentTimelineEndDate || 'now'}`
      : ' (all-time history)';
    statusEl.textContent = `Visualizing ${entities.length} entities scaled by note mentions${rangeHint}:`;
    listEl.innerHTML = entities.map(ent => `
      <div class="entity-card" onclick="showDossier(${ent.id})">
        <div class="entity-card-head">
          <span class="feature-badge ${ent.type}">${ent.type === 'person' ? '👤 Person' : ent.type === 'place' ? '📍 Place' : '🏢 Org'}</span>
          <span class="mono" style="font-size:12px; color:var(--muted)">${ent.item_count || ent.mention_count || 1} mentions</span>
        </div>
        <div class="entity-card-title">${esc(ent.name)}</div>
        <div class="muted" style="font-size:12px; margin-top:6px;">Click to view full dossier & related notes &rarr;</div>
      </div>
    `).join('');

    renderEntityTreemap(entities);
  } catch (err) {
    statusEl.textContent = 'Failed to load entities.';
  }
}

function filterEntitiesByType(type) {
  document.querySelectorAll('.entity-type-chip').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(type) || (!type && btn.textContent === 'All'));
  });
  loadEntities(type);
}

async function showDossier(entityId) {
  currentDossierEntityId = entityId;
  const dossierEl = document.getElementById('entityDossier');
  const titleEl = document.getElementById('dossierTitle');
  const typeEl = document.getElementById('dossierType');
  const noticeEl = document.getElementById('dossierTimelineNotice');
  const itemsEl = document.getElementById('dossierItems');
  const summaryEl = document.getElementById('dossierSummaryText');
  if (!dossierEl) return;

  try {
    const params = new URLSearchParams();
    if (currentTimelineStartDate) params.append('start_date', currentTimelineStartDate);
    if (currentTimelineEndDate) params.append('end_date', currentTimelineEndDate);
    const qs = params.toString();

    const data = await fetchJson(`/api/entities/${entityId}${qs ? '?' + qs : ''}`, {}, 'Failed to fetch dossier');
    dossierEl.hidden = false;
    titleEl.textContent = `Dossier: ${data.name}`;
    typeEl.innerHTML = `<span class="feature-badge ${data.type}">${data.type.toUpperCase()}</span>`;

    if (noticeEl) {
      if (currentTimelineStartDate || currentTimelineEndDate) {
        noticeEl.hidden = false;
        noticeEl.textContent = `Filtered Timeline: ${data.items ? data.items.length : 0} of ${data.total_mentions || data.item_count || data.items.length} notes in range`;
      } else {
        noticeEl.hidden = false;
        noticeEl.textContent = `All Time: ${data.items ? data.items.length : 0} notes`;
      }
    }

    if (summaryEl) {
      summaryEl.innerHTML = '<span class="muted" style="font-size:13px;">Synthesizing comprehensive second-brain profile summary…</span>';
    }

    if (!data.items || data.items.length === 0) {
      itemsEl.innerHTML = '<div class="muted" style="padding:16px 0;">No notes found for this entity within the selected timeline window. Try expanding the slider dates above.</div>';
    } else {
      itemsEl.innerHTML = data.items.map(item => `
        <div style="padding:12px 14px; background:var(--void); border:1px solid var(--line); border-radius:var(--radius-inner);">
          <div style="font-weight:500; font-size:14px; color:var(--text);">${esc(item.title || item.raw_text?.slice(0, 80) || 'Inbox Item')}</div>
          <div class="muted" style="font-size:12.5px; margin-top:4px; line-height:1.5;">${esc(item.raw_text?.slice(0, 180) || '')}</div>
          <div class="mono" style="font-size:11px; color:var(--indigo-text); margin-top:6px;">Item #${item.id} · ${item.item_type} · ${item.created_at?.slice(0, 10)}</div>
        </div>
      `).join('');
    }
    dossierEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Fetch AI Summary asynchronously
    fetchJson(`/api/entities/${entityId}/summary${qs ? '?' + qs : ''}`, {}, 'Failed to fetch summary')
      .then(res => {
        if (summaryEl && currentDossierEntityId === entityId) {
          summaryEl.innerHTML = renderMarkdownSnippet(res.summary);
        }
      })
      .catch(() => {
        if (summaryEl && currentDossierEntityId === entityId) {
          summaryEl.innerHTML = '<span class="muted">Could not generate summary at this moment.</span>';
        }
      });
  } catch (e) {
    console.error(e);
  }
}

async function refreshDossierSummary() {
  if (!currentDossierEntityId) return;
  const summaryEl = document.getElementById('dossierSummaryText');
  const btn = document.getElementById('refreshSummaryBtn');
  if (summaryEl) summaryEl.innerHTML = '<span class="muted" style="font-size:13px;">Synthesizing updated profile summary…</span>';
  if (btn) btn.disabled = true;

  try {
    const params = new URLSearchParams();
    if (currentTimelineStartDate) params.append('start_date', currentTimelineStartDate);
    if (currentTimelineEndDate) params.append('end_date', currentTimelineEndDate);
    const qs = params.toString();

    const res = await fetchJson(`/api/entities/${currentDossierEntityId}/summary${qs ? '?' + qs : ''}`, {}, 'Failed to refresh summary');
    if (summaryEl) summaryEl.innerHTML = renderMarkdownSnippet(res.summary);
    if (window.toast) window.toast('Entity summary updated with latest context', 'info');
  } catch {
    if (summaryEl) summaryEl.innerHTML = '<span class="muted">Failed to generate updated summary.</span>';
  } finally {
    if (btn) btn.disabled = false;
  }
}

function formatInlineMarkdown(str) {
  if (!str) return '';
  let s = esc(str);
  s = s.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*(.*?)\*/g, '<em>$1</em>');
  return s;
}

function renderMarkdownSnippet(text) {
  if (!text) return '';
  const lines = text.split('\n');
  let html = '';
  let inList = false;

  for (let rawLine of lines) {
    let line = rawLine.trim();
    if (!line) {
      if (inList) {
        html += '</ul>';
        inList = false;
      }
      continue;
    }

    if (line.startsWith('•') || line.startsWith('- ') || line.startsWith('* ')) {
      if (!inList) {
        html += '<ul>';
        inList = true;
      }
      let content = line.replace(/^[•\-\*]\s*/, '');
      html += `<li>${formatInlineMarkdown(content)}</li>`;
    } else {
      if (inList) {
        html += '</ul>';
        inList = false;
      }
      html += `<p>${formatInlineMarkdown(line)}</p>`;
    }
  }

  if (inList) {
    html += '</ul>';
  }
  return html;
}

function closeDossier() {
  const dossierEl = document.getElementById('entityDossier');
  if (dossierEl) dossierEl.hidden = true;
  currentDossierEntityId = null;
}

async function scanAllNotesForEntities() {
  const statusEl = document.getElementById('entitiesStatus');
  statusEl.textContent = 'Extracting entities across notes…';
  try {
    const items = await fetchJson('/api/items?limit=50', {}, 'Failed to list items');
    let total = 0;
    for (const it of items) {
      try {
        const ents = await fetchJson(`/api/items/${it.id}/entities`, { method: 'POST' });
        total += ents.length;
      } catch (e) {}
    }
    statusEl.textContent = `Scan complete! Discovered ${total} entity connections.`;
    if (window.toast) window.toast(`Scan complete! Discovered ${total} entity connections.`, 'success');
    await loadEntities();
  } catch (e) {
    statusEl.textContent = 'Entity extraction encountered an issue.';
    if (window.toast) window.toast('Entity extraction encountered an issue.', 'error');
  }
}

function renderEntityTreemap(entities) {
  const container = document.getElementById('entityTreemap');
  if (!container) return;

  if (!entities || entities.length === 0) {
    container.innerHTML = '<div style="display:flex; height:100%; align-items:center; justify-content:center; color:var(--muted); font-size:13px;">No entities to visualize yet.</div>';
    return;
  }

  const width = container.clientWidth || 900;
  const height = 480;

  const total = entities.reduce((sum, it) => sum + (it.item_count || it.mention_count || 1), 0);
  if (total <= 0) return;

  const scale = (width * height) / total;
  const items = entities.map(it => ({
    ...it,
    value: it.item_count || it.mention_count || 1,
    area: (it.item_count || it.mention_count || 1) * scale
  })).sort((a, b) => b.value - a.value);

  const rect = { x: 0, y: 0, w: width, h: height };
  const layout = [];

  function worst(row, side) {
    if (!row || row.length === 0) return Infinity;
    const s2 = side * side;
    const S = row.reduce((sum, item) => sum + item.area, 0);
    const S2 = S * S;
    let maxAspect = 0;
    for (const item of row) {
      const a = item.area;
      const aspect = Math.max((s2 * a) / S2, S2 / (s2 * a));
      if (aspect > maxAspect) maxAspect = aspect;
    }
    return maxAspect;
  }

  function layoutRow(row, side) {
    if (!row || row.length === 0) return;
    const S = row.reduce((sum, item) => sum + item.area, 0);
    const isHorizontal = rect.w < rect.h;
    const rowThickness = S / side;
    let offset = 0;

    for (const item of row) {
      if (isHorizontal) {
        const itemWidth = item.area / rowThickness;
        layout.push({ ...item, x: rect.x + offset, y: rect.y, w: itemWidth, h: rowThickness });
        offset += itemWidth;
      } else {
        const itemHeight = item.area / rowThickness;
        layout.push({ ...item, x: rect.x, y: rect.y + offset, w: rowThickness, h: itemHeight });
        offset += itemHeight;
      }
    }

    if (isHorizontal) {
      rect.y += rowThickness;
      rect.h -= rowThickness;
    } else {
      rect.x += rowThickness;
      rect.w -= rowThickness;
    }
  }

  let row = [];
  const queue = [...items];

  while (queue.length > 0) {
    const side = Math.min(rect.w, rect.h);
    if (side <= 0.001) break;
    const newRow = [...row, queue[0]];
    if (row.length === 0 || worst(newRow, side) <= worst(row, side)) {
      row.push(queue.shift());
    } else {
      layoutRow(row, side);
      row = [];
    }
  }

  if (row.length > 0) {
    const side = Math.min(rect.w, rect.h);
    layoutRow(row, side);
  }

  container.innerHTML = layout.map(cell => {
    const icon = cell.type === 'person' ? '👤' : cell.type === 'place' ? '📍' : '🏢';
    const showIcon = cell.w >= 45 && cell.h >= 45;
    const showCount = cell.w >= 70 && cell.h >= 55;
    const isSmall = cell.w < 60 || cell.h < 35;
    const countText = `${cell.value} ${cell.value === 1 ? 'mention' : 'mentions'}`;
    const tooltip = `${cell.name} (${cell.type}) · ${countText}. Click to view connected notes.`;

    return `
      <div class="treemap-cell ${esc(cell.type)}"
           style="left:${cell.x.toFixed(1)}px; top:${cell.y.toFixed(1)}px; width:${cell.w.toFixed(1)}px; height:${cell.h.toFixed(1)}px;"
           title="${esc(tooltip)}"
           tabindex="0"
           role="button"
           aria-label="${esc(tooltip)}"
           onclick="showDossier(${cell.id})"
           onkeydown="if(event.key==='Enter'||event.key===' ') { event.preventDefault(); showDossier(${cell.id}); }">
        ${showIcon ? `<span class="treemap-cell-icon" aria-hidden="true">${icon}</span>` : ''}
        <span class="treemap-cell-name" style="${isSmall ? 'font-size:11px;' : ''}">${esc(cell.name)}</span>
        ${showCount ? `<span class="treemap-cell-count">${countText}</span>` : ''}
      </div>
    `;
  }).join('');
}

window.addEventListener('resize', () => {
  const panel = document.getElementById('panel-entities');
  if (panel && !panel.hidden && allEntitiesCache && allEntitiesCache.length > 0) {
    renderEntityTreemap(allEntitiesCache);
  }
});

// --- 06 · Smart Cards & Expense Tracker ----------------------------------
let allCardsCache = [];
let currentCardKindFilter = '';
let currentCardSearchQuery = '';
let currentCardSort = 'newest';
let currentCardCategoryFilter = '';
let cookTimerSeconds = 0;
let cookTimerInterval = null;

async function loadCards(kindFilter = undefined) {
  if (kindFilter !== undefined) currentCardKindFilter = kindFilter;
  const statusEl = document.getElementById('cardsStatus');
  const listEl = document.getElementById('cardsList');
  const totalAmountEl = document.getElementById('expenseTotalAmount');
  const categoryBreakdownEl = document.getElementById('expenseCategoryBreakdown');
  if (!listEl) return;

  try {
    // 1. Fetch expenses
    const exp = await fetchJson('/api/expenses', {}, 'Failed to load expenses');
    if (totalAmountEl) {
      totalAmountEl.textContent = `₹${(exp.total || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    }
    if (categoryBreakdownEl) {
      const cats = Object.entries(exp.by_category || {});
      categoryBreakdownEl.innerHTML = cats.length ? cats.map(([cat, amt]) => {
        const isCatActive = currentCardCategoryFilter.toLowerCase() === cat.toLowerCase();
        return `
          <button type="button" class="category-filter-chip feature-badge ${isCatActive ? 'active' : ''}"
                  style="background:${isCatActive ? 'var(--green)' : 'rgba(52, 211, 153, 0.15)'}; color:${isCatActive ? '#000' : 'var(--green)'}; border:1px solid rgba(52, 211, 153, 0.3);"
                  onclick="filterCardsByCategory('${esc(cat)}')">
            ${esc(cat)}: ₹${amt.toLocaleString('en-IN')}
          </button>
        `;
      }).join('') : '<span class="muted" style="font-size:12px;">No receipt breakdowns yet.</span>';

      if (currentCardCategoryFilter) {
        categoryBreakdownEl.innerHTML += `
          <button type="button" class="category-filter-chip feature-badge" style="background:rgba(239, 68, 68, 0.15); color:var(--red); border:1px solid rgba(239, 68, 68, 0.3);" onclick="filterCardsByCategory('')">
            ✕ Clear Category Filter
          </button>
        `;
      }
    }

    // 2. Fetch all cards once to cache and compute counts
    const cards = await fetchJson('/api/cards', {}, 'Failed to load cards');
    allCardsCache = cards;

    // Update counts on filter buttons
    const counts = { all: cards.length, receipt: 0, recipe: 0, book: 0, contact: 0, event: 0 };
    for (const c of cards) {
      if (counts[c.kind] !== undefined) counts[c.kind]++;
    }
    const updateCount = (id, n) => {
      const el = document.getElementById(id);
      if (el) el.textContent = `(${n})`;
    };
    updateCount('count-cards-all', counts.all);
    updateCount('count-cards-receipt', counts.receipt);
    updateCount('count-cards-recipe', counts.recipe);
    updateCount('count-cards-book', counts.book);
    updateCount('count-cards-contact', counts.contact);
    updateCount('count-cards-event', counts.event);

    filterAndRenderCards();
  } catch (err) {
    if (statusEl) statusEl.textContent = 'Failed to load cards.';
  }
}

function filterAndRenderCards() {
  const statusEl = document.getElementById('cardsStatus');
  const listEl = document.getElementById('cardsList');
  if (!listEl) return;

  let filtered = allCardsCache.slice();

  // Filter by kind
  if (currentCardKindFilter) {
    filtered = filtered.filter(c => c.kind === currentCardKindFilter);
  }

  // Filter by category (for receipts)
  if (currentCardCategoryFilter) {
    filtered = filtered.filter(c => {
      if (c.kind !== 'receipt') return false;
      const cat = (c.kind_fields?.category || '').toLowerCase();
      return cat === currentCardCategoryFilter.toLowerCase();
    });
  }

  // Filter by search query
  if (currentCardSearchQuery) {
    const q = currentCardSearchQuery.toLowerCase();
    filtered = filtered.filter(c => {
      const f = c.kind_fields || {};
      const target = [
        c.title,
        c.raw_text,
        f.title,
        f.name,
        f.vendor,
        f.category,
        f.author,
        f.genre,
        f.company,
        f.role,
        f.venue,
        f.location,
        Array.isArray(f.ingredients) ? f.ingredients.join(' ') : f.ingredients,
        f.phone,
        f.email
      ].filter(Boolean).join(' ').toLowerCase();
      return target.includes(q);
    });
  }

  // Sort
  if (currentCardSort === 'newest') {
    filtered.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  } else if (currentCardSort === 'oldest') {
    filtered.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  } else if (currentCardSort === 'amount-high') {
    filtered.sort((a, b) => (Number(b.kind_fields?.amount || 0)) - (Number(a.kind_fields?.amount || 0)));
  } else if (currentCardSort === 'amount-low') {
    filtered.sort((a, b) => (Number(a.kind_fields?.amount || 0)) - (Number(b.kind_fields?.amount || 0)));
  } else if (currentCardSort === 'alpha') {
    filtered.sort((a, b) => {
      const nameA = (a.kind_fields?.name || a.kind_fields?.title || a.kind_fields?.vendor || a.title || '').toLowerCase();
      const nameB = (b.kind_fields?.name || b.kind_fields?.title || b.kind_fields?.vendor || b.title || '').toLowerCase();
      return nameA.localeCompare(nameB);
    });
  }

  // Render status
  if (filtered.length === 0) {
    statusEl.textContent = allCardsCache.length === 0
      ? 'No smart cards created yet. Click "Auto-Format Notes into Cards" or "+ New Smart Card"!'
      : 'No smart cards match your current search/filters.';
    if (allCardsCache.length === 0) {
      listEl.innerHTML = `
        <div class="cards-empty-state">
          <div class="cards-empty-icon">🃏</div>
          <div class="cards-empty-title">No smart cards created yet</div>
          <p class="cards-empty-desc">Turn notes into structured receipts, recipes, book reviews, or contacts automatically with AI.</p>
          <div class="cards-empty-actions">
            <button type="button" class="primary" onclick="openNewCardModal()">+ New Smart Card</button>
            <button type="button" class="secondary" onclick="convertAllNotesToCards()">✨ Auto-Format Notes into Cards</button>
          </div>
        </div>
      `;
    } else {
      const activeFilters = [
        currentCardKindFilter ? `kind "${currentCardKindFilter}"` : '',
        currentCardSearchQuery ? `matching "${esc(currentCardSearchQuery)}"` : '',
        currentCardCategoryFilter ? `category "${currentCardCategoryFilter}"` : ''
      ].filter(Boolean).join(' and ');
      listEl.innerHTML = `
        <div class="cards-empty-state">
          <div class="cards-empty-icon">🔍</div>
          <div class="cards-empty-title">No matching smart cards</div>
          <p class="cards-empty-desc">No cards match active filter: ${activeFilters || 'current criteria'}.</p>
          <div class="cards-empty-actions">
            <button type="button" class="secondary" onclick="clearCardsFilters()">Clear filters</button>
          </div>
        </div>
      `;
    }
    return;
  }

  const kindLabel = currentCardKindFilter ? ` · Kind: ${currentCardKindFilter}` : '';
  const catLabel = currentCardCategoryFilter ? ` · Category: "${currentCardCategoryFilter}"` : '';
  const searchLabel = currentCardSearchQuery ? ` · Search: "${currentCardSearchQuery}"` : '';
  statusEl.textContent = `Displaying ${filtered.length} of ${allCardsCache.length} smart cards${kindLabel}${catLabel}${searchLabel}:`;

  listEl.innerHTML = filtered.map(c => {
    const k = c.kind || 'card';
    const f = c.kind_fields || {};
    let bodyHtml = '';
    let actionHtml = '';

    if (k === 'recipe') {
      const prep = f.prep_time || '30 mins';
      const servings = f.servings || '4';
      let ings = f.ingredients || [];
      if (typeof ings === 'string') ings = ings.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
      const previewIngs = Array.isArray(ings) ? ings.slice(0, 4) : [];
      const remainingCount = ings.length - previewIngs.length;

      bodyHtml = `
        <div style="display:flex; gap:8px; margin:6px 0 10px 0;">
          <span class="feature-badge" style="background:rgba(245, 158, 11, 0.15); color:var(--amber);">⏱️ ${esc(prep)}</span>
          <span class="feature-badge" style="background:rgba(99, 102, 241, 0.12); color:var(--indigo-text);">🍽️ ${esc(String(servings))} servings</span>
        </div>
        <div class="mono" style="font-size:11px; color:var(--muted); text-transform:uppercase; margin-bottom:4px;">Ingredients:</div>
        <div style="display:flex; flex-direction:column; gap:2px; margin-bottom:6px;">
          ${previewIngs.map(ing => `
            <label class="ingredient-check-item">
              <input type="checkbox" onchange="this.parentElement.style.opacity = this.checked ? '0.5' : '1'; this.nextElementSibling.style.textDecoration = this.checked ? 'line-through' : 'none';">
              <span>${esc(ing)}</span>
            </label>
          `).join('')}
          ${remainingCount > 0 ? `<span class="muted" style="font-size:11px; margin-top:2px;">+ ${remainingCount} more in cook mode</span>` : ''}
        </div>
      `;
      actionHtml = `
        <div class="card-action-bar">
          <button type="button" class="card-action-btn" onclick="openRecipeCookMode(${c.id})">🍳 Cook Mode & Timer</button>
        </div>
      `;
    } else if (k === 'book') {
      const author = f.author || 'Unknown Author';
      const genre = f.genre || 'General';
      const status = f.status || 'To Read';
      const isFinished = status.toLowerCase().includes('finish');
      const isReading = status.toLowerCase().includes('reading');
      const statusPill = isFinished ? '✅ Finished' : isReading ? '📖 Reading' : '🔖 To Read';

      bodyHtml = `
        <div class="typed-card-fields" style="margin-top:8px;">
          <div class="typed-card-field"><dt>Author:</dt><dd><strong>${esc(author)}</strong></dd></div>
          <div class="typed-card-field"><dt>Genre:</dt><dd>${esc(genre)}</dd></div>
          <div class="typed-card-field"><dt>Reading Status:</dt><dd><span class="feature-badge" style="background:rgba(99, 102, 241, 0.15); color:var(--indigo-text);">${statusPill}</span></dd></div>
        </div>
      `;
      actionHtml = `
        <div class="card-action-bar">
          <button type="button" class="card-action-btn" onclick="cycleBookStatus(${c.id}, event)" title="Click to cycle status: To Read → Reading → Finished">
            🔄 Switch: ${isFinished ? 'Set To Read' : isReading ? 'Mark Finished' : 'Start Reading'}
          </button>
        </div>
      `;
    } else if (k === 'receipt') {
      const amt = Number(f.amount || 0);
      const cat = f.category || 'General';
      const date = f.date || (c.created_at || '').slice(0, 10);
      const reimbursed = Boolean(f.reimbursed);

      bodyHtml = `
        <div class="card-hero-amount">₹${amt.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</div>
        <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
          <span class="card-category-badge">${esc(cat)}</span>
          <span class="mono muted" style="font-size:12px;">${esc(date)}</span>
        </div>
      `;
      actionHtml = `
        <div class="card-action-bar">
          <button type="button" class="reimbursed-toggle-btn ${reimbursed ? 'is-reimbursed' : ''}" onclick="toggleCardReimbursed(${c.id}, event)" title="Click to toggle expense reimbursement status">
            ${reimbursed ? '✅ Reimbursed' : '⏳ Mark Reimbursed'}
          </button>
          <button type="button" class="card-action-btn" onclick="copyExpenseSnippet(${c.id}, event)" title="Copy expense row">
            📋 Copy Details
          </button>
        </div>
      `;
    } else if (k === 'contact') {
      const name = f.name || c.title || 'Contact';
      const role = f.role || f.company || 'Contact';
      const phone = f.phone || '';
      const email = f.email || '';
      const cleanPhone = phone.replace(/[^\d+]/g, '');

      bodyHtml = `
        <div class="typed-card-fields" style="margin-top:8px;">
          <div class="typed-card-field"><dt>Organization/Role:</dt><dd><strong>${esc(role)}</strong></dd></div>
          ${phone ? `<div class="typed-card-field"><dt>Phone:</dt><dd><a href="tel:${cleanPhone}" style="color:var(--cyan);">${esc(phone)}</a></dd></div>` : ''}
          ${email ? `<div class="typed-card-field"><dt>Email:</dt><dd><a href="mailto:${esc(email)}" style="color:var(--indigo-text);">${esc(email)}</a></dd></div>` : ''}
        </div>
      `;
      actionHtml = `
        <div class="card-action-bar">
          ${cleanPhone ? `<a href="tel:${cleanPhone}" class="card-action-btn">📞 Call</a>` : ''}
          ${cleanPhone ? `<a href="https://wa.me/${cleanPhone.replace('+', '')}" target="_blank" class="card-action-btn whatsapp-btn">💬 WhatsApp</a>` : ''}
          ${email ? `<a href="mailto:${esc(email)}" class="card-action-btn">✉️ Email</a>` : ''}
          <button type="button" class="card-action-btn dossier-link-btn" onclick="openContactInTreemap('${esc(name)}')">👤 View Treemap</button>
        </div>
      `;
    } else if (k === 'event') {
      const dt = f.date_time || f.date || 'Scheduled Event';
      const venue = f.venue || f.location || 'Local / Specified in notes';

      bodyHtml = `
        <div class="typed-card-fields" style="margin-top:8px;">
          <div class="typed-card-field"><dt>Date & Time:</dt><dd><strong>${esc(dt)}</strong></dd></div>
          <div class="typed-card-field"><dt>Location:</dt><dd>${esc(venue)}</dd></div>
        </div>
      `;
      actionHtml = `
        <div class="card-action-bar">
          <button type="button" class="card-action-btn" onclick="downloadEventIcs(${c.id})">📅 Add to Calendar (.ics)</button>
          <a href="https://maps.google.com/?q=${encodeURIComponent(venue)}" target="_blank" class="card-action-btn">🗺️ Maps</a>
        </div>
      `;
    } else {
      bodyHtml = `<div class="muted" style="font-size:13px; margin-top:8px; line-height:1.5;">${esc(c.raw_text?.slice(0, 160) || '')}</div>`;
    }

    return `
      <div class="typed-card">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
          <span class="typed-card-badge ${k}">${k.toUpperCase()}</span>
          <span class="mono muted" style="font-size:10.5px;">#${c.id}</span>
        </div>
        <div class="typed-card-title" style="margin-top:6px;">${esc(f.title || f.name || f.vendor || f.event_name || c.title || 'Untitled Card')}</div>
        ${bodyHtml}
        ${actionHtml}
      </div>
    `;
  }).join('');
}

function filterCardsByKind(kind) {
  currentCardKindFilter = kind;
  document.querySelectorAll('#panel-cards .feature-bar .chip').forEach(btn => {
    const isTarget = (!kind && btn.id === 'chip-cards-all') || (kind && btn.id === `chip-cards-${kind}`);
    btn.classList.toggle('active', isTarget);
  });
  filterAndRenderCards();
}

function clearCardsFilters() {
  currentCardKindFilter = '';
  currentCardCategoryFilter = '';
  currentCardSearchQuery = '';
  const searchInput = document.getElementById('cardsSearchInput');
  if (searchInput) searchInput.value = '';
  document.querySelectorAll('#panel-cards .feature-bar .chip').forEach(btn => {
    btn.classList.toggle('active', btn.id === 'chip-cards-all');
  });
  filterAndRenderCards();
}

function filterCardsByCategory(cat) {
  currentCardCategoryFilter = (currentCardCategoryFilter.toLowerCase() === cat.toLowerCase()) ? '' : cat;
  if (currentCardCategoryFilter && currentCardKindFilter !== 'receipt') {
    filterCardsByKind('receipt');
  } else {
    filterAndRenderCards();
  }
}

function onCardsSearchInput(val) {
  currentCardSearchQuery = val.trim();
  filterAndRenderCards();
}

function onCardsSortChange(val) {
  currentCardSort = val;
  filterAndRenderCards();
}

async function toggleCardReimbursed(itemId, evt) {
  if (evt) evt.stopPropagation();
  const card = allCardsCache.find(c => c.id === itemId);
  if (!card) return;
  card.kind_fields = card.kind_fields || {};
  card.kind_fields.reimbursed = !card.kind_fields.reimbursed;
  filterAndRenderCards();

  try {
    await fetchJson(`/api/items/${itemId}/card`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'receipt', fields: card.kind_fields })
    });
  } catch (e) {
    console.error('Failed to save reimbursed state', e);
  }
}

async function cycleBookStatus(itemId, evt) {
  if (evt) evt.stopPropagation();
  const card = allCardsCache.find(c => c.id === itemId);
  if (!card) return;
  card.kind_fields = card.kind_fields || {};
  const cur = (card.kind_fields.status || '').toLowerCase();
  let next = 'Reading';
  if (cur.includes('read') && !cur.includes('finished')) {
    next = 'Finished';
  } else if (cur.includes('finish')) {
    next = 'To Read';
  } else {
    next = 'Reading';
  }
  card.kind_fields.status = next;
  filterAndRenderCards();

  try {
    await fetchJson(`/api/items/${itemId}/card`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'book', fields: card.kind_fields })
    });
  } catch (e) {
    console.error('Failed to update book status', e);
  }
}

function copyExpenseSnippet(itemId, evt) {
  if (evt) evt.stopPropagation();
  const card = allCardsCache.find(c => c.id === itemId);
  if (!card) return;
  const f = card.kind_fields || {};
  const vendor = f.vendor || card.title || 'Expense';
  const amt = Number(f.amount || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 });
  const cat = f.category || 'General';
  const date = f.date || (card.created_at || '').slice(0, 10);
  const snippet = `${vendor} - ₹${amt} (${cat} · ${date})`;

  navigator.clipboard.writeText(snippet).then(() => {
    if (evt && evt.target) {
      const orig = evt.target.innerHTML;
      evt.target.innerHTML = '✓ Copied!';
      setTimeout(() => { evt.target.innerHTML = orig; }, 1500);
    }
  });
}

function downloadEventIcs(itemId) {
  const card = allCardsCache.find(c => c.id === itemId);
  if (!card) return;
  const f = card.kind_fields || {};
  const title = f.name || f.event_name || card.title || 'Life Inbox Event';
  const venue = f.venue || f.location || 'Online / Specified in note';
  const rawDate = f.date || f.date_time || '';

  let dtStartStr = '20261120T100000Z';
  let dtEndStr = '20261120T110000Z';
  if (rawDate) {
    const dMatch = rawDate.match(/(\d{4})[-/](\d{2})[-/](\d{2})/);
    if (dMatch) {
      const yr = dMatch[1], mo = dMatch[2], da = dMatch[3];
      dtStartStr = `${yr}${mo}${da}T100000Z`;
      dtEndStr = `${yr}${mo}${da}T110000Z`;
    }
  }

  const icsContent = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Life Inbox//Smart Cards//EN',
    'BEGIN:VEVENT',
    `UID:life-inbox-event-${card.id}@lifeinbox.app`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)}Z`,
    `DTSTART:${dtStartStr}`,
    `DTEND:${dtEndStr}`,
    `SUMMARY:${title.replace(/[\r\n]+/g, ' ')}`,
    `LOCATION:${venue.replace(/[\r\n]+/g, ' ')}`,
    `DESCRIPTION:Exported from Life Inbox item #${card.id}`,
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\r\n');

  const blob = new Blob([icsContent], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.ics`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function exportExpensesToCsv() {
  const receipts = allCardsCache.filter(c => c.kind === 'receipt');
  if (receipts.length === 0) {
    if (window.toast) window.toast('No receipts found to export.', 'info');
    return;
  }

  const rows = [
    ['Item ID', 'Date', 'Vendor / Description', 'Category', 'Amount (INR)', 'Status']
  ];

  for (const r of receipts) {
    const f = r.kind_fields || {};
    const vendor = f.vendor || r.title || 'Receipt';
    const amount = Number(f.amount || 0).toFixed(2);
    const date = f.date || (r.created_at || '').slice(0, 10);
    const cat = f.category || 'General';
    const status = f.reimbursed ? 'Reimbursed' : 'Pending';
    rows.push([r.id, date, `"${vendor.replace(/"/g, '""')}"`, `"${cat.replace(/"/g, '""')}"`, amount, status]);
  }

  const csvContent = rows.map(r => r.join(',')).join('\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `life_inbox_expenses_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  if (window.toast) window.toast(`Exported ${receipts.length} receipts to CSV!`, 'success');
}

async function openContactInTreemap(name) {
  const tabBtn = document.getElementById('tab-entities');
  if (tabBtn) tabBtn.click();

  if (!allEntitiesCache || allEntitiesCache.length === 0) {
    await loadEntities();
  }

  const clean = name.toLowerCase().trim();
  let match = (allEntitiesCache || []).find(e => e.name.toLowerCase() === clean);
  if (!match) {
    match = (allEntitiesCache || []).find(e => e.name.toLowerCase().includes(clean) || clean.includes(e.name.toLowerCase()));
  }

  if (match) {
    showDossier(match.id);
  } else {
    try {
      const results = await fetchJson(`/api/entities?q=${encodeURIComponent(name)}`);
      if (results && results.length > 0) {
        showDossier(results[0].id);
      }
    } catch(e) {}
  }
}

// Recipe Cook Mode
function openRecipeCookMode(itemId) {
  const card = allCardsCache.find(c => c.id === itemId);
  if (!card) return;
  const f = card.kind_fields || {};
  const modal = document.getElementById('recipeCookModal');
  const titleEl = document.getElementById('cookRecipeTitle');
  const metaEl = document.getElementById('cookRecipeMeta');
  const ingListEl = document.getElementById('cookIngredientsList');
  const stepsListEl = document.getElementById('cookStepsList');
  if (!modal) return;

  titleEl.textContent = f.title || f.name || card.title || 'Recipe';
  metaEl.textContent = `Prep Time: ${f.prep_time || '25-35 mins'} · Servings: ${f.servings || '4'}`;

  let ings = f.ingredients || [];
  if (typeof ings === 'string') ings = ings.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
  if (!Array.isArray(ings) || ings.length === 0) {
    ings = ['Ingredients listed in notes'];
  }
  ingListEl.innerHTML = ings.map(ing => `
    <label class="ingredient-check-item">
      <input type="checkbox" onchange="this.parentElement.style.opacity = this.checked ? '0.5' : '1'; this.nextElementSibling.style.textDecoration = this.checked ? 'line-through' : 'none';">
      <span>${esc(ing)}</span>
    </label>
  `).join('');

  let steps = f.steps || [];
  if (typeof steps === 'string') steps = steps.split(/\n+/).map(s => s.trim()).filter(Boolean);
  if (!Array.isArray(steps) || steps.length === 0) {
    const raw = card.raw_text || '';
    const sentences = raw.split(/[.\n]+/).map(s => s.trim()).filter(s => s.length > 10);
    steps = sentences.length > 0 ? sentences.slice(0, 6) : ['Follow instructions from note.'];
  }
  stepsListEl.innerHTML = steps.map((st, i) => `
    <div class="cook-step-item">
      <span class="cook-step-no">${i + 1}.</span>
      <span>${esc(st)}</span>
    </div>
  `).join('');

  modal.hidden = false;
  resetCookTimer();
}

function closeRecipeCookMode() {
  const modal = document.getElementById('recipeCookModal');
  if (modal) modal.hidden = true;
  resetCookTimer();
}

function setCookTimer(seconds) {
  cookTimerSeconds = seconds;
  updateCookTimerDisplay();
}

function toggleCookTimer() {
  const btn = document.getElementById('cookTimerToggleBtn');
  if (cookTimerInterval) {
    clearInterval(cookTimerInterval);
    cookTimerInterval = null;
    if (btn) btn.textContent = 'Resume';
  } else {
    if (cookTimerSeconds <= 0) cookTimerSeconds = 300;
    updateCookTimerDisplay();
    if (btn) btn.textContent = 'Pause';
    cookTimerInterval = setInterval(() => {
      cookTimerSeconds--;
      if (cookTimerSeconds <= 0) {
        clearInterval(cookTimerInterval);
        cookTimerInterval = null;
        if (btn) btn.textContent = 'Start';
        cookTimerSeconds = 0;
        updateCookTimerDisplay();
        if (window.toast) {
          window.toast('🔔 Kitchen Timer: Recipe step complete!', 'info', 8000);
        }
        if (typeof playSound === 'function') {
          playSound('ping');
        }
        const timerDisplay = document.getElementById('cookTimerDisplay');
        if (timerDisplay) {
          timerDisplay.classList.add('timer-ringing');
          setTimeout(() => timerDisplay.classList.remove('timer-ringing'), 3000);
        }
      } else {
        updateCookTimerDisplay();
      }
    }, 1000);
  }
}

function resetCookTimer() {
  if (cookTimerInterval) {
    clearInterval(cookTimerInterval);
    cookTimerInterval = null;
  }
  cookTimerSeconds = 0;
  updateCookTimerDisplay();
  const btn = document.getElementById('cookTimerToggleBtn');
  if (btn) btn.textContent = 'Start';
}

function updateCookTimerDisplay() {
  const display = document.getElementById('cookTimerDisplay');
  if (!display) return;
  const m = Math.floor(cookTimerSeconds / 60);
  const s = cookTimerSeconds % 60;
  display.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// Modal: Create New Smart Card
function openNewCardModal() {
  const modal = document.getElementById('newCardModal');
  if (modal) {
    modal.hidden = false;
    const kindSelect = document.getElementById('newCardKindSelect');
    if (kindSelect) onNewCardKindChange(kindSelect.value || 'receipt');
  }
}

function closeNewCardModal() {
  const modal = document.getElementById('newCardModal');
  if (modal) modal.hidden = true;
}

function onNewCardKindChange(kind) {
  const container = document.getElementById('newCardDynamicFields');
  if (!container) return;
  if (kind === 'receipt') {
    container.innerHTML = `
      <div style="display:flex; gap:12px; flex-wrap:wrap;">
        <label style="flex:1 1 180px;">
          Amount (₹)
          <input type="number" id="field_receipt_amount" step="0.01" min="0" required placeholder="1450.00" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 180px;">
          Category
          <select id="field_receipt_category" style="width:100%; margin-top:4px;">
            <option value="Healthcare & Pharmacy">Healthcare & Pharmacy</option>
            <option value="Food & Dining">Food & Dining</option>
            <option value="Groceries & Supermarket">Groceries & Supermarket</option>
            <option value="Travel & Commute">Travel & Commute</option>
            <option value="Utilities & Bills">Utilities & Bills</option>
            <option value="Electronics & Tech">Electronics & Tech</option>
            <option value="General">General</option>
          </select>
        </label>
        <label style="flex:1 1 180px;">
          Date
          <input type="date" id="field_receipt_date" value="${new Date().toISOString().slice(0, 10)}" style="width:100%; margin-top:4px;">
        </label>
      </div>
    `;
  } else if (kind === 'recipe') {
    container.innerHTML = `
      <div style="display:flex; gap:12px; flex-wrap:wrap;">
        <label style="flex:1 1 180px;">
          Prep Time
          <input type="text" id="field_recipe_prep" placeholder="e.g. 25 mins" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 180px;">
          Servings
          <input type="number" id="field_recipe_servings" value="4" min="1" style="width:100%; margin-top:4px;">
        </label>
      </div>
      <label>
        Ingredients (comma-separated or one per line)
        <textarea id="field_recipe_ingredients" rows="3" placeholder="200g paneer, butter, tomato puree, kasuri methi..." style="width:100%; margin-top:4px;"></textarea>
      </label>
      <label>
        Cooking Steps (one per line)
        <textarea id="field_recipe_steps" rows="3" placeholder="1. Heat pan and melt butter\n2. Sauté spices and puree\n3. Add paneer cubes and simmer" style="width:100%; margin-top:4px;"></textarea>
      </label>
    `;
  } else if (kind === 'contact') {
    container.innerHTML = `
      <div style="display:flex; gap:12px; flex-wrap:wrap;">
        <label style="flex:1 1 180px;">
          Phone Number
          <input type="tel" id="field_contact_phone" placeholder="+91 98765 43210" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 180px;">
          Email
          <input type="email" id="field_contact_email" placeholder="contact@example.com" style="width:100%; margin-top:4px;">
        </label>
      </div>
      <label>
        Company / Role
        <input type="text" id="field_contact_company" placeholder="e.g. Cardiologist at AIIMS, Plumbing Specialist..." style="width:100%; margin-top:4px;">
      </label>
    `;
  } else if (kind === 'book') {
    container.innerHTML = `
      <div style="display:flex; gap:12px; flex-wrap:wrap;">
        <label style="flex:1 1 180px;">
          Author
          <input type="text" id="field_book_author" placeholder="e.g. James Clear" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 180px;">
          Genre
          <input type="text" id="field_book_genre" placeholder="e.g. Productivity, Sci-Fi" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 180px;">
          Status
          <select id="field_book_status" style="width:100%; margin-top:4px;">
            <option value="To Read">To Read</option>
            <option value="Reading">Currently Reading</option>
            <option value="Finished">Finished</option>
          </select>
        </label>
      </div>
    `;
  } else if (kind === 'event') {
    container.innerHTML = `
      <div style="display:flex; gap:12px; flex-wrap:wrap;">
        <label style="flex:1 1 200px;">
          Date & Time
          <input type="text" id="field_event_date" placeholder="e.g. 2026-11-20 10:00 AM" style="width:100%; margin-top:4px;">
        </label>
        <label style="flex:1 1 200px;">
          Venue / Location
          <input type="text" id="field_event_venue" placeholder="e.g. Bangalore Palace Ground" style="width:100%; margin-top:4px;">
        </label>
      </div>
    `;
  }
}

async function submitNewSmartCard(e) {
  e.preventDefault();
  const kind = document.getElementById('newCardKindSelect').value;
  const title = document.getElementById('newCardTitleInput').value.trim();
  const fields = {};

  if (kind === 'receipt') {
    fields.vendor = title;
    fields.amount = parseFloat(document.getElementById('field_receipt_amount')?.value || '0');
    fields.category = document.getElementById('field_receipt_category')?.value || 'General';
    fields.date = document.getElementById('field_receipt_date')?.value || new Date().toISOString().slice(0, 10);
    fields.reimbursed = false;
  } else if (kind === 'recipe') {
    fields.title = title;
    fields.prep_time = document.getElementById('field_recipe_prep')?.value || '30 mins';
    fields.servings = parseInt(document.getElementById('field_recipe_servings')?.value || '4', 10);
    const rawIngs = document.getElementById('field_recipe_ingredients')?.value || '';
    fields.ingredients = rawIngs.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
    const rawSteps = document.getElementById('field_recipe_steps')?.value || '';
    fields.steps = rawSteps.split(/\n+/).map(s => s.trim()).filter(Boolean);
  } else if (kind === 'contact') {
    fields.name = title;
    fields.phone = document.getElementById('field_contact_phone')?.value || '';
    fields.email = document.getElementById('field_contact_email')?.value || '';
    fields.company = document.getElementById('field_contact_company')?.value || '';
  } else if (kind === 'book') {
    fields.title = title;
    fields.author = document.getElementById('field_book_author')?.value || 'Unknown';
    fields.genre = document.getElementById('field_book_genre')?.value || 'General';
    fields.status = document.getElementById('field_book_status')?.value || 'To Read';
  } else if (kind === 'event') {
    fields.name = title;
    fields.event_name = title;
    fields.date = document.getElementById('field_event_date')?.value || '';
    fields.date_time = document.getElementById('field_event_date')?.value || '';
    fields.venue = document.getElementById('field_event_venue')?.value || '';
  }

  try {
    await fetchJson('/api/cards', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, title, fields })
    }, 'Failed to create card');

    closeNewCardModal();
    filterCardsByKind(kind);
    await loadCards(kind);
    if (window.toast) window.toast('Smart card created successfully!', 'success');
  } catch (err) {
    if (window.toast) {
      window.toast('Error creating smart card: ' + err.message, 'error');
    }
  }
}

async function convertAllNotesToCards() {
  const statusEl = document.getElementById('cardsStatus');
  statusEl.textContent = 'Formatting notes into smart cards…';
  try {
    const items = await fetchJson('/api/items?limit=50', {}, 'Failed to list items');
    let count = 0;
    for (const it of items) {
      if (!it.kind) {
        try {
          await fetchJson(`/api/items/${it.id}/card`, { method: 'POST' });
          count++;
        } catch(e) {}
      }
    }
    statusEl.textContent = `Successfully structured ${count} cards!`;
    if (window.toast) window.toast(`Successfully structured ${count} cards!`, 'success');
    await loadCards();
  } catch (e) {
    statusEl.textContent = 'Error formatting notes into cards.';
    if (window.toast) window.toast('Error formatting notes into cards.', 'error');
  }
}

// --- 07 · Developer & MCP Panel -----------------------------------------
async function loadDeveloperPanel() {
  const tokensListEl = document.getElementById('tokensList');
  const webhooksListEl = document.getElementById('webhooksList');
  const aiLogEl = document.getElementById('aiCallLogList');

  // Load tokens
  if (tokensListEl) {
    try {
      const tokens = await fetchJson('/api/tokens', {}, 'Failed to load tokens');
      tokensListEl.innerHTML = tokens.length ? tokens.map(t => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:var(--void); border:1px solid var(--line); border-radius:var(--radius-inner);">
          <div>
            <strong>${esc(t.name)}</strong>
            <div class="mono muted" style="font-size:11px;">${t.token_prefix || 'li_...'} · Created ${t.created_at}</div>
          </div>
          <button type="button" class="danger icon-btn" onclick="deleteApiToken(${t.id})" title="Revoke token">&times;</button>
        </div>
      `).join('') : '<div class="muted" style="font-size:13px;">No active tokens.</div>';
    } catch(e) {}
  }

  // Load webhooks
  if (webhooksListEl) {
    try {
      const hooks = await fetchJson('/api/webhooks', {}, 'Failed to load webhooks');
      webhooksListEl.innerHTML = hooks.length ? hooks.map(h => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:var(--void); border:1px solid var(--line); border-radius:var(--radius-inner);">
          <div class="mono" style="font-size:12px; color:var(--text); overflow:hidden; text-overflow:ellipsis;">${esc(h.url)}</div>
          <button type="button" class="danger icon-btn" onclick="deleteWebhook(${h.id})" title="Delete">&times;</button>
        </div>
      `).join('') : '<div class="muted" style="font-size:13px;">No active webhooks.</div>';
    } catch(e) {}
  }

  // Load Privacy AI call log
  if (aiLogEl) {
    try {
      const logs = await fetchJson('/api/privacy/ai-log', {}, 'Failed to load AI log');
      aiLogEl.innerHTML = logs.length ? logs.map(l => `
        <div style="padding:8px 12px; background:var(--void); border:1px solid var(--line); border-radius:var(--radius-inner); font-size:12px;">
          <div style="display:flex; justify-content:space-between;">
            <strong style="color:var(--indigo-text);">${esc(l.feature)}</strong>
            <span class="mono muted">${l.created_at}</span>
          </div>
          <div class="muted" style="font-size:11px; margin-top:2px;">
            Chars sent: ${l.chars_sent} · <span style="color:var(--amber);">Masked values: ${l.masked_count}</span> · Model: ${l.model}
          </div>
        </div>
      `).join('') : '<div class="muted" style="font-size:13px;">No AI calls logged yet.</div>';
    } catch(e) {}
  }
}

async function createApiToken(e) {
  e.preventDefault();
  const input = document.getElementById('tokenNameInput');
  const display = document.getElementById('newTokenDisplay');
  try {
    const res = await fetchJson('/api/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: input.value.trim() })
    });
    input.value = '';
    display.hidden = false;
    display.innerHTML = `
      <div style="color:var(--green); margin-bottom:4px;">Token created! Copy it now (will not be shown again):</div>
      <code style="user-select:all; font-weight:bold; color:#fff;">${res.token}</code>
    `;
    await loadDeveloperPanel();
  } catch(e) {}
}

async function deleteApiToken(id) {
  try {
    await fetchJson(`/api/tokens/${id}`, { method: 'DELETE' });
    await loadDeveloperPanel();
  } catch(e) {}
}

async function createWebhook(e) {
  e.preventDefault();
  const input = document.getElementById('webhookUrlInput');
  try {
    await fetchJson('/api/webhooks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: input.value.trim(), event: 'item.created' })
    });
    input.value = '';
    await loadDeveloperPanel();
  } catch(e) {}
}

async function deleteWebhook(id) {
  try {
    await fetchJson(`/api/webhooks/${id}`, { method: 'DELETE' });
    await loadDeveloperPanel();
  } catch(e) {}
}

function copyMcpConfig() {
  const snippet = document.getElementById('mcpConfigSnippet').textContent;
  navigator.clipboard.writeText(snippet);
  if (window.toast) window.toast('MCP configuration copied to clipboard!', 'success');
}

// --- Floating Onboarding Tour --------------------------------------------
function openOnboardingTour() {
  const modal = document.getElementById('tourModal');
  if (modal) modal.hidden = false;
}

function closeOnboardingTour() {
  const modal = document.getElementById('tourModal');
  if (modal) modal.hidden = true;
}

// --- Wire Tab Switching to Extension Loaders -----------------------------
document.addEventListener('click', (e) => {
  const tabBtn = e.target.closest('[role="tab"][data-tab]');
  if (!tabBtn) return;
  const tabName = tabBtn.dataset.tab;
  if (tabName === 'entities') loadEntities();
  if (tabName === 'cards') loadCards();
  if (tabName === 'developer') loadDeveloperPanel();
});

// Notes filters apply as soon as they change; typing in the search box waits
// for a short pause so every keystroke is not a request.
for (const elementId of Object.values(itemFilterFields)) {
  const element = document.getElementById(elementId);
  if (element && element.tagName === "SELECT") {
    element.addEventListener("change", () => applyItemFilters());
  }
}
const itemSearchEl = document.getElementById("itemSearch");
if (itemSearchEl) {
  itemSearchEl.addEventListener("input", () => {
    window.clearTimeout(searchDebounce);
    searchDebounce = window.setTimeout(
      () => applyItemFilters(null, {replace: true}),
      300
    );
  });
}

setInterval(() => {
  if (!document.hidden && typeof loadDashboard === "function") loadDashboard({quiet: true});
}, 60000);

// Fast-capture keyboard shortcuts (Cmd+Enter on #noteText, Enter on #urlText)
const noteTextEl = document.getElementById('noteText');
if (noteTextEl) {
  noteTextEl.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      addNote();
    }
  });
}

const urlTextEl = document.getElementById('urlText');
if (urlTextEl) {
  urlTextEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addUrl();
    }
  });
}

