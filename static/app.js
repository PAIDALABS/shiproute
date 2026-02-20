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

let routeLayer   = null;
let markersLayer = null;
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
