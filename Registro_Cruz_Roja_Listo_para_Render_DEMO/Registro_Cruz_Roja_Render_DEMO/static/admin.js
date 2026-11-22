(() => {
  'use strict';
  const input = document.getElementById('public-url');
  const img = document.getElementById('qr-image');
  const download = document.getElementById('download-qr');
  const update = () => {
    const base = input.value.trim().replace(/\/$/, '');
    try {
      const url = new URL(base);
      if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash) throw Error();
    } catch {
      alert('Ingresá una URL completa como http://192.168.1.20:8765');
      return;
    }
    const target = '/admin/qr.png?base=' + encodeURIComponent(base);
    img.src = target;
    download.href = target;
  };
  document.getElementById('qr-update').addEventListener('click', update);
  document.getElementById('copy-url').addEventListener('click', async () => {
    try {
      const base = input.value.trim().replace(/\/$/, '');
      const r = await fetch('/admin/qr-link?base=' + encodeURIComponent(base));
      if (!r.ok) throw Error();
      const { url } = await r.json();
      await navigator.clipboard.writeText(url);
      document.getElementById('copy-url').textContent = '✓ Copiado';
    } catch { alert('No se pudo copiar el enlace. Podés usar Descargar QR.'); }
  });
  const search = document.getElementById('buscar-persona');
  const rows = [...document.querySelectorAll('#person-rows tr[data-persona]')];
  const results = document.getElementById('person-result-count');
  const empty = document.getElementById('person-filter-empty');
  const filter = () => {
    const term = search.value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim();
    let visible = 0;
    for (const row of rows) {
      const searchText = row.dataset.persona.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();
      row.hidden = !searchText.includes(term);
      if (!row.hidden) visible++;
    }
    results.textContent = `${visible} de ${rows.length} operarios`;
    empty.hidden = visible !== 0;
  };
  search.addEventListener('input', filter);
  filter();
  const monthInput = document.getElementById('mes');
  monthInput.addEventListener('change', () => {
    document.querySelector('.export-month').href = '/admin/horas.csv?mes=' + encodeURIComponent(monthInput.value);
  });
})();
