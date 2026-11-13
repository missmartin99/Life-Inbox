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

  function tabIndex(name) {
    return tabs.findIndex(tab => tab.dataset.tab === name);
  }

  // Slide the incoming page in from the direction of travel.
  function animatePanel(panel, direction) {
    if (!panel || !direction || prefersStill()) return;
    panel.classList.remove('page-from-below', 'page-from-above');
    void panel.offsetWidth; // restart the animation
    panel.classList.add(direction > 0 ? 'page-from-below' : 'page-from-above');
    panel.addEventListener('animationend', function done(event) {
      if (event.target !== panel) return;
      panel.classList.remove('page-from-below', 'page-from-above');
      panel.removeEventListener('animationend', done);
    });
  }

  function activate(name, {focus = false, updateHash = true} = {}) {
    if (!panels.has(name)) name = 'capture';
    if (name === activeTab) return;
    const direction = activeTab === null ? 0 : Math.sign(tabIndex(name) - tabIndex(activeTab));
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
    animatePanel(panels.get(name), direction);
    resetPagePull();

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

  /* ---------- WebGL ribbon hero (static/ribbon.js) ---------- */
  const ribbon = window.LifeInboxRibbon
    ? window.LifeInboxRibbon.create(document.getElementById('ribbon'), {
        isActive: () => activeTab === 'capture',
      })
    : {sync() {}};

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

  /* ---------- Top-bar popovers: pending tasks (bell) and account ---------- */
  // Only one is open at a time. app.js loads what goes inside them.
  const accountToggle = document.getElementById('accountToggle');
  const authUser = document.getElementById('authUser');
  const popovers = [
    {toggle: document.getElementById('bellToggle'), panel: document.getElementById('bellPanel')},
    {toggle: accountToggle, panel: document.getElementById('accountPanel')},
  ];

  function setPopoverOpen(popover, open) {
    popover.panel.hidden = !open;
    popover.toggle.setAttribute('aria-expanded', String(open));
    if (open) {
      requestAnimationFrame(() => popover.panel.classList.add('open'));
    } else {
      popover.panel.classList.remove('open');
    }
  }
  for (const popover of popovers) {
    popover.toggle.addEventListener('click', () => {
      const opening = popover.panel.hidden;
      for (const other of popovers) {
        if (other !== popover) setPopoverOpen(other, false);
      }
      setPopoverOpen(popover, opening);
    });
  }
  document.addEventListener('click', event => {
    if (event.target.closest('.nav-actions')) return;
    for (const popover of popovers) {
      if (!popover.panel.hidden) setPopoverOpen(popover, false);
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    for (const popover of popovers) {
      if (!popover.panel.hidden) {
        setPopoverOpen(popover, false);
        popover.toggle.focus();
      }
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

  /* ---------- Scroll to the next / previous page ---------- */
  // Keep scrolling past the bottom of a page to move to the next tab, or past
  // the top to go back. A small hint fills up first, so it never happens by
  // accident; inner scroll areas, open dialogs and menus are respected.
  const PAGE_THRESHOLD = 260;      // px of extra scrolling needed
  const PAGE_COOLDOWN_MS = 900;    // pause after a page change
  const PAGE_IDLE_RESET_MS = 450;  // the hint drains if scrolling stops
  let pagePull = 0;
  let pageDirection = 0;
  let pageLockedUntil = 0;
  let pageIdleTimer = 0;
  let lastWindowScrollAt = 0;

  const pageHint = document.createElement('div');
  pageHint.className = 'page-hint';
  pageHint.setAttribute('aria-hidden', 'true');
  pageHint.innerHTML = '<span class="page-hint-label"></span><span class="page-hint-bar"><span></span></span>';
  document.body.append(pageHint);
  const pageHintLabel = pageHint.querySelector('.page-hint-label');
  const pageHintFill = pageHint.querySelector('.page-hint-bar > span');

  function neighbourTab(direction) {
    const index = tabIndex(activeTab);
    return index < 0 ? null : tabs[index + direction] || null;
  }

  function tabLabel(tab) {
    const text = tab.textContent.replace(/^\s*\d+\s*/, '').trim().toLowerCase();
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function updatePageHint() {
    const target = pageDirection && pagePull > 0 ? neighbourTab(pageDirection) : null;
    if (!target) {
      pageHint.classList.remove('show');
      return;
    }
    pageHint.classList.toggle('up', pageDirection < 0);
    pageHintLabel.textContent = `${pageDirection > 0 ? 'Next' : 'Back to'}: ${tabLabel(target)}`;
    pageHintFill.style.transform = `scaleX(${Math.min(1, pagePull / PAGE_THRESHOLD)})`;
    pageHint.classList.add('show');
  }

  function resetPagePull() {
    pagePull = 0;
    pageDirection = 0;
    updatePageHint();
  }

  function pageBlocked(target) {
    if (introPlaying || Date.now() < pageLockedUntil) return true;
    if (document.querySelector('.tour-modal-backdrop:not([hidden]), dialog[open]')) return true;
    if (document.querySelector('#accountPanel:not([hidden]), #bellPanel:not([hidden])')) return true;
    return Boolean(target && target.closest && target.closest('select, input[type="range"]'));
  }

  // True when an element under the pointer can still scroll this way itself.
  function innerCanScroll(target, direction) {
    for (let el = target; el && el !== document.body && el !== root; el = el.parentElement) {
      if (!(el instanceof Element)) continue;
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      if (!/(auto|scroll)/.test(window.getComputedStyle(el).overflowY)) continue;
      if (direction > 0 && el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
      if (direction < 0 && el.scrollTop > 0) return true;
    }
    return false;
  }

  function atPageEdge(direction) {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    return direction > 0 ? window.scrollY >= max - 2 : window.scrollY <= 1;
  }

  function goToNeighbour(direction) {
    const target = neighbourTab(direction);
    resetPagePull();
    if (!target) return;
    pageLockedUntil = Date.now() + PAGE_COOLDOWN_MS;
    activate(target.dataset.tab);
    if (direction < 0) {
      // Arriving from below: start at the bottom of the previous page.
      window.scrollTo(0, document.documentElement.scrollHeight);
    }
  }

  function pullPage(direction, amount, target) {
    if (!direction || pageBlocked(target) || innerCanScroll(target, direction)
        || !atPageEdge(direction) || !neighbourTab(direction)) {
      if (pagePull) resetPagePull();
      return;
    }
    // Ignore the tail of a scroll that only just reached the edge.
    if (Date.now() - lastWindowScrollAt < 120) return;
    if (direction !== pageDirection) {
      pageDirection = direction;
      pagePull = 0;
    }
    pagePull += amount;
    window.clearTimeout(pageIdleTimer);
    pageIdleTimer = window.setTimeout(resetPagePull, PAGE_IDLE_RESET_MS);
    if (pagePull >= PAGE_THRESHOLD) goToNeighbour(direction);
    else updatePageHint();
  }

  window.addEventListener('scroll', () => { lastWindowScrollAt = Date.now(); }, {passive: true});

  window.addEventListener('wheel', event => {
    if (event.ctrlKey || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
    const amount = Math.min(Math.abs(event.deltaY) * unit, 120);
    pullPage(Math.sign(event.deltaY), amount, event.target);
  }, {passive: true});

  // Touch: swipe up past the bottom (or down past the top).
  let touchY = null;
  window.addEventListener('touchstart', event => {
    touchY = event.touches.length === 1 ? event.touches[0].clientY : null;
  }, {passive: true});
  window.addEventListener('touchmove', event => {
    if (touchY === null || event.touches.length !== 1) return;
    const y = event.touches[0].clientY;
    const delta = touchY - y;
    touchY = y;
    if (Math.abs(delta) < 1) return;
    pullPage(Math.sign(delta), Math.abs(delta) * 1.6, event.target);
  }, {passive: true});
  window.addEventListener('touchend', () => { touchY = null; }, {passive: true});

  // Keyboard: Page Down / Space at the bottom, Page Up / Shift+Space at the top.
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.closest && event.target.closest('input, textarea, select, button, [contenteditable="true"]')) return;
    const forward = event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey);
    const back = event.key === 'PageUp' || (event.key === ' ' && event.shiftKey);
    const direction = forward ? 1 : back ? -1 : 0;
    if (!direction || pageBlocked(event.target) || !atPageEdge(direction)) return;
    if (!neighbourTab(direction)) return;
    event.preventDefault();
    goToNeighbour(direction);
  });

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
