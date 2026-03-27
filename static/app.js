'use strict';

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map('map', { zoomControl: true }).setView([20, 0], 2);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  origin:      { mode: 'port', point: null },   // point: {name, lat, lon}
  destination: { mode: 'port', point: null },
};

let routeLayer    = null;
let markersLayer  = null;
let lastRouteData = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const ui = {
  calcBtn:      $('calc-btn'),
  errorBox:     $('error-box'),
  statsPanel:   $('stats-panel'),
  loading:      $('loading'),
  statKm:       $('stat-km'),
  statNmi:      $('stat-nmi'),
  statTime:     $('stat-time'),
  statsLabel:   $('stats-label'),
  speedInput:   $('speed-input'),
  speedPreset:  $('speed-preset'),
};

// ── Speed selector ─────────────────────────────────────────────────────────
function getSpeed() {
  const v = parseFloat(ui.speedInput.value);
  return (isNaN(v) || v <= 0) ? 14 : v;
}

function updateTransitStat() {
  if (!ui.statsPanel.classList.contains('hidden') && lastRouteData) {
    const spd = getSpeed();
    ui.statTime.textContent = formatDuration(lastRouteData.distance_nmi / spd);
    document.querySelector('#stats-panel .stat-card.wide .stat-label').textContent =
      `Transit at ${spd} kn`;
  }
}

ui.speedPreset.addEventListener('change', () => {
  if (ui.speedPreset.value) {
    ui.speedInput.value = ui.speedPreset.value;
    ui.speedPreset.value = '';
    updateTransitStat();
  }
});

ui.speedInput.addEventListener('input', updateTransitStat);

// ── Tab switching ──────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const wp  = btn.dataset.wp;   // 'origin' | 'destination'
    const tab = btn.dataset.tab;  // 'port'   | 'coords'

    // Update button active state
    document.querySelectorAll(`.tab-btn[data-wp="${wp}"]`).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Show correct pane
    $(`${wp}-port`  ).classList.toggle('active', tab === 'port');
    $(`${wp}-coords`).classList.toggle('active', tab === 'coords');

    state[wp].mode  = tab;
    state[wp].point = null;
    $(`${wp}-selected`).textContent = '';
  });
});

// ── Autocomplete ───────────────────────────────────────────────────────────
function setupAutocomplete(wp) {
  const input    = $(`${wp}-search`);
  const dropdown = $(`${wp}-dropdown`);
  const selected = $(`${wp}-selected`);
  let debounceTimer = null;
  let focusedIdx    = -1;
  let items         = [];

  function openDropdown(results) {
    items = results;
    focusedIdx = -1;
    dropdown.innerHTML = '';

    if (!results.length) {
      dropdown.classList.remove('open');
      return;
    }

    results.forEach((port, i) => {
      const div = document.createElement('div');
      div.className = 'dropdown-item';
      div.innerHTML = `
        <span class="di-locode">${port.locode}</span>
        <div class="di-info">
          <div class="di-name">${port.name}</div>
          <div class="di-country">${port.country}</div>
        </div>`;
      div.addEventListener('mousedown', e => {
        e.preventDefault();
        selectPort(port);
      });
      dropdown.appendChild(div);
    });

    dropdown.classList.add('open');
  }

  function closeDropdown() {
    dropdown.classList.remove('open');
    focusedIdx = -1;
  }

  function setFocus(idx) {
    const divs = dropdown.querySelectorAll('.dropdown-item');
    divs.forEach(d => d.classList.remove('focused'));
    if (idx >= 0 && idx < divs.length) {
      divs[idx].classList.add('focused');
      divs[idx].scrollIntoView({ block: 'nearest' });
    }
    focusedIdx = idx;
  }

  function selectPort(port) {
    state[wp].point = { name: port.name, lat: port.lat, lon: port.lon };
    input.value     = `${port.name} (${port.locode})`;
    selected.textContent = `✓ ${port.lat.toFixed(3)}, ${port.lon.toFixed(3)}`;
    closeDropdown();
  }

  // Debounced search
  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    state[wp].point = null;
    selected.textContent = '';
    const q = input.value.trim();
    if (!q) { closeDropdown(); return; }
    debounceTimer = setTimeout(async () => {
      try {
        const res     = await fetch(`/api/ports/search?q=${encodeURIComponent(q)}&limit=10`);
        const results = await res.json();
        openDropdown(results);
      } catch { /* ignore */ }
    }, 300);
  });

  // Keyboard nav
  input.addEventListener('keydown', e => {
    if (!dropdown.classList.contains('open')) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setFocus(Math.min(focusedIdx + 1, items.length - 1)); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setFocus(Math.max(focusedIdx - 1, 0)); }
    if (e.key === 'Enter')     { if (focusedIdx >= 0) { selectPort(items[focusedIdx]); } }
    if (e.key === 'Escape')    { closeDropdown(); }
  });

  // Click outside
  document.addEventListener('click', e => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      closeDropdown();
    }
  });
}

setupAutocomplete('origin');
setupAutocomplete('destination');

// ── Resolve input to point ─────────────────────────────────────────────────
function resolvePoint(wp) {
  const s = state[wp];
  if (s.mode === 'port') {
    if (!s.point) throw new Error(`Select a port for ${wp}`);
    return { lat: s.point.lat, lon: s.point.lon };
  }
  // coords mode
  const lat = parseFloat($(`${wp}-lat`).value);
  const lon = parseFloat($(`${wp}-lon`).value);
  if (isNaN(lat) || isNaN(lon)) throw new Error(`Enter valid coordinates for ${wp}`);
  if (lat < -90 || lat > 90)    throw new Error(`${wp} latitude must be between -90 and 90`);
  if (lon < -180 || lon > 180)  throw new Error(`${wp} longitude must be between -180 and 180`);
  return { lat, lon };
}

// ── Geo math ───────────────────────────────────────────────────────────────
const EARTH_NMI = 3440.065;

function haversineNmi(lat1, lon1, lat2, lon2) {
  const toR = Math.PI / 180;
  const dLat = (lat2 - lat1) * toR;
  const dLon = (lon2 - lon1) * toR;
  const a = Math.sin(dLat / 2) ** 2
          + Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLon / 2) ** 2;
  return EARTH_NMI * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Build parallel arrays: cumDist[i] = nmi from start to vertex i
function buildCumDist(coords) {
  // coords = [[lon,lat], ...]
  const cum = [0];
  for (let i = 1; i < coords.length; i++) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    cum.push(cum[i - 1] + haversineNmi(lat1, lon1, lat2, lon2));
  }
  return cum;
}

// Return nmi from route start to closest point on route to `latlng`
function distAlongRoute(latlng, coords, cumDist) {
  let bestDist = Infinity, bestNmi = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[i + 1];
    const dx = lon2 - lon1, dy = lat2 - lat1;
    const len2 = dx * dx + dy * dy;
    let t = len2 === 0 ? 0 :
      Math.max(0, Math.min(1, ((latlng.lng - lon1) * dx + (latlng.lat - lat1) * dy) / len2));
    const nearLon = lon1 + t * dx;
    const nearLat = lat1 + t * dy;
    const d = haversineNmi(latlng.lat, latlng.lng, nearLat, nearLon);
    if (d < bestDist) {
      bestDist = d;
      bestNmi  = cumDist[i] + t * (cumDist[i + 1] - cumDist[i]);
    }
  }
  return bestNmi;
}

// ── Format duration ────────────────────────────────────────────────────────
function formatDuration(hours) {
  const d = Math.floor(hours / 24);
  const h = Math.round(hours % 24);
  if (d === 0) return `${h}h`;
  if (h === 0) return `${d}d`;
  return `${d}d ${h}h`;
}

// ── Calculate route ────────────────────────────────────────────────────────
ui.calcBtn.addEventListener('click', async () => {
  hideError();
  hideStats();

  let origin, destination;
  try {
    origin      = resolvePoint('origin');
    destination = resolvePoint('destination');
  } catch (err) {
    showError(err.message);
    return;
  }

  showLoading(true);

  try {
    const res = await fetch('/api/route', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        origin:      { lat: origin.lat,      lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Server error');
    }

    renderRoute(data);
    renderStats(data);

  } catch (err) {
    showError(err.message);
  } finally {
    showLoading(false);
  }
});

// ── Cursor lat/lon readout ─────────────────────────────────────────────────
const cursorEl = document.getElementById('cursor-pos');
map.on('mousemove', e => {
  const { lat, lng } = e.latlng;
  cursorEl.textContent =
    `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? 'N' : 'S'}  `
    + `${Math.abs(lng).toFixed(4)}°${lng >= 0 ? 'E' : 'W'}`;
});
map.on('mouseout', () => { cursorEl.textContent = '—'; });

// ── Map rendering ──────────────────────────────────────────────────────────
function renderRoute(data) {
  // Remove previous layers
  if (routeLayer)   { routeLayer.remove();   routeLayer = null; }
  if (markersLayer) { markersLayer.remove(); markersLayer = null; }

  const group = L.featureGroup();

  // Pre-compute cumulative distances along route
  const routeCoords = data.route.geometry.coordinates; // [[lon,lat],...]
  const cumDist     = buildCumDist(routeCoords);
  const totalNmi    = cumDist[cumDist.length - 1];

  // Route polyline with hover tooltip
  const tooltip = L.tooltip({
    sticky:    true,
    className: 'route-tooltip',
    offset:    [14, 0],
  });

  const geojson = L.geoJSON(data.route, {
    style: {
      color:     '#2196f3',
      weight:    5,
      opacity:   0.85,
      dashArray: null,
    },
  });

  geojson.eachLayer(layer => {
    layer.on('mousemove', e => {
      const elapsedNmi  = distAlongRoute(e.latlng, routeCoords, cumDist);
      const remainNmi   = Math.max(0, totalNmi - elapsedNmi);
      const elapsedKm   = elapsedNmi * 1.852;
      const spd         = getSpeed();
      const etaHours    = remainNmi / spd;

      tooltip
        .setLatLng(e.latlng)
        .setContent(`
          <div class="rt-row">
            <span class="rt-dot" style="background:#26a69a"></span>
            <span class="rt-label">From origin</span>
            <span class="rt-val">${elapsedNmi.toFixed(0)} nmi</span>
            <span style="color:var(--text2);font-size:11px">(${(elapsedKm).toFixed(0)} km)</span>
          </div>
          <hr class="rt-divider"/>
          <div class="rt-row">
            <span class="rt-dot" style="background:#ef5350"></span>
            <span class="rt-label">Remaining</span>
            <span class="rt-val">${remainNmi.toFixed(0)} nmi</span>
          </div>
          <div class="rt-row">
            <span class="rt-dot" style="background:transparent"></span>
            <span class="rt-label">ETA</span>
            <span class="rt-val">${formatDuration(etaHours)}</span>
            <span style="color:var(--text2);font-size:11px">at ${spd} kn</span>
          </div>`)
        .openOn(map);
    });

    layer.on('mouseout', () => { map.closeTooltip(tooltip); });
  });

  geojson.addTo(group);

  // Markers
  const originIcon = makeIcon('#26a69a', '●');
  const destIcon   = makeIcon('#ef5350', '■');

  const oLat = data.origin.lat;
  const oLon = data.origin.lon;
  const dLat = data.destination.lat;
  const dLon = data.destination.lon;

  L.marker([oLat, oLon], { icon: originIcon })
    .bindPopup(`<strong>${escHtml(data.origin.name)}</strong><br>${oLat.toFixed(4)}, ${oLon.toFixed(4)}`)
    .addTo(group);

  L.marker([dLat, dLon], { icon: destIcon })
    .bindPopup(`<strong>${escHtml(data.destination.name)}</strong><br>${dLat.toFixed(4)}, ${dLon.toFixed(4)}`)
    .addTo(group);

  group.addTo(map);
  map.fitBounds(group.getBounds(), { padding: [40, 40] });

  markersLayer = group;
}

function makeIcon(color, symbol) {
  return L.divIcon({
    className: '',
    html: `<div style="
      background:${color};
      color:#fff;
      width:26px;height:26px;
      border-radius:50%;
      display:flex;align-items:center;justify-content:center;
      font-size:14px;
      border:2px solid rgba(255,255,255,.8);
      box-shadow:0 2px 6px rgba(0,0,0,.5);
    ">${symbol}</div>`,
    iconSize:   [26, 26],
    iconAnchor: [13, 13],
    popupAnchor:[0, -15],
  });
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Stats ──────────────────────────────────────────────────────────────────
function renderStats(data) {
  lastRouteData = data;
  ui.statsLabel.textContent = `${data.origin.name} → ${data.destination.name}`;
  ui.statKm.textContent  = Number(data.distance_km).toLocaleString()  + ' km';
  ui.statNmi.textContent = Number(data.distance_nmi).toLocaleString() + ' nmi';
  ui.statTime.textContent = formatDuration(data.distance_nmi / getSpeed());
  document.querySelector('#stats-panel .stat-card.wide .stat-label').textContent =
    `Transit at ${getSpeed()} kn`;
  showStats();
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function showError(msg)    { ui.errorBox.textContent = msg; ui.errorBox.classList.remove('hidden'); }
function hideError()       { ui.errorBox.classList.add('hidden'); }
function showStats()       { ui.statsPanel.classList.remove('hidden'); }
function hideStats()       { ui.statsPanel.classList.add('hidden'); }
function showLoading(on)   { ui.loading.classList.toggle('hidden', !on); ui.calcBtn.disabled = on; }


// ── App Mode ──────────────────────────────────────────────────────────────
let appMode           = 'route';   // 'route' | 'congestion'
let congestionData    = null;      // cached /api/congestion/v2 response
let portMarkers       = L.featureGroup().addTo(map);
let selectedPortLocode = null;
let currentSortKey    = 'score';

function switchMode(mode) {
  const routeEl   = $('route-mode');
  const congEl    = $('congestion-mode');
  const liveEl    = $('live-mode');
  const btnRoute  = $('btn-route-mode');
  const btnCong   = $('btn-congestion-mode');
  const btnLive   = $('btn-live-mode');

  // Stop live polling when leaving live mode
  if (livePollingTimer) {
    clearInterval(livePollingTimer);
    livePollingTimer = null;
  }
  liveVesselMarkers.clearLayers();
  livePortMarkers.clearLayers();

  appMode = mode;

  // Reset all
  routeEl.classList.add('hidden');
  congEl.classList.add('hidden');
  liveEl.classList.add('hidden');
  btnRoute.classList.remove('active');
  btnCong.classList.remove('active');
  btnLive.classList.remove('active');

  // Remove map layers
  portMarkers.clearLayers();
  closePortDetail();
  if (routeLayer)   { routeLayer.remove();   routeLayer = null; }
  if (markersLayer) { markersLayer.remove(); markersLayer = null; }

  if (mode === 'route') {
    routeEl.classList.remove('hidden');
    btnRoute.classList.add('active');
  } else if (mode === 'congestion') {
    congEl.classList.remove('hidden');
    btnCong.classList.add('active');
    loadCongestionData();
  } else if (mode === 'live') {
    liveEl.classList.remove('hidden');
    btnLive.classList.add('active');
    liveVesselMarkers.addTo(map);
    livePortMarkers.addTo(map);
    loadLiveData();
    livePollingTimer = setInterval(loadLiveData, LIVE_POLL_INTERVAL);
  }
}

// ── Sort buttons ────────────────────────────────────────────────────────────
document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSortKey = btn.dataset.sort;
    if (congestionData) {
      const sorted = sortPorts(congestionData.ports, currentSortKey);
      renderPortList(sorted);
    }
  });
});

function sortPorts(ports, key) {
  const copy = [...ports];
  if (key === 'score') return copy.sort((a, b) => b.congestion_score - a.congestion_score);
  if (key === 'wait')  return copy.sort((a, b) => b.avg_actual_wait_hrs - a.avg_actual_wait_hrs);
  if (key === 'queue') return copy.sort((a, b) => b.peak_anchored - a.peak_anchored);
  return copy;
}

// ── Congestion data ───────────────────────────────────────────────────────
async function loadCongestionData() {
  const loadingEl = $('congestion-loading');
  loadingEl.classList.remove('hidden');

  try {
    const res = await fetch('/api/congestion/v2');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    congestionData = await res.json();

    const sorted = sortPorts(congestionData.ports, currentSortKey);
    renderPortMarkers(sorted);
    renderPortList(sorted);
  } catch (err) {
    $('port-list').innerHTML = `<div class="cong-error">Failed to load congestion data: ${escHtml(err.message)}</div>`;
  } finally {
    loadingEl.classList.add('hidden');
  }
}

// ── Congestion level helpers ───────────────────────────────────────────────
const LEVEL_COLORS = {
  SEVERE:   '#ef5350',
  HIGH:     '#ff9800',
  MODERATE: '#ffeb3b',
  LOW:      '#4caf50',
};

function levelColor(level) {
  return LEVEL_COLORS[level] || '#4caf50';
}

function levelClass(level) {
  return `severity-${(level || 'low').toLowerCase()}`;
}

// ── Port markers on map ────────────────────────────────────────────────────
function renderPortMarkers(ports) {
  portMarkers.clearLayers();

  ports.forEach(port => {
    if (port.lat == null || port.lon == null) return;

    const score   = Number(port.congestion_score) || 0;
    const anchored = Number(port.peak_anchored) || 0;
    const radius  = Math.max(5, Math.min(35, 5 + anchored * 1.5));
    const color   = levelColor(port.congestion_level);

    const circle = L.circleMarker([port.lat, port.lon], {
      radius,
      color,
      weight:      2,
      opacity:     0.9,
      fillColor:   color,
      fillOpacity: 0.35,
    });

    circle.bindTooltip(`
      <strong>${escHtml(port.name)}</strong><br>
      ${escHtml(port.port_locode)} · ${escHtml(port.country || '')}<br>
      Score: <strong>${score}</strong> · ${escHtml(port.congestion_level || 'LOW')}
    `, { sticky: true, className: 'port-tooltip' });

    circle.on('click', () => openPortDetail(port.port_locode));

    portMarkers.addLayer(circle);
  });
}

// ── Port list in sidebar ───────────────────────────────────────────────────
function renderPortList(ports) {
  const listEl = $('port-list');
  if (!ports || ports.length === 0) {
    listEl.innerHTML = '<div class="cong-empty">No port data available.</div>';
    return;
  }

  listEl.innerHTML = '';
  ports.forEach(port => {
    const score    = Number(port.congestion_score) || 0;
    const anchored = Number(port.peak_anchored) || 0;
    const wait     = Number(port.avg_actual_wait_hrs) || 0;
    const level    = port.congestion_level || 'LOW';
    const color    = levelColor(level);

    const item = document.createElement('div');
    item.className = `port-list-item ${selectedPortLocode === port.port_locode ? 'selected' : ''}`;
    item.dataset.locode = port.port_locode;
    item.innerHTML = `
      <div class="pli-stripe ${levelClass(level)}"></div>
      <div class="pli-body">
        <div class="pli-top">
          <div class="pli-name">${escHtml(port.name)}</div>
          <div class="pli-score" style="background:${color}20;color:${color};border-color:${color}40">${score}</div>
        </div>
        <div class="pli-sub">
          <span class="pli-country">${escHtml(port.country || '')} · ${escHtml(port.port_locode)}</span>
          <span class="pli-stats">⚓ ${anchored} peak · ${wait > 0 ? wait.toFixed(1) + 'h wait' : 'no wait data'}</span>
        </div>
        <div class="pli-bar-track">
          <div class="pli-bar-fill" style="width:${score}%;background:${color}"></div>
        </div>
      </div>
    `;
    item.addEventListener('click', () => openPortDetail(port.port_locode));
    listEl.appendChild(item);
  });
}

// ── Port detail panel ──────────────────────────────────────────────────────
async function openPortDetail(locode) {
  selectedPortLocode = locode;

  // Highlight selected item in list
  document.querySelectorAll('.port-list-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.locode === locode);
  });

  // Show panel immediately with loading state
  const panel = $('port-detail');
  panel.classList.remove('hidden');
  panel.classList.add('open');

  $('pd-port-name').textContent = 'Loading…';
  $('pd-port-meta').textContent = '';
  $('pd-score-val').textContent = '—';
  $('pd-level-badge').textContent = '—';
  $('pd-level-badge').className = 'pd-level-badge';
  $('pd-peak-anchored').textContent = '—';
  $('pd-avg-wait').textContent = '—';
  $('pd-peak-berthed').textContent = '—';
  $('pd-transitioned').textContent = '—';
  $('pd-vessel-list').innerHTML = '';
  $('pd-state-breakdown').innerHTML = '';
  $('pd-timeline-chart').innerHTML = '<div class="timeline-loading">Loading chart…</div>';

  try {
    // Fetch port detail and timeline in parallel
    const [detailRes, timelineRes] = await Promise.all([
      fetch(`/api/congestion/v2/${encodeURIComponent(locode)}`),
      fetch(`/api/congestion/${encodeURIComponent(locode)}/timeline`),
    ]);

    if (!detailRes.ok) throw new Error(`Port detail: HTTP ${detailRes.status}`);
    const detail   = await detailRes.json();
    const timeline = timelineRes.ok ? await timelineRes.json() : { timeline: [] };

    renderPortDetail(detail, timeline);

    // Pan map to port
    if (detail.summary.lat != null && detail.summary.lon != null) {
      map.panTo([detail.summary.lat, detail.summary.lon], { animate: true });
    }
  } catch (err) {
    $('pd-port-name').textContent = 'Error loading port';
    $('pd-port-meta').textContent = err.message;
  }
}

function closePortDetail() {
  selectedPortLocode = null;
  const panel = $('port-detail');
  panel.classList.remove('open');
  // Wait for transition then hide
  setTimeout(() => {
    if (!panel.classList.contains('open')) {
      panel.classList.add('hidden');
    }
  }, 300);
  document.querySelectorAll('.port-list-item').forEach(el => el.classList.remove('selected'));
}

function renderPortDetail(detail, timeline) {
  const s     = detail.summary;
  const level = s.congestion_level || 'LOW';
  const score = Number(s.congestion_score) || 0;
  const color = levelColor(level);

  // Header
  $('pd-port-name').textContent = s.name || s.port_locode;
  $('pd-port-meta').textContent = `${s.port_locode} · ${s.country || ''}`;

  // Score ring
  const circumference = 2 * Math.PI * 32; // r=32
  const fill = $('pd-ring-fill');
  fill.style.stroke = color;
  const offset = circumference * (1 - score / 100);
  fill.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
  fill.setAttribute('stroke-dashoffset', offset);

  $('pd-score-val').textContent = score;
  $('pd-score-val').style.color = color;

  const badge = $('pd-level-badge');
  badge.textContent = level;
  badge.className   = `pd-level-badge level-${level.toLowerCase()}`;

  // Metrics
  $('pd-peak-anchored').textContent  = Number(s.peak_anchored) || 0;
  const wait = Number(s.avg_actual_wait_hrs);
  $('pd-avg-wait').textContent       = wait > 0 ? wait.toFixed(1) + 'h' : 'N/A';
  $('pd-peak-berthed').textContent   = Number(s.peak_berthed) || 0;
  $('pd-transitioned').textContent   = Number(s.vessels_transitioned) || 0;

  // Timeline chart
  drawTimelineChart('pd-timeline-chart', timeline.timeline || []);

  // State breakdown
  renderStateBreakdown(detail.breakdown || [], detail.vessels || []);

  // Vessel list
  renderVesselList(detail.vessels || []);
}

function renderStateBreakdown(breakdown, vessels) {
  const el = $('pd-state-breakdown');

  // Count vessels per state from the last-seen vessel list
  const stateCounts = {};
  vessels.forEach(v => {
    stateCounts[v.state] = (stateCounts[v.state] || 0) + 1;
  });

  const states = ['ANCHORED', 'BERTHED', 'APPROACHING', 'MANEUVERING', 'TRANSITING'];
  const stateColors = {
    ANCHORED:    '#ff9800',
    BERTHED:     '#2196f3',
    APPROACHING: '#9c27b0',
    MANEUVERING: '#00bcd4',
    TRANSITING:  '#4caf50',
  };

  const total = vessels.length || 1;
  el.innerHTML = states.map(st => {
    const count = stateCounts[st] || 0;
    const pct   = Math.round((count / total) * 100);
    const col   = stateColors[st] || '#8b949e';
    return `
      <div class="breakdown-row">
        <div class="breakdown-label">
          <span class="state-badge" style="background:${col}20;color:${col};border-color:${col}40">${st}</span>
          <span class="breakdown-count">${count}</span>
        </div>
        <div class="breakdown-bar-track">
          <div class="breakdown-bar-fill" style="width:${pct}%;background:${col}"></div>
        </div>
      </div>`;
  }).join('');
}

function renderVesselList(vessels) {
  const el = $('pd-vessel-list');
  if (!vessels.length) {
    el.innerHTML = '<div class="vessel-empty">No recent vessel data.</div>';
    return;
  }

  const stateColors = {
    ANCHORED:    '#ff9800',
    BERTHED:     '#2196f3',
    APPROACHING: '#9c27b0',
    MANEUVERING: '#00bcd4',
    TRANSITING:  '#4caf50',
  };

  el.innerHTML = vessels.map(v => {
    const col      = stateColors[v.state] || '#8b949e';
    const lastSeen = v.last_seen ? new Date(v.last_seen).toLocaleDateString() : '—';
    const distStr  = v.dist_nm != null ? `${v.dist_nm} nm` : '';
    return `
      <div class="vessel-row">
        <div class="vessel-row-top">
          <span class="vessel-name">${escHtml(v.name || v.imo || '—')}</span>
          <span class="state-badge" style="background:${col}20;color:${col};border-color:${col}40">${v.state}</span>
        </div>
        <div class="vessel-row-sub">
          <span class="vessel-type">${escHtml(v.vessel_type || '—')}</span>
          ${distStr ? `<span class="vessel-dist">${escHtml(distStr)}</span>` : ''}
          <span class="vessel-time">${lastSeen}</span>
        </div>
      </div>`;
  }).join('');
}

// ── SVG Timeline chart ─────────────────────────────────────────────────────
function drawTimelineChart(containerId, timelineData) {
  const container = $(containerId);

  if (!timelineData || timelineData.length < 2) {
    container.innerHTML = '<div class="timeline-empty">Not enough data for chart.</div>';
    return;
  }

  const W = container.clientWidth || 320;
  const H = 120;
  const PAD = { top: 10, right: 12, bottom: 28, left: 30 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;

  // Parse data
  const points = timelineData.map(d => ({
    t:        new Date(d.time).getTime(),
    anchored: Number(d.anchored) || 0,
    berthed:  Number(d.berthed)  || 0,
  }));

  const minT    = points[0].t;
  const maxT    = points[points.length - 1].t;
  const maxVal  = Math.max(1, ...points.map(p => Math.max(p.anchored, p.berthed)));
  const tRange  = maxT - minT || 1;

  function xp(t)   { return PAD.left + ((t - minT) / tRange) * chartW; }
  function yp(v)   { return PAD.top  + chartH - (v / maxVal) * chartH; }

  function makePath(key) {
    return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xp(p.t).toFixed(1)},${yp(p[key]).toFixed(1)}`).join(' ');
  }

  function makeAreaPath(key) {
    const base = PAD.top + chartH;
    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xp(p.t).toFixed(1)},${yp(p[key]).toFixed(1)}`).join(' ');
    const close = `L${xp(maxT).toFixed(1)},${base} L${xp(minT).toFixed(1)},${base} Z`;
    return line + ' ' + close;
  }

  // Y-axis ticks
  const yTicks = [0, Math.ceil(maxVal / 2), maxVal];
  const yTickLines = yTicks.map(v => {
    const y = yp(v).toFixed(1);
    return `
      <line x1="${PAD.left}" y1="${y}" x2="${PAD.left + chartW}" y2="${y}"
            stroke="#30363d" stroke-width="1" stroke-dasharray="3 3"/>
      <text x="${PAD.left - 4}" y="${y}" text-anchor="end" dominant-baseline="middle"
            fill="#8b949e" font-size="9">${v}</text>`;
  }).join('');

  // X-axis labels (up to 4 ticks)
  const xTickCount = Math.min(4, points.length);
  const xTickIdxs  = Array.from({ length: xTickCount }, (_, i) =>
    Math.round(i * (points.length - 1) / (xTickCount - 1)));
  const xTickLines = xTickIdxs.map(idx => {
    const p   = points[idx];
    const x   = xp(p.t).toFixed(1);
    const lbl = new Date(p.t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return `
      <line x1="${x}" y1="${PAD.top}" x2="${x}" y2="${PAD.top + chartH}"
            stroke="#30363d" stroke-width="1" stroke-dasharray="3 3"/>
      <text x="${x}" y="${H - 6}" text-anchor="middle" fill="#8b949e" font-size="9">${lbl}</text>`;
  }).join('');

  const svgId = `svg-${containerId}`;
  const svg = `
    <svg id="${svgId}" width="100%" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="grad-anchored-${containerId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#ff9800" stop-opacity="0.4"/>
          <stop offset="100%" stop-color="#ff9800" stop-opacity="0.02"/>
        </linearGradient>
        <linearGradient id="grad-berthed-${containerId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#2196f3" stop-opacity="0.3"/>
          <stop offset="100%" stop-color="#2196f3" stop-opacity="0.02"/>
        </linearGradient>
      </defs>

      <!-- Grid -->
      ${yTickLines}
      ${xTickLines}

      <!-- Area fills -->
      <path d="${makeAreaPath('anchored')}" fill="url(#grad-anchored-${containerId})"/>
      <path d="${makeAreaPath('berthed')}"  fill="url(#grad-berthed-${containerId})"/>

      <!-- Lines -->
      <path d="${makePath('anchored')}" fill="none" stroke="#ff9800" stroke-width="2" stroke-linejoin="round"/>
      <path d="${makePath('berthed')}"  fill="none" stroke="#2196f3" stroke-width="1.5" stroke-linejoin="round" stroke-dasharray="5 3"/>

      <!-- Legend -->
      <rect x="${PAD.left}" y="${H - 14}" width="8" height="3" rx="1.5" fill="#ff9800"/>
      <text x="${PAD.left + 11}" y="${H - 10}" fill="#8b949e" font-size="9">Anchored</text>
      <rect x="${PAD.left + 62}" y="${H - 14}" width="8" height="3" rx="1.5" fill="#2196f3"/>
      <text x="${PAD.left + 73}" y="${H - 10}" fill="#8b949e" font-size="9">Berthed</text>
    </svg>`;

  container.innerHTML = svg;
}

// ── Live Congestion Mode ─────────────────────────────────────────────────────
let liveData          = null;
let livePollingTimer  = null;
let liveVesselMarkers = L.featureGroup();
let livePortMarkers   = L.featureGroup();

const LIVE_POLL_INTERVAL = 45000; // 45 seconds

// ── Load live congestion data ────────────────────────────────────────────────
async function loadLiveData() {
  try {
    const res = await fetch('/api/congestion/live');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    liveData = await res.json();

    updateLiveStatus(liveData);
    renderLivePortList(liveData.ports);
    renderLiveMapMarkers(liveData.ports);
  } catch (err) {
    $('live-port-list').innerHTML = `<div class="cong-error">Failed to load live data: ${escHtml(err.message)}</div>`;
  }
}

function updateLiveStatus(data) {
  const dot  = $('live-status-dot');
  const text = $('live-status-text');
  const timeEl = $('live-last-update');

  if (data.stream_status === 'connected') {
    dot.className = 'live-dot connected';
    text.textContent = 'Stream connected';
  } else {
    dot.className = 'live-dot disconnected';
    text.textContent = 'Connecting...';
  }

  if (data.last_message_at) {
    const ago = Math.round((Date.now() - new Date(data.last_message_at).getTime()) / 1000);
    timeEl.textContent = ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`;
  } else {
    timeEl.textContent = '';
  }
}

function renderLivePortList(ports) {
  const listEl = $('live-port-list');
  if (!ports || ports.length === 0) {
    listEl.innerHTML = '<div class="cong-empty">No live data yet. Waiting for AIS stream...</div>';
    return;
  }

  const sorted = [...ports].sort((a, b) => b.congestion_score - a.congestion_score);

  listEl.innerHTML = '';
  sorted.forEach(port => {
    const score     = Number(port.congestion_score) || 0;
    const anchored  = Number(port.anchored_count) || 0;
    const berthed   = Number(port.berthed_count) || 0;
    const wait      = Number(port.avg_wait_hours) || 0;
    const level     = port.severity || 'LOW';
    const color     = levelColor(level);
    const total     = Number(port.total_vessels) || 0;

    const item = document.createElement('div');
    item.className = 'port-list-item';
    item.dataset.locode = port.locode;
    item.innerHTML = `
      <div class="pli-stripe ${levelClass(level)}"></div>
      <div class="pli-body">
        <div class="pli-top">
          <div class="pli-name">${escHtml(port.name)}</div>
          <div class="pli-score" style="background:${color}20;color:${color};border-color:${color}40">${score}</div>
        </div>
        <div class="pli-sub">
          <span class="pli-country">${escHtml(port.country || '')} · ${escHtml(port.locode)}</span>
          <span class="pli-stats">${total} vessels · ${anchored} anchored</span>
        </div>
        <div class="pli-sub">
          <span class="pli-stats">${berthed} berthed · ${wait > 0 ? wait.toFixed(1) + 'h avg wait' : 'no wait'}</span>
        </div>
        <div class="pli-bar-track">
          <div class="pli-bar-fill" style="width:${score}%;background:${color}"></div>
        </div>
      </div>
    `;
    item.addEventListener('click', () => openLivePortDetail(port.locode));
    listEl.appendChild(item);
  });
}

function renderLiveMapMarkers(ports) {
  livePortMarkers.clearLayers();
  liveVesselMarkers.clearLayers();

  ports.forEach(port => {
    if (port.lat == null || port.lon == null) return;

    const score  = Number(port.congestion_score) || 0;
    const color  = levelColor(port.severity || 'LOW');
    const radius = Math.max(8, Math.min(35, 8 + (port.anchored_count || 0) * 2));

    const circle = L.circleMarker([port.lat, port.lon], {
      radius,
      color,
      weight: 2,
      opacity: 0.9,
      fillColor: color,
      fillOpacity: 0.35,
    });

    circle.bindTooltip(`
      <strong>${escHtml(port.name)}</strong><br>
      ${escHtml(port.locode)} · ${escHtml(port.country || '')}<br>
      Score: <strong>${score}</strong> · ${escHtml(port.severity || 'LOW')}<br>
      Vessels: ${port.total_vessels || 0} (${port.anchored_count || 0} anchored)
    `, { sticky: true, className: 'port-tooltip' });

    circle.on('click', () => openLivePortDetail(port.locode));
    livePortMarkers.addLayer(circle);
  });
}

async function openLivePortDetail(locode) {
  selectedPortLocode = locode;

  document.querySelectorAll('.port-list-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.locode === locode);
  });

  const panel = $('port-detail');
  panel.classList.remove('hidden');
  panel.classList.add('open');

  $('pd-port-name').textContent = 'Loading…';
  $('pd-port-meta').textContent = '';
  $('pd-score-val').textContent = '—';
  $('pd-level-badge').textContent = '—';
  $('pd-level-badge').className = 'pd-level-badge';
  $('pd-peak-anchored').textContent = '—';
  $('pd-avg-wait').textContent = '—';
  $('pd-peak-berthed').textContent = '—';
  $('pd-transitioned').textContent = '—';
  $('pd-vessel-list').innerHTML = '';
  $('pd-state-breakdown').innerHTML = '';
  $('pd-timeline-chart').innerHTML = '<div class="timeline-loading">Loading live data…</div>';

  try {
    // Load detail immediately (fast)
    const detailRes = await fetch(`/api/congestion/live/${encodeURIComponent(locode)}`);
    if (!detailRes.ok) throw new Error(`HTTP ${detailRes.status}`);
    const detail = await detailRes.json();

    renderLivePortDetail(detail);
    renderLiveVesselDots(detail.vessels || []);

    if (detail.lat != null && detail.lon != null) {
      map.setView([detail.lat, detail.lon], 11, { animate: true });
    }

    // Load cargo and turnaround in background (slow — fetches vessel specs/history)
    $('pd-cargo').innerHTML = '<div class="vessel-empty">Loading cargo data...</div>';
    fetch(`/api/congestion/live/${encodeURIComponent(locode)}/cargo`)
      .then(r => r.ok ? r.json() : null)
      .then(cargo => renderCargoStats(cargo))
      .catch(() => {
        $('pd-cargo').innerHTML = '<div class="vessel-empty">Could not load cargo data.</div>';
      });

    $('pd-turnaround').innerHTML = '<div class="vessel-empty">Loading turnaround data...</div>';
    fetch(`/api/congestion/live/${encodeURIComponent(locode)}/turnaround`)
      .then(r => r.ok ? r.json() : null)
      .then(turnaround => renderTurnaroundStats(turnaround))
      .catch(() => {
        $('pd-turnaround').innerHTML = '<div class="vessel-empty">Could not load turnaround data.</div>';
      });
  } catch (err) {
    $('pd-port-name').textContent = 'Error';
    $('pd-port-meta').textContent = err.message;
  }
}

function renderLivePortDetail(detail) {
  // Metrics are at top level, not nested under detail.metrics
  const level = detail.severity || 'LOW';
  const score = Number(detail.congestion_score) || 0;
  const color = levelColor(level);

  $('pd-port-name').textContent = detail.name || detail.locode;
  $('pd-port-meta').textContent = `${detail.locode} · ${detail.country || ''} · LIVE`;

  const circumference = 2 * Math.PI * 32;
  const fill = $('pd-ring-fill');
  fill.style.stroke = color;
  fill.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
  fill.setAttribute('stroke-dashoffset', circumference * (1 - score / 100));

  $('pd-score-val').textContent = score;
  $('pd-score-val').style.color = color;

  const badge = $('pd-level-badge');
  badge.textContent = level;
  badge.className = `pd-level-badge level-${level.toLowerCase()}`;

  $('pd-peak-anchored').textContent = detail.anchored_count || 0;
  $('pd-avg-wait').textContent = detail.avg_wait_hours > 0 ? detail.avg_wait_hours.toFixed(1) + 'h' : 'N/A';
  $('pd-peak-berthed').textContent = detail.berthed_count || 0;
  $('pd-transitioned').textContent = detail.total_vessels || 0;

  const metricLabels = document.querySelectorAll('.pd-metric-label');
  if (metricLabels[3]) metricLabels[3].textContent = 'Total Vessels';
  if (metricLabels[0]) metricLabels[0].textContent = 'Anchored';
  if (metricLabels[2]) metricLabels[2].textContent = 'Berthed';

  $('pd-timeline-chart').innerHTML = '<div class="timeline-empty">Live mode — no historical timeline</div>';

  const vessels = detail.vessels || [];
  renderStateBreakdown([], vessels);
  renderLiveVesselList(vessels);
}

function renderLiveVesselList(vessels) {
  const el = $('pd-vessel-list');
  if (!vessels.length) {
    el.innerHTML = '<div class="vessel-empty">No vessels in port zone.</div>';
    return;
  }

  const stateColors = {
    ANCHORED:    '#ff9800',
    BERTHED:     '#2196f3',
    APPROACHING: '#9c27b0',
    TRANSITING:  '#4caf50',
  };

  el.innerHTML = vessels.map(v => {
    const col     = stateColors[v.state] || '#8b949e';
    const distStr = v.dist_to_port_nm != null ? `${v.dist_to_port_nm} nm` : '';
    const waitStr = v.wait_hours > 0 ? `${v.wait_hours}h wait` : '';
    return `
      <div class="vessel-row">
        <div class="vessel-row-top">
          <span class="vessel-name">${escHtml(v.name || String(v.mmsi))}</span>
          <span class="state-badge" style="background:${col}20;color:${col};border-color:${col}40">${v.state}</span>
        </div>
        <div class="vessel-row-sub">
          <span class="vessel-type">MMSI: ${v.mmsi}</span>
          ${distStr ? `<span class="vessel-dist">${escHtml(distStr)}</span>` : ''}
          ${waitStr ? `<span class="vessel-time">${escHtml(waitStr)}</span>` : ''}
        </div>
      </div>`;
  }).join('');
}

function renderLiveVesselDots(vessels) {
  liveVesselMarkers.clearLayers();

  const stateColors = {
    ANCHORED:    '#ff9800',
    BERTHED:     '#2196f3',
    APPROACHING: '#9c27b0',
    TRANSITING:  '#4caf50',
  };

  vessels.forEach(v => {
    if (v.lat == null || v.lon == null) return;

    const col  = stateColors[v.state] || '#8b949e';
    const size = v.state === 'ANCHORED' ? 7 : v.state === 'BERTHED' ? 6 : 5;

    const marker = L.circleMarker([v.lat, v.lon], {
      radius:      size,
      color:       '#fff',
      weight:      1.5,
      fillColor:   col,
      fillOpacity: 0.85,
    });

    const waitStr = v.wait_hours > 0 ? `<br>Wait: ${v.wait_hours}h` : '';
    marker.bindTooltip(`
      <strong>${escHtml(v.name || String(v.mmsi))}</strong><br>
      ${v.state} · ${v.speed || 0} kts<br>
      ${v.dist_to_port_nm || '?'} nm from port${waitStr}
    `, { sticky: true, className: 'port-tooltip' });

    liveVesselMarkers.addLayer(marker);
  });
}

// ── Turnaround stats by vessel type ──────────────────────────────────────────
function renderTurnaroundStats(data) {
  const el = $('pd-turnaround');
  if (!data || !data.by_vessel_type || data.by_vessel_type.length === 0) {
    el.innerHTML = '<div class="vessel-empty">No turnaround data yet. Data accumulates as vessels are tracked.</div>';
    return;
  }

  const o = data.overall;
  const fmtH = h => h < 1 ? `${Math.round(h * 60)}m` : h < 24 ? `${h.toFixed(1)}h` : `${(h / 24).toFixed(1)}d`;

  let html = `
    <div class="turnaround-overall">
      <div class="turnaround-stat">
        <div class="turnaround-stat-val">${fmtH(o.avg_turnaround_hours)}</div>
        <div class="turnaround-stat-label">Avg Turnaround</div>
      </div>
      <div class="turnaround-stat">
        <div class="turnaround-stat-val">${fmtH(o.avg_wait_hours)}</div>
        <div class="turnaround-stat-label">Avg Wait</div>
      </div>
      <div class="turnaround-stat">
        <div class="turnaround-stat-val">${fmtH(o.avg_berth_hours)}</div>
        <div class="turnaround-stat-label">Avg Berth</div>
      </div>
    </div>
    <table class="turnaround-table">
      <thead>
        <tr>
          <th>Vessel Type</th>
          <th style="text-align:right">#</th>
          <th style="text-align:right">Avg Turn</th>
          <th style="text-align:right">Avg Wait</th>
          <th style="text-align:right">Avg Berth</th>
        </tr>
      </thead>
      <tbody>`;

  data.by_vessel_type.forEach(t => {
    html += `
        <tr>
          <td class="tt-type" title="${escHtml(t.vessel_type)}">${escHtml(t.vessel_type)}</td>
          <td class="tt-num">${t.count}</td>
          <td class="tt-num tt-highlight">${fmtH(t.avg_total_hours)}</td>
          <td class="tt-num">${fmtH(t.avg_anchor_hours)}</td>
          <td class="tt-num">${fmtH(t.avg_berth_hours)}</td>
        </tr>`;
  });

  html += '</tbody></table>';
  el.innerHTML = html;
}

// ── Cargo estimation ─────────────────────────────────────────────────────────
function renderCargoStats(data) {
  const el = $('pd-cargo');
  if (!data || !data.vessels || data.vessels.length === 0) {
    el.innerHTML = '<div class="vessel-empty">No cargo data available.</div>';
    return;
  }

  const s = data.summary;
  const fmtT = t => {
    if (t >= 1000000) return (t / 1000000).toFixed(1) + 'M';
    if (t >= 1000) return Math.round(t / 1000) + 'K';
    return String(t);
  };

  let html = `
    <div class="cargo-summary">
      <div class="cargo-stat">
        <div class="cargo-stat-val">${fmtT(s.total_est_cargo_tonnes)} t</div>
        <div class="cargo-stat-label">Est. Cargo</div>
      </div>
      <div class="cargo-stat">
        <div class="cargo-stat-val">${fmtT(s.total_dwt)} DWT</div>
        <div class="cargo-stat-label">Total Capacity</div>
      </div>
      <div class="cargo-stat">
        <div class="cargo-stat-val">${s.avg_load_pct != null ? s.avg_load_pct + '%' : 'N/A'}</div>
        <div class="cargo-stat-label">Avg Load</div>
      </div>
      <div class="cargo-stat">
        <div class="cargo-stat-val">${data.vessels_analyzed}</div>
        <div class="cargo-stat-label">Vessels</div>
      </div>`;

  if (s.total_teu_capacity > 0) {
    html += `
      <div class="cargo-stat wide">
        <div class="cargo-stat-val">${fmtT(s.total_teu_capacity)} TEU</div>
        <div class="cargo-stat-label">Container Capacity</div>
      </div>`;
  }

  html += '</div>';

  // Cargo breakdown by type with bars
  if (data.by_type && data.by_type.length > 0) {
    const maxCargo = Math.max(...data.by_type.map(t => t.total_est_cargo || 0), 1);
    html += '<div class="cargo-bar">';
    data.by_type.forEach(t => {
      const pct = Math.round((t.total_est_cargo / maxCargo) * 100);
      const colors = {
        'Bulk Carrier': '#ff9800', 'General Cargo': '#2196f3',
        'Tanker': '#f44336', 'Oil/Chemical Tanker': '#e91e63',
        'Container Ship': '#4caf50', 'Cargo': '#ff9800',
      };
      const col = colors[t.vessel_type] || '#9c27b0';
      html += `
        <div class="cargo-bar-row">
          <span class="cargo-bar-label" title="${escHtml(t.vessel_type)}">${escHtml(t.vessel_type)}</span>
          <div class="cargo-bar-track">
            <div class="cargo-bar-fill" style="width:${pct}%;background:${col}"></div>
          </div>
          <span class="cargo-bar-val">${fmtT(t.total_est_cargo)} t (${t.count})</span>
        </div>`;
    });
    html += '</div>';
  }

  el.innerHTML = html;
}
