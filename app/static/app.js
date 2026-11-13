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

async function fetchJson(url, options, friendlyError) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error(friendlyError);
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
    : `Note #${note.id} · saved ${savedAt.toLocaleString()}`;
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
  root.append(foot);
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
    if (insight.priority === 'High') add('High priority', 'high');
    if (insight.priority === 'Low') add('Low priority', 'low');
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

/* ---------- Dashboard ---------- */
// Task groups in the order they matter. The server decides which group a task
// is in (it knows the timezone and the time of day).
const TASK_GROUPS = [
  ['overdue', 'Overdue'],
  ['today', 'Today'],
  ['upcoming', 'Coming up'],
  ['unscheduled', 'No date'],
  ['snoozed', 'Snoozed']
];
const PRIORITY_RANK = {High: 0, Medium: 1, Low: 2};
const CHECK_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';

async function loadDashboard({quiet = false} = {}) {
  try {
    const [data, board] = await Promise.all([
      fetchJson('/api/dashboard', undefined, 'Unable to load dashboard.'),
      fetchJson('/api/attention', undefined, 'Unable to load tasks.')
    ]);
    if (data.pending_analysis_count > 0) watchAnalysis(false);
    dashboardState.data = data;
    dashboardState.board = board;
    renderDashboard();
    if (!quiet) setStatus('dashboardStatus', '');
  } catch {
    if (!quiet) {
      setStatus(
        'dashboardStatus',
        'We could not load your dashboard. Reload to try again.',
        true
      );
    }
  }
}

function sortTasks(tasks) {
  return [...tasks].sort(
    (first, second) =>
      (first.normalized_due_on || '9999').localeCompare(second.normalized_due_on || '9999')
      || (first.due_time || '99:99').localeCompare(second.due_time || '99:99')
      || (PRIORITY_RANK[first.priority] ?? 9) - (PRIORITY_RANK[second.priority] ?? 9)
      || first.id - second.id
  );
}

function priorityTag(priority) {
  if (priority === 'High') return '<span class="tag tag-high">High</span>';
  if (priority === 'Low') return '<span class="tag tag-low">Low</span>';
  return '';
}

function sentence(text) {
  const clean = String(text || '').trim();
  return clean ? clean.charAt(0).toUpperCase() + clean.slice(1) : '';
}

// A list that shows its first few rows and a "Show N more" toggle.
function previewList(key, items, renderRow, limit) {
  const expanded = dashboardState.expanded.has(key);
  const shown = expanded ? items : items.slice(0, limit);
  const more = items.length > limit
    ? `<button type="button" class="secondary chip more" data-action="expand" data-key="${esc(key)}" aria-expanded="${expanded}">${expanded ? 'Show fewer' : `Show ${items.length - limit} more`}</button>`
    : '';
  return {rows: shown.map(renderRow).join(''), more};
}

function taskRowHtml(task, groupKey, todayIso) {
  let when = dueLabel(task, todayIso);
  if (groupKey === 'snoozed' && task.snoozed_until) {
    when = `Snoozed until ${dueLabel({normalized_due_on: task.snoozed_until}, todayIso).toLowerCase()}`;
  } else if (groupKey === 'overdue' && task.normalized_due_on) {
    const late = daysBetween(parseIsoDate(task.normalized_due_on), parseIsoDate(todayIso));
    if (late > 1) when += ` · ${late} days late`;
  } else if (!task.normalized_due_on && task.timing_text) {
    when = `No date · “${task.timing_text}”`;
  }
  const busy = pendingTaskActions.has(task.id) ? ' disabled' : '';
  const name = esc(task.text);
  return `
    <li class="task-row" data-task-id="${task.id}">
      <button type="button" class="check" data-action="complete" data-task-id="${task.id}" aria-label="Mark “${name}” as done" title="Mark as done"${busy}>${CHECK_ICON}</button>
      <span class="task-body">
        <span class="task-text">${name}</span>
        <span class="task-due">${esc(when)}</span>
      </span>
      ${priorityTag(task.priority)}
      <button type="button" class="ghost-x" data-action="dismiss" data-task-id="${task.id}" aria-label="Remove “${name}” from your tasks" title="Not a task? Remove it"${busy}>&times;</button>
    </li>`;
}

function tasksCardHtml(board) {
  const todayIso = board.reference_date;
  const groups = TASK_GROUPS
    .map(([key, label]) => ({key, label, tasks: sortTasks(board[key] || [])}))
    .filter(group => group.tasks.length);
  const undo = dashboardState.undo
    ? `<div class="undo-bar" role="status"><span>${esc(dashboardState.undo.message)}</span><button type="button" class="secondary chip" data-action="undo">Undo</button></div>`
    : '';
  if (!groups.length) {
    return `
      <section class="panel card-tasks" aria-label="Tasks">
        <h3>Tasks</h3>
        ${undo}
        <div class="empty-state">
          <strong>Nothing to do right now.</strong>
          <span>When a note mentions something to do, it shows up here with its date.</span>
        </div>
      </section>`;
  }
  const body = groups.map(({key, label, tasks}) => {
    const limit = key === 'overdue' || key === 'today' ? 8 : 5;
    const {rows, more} = previewList(
      `tasks-${key}`,
      tasks,
      task => taskRowHtml(task, key, todayIso),
      limit
    );
    return `
      <div class="task-group group-${key}">
        <h4><span>${label}</span><span class="count">${tasks.length}</span></h4>
        <ul class="task-list">${rows}</ul>
        ${more}
      </div>`;
  }).join('');
  return `
    <section class="panel card-tasks" aria-label="Tasks">
      <h3>Tasks</h3>
      ${undo}
      ${body}
    </section>`;
}

function miniCardHtml(key, title, items, renderRow, {limit = 5, footer = ''} = {}) {
  if (!items || !items.length) return '';
  const {rows, more} = previewList(key, items, renderRow, limit);
  return `
    <section class="panel card-mini" aria-label="${esc(title)}">
      <h3>${esc(title)}<span class="count">${items.length}</span></h3>
      <ul class="mini-list">${rows}</ul>
      ${more}${footer}
    </section>`;
}

function renderDashboard() {
  const {data, board} = dashboardState;
  if (!data || !board) return;

  // Skip the DOM work (and keep focus and scroll) when nothing changed.
  const fingerprint = JSON.stringify([
    data,
    board,
    dashboardState.undo,
    [...dashboardState.expanded]
  ]);
  if (fingerprint === dashboardState.rendered) return;
  const firstRender = !dashboardState.rendered;
  dashboardState.rendered = fingerprint;

  const counts = Object.fromEntries(
    TASK_GROUPS.map(([key]) => [key, (board[key] || []).length])
  );
  const openTasks = Object.values(counts).reduce((sum, count) => sum + count, 0);
  updateQuickBadge(counts);

  const today = parseIsoDate(board.reference_date);
  const dateText = today.toLocaleDateString(
    undefined,
    {weekday: 'long', day: 'numeric', month: 'long'}
  );
  const headline = [];
  if (counts.overdue) headline.push(`${counts.overdue} overdue`);
  if (counts.today) headline.push(`${counts.today} due today`);
  if (counts.upcoming) headline.push(`${counts.upcoming} coming up`);
  document.getElementById('dashboardSummary').textContent = headline.length
    ? `${dateText}. ${sentence(headline.join(', '))}.`
    : `${dateText}. ${openTasks ? 'Nothing is due today.' : 'No open tasks.'}`;

  const root = document.getElementById('dashboard');
  root.classList.toggle('settled', !firstRender);

  if (!data.item_count) {
    root.innerHTML = `
      <div class="empty-state empty-hero">
        <strong>Your dashboard fills in as you capture.</strong>
        <span>Save a note and Life Inbox sorts it into a topic and pulls out anything you have to do.</span>
        <button type="button" data-goto="capture">Capture your first note</button>
      </div>`;
    return;
  }

  const stat = (label, value, kind = '') => `
    <div class="stat${kind && value ? ` stat-${kind}` : ''}">
      <span class="stat-label">${label}</span>
      <strong class="stat-value">${value}</strong>
    </div>`;

  const attention = miniCardHtml(
    'attention',
    'Needs attention',
    data.needs_review,
    item => `
      <li class="mini-row">
        <span class="mini-title">${esc(item.effective_title || item.title || item.raw_text || `Note ${item.id}`)}</span>
        <span class="mini-sub">${esc(item.reason || 'Needs a look')}</span>
      </li>`
  );
  const highPriority = miniCardHtml(
    'high',
    'High priority',
    data.high_priority,
    item => {
      const why = [item.topic_name, sentence(item.rationale)].filter(Boolean).join(' · ');
      return `
        <li class="mini-row">
          <span class="mini-title">${esc(item.effective_title || item.raw_text || `Note ${item.id}`)}</span>
          ${why ? `<span class="mini-sub">${esc(why)}</span>` : ''}
        </li>`;
    }
  );
  const recent = miniCardHtml(
    'recent',
    'Recently captured',
    data.recently_captured,
    item => {
      const kind = item.item_type === 'text' ? '' : ` · ${captureTypeLabel(item.item_type)}`;
      return `
        <li class="mini-row">
          <span class="mini-title">${esc(item.effective_title || item.raw_text || `Note ${item.id}`)}</span>
          <span class="mini-sub">${esc(friendlyTime(item.created_at))}${esc(kind)}</span>
        </li>`;
    },
    {footer: '<button type="button" class="secondary chip more" data-goto="notes">All notes</button>'}
  );
  const related = miniCardHtml(
    'related',
    'Related notes',
    data.related_items,
    relation => {
      const duplicate = relation.relation_type !== 'related';
      return `
        <li class="mini-row rel-row">
          <span class="mini-title">${esc(relation.title_a)}<span class="rel-mark" aria-hidden="true">↔</span><span class="sr-only"> is related to </span>${esc(relation.title_b)}</span>
          ${duplicate ? '<span class="tag tag-busy">Possible duplicate</span>' : ''}
          <button type="button" class="ghost-x" data-action="dismiss-relation" data-relation-id="${relation.id}" aria-label="Hide this link" title="Not related? Hide it">&times;</button>
        </li>`;
    },
    {limit: 4}
  );

  root.innerHTML = `
    <div class="stat-row">
      ${stat('Notes', data.item_count)}
      ${stat('Open tasks', openTasks)}
      ${stat('Overdue', counts.overdue, 'overdue')}
      ${stat('Due today', counts.today, 'today')}
    </div>
    <div class="dash-cols">
      ${tasksCardHtml(board)}
      <div class="dash-side">
        ${attention}
        ${highPriority}
        ${recent}
        ${related}
      </div>
    </div>`;
}

// The capture page's "Open dashboard" button carries what needs you.
function updateQuickBadge(counts) {
  const badge = document.getElementById('quickBadge');
  if (!badge) return;
  const text = counts.overdue
    ? `${counts.overdue} overdue`
    : counts.today
      ? `${counts.today} due today`
      : '';
  badge.textContent = text;
  badge.hidden = !text;
  badge.classList.toggle('is-overdue', Boolean(counts.overdue));
}

function findBoardTask(taskId) {
  const board = dashboardState.board || {};
  for (const [key] of TASK_GROUPS) {
    const task = (board[key] || []).find(entry => entry.id === taskId);
    if (task) return task;
  }
  return null;
}

function setTaskUndo(undo) {
  window.clearTimeout(dashboardState.undoTimer);
  dashboardState.undo = undo;
  if (undo) {
    dashboardState.undoTimer = window.setTimeout(() => {
      dashboardState.undo = null;
      renderDashboard();
    }, 8000);
  }
}

// Tick a task off ("complete") or remove one that is not really a task
// ("dismiss"). Either can be undone for a few seconds.
async function changeTask(taskId, action) {
  if (pendingTaskActions.has(taskId)) return;
  const task = findBoardTask(taskId);
  if (!task) return;

  pendingTaskActions.add(taskId);
  const row = document.querySelector(`#dashboard .task-row[data-task-id="${taskId}"]`);
  if (row) {
    row.classList.add(action === 'complete' ? 'is-done' : 'is-removed');
    row.querySelectorAll('button').forEach(button => { button.disabled = true; });
  }
  try {
    await fetchJson(
      `/api/tasks/${taskId}/${action}`,
      {method: 'POST'},
      'Unable to update task.'
    );
    setTaskUndo({
      taskId,
      message: `${action === 'complete' ? 'Done' : 'Removed'}: ${task.text}`
    });
    await sleep(380); // let the tick land before the row leaves
    pendingTaskActions.delete(taskId);
    await loadDashboard();
  } catch {
    pendingTaskActions.delete(taskId);
    if (row) {
      row.classList.remove('is-done', 'is-removed');
      row.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
    setStatus(
      'dashboardStatus',
      'We could not update that task. Please try again.',
      true
    );
  }
}

async function undoTaskChange() {
  const undo = dashboardState.undo;
  if (!undo) return;
  setTaskUndo(null);
  try {
    await fetchJson(
      `/api/tasks/${undo.taskId}/reopen`,
      {method: 'POST'},
      'Unable to reopen task.'
    );
    await loadDashboard();
  } catch {
    renderDashboard();
    setStatus(
      'dashboardStatus',
      'We could not bring that task back. Please try again.',
      true
    );
  }
}

async function dismissRelation(relationId) {
  try {
    await fetchJson(
      `/api/relations/${relationId}/dismiss`,
      {method: 'POST'},
      'Unable to hide link.'
    );
    await loadDashboard();
  } catch {
    setStatus(
      'dashboardStatus',
      'We could not hide that link. Please try again.',
      true
    );
  }
}

document.getElementById('dashboard').addEventListener('click', event => {
  const target = event.target.closest('[data-action]');
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  if (action === 'expand') {
    const key = target.dataset.key;
    if (dashboardState.expanded.has(key)) dashboardState.expanded.delete(key);
    else dashboardState.expanded.add(key);
    renderDashboard();
  } else if (action === 'complete' || action === 'dismiss') {
    changeTask(Number(target.dataset.taskId), action);
  } else if (action === 'undo') {
    undoTaskChange();
  } else if (action === 'dismiss-relation') {
    dismissRelation(Number(target.dataset.relationId));
  }
});

/* ---------- Ask ---------- */
function askExample(text) {
  const input = document.getElementById('askQuestion');
  input.value = text;
  document.getElementById('askForm').requestSubmit();
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

async function loadAuthUser() {
  try {
    const user = await fetchJson('/api/auth/me', {}, 'Unable to load session.');
    document.getElementById('authUser').textContent =
      `Signed in as ${user.display_name} <${user.email}> (${user.auth_provider})`;
  } catch {
    document.getElementById('authUser').textContent = 'Not signed in';
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
    setStatus('authStatus', 'Signed out.');
    await loadAuthUser();
  } catch {
    setStatus('authStatus', 'Sign-out failed.', true);
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

// Notes filters apply as soon as they change; typing in the search box waits
// for a short pause so every keystroke is not a request.
for (const elementId of Object.values(itemFilterFields)) {
  const element = document.getElementById(elementId);
  if (element.tagName === 'SELECT') {
    element.addEventListener('change', () => applyItemFilters());
  }
}
document.getElementById('itemSearch').addEventListener('input', () => {
  window.clearTimeout(searchDebounce);
  searchDebounce = window.setTimeout(
    () => applyItemFilters(null, {replace: true}),
    300
  );
});

// A task with a clock time becomes overdue as the time passes, so keep the
// dashboard current while the page is open. Nothing is redrawn unless the
// data actually changed.
setInterval(() => {
  if (!document.hidden) loadDashboard({quiet: true});
}, 60000);
