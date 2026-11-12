let isSavingNote = false;
let isWatchingAnalysis = false;
let rewatchAnalysis = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingNoteActions = new Set();
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

function updateUrl(updates) {
  const url = new URL(window.location.href);
  for (const [name, value] of Object.entries(updates)) {
    if (value) {
      url.searchParams.set(name, value);
    } else {
      url.searchParams.delete(name);
    }
  }
  window.history.pushState({}, '', url);
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
}

function populateCategoryFilter(data) {
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

function applyItemFilters(event) {
  event.preventDefault();
  const values = {};
  const params = itemFilterParams();
  for (const name of Object.keys(itemFilterFields)) {
    values[name] = params.get(name) || '';
  }
  updateUrl(values);
  loadNotes();
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
      'Your note was saved, but the list could not refresh. Reload to try again.'
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

  const time = document.createElement('div');
  time.className = 'time';
  const status = note.processing_status ? ` · ${note.processing_status}` : '';
  const error = note.error_message ? ` · ${note.error_message}` : '';
  time.textContent = `#${note.id} · ${note.item_type}${status}${error} · ${note.created_at}`;

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  if (note.item_type === 'text') {
    const editButton = actionButton('Edit', 'secondary', () => {
      showEditNote(note, root);
    });
    const deleteButton = actionButton('Delete', 'danger', () => {
      deleteExistingNote(note, root, editButton, deleteButton);
    });
    actions.append(editButton, deleteButton);
  } else if (
    ['url', 'pdf', 'image', 'voice'].includes(note.item_type) &&
    note.processing_status === 'failed'
  ) {
    actions.append(
      actionButton('Retry', '', async () => {
        try {
          const updated = await fetchJson(
            `/api/items/${note.id}/retry`,
            {method: 'POST'},
            'Unable to retry capture processing.'
          );
          root.replaceWith(renderNote(updated));
          const label = captureTypeLabel(note.item_type);
          setStatus(
            'noteStatus',
            updated.processing_status === 'ready'
              ? `${label} processed.`
              : `${label} still failed.`
          );
          await loadLatestAnalysis();
          await loadDashboard();
        } catch {
          setStatus('noteStatus', 'Retry failed.', true);
        }
      })
    );
    actions.append(
      actionButton('Delete', 'danger', () => {
        deleteExistingNote(note, root, null, null);
      })
    );
  } else if (
    (note.item_type === 'image' || note.item_type === 'voice') &&
    note.processing_status === 'ready'
  ) {
    actions.append(
      actionButton(
        note.item_type === 'voice' ? 'Edit transcript' : 'Edit OCR text',
        'secondary',
        () => {
          showEditExtractedText(note, root);
        }
      )
    );
    actions.append(
      actionButton('Delete', 'danger', () => {
        deleteExistingNote(note, root, null, null);
      })
    );
  } else if (note.item_type === 'pdf') {
    actions.append(
      actionButton('Delete', 'danger', () => {
        deleteExistingNote(note, root, null, null);
      })
    );
  } else {
    actions.append(
      actionButton('Delete', 'danger', () => {
        deleteExistingNote(note, root, null, null);
      })
    );
  }

  root.append(text, time, actions);
  return root;
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
    setStatus('noteStatus', 'Saving extracted text…');
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
      setStatus('noteStatus', isVoice ? 'Transcript updated.' : 'Extracted text updated.');
      await loadLatestAnalysis();
    } catch {
      setStatus('noteStatus', 'Could not update extracted text.', true);
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
      setStatus('noteStatus', 'Edited note text cannot be empty.', true);
      textarea.focus();
      return;
    }
    if (pendingNoteActions.has(note.id)) return;

    pendingNoteActions.add(note.id);
    saveButton.disabled = true;
    cancelButton.disabled = true;
    saveButton.textContent = 'Saving…';
    setStatus('noteStatus', 'Saving your changes…');

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
      setStatus('noteStatus', 'Note updated.');
      await loadLatestAnalysis();
    } catch {
      setStatus(
        'noteStatus',
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
    setStatus('noteStatus', 'Edit cancelled.');
  });

  actions.append(saveButton, cancelButton);
  root.replaceChildren(textarea, actions);
  textarea.focus();
}

async function deleteExistingNote(note, root, editButton, deleteButton) {
  if (pendingNoteActions.has(note.id)) return;

  const confirmed = window.confirm(
    'Delete this item? This action cannot be undone.'
  );
  if (!confirmed) return;

  pendingNoteActions.add(note.id);
  if (editButton) editButton.disabled = true;
  if (deleteButton) {
    deleteButton.disabled = true;
    deleteButton.textContent = 'Deleting…';
  }
  setStatus('noteStatus', 'Deleting note…');

  try {
    await fetchJson(
      `/api/items/${note.id}`,
      {method: 'DELETE'},
      'Unable to delete note.'
    );
    LifeInboxPagination.afterLoadedNoteDeleted(itemPagination, note.id);
    root.remove();
    setStatus('noteStatus', 'Note deleted.');
    await loadLatestAnalysis('quiet');
  } catch {
    setStatus(
      'noteStatus',
      'We could not delete this note. It is still here—please try again.',
      true
    );
    if (editButton) editButton.disabled = false;
    if (deleteButton) {
      deleteButton.disabled = false;
      deleteButton.textContent = 'Delete';
    }
  } finally {
    pendingNoteActions.delete(note.id);
  }
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

async function loadNotes(
  failureMessage = 'We could not load your notes. Reload to try again.',
  {append = false} = {}
) {
  if (!LifeInboxPagination.beginListRequest(itemPagination)) {
    return false;
  }

  const params = itemFilterParams();
  if (!append) {
    LifeInboxPagination.resetForReload(itemPagination);
  }
  params.set('limit', String(itemPagination.pageSize));
  params.set('offset', String(itemPagination.loadedOffset));
  const filtered = [...params.keys()].some(
    key => key !== 'limit' && key !== 'offset' && params.get(key)
  );
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

    if (!append && !notes.length) {
      root.innerHTML = filtered
        ? '<div class="panel muted">No inbox items match these filters.</div>'
        : '<div class="panel muted">No saved notes yet. Capture a thought above to get started.</div>';
      setStatus(
        'searchStatus',
        filtered ? 'No results found.' : ''
      );
      return true;
    }

    const {textNotes, hasMore} = LifeInboxPagination.applyFetchedPage(
      itemPagination,
      notes,
      {append}
    );

    for (const note of textNotes) {
      root.appendChild(renderNote(note));
    }

    if (hasMore) {
      updateLoadMoreButton(failureMessage);
    }

    const loadedCount = itemPagination.loadedItems.length;
    setStatus(
      'searchStatus',
      filtered
        ? `${loadedCount}${hasMore ? '+' : ''} matching item${loadedCount === 1 ? '' : 's'}.`
        : hasMore
          ? `Showing ${loadedCount}+ items.`
          : ''
    );
    return true;
  } catch {
    if (append) {
      setStatus(
        'noteStatus',
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
      setStatus('noteStatus', failureMessage, true);
    }
    return false;
  } finally {
    LifeInboxPagination.endListRequest(itemPagination);
    if (itemPagination.hasMore) {
      updateLoadMoreButton(failureMessage);
    }
  }
}

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
    for (const topic of topics) {
      const row = document.createElement('div');
      row.className = 'topic';
      row.innerHTML = `<strong>${esc(topic.name)}</strong><div class="muted">${topic.item_count} item${topic.item_count === 1 ? '' : 's'}${topic.description ? ` · ${esc(topic.description)}` : ''}</div>`;
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
      upcomingEl.innerHTML = dashState.upcomingTasks.map(item => `
        <li class="cal-upcoming-item" id="upcoming-${item.id}">
          <span class="cal-day-tag mono">${esc(item.day || 'Soon')}</span>
          <span class="cal-upcoming-text">${esc(item.text)}</span>
        </li>
      `).join('');
    }

    // 7. Populate Pinned notes
    const pinnedEl = document.getElementById('pinnedList');
    let pins = defaultPinnedNotes;
    try {
      const stored = window.localStorage.getItem('lifeInbox.pinned');
      if (stored) pins = JSON.parse(stored);
    } catch {}

    if (pinnedEl) {
      pinnedEl.innerHTML = pins.map((p, idx) => `
        <li class="cal-pinned-item" title="${esc(p.detail || '')}">
          <span class="cal-star">★</span>
          <span class="cal-pinned-title">${esc(p.title)}</span>
          <button type="button" class="cal-pin-del" onclick="removePin(${idx})" title="Unpin">×</button>
        </li>
      `).join('');
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
  }
}

function renderDueTodayList() {
  const dueTodayEl = document.getElementById('dueTodayList');
  if (!dueTodayEl) return;
  dueTodayEl.innerHTML = dashState.todayTasks.map(task => `
    <li class="cal-task-item ${task.done ? 'completed' : ''}" id="task-${task.id}">
      <label class="cal-checkbox-label">
        <input type="checkbox" data-task-id="${task.id}" ${task.done ? 'checked' : ''} onchange="toggleTask('${task.id}', event.target.checked)" />
        <span class="cal-custom-box"></span>
        <span class="cal-task-name">${esc(task.text)}</span>
      </label>
    </li>
  `).join('');
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
  overdueEl.innerHTML = dashState.overdueTasks.map(task => `
    <li class="cal-task-item overdue ${task.done ? 'completed' : ''}" id="task-${task.id}">
      <label class="cal-checkbox-label">
        <input type="checkbox" data-task-id="${task.id}" ${task.done ? 'checked' : ''} onchange="toggleTask('${task.id}', event.target.checked)" />
        <span class="cal-custom-box rose"></span>
        <span class="cal-task-name">${esc(task.text)}</span>
      </label>
    </li>
  `).join('');
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

// Pinning notes
function promptAddPin() {
  const title = prompt('Enter a note title to pin:');
  if (!title) return;
  const detail = prompt('Optional details / notes:') || '';

  let pins = defaultPinnedNotes;
  try {
    const stored = window.localStorage.getItem('lifeInbox.pinned');
    if (stored) pins = JSON.parse(stored);
  } catch {}

  pins.push({ id: 'pin-' + Date.now(), title: title.trim(), detail: detail.trim() });
  try {
    window.localStorage.setItem('lifeInbox.pinned', JSON.stringify(pins));
  } catch {}

  loadDashboard();
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

function fillAskExample(text) {
  const input = document.getElementById('askQuestion');
  input.value = text;
  input.focus();
}

function renderAskResponse(data) {
  const root = document.getElementById('askOutput');
  root.innerHTML = '';
  const confidence = document.createElement('div');
  confidence.className = 'confidence';
  const modeLabel = data.mode ? ` · ${String(data.mode).replaceAll('_', ' ')}` : '';
  confidence.textContent = `Confidence: ${data.confidence} · ${data.answer_kind.replace('_', ' ')}${modeLabel}`;
  const answer = document.createElement('div');
  answer.style.whiteSpace = 'pre-line';
  answer.textContent = data.answer;
  root.append(confidence, answer);
  if (data.citations && data.citations.length) {
    const heading = document.createElement('h3');
    heading.textContent = 'Sources';
    root.appendChild(heading);
    for (const cite of data.citations) {
      const block = document.createElement('div');
      block.className = 'citation';
      const link = cite.preview_url
        ? `<a href="${esc(cite.preview_url)}" target="_blank" rel="noopener">Open source</a>`
        : '';
      block.innerHTML = `<strong>${esc(cite.title)}</strong> (#${cite.item_id} · ${esc(cite.source_kind)}) ${link}<div>${esc(cite.excerpt)}</div>`;
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
  setStatus('askStatus', 'Searching your inbox…');
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
    setStatus('askStatus', data.confidence === 'insufficient' ? 'Not enough evidence in your inbox.' : 'Answer ready.');
  } catch {
    setStatus('askStatus', 'Ask My Inbox failed.', true);
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
