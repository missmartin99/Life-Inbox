/* Jin speaks: tap the cat, talk, and she answers out loud.
 *
 * Self-contained and loaded after playful.js. It:
 *  - takes over the cat's tap/Enter/Space (instead of her yawn sound),
 *  - records 16 kHz mono WAV in the browser with silence auto-stop,
 *  - sends it to /api/assistant/voice, shows the answer in her bubble,
 *  - speaks it via /api/assistant/speak (Gnani), or the browser voice as a fallback.
 * While this is active, her synthesized sounds are muted (window.JinVoice.quiet).
 */
(() => {
  'use strict';
  const wrap = document.querySelector('.cat-wrap');
  const cat = wrap && wrap.querySelector('.cat');
  const bubble = wrap && wrap.querySelector('.cat-bubble');
  if (!wrap || !cat || !bubble) return;

  const TARGET_RATE = 16000;
  const MAX_RECORD_MS = 25000;
  const MIN_SECONDS = 0.5;
  const SPEECH_LEVEL = 0.02;
  const QUIET_LEVEL = 0.012;
  const END_AFTER_QUIET_MS = 1500;
  const SILENT_PEAK = 0.004;
  const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';

  const state = {quiet: true, mode: 'idle'}; // idle | listening | thinking | speaking
  window.JinVoice = state;

  const style = document.createElement('style');
  style.textContent = `
    .cat-wrap.listening .cat-svg { filter: drop-shadow(0 0 calc(6px + var(--lvl, 0) * 14px) rgba(129,140,248,.9)); }
    .cat-wrap.thinking .cat-svg { filter: drop-shadow(0 0 8px rgba(165,180,252,.6)); }
    .cat-wrap.talking .cat-mouth { opacity: 0; }
    .cat-wrap.talking .cat-mouth-open { animation: jin-talk .32s ease-in-out infinite; }
    @keyframes jin-talk { 0%,100% { opacity: 0; transform: scale(.4); } 50% { opacity: 1; transform: scale(1.3); } }
    .cat-bubble.voice { max-width: min(320px, 80vw); white-space: normal; text-align: left; }
    @media (prefers-reduced-motion: reduce) { .cat-wrap.talking .cat-mouth-open { animation: none; opacity: 1; } }
  `;
  document.head.append(style);

  let keepAwake = 0;
  let hideTimer = 0;
  function show(text, ms = 0) {
    bubble.textContent = text;
    bubble.classList.add('voice');
    bubble.classList.remove('show');
    void bubble.offsetWidth;
    bubble.classList.add('show');
    window.clearTimeout(hideTimer);
    if (ms) hideTimer = window.setTimeout(() => bubble.classList.remove('show'), ms);
  }

  function setMode(mode) {
    state.mode = mode;
    window.dispatchEvent(new CustomEvent('jin-mode', {detail: mode}));
    for (const name of ['listening', 'thinking', 'talking']) wrap.classList.remove(name);
    if (mode === 'listening') wrap.classList.add('listening');
    if (mode === 'thinking') wrap.classList.add('thinking');
    if (mode === 'speaking') wrap.classList.add('talking');
    window.clearInterval(keepAwake);
    if (mode === 'idle') {
      wrap.style.removeProperty('--lvl');
      window.setTimeout(() => wrap.classList.remove('awake'), 2500);
    } else {
      wrap.classList.add('awake');
      keepAwake = window.setInterval(() => wrap.classList.add('awake'), 500);
    }
    cat.setAttribute('aria-label', mode === 'idle'
      ? 'Jin the cat. Tap to talk to her. Right-click for pet, feed, play or rename.'
      : `Jin is ${mode === 'speaking' ? 'talking' : mode}. Tap to stop.`);
  }

  /* ---------- recording ---------- */
  let rec = null;
  const player = new Audio();
  // Catty voice: a higher, slightly quicker pitch (pitch follows speed when preservesPitch is off).
  const CAT_PITCH = 1.14;
  player.preservesPitch = false; player.mozPreservesPitch = false; player.webkitPreservesPitch = false;
  let objectUrl = '';

  function stopSpeaking() {
    player.pause();
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = ''; }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  function downsample(chunks, inRate, outRate) {
    let total = 0;
    chunks.forEach(c => { total += c.length; });
    const all = new Float32Array(total);
    let off = 0;
    chunks.forEach(c => { all.set(c, off); off += c.length; });
    if (inRate === outRate) return all;
    const ratio = inRate / outRate;
    const out = new Float32Array(Math.floor(all.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const a = Math.floor(i * ratio);
      const b = Math.min(all.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = a; j < b; j++) sum += all[j];
      out[i] = b > a ? sum / (b - a) : 0;
    }
    return out;
  }

  function encodeWav(samples, rate) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buf);
    const str = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, 'data'); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const x = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Blob([buf], {type: 'audio/wav'});
  }

  async function startListening() {
    stopSpeaking();
    player.src = SILENT_WAV;           // unlock playback inside this tap
    player.play().catch(() => {});
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      show("This browser can't record. Try Chrome or Safari.", 5000);
      return;
    }
    const ctx = new AC();
    ctx.resume();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1},
      });
    } catch (err) {
      ctx.close();
      show('I need the microphone. Allow it in the address bar (and macOS Settings → Privacy → Microphone).', 7000);
      return;
    }
    const source = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
    rec = {ctx, stream, source, proc, mute, chunks: [], peak: 0, heard: false, quietSince: 0, startedAt: Date.now(), timer: 0};

    proc.onaudioprocess = ev => {
      if (!rec) return;
      const input = ev.inputBuffer.getChannelData(0);
      rec.chunks.push(new Float32Array(input));
      let sum = 0;
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
      const level = Math.sqrt(sum / input.length);
      rec.peak = Math.max(rec.peak, level);
      wrap.style.setProperty('--lvl', Math.min(1, level * 8).toFixed(2));
      const now = Date.now();
      if (level > SPEECH_LEVEL) { rec.heard = true; rec.quietSince = 0; }
      else if (rec.heard && level < QUIET_LEVEL) {
        if (!rec.quietSince) rec.quietSince = now;
        else if (now - rec.quietSince > END_AFTER_QUIET_MS) finishListening();
      }
    };
    rec.timer = window.setInterval(() => {
      if (!rec) return;
      const secs = (Date.now() - rec.startedAt) / 1000;
      if (secs >= 2 && rec.peak < SILENT_PEAK) show("I can't hear anything… is the right microphone on?");
      if (secs * 1000 > MAX_RECORD_MS) finishListening();
    }, 300);

    setMode('listening');
  }

  function cancelListening() {
    if (!rec) return;
    const r = rec; rec = null;
    cleanup(r);
    setMode('idle');
    bubble.classList.remove('show');
  }

  function cleanup(r) {
    window.clearInterval(r.timer);
    r.proc.onaudioprocess = null;
    try { r.source.disconnect(); r.proc.disconnect(); r.mute.disconnect(); } catch (e) { /* already gone */ }
    r.stream.getTracks().forEach(t => t.stop());
    r.ctx.close();
  }

  function finishListening() {
    if (!rec) return;
    const r = rec; rec = null;
    const inRate = r.ctx.sampleRate;
    cleanup(r);
    const samples = downsample(r.chunks, inRate, TARGET_RATE);
    const seconds = samples.length / TARGET_RATE;
    if (r.peak < SILENT_PEAK) {
      setMode('idle');
      show("I couldn't hear anything. Check your microphone (macOS Settings → Privacy → Microphone) and tap me again.", 7000);
      return;
    }
    if (seconds < MIN_SECONDS) {
      setMode('idle');
      show(`That was only ${seconds.toFixed(1)}s. Tap me, speak, then pause.`, 4500);
      return;
    }
    const form = new FormData();
    form.append('file', encodeWav(samples, TARGET_RATE), 'voice.wav');
    ask(form);
  }

  /* ---------- asking and speaking ---------- */
  async function ask(form) {
    setMode('thinking');
    bubble.classList.remove('show');
    let data;
    try {
      const response = await fetch('/api/assistant/voice', {method: 'POST', body: form});
      data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMode('idle');
        show(typeof data.detail === 'string' ? data.detail : 'Something went wrong. Try again?', 6000);
        return;
      }
    } catch (err) {
      setMode('idle');
      show("I couldn't reach the app. Is it running?", 5000);
      return;
    }
    if (data.saved_item_id && typeof window.loadNotes === 'function') {
      try { window.loadNotes(); } catch (e) { /* list refresh is best effort */ }
    }
    // Jin answers out loud only: no caption bubble for her replies.
    const reply = data.reply || '';
    bubble.classList.remove('show');
    await speak(data.speech || reply);
    setMode('idle');
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => bubble.classList.remove('show'), 4000);
  }

  function browserVoice(text) {
    return new Promise(resolve => {
      if (!window.speechSynthesis) { resolve(); return; }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.pitch = 1.7; utterance.rate = 1.1;
      const voices = window.speechSynthesis.getVoices() || [];
      const pick = voices.find(v => /en-IN|hi-IN/i.test(v.lang)) || voices.find(v => /^en/i.test(v.lang));
      if (pick) utterance.voice = pick;
      utterance.onend = utterance.onerror = () => resolve();
      window.speechSynthesis.speak(utterance);
    });
  }

  async function speak(text) {
    if (!text) return;
    setMode('speaking');
    try {
      const response = await fetch('/api/assistant/speak', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      if (!response.ok) throw new Error(String(response.status));
      const blob = await response.blob();
      if (state.mode !== 'speaking') return; // tapped to stop meanwhile
      objectUrl = URL.createObjectURL(blob);
      player.src = objectUrl;
      player.playbackRate = CAT_PITCH;
      await new Promise((resolve, reject) => {
        player.onended = resolve;
        player.onpause = resolve;
        player.onerror = reject;
        player.play().catch(reject);
      });
    } catch (err) {
      if (state.mode === 'speaking') await browserVoice(text); // no Gnani key, or autoplay blocked
    } finally {
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = ''; }
    }
  }

  /* ---------- wiring ---------- */
  function onTap() {
    if (state.mode === 'listening') { finishListening(); return; }
    if (state.mode === 'thinking') return;
    if (state.mode === 'speaking') { stopSpeaking(); setMode('idle'); bubble.classList.remove('show'); return; }
    startListening();
  }

  // Capture phase on the wrapper runs before playful.js's yawn handlers on .cat.
  wrap.addEventListener('click', event => {
    if (!cat.contains(event.target)) return;
    event.stopPropagation();
    onTap();
  }, true);

  wrap.addEventListener('keydown', event => {
    if (!cat.contains(event.target)) return;
    if (event.key === 'Escape' && state.mode !== 'idle') {
      event.preventDefault();
      if (state.mode === 'listening') cancelListening();
      else { stopSpeaking(); setMode('idle'); }
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      onTap();
    }
  }, true);

  state.tap = onTap;                 // used by the Record button in the note box
  setMode('idle');
  if (window.speechSynthesis) window.speechSynthesis.getVoices(); // warm up the fallback voice list
})();
