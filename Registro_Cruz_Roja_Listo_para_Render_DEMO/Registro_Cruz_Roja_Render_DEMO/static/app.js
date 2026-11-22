(() => {
  'use strict';
  const $ = (q) => document.querySelector(q);
  const csrf = document.body.dataset.csrf;
  const codigo = $('#codigo');
  const nombre = $('#nombre');
  const apellido = $('#apellido');
  const form = $('#turno-form');
  const submit = $('#submit-btn');
  const textButton = $('#submit-text');
  const submitIcon = $('#submit-icon');
  const statusBox = $('#status-box');
  const statusText = $('#status-text');
  const statusIcon = $('#status-icon');
  const spinner = $('#lookup-spin');
  const notice = $('#notice-box');
  const mascotMessage = $('#mascot-message');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let currentAction = 'entrada';
  let lookupCode = '';
  let requestNumber = 0;
  let debounceTimer;
  let busy = false;
  let lookupReady = false;
  const say = (message) => {
    if (mascotMessage.textContent !== message && window.gsap && !reduceMotion) {
      gsap.fromTo('#mascot-bubble', { y: 8, opacity: .55 }, { y: 0, opacity: 1, duration: .35 });
    }
    mascotMessage.textContent = message;
  };
  function moveMascot(variant = 'idle') {
    if (!window.gsap || reduceMotion) return;
    const coords = { idle: { x: 0, y: 0, rotation: 0 }, code: { x: -9, y: -5, rotation: -4 },
      name: { x: 12, y: -5, rotation: 3 }, success: { x: -3, y: -25, rotation: -4 } };
    gsap.to('#mascot-scene', { ...coords[variant], duration: .7, ease: 'power2.out' });
    if (variant === 'success') {
      gsap.fromTo('#mascot-scene', { rotation: -9 }, { rotation: 7, yoyo: true, repeat: 3, duration: .15 });
    }
  }
  const setStatus = (type, text) => {
    statusBox.dataset.status = type;
    statusText.textContent = text;
    statusIcon.textContent = ({ entrada: '✓', salida: '◷', error: '!', neutral: '✳' })[type];
  };
  const showError = (message) => {
    notice.hidden = false;
    notice.textContent = message;
    say('Revisemos eso. Te acompaño.');
    if (window.gsap && !reduceMotion) gsap.fromTo('#notice-box', { x: -7 }, { x: 0, duration: .18, ease: 'back.out(3)' });
  };
  const clearError = () => { notice.hidden = true; notice.textContent = ''; };
  const localISO = (date = new Date()) => {
    const pad = (n) => String(n).padStart(2, '0');
    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? '+' : '-';
    const tz = `${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${tz}`;
  };
  function updateClock() {
    const now = new Date();
    $('#clock-header').textContent = new Intl.DateTimeFormat('es-CR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(now);
    $('#datetime-live').textContent = new Intl.DateTimeFormat('es-CR', {
      weekday: 'short', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(now);
  }
  updateClock();
  setInterval(updateClock, 1000);
  function toggleReady() {
    const hasData = codigo.value.trim().length >= 2 && nombre.value.trim().length >= 2 && apellido.value.trim().length >= 2;
    submit.disabled = busy || !hasData || !lookupReady || lookupCode !== codigo.value.trim().toUpperCase();
  }
  async function post(url, data) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify({ ...data, csrf }) });
    const result = await res.json();
    if (!res.ok || !result.ok) throw new Error(result.error || 'No se pudo completar la solicitud.');
    return result;
  }
  async function lookup() {
    const code = codigo.value.trim().toUpperCase();
    const serial = ++requestNumber;
    lookupReady = false;
    lookupCode = '';
    toggleReady();
    clearError();
    if (code.length < 2) {
      setStatus('neutral', 'Escribí tu código para detectar tu estado.');
      return;
    }
    spinner.classList.add('visible');
    setStatus('neutral', 'Verificando si tenés un turno activo…');
    try {
      const data = await post('/api/estado', { codigo: code });
      if (serial !== requestNumber || code !== codigo.value.trim().toUpperCase()) return;
      currentAction = data.accion;
      lookupReady = true;
      lookupCode = code;
      textButton.textContent = currentAction === 'entrada' ? 'Registrar entrada' : 'Registrar salida';
      submitIcon.textContent = currentAction === 'entrada' ? '↗' : '↙';
      if (data.nombre && data.apellido) {
        nombre.value = data.nombre;
        apellido.value = data.apellido;
        nombre.readOnly = true;
        apellido.readOnly = true;
      } else {
        nombre.readOnly = false;
        apellido.readOnly = false;
      }
      if (currentAction === 'entrada') {
        setStatus('entrada', data.nombre ? `¡Bienvenido otra vez, ${data.nombre}! Listo para registrar entrada.` : 'Sin turno abierto. Se registrará una entrada.');
        say('¡Vamos a comenzar un gran turno!');
      } else {
        const date = new Date(data.entrada);
        const shown = Number.isNaN(date.getTime()) ? 'tu último registro' : date.toLocaleString('es-CR', { hour: '2-digit', minute: '2-digit' });
        setStatus('salida', `Tenés una entrada activa desde las ${shown}. Se registrará tu salida.`);
        say('¡Buen trabajo! Ya podemos registrar tu salida.');
      }
    } catch (error) {
      if (serial === requestNumber) {
        setStatus('error', 'No pudimos verificar tu turno.');
        showError(error.message);
      }
    } finally {
      if (serial === requestNumber) spinner.classList.remove('visible');
      toggleReady();
    }
  }
  codigo.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    lookupReady = false;
    nombre.readOnly = false;
    apellido.readOnly = false;
    toggleReady();
    debounceTimer = setTimeout(lookup, 350);
  });
  codigo.addEventListener('blur', () => { codigo.value = codigo.value.trim().toUpperCase(); });
  [nombre, apellido].forEach((el) => el.addEventListener('input', toggleReady));
  codigo.addEventListener('focus', () => { say('Primero, contame cuál es tu código.'); moveMascot('code'); });
  [nombre, apellido].forEach((el) => el.addEventListener('focus', () => { say('Un gusto acompañarte en tu servicio.'); moveMascot('name'); }));
  [codigo, nombre, apellido].forEach((el) => el.addEventListener('blur', () => moveMascot('idle')));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || submit.disabled) return;
    busy = true;
    submit.disabled = true;
    textButton.textContent = 'Guardando registro…';
    clearError();
    const capture = new Date();
    try {
      const result = await post('/api/registrar', {
        codigo: codigo.value.trim().toUpperCase(), nombre: nombre.value.trim(), apellido: apellido.value.trim(),
        hora_celular: localISO(capture), zona: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Desconocida',
      });
      form.hidden = true;
      const success = $('#success-panel');
      success.hidden = false;
      const entrada = result.accion === 'entrada';
      $('#success-heading').textContent = entrada ? '¡Entrada registrada!' : '¡Salida registrada!';
      $('#success-message').textContent = entrada ? `¡Bienvenido, ${result.nombre}! Tu turno quedó abierto correctamente.` :
        `¡Excelente trabajo, ${result.nombre}! Tu turno ha finalizado.`;
      $('#success-time').textContent = new Intl.DateTimeFormat('es-CR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(capture);
      say(entrada ? '¡Listo! Que tengas un turno excelente.' : '¡Hasta pronto! Gracias por tu entrega.');
      moveMascot('success');
      if (window.gsap && !reduceMotion) {
        gsap.fromTo('#success-panel', { opacity: 0, y: 25 }, { opacity: 1, y: 0, duration: .7, ease: 'power3.out' });
        gsap.fromTo('.success-symbol', { scale: .3, rotation: -60 }, { scale: 1, rotation: 0, duration: .75, ease: 'elastic.out(1,.4)' });
      }
    } catch (error) {
      showError(error.message);
      if (error.message.includes('Escaneá')) setStatus('error', 'La sesión expiró o ya se usó.');
      busy = false;
      textButton.textContent = currentAction === 'entrada' ? 'Registrar entrada' : 'Registrar salida';
      toggleReady();
    }
  });
  if (window.gsap && !reduceMotion) {
    gsap.from('.hero-badge,.hero-title-wrap,.mascot-card,.form-panel', { opacity: 0, y: 20, stagger: .13, duration: .7, ease: 'power2.out' });
    gsap.to('.mascot-scene', { y: 9, yoyo: true, repeat: -1, duration: 2.8, ease: 'sine.inOut' });
    gsap.to('.hand-right', { rotation: -43, yoyo: true, repeat: -1, duration: 1.4, ease: 'sine.inOut', transformOrigin: 'top center' });
    gsap.to('.btn-sheen', { x: '270%', repeat: -1, repeatDelay: 3.2, duration: 1.2, ease: 'power1.inOut' });
  }
  const mascot = $('#mascot-card');
  mascot.addEventListener('pointermove', (event) => {
    if (event.pointerType === 'touch' || reduceMotion || !window.gsap) return;
    const box = mascot.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width - .5) * 5;
    const y = ((event.clientY - box.top) / box.height - .5) * 5;
    gsap.to('.mascot-orbit', { x: -x * 2, y: -y * 2, duration: .5 });
  });
})();
