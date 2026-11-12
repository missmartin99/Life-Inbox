// Playful touches on the landing page: the "Life Inbox" letters play Für Elise
// on hover, and Jin, a grey Persian (unless renamed), naps on top of the composer.
// Presentation only: nothing here calls the API. Sounds are synthesised with Web Audio.
(() => {
  const root = document.documentElement;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const wait = ms => new Promise(resolve => window.setTimeout(resolve, ms));
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  // Per-visitor preferences only (sound on/off, the cat's name and mood); failures are harmless.
  const store = {
    get(key, fallback) {
      try {
        const raw = window.localStorage.getItem(key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch {
        // Storage unavailable (private mode, blocked site data): keep it in memory.
      }
    }
  };

  /* ---------- Audio engine ---------- */
  let soundOn = store.get('lifeInbox.sound', true) !== false;

  const audio = (() => {
    let ctx = null;
    let dry = null;
    let reverb = null;
    let noise = null;

    // Browsers only allow audio after the visitor has clicked or pressed a key.
    const hasActivation = () => (navigator.userActivation
      ? navigator.userActivation.hasBeenActive
      : true);

    function impulse(seconds, decay) {
      const length = Math.floor(ctx.sampleRate * seconds);
      const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
      for (let channel = 0; channel < 2; channel++) {
        const data = buffer.getChannelData(channel);
        for (let i = 0; i < length; i++) {
          data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay);
        }
      }
      return buffer;
    }

    function build() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return false;
      ctx = new AudioContextClass();
      const glue = ctx.createDynamicsCompressor();
      glue.threshold.value = -20;
      glue.ratio.value = 3;
      glue.connect(ctx.destination);
      dry = ctx.createGain();
      dry.gain.value = 0.8;
      dry.connect(glue);
      // A soft synthetic room so every note rings out gently.
      reverb = ctx.createConvolver();
      reverb.buffer = impulse(2.8, 2.6);
      const wet = ctx.createGain();
      wet.gain.value = 0.34;
      reverb.connect(wet).connect(glue);
      return true;
    }

    async function ready() {
      if (!soundOn || !hasActivation()) return null;
      if (!ctx && !build()) return null;
      if (ctx.state !== 'running') {
        try {
          await Promise.race([ctx.resume(), wait(300)]);
        } catch {
          return null;
        }
      }
      return ctx.state === 'running' ? ctx : null;
    }

    function send(node, {wet = true} = {}) {
      node.connect(dry);
      if (wet) node.connect(reverb);
    }

    function brownNoise() {
      if (noise) return noise;
      const length = ctx.sampleRate * 2;
      noise = ctx.createBuffer(1, length, ctx.sampleRate);
      const data = noise.getChannelData(0);
      let last = 0;
      for (let i = 0; i < length; i++) {
        last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
        data[i] = last * 3.5;
      }
      return noise;
    }

    return {ready, send, brownNoise, hasActivation, get ctx() { return ctx; }};
  })();

  // A warm, bell-like tone: sine body, soft overtones, slightly detuned triangle.
  function bell(freq, {when = 0, gain = 0.2, decay = 2.4, pan = 0} = {}) {
    const ctx = audio.ctx;
    const start = ctx.currentTime + when;
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, start);
    env.gain.exponentialRampToValueAtTime(gain, start + 0.015);
    env.gain.exponentialRampToValueAtTime(0.0001, start + decay);
    const tone = ctx.createBiquadFilter();
    tone.type = 'lowpass';
    tone.frequency.value = 2800;
    tone.Q.value = 0.3;
    tone.connect(env);
    if (ctx.createStereoPanner) {
      const panner = ctx.createStereoPanner();
      panner.pan.value = pan;
      env.connect(panner);
      audio.send(panner);
    } else {
      audio.send(env);
    }
    const partials = [
      // [frequency ratio, wave, level, share of the decay]
      [1, 'sine', 1, 1],
      [2, 'sine', 0.22, 0.35],
      [3, 'sine', 0.06, 0.2],
      [1, 'triangle', 0.14, 1]
    ];
    for (const [ratio, type, level, life] of partials) {
      const osc = ctx.createOscillator();
      osc.type = type;
      osc.frequency.value = freq * ratio;
      if (type === 'triangle') osc.detune.value = 7;
      const partialGain = ctx.createGain();
      partialGain.gain.setValueAtTime(level, start);
      partialGain.gain.exponentialRampToValueAtTime(0.0001, start + decay * life);
      osc.connect(partialGain).connect(tone);
      osc.start(start);
      osc.stop(start + decay + 0.1);
    }
  }

  function purr(duration = 1.8) {
    const ctx = audio.ctx;
    const start = ctx.currentTime;
    const source = ctx.createBufferSource();
    source.buffer = audio.brownNoise();
    source.loop = true;
    const lowpass = ctx.createBiquadFilter();
    lowpass.type = 'lowpass';
    lowpass.frequency.value = 260;
    // The rumble pulses about 24 times a second, which is what makes it a purr.
    const pulse = ctx.createGain();
    pulse.gain.value = 0.5;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 24;
    const depth = ctx.createGain();
    depth.gain.value = 0.45;
    lfo.connect(depth).connect(pulse.gain);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, start);
    env.gain.exponentialRampToValueAtTime(0.35, start + 0.25);
    env.gain.setValueAtTime(0.35, start + duration - 0.45);
    env.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(lowpass).connect(pulse).connect(env);
    audio.send(env, {wet: false});
    source.start(start);
    lfo.start(start);
    source.stop(start + duration + 0.05);
    lfo.stop(start + duration + 0.05);
  }

  function withSound(play) {
    audio.ready().then(ctx => { if (ctx) play(ctx); });
  }

  /* ---------- Sound toggle ---------- */
  const soundToggle = document.getElementById('soundToggle');
  function renderSoundToggle() {
    soundToggle.setAttribute('aria-pressed', String(soundOn));
    soundToggle.title = soundOn ? 'Sound on' : 'Sound off';
  }
  soundToggle.addEventListener('click', () => {
    soundOn = !soundOn;
    store.set('lifeInbox.sound', soundOn);
    renderSoundToggle();
    if (soundOn) withSound(() => bell(783.99, {gain: 0.1, decay: 1.2}));
  });
  renderSoundToggle();

  // Create the audio context inside the first real gesture (needed by Safari).
  const unlock = () => { if (soundOn) audio.ready(); };
  window.addEventListener('pointerdown', unlock, {once: true, capture: true});
  window.addEventListener('keydown', unlock, {once: true, capture: true});

  /* ---------- Singing title: Für Elise ---------- */
  const title = document.querySelector('.hero-title');
  const heroInner = title?.closest('.hero-inner');
  const letters = title ? Array.from(title.querySelectorAll('.sp')) : [];
  const NOTE_COLORS = ['#A5B4FC', '#C4B5FD', '#67E8F9'];
  const NOTE_GLYPHS = ['♪', '♫', '♩', '♬'];
  const lastPlayed = new Map();
  let hintShown = false;

  // Beethoven, Für Elise (1810, public domain): the main theme's right hand.
  // Every letter hovered plays the next note, so the tune comes out in order
  // whichever way the pointer moves; the opening phrase is exactly nine notes,
  // one per letter of "Life Inbox". [note, left-hand chord that follows it]
  const FUR_ELISE = [
    ['E5'], ['D#5'], ['E5'], ['D#5'], ['E5'], ['B4'], ['D5'], ['C5'], ['A4', 'Am'],
    ['C4'], ['E4'], ['A4'], ['B4', 'E'],
    ['E4'], ['G#4'], ['B4'], ['C5', 'Am'],
    ['E4'], ['E5'], ['D#5'], ['E5'], ['D#5'], ['E5'], ['B4'], ['D5'], ['C5'], ['A4', 'Am'],
    ['C4'], ['E4'], ['A4'], ['B4', 'E'],
    ['E4'], ['C5'], ['B4'], ['A4', 'Am']
  ];
  // The broken-chord accompaniment Beethoven gives the left hand on each held note.
  const LEFT_HAND = {Am: ['A2', 'E3', 'A3'], E: ['E2', 'E3', 'G#3']};
  const RESTART_AFTER_MS = 3000;
  let melodyStep = 0;
  let lastNoteAt = 0;

  function frequency(name) {
    const [, letter, accidental, octave] = name.match(/^([A-G])(#?)(\d)$/);
    const semitone = {C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11}[letter] + (accidental ? 1 : 0);
    const midi = (Number(octave) + 1) * 12 + semitone;
    return 440 * 2 ** ((midi - 69) / 12);
  }

  function playMelodyNote(note, chord, pan) {
    const freq = frequency(note);
    // Short, piano-like decay keeps the E / D-sharp trill clean.
    bell(freq, {gain: 0.2 * Math.pow(440 / freq, 0.3), decay: 1.2, pan});
    if (!chord) return;
    LEFT_HAND[chord].forEach((bass, i) => {
      bell(frequency(bass), {when: 0.16 + i * 0.16, gain: 0.09, decay: 1.6, pan: -0.25});
    });
  }

  // A music note drifts up from the hovered letter (the letter itself stays still).
  function floatNote(letter, index) {
    if (reduceMotion.matches || !heroInner) return;
    const box = letter.getBoundingClientRect();
    const frame = heroInner.getBoundingClientRect();
    const note = document.createElement('span');
    note.className = 'note-float';
    note.setAttribute('aria-hidden', 'true');
    note.textContent = NOTE_GLYPHS[index % NOTE_GLYPHS.length];
    note.style.left = `${box.left - frame.left + box.width / 2}px`;
    note.style.top = `${box.top - frame.top}px`;
    note.style.setProperty('--drift', `${Math.round(Math.random() * 28 - 14)}px`);
    note.style.color = NOTE_COLORS[index % NOTE_COLORS.length];
    heroInner.append(note);
    note.addEventListener('animationend', () => note.remove(), {once: true});
    window.setTimeout(() => note.remove(), 2000);
  }

  function noteBurst() {
    letters.forEach((letter, i) => window.setTimeout(() => floatNote(letter, i), i * 70));
  }

  function showSoundHint() {
    if (hintShown || !heroInner) return;
    hintShown = true;
    const hint = document.createElement('span');
    hint.className = 'title-hint mono';
    hint.textContent = 'Click anywhere, then glide over the letters to play Für Elise ♪';
    hint.style.top = `${title.offsetTop - 34}px`;
    heroInner.append(hint);
    requestAnimationFrame(() => hint.classList.add('show'));
    window.setTimeout(() => {
      hint.classList.remove('show');
      window.setTimeout(() => hint.remove(), 600);
    }, 3200);
  }

  function playLetter(index) {
    if (root.classList.contains('intro-play')) return;
    const now = performance.now();
    // Ignore jitter on a letter's edge, and very fast flurries across letters.
    if (now - (lastPlayed.get(index) || 0) < 140 || now - lastNoteAt < 70) return;
    lastPlayed.set(index, now);

    // Pause for a moment and the tune starts again from its famous opening.
    if (now - lastNoteAt > RESTART_AFTER_MS) melodyStep = 0;
    lastNoteAt = now;
    const [note, chord] = FUR_ELISE[melodyStep];
    const themeEnds = melodyStep === FUR_ELISE.length - 1;
    melodyStep = (melodyStep + 1) % FUR_ELISE.length;

    floatNote(letters[index], index);
    // Finishing the whole theme sends a little burst of notes across the title.
    if (themeEnds) window.setTimeout(noteBurst, 300);

    if (!soundOn) return;
    if (!audio.hasActivation()) {
      showSoundHint();
      return;
    }
    const pan = letters.length > 1 ? (index / (letters.length - 1)) * 1.2 - 0.6 : 0;
    withSound(() => playMelodyNote(note, chord, pan));
  }

  letters.forEach((letter, index) => {
    letter.addEventListener('pointerenter', () => playLetter(index));
  });
  // Let a finger glide across the letters instead of being captured by the first one.
  title?.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'mouse' && event.target.hasPointerCapture?.(event.pointerId)) {
      event.target.releasePointerCapture(event.pointerId);
    }
  });

  /* ---------- Jin, the Persian who naps on the composer ---------- */
  const catWrap = document.querySelector('.cat-wrap');
  if (!catWrap) return;
  const cat = catWrap.querySelector('.cat');
  const catSvg = cat.querySelector('.cat-svg');
  const bubble = catWrap.querySelector('.cat-bubble');
  const menu = document.getElementById('catMenu');
  const menuMain = menu.querySelector('.cat-menu-main');
  const menuItems = Array.from(menu.querySelectorAll('[role="menuitem"]'));
  const renameForm = menu.querySelector('.cat-rename');
  const nameInput = document.getElementById('catNameInput');
  const nameLabel = menu.querySelector('.cat-menu-name');
  const moodLabel = document.getElementById('catMood');
  const happyValue = document.getElementById('catHappyValue');
  const happyMeter = document.getElementById('catHappyMeter');
  const composer = document.querySelector('.composer');

  const MOODS = [
    [25, 'grumpy', 'Grumpy'],
    [50, 'bored', 'Bored'],
    [75, 'content', 'Content'],
    [90, 'happy', 'Happy'],
    [Infinity, 'blissful', 'Blissful']
  ];
  const FULL_FOR_MS = 45000;
  const DEFAULT_NAME = 'Jin';
  const PREVIOUS_DEFAULT = 'Mochi'; // earlier default; treated as "never renamed"
  const NAME_MAX = 20;

  function cleanName(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().slice(0, NAME_MAX);
  }

  function loadCat() {
    const now = Date.now();
    const saved = store.get('lifeInbox.cat', null) || {};
    const savedName = cleanName(saved.name);
    const name = savedName && savedName !== PREVIOUS_DEFAULT ? savedName : DEFAULT_NAME;
    if (typeof saved.happy !== 'number') return {happy: 60, savedAt: now, fedAt: 0, name};
    // Happiness fades by one point per minute away, but never below 10.
    const minutesAway = Math.max(0, Math.floor((now - (saved.savedAt || now)) / 60000));
    return {happy: clamp(saved.happy - minutesAway, 10, 100), savedAt: now, fedAt: saved.fedAt || 0, name};
  }

  const catState = loadCat();

  function saveCat() {
    catState.savedAt = Date.now();
    store.set('lifeInbox.cat', catState);
  }

  function renderName() {
    const name = catState.name;
    nameLabel.textContent = name;
    menu.querySelectorAll('[data-label]').forEach(label => {
      label.textContent = label.dataset.label.replace('{name}', name);
    });
    cat.setAttribute('aria-label', `${name} the cat. Right-click, or press Shift+F10, to pet, feed, play with or rename her.`);
    menu.setAttribute('aria-label', name);
  }

  function renderMood() {
    const [, key, label] = MOODS.find(([limit]) => catState.happy < limit);
    const percent = Math.round(catState.happy);
    catWrap.dataset.mood = key;
    menu.dataset.mood = key;
    menu.style.setProperty('--happy', String(percent));
    moodLabel.textContent = label;
    happyValue.textContent = `${percent}%`;
    happyMeter.setAttribute('aria-valuenow', String(percent));
    happyMeter.setAttribute('aria-valuetext', `${percent}%, ${label}`);
  }

  function addHappy(amount) {
    catState.happy = clamp(catState.happy + amount, 10, 100);
    saveCat();
    renderMood();
  }

  renderName();
  renderMood();
  window.setInterval(() => {
    if (catState.happy <= 10) return;
    catState.happy -= 1;
    saveCat();
    renderMood();
  }, 60000);

  let bubbleTimer = 0;
  function say(text, ms = 1800) {
    bubble.textContent = text;
    bubble.classList.remove('show');
    void bubble.offsetWidth;
    bubble.classList.add('show');
    window.clearTimeout(bubbleTimer);
    bubbleTimer = window.setTimeout(() => bubble.classList.remove('show'), ms);
  }

  const flashTimers = new Map();
  function flash(className, ms) {
    catWrap.classList.add(className);
    window.clearTimeout(flashTimers.get(className));
    flashTimers.set(className, window.setTimeout(() => catWrap.classList.remove(className), ms));
  }

  function hearts(count = 4) {
    if (reduceMotion.matches) return;
    for (let i = 0; i < count; i++) {
      window.setTimeout(() => {
        const heart = document.createElement('span');
        heart.className = 'cat-heart';
        heart.setAttribute('aria-hidden', 'true');
        heart.textContent = '♥';
        heart.style.setProperty('--dx', `${Math.round(Math.random() * 40 - 20)}px`);
        heart.style.color = i % 2 ? '#A5B4FC' : '#F9A8D4';
        catWrap.append(heart);
        heart.addEventListener('animationend', () => heart.remove(), {once: true});
        window.setTimeout(() => heart.remove(), 2000);
      }, i * 180);
    }
  }

  // Map a point in the drawing's own coordinates to the page.
  function svgPoint(x, y) {
    const box = catSvg.getBoundingClientRect();
    const view = catSvg.viewBox.baseVal;
    return {x: box.left + (x / view.width) * box.width, y: box.top + (y / view.height) * box.height};
  }

  /* Sleeping and waking: she naps by default and wakes for a while when something happens. */
  let hovered = false;
  let sleepTimer = 0;
  function wake(forMs = 2500) {
    catWrap.classList.add('awake');
    window.clearTimeout(sleepTimer);
    sleepTimer = window.setTimeout(() => {
      if (!hovered) catWrap.classList.remove('awake');
    }, forMs);
  }
  const isAwake = () => catWrap.classList.contains('awake');

  // While awake, her eyes follow the pointer and her head tilts toward it.
  let lookFrame = 0;
  let lastLook = null;
  let curiousUntil = 0;
  document.addEventListener('pointermove', event => {
    lastLook = event;
    if (lookFrame) return;
    lookFrame = requestAnimationFrame(() => {
      lookFrame = 0;
      if (!catWrap.offsetParent || performance.now() < curiousUntil) return;
      const head = svgPoint(28, 40);
      const dx = lastLook.clientX - head.x;
      const dy = lastLook.clientY - head.y;
      catWrap.style.setProperty('--px', `${(clamp(dx / 140, -1, 1) * 1.3).toFixed(2)}px`);
      catWrap.style.setProperty('--py', `${(clamp(dy / 140, -1, 1) * 1.1).toFixed(2)}px`);
      catWrap.style.setProperty('--tilt', `${(clamp(dx / 220, -1, 1) * 6).toFixed(1)}deg`);
    });
  }, {passive: true});

  // Typing makes her open one eye to see what you're up to.
  composer?.addEventListener('input', () => {
    if (isAwake()) return;
    curiousUntil = performance.now() + 1200;
    catWrap.style.setProperty('--px', '-0.8px');
    catWrap.style.setProperty('--py', '1px');
    flash('peek', 1200);
  });

  // Now and then she mumbles in her sleep.
  const SLEEP_TALK = ['zzz…', 'mrrm…', '…fish…', 'prrr…', '…five more minutes…'];
  window.setInterval(() => {
    if (!catWrap.offsetParent || isAwake() || !menu.hidden || Math.random() > 0.5) return;
    say(SLEEP_TALK[Math.floor(Math.random() * SLEEP_TALK.length)], 2200);
  }, 22000);

  let lastChirp = 0;
  cat.addEventListener('pointerenter', () => {
    hovered = true;
    wake();
    const now = performance.now();
    if (now - lastChirp > 8000 && menu.hidden) {
      lastChirp = now;
      say(catState.happy < 25 ? 'hmph.' : 'mrrp?', 1400);
    }
  });
  cat.addEventListener('pointerleave', () => {
    hovered = false;
    catWrap.style.setProperty('--tilt', '0deg');
    wake(2500); // stays up a moment, then curls back to sleep
  });

  /* Little sounds */
  function yawnSound() {
    const ctx = audio.ctx;
    const start = ctx.currentTime;
    const osc = ctx.createOscillator();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(560, start);
    osc.frequency.exponentialRampToValueAtTime(300, start + 0.9);
    const vibrato = ctx.createOscillator();
    vibrato.frequency.value = 6;
    const vibratoDepth = ctx.createGain();
    vibratoDepth.gain.value = 12;
    vibrato.connect(vibratoDepth).connect(osc.frequency);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, start);
    env.gain.exponentialRampToValueAtTime(0.06, start + 0.15);
    env.gain.exponentialRampToValueAtTime(0.0001, start + 1);
    osc.connect(env);
    audio.send(env);
    osc.start(start);
    vibrato.start(start);
    osc.stop(start + 1.05);
    vibrato.stop(start + 1.05);
  }

  function chime() {
    bell(1318.5, {gain: 0.05, decay: 0.6, pan: 0.2});
    bell(1567.98, {when: 0.09, gain: 0.04, decay: 0.7, pan: 0.2});
  }

  /* Things Jin can do */
  let lastBoop = 0;
  function yawn() {
    wake(3000);
    flash('yawn', 1600);
    say(catState.happy < 25 ? 'hmph… *yawn*' : '*yaaawn*', 1500);
    if (soundOn) withSound(yawnSound);
    const now = performance.now();
    if (now - lastBoop > 4000) {
      lastBoop = now;
      addHappy(1);
    }
  }

  function pet() {
    // She doesn't even wake up: just smiles, purrs and gets cosy.
    flash('happy', 2000);
    flash('purring', 2000);
    hearts(catState.happy >= 90 ? 6 : 4);
    say(catState.happy >= 95 ? 'purrrr… ♥' : 'purrrr… zz', 1900);
    if (soundOn) withSound(() => purr(1.9));
    addHappy(8);
  }

  function feed(from) {
    const now = Date.now();
    wake(3200);
    if (now - catState.fedAt < FULL_FOR_MS) {
      say("I'm full!", 1500);
      addHappy(2);
      return;
    }
    catState.fedAt = now;
    const mouth = svgPoint(28.5, 48);
    const fish = document.createElement('span');
    fish.className = 'cat-fish';
    fish.setAttribute('aria-hidden', 'true');
    fish.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3 12c3.5-5 9.5-6 14-2l4-3v10l-4-3c-4.5 4-10.5 3-14-2z"/><circle cx="8" cy="11" r="1"/></svg>';
    fish.style.left = `${from.x}px`;
    fish.style.top = `${from.y}px`;
    document.body.append(fish);
    const flight = fish.animate(
      [
        {transform: 'translate(-50%, -50%) scale(1) rotate(0deg)'},
        {
          transform: `translate(calc(-50% + ${(mouth.x - from.x) / 2}px), calc(-50% + ${(mouth.y - from.y) / 2 - 60}px)) scale(1.1) rotate(-20deg)`,
          offset: 0.5
        },
        {transform: `translate(calc(-50% + ${mouth.x - from.x}px), calc(-50% + ${mouth.y - from.y}px)) scale(0.3) rotate(-40deg)`, opacity: 0.2}
      ],
      {duration: reduceMotion.matches ? 1 : 700, easing: 'cubic-bezier(0.45, 0, 0.2, 1)', fill: 'forwards'}
    );
    flight.finished.catch(() => {}).finally(() => {
      fish.remove();
      wake(3000);
      flash('eating', 1300);
      say('nom nom', 1400);
      if (soundOn) {
        withSound(() => {
          bell(1046.5, {gain: 0.07, decay: 0.5});
          bell(1318.5, {when: 0.14, gain: 0.06, decay: 0.6});
        });
      }
      window.setTimeout(() => {
        flash('happy', 1100);
        hearts(3);
      }, 1300);
      addHappy(16);
    });
  }

  function play() {
    // A yarn ball rolls in; she wakes up and bats it three times before it rolls away.
    wake(3600);
    flash('playing', 3200);
    say('!', 900);
    if (soundOn) {
      withSound(() => {
        [[0.85, 1174.66], [1.55, 1318.5], [2.25, 1567.98]].forEach(([when, freq]) => {
          bell(freq, {when, gain: 0.05, decay: 0.35, pan: -0.3});
        });
      });
    }
    window.setTimeout(() => {
      flash('happy', 1100);
      hearts(3);
    }, 3200);
    addHappy(10);
  }

  /* Right-click menu: happiness meter, pet, feed, play, rename */
  function showMainView() {
    renameForm.hidden = true;
    menuMain.hidden = false;
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', catState.name);
  }

  function placeMenu(x, y) {
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    menu.style.left = `${clamp(x, 8, window.innerWidth - width - 8)}px`;
    menu.style.top = `${clamp(y, 8, window.innerHeight - height - 8)}px`;
  }

  function openMenu(x, y) {
    renderMood();
    showMainView();
    menu.hidden = false;
    menu.classList.remove('open');
    placeMenu(x, y);
    cat.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => menu.classList.add('open'));
    menuItems[0].focus({preventScroll: true});
  }

  function closeMenu({restoreFocus = false} = {}) {
    if (menu.hidden) return;
    menu.hidden = true;
    menu.classList.remove('open');
    showMainView();
    cat.setAttribute('aria-expanded', 'false');
    if (restoreFocus) cat.focus({preventScroll: true});
  }

  function openMenuAtCat() {
    const box = cat.getBoundingClientRect();
    openMenu(box.left + box.width * 0.2, box.bottom - 4);
  }

  // Rename swaps the menu for a small form in the same pop-up.
  function startRename() {
    menuMain.hidden = true;
    renameForm.hidden = false;
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', `Rename ${catState.name}`);
    nameInput.value = catState.name;
    placeMenu(parseFloat(menu.style.left), parseFloat(menu.style.top));
    nameInput.focus({preventScroll: true});
    nameInput.select();
  }

  renameForm.addEventListener('submit', event => {
    event.preventDefault();
    const name = cleanName(nameInput.value);
    closeMenu({restoreFocus: true});
    if (!name || name === catState.name) return;
    catState.name = name;
    saveCat();
    renderName();
    wake(2500);
    flash('happy', 1400);
    hearts(3);
    say(`I'm ${name} now!`, 2000);
    if (soundOn) withSound(chime);
  });

  cat.addEventListener('contextmenu', event => {
    event.preventDefault();
    if (event.clientX === 0 && event.clientY === 0) openMenuAtCat();
    else openMenu(event.clientX, event.clientY);
  });
  cat.addEventListener('click', yawn);
  cat.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      yawn();
    } else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      event.preventDefault();
      openMenuAtCat();
    }
  });

  menu.addEventListener('click', event => {
    const item = event.target.closest('[data-cat-action]');
    if (!item) return;
    const action = item.dataset.catAction;
    if (action === 'rename') {
      startRename();
      return;
    }
    const box = item.getBoundingClientRect();
    const from = {x: box.left + 24, y: box.top + box.height / 2};
    closeMenu();
    if (action === 'pet') pet();
    if (action === 'feed') feed(from);
    if (action === 'play') play();
  });

  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu({restoreFocus: true});
      return;
    }
    // Inside the rename form, keys behave normally (typing, Tab to Save).
    if (renameForm.contains(event.target)) return;
    const index = menuItems.indexOf(document.activeElement);
    let next = null;
    if (event.key === 'ArrowDown') next = menuItems[(index + 1) % menuItems.length];
    if (event.key === 'ArrowUp') next = menuItems[(index - 1 + menuItems.length) % menuItems.length];
    if (event.key === 'Home') next = menuItems[0];
    if (event.key === 'End') next = menuItems[menuItems.length - 1];
    if (next) {
      event.preventDefault();
      next.focus();
    } else if (event.key === 'Tab') {
      closeMenu();
    }
  });

  document.addEventListener('pointerdown', event => {
    if (!menu.hidden && !menu.contains(event.target)) closeMenu();
  });
  window.addEventListener('resize', () => closeMenu());
  window.addEventListener('scroll', () => closeMenu(), {passive: true});
  window.addEventListener('blur', () => closeMenu());
})();
