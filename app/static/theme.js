// Applies the theme (dark, light, or follow the device) before the page
// paints, so it never flashes the wrong colours. Loaded in <head>.
// The saved choice lives on the server (Settings); this browser keeps a copy
// so the very first paint is already right.
(function () {
  var KEY = 'lifeInbox.theme';
  var CHOICES = ['system', 'dark', 'light'];
  var deviceLight = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  var choice = 'light'; // light is the default for everyone
  try {
    var saved = window.localStorage.getItem(KEY);
    if (CHOICES.indexOf(saved) >= 0) choice = saved;
  } catch (error) {
    // Private mode or blocked storage: keep the default.
  }

  function resolved() {
    if (choice === 'system') return deviceLight && deviceLight.matches ? 'light' : 'dark';
    return choice;
  }

  function paint() {
    var theme = resolved();
    var root = document.documentElement;
    root.setAttribute('data-theme', theme);
    var scheme = document.querySelector('meta[name="color-scheme"]');
    if (scheme) scheme.setAttribute('content', theme);
    var bar = document.querySelector('meta[name="theme-color"]');
    if (bar) bar.setAttribute('content', theme === 'light' ? '#F6F6F8' : '#0A0A0A');
  }

  window.lifeInboxTheme = {
    // choice: 'system' | 'dark' | 'light'
    apply: function (next) {
      choice = CHOICES.indexOf(next) >= 0 ? next : 'light';
      try {
        window.localStorage.setItem(KEY, choice);
      } catch (error) {
        // Still applied for this page.
      }
      paint();
    },
    choice: function () { return choice; },
  };

  if (deviceLight) {
    var follow = function () { if (choice === 'system') paint(); };
    if (deviceLight.addEventListener) deviceLight.addEventListener('change', follow);
    else if (deviceLight.addListener) deviceLight.addListener(follow);
  }
  paint();
})();
