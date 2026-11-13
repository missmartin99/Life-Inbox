// The animated WebGL ribbon behind the "Life Inbox" title, shared by the main
// page (through ui.js) and the sign-in page. Without WebGL, or on a GPU too
// slow to keep up, the hero shows the CSS glow (.hero::before) instead.
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  // isActive(): whether the ribbon's page section is the one on screen.
  function create(canvas, {isActive = () => true} = {}) {
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
      const shouldRun = isActive()
        && state.onScreen
        && !document.hidden
        && !reduceMotion.matches;
      if (shouldRun && !state.running) {
        watchdog.frames = 0;
        watchdog.since = 0;
      }
      state.running = shouldRun;
      if (shouldRun && !state.raf) state.raf = requestAnimationFrame(loop);
      if (!shouldRun && isActive()) draw(8); // still frame for reduced motion
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
  }

  window.LifeInboxRibbon = {create};
})();
