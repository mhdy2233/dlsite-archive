/* DLsite Archive - static front end (no framework, no build step) */
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const yen = n => n == null ? '—' : '¥' + Number(n).toLocaleString('ja-JP');
const bytes = n => {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 ? 0 : 1) + ' ' + u[i];
};
const num = n => n == null ? '—' : Number(n).toLocaleString('en-US');
const date = ts => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : '—';

const state = {
  all: [], meta: null, q: '', types: new Set(), tags: new Set(),
  age: '', sort: 'new', shown: 0, PAGE: 60, detailCache: new Map(),
};

const SORTS = [['new', '最新发售'], ['dl', '销量'], ['rate', '评分'],
               ['plow', '价格 ↑'], ['phigh', '价格 ↓']];
const AGES = [['', '全部'], ['r18', 'R18'], ['r15', 'R15'], ['all', '全年龄']];

/* ---------------------------------------------------------------- boot */
function gate() {
  const el = $('#gate');
  if (localStorage.getItem('dlage') === 'ok') { el.remove(); return; }
  el.hidden = false;
  $('#gate-yes').onclick = () => { localStorage.setItem('dlage', 'ok'); el.remove(); };
  $('#gate-no').onclick = () => { location.href = 'https://www.dlsite.com/'; };
}

async function boot() {
  gate();
  let index = [], meta = null;
  try {
    [index, meta] = await Promise.all([
      fetch('api/index.json').then(r => r.ok ? r.json() : []),
      fetch('api/meta.json').then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
  } catch (e) { console.error('load failed', e); }
  state.all = Array.isArray(index) ? index : [];
  state.meta = meta;
  renderFilters();
  render();
  bindSearch();
  routeFromLocation();
}

/* ------------------------------------------------------------- filters */
function renderFilters() {
  $('#sort').innerHTML = SORTS.map(([k, label]) =>
    `<button class="chip${state.sort === k ? ' on' : ''}" data-sort="${k}">${label}</button>`
  ).join('');
  $('#sort').onclick = e => {
    const b = e.target.closest('[data-sort]'); if (!b) return;
    state.sort = b.dataset.sort; renderFilters(); render();
  };

  const types = state.meta?.types || [];
  $('#types').innerHTML = types.map(t =>
    `<button class="chip${state.types.has(t.name) ? ' on' : ''}" data-type="${esc(t.name)}">${esc(t.name)}<span class="n">${t.n}</span></button>`
  ).join('');
  $('#types').onclick = e => {
    const b = e.target.closest('[data-type]'); if (!b) return;
    const v = b.dataset.type;
    state.types.has(v) ? state.types.delete(v) : state.types.add(v);
    renderFilters(); render();
  };

  // tags: keep the top slice, but always show selected ones
  let tags = (state.meta?.tags || []).map(t => t.name);
  for (const t of state.tags) if (!tags.includes(t)) tags.unshift(t);
  $('#tags').innerHTML = tags.slice(0, 60).map(t =>
    `<button class="chip${state.tags.has(t) ? ' on' : ''}" data-tag="${esc(t)}">${esc(t)}</button>`
  ).join('');
  $('#tags').onclick = e => {
    const b = e.target.closest('[data-tag]'); if (!b) return;
    const v = b.dataset.tag;
    state.tags.has(v) ? state.tags.delete(v) : state.tags.add(v);
    renderFilters(); render();
  };

  $('#ages').innerHTML = AGES.map(([k, label]) =>
    `<button class="chip${state.age === k ? ' on' : ''}" data-age="${k}">${label}</button>`
  ).join('');
  $('#ages').onclick = e => {
    const b = e.target.closest('[data-age]'); if (!b) return;
    state.age = b.dataset.age; renderFilters(); render();
  };

  const total = state.all.length;
  $('#stats').textContent = state.meta?.updated
    ? `${total} 部作品 · 更新 ${new Date(state.meta.updated * 1000).toLocaleString('zh-CN')}`
    : `${total} 部作品`;
}

function bindSearch() {
  const q = $('#q'), clr = $('#qclear');
  let t;
  q.addEventListener('input', () => {
    clr.hidden = !q.value;
    clearTimeout(t);
    t = setTimeout(() => { state.q = q.value; render(); }, 120);
  });
  clr.onclick = () => { q.value = ''; state.q = ''; clr.hidden = true; render(); q.focus(); };
  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); }
    if (e.key === 'Escape') closeDetail();
  });
}

/* -------------------------------------------------------------- render */
function filtered() {
  const q = state.q.trim().toLowerCase();
  const terms = q ? q.split(/\s+/).filter(Boolean) : [];
  const out = state.all.filter(c => {
    if (state.types.size && !state.types.has(c.ty)) return false;
    if (state.tags.size && ![...state.tags].every(t => (c.g || []).includes(t))) return false;
    if (state.age && c.age !== state.age) return false;
    if (terms.length) {
      const hay = `${c.i} ${c.t} ${c.rm || ''} ${c.c} ${(c.g || []).join(' ')} ${c.d || ''}`.toLowerCase();
      if (!terms.every(t => hay.includes(t))) return false;
    }
    return true;
  });
  const s = state.sort;
  out.sort((a, b) => s === 'dl' ? (b.dl || 0) - (a.dl || 0)
    : s === 'rate' ? (b.r || 0) - (a.r || 0)
    : s === 'plow' ? (a.p || 1e9) - (b.p || 1e9)
    : s === 'phigh' ? (b.p || 0) - (a.p || 0)
    : (b.rel || 0) - (a.rel || 0));
  return out;
}

function cardHTML(c) {
  const off = c.po && c.p > 0 && c.po > c.p ? Math.round((1 - c.p / c.po) * 100) : 0;
  const img = c.th ? `<img loading="lazy" src="${esc(c.th)}" alt="${esc(c.t)}">` : '';
  return `<article class="card" data-id="${esc(c.i)}">
    <div class="cover">${img}
      <span class="badge">${esc(c.ty || '')}</span>
      ${c.age === 'r18' ? '<span class="badge age">R18</span>' : ''}
      ${off ? `<span class="badge off">-${off}%</span>` : ''}
    </div>
    <div class="card-body">
      <div class="card-title">${esc(c.t)}</div>
      <div class="card-circle">${esc(c.c || '')}</div>
      <div class="card-foot">
        <span class="price">${c.p === 0 ? '免费' : yen(c.p)}${off ? `<span class="old">${yen(c.po)}</span>` : ''}</span>
        <span class="meta">${c.r ? `<span class="star">★${c.r}</span>` : ''}${c.dl ? `<span>${num(c.dl)}</span>` : ''}</span>
      </div>
    </div>
  </article>`;
}

function render() {
  const list = filtered();
  state.shown = Math.min(state.PAGE, list.length);
  $('#grid').innerHTML = list.slice(0, state.shown).map(cardHTML).join('');
  $('#empty').hidden = list.length > 0;
  $('#more').hidden = list.length <= state.shown;
  $('#stats').textContent = `${list.length} / ${state.all.length} 部作品`;
  window.scrollTo({ top: 0 });
}

$('#morebtn').onclick = () => {
  const list = filtered();
  const next = Math.min(state.shown + state.PAGE, list.length);
  $('#grid').insertAdjacentHTML('beforeend', list.slice(state.shown, next).map(cardHTML).join(''));
  state.shown = next;
  $('#more').hidden = list.length <= state.shown;
};

/* -------------------------------------------------------------- detail */
$('#grid').addEventListener('click', e => {
  const card = e.target.closest('.card');
  if (card) openDetail(card.dataset.id);
});
$('#mclose').onclick = closeDetail;
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeDetail(); });

function closeDetail() {
  $('#modal').classList.remove('on');
  document.body.style.overflow = '';
  if (location.pathname.match(WORK_RE)) history.pushState({}, '', '/');
}

/* /RJ01530415 -> open that work directly (served through 404.html) */
const WORK_RE = /\/(RJ\d{5,8})\/?$/i;

function routeFromLocation() {
  const m = location.pathname.match(WORK_RE);
  if (m) openDetail(m[1].toUpperCase(), { fromUrl: true });
}

window.addEventListener('popstate', routeFromLocation);

async function openDetail(id, opts = {}) {
  const m = $('#modal');
  m.classList.add('on');
  document.body.style.overflow = 'hidden';
  if (!opts.fromUrl) history.pushState({ w: id }, '', '/' + id);
  $('#mtitle').textContent = '加载中…';
  $('#minfo').innerHTML = '';
  $('#mhero').removeAttribute('src');
  $('#mthumbs').innerHTML = '';

  let w = state.detailCache.get(id);
  if (!w) {
    try { w = await fetch(`api/works/${id}.json`).then(r => r.ok ? r.json() : null); }
    catch (e) { w = null; }
    if (w) state.detailCache.set(id, w);
  }
  if (!w) { $('#mtitle').textContent = '详情加载失败'; return; }

  $('#mtitle').textContent = w.title;
  const li = w.local_images || {};
  const hero = li.main ? `images/${li.main}` : (w.images.main || '').replace(/^\/\//, 'https://');
  $('#mhero').src = hero;
  $('#mhero').alt = w.title;
  const samples = (li.samples || []).map(s => `images/${s}`);
  const cdnSamples = (w.images.samples || []).map(u => u.startsWith('//') ? 'https:' + u : u);
  const shotList = samples.length ? samples : cdnSamples;
  const fullSamples = cdnSamples.length ? cdnSamples : shotList;
  $('#mthumbs').innerHTML = shotList.map((s, i) =>
    `<img loading="lazy" src="${esc(s)}" data-full="${esc(fullSamples[i] || s)}" alt="sample ${i + 1}">`
  ).join('');
  $('#mthumbs').onclick = e => {
    const img = e.target.closest('img'); if (!img) return;
    $('#mhero').src = img.dataset.full;
  };

  const tags = (w.tags || []).map(t =>
    `<button class="chip" data-tag="${esc(t.name)}">${esc(t.name)}</button>`).join('');
  const langs = (w.languages || []).join(' / ') || '—';
  const off = w.price.official_jpy && w.price.jpy > 0 && w.price.official_jpy > w.price.jpy;
  const rank = (w.ranking || []).slice(0, 3).map(r =>
    `${r.period} #${r.rank}`).join(' · ');
  const intro = renderIntro(w.introductions);

  $('#minfo').innerHTML = `
    <h1>${esc(w.title)}</h1>
    ${w.title_romaji ? `<div class="romaji">${esc(w.title_romaji)}</div>` : ''}
    <div class="price-row">
      <span class="big">${w.price.jpy === 0 ? '免费' : yen(w.price.jpy)}</span>
      ${off ? `<span class="was">${yen(w.price.official_jpy)}</span>` : ''}
      ${w.point?.point_amount ? `<span class="cur">返点 ${w.point.point_amount}pt</span>` : ''}
    </div>
    <dl class="kv">
      <dt>社团</dt><dd>${esc(w.circle.name || '—')}</dd>
      <dt>类型</dt><dd>${esc(w.type.name || '—')}</dd>
      <dt>发售日</dt><dd>${date(w.release)}</dd>
      <dt>文件</dt><dd>${esc(w.file.type || '—')} · ${bytes(w.file.size)}</dd>
      <dt>语言</dt><dd>${esc(langs)}</dd>
      <dt>评价</dt><dd>${w.rating.average ? `★ ${w.rating.average}` : '—'}${w.review_count ? ` · ${num(w.review_count)} 条评价` : ''}</dd>
      <dt>销量</dt><dd>${num(w.dl_count)}</dd>
      ${w.wishlist_count ? `<dt>收藏</dt><dd>${num(w.wishlist_count)}</dd>` : ''}
      ${rank ? `<dt>榜位</dt><dd>${esc(rank)}</dd>` : ''}
    </dl>
    <div class="taglist">${tags}</div>
    <div class="intro">${intro}</div>
    <a class="ext" href="${esc(w.url)}" target="_blank" rel="noopener">在 DLsite 查看</a>
  `;
  $('#minfo').querySelectorAll('[data-tag]').forEach(b => {
    b.onclick = () => {
      state.tags.add(b.dataset.tag);
      closeDetail(); renderFilters(); render();
    };
  });
}

function renderIntro(intros) {
  if (!Array.isArray(intros) || !intros.length) return '';
  return intros.map(sec => {
    const title = sec.title ? `<h3>${esc(sec.title)}</h3>` : '';
    const body = (sec.body || []).map(b => {
      let html = b.intro || '';
      if (b.image_info?.url) {
        const u = b.image_info.url.replace(/^\/\//, 'https://');
        html += `<img loading="lazy" src="${esc(u)}" alt="${esc(b.image_info.alt || '')}">`;
      }
      return html;
    }).join('');
    return title + body;
  }).join('');
}

boot();
