(() => {
  const D = JSON.parse(document.getElementById('data').textContent);
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const bySlug = Object.fromEntries(D.products.map(p => [p.slug, p]));
  const T = D.t;
  const fmt = p => (p.priceFrom ? T.from + ' ' : '') + p.price.toLocaleString(T.numLocale) + '\u00a0₽';
  // --- own visit analytics (time on site counts only while the tab is visible)
  const track = (() => {
    const rnd = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Math.random().toString(36).slice(2) + Date.now().toString(36));
    const store = (k, v) => { try { if (v !== undefined) localStorage.setItem(k, v); return localStorage.getItem(k); } catch (e) { return v === undefined ? null : v; } };
    const u = store('gl_u') || store('gl_u', rnd());
    let v = store('gl_v');
    const fresh = !v || Date.now() - (+store('gl_vt') || 0) > 30 * 60e3;
    if (fresh) v = store('gl_v', rnd());
    let active = fresh ? 0 : (+store('gl_va') || 0) * 1000;
    let since = document.visibilityState === 'visible' ? Date.now() : null;
    const sec = () => Math.round((active + (since ? Date.now() - since : 0)) / 1000);
    const send = (t, extra) => {
      store('gl_vt', Date.now()); store('gl_va', sec());
      const body = JSON.stringify({ v, u, t, a: sec(), ...(extra || {}) });
      try {
        if (!(navigator.sendBeacon && navigator.sendBeacon('/api/track', new Blob([body], { type: 'text/plain' }))))
          fetch('/api/track', { method: 'POST', body, keepalive: true });
      } catch (e) {}
    };
    send('start', { url: location.href, ref: document.referrer, lang: D.lang, scr: `${screen.width}x${screen.height}` });
    setInterval(() => { if (document.visibilityState === 'visible') send('ping'); }, 15000);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') since = Date.now();
      else { if (since) { active += Date.now() - since; since = null; } send('end'); }
    });
    window.__glVisit = v;
    return name => send('event', { e: name });
  })();
  const goal = name => {
    track(name);
    try { if (window.ym && window.__glMetrika) ym(window.__glMetrika, 'reachGoal', name); } catch (e) {}
  };

  // --- menu
  const burger = $('#burger'), nav = $('#nav');
  burger.addEventListener('click', () => {
    const open = burger.getAttribute('aria-expanded') !== 'true';
    burger.setAttribute('aria-expanded', open);
    nav.classList.toggle('is-open', open);
  });
  $$('a', nav).forEach(a => a.addEventListener('click', () => { burger.setAttribute('aria-expanded', false); nav.classList.remove('is-open'); }));

  // --- goals on outbound clicks
  document.addEventListener('click', e => {
    const g = e.target.closest('[data-goal]'); if (g) goal(g.dataset.goal);
    if (e.target.closest('[data-lang-switch]')) goal('lang_switch');
  });

  // --- catalog filter
  const cards = $$('.card'), chips = $$('.chip');
  const match = (f, p) => f === 'all' ? true
    : f === 'under2000' ? p.price <= 2000
    : (f === 'him' || f === 'her') ? D.tags[f].includes(p.slug)
    : p.cat === f;
  function applyFilter(f) {
    let n = 0;
    cards.forEach(c => { const ok = match(f, bySlug[c.dataset.slug]); c.hidden = !ok; n += ok; });
    chips.forEach(ch => { const on = ch.dataset.filter === f; ch.classList.toggle('is-active', on); ch.setAttribute('aria-pressed', on); });
    $('#empty').hidden = n > 0;
    try { history.replaceState(null, '', f === 'all' ? location.pathname : '#f=' + f); } catch (e) {}
  }
  chips.forEach(ch => ch.addEventListener('click', () => applyFilter(ch.dataset.filter)));
  $$('[data-jump]').forEach(b => b.addEventListener('click', () => {
    applyFilter(b.dataset.jump);
    $('#catalog').scrollIntoView();
  }));
  const m = location.hash.match(/^#f=(\w+)/);
  if (m && chips.some(c => c.dataset.filter === m[1])) applyFilter(m[1]);

  // --- product modal
  const modal = $('#modal'), mImg = $('#m-img'), mThumbs = $('#m-thumbs');
  function show(p, i) {
    mImg.src = p.imgs[i][1];
    mImg.alt = `${p.name} — ${T.photo} ${i + 1} ${T.of} ${p.imgs.length}`;
    $$('button', mThumbs).forEach((b, j) => b.setAttribute('aria-current', j === i));
  }
  function openProduct(slug) {
    const p = bySlug[slug]; if (!p) return;
    $('#m-cat').textContent = D.cats[p.cat];
    $('#m-title').textContent = p.name;
    $('#m-price').textContent = fmt(p);
    $('#m-desc').replaceChildren(...p.desc.map(t => Object.assign(document.createElement('li'), { textContent: t })));
    mThumbs.replaceChildren(...p.imgs.map((id, i) => {
      const b = document.createElement('button');
      b.type = 'button'; b.setAttribute('aria-label', `${T.photoN} ${i + 1}`);
      b.innerHTML = `<img src="${id[0]}" alt="" loading="lazy">`;
      b.addEventListener('click', () => show(p, i));
      return b;
    }));
    mThumbs.hidden = p.imgs.length < 2;
    if (p.imgs.length) show(p, 0); else { mImg.removeAttribute('src'); mImg.alt = ''; }
    $('#m-order').dataset.product = p.name;
    modal.showModal();
    goal('product_open');
    try { history.replaceState(null, '', '#p=' + slug); } catch (e) {}
  }
  function closeModal() { modal.close(); }
  modal.addEventListener('close', () => { try { history.replaceState(null, '', location.pathname); } catch (e) {} });
  $('#m-close').addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  $$('.card__open').forEach(b => b.addEventListener('click', () => openProduct(b.closest('.card').dataset.slug)));
  // swipe/arrow between photos
  let sx = null;
  mImg.addEventListener('touchstart', e => { sx = e.touches[0].clientX; }, { passive: true });
  mImg.addEventListener('touchend', e => { if (sx === null) return; const dx = e.changedTouches[0].clientX - sx; sx = null; if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1); });
  modal.addEventListener('keydown', e => { if (e.key === 'ArrowRight') step(1); if (e.key === 'ArrowLeft') step(-1); });
  function step(d) {
    const btns = $$('button', mThumbs); if (btns.length < 2) return;
    const i = btns.findIndex(b => b.getAttribute('aria-current') === 'true');
    btns[(i + d + btns.length) % btns.length].click();
  }
  const pm = location.hash.match(/^#p=([\w-]+)/);
  if (pm) openProduct(pm[1]);

  // --- "order" buttons prefill the form
  const form = $('#form'), sel = $('#product');
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-product]'); if (!a) return;
    const name = a.dataset.product;
    if ([...sel.options].some(o => o.value === name || o.text === name)) sel.value = name;
    if (modal.open) closeModal();
    goal('order_click');
  });

  // --- form submit -> /api/order -> Telegram bot
  const msg = $('#form-msg'), started = Date.now();
  const say = (t, cls) => { msg.textContent = t; msg.className = 'form__msg ' + (cls || ''); };
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(form);
    let bad = false;
    ['name', 'contact'].forEach(n => { const el = form.elements[n]; const ok = el.value.trim().length >= 2; el.classList.toggle('is-invalid', !ok); bad ||= !ok; });
    const consent = form.elements.consent; consent.closest('label').classList.toggle('is-invalid', !consent.checked); bad ||= !consent.checked;
    if (bad) return say(T.fill, 'err');
    const btn = $('button[type=submit]', form); btn.disabled = true; say(T.sending);
    try {
      const r = await fetch('/api/order', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...Object.fromEntries(f), elapsed: Date.now() - started, lang: D.lang, visit: window.__glVisit || '' })
      });
      if (!r.ok) throw new Error(r.status);
      form.reset();
      say(T.sent, 'ok');
      goal('order_sent');
    } catch (err) {
      msg.className = 'form__msg err';
      msg.textContent = T.failed + ' ';
      if (D.tg) msg.insertAdjacentHTML('beforeend', `<a href="https://t.me/${D.tg}" target="_blank" rel="noopener">Telegram</a> `);
      if (D.phone) msg.insertAdjacentHTML('beforeend', `· <a href="tel:${D.phone}">${D.phoneText}</a>`);
    } finally { btn.disabled = false; }
  });
})();
