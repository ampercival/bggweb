// Games catalog: filtering, sorting, and paging for the live results table.
// Server-injected values (rows endpoint, default sort/dir, year-slider bounds)
// are read from data-* attributes on #filtersForm so this file stays static.
(function () {
  "use strict";

  var filtersForm = document.getElementById('filtersForm');
  var cfg = (filtersForm && filtersForm.dataset) || {};
  var ROWS_URL = cfg.rowsUrl || '/games/rows/';
  var DEFAULT_SORT = cfg.defaultSort || 'score_factor';
  var DEFAULT_DIR = cfg.defaultDir || 'desc';
  var YEAR_MIN = cfg.yearMin || '';
  var YEAR_MAX = cfg.yearMax || '';

  function clampRanges(minEl, maxEl) {
    const minv = parseFloat(minEl.value);
    const maxv = parseFloat(maxEl.value);
    if (minv > maxv) {
      maxEl.value = minv;
    }
  }
  function updateLabels() {
    const minAR = document.getElementById('min_avg_rating');
    const maxAR = document.getElementById('max_avg_rating');
    const minAW = document.getElementById('min_weight');
    const maxAW = document.getElementById('max_weight');
    const minY = document.getElementById('min_year');
    const maxY = document.getElementById('max_year');
    if (minAR && maxAR) {
      clampRanges(minAR, maxAR);
      document.getElementById('min_avg_rating_val').textContent = parseFloat(minAR.value).toFixed(1);
      document.getElementById('max_avg_rating_val').textContent = parseFloat(maxAR.value).toFixed(1);
    }
    if (minAW && maxAW) {
      clampRanges(minAW, maxAW);
      document.getElementById('min_weight_val').textContent = parseFloat(minAW.value).toFixed(1);
      document.getElementById('max_weight_val').textContent = parseFloat(maxAW.value).toFixed(1);
    }
    if (minY && maxY) {
      clampRanges(minY, maxY);
      document.getElementById('min_year_val').textContent = parseInt(minY.value);
      document.getElementById('max_year_val').textContent = parseInt(maxY.value);
    }
  }
  ['min_avg_rating', 'max_avg_rating', 'min_weight', 'max_weight', 'min_year', 'max_year'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('input', updateLabels);
  });
  updateLabels();

  var loadingIndicator = document.getElementById('loadingIndicator');

  function buildQuery() {
    var form = document.getElementById('filtersForm');
    var fd = new FormData(form);
    var params = new URLSearchParams();
    for (var pair of fd.entries()) {
      var key = pair[0];
      var value = pair[1];
      if (value === null || value === '') continue;
      if (key === 'categories' || key === 'owners') {
        params.append(key, value);
      } else {
        params.set(key, value);
      }
    }
    return params.toString();
  }

  async function updateRows() {
    var qs = buildQuery();
    var url = new URL(window.location.origin + ROWS_URL);
    url.search = qs;
    if (loadingIndicator) loadingIndicator.classList.add('is-active');
    try {
      var resp = await fetch(url, { headers: { 'x-requested-with': 'XMLHttpRequest' } });
      if (!resp.ok) { showLoadError(); return; }
      var data = await resp.json();
      var tbody = document.getElementById('rows');
      if (tbody) tbody.innerHTML = data.html;
      var countEl = document.getElementById('rowsCount');
      if (countEl) countEl.textContent = data.count;
      var totalGamesEl = document.getElementById('totalGames');
      if (totalGamesEl && Object.prototype.hasOwnProperty.call(data, 'total_games') && data.total_games != null) {
        totalGamesEl.textContent = data.total_games;
      }
      if (sortInput && dirInput) {
        updateSortIndicators(sortInput.value || 'score_factor', dirInput.value || 'desc');
      }
      var rs = document.getElementById('rangeStart'); if (rs) rs.textContent = data.start;
      var re = document.getElementById('rangeEnd'); if (re) re.textContent = data.end;
      document.querySelectorAll('.js-page-display').forEach(function (el) { el.textContent = data.page; });
      document.querySelectorAll('.js-pages-total').forEach(function (el) { el.textContent = data.num_pages; });
      var pageUrl = new URL(window.location.href);
      pageUrl.search = qs;
      window.history.replaceState({}, '', pageUrl);
    } catch (e) {
      showLoadError();
    } finally {
      if (loadingIndicator) loadingIndicator.classList.remove('is-active');
    }
  }

  function showLoadError() {
    var tbody = document.getElementById('rows');
    if (tbody) {
      tbody.innerHTML = '<tr><td colspan="25" style="color:#b91c1c;">Could not update results. Check your connection and try again.</td></tr>';
    }
  }

  var form = document.getElementById('filtersForm');
  var timer = null;
  function submitDebounced(delay) {
    if (delay === void 0) { delay = 400; }
    if (!form) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      updateRows();
    }, delay);
  }

  if (form) {
    form.querySelectorAll('select, input[type="checkbox"]').forEach(function (el) {
      el.addEventListener('change', function () { submitDebounced(10); });
    });
    form.querySelectorAll('input[type="text"]:not(#categoriesSearch):not(#mechanicsSearch), input[type="number"], input[type="range"]').forEach(function (el) {
      el.addEventListener('input', function () { submitDebounced(400); });
    });
    form.querySelectorAll('input[name="q"], input[type="checkbox"][name="owners"], select[name="type"], select[name="playable"], select[name="player_count"], input[type="checkbox"][name="categories"], input[type="checkbox"][name="mechanics"], input[name="min_year"], input[name="max_year"], input[name="min_avg_rating"], input[name="max_avg_rating"], input[name="min_weight"], input[name="max_weight"], input[name="min_voters"]').forEach(function (el) {
      el.addEventListener('change', function () { var p = document.getElementById('page_input'); if (p) p.value = '1'; });
      el.addEventListener('input', function () { var p = document.getElementById('page_input'); if (p) p.value = '1'; });
    });
  }

  var headerLinks = document.querySelectorAll('th a[data-sort]');
  var sortInput = form ? form.querySelector('[name="sort"]') : null;
  var dirInput = form ? form.querySelector('[name="dir"]') : null;

  function updateSortIndicators(currentSort, currentDir) {
    headerLinks.forEach(function (link) {
      var arrow = link.querySelector('.sort-arrow');
      var th = link.closest('th');
      if (link.dataset.sort === currentSort) {
        if (arrow) arrow.textContent = currentDir === 'desc' ? ' v' : ' ^';
        link.dataset.nextDir = currentDir === 'desc' ? 'asc' : 'desc';
        if (th) th.setAttribute('aria-sort', currentDir === 'desc' ? 'descending' : 'ascending');
      } else {
        if (arrow) arrow.textContent = '';
        link.dataset.nextDir = 'desc';
        if (th) th.setAttribute('aria-sort', 'none');
      }
    });
  }

  if (form && sortInput && dirInput) {
    updateSortIndicators(sortInput.value || DEFAULT_SORT, dirInput.value || DEFAULT_DIR);
  } else {
    updateSortIndicators(DEFAULT_SORT, DEFAULT_DIR);
  }

  headerLinks.forEach(function (link) {
    link.addEventListener('click', function (event) {
      if (!form || !sortInput || !dirInput) {
        return;
      }
      event.preventDefault();
      var sortKey = link.dataset.sort;
      var nextDir = link.dataset.nextDir || (sortInput.value === sortKey && dirInput.value === 'desc' ? 'asc' : 'desc');
      sortInput.value = sortKey;
      dirInput.value = nextDir;
      var pageInput = form.querySelector('[name="page"]');
      if (pageInput) pageInput.value = '1';
      updateSortIndicators(sortKey, nextDir);
      submitDebounced(10);
    });
  });

  var resetBtn = document.getElementById('resetDefaults');
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      var formEl = resetBtn.closest('form');
      if (!formEl) return;
      formEl.querySelector('[name="q"]').value = '';
      formEl.querySelectorAll('input[type="checkbox"][name="owners"]').forEach(function (cb) { cb.checked = false; });
      formEl.querySelector('[name="type"]').value = 'all';
      formEl.querySelector('[name="playable"]').value = 'playable';
      formEl.querySelector('[name="player_count"]').value = 'all';
      var minYear = formEl.querySelector('#min_year'); if (minYear) minYear.value = YEAR_MIN;
      var maxYear = formEl.querySelector('#max_year'); if (maxYear) maxYear.value = YEAR_MAX;
      var minAR = formEl.querySelector('#min_avg_rating'); if (minAR) minAR.value = '0';
      var maxAR = formEl.querySelector('#max_avg_rating'); if (maxAR) maxAR.value = '10';
      var minW = formEl.querySelector('#min_weight'); if (minW) minW.value = '0';
      var maxW = formEl.querySelector('#max_weight'); if (maxW) maxW.value = '5';
      var minV = formEl.querySelector('[name="min_voters"]'); if (minV) minV.value = '';
      if (sortInput) sortInput.value = 'score_factor';
      if (dirInput) dirInput.value = 'desc';
      updateSortIndicators('score_factor', 'desc');
      var p = formEl.querySelector('[name="page"]'); if (p) p.value = '1';
      var ps = formEl.querySelector('[name="page_size"]'); if (ps) ps.value = '200';
      document.querySelectorAll('input[type="checkbox"][name="categories"]').forEach(function (cb) { cb.checked = false; });
      document.querySelectorAll('input[type="checkbox"][name="mechanics"]').forEach(function (cb) { cb.checked = false; });
      updateLabels();
      updateRows();
    });
  }

  var catSearch = document.getElementById('categoriesSearch');
  if (catSearch) {
    var catSearchTimer = null;
    catSearch.addEventListener('input', function () {
      if (catSearchTimer) clearTimeout(catSearchTimer);
      catSearchTimer = setTimeout(function () {
        var q = (catSearch.value || '').toLowerCase();
        var items = document.querySelectorAll('#categoriesList .cat-item');
        items.forEach(function (el) {
          var name = el.getAttribute('data-name') || '';
          el.style.display = name.indexOf(q) !== -1 ? 'inline-flex' : 'none';
        });
        var more = document.getElementById('categoriesMore');
        if (more) {
          if (q) more.setAttribute('open', 'open');
          else more.removeAttribute('open');
        }
      }, 200);
    });
  }
  var clearOwnersBtn = document.getElementById('clearOwners');
  if (clearOwnersBtn) {
    clearOwnersBtn.addEventListener('click', function () {
      document.querySelectorAll('input[type="checkbox"][name="owners"]').forEach(function (cb) { cb.checked = false; });
      var p = document.getElementById('page_input'); if (p) p.value = '1';
      submitDebounced(10);
    });
  }

  var clearCats = document.getElementById('clearCategories');
  if (clearCats) {
    clearCats.addEventListener('click', function () {
      document.querySelectorAll('input[type="checkbox"][name="categories"]').forEach(function (cb) { cb.checked = false; });
      var p = document.getElementById('page_input'); if (p) p.value = '1';
      submitDebounced(10);
    });
  }

  var mecSearch = document.getElementById('mechanicsSearch');
  if (mecSearch) {
    var mecSearchTimer = null;
    mecSearch.addEventListener('input', function () {
      if (mecSearchTimer) clearTimeout(mecSearchTimer);
      mecSearchTimer = setTimeout(function () {
        var q = (mecSearch.value || '').toLowerCase();
        var items = document.querySelectorAll('#mechanicsList .cat-item');
        items.forEach(function (el) {
          var name = el.getAttribute('data-name') || '';
          el.style.display = name.indexOf(q) !== -1 ? 'inline-flex' : 'none';
        });
        var more = document.getElementById('mechanicsMore');
        if (more) {
          if (q) more.setAttribute('open', 'open');
          else more.removeAttribute('open');
        }
      }, 200);
    });
  }
  var clearMecs = document.getElementById('clearMechanics');
  if (clearMecs) {
    clearMecs.addEventListener('click', function () {
      document.querySelectorAll('input[type="checkbox"][name="mechanics"]').forEach(function (cb) { cb.checked = false; });
      var p = document.getElementById('page_input'); if (p) p.value = '1';
      submitDebounced(10);
    });
  }

  function changePage(delta) {
    var p = document.getElementById('page_input');
    var totalEl = document.querySelector('.js-pages-total');
    var total = parseInt(totalEl ? totalEl.textContent : '1', 10);
    if (isNaN(total) || total < 1) total = 1;
    var cur = parseInt(p ? p.value : '1', 10);
    if (isNaN(cur) || cur < 1) cur = 1;
    var next = cur + delta;
    if (next < 1) next = 1;
    if (next > total) next = total;
    if (p) p.value = String(next);
    submitDebounced(10);
  }
  document.querySelectorAll('.js-prev').forEach(function (btn) {
    btn.addEventListener('click', function () { changePage(-1); });
  });
  document.querySelectorAll('.js-next').forEach(function (btn) {
    btn.addEventListener('click', function () { changePage(1); });
  });
})();
