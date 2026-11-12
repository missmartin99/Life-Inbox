// Presentation layer for Life Inbox: tabs, motion, and visual effects.
// Everything that loads or changes data stays in app.js; this file never
// calls the API and only reacts to the DOM that app.js renders.
(() => {
  const root = document.documentElement;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  root.classList.add('js');

  const prefersStill = () => reduceMotion.matches;

  /* ---------- Split headings into animated glyphs / words ---------- */
  function splitText(element) {
    const text = element.textContent.trim();
    const parts = element.dataset.split === 'glyph'
      ? Array.from(text)
      : text.split(/(\s+)/);
    element.setAttribute('aria-label', text);
    element.textContent = '';
    let index = 0;
    for (const part of parts) {
      if (!part) continue;
      if (/^\s+$/.test(part)) {
        element.append(' ');
        continue;
      }
      const span = document.createElement('span');
      span.className = 'sp';
      span.setAttribute('aria-hidden', 'true');
      span.style.setProperty('--i', String(index++));
      span.textContent = part;
      element.append(span);
    }
  }
  document.querySelectorAll('[data-split]').forEach(splitText);

  /* ---------- Tabs ---------- */
  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-tab]'));
  const tabList = document.querySelector('.tabs');
  const indicator = document.querySelector('.tab-indicator');
  const panels = new Map(
    tabs.map(tab => [tab.dataset.tab, document.getElementById(tab.getAttribute('aria-controls'))])
  );
  let activeTab = null;
  let introPlaying = false;

  function tabFromHash() {
    const name = window.location.hash.replace('#', '');
    return panels.has(name) ? name : 'capture';
  }

  function tabButton(name) {
    return tabs.find(tab => tab.dataset.tab === name);
  }

  function moveIndicator() {
    const tab = tabButton(activeTab);
    if (!indicator || !tab) return;
    indicator.style.width = `${tab.offsetWidth}px`;
    indicator.style.transform = `translateX(${tab.offsetLeft}px)`;
  }

  function revealPanel(panel, skip = null) {
    if (!panel) return;
    const items = Array.from(panel.querySelectorAll('[data-rev], [data-split]'))
      .filter(item => item !== skip);
    items.forEach(item => item.classList.remove('in'));
    void panel.offsetWidth; // restart transitions
    let step = 0;
    items.forEach(item => {
      item.style.setProperty('--d', `${Math.min(step++ * 70, 560)}ms`);
      item.classList.add('in');
    });
  }

  function activate(name, {focus = false, updateHash = true} = {}) {
    if (!panels.has(name)) name = 'capture';
    if (name === activeTab) return;
    activeTab = name;

    for (const tab of tabs) {
      const selected = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) {
        tab.classList.remove('has-update');
        if (focus) tab.focus();
        if (tabList && tabList.scrollWidth > tabList.clientWidth) {
          tabList.scrollTo({
            left: tab.offsetLeft - (tabList.clientWidth - tab.offsetWidth) / 2,
            behavior: prefersStill() ? 'auto' : 'smooth'
          });
        }
      }
    }
    for (const [key, panel] of panels) panel.hidden = key !== name;
    root.dataset.tab = name;
    moveIndicator();

    if (updateHash) {
      const url = new URL(window.location.href);
      url.hash = name === 'capture' ? '' : name;
      window.history.replaceState(window.history.state, '', url);
    }
    window.scrollTo(0, 0);
    if (!introPlaying) revealPanel(panels.get(name));
    ribbon.sync();
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => activate(tab.dataset.tab));
  });

  tabList?.addEventListener('keydown', event => {
    const index = tabs.findIndex(tab => tab.dataset.tab === activeTab);
    let next = null;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    activate(next.dataset.tab, {focus: true});
  });

  document.addEventListener('click', event => {
    const link = event.target.closest('[data-goto]');
    if (!link) return;
    event.preventDefault();
    activate(link.dataset.goto);
  });

  // app.js uses pushState for search filters; keep the tab in step with history.
  window.addEventListener('popstate', () => activate(tabFromHash(), {updateHash: false}));
  window.addEventListener('resize', moveIndicator);
  document.fonts?.ready.then(moveIndicator);

  /* ---------- Landing intro: giant title shrinks into place ---------- */
  // The inline script in <head> adds .intro-play before first paint when the
  // page opens on the capture tab without reduced motion.
  const wait = ms => new Promise(resolve => window.setTimeout(resolve, ms));

  async function playIntro() {
    const panel = panels.get('capture');
    const title = panel.querySelector('.hero-title');
    const done = new AbortController();
    let revealed = false;

    const reveal = () => {
      if (revealed) return;
      revealed = true;
      introPlaying = false;
      done.abort();
      root.classList.remove('intro-play', 'intro-ready');
      revealPanel(panel, title);
      ribbon.sync();
    };

    // Measure only once the display font is in, or the scale would be off.
    await Promise.race([document.fonts ? document.fonts.ready : Promise.resolve(), wait(800)]);
    const rect = title.getBoundingClientRect();
    const scale = Math.max(1, Math.min(
      (window.innerWidth * 0.92) / rect.width,
      (window.innerHeight * 0.72) / rect.height
    ));
    const dx = window.innerWidth / 2 - (rect.left + rect.width / 2);
    const dy = window.innerHeight / 2 - (rect.top + rect.height / 2);
    const big = `translate(${dx}px, ${dy}px) scale(${scale})`;

    title.style.setProperty('--d', '0ms');
    title.classList.add('in'); // glyphs rise while the title is still huge
    const shrink = title.animate(
      [
        {transform: big, textShadow: '0 0 70px rgba(99, 102, 241, 0.65)'},
        {transform: 'none', textShadow: '0 0 0 rgba(99, 102, 241, 0)'}
      ],
      {duration: 2000, delay: 600, easing: 'cubic-bezier(0.76, 0, 0.24, 1)', fill: 'both'}
    );
    root.classList.add('intro-ready');

    const skip = () => {
      shrink.finish();
      reveal();
    };
    ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(type => {
      window.addEventListener(type, skip, {once: true, passive: true, signal: done.signal});
    });
    // Bring the rest of the page in while the title is landing.
    window.setTimeout(reveal, 600 + 1450);
    shrink.finished
      .then(() => shrink.cancel())
      .catch(() => {});
  }

  /* ---------- WebGL ribbon hero ---------- */
  const ribbon = (() => {
    const canvas = document.getElementById('ribbon');
    const hero = canvas?.closest('.hero');
    const state = {gl: null, uniforms: {}, raf: 0, running: false, onScreen: true};
    const pointer = {x: 0.5, y: 0.5, tx: 0.5, ty: 0.5};
    // Frame-rate check: slow GPUs fall back to the CSS glow instead of stuttering.
    const watchdog = {frames: 0, since: 0, done: false};
    const start = performance.now();

    const vertexSource = 'attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }';
    const fragmentSource = `
      precision mediump float;
      uniform vec2 uRes;
      uniform float uTime;
      uniform vec2 uPointer;

      float band(vec2 uv, float base, float f1, float f2, float amp, float speed, float width, float phase) {
        float x = uv.x;
        float y = base
          + amp * sin(x * f1 + uTime * speed + phase)
          + amp * 0.55 * sin(x * f2 - uTime * speed * 0.7 + phase * 2.3)
          + (uPointer.y - 0.5) * 0.12 * sin(x * 3.1416 + phase)
          + (uPointer.x - 0.5) * 0.06 * cos(x * 2.0 + phase);
        float d = uv.y - y;
        float w = width * (0.75 + 0.45 * sin(x * 2.3 + uTime * 0.4 + phase));
        return exp(-(d * d) / (w * w));
      }

      void main() {
        vec2 uv = gl_FragCoord.xy / uRes;
        float a = band(uv, 0.52, 3.0, 5.3, 0.11, 0.32, 0.050, 0.0);
        float b = band(uv, 0.46, 2.2, 4.1, 0.14, -0.26, 0.040, 1.7);
        float c = band(uv, 0.58, 3.8, 6.7, 0.09, 0.21, 0.028, 3.1);
        vec3 col = vec3(0.388, 0.400, 0.945) * a * 0.95
                 + vec3(0.545, 0.361, 0.965) * b * 0.85
                 + vec3(0.133, 0.827, 0.933) * c * 0.55
                 + vec3(1.0) * pow(a * b, 2.0) * 0.35;
        col = 1.0 - exp(-col * 1.35);
        float vignette = smoothstep(1.25, 0.25, length((uv - vec2(0.5, 0.52)) * vec2(1.0, 1.6)));
        col *= vignette;
        float grain = fract(sin(dot(gl_FragCoord.xy + uTime, vec2(12.9898, 78.233))) * 43758.5453);
        col += (grain - 0.5) * 0.025;
        gl_FragColor = vec4(col + vec3(0.039), 1.0);
      }
    `;

    function compile(gl, type, source) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    }

    function init() {
      if (!canvas) return false;
      const gl = canvas.getContext('webgl', {antialias: false, alpha: false, powerPreference: 'low-power'});
      if (!gl) return false;
      const vs = compile(gl, gl.VERTEX_SHADER, vertexSource);
      const fs = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
      if (!vs || !fs) return false;
      const program = gl.createProgram();
      gl.attachShader(program, vs);
      gl.attachShader(program, fs);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return false;
      gl.useProgram(program);
      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const location = gl.getAttribLocation(program, 'p');
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
      state.gl = gl;
      state.uniforms = {
        res: gl.getUniformLocation(program, 'uRes'),
        time: gl.getUniformLocation(program, 'uTime'),
        pointer: gl.getUniformLocation(program, 'uPointer')
      };
      return true;
    }

    function resize() {
      // Render below native resolution: cheaper, and the upscale softens the glow.
      const scale = Math.min(window.devicePixelRatio || 1, 1.5) * 0.6;
      const width = Math.max(1, Math.round(canvas.clientWidth * scale));
      const height = Math.max(1, Math.round(canvas.clientHeight * scale));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        state.gl.viewport(0, 0, width, height);
      }
    }

    function draw(seconds) {
      const {gl, uniforms} = state;
      resize();
      pointer.x += (pointer.tx - pointer.x) * 0.04;
      pointer.y += (pointer.ty - pointer.y) * 0.04;
      gl.uniform2f(uniforms.res, canvas.width, canvas.height);
      gl.uniform1f(uniforms.time, seconds);
      gl.uniform2f(uniforms.pointer, pointer.x, pointer.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    function degrade() {
      state.running = false;
      state.gl.getExtension('WEBGL_lose_context')?.loseContext();
      state.gl = null;
      hero.classList.remove('has-webgl');
      canvas.remove();
    }

    function loop(now) {
      state.raf = 0;
      if (!state.running) return;
      draw((now - start) / 1000);
      if (!watchdog.done) {
        if (!watchdog.since) watchdog.since = now;
        watchdog.frames += 1;
        const elapsed = now - watchdog.since;
        if (elapsed > 1500) {
          watchdog.done = true;
          if (elapsed / watchdog.frames > 45) {
            degrade();
            return;
          }
        }
      }
      state.raf = requestAnimationFrame(loop);
    }

    function sync() {
      if (!state.gl) return;
      const shouldRun = activeTab === 'capture'
        && state.onScreen
        && !document.hidden
        && !prefersStill();
      if (shouldRun && !state.running) {
        watchdog.frames = 0;
        watchdog.since = 0;
      }
      state.running = shouldRun;
      if (shouldRun && !state.raf) state.raf = requestAnimationFrame(loop);
      if (!shouldRun && activeTab === 'capture') draw(8); // still frame for reduced motion
    }

    if (init()) {
      hero.classList.add('has-webgl');
      new IntersectionObserver(entries => {
        state.onScreen = entries[0].isIntersecting;
        sync();
      }).observe(hero);
      document.addEventListener('visibilitychange', sync);
      reduceMotion.addEventListener?.('change', sync);
      hero.addEventListener('pointermove', event => {
        const rect = hero.getBoundingClientRect();
        pointer.tx = (event.clientX - rect.left) / rect.width;
        pointer.ty = 1 - (event.clientY - rect.top) / rect.height;
      });
    }
    return {sync};
  })();

  /* ---------- Pointer effects: spotlight, magnetic, ripple ---------- */
  const spotlightSelector = '.panel, .note, .task, .topic, .composer, .search-toolbar, .ask-form, .citation';
  let pointerFrame = 0;
  let lastPointer = null;

  document.addEventListener('pointermove', event => {
    if (!finePointer.matches) return;
    lastPointer = event;
    if (pointerFrame) return;
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = 0;
      const target = lastPointer.target instanceof Element
        ? lastPointer.target.closest(spotlightSelector)
        : null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      target.style.setProperty('--mx', `${lastPointer.clientX - rect.left}px`);
      target.style.setProperty('--my', `${lastPointer.clientY - rect.top}px`);
    });
  }, {passive: true});

  /* ---------- Capture composer: one box, five capture modes ---------- */
  // Each mode shows its original input and its original submit button, so
  // app.js keeps working on the same element ids.
  const composer = document.querySelector('.composer');
  const modeButtons = Array.from(composer.querySelectorAll('.mode'));
  const modePanes = Array.from(composer.querySelectorAll('.mode-pane'));
  const sendButtons = Array.from(composer.querySelectorAll('[data-send]'));
  const dropSyncs = [];

  function activePane() {
    return modePanes.find(pane => pane.dataset.pane === composer.dataset.mode);
  }

  function updateReady() {
    const pane = activePane();
    const field = pane?.querySelector('textarea, input');
    const hasValue = field?.type === 'file'
      ? Boolean(field.files && field.files.length)
      : Boolean(field?.value.trim());
    composer.classList.toggle('ready', hasValue);
  }

  function setMode(mode, {focus = true} = {}) {
    composer.dataset.mode = mode;
    modeButtons.forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
    });
    modePanes.forEach(pane => {
      const active = pane.dataset.pane === mode;
      pane.hidden = !active;
      if (active) {
        pane.classList.remove('enter');
        void pane.offsetWidth;
        pane.classList.add('enter');
      }
    });
    sendButtons.forEach(button => { button.hidden = button.dataset.send !== mode; });
    updateReady();
    if (focus) activePane()?.querySelector('textarea, input[type="url"], button, input')?.focus();
  }

  modeButtons.forEach(button => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
  });
  composer.addEventListener('input', updateReady);
  composer.addEventListener('change', updateReady);

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  composer.querySelectorAll('.drop').forEach(zone => {
    const input = zone.querySelector('input[type="file"]');
    const hint = zone.querySelector('.drop-hint');
    const emptyHint = hint.textContent;
    const sync = () => {
      const file = input.files && input.files[0];
      zone.classList.toggle('has-file', Boolean(file));
      hint.textContent = file ? `${file.name} · ${formatSize(file.size)}` : emptyHint;
      updateReady();
    };
    input.addEventListener('change', sync);
    ['dragenter', 'dragover'].forEach(type => {
      zone.addEventListener(type, () => zone.classList.add('dragging'));
    });
    ['dragleave', 'drop'].forEach(type => {
      zone.addEventListener(type, () => zone.classList.remove('dragging'));
    });
    dropSyncs.push(sync);
  });

  // app.js clears inputs after a successful save without firing change events,
  // so re-read them whenever the capture status line updates.
  new MutationObserver(() => {
    dropSyncs.forEach(sync => sync());
    updateReady();
  }).observe(document.getElementById('noteStatus'), {childList: true, characterData: true, subtree: true});

  document.querySelectorAll('.magnetic').forEach(button => {
    button.addEventListener('pointermove', event => {
      if (prefersStill() || !finePointer.matches || button.disabled) return;
      const rect = button.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      button.style.setProperty('--tx', `${(x * 10).toFixed(1)}px`);
      button.style.setProperty('--ty', `${(y * 8).toFixed(1)}px`);
    });
    button.addEventListener('pointerleave', () => {
      button.style.setProperty('--tx', '0px');
      button.style.setProperty('--ty', '0px');
    });
  });

  document.addEventListener('pointerdown', event => {
    const button = event.target instanceof Element ? event.target.closest('button') : null;
    if (!button || button.disabled || prefersStill()) return;
    const rect = button.getBoundingClientRect();
    button.style.setProperty('--ripple-x', `${event.clientX - rect.left}px`);
    button.style.setProperty('--ripple-y', `${event.clientY - rect.top}px`);
    button.classList.remove('rippling');
    void button.offsetWidth;
    button.classList.add('rippling');
  });
  document.addEventListener('animationend', event => {
    if (event.animationName === 'ripple') event.target.classList.remove('rippling');
  });

  /* ---------- Account popover ---------- */
  const accountToggle = document.getElementById('accountToggle');
  const accountPanel = document.getElementById('accountPanel');
  const authUser = document.getElementById('authUser');

  function setAccountOpen(open) {
    accountPanel.hidden = !open;
    accountToggle.setAttribute('aria-expanded', String(open));
    if (open) {
      requestAnimationFrame(() => accountPanel.classList.add('open'));
    } else {
      accountPanel.classList.remove('open');
    }
  }
  accountToggle.addEventListener('click', () => setAccountOpen(accountPanel.hidden));
  document.addEventListener('click', event => {
    if (accountPanel.hidden) return;
    if (event.target.closest('.nav-actions')) return;
    setAccountOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !accountPanel.hidden) {
      setAccountOpen(false);
      accountToggle.focus();
    }
  });
  const syncAccount = () => {
    const text = authUser.textContent.trim();
    accountToggle.dataset.state = text.startsWith('Signed in') ? 'in' : 'out';
    accountToggle.title = text;
  };
  new MutationObserver(syncAccount).observe(authUser, {childList: true, characterData: true, subtree: true});
  syncAccount();

  /* ---------- Status flashes, tab badges, and toasts ---------- */
  const toastRoot = document.getElementById('toasts');
  const tabLabels = Object.fromEntries(
    tabs.map(tab => [tab.dataset.tab, tab.textContent.replace(/^\d+/, '').trim()])
  );
  let armed = false; // ignore the burst of renders from the initial page load
  window.setTimeout(() => { armed = true; }, 3000);

  function panelNameFor(element) {
    return element.closest('.tab-panel')?.dataset.panel || null;
  }

  const openToasts = new Map(); // one toast per panel; later updates replace its text

  function showToast(panelName, message, isError) {
    const existing = openToasts.get(panelName);
    if (existing) {
      existing.update(message, isError);
      return;
    }
    const toast = document.createElement('div');
    const label = document.createElement('span');
    label.className = 'mono';
    label.textContent = tabLabels[panelName] || 'Life Inbox';
    const text = document.createElement('span');
    text.className = 'toast-text';
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'secondary chip';
    view.textContent = 'View';
    view.addEventListener('click', () => {
      activate(panelName);
      dismiss();
    });
    toast.append(label, text, view);
    toastRoot.append(toast);
    requestAnimationFrame(() => toast.classList.add('in'));

    let timer = 0;
    const restartTimer = delay => {
      window.clearTimeout(timer);
      timer = window.setTimeout(dismiss, delay);
    };
    function update(nextMessage, nextIsError) {
      toast.className = `toast in${nextIsError ? ' error' : ''}`;
      text.textContent = nextMessage;
      restartTimer(5200);
    }
    function dismiss() {
      window.clearTimeout(timer);
      openToasts.delete(panelName);
      toast.classList.remove('in');
      toast.classList.add('out');
      window.setTimeout(() => toast.remove(), 450);
    }
    toast.className = `toast${isError ? ' error' : ''}`;
    text.textContent = message;
    restartTimer(5200);
    toast.addEventListener('pointerenter', () => window.clearTimeout(timer));
    toast.addEventListener('pointerleave', () => restartTimer(2000));
    openToasts.set(panelName, {update});
  }

  document.querySelectorAll('.status').forEach(status => {
    new MutationObserver(() => {
      status.classList.remove('flash');
      void status.offsetWidth;
      status.classList.add('flash');
      const panelName = panelNameFor(status);
      const message = status.textContent.trim();
      if (!armed || !panelName || panelName === activeTab || !message) return;
      const isError = status.classList.contains('error');
      if (isError) showToast(panelName, message, isError);
    }).observe(status, {childList: true, characterData: true, subtree: true});
  });

  ['dashboard', 'topics', 'notes'].forEach(id => {
    const element = document.getElementById(id);
    if (!element) return;
    new MutationObserver(() => {
      const panelName = panelNameFor(element);
      if (!armed || !panelName || panelName === activeTab) return;
      tabButton(panelName)?.classList.add('has-update');
    }).observe(element, {childList: true});
  });

  /* ---------- Scroll progress + nav state ---------- */
  let scrollFrame = 0;
  function onScroll() {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      root.style.setProperty('--progress', max > 0 ? String(window.scrollY / max) : '0');
      root.classList.toggle('scrolled', window.scrollY > 8);
    });
  }
  window.addEventListener('scroll', onScroll, {passive: true});

  /* ---------- Boot ---------- */
  const playingIntro = root.classList.contains('intro-play')
    && tabFromHash() === 'capture'
    && !prefersStill();
  if (!playingIntro) root.classList.remove('intro-play');
  introPlaying = playingIntro;
  activate(tabFromHash(), {updateHash: false});
  if (playingIntro) {
    playIntro().catch(() => {
      introPlaying = false;
      root.classList.remove('intro-play', 'intro-ready');
      revealPanel(panels.get('capture'));
    });
  }
  onScroll();
  window.lifeInboxUiReady = true;
})();
