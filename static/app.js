'use strict';

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map('map', { zoomControl: true }).setView([20, 0], 2);

L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
  subdomains: 'abcd',
}).addTo(map);

// ── State ──────────────────────────────────────────────────────────────────
let routeLayer    = null;
let markersLayer  = null;
let lastRouteData = null;

// Waypoints for multi-leg voyage
let waypoints = [
  { point: null },  // Port 1
  { point: null },  // Port 2
];

// Finder state
let finderPort   = null;
let finderMarkers = L.featureGroup();
let finderTrackLayer = null;

// ── Fleet Dashboard state ─────────────────────────────────────────────────
let fleetData          = null;
let fleetAllVessels    = [];
let fleetFilteredVessels = [];
let fleetRefreshTimer  = null;
let fleetVesselMarkers = L.featureGroup();
let fleetPortMarkers   = L.featureGroup();
let fleetRouteLayer    = null;
let fleetVoyageOrigin  = null;

// Keep CC aliases so switchMode cleanup code doesn't break
const ccVesselMarkers  = L.featureGroup();
const ccWeatherMarkers = L.featureGroup();
const ccPortMarkers    = L.featureGroup();
let ccRouteLayer = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const ui = {
  calcBtn:      $('calc-btn'),
  errorBox:     $('error-box'),
  loading:      $('loading'),
  speedInput:   $('speed-input'),
  speedPreset:  $('speed-preset'),
};

// ── Speed selector ─────────────────────────────────────────────────────────
function getSpeed() {
  const v = parseFloat(ui.speedInput.value);
  return (isNaN(v) || v <= 0) ? 14 : v;
}

ui.speedPreset.addEventListener('change', () => {
  if (ui.speedPreset.value) {
    ui.speedInput.value = ui.speedPreset.value;
    ui.speedPreset.value = '';
  }
});

// Fuel preset
const fuelPreset = $('fuel-preset');
if (fuelPreset) {
  fuelPreset.addEventListener('change', () => {
    if (fuelPreset.value) {
      $('fuel-consumption').value = fuelPreset.value;
      fuelPreset.value = '';
    }
  });
}

// ── Port Autocomplete Factory ─────────────────────────────────────────────
function createPortAutocomplete(input, dropdown, onSelect, opts = {}) {
  let debounceTimer = null;
  const debounceMs = opts.debounce || 300;
  const onClear = opts.onClear || null;

  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    if (onClear) onClear();
    const q = input.value.trim();
    if (q.length < 1) { dropdown.innerHTML = ''; dropdown.classList.remove('open'); return; }
    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/ports/search?q=${encodeURIComponent(q)}&limit=8`);
        const ports = await res.json();
        if (!ports.length) { dropdown.innerHTML = ''; dropdown.classList.remove('open'); return; }
        dropdown.innerHTML = '';
        ports.forEach(port => {
          const div = document.createElement('div');
          div.className = 'dropdown-item';
          div.innerHTML = `
            <span class="di-locode">${escHtml(port.locode)}</span>
            <div class="di-info">
              <div class="di-name">${escHtml(port.name)}</div>
              <div class="di-country">${escHtml(port.country)}</div>
            </div>`;
          div.addEventListener('mousedown', e => { e.preventDefault(); onSelect(port); dropdown.innerHTML = ''; dropdown.classList.remove('open'); });
          dropdown.appendChild(div);
        });
        dropdown.classList.add('open');
      } catch { dropdown.innerHTML = ''; dropdown.classList.remove('open'); }
    }, debounceMs);
  });

  // Keyboard navigation
  input.addEventListener('keydown', e => {
    const items = dropdown.querySelectorAll('.dropdown-item');
    if (!items.length) return;
    const active = dropdown.querySelector('.dropdown-item.active');
    let idx = active ? [...items].indexOf(active) : -1;
    if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, items.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); }
    else if (e.key === 'Enter' && active) { e.preventDefault(); active.dispatchEvent(new Event('mousedown')); return; }
    else if (e.key === 'Escape') { dropdown.innerHTML = ''; dropdown.classList.remove('open'); return; }
    else return;
    items.forEach(i => i.classList.remove('active'));
    if (items[idx]) { items[idx].classList.add('active'); items[idx].scrollIntoView({ block: 'nearest' }); }
  });

  // Close on blur
  input.addEventListener('blur', () => {
    setTimeout(() => { dropdown.innerHTML = ''; dropdown.classList.remove('open'); }, 200);
  });
}

// ── Waypoint Autocomplete (Voyage Mode) ───────────────────────────────────
function setupWaypointAutocomplete(input, dropdown, wpIndex) {
  createPortAutocomplete(input, dropdown, (port) => {
    waypoints[wpIndex] = { point: { name: port.name, lat: port.lat, lon: port.lon, locode: port.locode } };
    input.value = `${port.name} (${port.locode})`;
  }, {
    onClear: () => { waypoints[wpIndex] = { point: null }; },
  });
}

// Initialize waypoint autocompletes for the initial 2 waypoints
function initWaypointAutocompletes() {
  document.querySelectorAll('#waypoints-container .wp-search').forEach(input => {
    const idx = parseInt(input.dataset.idx, 10);
    const dropdown = input.parentElement.querySelector('.wp-dropdown');
    setupWaypointAutocomplete(input, dropdown, idx);
  });
}
initWaypointAutocompletes();

// ── Add Waypoint ──────────────────────────────────────────────────────────
$('add-wp-btn').addEventListener('click', () => {
  const idx = waypoints.length;
  waypoints.push({ point: null });

  const container = $('waypoints-container');
  const block = document.createElement('div');
  block.className = 'waypoint-block';
  block.dataset.wpIdx = idx;

  const dotClass = idx === 0 ? 'dot-origin' : 'dot-dest';
  block.innerHTML = `
    <div class="waypoint-label">
      <span class="dot ${dotClass}"></span> Port ${idx + 1}
      <button class="wp-remove-btn" data-remove-idx="${idx}" title="Remove stop">&times;</button>
    </div>
    <div class="autocomplete-wrap">
      <input class="inp wp-search" type="text" placeholder="Search port..." autocomplete="off" data-idx="${idx}" />
      <div class="dropdown wp-dropdown"></div>
    </div>`;
  container.appendChild(block);

  const newInput = block.querySelector('.wp-search');
  const newDropdown = block.querySelector('.wp-dropdown');
  setupWaypointAutocomplete(newInput, newDropdown, idx);

  block.querySelector('.wp-remove-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    removeWaypoint(idx);
  });
});

function removeWaypoint(idx) {
  if (waypoints.length <= 2) return; // need at least 2
  waypoints.splice(idx, 1);
  rebuildWaypointUI();
}

function rebuildWaypointUI() {
  const container = $('waypoints-container');
  container.innerHTML = '';

  waypoints.forEach((wp, i) => {
    const block = document.createElement('div');
    block.className = 'waypoint-block';
    block.dataset.wpIdx = i;

    const dotClass = i === 0 ? 'dot-origin' : 'dot-dest';
    const removable = waypoints.length > 2;
    block.innerHTML = `
      <div class="waypoint-label">
        <span class="dot ${dotClass}"></span> Port ${i + 1}
        ${removable ? `<button class="wp-remove-btn" data-remove-idx="${i}" title="Remove stop">&times;</button>` : ''}
      </div>
      <div class="autocomplete-wrap">
        <input class="inp wp-search" type="text" placeholder="Search port..." autocomplete="off" data-idx="${i}" />
        <div class="dropdown wp-dropdown"></div>
      </div>`;
    container.appendChild(block);

    const input = block.querySelector('.wp-search');
    const dropdown = block.querySelector('.wp-dropdown');

    if (wp.point) {
      input.value = `${wp.point.name} (${wp.point.locode || ''})`;
    }
    setupWaypointAutocomplete(input, dropdown, i);

    if (removable) {
      block.querySelector('.wp-remove-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        removeWaypoint(i);
      });
    }
  });
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

function buildCumDist(coords) {
  const cum = [0];
  for (let i = 1; i < coords.length; i++) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    cum.push(cum[i - 1] + haversineNmi(lat1, lon1, lat2, lon2));
  }
  return cum;
}

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

// ── Calculate Voyage (multi-leg) ──────────────────────────────────────────
ui.calcBtn.addEventListener('click', async () => {
  hideError();
  $('voyage-results').classList.add('hidden');

  // Validate waypoints
  const resolved = [];
  for (let i = 0; i < waypoints.length; i++) {
    const wp = waypoints[i];
    if (!wp.point) {
      showError(`Select a port for Port ${i + 1}`);
      return;
    }
    resolved.push({ lat: wp.point.lat, lon: wp.point.lon, name: wp.point.name });
  }

  showLoading(true);

  try {
    const speed = getSpeed();
    const res = await fetch('/api/route/multi', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ waypoints: resolved, speed_knots: speed }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Server error');

    lastRouteData = data;
    renderVoyageRoute(data);
    renderVoyageResults(data);
  } catch (err) {
    showError(err.message);
  } finally {
    showLoading(false);
  }
});

// ── Render multi-leg route on map ─────────────────────────────────────────
function renderVoyageRoute(data) {
  if (routeLayer)   { routeLayer.remove();   routeLayer = null; }
  if (markersLayer) { markersLayer.remove(); markersLayer = null; }

  const group = L.featureGroup();
  const legColors = ['#2196f3', '#4caf50', '#ff9800', '#9c27b0', '#00bcd4', '#e91e63', '#ff5722'];

  data.legs.forEach((leg, i) => {
    const color = legColors[i % legColors.length];

    // Route polyline with hover
    const routeCoords = leg.route.geometry.coordinates;
    const cumDist = buildCumDist(routeCoords);
    const totalNmi = cumDist[cumDist.length - 1];

    const tooltip = L.tooltip({ sticky: true, className: 'route-tooltip', offset: [14, 0] });

    const geojson = L.geoJSON(leg.route, {
      style: { color, weight: 4, opacity: 0.85 },
    });

    geojson.eachLayer(layer => {
      layer.on('mousemove', e => {
        const elapsedNmi = distAlongRoute(e.latlng, routeCoords, cumDist);
        const remainNmi  = Math.max(0, totalNmi - elapsedNmi);
        const spd = getSpeed();

        tooltip
          .setLatLng(e.latlng)
          .setContent(`
            <div class="rt-row"><span class="rt-dot" style="background:${color}"></span>
            <span class="rt-label">Leg ${leg.leg}</span>
            <span class="rt-val">${escHtml(leg.origin.name)} &rarr; ${escHtml(leg.destination.name)}</span></div>
            <hr class="rt-divider"/>
            <div class="rt-row"><span class="rt-dot" style="background:#26a69a"></span>
            <span class="rt-label">From start</span><span class="rt-val">${elapsedNmi.toFixed(0)} nmi</span></div>
            <div class="rt-row"><span class="rt-dot" style="background:#ef5350"></span>
            <span class="rt-label">Remaining</span><span class="rt-val">${remainNmi.toFixed(0)} nmi</span></div>
            <div class="rt-row"><span class="rt-dot" style="background:transparent"></span>
            <span class="rt-label">ETA</span><span class="rt-val">${formatDuration(remainNmi / spd)}</span>
            <span style="color:var(--text2);font-size:11px">at ${spd} kn</span></div>`)
          .openOn(map);
      });
      layer.on('mouseout', () => { map.closeTooltip(tooltip); });
    });

    geojson.addTo(group);

    // Origin marker
    const oIcon = makeIcon(i === 0 ? '#26a69a' : color, i === 0 ? '1' : String(i + 1));
    L.marker([leg.origin.lat, leg.origin.lon], { icon: oIcon })
      .bindPopup(`<strong>${escHtml(leg.origin.name)}</strong>`)
      .addTo(group);

    // Destination marker (only for last leg)
    if (i === data.legs.length - 1) {
      const dIcon = makeIcon('#ef5350', String(i + 2));
      L.marker([leg.destination.lat, leg.destination.lon], { icon: dIcon })
        .bindPopup(`<strong>${escHtml(leg.destination.name)}</strong>`)
        .addTo(group);
    }
  });

  group.addTo(map);
  map.fitBounds(group.getBounds(), { padding: [40, 40] });
  markersLayer = group;
}

// ── Render voyage results in sidebar ──────────────────────────────────────
function renderVoyageResults(data) {
  const resultsDiv = $('voyage-results');
  resultsDiv.classList.remove('hidden');
  const onboard = $('voyage-onboard');
  if (onboard) onboard.classList.add('hidden');

  const t = data.totals;
  const speed = data.speed_knots;

  // Fuel calculation
  const fuelConsumption = parseFloat($('fuel-consumption').value) || 22;
  const fuelPrice       = parseFloat($('fuel-price').value) || 600;
  const totalDays       = t.duration_hours / 24;
  const totalFuelMT     = totalDays * fuelConsumption;
  const totalFuelCost   = totalFuelMT * fuelPrice;

  // Summary
  $('voyage-summary').innerHTML = `
    <div class="stats-route-label">${data.legs.map(l => escHtml(l.origin.name)).concat([escHtml(data.legs[data.legs.length - 1].destination.name)]).join(' &rarr; ')}</div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">${Number(t.distance_nmi).toLocaleString()} nmi</div>
        <div class="stat-label">Total Distance</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatDuration(t.duration_hours)}</div>
        <div class="stat-label">Transit at ${speed} kn</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${Number(t.distance_km).toLocaleString()} km</div>
        <div class="stat-label">Kilometers</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${t.legs} leg${t.legs > 1 ? 's' : ''}</div>
        <div class="stat-label">Route Legs</div>
      </div>
    </div>`;

  // Per-leg breakdown
  $('voyage-legs').innerHTML = data.legs.map(leg => `
    <div class="voyage-leg">
      <div class="voyage-leg-header">
        <span class="voyage-leg-num">Leg ${leg.leg}</span>
        <span class="voyage-leg-route">${escHtml(leg.origin.name)} &rarr; ${escHtml(leg.destination.name)}</span>
      </div>
      <div class="voyage-leg-stats">
        <span>${Number(leg.distance_nmi).toLocaleString()} nmi</span>
        <span>${formatDuration(leg.duration_hours)}</span>
        <span>${Number(leg.distance_km).toLocaleString()} km</span>
      </div>
    </div>`).join('');

  // Fuel summary
  $('voyage-fuel').innerHTML = `
    <div class="voyage-fuel-card">
      <div class="voyage-fuel-title">Fuel Estimate</div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">${totalFuelMT.toFixed(1)} MT</div>
          <div class="stat-label">Total Fuel</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">$${totalFuelCost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
          <div class="stat-label">Fuel Cost</div>
        </div>
      </div>
      <div class="voyage-fuel-detail">${fuelConsumption} MT/day &times; ${totalDays.toFixed(1)} days &times; $${fuelPrice}/MT</div>
    </div>`;

  // Check congestion at final destination
  const lastLeg = data.legs[data.legs.length - 1];
  const destPort = waypoints[waypoints.length - 1];
  const destLocode = destPort?.point?.locode;
  const congestionEl = $('voyage-congestion');
  if (congestionEl) congestionEl.innerHTML = '';
  if (destLocode) {
    fetch(`/api/port-watch/${destLocode}/arrival-advisory`)
      .then(r => r.ok ? r.json() : null)
      .then(advisory => {
        if (advisory && advisory.monitored) {
          const el = $('voyage-congestion');
          if (!el) return;
          const riskCol = advisory.severity === 'SEVERE' ? '#ef5350' : advisory.severity === 'HIGH' ? '#ff9800' : advisory.severity === 'MODERATE' ? '#ffeb3b' : '#4caf50';
          el.innerHTML = `
            <div style="margin-top:10px;padding:10px;background:var(--bg3);border:1px solid var(--border);border-radius:8px;border-left:3px solid ${riskCol}">
              <div style="font-size:11px;font-weight:600;color:${riskCol};text-transform:uppercase;margin-bottom:4px">${severityIcon(advisory.severity)} Port Congestion at ${escHtml(advisory.name)}</div>
              <div style="font-size:13px;font-weight:700;color:var(--text)">Expected wait: ${advisory.estimated_wait_hours > 0 ? advisory.estimated_wait_days + ' days' : 'Minimal'}</div>
              <div style="font-size:11px;color:var(--text2);margin-top:4px">${escHtml(advisory.recommendation)}</div>
              <div style="font-size:10px;color:var(--text2);margin-top:2px">Queue: ${advisory.current_queue} vessels · Score: ${advisory.congestion_score}</div>
            </div>`;
        }
      })
      .catch(() => {});
  }
}

// ── Cursor lat/lon readout ─────────────────────────────────────────────────
const cursorEl = document.getElementById('cursor-pos');
map.on('mousemove', e => {
  const { lat, lng } = e.latlng;
  cursorEl.textContent =
    `${Math.abs(lat).toFixed(4)}\u00b0${lat >= 0 ? 'N' : 'S'}  `
    + `${Math.abs(lng).toFixed(4)}\u00b0${lng >= 0 ? 'E' : 'W'}`;
});
map.on('mouseout', () => { cursorEl.textContent = '\u2014'; });

// ── Map rendering helpers ─────────────────────────────────────────────────
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
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function showError(msg)    { ui.errorBox.textContent = msg; ui.errorBox.classList.remove('hidden'); }
function hideError()       { ui.errorBox.classList.add('hidden'); }
function showLoading(on)   { ui.loading.classList.toggle('hidden', !on); ui.calcBtn.disabled = on; }


// ── App Mode ──────────────────────────────────────────────────────────────
let appMode           = 'portwatch';
let congestionData    = null;
let portMarkers       = L.featureGroup().addTo(map);
let selectedPortLocode = null;
let currentSortKey    = 'score';

function switchMode(mode) {
  const voyageEl   = $('voyage-mode');
  const portwatchEl = $('portwatch-mode');
  const finderEl   = $('finder-mode');
  const weatherEl  = $('weather-mode');
  const marketEl   = $('market-mode');

  const btnVoyage   = $('btn-voyage-mode');
  const btnPortwatch = $('btn-portwatch-mode');
  const btnFinder   = $('btn-finder-mode');
  const btnWeather  = $('btn-weather-mode');
  const btnMarket   = $('btn-market-mode');

  // Stop live polling
  if (livePollingTimer) {
    clearInterval(livePollingTimer);
    livePollingTimer = null;
  }
  liveVesselMarkers.clearLayers();
  livePortMarkers.clearLayers();

  // Stop fleet refresh
  if (fleetRefreshTimer) {
    clearInterval(fleetRefreshTimer);
    fleetRefreshTimer = null;
  }
  fleetVesselMarkers.clearLayers();
  fleetPortMarkers.clearLayers();

  appMode = mode;

  // Hide all mode panels
  voyageEl.classList.add('hidden');
  portwatchEl.classList.add('hidden');
  finderEl.classList.add('hidden');
  weatherEl.classList.add('hidden');
  marketEl.classList.add('hidden');
  $('command-mode')?.classList.add('hidden');

  // Deactivate all buttons
  btnVoyage.classList.remove('active');
  btnPortwatch.classList.remove('active');
  btnFinder.classList.remove('active');
  btnWeather.classList.remove('active');
  btnMarket.classList.remove('active');
  $('btn-command-mode')?.classList.remove('active');

  // Clear map overlays
  portMarkers.clearLayers();
  finderMarkers.clearLayers();
  if (weatherRouteMarkers) weatherRouteMarkers.clearLayers();
  if (weatherPortMarkers) weatherPortMarkers.clearLayers();
  if (finderTrackLayer) { finderTrackLayer.remove(); finderTrackLayer = null; }
  closePortDetail();
  if (routeLayer)   { routeLayer.remove();   routeLayer = null; }
  if (markersLayer) { markersLayer.remove(); markersLayer = null; }
  if (ccRouteLayer) ccRouteLayer.remove();
  ccVesselMarkers.remove();
  ccWeatherMarkers.remove();
  ccPortMarkers.remove();

  if (mode === 'voyage') {
    voyageEl.classList.remove('hidden');
    btnVoyage.classList.add('active');
    // Show onboarding if no results yet
    const onboard = $('voyage-onboard');
    const results = $('voyage-results');
    if (onboard && results && results.classList.contains('hidden')) {
      onboard.classList.remove('hidden');
    }
  } else if (mode === 'portwatch') {
    portwatchEl.classList.remove('hidden');
    btnPortwatch.classList.add('active');
    liveVesselMarkers.addTo(map);
    livePortMarkers.addTo(map);
    loadLiveData();
    livePollingTimer = setInterval(loadLiveData, LIVE_POLL_INTERVAL);
  } else if (mode === 'finder') {
    finderEl.classList.remove('hidden');
    btnFinder.classList.add('active');
    finderMarkers.addTo(map);
  } else if (mode === 'weather') {
    weatherEl.classList.remove('hidden');
    btnWeather.classList.add('active');
  } else if (mode === 'market') {
    marketEl.classList.remove('hidden');
    btnMarket.classList.add('active');
    loadMarketData();
  } else if (mode === 'command') {
    $('command-mode').classList.remove('hidden');
    $('btn-command-mode').classList.add('active');
    fleetVesselMarkers.addTo(map);
    fleetPortMarkers.addTo(map);
    loadFleetDashboard();
    fleetRefreshTimer = setInterval(loadFleetDashboard, 60000);
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

// Color-blind safe severity icons — secondary encoding beyond color
function severityIcon(level) {
  const icons = { SEVERE: '\u26A0', HIGH: '\u25B2', MODERATE: '\u25CF', LOW: '\u2713' };
  return icons[level] || '';
}

// ── Sort button wiring ─────────────────────────────────────────────────────
document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSortKey = btn.dataset.sort;
    if (liveData && liveData.ports) {
      renderLivePortList(liveData.ports);
    }
  });
});

// ── Port detail panel ──────────────────────────────────────────────────────
async function openPortDetail(locode) {
  selectedPortLocode = locode;

  document.querySelectorAll('.port-list-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.locode === locode);
  });

  const panel = $('port-detail');
  panel.classList.remove('hidden');
  panel.classList.add('open');

  $('pd-port-name').textContent = 'Loading\u2026';
  $('pd-port-meta').textContent = '';
  $('pd-score-val').textContent = '\u2014';
  $('pd-level-badge').textContent = '\u2014';
  $('pd-level-badge').className = 'pd-level-badge';
  $('pd-peak-anchored').textContent = '\u2014';
  $('pd-avg-wait').textContent = '\u2014';
  $('pd-peak-berthed').textContent = '\u2014';
  $('pd-transitioned').textContent = '\u2014';
  $('pd-vessel-list').innerHTML = '';
  $('pd-state-breakdown').innerHTML = '';
  $('pd-timeline-chart').innerHTML = '<div class="timeline-loading">Loading chart\u2026</div>';

  try {
    const [detailRes, timelineRes] = await Promise.all([
      fetch(`/api/congestion/v2/${encodeURIComponent(locode)}`),
      fetch(`/api/congestion/${encodeURIComponent(locode)}/timeline`),
    ]);

    if (!detailRes.ok) throw new Error(`Port detail: HTTP ${detailRes.status}`);
    const detail   = await detailRes.json();
    const timeline = timelineRes.ok ? await timelineRes.json() : { timeline: [] };

    renderPortDetail(detail, timeline);

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
  setTimeout(() => {
    if (!panel.classList.contains('open')) panel.classList.add('hidden');
  }, 300);
  document.querySelectorAll('.port-list-item').forEach(el => el.classList.remove('selected'));
}

function renderPortDetail(detail, timeline) {
  const s     = detail.summary;
  const level = s.congestion_level || 'LOW';
  const score = Number(s.congestion_score) || 0;
  const color = levelColor(level);

  $('pd-port-name').textContent = s.name || s.port_locode;
  $('pd-port-meta').textContent = `${s.port_locode} \u00b7 ${s.country || ''}`;

  const circumference = 2 * Math.PI * 32;
  const fill = $('pd-ring-fill');
  fill.style.stroke = color;
  const offset = circumference * (1 - score / 100);
  fill.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
  fill.setAttribute('stroke-dashoffset', offset);

  $('pd-score-val').textContent = score;
  $('pd-score-val').style.color = color;

  // Update ARIA label for screen readers
  const scoreRow = $('pd-score-row');
  if (scoreRow) scoreRow.setAttribute('aria-label', `${s.name || s.port_locode} congestion: ${level}, score ${score}`);

  const badge = $('pd-level-badge');
  badge.textContent = `${severityIcon(level)} ${level}`;
  badge.className   = `pd-level-badge level-${level.toLowerCase()}`;

  $('pd-peak-anchored').textContent  = Number(s.peak_anchored) || 0;
  const wait = Number(s.avg_actual_wait_hrs);
  $('pd-avg-wait').textContent       = wait > 0 ? wait.toFixed(1) + 'h' : 'N/A';
  $('pd-peak-berthed').textContent   = Number(s.peak_berthed) || 0;
  $('pd-transitioned').textContent   = Number(s.vessels_transitioned) || 0;

  drawTimelineChart('pd-timeline-chart', timeline.timeline || []);
  renderStateBreakdown(detail.breakdown || [], detail.vessels || []);
  renderVesselList(detail.vessels || []);
}

function renderStateBreakdown(breakdown, vessels) {
  const el = $('pd-state-breakdown');
  const stateCounts = {};
  vessels.forEach(v => { stateCounts[v.state] = (stateCounts[v.state] || 0) + 1; });

  const states = ['ANCHORED', 'BERTHED', 'APPROACHING', 'MANEUVERING', 'TRANSITING'];
  const stateColors = {
    ANCHORED: '#ff9800', BERTHED: '#2196f3', APPROACHING: '#9c27b0',
    MANEUVERING: '#00bcd4', TRANSITING: '#4caf50',
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
    ANCHORED: '#ff9800', BERTHED: '#2196f3', APPROACHING: '#9c27b0',
    MANEUVERING: '#00bcd4', TRANSITING: '#4caf50',
  };
  el.innerHTML = vessels.map(v => {
    const col      = stateColors[v.state] || '#8b949e';
    const lastSeen = v.last_seen ? new Date(v.last_seen).toLocaleDateString() : '\u2014';
    const distStr  = v.dist_nm != null ? `${v.dist_nm} nm` : '';
    return `
      <div class="vessel-row">
        <div class="vessel-row-top">
          <span class="vessel-name">${escHtml(v.name || v.imo || '\u2014')}</span>
          <span class="state-badge" style="background:${col}20;color:${col};border-color:${col}40">${v.state}</span>
        </div>
        <div class="vessel-row-sub">
          <span class="vessel-type">${escHtml(v.vessel_type || '\u2014')}</span>
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
    return line + ` L${xp(maxT).toFixed(1)},${base} L${xp(minT).toFixed(1)},${base} Z`;
  }

  const yTicks = [0, Math.ceil(maxVal / 2), maxVal];
  const yTickLines = yTicks.map(v => {
    const y = yp(v).toFixed(1);
    return `
      <line x1="${PAD.left}" y1="${y}" x2="${PAD.left + chartW}" y2="${y}"
            stroke="#30363d" stroke-width="1" stroke-dasharray="3 3"/>
      <text x="${PAD.left - 4}" y="${y}" text-anchor="end" dominant-baseline="middle"
            fill="#8b949e" font-size="9">${v}</text>`;
  }).join('');

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

  const svg = `
    <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
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
      ${yTickLines}
      ${xTickLines}
      <path d="${makeAreaPath('anchored')}" fill="url(#grad-anchored-${containerId})"/>
      <path d="${makeAreaPath('berthed')}"  fill="url(#grad-berthed-${containerId})"/>
      <path d="${makePath('anchored')}" fill="none" stroke="#ff9800" stroke-width="2" stroke-linejoin="round"/>
      <path d="${makePath('berthed')}"  fill="none" stroke="#2196f3" stroke-width="1.5" stroke-linejoin="round" stroke-dasharray="5 3"/>
      <rect x="${PAD.left}" y="${H - 14}" width="8" height="3" rx="1.5" fill="#ff9800"/>
      <text x="${PAD.left + 11}" y="${H - 10}" fill="#8b949e" font-size="9">Anchored</text>
      <rect x="${PAD.left + 62}" y="${H - 14}" width="8" height="3" rx="1.5" fill="#2196f3"/>
      <text x="${PAD.left + 73}" y="${H - 10}" fill="#8b949e" font-size="9">Berthed</text>
    </svg>`;

  container.innerHTML = svg;
}

// ── Live Congestion (Port Watch) ──────────────────────────────────────────
let liveData          = null;
let livePollingTimer  = null;
let liveVesselMarkers = L.featureGroup();
let livePortMarkers   = L.featureGroup();

const LIVE_POLL_INTERVAL = 45000;

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

  const sorted = [...ports].sort((a, b) => {
    if (currentSortKey === 'score') return b.congestion_score - a.congestion_score;
    if (currentSortKey === 'wait') return (b.avg_wait_hours || 0) - (a.avg_wait_hours || 0);
    if (currentSortKey === 'vessels') return b.total_vessels - a.total_vessels;
    if (currentSortKey === 'name') return a.name.localeCompare(b.name);
    return 0;
  });

  listEl.innerHTML = '';
  sorted.forEach(port => {
    const score     = Number(port.congestion_score) || 0;
    const anchored  = Number(port.anchored_count) || 0;
    const berthed   = Number(port.berthed_count) || 0;
    const wait      = Number(port.avg_wait_hours) || 0;
    const level     = port.severity || 'LOW';
    const color     = levelColor(level);
    const total     = Number(port.total_vessels) || 0;

    const delta = port.score_delta_24h;
    const deltaHtml = delta !== null && delta !== undefined
      ? `<span class="delta ${delta > 0 ? 'delta-up' : delta < 0 ? 'delta-down' : 'delta-flat'}">${delta > 0 ? '\u2191' : delta < 0 ? '\u2193' : '\u2192'}${Math.abs(delta).toFixed(1)}</span>`
      : '';

    const item = document.createElement('div');
    item.className = 'port-list-item';
    item.dataset.locode = port.locode;
    item.innerHTML = `
      <div class="pli-stripe ${levelClass(level)}"></div>
      <div class="pli-body">
        <div class="pli-top">
          <div class="pli-name">${escHtml(port.name)}</div>
          <div class="pli-score" style="background:${color}20;color:${color};border-color:${color}40">${severityIcon(level)} ${score}${deltaHtml}</div>
        </div>
        <div class="pli-sub">
          <span class="pli-country">${escHtml(port.country || '')} \u00b7 ${escHtml(port.locode)}</span>
          <span class="pli-stats">${total} vessels \u00b7 ${anchored} anchored</span>
        </div>
        <div class="pli-sub">
          <span class="pli-stats">${berthed} berthed \u00b7 ${wait > 0 ? wait.toFixed(1) + 'h avg wait' : 'no wait'}</span>
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
      radius, color, weight: 2, opacity: 0.9, fillColor: color, fillOpacity: 0.35,
    });

    circle.bindTooltip(`
      <strong>${escHtml(port.name)}</strong><br>
      ${escHtml(port.locode)} \u00b7 ${escHtml(port.country || '')}<br>
      Score: <strong>${score}</strong> \u00b7 ${severityIcon(port.severity || 'LOW')} ${escHtml(port.severity || 'LOW')}<br>
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

  $('pd-port-name').textContent = 'Loading\u2026';
  $('pd-port-meta').textContent = '';
  $('pd-score-val').textContent = '\u2014';
  $('pd-level-badge').textContent = '\u2014';
  $('pd-level-badge').className = 'pd-level-badge';
  $('pd-peak-anchored').textContent = '\u2014';
  $('pd-avg-wait').textContent = '\u2014';
  $('pd-peak-berthed').textContent = '\u2014';
  $('pd-transitioned').textContent = '\u2014';
  $('pd-vessel-list').innerHTML = '';
  $('pd-state-breakdown').innerHTML = '';
  $('pd-timeline-chart').innerHTML = '<div class="timeline-loading">Loading live data\u2026</div>';

  try {
    const detailRes = await fetch(`/api/congestion/live/${encodeURIComponent(locode)}`);
    if (!detailRes.ok) throw new Error(`HTTP ${detailRes.status}`);
    const detail = await detailRes.json();

    renderLivePortDetail(detail);
    renderLiveVesselDots(detail.vessels || []);

    if (detail.lat != null && detail.lon != null) {
      map.setView([detail.lat, detail.lon], 11, { animate: true });
    }

    // Load intelligence in background
    $('pd-intelligence').innerHTML = '<div class="vessel-empty">Loading port intelligence...</div>';
    fetch(`/api/congestion/live/${encodeURIComponent(locode)}/intelligence`)
      .then(r => r.ok ? r.json() : null)
      .then(intel => renderIntelligence(intel))
      .catch(() => {
        $('pd-intelligence').innerHTML = '<div class="vessel-empty">Could not load intelligence.</div>';
      });
  } catch (err) {
    $('pd-port-name').textContent = 'Error';
    $('pd-port-meta').textContent = err.message;
  }
}

function renderLivePortDetail(detail) {
  const level = detail.severity || 'LOW';
  const score = Number(detail.congestion_score) || 0;
  const color = levelColor(level);

  $('pd-port-name').textContent = detail.name || detail.locode;
  $('pd-port-meta').textContent = `${detail.locode} \u00b7 ${detail.country || ''} \u00b7 LIVE`;

  const circumference = 2 * Math.PI * 32;
  const fill = $('pd-ring-fill');
  fill.style.stroke = color;
  fill.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
  fill.setAttribute('stroke-dashoffset', circumference * (1 - score / 100));

  $('pd-score-val').textContent = score;
  $('pd-score-val').style.color = color;

  // Update ARIA label for screen readers
  const scoreRow = $('pd-score-row');
  if (scoreRow) scoreRow.setAttribute('aria-label', `${detail.name || detail.locode} congestion: ${level}, score ${score}`);

  const badge = $('pd-level-badge');
  badge.textContent = `${severityIcon(level)} ${level}`;
  badge.className = `pd-level-badge level-${level.toLowerCase()}`;

  $('pd-peak-anchored').textContent = detail.anchored_count || 0;
  $('pd-avg-wait').textContent = detail.avg_wait_hours > 0 ? detail.avg_wait_hours.toFixed(1) + 'h' : 'N/A';
  $('pd-peak-berthed').textContent = detail.berthed_count || 0;
  $('pd-transitioned').textContent = detail.total_vessels || 0;

  const metricLabels = document.querySelectorAll('.pd-metric-label');
  if (metricLabels[3]) metricLabels[3].textContent = 'Total Vessels';
  if (metricLabels[0]) metricLabels[0].textContent = 'Anchored';
  if (metricLabels[2]) metricLabels[2].textContent = 'Berthed';

  $('pd-timeline-chart').innerHTML = '<div class="timeline-empty">Live mode \u2014 no historical timeline</div>';

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
    ANCHORED: '#ff9800', BERTHED: '#2196f3', APPROACHING: '#9c27b0', TRANSITING: '#4caf50',
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
    ANCHORED: '#ff9800', BERTHED: '#2196f3', APPROACHING: '#9c27b0', TRANSITING: '#4caf50',
  };

  // Color-blind safe: differentiate vessel states by size, fill, and border
  const stateStyle = {
    BERTHED:     { radius: 7, weight: 1.5, fillOpacity: 0.85 },
    ANCHORED:    { radius: 7, weight: 3,   fillOpacity: 0.3  },
    APPROACHING: { radius: 5, weight: 1.5, fillOpacity: 0.85 },
    TRANSITING:  { radius: 4, weight: 1.5, fillOpacity: 0.5  },
  };

  vessels.forEach(v => {
    if (v.lat == null || v.lon == null) return;

    const col   = stateColors[v.state] || '#8b949e';
    const style = stateStyle[v.state] || { radius: 5, weight: 1.5, fillOpacity: 0.85 };

    const marker = L.circleMarker([v.lat, v.lon], {
      radius: style.radius, color: '#fff', weight: style.weight, fillColor: col, fillOpacity: style.fillOpacity,
    });

    const waitStr = v.wait_hours > 0 ? `<br>Wait: ${v.wait_hours}h` : '';
    marker.bindTooltip(`
      <strong>${escHtml(v.name || String(v.mmsi))}</strong><br>
      ${v.state} \u00b7 ${v.speed || 0} kts<br>
      ${v.dist_to_port_nm || '?'} nm from port${waitStr}
    `, { sticky: true, className: 'port-tooltip' });

    liveVesselMarkers.addLayer(marker);
  });
}

// ── Port Intelligence ─────────────────────────────────────────────────────────
function renderIntelligence(data) {
  const el = $('pd-intelligence');
  if (!data) {
    el.innerHTML = '<div class="vessel-empty">No intelligence data.</div>';
    return;
  }

  const fmtT = t => {
    if (!t) return '0';
    if (t >= 1000000) return (t / 1000000).toFixed(1) + 'M';
    if (t >= 1000) return Math.round(t / 1000) + 'K';
    return String(Math.round(t));
  };
  const fmtH = h => h < 1 ? `${Math.round(h * 60)}m` : h < 24 ? `${h.toFixed(1)}h` : `${(h / 24).toFixed(1)}d`;

  let html = '';

  // Africa Bagged Cargo Leads
  const leads = data.africa_bagged_cargo_leads || [];
  if (leads.length > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title" style="color:#ff9800">Africa Cargo Leads (${leads.length})</div>`;
    leads.forEach(l => {
      html += `<div class="intel-lead">
        <div class="intel-lead-name">${escHtml(l.name || String(l.mmsi))}</div>
        <div class="intel-lead-detail">${escHtml(l.type || '')} \u00b7 ${escHtml(l.flag || '')} \u00b7 ${escHtml(l.reason)}</div>
        ${l.destination ? `<div class="intel-lead-detail">Dest: ${escHtml(l.destination)}</div>` : ''}
      </div>`;
    });
    html += '</div>';
  }

  // Cargo Estimation
  const cargo = data.specs?.cargo_estimate;
  if (cargo && cargo.vessels_analyzed > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title">Cargo Estimation</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val warn">${fmtT(cargo.total_est_cargo_tonnes)}t</div><div class="intel-label">Est. Cargo</div></div>
        <div class="intel-card"><div class="intel-val">${fmtT(cargo.total_dwt)} DWT</div><div class="intel-label">Capacity</div></div>
        <div class="intel-card"><div class="intel-val ${cargo.avg_load_pct > 80 ? 'hot' : 'good'}">${cargo.avg_load_pct != null ? cargo.avg_load_pct + '%' : 'N/A'}</div><div class="intel-label">Avg Load</div></div>
      </div>`;
    if (cargo.by_type?.length) {
      const maxC = Math.max(...cargo.by_type.map(t => t.est_cargo || 0), 1);
      cargo.by_type.forEach(t => {
        const pct = Math.round((t.est_cargo / maxC) * 100);
        html += `<div class="intel-bar-row">
          <span class="intel-bar-label" title="${escHtml(t.type)}">${escHtml(t.type)}</span>
          <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${pct}%;background:#ff9800"></div></div>
          <span class="intel-bar-val">${fmtT(t.est_cargo)}t</span>
        </div>`;
      });
    }
    html += '</div>';
  }

  // Trade Route Origins
  const origins = data.origins || [];
  if (origins.length > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title">Trade Origins (30d history)</div>`;
    origins.forEach(o => {
      html += `<div class="intel-origin-line">
        <span class="intel-origin-dot"></span>
        <span style="font-weight:500;flex:1">${escHtml(o.name || '?')}</span>
        <span style="color:var(--text2);font-size:10px">${escHtml(o.type || '')}</span>
      </div>
      <div style="font-size:10px;color:var(--text2);padding-left:14px;margin-bottom:4px">
        From (${o.origin_lat}, ${o.origin_lon}) ${o.origin_date ? o.origin_date.split('T')[0] : ''}
      </div>`;
    });
    html += '</div>';
  }

  // Flag Distribution
  const flags = data.flag_distribution || [];
  if (flags.length > 0) {
    const maxF = flags[0]?.count || 1;
    html += `<div class="intel-section">
      <div class="intel-section-title">Flag States</div>`;
    flags.slice(0, 8).forEach(f => {
      const pct = Math.round((f.count / maxF) * 100);
      html += `<div class="intel-bar-row">
        <span class="intel-bar-label">${escHtml(f.flag)}</span>
        <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${pct}%;background:#2196f3"></div></div>
        <span class="intel-bar-val">${f.count} (${f.pct}%)</span>
      </div>`;
    });
    html += '</div>';
  }

  // Fleet Profile
  const age = data.specs?.fleet_age;
  const sizes = data.specs?.size_classes;
  if (age && age.vessels_with_data) {
    html += `<div class="intel-section">
      <div class="intel-section-title">Fleet Profile</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val">${age.avg_age}y</div><div class="intel-label">Avg Age</div></div>
        <div class="intel-card"><div class="intel-val good">${age.newest}y</div><div class="intel-label">Newest</div></div>
        <div class="intel-card"><div class="intel-val ${age.oldest > 25 ? 'hot' : 'warn'}">${age.oldest}y</div><div class="intel-label">Oldest</div></div>
      </div>`;
    if (sizes?.length) {
      const maxS = Math.max(...sizes.map(s => s.count), 1);
      sizes.forEach(s => {
        const pct = Math.round((s.count / maxS) * 100);
        html += `<div class="intel-bar-row">
          <span class="intel-bar-label">${escHtml(s.class)}</span>
          <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${pct}%;background:#9c27b0"></div></div>
          <span class="intel-bar-val">${s.count}</span>
        </div>`;
      });
    }
    html += '</div>';
  }

  // Port Specialization
  const spec = data.port_specialization || [];
  if (spec.length > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title">Port Specialization</div>`;
    spec.forEach(s => {
      html += `<div class="intel-bar-row">
        <span class="intel-bar-label">${escHtml(s.type)}</span>
        <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${s.pct}%;background:#4caf50"></div></div>
        <span class="intel-bar-val">${s.pct}%</span>
      </div>`;
    });
    html += '</div>';
  }

  // ETA Analysis
  const eta = data.eta_analysis;
  if (eta && eta.vessels_with_eta > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title">ETA Accuracy</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val">${eta.vessels_with_eta}</div><div class="intel-label">With ETA</div></div>
        <div class="intel-card"><div class="intel-val good">${eta.overdue}</div><div class="intel-label">Overdue</div></div>
        <div class="intel-card"><div class="intel-val">${eta.still_expected}</div><div class="intel-label">Expected</div></div>
      </div>
      ${eta.avg_overdue_hours > 0 ? `<div style="font-size:11px;color:var(--text2);text-align:center">Avg ${fmtH(eta.avg_overdue_hours)} past ETA \u00b7 Max ${fmtH(eta.max_overdue_hours)}</div>` : ''}
    </div>`;
  }

  // Speed Profile
  const sp = data.speed_profile;
  if (sp) {
    html += `<div class="intel-section">
      <div class="intel-section-title">Speed Profile</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val">${sp.stationary}</div><div class="intel-label">Stationary</div></div>
        <div class="intel-card"><div class="intel-val">${sp.slow}</div><div class="intel-label">Slow</div></div>
        <div class="intel-card"><div class="intel-val">${sp.fast + sp.moderate}</div><div class="intel-label">Moving</div></div>
      </div>
    </div>`;
  }

  if (!html) html = '<div class="vessel-empty">No intelligence data available.</div>';
  el.innerHTML = html;
}


// ── Vessel Finder ─────────────────────────────────────────────────────────────

// Port search for finder mode
(function initFinderPortSearch() {
  const input = $('finder-port-search');
  const dropdown = $('finder-port-dropdown');
  if (!input || !dropdown) return;

  createPortAutocomplete(input, dropdown, (port) => {
    finderPort = { name: port.name, lat: port.lat, lon: port.lon, locode: port.locode };
    input.value = `${port.name} (${port.locode})`;
  }, {
    onClear: () => { finderPort = null; },
  });
})();

// Search button
(function initFinderSearch() {
  const btn = $('finder-search-btn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    if (!finderPort) {
      $('finder-results').innerHTML = '<div class="vessel-empty">Select a port first.</div>';
      return;
    }

    const typeFilter = $('finder-type-filter').value;
    const idleOnly   = $('finder-idle-only').checked;
    const africaOnly = $('finder-africa-only').checked;

    const loading = $('finder-loading');
    loading.classList.remove('hidden');
    $('finder-results').innerHTML = '';

    const params = new URLSearchParams({
      locode: finderPort.locode || '',
      lat: finderPort.lat,
      lon: finderPort.lon,
    });
    if (typeFilter) params.set('type', typeFilter);
    if (idleOnly) params.set('idle_only', 'true');
    if (africaOnly) params.set('africa_only', 'true');

    try {
      const res = await fetch(`/api/vessels/search?${params}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      renderFinderResults(data);
    } catch (err) {
      $('finder-results').innerHTML = `<div class="cong-error">${escHtml(err.message)}</div>`;
    } finally {
      loading.classList.add('hidden');
    }
  });
})();

function renderFinderResults(data) {
  const resultsEl = $('finder-results');
  const vessels = data.vessels || [];

  finderMarkers.clearLayers();

  if (!vessels.length) {
    resultsEl.innerHTML = '<div class="vessel-empty">No vessels found near this port.</div>';
    return;
  }

  // Show count
  let html = `<div style="font-size:11px;color:var(--text2);margin:8px 0">${vessels.length} vessel${vessels.length > 1 ? 's' : ''} found</div>`;

  vessels.forEach(v => {
    const avail = v.availability || {};
    const score = avail.score != null ? avail.score : 0;
    const badgeClass = score >= 0.6 ? 'avail-high' : score >= 0.3 ? 'avail-med' : 'avail-low';
    const badgeText  = score >= 0.6 ? 'Likely Available' : score >= 0.3 ? 'Possibly Available' : 'In Use';
    const isAfrica   = v.africa_trade;

    html += `
      <div class="finder-vessel" data-mmsi="${v.mmsi}">
        <div class="finder-vessel-top">
          <span class="vessel-name">${escHtml(v.name || String(v.mmsi))}</span>
          <span class="avail-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="finder-vessel-meta">
          <span>${escHtml(v.type || v.vessel_type || 'Unknown')}</span>
          <span>${v.speed != null ? v.speed + ' kts' : ''}</span>
          <span>${v.distance_nm != null ? v.distance_nm.toFixed(1) + ' nm' : ''}</span>
          ${isAfrica ? '<span class="africa-badge">Africa Trade</span>' : ''}
        </div>
        ${avail.reasons?.length ? `<div class="finder-vessel-reasons">${avail.reasons.join(' \u00b7 ')}</div>` : ''}
        ${v.destination ? `<div class="finder-vessel-reasons">Dest: ${escHtml(v.destination)}</div>` : ''}
      </div>`;

    // Add marker on map
    if (v.lat != null && v.lon != null) {
      const col = score >= 0.6 ? '#4caf50' : score >= 0.3 ? '#ff9800' : '#8b949e';
      const marker = L.circleMarker([v.lat, v.lon], {
        radius: 6, color: '#fff', weight: 1.5, fillColor: col, fillOpacity: 0.9,
      });
      marker.bindTooltip(`
        <strong>${escHtml(v.name || String(v.mmsi))}</strong><br>
        ${escHtml(v.type || '')} \u00b7 ${v.speed || 0} kts<br>
        ${badgeText}
      `, { sticky: true, className: 'port-tooltip' });
      finderMarkers.addLayer(marker);
    }
  });

  resultsEl.innerHTML = html;

  // Click handler for vessel detail
  resultsEl.querySelectorAll('.finder-vessel').forEach(el => {
    el.addEventListener('click', () => {
      const mmsi = el.dataset.mmsi;
      if (mmsi) loadVesselDetail(parseInt(mmsi, 10));
    });
  });

  // Fit map to show all vessel markers
  if (finderMarkers.getLayers().length > 0) {
    map.fitBounds(finderMarkers.getBounds(), { padding: [40, 40], maxZoom: 12 });
  }
}

async function loadVesselDetail(mmsi) {
  // Show detail in port-detail panel (reuse)
  const panel = $('port-detail');
  panel.classList.remove('hidden');
  panel.classList.add('open');

  $('pd-port-name').textContent = 'Loading vessel...';
  $('pd-port-meta').textContent = `MMSI: ${mmsi}`;
  $('pd-score-val').textContent = '\u2014';
  $('pd-level-badge').textContent = '';
  $('pd-level-badge').className = 'pd-level-badge';
  $('pd-peak-anchored').textContent = '\u2014';
  $('pd-avg-wait').textContent = '\u2014';
  $('pd-peak-berthed').textContent = '\u2014';
  $('pd-transitioned').textContent = '\u2014';
  $('pd-vessel-list').innerHTML = '';
  $('pd-state-breakdown').innerHTML = '';
  $('pd-timeline-chart').innerHTML = '<div class="timeline-loading">Loading vessel detail...</div>';
  $('pd-intelligence').innerHTML = '';

  try {
    const [detailRes, trackRes] = await Promise.all([
      fetch(`/api/vessels/${mmsi}/detail`),
      fetch(`/api/vessels/${mmsi}/track?days=30`),
    ]);

    const detail = detailRes.ok ? await detailRes.json() : null;
    const track  = trackRes.ok ? await trackRes.json() : null;

    if (detail) {
      $('pd-port-name').textContent = detail.name || String(mmsi);
      const meta = [detail.type, detail.flag, detail.imo ? `IMO: ${detail.imo}` : ''].filter(Boolean).join(' \u00b7 ');
      $('pd-port-meta').textContent = meta || `MMSI: ${mmsi}`;

      // Use metrics area for vessel specs
      $('pd-peak-anchored').textContent = detail.dwt ? `${Number(detail.dwt).toLocaleString()} DWT` : '\u2014';
      $('pd-avg-wait').textContent = detail.built_year || '\u2014';
      $('pd-peak-berthed').textContent = detail.length ? `${detail.length}m` : '\u2014';
      $('pd-transitioned').textContent = detail.beam ? `${detail.beam}m` : '\u2014';

      const metricLabels = document.querySelectorAll('.pd-metric-label');
      if (metricLabels[0]) metricLabels[0].textContent = 'Deadweight';
      if (metricLabels[1]) metricLabels[1].textContent = 'Built';
      if (metricLabels[2]) metricLabels[2].textContent = 'Length';
      if (metricLabels[3]) metricLabels[3].textContent = 'Beam';

      // Availability score as ring
      const avail = detail.availability || {};
      const score = Math.round((avail.score || 0) * 100);
      const color = score >= 60 ? '#4caf50' : score >= 30 ? '#ff9800' : '#ef5350';
      const circumference = 2 * Math.PI * 32;
      const fill = $('pd-ring-fill');
      fill.style.stroke = color;
      fill.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
      fill.setAttribute('stroke-dashoffset', circumference * (1 - score / 100));
      $('pd-score-val').textContent = score;
      $('pd-score-val').style.color = color;

      // Update ARIA label for screen readers
      const scoreRow = $('pd-score-row');
      const availLabel = score >= 60 ? 'AVAILABLE' : score >= 30 ? 'MAYBE' : 'IN USE';
      if (scoreRow) scoreRow.setAttribute('aria-label', `${detail.name || detail.mmsi} availability: ${availLabel}, score ${score}`);

      const badge = $('pd-level-badge');
      badge.textContent = score >= 60 ? 'AVAILABLE' : score >= 30 ? 'MAYBE' : 'IN USE';
      badge.className = `pd-level-badge ${score >= 60 ? 'level-low' : score >= 30 ? 'level-moderate' : 'level-high'}`;
    }

    // Render track on map
    if (track && track.track) {
      if (finderTrackLayer) { finderTrackLayer.remove(); finderTrackLayer = null; }
      const trackGeojson = L.geoJSON(track.track, {
        style: { color: '#9c27b0', weight: 2, opacity: 0.7, dashArray: '6 4' },
      });
      trackGeojson.addTo(map);
      finderTrackLayer = trackGeojson;
      map.fitBounds(trackGeojson.getBounds(), { padding: [40, 40] });

      $('pd-timeline-chart').innerHTML = '<div class="timeline-empty">30-day track shown on map</div>';
    } else {
      $('pd-timeline-chart').innerHTML = '<div class="timeline-empty">No track data available</div>';
    }

    $('pd-state-breakdown').innerHTML = '';
    $('pd-vessel-list').innerHTML = '';

  } catch (err) {
    $('pd-port-name').textContent = 'Error loading vessel';
    $('pd-port-meta').textContent = err.message;
  }
}

// ── Weather & Risk Mode ──────────────────────────────────────────────────────
let weatherPortMarkers = L.featureGroup();
let weatherRouteMarkers = L.featureGroup();
let selectedWeatherPort = null;

// Port weather search autocomplete
(function initWeatherAutocomplete() {
  const inp = document.getElementById('weather-port-search');
  const dd = document.getElementById('weather-port-dropdown');
  if (!inp || !dd) return;

  createPortAutocomplete(inp, dd, (port) => {
    selectedWeatherPort = { locode: port.locode, name: port.name };
    inp.value = `${port.name} (${port.locode})`;
  });
})();

document.getElementById('weather-port-btn')?.addEventListener('click', async () => {
  if (!selectedWeatherPort) return;
  const el = document.getElementById('weather-port-result');
  el.innerHTML = '<div class="vessel-empty">Loading weather...</div>';

  try {
    const res = await fetch(`/api/weather/port/${selectedWeatherPort.locode}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderPortWeather(data);
  } catch (err) {
    el.innerHTML = `<div class="vessel-empty">Error: ${err.message}</div>`;
  }
});

function renderPortWeather(data) {
  const el = document.getElementById('weather-port-result');
  const c = data.current || {};
  const forecast = data.forecast || [];

  const windDir = (deg) => {
    const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return dirs[Math.round((deg % 360) / 22.5) % 16];
  };

  let html = `
    <div class="intel-section" style="margin-top:10px">
      <div class="intel-section-title">Current Conditions — ${escHtml(data.port?.name || '')}</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val" style="color:${c.risk_color || 'var(--text)'}">${c.wave_height_m?.toFixed(1) || 0}m</div><div class="intel-label">Waves</div></div>
        <div class="intel-card"><div class="intel-val">${Math.round(c.wind_speed_kts || 0)} kts</div><div class="intel-label">Wind ${windDir(c.wind_direction || 0)}</div></div>
        <div class="intel-card"><div class="intel-val">${c.temperature_c?.toFixed(0) || '?'}°C</div><div class="intel-label">Temp</div></div>
      </div>
      <div style="text-align:center;margin:6px 0;font-size:12px;font-weight:600;color:${c.risk_color || 'var(--text)'}">${c.risk_level || 'N/A'} Risk</div>
    </div>`;

  if (forecast.length > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title">7-Day Forecast</div>
      <table class="turnaround-table">
        <thead><tr><th>Date</th><th style="text-align:right">Wind</th><th style="text-align:right">Waves</th><th style="text-align:right">Rain</th></tr></thead>
        <tbody>`;
    forecast.forEach(f => {
      const wMax = f.wind_max_kts || 0;
      const waveMax = f.wave_max_m || 0;
      const risk = waveMax > 4 || wMax > 35 ? 'color:#ef5350' : waveMax > 2.5 || wMax > 25 ? 'color:#ff9800' : '';
      html += `<tr>
        <td>${(f.date || '').substring(5)}</td>
        <td class="tt-num" style="${risk}">${Math.round(wMax)} kts</td>
        <td class="tt-num" style="${risk}">${waveMax?.toFixed(1) || '?'}m</td>
        <td class="tt-num">${(f.precipitation_mm || 0).toFixed(0)}mm</td>
      </tr>`;
    });
    html += '</tbody></table></div>';
  }

  el.innerHTML = html;
}

// Route weather
document.getElementById('weather-route-btn')?.addEventListener('click', async () => {
  if (!routeLayer && !lastRouteData) {
    document.getElementById('weather-route-result').innerHTML = '<div class="vessel-empty">Calculate a voyage first in the Voyage tab.</div>';
    return;
  }

  const el = document.getElementById('weather-route-result');
  el.innerHTML = '<div class="vessel-empty">Checking weather along route...</div>';

  // Get the route GeoJSON from the last calculated route
  const routeGeoJSON = routeLayer ? routeLayer.toGeoJSON() : { type: 'FeatureCollection', features: lastRouteData.legs.map(l => l.route) };
  const speed = parseFloat(document.getElementById('speed-input')?.value) || 14;

  try {
    const res = await fetch('/api/weather/route', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ route_geojson: routeGeoJSON, speed_knots: speed, departure_time: new Date().toISOString() }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderRouteWeather(data);
  } catch (err) {
    el.innerHTML = `<div class="vessel-empty">Error: ${err.message}</div>`;
  }
});

function renderRouteWeather(data) {
  const el = document.getElementById('weather-route-result');
  const points = data.weather_points || [];

  // Add weather dots on map
  weatherRouteMarkers.clearLayers();
  weatherRouteMarkers.addTo(map);

  points.forEach(p => {
    if (!p.lat || !p.lon) return;
    const marker = L.circleMarker([p.lat, p.lon], {
      radius: 6,
      color: '#fff',
      weight: 1.5,
      fillColor: p.risk_color || '#8b949e',
      fillOpacity: 0.85,
    });
    const wx = p.weather || {};
    marker.bindTooltip(`
      <strong>${p.distance_nmi} nmi</strong> — ${p.risk_level}${p.is_forecast ? ' (forecast)' : p.risk_level === 'UNKNOWN' ? ' (beyond forecast)' : ' (current)'}<br>
      Waves: ${wx.wave_height_m?.toFixed(1) || '?'}m<br>
      Swell: ${wx.swell_height_m?.toFixed(1) || '?'}m
    `, { sticky: true });
    weatherRouteMarkers.addLayer(marker);
  });

  // Summary
  let html = `
    <div class="intel-section" style="margin-top:10px">
      <div class="intel-section-title">Route Weather — ${points.length} points</div>
      <div style="text-align:center;padding:8px;background:var(--bg3);border-radius:6px;margin-bottom:8px">
        <div style="font-size:16px;font-weight:700;color:${data.overall_risk_color}">${data.overall_risk} RISK</div>
      </div>`;

  if (data.worst_point?.weather) {
    const wp = data.worst_point;
    const wx = wp.weather;
    html += `<div style="font-size:11px;color:var(--text2)">
      Worst: ${wx.wave_height_m?.toFixed(1)}m waves at ${wp.distance_nmi} nmi from origin
    </div>`;
  }

  // Risk breakdown
  const counts = {LOW: 0, MODERATE: 0, HIGH: 0, SEVERE: 0};
  points.forEach(p => { if (counts[p.risk_level] !== undefined) counts[p.risk_level]++; });
  html += `<div class="intel-grid" style="margin-top:6px">
    <div class="intel-card"><div class="intel-val good">${counts.LOW}</div><div class="intel-label">Low</div></div>
    <div class="intel-card"><div class="intel-val warn">${counts.MODERATE}</div><div class="intel-label">Moderate</div></div>
    <div class="intel-card"><div class="intel-val hot">${counts.HIGH + counts.SEVERE}</div><div class="intel-label">High/Severe</div></div>
  </div>`;

  html += '</div>';
  el.innerHTML = html;
}


// ── Market Intel Mode ────────────────────────────────────────────────────────
async function loadMarketData() {
  const loading = document.getElementById('market-loading');
  const content = document.getElementById('market-content');
  loading.classList.remove('hidden');
  loading.textContent = 'Loading market intelligence...';
  content.innerHTML = '';

  try {
    const [overviewRes, corridorRes] = await Promise.all([
      fetch('/api/market/overview'),
      fetch('/api/market/africa-corridor'),
    ]);

    const overview = overviewRes.ok ? await overviewRes.json() : null;
    const corridor = corridorRes.ok ? await corridorRes.json() : null;

    loading.classList.add('hidden');
    renderMarketIntel(overview, corridor);
  } catch (err) {
    loading.textContent = `Error: ${err.message}`;
  }
}

function renderMarketIntel(overview, corridor) {
  const content = document.getElementById('market-content');
  let html = '';

  if (overview) {
    const s = overview.summary;

    // Summary cards
    html += `<div class="intel-section">
      <div class="intel-section-title">Market Overview</div>
      <div class="intel-grid">
        <div class="intel-card"><div class="intel-val">${s.total_vessels_all_ports}</div><div class="intel-label">Total Vessels</div></div>
        <div class="intel-card"><div class="intel-val warn">${s.total_africa_trade}</div><div class="intel-label">Africa Trade</div></div>
        <div class="intel-card"><div class="intel-val">${s.ports_monitored}</div><div class="intel-label">Ports</div></div>
      </div>
    </div>`;

    // Port comparison heatmap
    html += `<div class="intel-section">
      <div class="intel-section-title">Port Activity Comparison</div>`;

    const maxV = Math.max(...overview.ports.map(p => p.total_vessels), 1);
    overview.ports.forEach(p => {
      const pct = Math.round((p.total_vessels / maxV) * 100);
      const severityCol = {'SEVERE': '#ef5350', 'HIGH': '#ff9800', 'MODERATE': '#ffeb3b', 'LOW': '#4caf50'}[p.severity] || '#4caf50';
      html += `<div class="intel-bar-row">
        <span class="intel-bar-label" title="${p.name}">${severityIcon(p.severity || 'LOW')} ${p.name.substring(0, 10)}</span>
        <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${pct}%;background:${severityCol}"></div></div>
        <span class="intel-bar-val">${p.total_vessels} (${p.congestion_score})</span>
      </div>`;
    });
    html += '</div>';

    // Vessel type distribution across all ports
    html += `<div class="intel-section">
      <div class="intel-section-title">Fleet Mix (All Ports)</div>`;
    const allTypes = {};
    overview.ports.forEach(p => {
      Object.entries(p.vessel_types || {}).forEach(([t, c]) => {
        allTypes[t] = (allTypes[t] || 0) + c;
      });
    });
    const sortedTypes = Object.entries(allTypes).sort((a, b) => b[1] - a[1]);
    const maxType = sortedTypes[0]?.[1] || 1;
    sortedTypes.slice(0, 8).forEach(([t, c]) => {
      const pct = Math.round((c / maxType) * 100);
      html += `<div class="intel-bar-row">
        <span class="intel-bar-label">${t}</span>
        <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${pct}%;background:#9c27b0"></div></div>
        <span class="intel-bar-val">${c}</span>
      </div>`;
    });
    html += '</div>';
  }

  // Africa corridor
  if (corridor && corridor.total_vessels > 0) {
    html += `<div class="intel-section">
      <div class="intel-section-title" style="color:#ff9800">India ↔ Africa Corridor</div>
      <div class="intel-grid">
        <div class="intel-card full"><div class="intel-val warn">${corridor.total_vessels} vessels</div><div class="intel-label">Bagged Cargo on Corridor</div></div>
      </div>`;

    // By port
    Object.entries(corridor.by_port || {}).forEach(([locode, count]) => {
      if (count > 0) {
        html += `<div style="font-size:11px;color:var(--text2);margin:2px 0">${locode}: ${count} vessels</div>`;
      }
    });

    // Vessel list
    html += '<div style="margin-top:6px">';
    corridor.vessels.slice(0, 15).forEach(v => {
      html += `<div class="intel-lead">
        <div class="intel-lead-name">${escHtml(v.name || String(v.mmsi))}</div>
        <div class="intel-lead-detail">${escHtml(v.type)} · ${escHtml(v.state)} at ${escHtml(v.port)}${v.destination ? ' → ' + escHtml(v.destination) : ''}</div>
      </div>`;
    });
    html += '</div></div>';
  } else if (corridor) {
    html += `<div class="intel-section">
      <div class="intel-section-title" style="color:#ff9800">India ↔ Africa Corridor</div>
      <div class="vessel-empty">No bagged cargo vessels detected on corridor currently.</div>
    </div>`;
  }

  content.innerHTML = html;
}

// ── Voyage Onboarding ─────────────────────────────────────────────────────
document.querySelectorAll('.onboard-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const fromLocode = btn.dataset.from;
    const toLocode   = btn.dataset.to;

    // Look up both ports
    try {
      const [fromRes, toRes] = await Promise.all([
        fetch(`/api/ports/search?q=${encodeURIComponent(fromLocode)}&limit=1`),
        fetch(`/api/ports/search?q=${encodeURIComponent(toLocode)}&limit=1`),
      ]);
      const fromPorts = await fromRes.json();
      const toPorts   = await toRes.json();
      if (!fromPorts.length || !toPorts.length) return;

      const fp = fromPorts[0];
      const tp = toPorts[0];

      // Set waypoints
      waypoints[0] = { point: { name: fp.name, lat: fp.lat, lon: fp.lon, locode: fp.locode } };
      waypoints[1] = { point: { name: tp.name, lat: tp.lat, lon: tp.lon, locode: tp.locode } };

      // Fill inputs
      const inputs = document.querySelectorAll('#waypoints-container .wp-search');
      if (inputs[0]) inputs[0].value = `${fp.name} (${fp.locode})`;
      if (inputs[1]) inputs[1].value = `${tp.name} (${tp.locode})`;

      // Trigger calculation
      ui.calcBtn.click();
    } catch { /* ignore */ }
  });
});

// ── Fleet Dashboard ──────────────────────────────────────────────────────

const FLEET_AFRICA_KEYWORDS = [
  'MOMBASA', 'DAR ES SALAAM', 'ZANZIBAR', 'LAMU', 'TANGA',
  'TOAMASINA', 'TAMATAVE', 'TOLIARA', 'MADAGASCAR',
  'MAPUTO', 'BEIRA', 'NACALA',
  'DJIBOUTI', 'MOGADISHU', 'BERBERA',
  'DURBAN', 'CAPE TOWN', 'RICHARDS BAY', 'PORT ELIZABETH',
  'LAGOS', 'APAPA', 'TEMA', 'ABIDJAN', 'DAKAR', 'LUANDA', 'DOUALA',
  'LOME', 'COTONOU', 'CONAKRY', 'POINTE NOIRE',
  'PORT SUDAN',
  'AFRICA', 'KENYA', 'TANZANIA', 'MOZAMBIQUE',
  'NIGERIA', 'GHANA', 'SENEGAL', 'ANGOLA', 'CAMEROON', 'SOMALIA',
];

const FLEET_AFRICA_FLAGS = new Set([
  'KE','TZ','UG','RW','BI','ET','ER','DJ','SO','SS','SD',
  'ZA','MZ','MG','MW','ZM','ZW','BW','NA','SZ','LS','MU','SC','KM',
  'NG','GH','SN','CI','CM','AO','GA','CG','CD','GN','ML','BF',
  'NE','TG','BJ','SL','LR','GW','GM','CV','MR','GQ',
  'EG','LY','TN','DZ','MA',
]);

function scoreFleetAvailability(v) {
  let score = 0;
  const reasons = [];
  const speed = v.speed || 0;
  const dest = (v.destination || '').trim().toUpperCase();
  const state = (v.state || '').toUpperCase();

  if (speed < 0.5 || state === 'ANCHORED') {
    score += 0.3; reasons.push('Stationary');
  } else if (speed < 2) {
    score += 0.1; reasons.push('Barely moving');
  }
  if (!dest || dest === 'CLASS B') {
    score += 0.10; reasons.push('No destination');
  }
  const vtype = (v.type_specific || v.ship_type || v.type || '').toLowerCase();
  if (['cargo','bulk','tanker','general','multi purpose'].some(t => vtype.includes(t))) {
    score += 0.15; reasons.push('Commercial type');
  }
  if ((speed < 0.5 || state === 'ANCHORED') && state !== 'BERTHED') {
    score += 0.2; reasons.push('At anchorage');
  }
  score = Math.min(score, 1.0);
  const label = score >= 0.7 ? 'Likely Available'
    : score >= 0.4 ? 'Possibly Available'
    : score >= 0.2 ? 'Uncertain'
    : 'En Route';
  return { score: Math.round(score * 100) / 100, label, reasons };
}

function isFleetAfricaTrade(v) {
  const dest = (v.destination || '').toUpperCase();
  const flag = (v.country_iso || '').toUpperCase();
  const vtype = (v.type_specific || v.ship_type || v.type || '').toLowerCase();
  const isCargo = ['cargo','bulk','tanker','general','multi purpose'].some(t => vtype.includes(t));
  if (!isCargo) return null;
  for (const kw of FLEET_AFRICA_KEYWORDS) {
    if (dest.includes(kw)) return `→ ${dest}`;
  }
  if (FLEET_AFRICA_FLAGS.has(flag)) return `Flag: ${flag}`;
  return null;
}

async function loadFleetDashboard() {
  try {
    const res = await fetch('/api/fleet/all');
    if (!res.ok) throw new Error('API error');
    fleetData = await res.json();

    // Score and tag each vessel
    fleetAllVessels = (fleetData.vessels || []).map(v => {
      v._avail = scoreFleetAvailability(v);
      v._africa = isFleetAfricaTrade(v);
      return v;
    });

    renderFleetStats();
    renderFleetArrivals();
    populateFleetPortFilter();
    applyFleetFilters();
    renderFleetPortsOnMap();
  } catch (e) {
    console.error('Fleet load failed', e);
  }
}

function renderFleetStats() {
  if (!fleetAllVessels.length) return;
  const total = fleetAllVessels.length;
  const anchored = fleetAllVessels.filter(v => (v.state||'').toUpperCase() === 'ANCHORED').length;
  const berthed  = fleetAllVessels.filter(v => (v.state||'').toUpperCase() === 'BERTHED').length;
  const moving   = fleetAllVessels.filter(v => (v.speed||0) > 0.5 && (v.state||'').toUpperCase() !== 'ANCHORED').length;
  const set = (id, val) => { const el = $(id); if (el) el.textContent = val; };
  set('fleet-total', total);
  set('fleet-anchored', anchored);
  set('fleet-berthed', berthed);
  set('fleet-moving', moving);
}

function renderFleetArrivals() {
  const inbound = fleetData?.inbound || {};
  const ports   = fleetData?.ports  || [];
  const list    = $('fleet-arrivals-list');
  const badge   = $('fleet-arrivals-count');
  if (!list) return;

  const entries = ports.map(p => ({
    locode: p.locode,
    name: p.name,
    count: inbound[p.locode]?.count || 0,
  })).filter(p => p.count > 0).sort((a,b) => b.count - a.count);

  if (badge) badge.textContent = entries.reduce((s, e) => s + e.count, 0);

  list.innerHTML = entries.map(e => `
    <div class="fleet-arrival-row" onclick="filterFleetByInbound('${escHtml(e.locode)}')" style="cursor:pointer;padding:4px 8px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:var(--text1)">${escHtml(e.name)}</span>
      <span class="cc-count-badge">${e.count} inbound</span>
    </div>`).join('') || '<div style="padding:8px;font-size:11px;color:var(--text2)">No inbound data yet</div>';
}

function filterFleetByInbound(locode) {
  const portSel = $('fleet-filter-port');
  if (portSel) portSel.value = locode;
  applyFleetFilters();
}

function populateFleetPortFilter() {
  const sel = $('fleet-filter-port');
  if (!sel || sel.options.length > 1) return; // already populated
  (fleetData?.ports || []).forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.locode;
    opt.textContent = p.name;
    sel.appendChild(opt);
  });
}

function applyFleetFilters() {
  const typeF  = ($('fleet-filter-type')?.value || '').toLowerCase();
  const portF  = ($('fleet-filter-port')?.value || '').toUpperCase();
  const stateF = ($('fleet-filter-state')?.value || '').toUpperCase();
  const destF  = ($('fleet-filter-dest')?.value || '').toUpperCase();
  const availF = $('fleet-filter-available')?.checked;
  const africaF= $('fleet-filter-africa')?.checked;

  fleetFilteredVessels = fleetAllVessels.filter(v => {
    if (typeF && !(v.type_specific||v.ship_type||v.type||'').toLowerCase().includes(typeF)) return false;
    if (portF && (v.port_locode||'').toUpperCase() !== portF) return false;
    if (stateF && (v.state||'').toUpperCase() !== stateF) return false;
    if (destF && !(v.destination||'').toUpperCase().includes(destF)) return false;
    if (availF && v._avail.score < 0.4) return false;
    if (africaF && !v._africa) return false;
    return true;
  });

  const badge = $('fleet-vessel-count');
  if (badge) badge.textContent = fleetFilteredVessels.length;

  // Sort: available first, then by state priority
  fleetFilteredVessels.sort((a,b) => b._avail.score - a._avail.score);

  renderFleetVesselList();
  renderFleetVesselsOnMap();
}

function renderFleetVesselList() {
  const el = $('fleet-vessel-list');
  if (!el) return;
  const slice = fleetFilteredVessels.slice(0, 100);
  if (!slice.length) {
    el.innerHTML = '<div style="padding:12px;font-size:12px;color:var(--text2);text-align:center">No vessels match filters</div>';
    return;
  }
  el.innerHTML = slice.map(v => {
    const s = v._avail.score;
    const dot = s >= 0.6 ? '#4caf50' : s >= 0.3 ? '#ff9800' : '#8b949e';
    const name = escHtml(v.name || `MMSI ${v.mmsi}`);
    const type = escHtml(v.type_specific || v.ship_type || v.type || '');
    const state = escHtml(v.state || '');
    const port  = escHtml(v.port_name || v.port_locode || '');
    const dest  = v.destination ? `→ ${escHtml(v.destination)}` : '';
    const afr   = v._africa ? `<span style="color:#ff9800;font-size:10px">🌍 Africa</span>` : '';
    const scoreBar = Math.round(s * 100);
    return `<div class="vessel-card" style="padding:8px 10px;border-bottom:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="font-size:12px;font-weight:600;color:var(--text1)">${name}</div>
        <button class="btn-secondary" style="font-size:10px;padding:2px 7px;line-height:1.4" onclick="startFleetVoyagePlan(${JSON.stringify({ mmsi: v.mmsi, name: v.name, lat: v.lat, lon: v.lon, port: v.port_name }).replace(/"/g,'&quot;')})">Plan Voyage</button>
      </div>
      <div style="font-size:11px;color:var(--text2);margin-top:2px">${type} · ${state} · ${port} ${dest}</div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:4px">${afr}
        <div style="flex:1;height:3px;background:var(--border);border-radius:2px">
          <div style="width:${scoreBar}%;height:100%;background:${dot};border-radius:2px"></div>
        </div>
        <span style="font-size:10px;color:${dot}">${v._avail.label}</span>
      </div>
    </div>`;
  }).join('');
}

function renderFleetPortsOnMap() {
  fleetPortMarkers.clearLayers();
  (fleetData?.ports || []).forEach(p => {
    if (!p.lat || !p.lon) return;
    const col = p.congestion_score > 60 ? '#ff9800' : p.congestion_score > 40 ? '#ffeb3b' : '#4caf50';
    const c = L.circleMarker([p.lat, p.lon], {
      radius: 14, color: col, weight: 2, opacity: 0.9,
      fillColor: col, fillOpacity: 0.15,
    });
    c.bindTooltip(`<strong>${escHtml(p.name)}</strong><br>${p.total_vessels} vessels · Score ${p.congestion_score}`, { className: 'port-tooltip' });
    fleetPortMarkers.addLayer(c);
  });
}

function renderFleetVesselsOnMap() {
  fleetVesselMarkers.clearLayers();
  fleetFilteredVessels.forEach(v => {
    if (v.lat == null || v.lon == null) return;
    const s = v._avail.score;
    const col = s >= 0.6 ? '#4caf50' : s >= 0.3 ? '#ff9800' : '#8b949e';
    const m = L.circleMarker([v.lat, v.lon], {
      radius: 4, color: '#fff', weight: 1, fillColor: col, fillOpacity: 0.85,
    });
    m.bindTooltip(
      `<strong>${escHtml(v.name || String(v.mmsi))}</strong><br>` +
      `${escHtml(v.type_specific || '')} · ${v.speed || 0} kts<br>` +
      (v.destination ? `→ ${escHtml(v.destination)}` : 'No destination'),
      { sticky: true, className: 'port-tooltip' }
    );
    m.on('click', () => loadVesselDetail(v.mmsi));
    fleetVesselMarkers.addLayer(m);
  });
}

function startFleetVoyagePlan(vessel) {
  fleetVoyageOrigin = vessel;
  const voyageEl = $('fleet-voyage');
  const originEl = $('fleet-voyage-origin');
  if (voyageEl) voyageEl.classList.remove('hidden');
  if (originEl) originEl.textContent = `From: ${vessel.name || vessel.mmsi} · ${vessel.port || ''}`;
  const destInput = $('fleet-voyage-dest');
  const destDD    = $('fleet-voyage-dropdown');
  if (destInput && destDD) {
    createPortAutocomplete(destInput, destDD, port => {
      destInput.value = `${port.name} (${port.locode})`;
      destInput._selectedPort = port;
    });
  }
  const calcBtn = $('fleet-voyage-calc');
  if (calcBtn) {
    // Replace onclick to avoid stacking duplicate listeners
    calcBtn.onclick = calculateFleetVoyage;
  }
  voyageEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function calculateFleetVoyage() {
  let dest = $('fleet-voyage-dest')?._selectedPort;
  // Fallback: look up port by typed name if _selectedPort not set via dropdown click
  if (!dest) {
    const val = ($('fleet-voyage-dest')?.value || '').trim();
    if (val) {
      try {
        const r = await fetch(`/api/ports/search?q=${encodeURIComponent(val)}&limit=1`);
        const ps = await r.json();
        if (ps.length) dest = ps[0];
      } catch { /* ignore */ }
    }
  }
  const origin = fleetVoyageOrigin;
  if (!origin?.lat || !origin?.lon || !dest?.lat || !dest?.lon) {
    $('fleet-voyage-result').innerHTML = '<div style="color:#ef5350;font-size:11px;padding:6px">Select a valid destination port</div>';
    return;
  }
  $('fleet-voyage-result').innerHTML = '<div class="cc-loading-sm"><div class="spinner"></div> Calculating…</div>';
  try {
    const res = await fetch('/api/route/multi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        waypoints: [
          { lat: origin.lat, lon: origin.lon, name: origin.name || 'Vessel' },
          { lat: dest.lat, lon: dest.lon, name: dest.name },
        ],
        speed_knots: 12,
      }),
    });
    const route = await res.json();
    if (!route || route.error) throw new Error(route?.error || 'Route failed');
    const totalNmi = route.legs.reduce((s, l) => s + (l.distance_nmi || 0), 0);
    const etaH = Math.round(totalNmi / 12);
    $('fleet-voyage-result').innerHTML = `
      <div style="padding:8px;background:var(--card-bg);border-radius:6px;margin-top:6px;font-size:12px">
        <div style="font-weight:600;margin-bottom:4px">${escHtml(origin.name||'Vessel')} → ${escHtml(dest.name)}</div>
        <div style="color:var(--text2)">${Math.round(totalNmi)} nmi · ETA ${formatDuration(etaH)}</div>
      </div>`;
    renderFleetRouteOnMap(route, origin, dest);
  } catch (e) {
    $('fleet-voyage-result').innerHTML = `<div style="color:#ef5350;font-size:11px;padding:6px">${escHtml(e.message)}</div>`;
  }
}

function renderFleetRouteOnMap(route, origin, dest) {
  if (fleetRouteLayer) { fleetRouteLayer.remove(); fleetRouteLayer = null; }
  const group = L.featureGroup();
  route.legs.forEach(leg => {
    L.geoJSON(leg.route, { style: { color: '#2196f3', weight: 3, opacity: 0.85 } }).addTo(group);
  });
  L.circleMarker([origin.lat, origin.lon], { radius: 8, color: '#4caf50', fillColor: '#4caf50', fillOpacity: 0.9, weight: 2 })
    .bindTooltip(escHtml(origin.name || 'Vessel'), { className: 'port-tooltip' }).addTo(group);
  L.circleMarker([dest.lat, dest.lon], { radius: 8, color: '#ef5350', fillColor: '#ef5350', fillOpacity: 0.9, weight: 2 })
    .bindTooltip(escHtml(dest.name), { className: 'port-tooltip' }).addTo(group);
  group.addTo(map);
  fleetRouteLayer = group;
  map.fitBounds(group.getBounds(), { padding: [40, 40] });
}

// Wire up fleet filter listeners (run once after DOM ready)
(function initFleetDashboard() {
  ['fleet-filter-type','fleet-filter-port','fleet-filter-state'].forEach(id => {
    $(id)?.addEventListener('change', applyFleetFilters);
  });
  $('fleet-filter-dest')?.addEventListener('input', applyFleetFilters);
  $('fleet-filter-available')?.addEventListener('change', applyFleetFilters);
  $('fleet-filter-africa')?.addEventListener('change', applyFleetFilters);
  $('fleet-arrivals-toggle')?.addEventListener('click', () => {
    const el = $('fleet-arrivals-list');
    if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
  });
})();

// fetchCCPortQuickIntel removed — replaced by Fleet Dashboard

// calculateCommandCenter, showCCError, hideCCError, renderCCRoute removed — replaced by Fleet Dashboard

// ── (legacy CC render stubs — kept to avoid ReferenceErrors) ─────────────
function renderCCRoute(data, speed, fuelCons) {
  const el = $('cc-route-summary'); if (!el) return; el.classList.remove('hidden');
  const t = data.totals;
  const days = t.duration_hours / 24;
  const fuelMT = Math.round(days * fuelCons);
  const fuelCost = Math.round(fuelMT * 600);
  el.innerHTML = `<div class="cc-section-header">Route Summary</div>
    <div class="cc-route-card">
      <div class="cc-route-label">${escHtml(data.legs[0].origin.name)} &rarr; ${escHtml(data.legs[data.legs.length-1].destination.name)}</div>
      <div class="stats-grid">
        <div class="stat-card"><div class="stat-value">${Number(t.distance_nmi).toLocaleString()}</div><div class="stat-label">NMI</div></div>
        <div class="stat-card"><div class="stat-value">${formatDuration(t.duration_hours)}</div><div class="stat-label">at ${speed} kn</div></div>
        <div class="stat-card"><div class="stat-value">${fuelMT} MT</div><div class="stat-label">Fuel</div></div>
        <div class="stat-card"><div class="stat-value">$${(fuelCost/1000).toFixed(0)}K</div><div class="stat-label">Cost</div></div>
      </div>
    </div>`;
}

function renderCCDestIntel(adv) {
  const el = $('cc-dest-detail');
  if (!adv || !adv.monitored) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  const col = levelColor(adv.severity);
  el.innerHTML = `<div class="cc-section-header">Destination Intel</div>
    <div class="cc-dest-card" style="border-left:3px solid ${col}">
      <div class="cc-quick-row"><span>Expected wait</span><span style="font-weight:700;color:${col}">${adv.estimated_wait_hours > 0 ? escHtml(String(adv.estimated_wait_hours))+'h' : 'Minimal'}</span></div>
      <div class="cc-quick-row"><span>Queue</span><span>${escHtml(String(adv.current_queue))} anchored · ${escHtml(String(adv.berths_occupied))} berthed</span></div>
      <div class="cc-quick-row"><span>Score</span><span>${severityIcon(adv.severity)} ${escHtml(String(adv.congestion_score))} ${escHtml(adv.severity)}</span></div>
      <div style="font-size:11px;color:var(--text2);margin-top:4px">${escHtml(adv.recommendation)}</div>
    </div>`;
}

function renderCCVessels(data) {
  const section = $('cc-vessels-section');
  const list = $('cc-vessels-list');
  const count = $('cc-vessels-count');
  if (!data?.vessels?.length) {
    section.classList.remove('hidden');
    count.textContent = '0';
    list.innerHTML = '<div class="cc-muted">No vessels found near origin</div>';
    return;
  }
  section.classList.remove('hidden');
  count.textContent = data.vessels.length;
  const top = data.vessels.slice(0, 10);
  list.innerHTML = top.map(v => {
    const s = v.availability?.score || 0;
    const dots = '\u25CF'.repeat(Math.round(s*5)) + '\u25CB'.repeat(5-Math.round(s*5));
    const cls = s >= 0.6 ? 'good' : s >= 0.3 ? 'warn' : '';
    return `<div class="cc-vessel-card" data-mmsi="${v.mmsi}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:600;font-size:12px">${escHtml(v.name || String(v.mmsi))}</span>
        <span class="${cls}" style="font-size:11px;letter-spacing:1px">${dots}</span>
      </div>
      <div style="font-size:11px;color:var(--text2)">${escHtml(v.type || 'Unknown')} · ${(v.distance||0).toFixed(1)} nm${v.africa_trade ? ' · <span style="color:#ff9800">Africa</span>' : ''}</div>
    </div>`;
  }).join('');
  list.querySelectorAll('.cc-vessel-card').forEach(el => {
    el.addEventListener('click', () => {
      const mmsi = parseInt(el.dataset.mmsi, 10);
      if (mmsi) loadVesselDetail(mmsi);
    });
  });
}

function renderCCWeather(wx) {
  const el = $('cc-weather-content');
  const counts = { LOW:0, MODERATE:0, HIGH:0, SEVERE:0 };
  (wx.weather_points||[]).forEach(p => { if (counts[p.risk_level] !== undefined) counts[p.risk_level]++; });
  let html = `<div style="text-align:center;padding:6px;background:var(--bg3);border-radius:6px;margin-bottom:6px">
    <div style="font-size:14px;font-weight:700;color:${levelColor(wx.overall_risk)}">${escHtml(wx.overall_risk)} RISK</div>
  </div>
  <div class="intel-grid">
    <div class="intel-card"><div class="intel-val good">${counts.LOW}</div><div class="intel-label">Low</div></div>
    <div class="intel-card"><div class="intel-val warn">${counts.MODERATE}</div><div class="intel-label">Mod</div></div>
    <div class="intel-card"><div class="intel-val hot">${counts.HIGH+counts.SEVERE}</div><div class="intel-label">High+</div></div>
  </div>`;
  if (wx.worst_point?.weather) {
    const w = wx.worst_point;
    html += `<div style="font-size:11px;color:var(--text2);margin-top:4px">Worst: ${w.weather.wave_height_m?.toFixed(1)||'?'}m waves at ${w.distance_nmi} nmi</div>`;
  }
  el.innerHTML = html;
}

function renderCCMarket(overview, corridor) {
  const section = $('cc-market-section');
  const el = $('cc-market-content');
  if (!overview && !corridor) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');
  let html = '';
  if (overview?.summary) {
    const s = overview.summary;
    html += `<div class="intel-grid">
      <div class="intel-card"><div class="intel-val">${s.total_vessels_all_ports}</div><div class="intel-label">Fleet</div></div>
      <div class="intel-card"><div class="intel-val warn">${s.total_africa_trade}</div><div class="intel-label">Africa</div></div>
      <div class="intel-card"><div class="intel-val">${s.ports_monitored}</div><div class="intel-label">Ports</div></div>
    </div>`;
  }
  if (corridor?.total_vessels > 0) {
    html += `<div style="font-size:11px;color:#ff9800;margin-top:4px">${corridor.total_vessels} vessels on India-Africa corridor</div>`;
  }
  el.innerHTML = html;
}

// ── Command Center: Map Render Functions ─────────────────────────────────
function renderCCRouteOnMap(data) {
  if (ccRouteLayer) { ccRouteLayer.remove(); ccRouteLayer = null; }
  const group = L.featureGroup();
  const colors = ['#2196f3','#4caf50','#ff9800','#9c27b0','#00bcd4'];
  data.legs.forEach((leg, i) => {
    const col = colors[i % colors.length];
    const coords = leg.route.geometry.coordinates;
    const cumD = buildCumDist(coords);
    const totalNmi = cumD[cumD.length - 1];
    const tip = L.tooltip({ sticky:true, className:'route-tooltip', offset:[14,0] });
    const gj = L.geoJSON(leg.route, { style: { color:col, weight:4, opacity:0.85 } });
    gj.eachLayer(layer => {
      layer.on('mousemove', e => {
        const elapsed = distAlongRoute(e.latlng, coords, cumD);
        const remain = Math.max(0, totalNmi - elapsed);
        const spd = parseFloat($('cc-speed')?.value) || 14;
        tip.setLatLng(e.latlng).setContent(
          `<div class="rt-row"><span class="rt-label">From start</span><span class="rt-val">${elapsed.toFixed(0)} nmi</span></div>` +
          `<div class="rt-row"><span class="rt-label">Remaining</span><span class="rt-val">${remain.toFixed(0)} nmi</span></div>` +
          `<div class="rt-row"><span class="rt-label">ETA</span><span class="rt-val">${formatDuration(remain/spd)}</span></div>`
        ).openOn(map);
      });
      layer.on('mouseout', () => map.closeTooltip(tip));
    });
    gj.addTo(group);
    const oIcon = makeIcon(i===0?'#26a69a':col, String(i+1));
    L.marker([leg.origin.lat, leg.origin.lon], {icon:oIcon}).addTo(group);
    if (i === data.legs.length-1) {
      L.marker([leg.destination.lat, leg.destination.lon], {icon:makeIcon('#ef5350',String(i+2))}).addTo(group);
    }
  });
  group.addTo(map);
  ccRouteLayer = group;
  map.fitBounds(group.getBounds(), {padding:[40,40]});
}

function renderCCPortsOnMap(origCong, destCong) {
  ccPortMarkers.clearLayers();
  [[ccOriginPort, origCong], [ccDestPort, destCong]].forEach(([port, cong]) => {
    if (!port || !cong?.monitored) return;
    const col = levelColor(cong.severity);
    const c = L.circleMarker([port.lat, port.lon], {
      radius:18, color:col, weight:3, opacity:0.8, fillColor:col, fillOpacity:0.2,
    });
    c.bindTooltip(`${escHtml(port.name)}<br>${severityIcon(cong.severity)} ${escHtml(cong.severity)} (${escHtml(String(cong.congestion_score))})`, {className:'port-tooltip'});
    c.on('click', () => openLivePortDetail(port.locode));
    ccPortMarkers.addLayer(c);
  });
  if (ccPortMarkers.getLayers().length) ccPortMarkers.addTo(map);
}

function renderCCVesselsOnMap(data) {
  ccVesselMarkers.clearLayers();
  if (!data?.vessels) return;
  data.vessels.forEach(v => {
    if (v.lat == null || v.lon == null) return;
    const s = v.availability?.score || 0;
    const col = s >= 0.6 ? '#4caf50' : s >= 0.3 ? '#ff9800' : '#8b949e';
    const m = L.circleMarker([v.lat, v.lon], {
      radius:6, color:'#fff', weight:1.5, fillColor:col, fillOpacity:0.9,
    });
    m.bindTooltip(`<strong>${escHtml(v.name||String(v.mmsi))}</strong><br>${escHtml(v.type||'')} · ${v.speed||0} kts`, {sticky:true, className:'port-tooltip'});
    m.on('click', () => loadVesselDetail(v.mmsi));
    ccVesselMarkers.addLayer(m);
  });
  if (ccVesselMarkers.getLayers().length) ccVesselMarkers.addTo(map);
}

function renderCCWeatherOnMap(wx) {
  ccWeatherMarkers.clearLayers();
  (wx.weather_points||[]).forEach(p => {
    if (!p.lat || !p.lon) return;
    const m = L.circleMarker([p.lat, p.lon], {
      radius:5, color:'#fff', weight:1, fillColor:p.risk_color||'#8b949e', fillOpacity:0.85,
    });
    const w = p.weather||{};
    m.bindTooltip(`<strong>${p.distance_nmi} nmi</strong> — ${p.risk_level}<br>Waves: ${w.wave_height_m?.toFixed(1)||'?'}m · Wind: ${Math.round(w.wind_speed_kts||0)} kts`, {sticky:true, className:'port-tooltip'});
    ccWeatherMarkers.addLayer(m);
  });
  if (ccWeatherMarkers.getLayers().length) ccWeatherMarkers.addTo(map);
}

// ── Boot into Port Watch mode ────────────────────────────────────────────
switchMode('portwatch');
