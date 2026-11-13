// Sign-in page: the same wordmark animation and ribbon as the main page.
(() => {
  const title = document.querySelector('.hero-title[data-split]');
  if (title) {
    const text = title.textContent.trim();
    title.setAttribute('aria-label', text);
    title.textContent = '';
    let index = 0;
    for (const glyph of Array.from(text)) {
      if (/\s/.test(glyph)) {
        title.append(' ');
        continue;
      }
      const span = document.createElement('span');
      span.className = 'sp';
      span.setAttribute('aria-hidden', 'true');
      span.style.setProperty('--i', String(index++));
      span.textContent = glyph;
      title.append(span);
    }
  }

  requestAnimationFrame(() => {
    title?.classList.add('in');
    document.querySelectorAll('[data-rev]').forEach(element => element.classList.add('in'));
  });

  const canvas = document.getElementById('ribbon');
  if (canvas && window.LifeInboxRibbon) window.LifeInboxRibbon.create(canvas).sync();

  window.lifeInboxLoginReady = true;
})();
