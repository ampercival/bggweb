// Canonical form state drives rows, shareable URLs and filtered exports.
(function () {
  "use strict";
  const form = document.getElementById('filtersForm');
  if (!form) return;
  const loading = document.getElementById('loadingIndicator');
  const notice = document.getElementById('resultsNotice');
  let sequence = 0, controller = null, timer = null;
  const sort = form.elements.namedItem('sort');
  const direction = form.elements.namedItem('dir');
  const page = form.elements.namedItem('page');

  function labels() {
    ['avg_rating', 'weight', 'year'].forEach(key => {
      const low = document.getElementById('min_' + key);
      const high = document.getElementById('max_' + key);
      if (Number(low.value) > Number(high.value)) high.value = low.value;
      [low, high].forEach(input => {
        document.getElementById(input.id + '_val').textContent = key === 'year' ? input.value : Number(input.value).toFixed(1);
      });
    });
  }
  function indicators() {
    document.querySelectorAll('th a[data-sort]').forEach(link => {
      const active = link.dataset.sort === sort.value;
      link.querySelector('.sort-arrow').textContent = active ? (direction.value === 'desc' ? ' ▼' : ' ▲') : '';
      link.closest('th').setAttribute('aria-sort', active ? (direction.value === 'desc' ? 'descending' : 'ascending') : 'none');
    });
  }
  function searchFacets(id, list, more) {
    const input = document.getElementById(id);
    if (!input) return;
    const query = input.value.toLowerCase();
    document.querySelectorAll('#' + list + ' .cat-item').forEach(item => {
      item.hidden = !item.dataset.name.includes(query);
    });
    if (query) document.getElementById(more)?.setAttribute('open', '');
  }
  async function update(current) {
    const query = new URLSearchParams(new FormData(form));
    controller = new AbortController();
    loading?.classList.add('is-active');
    loading?.setAttribute('aria-hidden', 'false');
    document.getElementById('rows').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(form.dataset.rowsUrl + '?' + query, {
        headers: {'x-requested-with': 'XMLHttpRequest'}, signal: controller.signal,
      });
      if (!response.ok) throw new Error('Request failed');
      const data = await response.json();
      if (current !== sequence) return;
      const facets = document.getElementById('catalogFacets');
      const saved = {};
      ['categoriesSearch', 'mechanicsSearch'].forEach(id => saved[id] = document.getElementById(id)?.value || '');
      const opened = ['categoriesMore', 'mechanicsMore'].filter(id => document.getElementById(id)?.open);
      if (facets && data.facets_html) facets.innerHTML = data.facets_html;
      Object.entries(saved).forEach(([id, value]) => { if (document.getElementById(id)) document.getElementById(id).value = value; });
      opened.forEach(id => document.getElementById(id)?.setAttribute('open', ''));
      searchFacets('categoriesSearch', 'categoriesList', 'categoriesMore');
      searchFacets('mechanicsSearch', 'mechanicsList', 'mechanicsMore');
      document.getElementById('rows').innerHTML = data.html;
      for (const [id, key] of [['rowsCount', 'count'], ['rangeStart', 'start'], ['rangeEnd', 'end'], ['totalGames', 'total_games']]) {
        document.getElementById(id).textContent = data[key];
      }
      page.value = data.page;
      document.querySelectorAll('.js-page-display').forEach(el => el.textContent = data.page);
      document.querySelectorAll('.js-pages-total').forEach(el => el.textContent = data.num_pages);
      document.querySelectorAll('.js-prev').forEach(el => el.disabled = data.page <= 1);
      document.querySelectorAll('.js-next').forEach(el => el.disabled = data.page >= data.num_pages);
      const canonical = data.query || query.toString();
      window.history.replaceState({}, '', window.location.pathname + '?' + canonical);
      document.getElementById('filteredExport').href = '/export.csv?' + canonical;
      notice.textContent = data.count + ' matching game/player-count rows.';
      indicators(); activeFilters();
    } catch (error) {
      if (current === sequence && error.name !== 'AbortError') {
        notice.textContent = 'Could not update results. Your previous results are still shown. Try Apply filters again.';
      }
    } finally {
      if (current === sequence) {
        loading?.classList.remove('is-active');
        loading?.setAttribute('aria-hidden', 'true');
        document.getElementById('rows').setAttribute('aria-busy', 'false');
      }
    }
  }
  function schedule(delay = 250) {
    sequence += 1;
    if (controller) controller.abort();
    clearTimeout(timer);
    const current = sequence;
    timer = setTimeout(() => update(current), delay);
  }
  form.addEventListener('submit', event => { event.preventDefault(); schedule(0); });
  form.addEventListener('input', event => {
    if (event.target.id === 'categoriesSearch') { searchFacets('categoriesSearch', 'categoriesList', 'categoriesMore'); return; }
    if (event.target.id === 'mechanicsSearch') { searchFacets('mechanicsSearch', 'mechanicsList', 'mechanicsMore'); return; }
    if (event.target.type === 'range') labels();
    page.value = '1'; schedule();
  });
  form.addEventListener('change', event => {
    if (['categoriesSearch', 'mechanicsSearch'].includes(event.target.id)) return;
    page.value = '1'; schedule(0);
  });
  form.addEventListener('click', event => {
    const clears = {clearOwners: 'owners', clearCategories: 'categories', clearMechanics: 'mechanics'};
    if (clears[event.target.id]) {
      form.querySelectorAll('[name="' + clears[event.target.id] + '"]').forEach(el => el.checked = false);
      page.value = '1'; schedule(0);
    }
    if (event.target.id === 'resetDefaults') window.location.assign('/games/');
  });
  document.querySelectorAll('th a[data-sort]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    direction.value = sort.value === link.dataset.sort && direction.value === 'desc' ? 'asc' : 'desc';
    sort.value = link.dataset.sort; page.value = '1'; schedule(0);
  }));
  document.querySelectorAll('.js-prev, .js-next').forEach(button => button.addEventListener('click', () => {
    const total = Number(document.querySelector('.js-pages-total').textContent);
    page.value = Math.max(1, Math.min(total, Number(page.value) + (button.classList.contains('js-next') ? 1 : -1)));
    schedule(0);
  }));
  function activeFilters() {
    const target = document.getElementById('activeFilters');
    if (!target) return;
    const values = [];
    if (form.elements.namedItem('q').value) values.push('Title: ' + form.elements.namedItem('q').value);
    const count = form.elements.namedItem('player_count').value;
    if (count !== 'all') values.push('Players: ' + count);
    form.querySelectorAll('input[type="checkbox"]:checked').forEach(el => values.push(el.value));
    const minimum = form.elements.namedItem('min_poll_votes').value;
    if (Number(minimum)) values.push('Poll votes: ' + minimum + '+');
    target.replaceChildren(...values.map(value => { const chip = document.createElement('span'); chip.className = 'filter-chip'; chip.textContent = value; return chip; }));
  }
  const detailed = document.getElementById('detailedColumns');
  detailed?.addEventListener('change', () => document.querySelector('.data-table').classList.toggle('show-details', detailed.checked));
  labels(); indicators(); activeFilters();
})();
