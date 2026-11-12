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
      if (window.toast) window.toast(`Converted to ${res.kind} card!`);
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
      if (window.toast) window.toast(`Extracted ${res.length} entities! Check 05 Entities tab.`);
    } catch {
      extractEntBtn.textContent = '👤 Entities';
    }
  };

  const flashcardBtn = document.createElement('button');
  flashcardBtn.type = 'button';
  flashcardBtn.className = 'note-tool-btn';
  flashcardBtn.textContent = '🗂️ Flashcard';
  flashcardBtn.title = 'Generate study flashcards from this note';
  flashcardBtn.onclick = async () => {
    flashcardBtn.textContent = 'Making…';
    try {
      const res = await fetchJson(`/api/items/${note.id}/flashcards`, {method: 'POST'});
      flashcardBtn.textContent = `🗂️ ${res.length} Cards`;
      if (window.toast) window.toast(`Created ${res.length} flashcards! Check 07 Flashcards tab.`);
    } catch {
      flashcardBtn.textContent = '🗂️ Flashcard';
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

  featureBar.append(makeCardBtn, extractEntBtn, flashcardBtn, sensBtn);
  root.append(text, time, featureBar, actions);
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

async function loadDashboard() {
  try {
    const data = await fetchJson(
      '/api/dashboard',
      undefined,
      'Unable to load dashboard.'
    );
    if (data.pending_analysis_count > 0) watchAnalysis(false);
    const root = document.getElementById('dashboard');
    const section = (title, body) => `
      <div class="panel">
        <h3>${esc(title)}</h3>
        ${body}
      </div>
    `;
    const listOrEmpty = (items, render) =>
      items && items.length
        ? `<ul>${items.map(render).join('')}</ul>`
        : '<p class="muted">Nothing here yet.</p>';

    root.innerHTML = `
      <div class="dashboard-grid">
        ${section('Topics / Spaces', listOrEmpty(data.topics, t =>
          `<li><strong>${esc(t.name)}</strong> · ${t.item_count}</li>`
        ))}
        ${section('High Priority', listOrEmpty(data.high_priority, item =>
          `<li>${esc(item.effective_title || item.raw_text || ('#' + item.id))}${item.rationale ? `<div class="why">Why: ${esc(item.rationale)}</div>` : ''}</li>`
        ))}
        ${section('Open Tasks', listOrEmpty(data.open_tasks, task =>
          `<li><strong>${esc(task.text)}</strong> <span class="pill">${esc(task.priority)}</span>${task.timing_text ? ` · ${esc(task.timing_text)}` : ''}</li>`
        ))}
        ${section('Recently Captured', listOrEmpty(data.recently_captured, item =>
          `<li>#${item.id} · ${esc((item.raw_text || '').slice(0, 80))}</li>`
        ))}
        ${section('Needs Review', listOrEmpty(data.needs_review, item =>
          `<li>#${item.id} · ${esc(item.reason || 'Review needed')}</li>`
        ))}
        ${section('Related Items', listOrEmpty(data.related_items, rel =>
          `<li>#${rel.item_id_a} ↔ #${rel.item_id_b} <span class="pill">${esc(rel.relation_type)}</span></li>`
        ))}
      </div>
    `;
    setStatus('dashboardStatus', 'Dashboard updated.');
  } catch {
    setStatus('dashboardStatus', 'We could not load the organized dashboard.', true);
  }
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

/* =========================================================================
   Applied AI UI Extensions: Entities Graph, Smart Cards, Flashcards, API
   ========================================================================= */

// --- 05 · Entities & Knowledge Graph -------------------------------------
let allEntitiesCache = [];
let entityGraphNodes = [];
let entityGraphAnimFrame = null;

async function loadEntities(typeFilter = '') {
  const statusEl = document.getElementById('entitiesStatus');
  const listEl = document.getElementById('entitiesList');
  if (!listEl) return;
  try {
    const url = typeFilter ? `/api/entities?type=${encodeURIComponent(typeFilter)}` : '/api/entities';
    const entities = await fetchJson(url, {}, 'Failed to load entities');
    allEntitiesCache = entities;
    if (entities.length === 0) {
      statusEl.textContent = 'No entities discovered yet. Click "Extract Entities from All Notes" to analyze your inbox!';
      listEl.innerHTML = '';
      drawEntityGraph([]);
      return;
    }
    statusEl.textContent = `Found ${entities.length} entities across your notes:`;
    listEl.innerHTML = entities.map(ent => `
      <div class="entity-card" onclick="showDossier(${ent.id})">
        <div class="entity-card-head">
          <span class="feature-badge ${ent.type}">${ent.type === 'person' ? '👤 Person' : ent.type === 'place' ? '📍 Place' : '🏢 Org'}</span>
          <span class="mono" style="font-size:12px; color:var(--muted)">${ent.mention_count || 1} mentions</span>
        </div>
        <div class="entity-card-title">${esc(ent.name)}</div>
        <div class="muted" style="font-size:12px; margin-top:6px;">Click to view full dossier & related notes &rarr;</div>
      </div>
    `).join('');

    drawEntityGraph(entities);
  } catch (err) {
    statusEl.textContent = 'Failed to load entities.';
  }
}

function filterEntitiesByType(type) {
  document.querySelectorAll('#panel-entities .feature-bar .chip').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(type) || (!type && btn.textContent === 'All'));
  });
  loadEntities(type);
}

async function showDossier(entityId) {
  const dossierEl = document.getElementById('entityDossier');
  const titleEl = document.getElementById('dossierTitle');
  const typeEl = document.getElementById('dossierType');
  const itemsEl = document.getElementById('dossierItems');
  if (!dossierEl) return;

  try {
    const data = await fetchJson(`/api/entities/${entityId}`, {}, 'Failed to fetch dossier');
    dossierEl.hidden = false;
    titleEl.textContent = `Dossier: ${data.name}`;
    typeEl.innerHTML = `<span class="feature-badge ${data.type}">${data.type.toUpperCase()}</span>`;
    
    if (!data.items || data.items.length === 0) {
      itemsEl.innerHTML = '<div class="muted">No items explicitly linked yet.</div>';
    } else {
      itemsEl.innerHTML = data.items.map(item => `
        <div style="padding:12px; background:var(--void); border:1px solid var(--line); border-radius:var(--radius-inner);">
          <div style="font-weight:500; font-size:14px; color:var(--text);">${esc(item.title || item.raw_text?.slice(0, 80) || 'Inbox Item')}</div>
          <div class="muted" style="font-size:12px; margin-top:4px;">${esc(item.raw_text?.slice(0, 160) || '')}</div>
          <div class="mono" style="font-size:11px; color:var(--indigo-text); margin-top:6px;">Item #${item.id} · ${item.item_type} · ${item.created_at}</div>
        </div>
      `).join('');
    }
    dossierEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    console.error(e);
  }
}

function closeDossier() {
  const dossierEl = document.getElementById('entityDossier');
  if (dossierEl) dossierEl.hidden = true;
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
    await loadEntities();
  } catch (e) {
    statusEl.textContent = 'Entity extraction encountered an issue.';
  }
}

function drawEntityGraph(entities) {
  const canvas = document.getElementById('entityGraphCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const width = rect.width;
  const height = rect.height;
  const cx = width / 2;
  const cy = height / 2;

  // Center node: "Life Inbox"
  const nodes = [{
    id: 0,
    name: "Life Inbox",
    type: "center",
    x: cx,
    y: cy,
    radius: 28,
    color: "#6366F1"
  }];

  const displayList = entities.slice(0, 18);
  const angleStep = (2 * Math.PI) / (displayList.length || 1);
  const radius = Math.min(width, height) * 0.38;

  displayList.forEach((ent, i) => {
    const angle = i * angleStep;
    const color = ent.type === 'person' ? '#22D3EE' : ent.type === 'place' ? '#FBBF24' : '#8B5CF6';
    nodes.push({
      id: ent.id,
      name: ent.name,
      type: ent.type,
      x: cx + radius * Math.cos(angle) + (Math.random() - 0.5) * 20,
      y: cy + radius * Math.sin(angle) + (Math.random() - 0.5) * 20,
      radius: Math.min(22, 14 + (ent.mention_count || 1) * 2),
      color: color
    });
  });

  ctx.clearRect(0, 0, width, height);

  // Draw connecting edges
  for (let i = 1; i < nodes.length; i++) {
    const target = nodes[i];
    ctx.beginPath();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
    ctx.lineWidth = 1.2;
    ctx.moveTo(cx, cy);
    ctx.lineTo(target.x, target.y);
    ctx.stroke();

    // Secondary ring links
    if (i > 1) {
      const prev = nodes[i - 1];
      ctx.beginPath();
      ctx.strokeStyle = "rgba(99, 102, 241, 0.08)";
      ctx.lineWidth = 1;
      ctx.moveTo(prev.x, prev.y);
      ctx.lineTo(target.x, target.y);
      ctx.stroke();
    }
  }

  // Draw nodes
  nodes.forEach(node => {
    ctx.beginPath();
    ctx.arc(node.x, node.y, node.radius, 0, 2 * Math.PI);
    ctx.fillStyle = node.color;
    ctx.shadowColor = node.color;
    ctx.shadowBlur = 12;
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.strokeStyle = "#FFFFFF";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.fillStyle = "#FFFFFF";
    ctx.font = node.type === 'center' ? "600 12px Inter, sans-serif" : "500 11px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(node.name.length > 12 ? node.name.slice(0, 11) + '…' : node.name, node.x, node.y + node.radius + 14);
  });
}

// --- 06 · Smart Cards & Expense Tracker ----------------------------------
async function loadCards(kindFilter = '') {
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
      categoryBreakdownEl.innerHTML = cats.length ? cats.map(([cat, amt]) => `
        <span class="feature-badge" style="background:rgba(52, 211, 153, 0.15); color:var(--green)">
          ${esc(cat)}: ₹${amt.toLocaleString('en-IN')}
        </span>
      `).join('') : '<span class="muted" style="font-size:12px;">No receipt breakdowns yet.</span>';
    }

    // 2. Fetch cards
    const url = kindFilter ? `/api/cards?kind=${encodeURIComponent(kindFilter)}` : '/api/cards';
    const cards = await fetchJson(url, {}, 'Failed to load cards');
    if (cards.length === 0) {
      statusEl.textContent = 'No smart cards created yet. Click "Auto-Format Notes into Cards" below!';
      listEl.innerHTML = '';
      return;
    }
    statusEl.textContent = `Displaying ${cards.length} structured cards:`;
    listEl.innerHTML = cards.map(c => {
      const k = c.kind || 'card';
      const f = c.kind_fields || {};
      let bodyHtml = '';
      if (k === 'recipe') {
        const ings = Array.isArray(f.ingredients) ? f.ingredients.slice(0, 4).join(', ') : (f.ingredients || '');
        bodyHtml = `
          <div class="typed-card-fields">
            <div class="typed-card-field"><dt>Prep time:</dt><dd>${esc(f.prep_time || 'N/A')}</dd></div>
            <div class="typed-card-field"><dt>Ingredients:</dt><dd>${esc(ings)}</dd></div>
          </div>
        `;
      } else if (k === 'book') {
        bodyHtml = `
          <div class="typed-card-fields">
            <div class="typed-card-field"><dt>Author:</dt><dd>${esc(f.author || 'Unknown')}</dd></div>
            <div class="typed-card-field"><dt>Genre:</dt><dd>${esc(f.genre || 'General')}</dd></div>
            <div class="typed-card-field"><dt>Status:</dt><dd>${esc(f.status || 'To Read')}</dd></div>
          </div>
        `;
      } else if (k === 'receipt') {
        bodyHtml = `
          <div class="typed-card-fields">
            <div class="typed-card-field"><dt>Amount:</dt><dd><strong style="color:var(--green); font-size:1.15rem;">₹${esc(String(f.amount || '0'))}</strong></dd></div>
            <div class="typed-card-field"><dt>Category:</dt><dd>${esc(f.category || 'General')}</dd></div>
            <div class="typed-card-field"><dt>Date:</dt><dd>${esc(f.date || 'Recent')}</dd></div>
          </div>
        `;
      } else if (k === 'contact') {
        bodyHtml = `
          <div class="typed-card-fields">
            <div class="typed-card-field"><dt>Role/Org:</dt><dd>${esc(f.role || f.company || 'Contact')}</dd></div>
            <div class="typed-card-field"><dt>Contact:</dt><dd>${esc(f.phone || f.email || 'N/A')}</dd></div>
          </div>
        `;
      } else {
        bodyHtml = `<div class="muted" style="font-size:13px;">${esc(c.raw_text?.slice(0, 160) || '')}</div>`;
      }

      return `
        <div class="typed-card">
          <span class="typed-card-badge ${k}">${k}</span>
          <div class="typed-card-title">${esc(f.title || f.name || f.vendor || c.title || 'Untitled Card')}</div>
          ${bodyHtml}
          <div class="mono" style="font-size:11px; color:var(--muted); margin-top:auto;">Note #${c.id} · ${c.created_at}</div>
        </div>
      `;
    }).join('');
  } catch (err) {
    statusEl.textContent = 'Failed to load cards.';
  }
}

function filterCardsByKind(kind) {
  document.querySelectorAll('#panel-cards .feature-bar .chip').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(kind) || (!kind && btn.textContent === 'All'));
  });
  loadCards(kind);
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
    await loadCards();
  } catch (e) {
    statusEl.textContent = 'Error formatting notes into cards.';
  }
}

// --- 07 · Flashcards & Spaced Repetition Station -------------------------
let dueFlashcardsList = [];
let currentCardIndex = 0;
let isCurrentCardFlipped = false;

async function loadFlashcardsStation() {
  const metaEl = document.getElementById('flashcardMeta');
  const contentEl = document.getElementById('flashcardContent');
  const actionsEl = document.getElementById('flashcardActions');
  const cardEl = document.getElementById('activeFlashcard');
  const flipBtn = document.getElementById('flipCardBtn');
  const forgotBtn = document.getElementById('cardForgotBtn');
  const rememberedBtn = document.getElementById('cardRememberedBtn');
  const resurfaceEl = document.getElementById('resurfaceList');

  try {
    // 1. Load resurfacing items
    const res = await fetchJson('/api/resurface', {}, 'Failed to load resurfaced items');
    if (resurfaceEl) {
      const items = [...(res.on_this_day || []), ...(res.random_discovery || [])];
      resurfaceEl.innerHTML = items.length ? items.map(it => `
        <div style="padding:14px; background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-card); display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-weight:500; color:var(--text);">${esc(it.title || it.raw_text?.slice(0, 100) || 'Note')}</div>
            <div class="muted" style="font-size:12px; margin-top:2px;">${esc(it.raw_text?.slice(0, 160) || '')}</div>
          </div>
          <span class="mono" style="font-size:11px; color:var(--amber); white-space:nowrap; margin-left:14px;">${it.resurface_reason || 'Rediscovery'}</span>
        </div>
      `).join('') : '<div class="muted">No historical resurfacing notes yet.</div>';
    }

    // 2. Load due flashcards
    const due = await fetchJson('/api/flashcards/due', {}, 'Failed to load due flashcards');
    dueFlashcardsList = due;
    currentCardIndex = 0;
    isCurrentCardFlipped = false;

    // Update Box counters
    for (let b = 0; b <= 4; b++) {
      const countEl = document.getElementById(`box${b}Count`);
      if (countEl) {
        countEl.textContent = due.filter(c => c.box === b).length;
      }
    }

    if (due.length === 0) {
      metaEl.textContent = 'All caught up!';
      contentEl.textContent = 'No flashcards due right now. Click "Generate Flashcards" below to generate study cards from your notes.';
      actionsEl.hidden = true;
      cardEl.classList.remove('flipped');
      return;
    }

    renderActiveFlashcard();
  } catch (e) {
    console.error(e);
  }
}

function renderActiveFlashcard() {
  const metaEl = document.getElementById('flashcardMeta');
  const contentEl = document.getElementById('flashcardContent');
  const actionsEl = document.getElementById('flashcardActions');
  const cardEl = document.getElementById('activeFlashcard');
  const flipBtn = document.getElementById('flipCardBtn');
  const forgotBtn = document.getElementById('cardForgotBtn');
  const rememberedBtn = document.getElementById('cardRememberedBtn');

  if (currentCardIndex >= dueFlashcardsList.length) {
    metaEl.textContent = 'Session Complete! 🎉';
    contentEl.textContent = 'You have reviewed all due flashcards for today.';
    actionsEl.hidden = true;
    cardEl.classList.remove('flipped');
    return;
  }

  const card = dueFlashcardsList[currentCardIndex];
  isCurrentCardFlipped = false;
  cardEl.classList.remove('flipped');
  metaEl.textContent = `Card ${currentCardIndex + 1} of ${dueFlashcardsList.length} · Box ${card.box || 0}`;
  contentEl.textContent = card.front;
  actionsEl.hidden = false;
  flipBtn.hidden = false;
  forgotBtn.hidden = true;
  rememberedBtn.hidden = true;
}

function flipActiveCard() {
  const card = dueFlashcardsList[currentCardIndex];
  if (!card) return;
  const contentEl = document.getElementById('flashcardContent');
  const cardEl = document.getElementById('activeFlashcard');
  const flipBtn = document.getElementById('flipCardBtn');
  const forgotBtn = document.getElementById('cardForgotBtn');
  const rememberedBtn = document.getElementById('cardRememberedBtn');

  isCurrentCardFlipped = !isCurrentCardFlipped;
  cardEl.classList.toggle('flipped', isCurrentCardFlipped);
  if (isCurrentCardFlipped) {
    contentEl.textContent = card.back;
    flipBtn.hidden = true;
    forgotBtn.hidden = false;
    rememberedBtn.hidden = false;
  } else {
    contentEl.textContent = card.front;
    flipBtn.hidden = false;
    forgotBtn.hidden = true;
    rememberedBtn.hidden = true;
  }
}

async function reviewActiveCard(remembered) {
  const card = dueFlashcardsList[currentCardIndex];
  if (!card) return;
  try {
    await fetchJson(`/api/flashcards/${card.id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remembered: remembered })
    });
    if (window.toast) {
      window.toast(remembered ? 'Card promoted to next Leitner box! 🚀' : 'Card reset to Box 0 for review tomorrow.');
    }
    currentCardIndex++;
    renderActiveFlashcard();
  } catch (e) {
    console.error(e);
  }
}

async function generateFlashcardsForAllNotes() {
  const contentEl = document.getElementById('flashcardContent');
  contentEl.textContent = 'Generating study flashcards across notes…';
  try {
    const items = await fetchJson('/api/items?limit=25', {}, 'Failed to fetch items');
    let created = 0;
    for (const it of items) {
      try {
        const cards = await fetchJson(`/api/items/${it.id}/flashcards`, { method: 'POST' });
        created += cards.length;
      } catch (e) {}
    }
    await loadFlashcardsStation();
    if (window.toast) window.toast(`Generated ${created} flashcards!`);
  } catch (e) {
    console.error(e);
  }
}

// --- 08 · Developer & MCP Panel -----------------------------------------
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
  if (window.toast) window.toast('MCP configuration copied to clipboard!');
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
  if (tabName === 'flashcards') loadFlashcardsStation();
  if (tabName === 'developer') loadDeveloperPanel();
});
