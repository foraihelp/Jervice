// Instant tooltips for anything with a data-tip attribute. The browser's
// built-in title tooltips take about a second to appear and don't work on
// SVG icons, so this shows a small label after 120 ms instead.
(function () {
  const tip = document.createElement('div');
  Object.assign(tip.style, {
    position: 'fixed', zIndex: '99999', pointerEvents: 'none', display: 'none',
    maxWidth: '250px', padding: '6px 10px', background: '#0E1620', color: '#D7E2EA',
    border: '1px solid rgba(111,227,255,0.35)', borderRadius: '6px',
    font: "500 11.5px/1.4 'Sora', system-ui, sans-serif", letterSpacing: '0',
    textTransform: 'none', boxShadow: '0 6px 20px rgba(0,0,0,0.5)',
  });
  document.body.appendChild(tip);

  let current = null;
  let timer = null;

  function place(el) {
    const r = el.getBoundingClientRect();
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    let x = r.left + r.width / 2 - w / 2;
    x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
    let y = r.bottom + 8;
    if (y + h > window.innerHeight - 8) y = r.top - h - 8;
    tip.style.left = x + 'px';
    tip.style.top = Math.max(8, y) + 'px';
  }

  function show(el) {
    const text = el.getAttribute('data-tip');
    if (!text) return;
    tip.textContent = text;
    tip.style.display = 'block';
    place(el);
  }

  function hide() {
    clearTimeout(timer);
    tip.style.display = 'none';
    current = null;
  }

  document.addEventListener('mouseover', (e) => {
    const el = e.target.closest ? e.target.closest('[data-tip]') : null;
    if (el === current) return;
    hide();
    if (el) {
      current = el;
      timer = setTimeout(() => show(el), 120);
    }
  });
  document.addEventListener('mouseout', (e) => {
    if (current && !current.contains(e.relatedTarget)) hide();
  });
  document.addEventListener('mousedown', hide);
  document.addEventListener('scroll', hide, true);
})();
