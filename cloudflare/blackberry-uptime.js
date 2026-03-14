// internet-uptime-worker.js
// Cloudflare Worker — Blackberry Hill Internet Uptime Monitor
//
// Probes the Pi's /ping endpoint on a cron schedule,
// stores results in KV, and serves a visual dashboard at /internet.
//
// Required KV bindings:  UPTIME_LOG, UPTIME_CURRENT
//
// Cron trigger: set in Workers dashboard under Triggers
//   Every 2 min: */2 * * * *
//   Every 4 min: */4 * * * *  (recommended — 360 writes/day, well within free tier)

// ─── Configuration ────────────────────────────────────────────────────────────

const TZ = 'America/Los_Angeles';

// Set this to match your cron interval. A gap slightly larger than this
// will be treated as "no data" (genuine outage with no records) rather
// than just a missing poll. Recommend: cron interval + 1 minute of headroom.
//
// Examples:
//   Cron every 1 min  → POLL_INTERVAL_MS = 1 * 60 * 1000
//   Cron every 2 min  → POLL_INTERVAL_MS = 2 * 60 * 1000
//   Cron every 4 min  → POLL_INTERVAL_MS = 4 * 60 * 1000
const POLL_INTERVAL_MS = 6 * 60 * 1000;

// Gaps up to this size are filled with the last known state rather than
// shown as grey "no data". Set to poll interval + 1 minute headroom.
const GAP_THRESHOLD_MS = POLL_INTERVAL_MS + 60 * 1000;

// KV TTL — how long to keep entries (28 days)
const KV_TTL_SECONDS = 60 * 60 * 24 * 28;

// ─── Entry Point ─────────────────────────────────────────────────────────────

export default {
  // Cron trigger — fires on schedule set in Workers dashboard
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(recordStatus(env));
  },

  async fetch(request, env, _ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/internet') {
      return serveDashboard(env);
    }
    if (url.pathname === '/internet.json') {
      return serveJSON(env);
    }
    if (url.pathname === '/internet/trigger') {
      // Manual trigger for testing — visit this URL to seed initial data
      await recordStatus(env);
      return new Response('OK - status recorded', { status: 200 });
    }
    return new Response('Not found', { status: 404 });
  }
};

// ─── Data Recording ───────────────────────────────────────────────────────────

const PING_URL = 'https://onblackberryhill.com/ping';

async function recordStatus(env) {
  let up = false;
  try {
    const res = await fetch(PING_URL, { signal: AbortSignal.timeout(10000) });
    up = res.ok;
  } catch (e) {
    up = false;
  }
  const ts = new Date().toISOString();
  const entry = JSON.stringify({ ts, up });

  // ISO timestamp as key — sorts lexicographically = chronological order in KV list
  const key = `log:${ts}`;
  await env.UPTIME_LOG.put(key, entry, { expirationTtl: KV_TTL_SECONDS });
  await env.UPTIME_CURRENT.put('latest', entry);
}

// ─── KV Helpers ───────────────────────────────────────────────────────────────

// KV list() has a hard limit of 1000 keys per call.
// At 4-min intervals, 7 days = 2,520 entries — requires pagination.
async function listAllKeys(env, prefix) {
  let keys = [];
  let cursor = undefined;
  do {
    const opts = { prefix, limit: 1000 };
    if (cursor) opts.cursor = cursor;
    const result = await env.UPTIME_LOG.list(opts);
    keys = keys.concat(result.keys);
    cursor = result.list_complete ? undefined : result.cursor;
  } while (cursor);
  return keys;
}

// ─── JSON Endpoint ────────────────────────────────────────────────────────────

async function serveJSON(env) {
  const keys = await listAllKeys(env, 'log:');
  const entries = await Promise.all(
    keys.map(k => env.UPTIME_LOG.get(k.name).then(JSON.parse))
  );
  return new Response(JSON.stringify(entries), {
    headers: { 'Content-Type': 'application/json' }
  });
}

// ─── Formatting Helpers ───────────────────────────────────────────────────────

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('en-US', {
    hour: 'numeric', minute: '2-digit', hour12: true, timeZone: TZ
  });
}

function formatDay(ts) {
  return new Date(ts).toLocaleDateString('en-US', {
    weekday: 'short', month: 'numeric', day: 'numeric', timeZone: TZ
  });
}

function formatDowntime(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m down`;
  return `${h}h ${m.toString().padStart(2, '0')}m down`;
}

function formatDateTime(ts) {
  return new Date(ts).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true, timeZone: TZ
  });
}

// Estimate actual poll interval from the median gap between recent entries.
function estimatePollInterval(entries) {
  const recent = entries.slice(-10);
  if (recent.length < 2) return null;
  const gaps = [];
  for (let i = 1; i < recent.length; i++) {
    gaps.push(new Date(recent[i].ts).getTime() - new Date(recent[i - 1].ts).getTime());
  }
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

// Returns outage intervals from a sorted entry array as {start, end} ms timestamps.
// end is null if the outage is ongoing (last entry is still down).
function downtimeIntervals(entries) {
  const intervals = [];
  let downStart = null;
  for (const e of entries) {
    const t = new Date(e.ts).getTime();
    if (!e.up && downStart === null) {
      downStart = t;
    } else if (e.up && downStart !== null) {
      intervals.push({ start: downStart, end: t });
      downStart = null;
    }
  }
  if (downStart !== null) {
    intervals.push({ start: downStart, end: null });
  }
  return intervals;
}

// ─── Dashboard Components ─────────────────────────────────────────────────────

// Uses timestamp-based accounting so downtime matches the outage list exactly,
// regardless of whether the poll interval has changed over time.
function statLine(arr, now) {
  if (arr.length === 0) return '<span style="color:#555">— no data</span>';
  const intervals = downtimeIntervals(arr);
  const downMs = intervals.reduce((sum, o) => sum + ((o.end ?? now) - o.start), 0);
  const downMins = Math.round(downMs / 60000);
  const windowMs = now - new Date(arr[0].ts).getTime();
  const p = windowMs > 0 ? ((windowMs - downMs) / windowMs) * 100 : 100;
  const pctStr = p.toFixed(1) + '%';
  const downStr = downMins > 0 ? ' — ' + formatDowntime(downMins) : '';
  const color = p >= 99 ? '#22c55e' : '#ef4444';
  return `<span style="color:${color}">${pctStr} uptime${downStr}</span>`;
}

// Renders a horizontal timeline bar.
// Buckets smaller than GAP_THRESHOLD_MS that have no data inherit the last
// known state, avoiding spurious grey gaps from the poll interval.
// Buckets wider than GAP_THRESHOLD_MS that have no data show as grey,
// indicating a genuine period with no records (e.g. Pi was fully offline).
function makeTimeline(data, buckets, totalMs, now) {
  if (data.length === 0) return '<div style="color:#555;font-size:0.85rem">No data yet</div>';

  const bucketMs = totalMs / buckets;
  const bars = [];
  let lastKnownState = 'nodata';

  for (let i = 0; i < buckets; i++) {
    const bucketStart = now - totalMs + i * bucketMs;
    const bucketEnd   = bucketStart + bucketMs;
    const inBucket = data.filter(e => {
      const t = new Date(e.ts).getTime();
      return t >= bucketStart && t < bucketEnd;
    });

    let state;
    if (inBucket.length === 0) {
      // Small gap (expected between polls) — carry forward last known state
      // Large gap (genuine outage or no data) — show as no-data grey
      state = bucketMs <= GAP_THRESHOLD_MS ? lastKnownState : 'nodata';
    } else {
      const ratio = inBucket.filter(e => e.up).length / inBucket.length;
      state = ratio > 0 ? 'up' : 'down';
      lastKnownState = state;
    }

    const color = state === 'up'   ? '#22c55e'
                : state === 'down' ? '#ef4444'
                : '#333';

    const pctLeft  = (i / buckets * 100).toFixed(3);
    const pctWidth = (1 / buckets * 100).toFixed(3);

    let tooltip = '';
    if (state === 'down') {
      const label = `${formatTime(bucketStart)} – ${formatTime(bucketEnd)}`;
      tooltip = `data-tip="${label}"`;
    }

    bars.push(
      `<div class="bar ${state === 'down' ? 'has-tip' : ''}" `
      + `style="position:absolute;left:${pctLeft}%;width:${pctWidth}%;height:100%;background:${color}" `
      + `${tooltip}></div>`
    );
  }

  return `<div style="position:relative;width:100%;height:32px;background:#222;border-radius:4px;overflow:visible">${bars.join('')}</div>`;
}

function timeLabels(totalMs, count, now) {
  const labels = [];
  for (let i = 0; i <= count; i++) {
    const t = now - totalMs + i * (totalMs / count);
    labels.push(`<span>${formatTime(t)}</span>`);
  }
  return `<div style="display:flex;justify-content:space-between;font-size:0.7rem;color:#555;margin-top:2px">${labels.join('')}</div>`;
}

function dayLabels(totalDays, stepDays, now, hour) {
  const labels = [];
  for (let i = totalDays; i >= 0; i -= stepDays) {
    const t = now - i * 24 * hour;
    labels.push(`<span>${formatDay(t)}</span>`);
  }
  return `<div style="display:flex;justify-content:space-between;font-size:0.7rem;color:#555;margin-top:2px">${labels.join('')}</div>`;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

async function serveDashboard(env) {
  const keys = await listAllKeys(env, 'log:');
  const entries = await Promise.all(
    keys.map(k => env.UPTIME_LOG.get(k.name).then(v => JSON.parse(v)))
  );

  const now = Date.now();
  const hour = 60 * 60 * 1000;

  const last4h   = entries.filter(e => now - new Date(e.ts).getTime() <=  4      * hour);
  const last24h  = entries.filter(e => now - new Date(e.ts).getTime() <= 24      * hour);
  const last28d  = entries.filter(e => now - new Date(e.ts).getTime() <= 28 * 24 * hour);

  const latest = entries[entries.length - 1];

  const estimatedMs = estimatePollInterval(entries);
  const pollMins = estimatedMs ? Math.round(estimatedMs / 60000) : Math.round(POLL_INTERVAL_MS / 60000);

  const outages = downtimeIntervals(last28d);

  const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Blackberry Hill — Internet</title>
  <!-- <meta http-equiv="refresh" content="60"> -->
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🌊</text></svg>">
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, sans-serif; background: #0f0f0f; color: #e2e8f0; padding: 1.5rem; max-width: 860px; margin: auto; }
    h1 { color: #60a5fa; margin-bottom: 0.25rem; }
    .subtitle { color: #64748b; font-size: 0.85rem; margin-bottom: 2rem; }
    .current { font-size: 1.4rem; font-weight: bold; margin-bottom: 2rem; }
    .up   { color: #22c55e; }
    .down { color: #ef4444; }
    .card { background: #1e1e1e; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.25rem; }
    .card h2 { font-size: 0.9rem; color: #94a3b8; margin: 0 0 0.25rem 0; text-transform: uppercase; letter-spacing: 0.05em; }
    .card .pct { font-size: 1.3rem; font-weight: bold; margin-bottom: 0.75rem; }
    .legend { display: flex; gap: 1rem; margin-top: 0.75rem; font-size: 0.75rem; color: #64748b; }
    .legend span { display: flex; align-items: center; gap: 0.3rem; }
    .dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

    /* Hover tooltips — pure CSS, no JavaScript */
    .bar.has-tip { cursor: crosshair; }
    .bar.has-tip:hover::after {
      content: attr(data-tip);
      position: absolute;
      bottom: 38px;
      left: 50%;
      transform: translateX(-50%);
      background: #1e293b;
      color: #f1f5f9;
      font-size: 0.75rem;
      white-space: nowrap;
      padding: 4px 8px;
      border-radius: 4px;
      border: 1px solid #334155;
      pointer-events: none;
      z-index: 10;
    }
    .bar.has-tip:hover::before {
      content: '';
      position: absolute;
      bottom: 32px;
      left: 50%;
      transform: translateX(-50%);
      border: 5px solid transparent;
      border-top-color: #334155;
      z-index: 10;
    }
  </style>
</head>
<body>
  <h1>🌊 Blackberry Hill</h1>
  <div class="subtitle">Internet connectivity · checks every ~${pollMins} min</div>

  <div class="current ${latest?.up ? 'up' : 'down'}">
    ${latest?.up ? '● Online' : '● Offline'}
    <span style="font-size:0.9rem;color:#64748b;font-weight:normal">
      — last checked ${latest ? formatTime(latest.ts) : 'never'} PT
    </span>
  </div>

  <div class="card">
    <h2>Past 4 Hours</h2>
    <div class="pct">${statLine(last4h, now)}</div>
    ${makeTimeline(last4h, 240, 4 * hour, now)}
    ${timeLabels(4 * hour, 4, now)}
    <div class="legend">
      <span><i class="dot" style="background:#22c55e"></i> Up</span>
      <span><i class="dot" style="background:#ef4444"></i> Down</span>
      <span><i class="dot" style="background:#333"></i> No data</span>
    </div>
  </div>

  <div class="card">
    <h2>Past 24 Hours</h2>
    <div class="pct">${statLine(last24h, now)}</div>
    ${makeTimeline(last24h, 288, 24 * hour, now)}
    ${timeLabels(24 * hour, 6, now)}
    <div class="legend">
      <span><i class="dot" style="background:#22c55e"></i> Up</span>
      <span><i class="dot" style="background:#ef4444"></i> Down</span>
      <span><i class="dot" style="background:#333"></i> No data</span>
    </div>
  </div>

  <div class="card">
    <h2>Past 28 Days</h2>
    <div class="pct">${statLine(last28d, now)}</div>
    ${makeTimeline(last28d, 336, 28 * 24 * hour, now)}
    ${dayLabels(28, 7, now, hour)}
    <div class="legend">
      <span><i class="dot" style="background:#22c55e"></i> Up</span>
      <span><i class="dot" style="background:#ef4444"></i> Down</span>
      <span><i class="dot" style="background:#333"></i> No data</span>
    </div>
  </div>

  <div class="card">
    <h2>Outages — Past 28 Days</h2>
    ${outages.length === 0
      ? '<div style="color:#22c55e;font-size:0.9rem">No outages recorded</div>'
      : `<table style="width:100%;border-collapse:collapse;font-size:0.85rem">
          <thead>
            <tr style="color:#64748b;text-align:left">
              <th style="padding:0.3rem 0.6rem 0.3rem 0;font-weight:normal">Start</th>
              <th style="padding:0.3rem 0.6rem;font-weight:normal">End</th>
              <th style="padding:0.3rem 0 0.3rem 0.6rem;font-weight:normal;text-align:right">Duration</th>
            </tr>
          </thead>
          <tbody>
            ${[...outages].reverse().map(o => {
              const durMins = Math.round(((o.end ?? now) - o.start) / 60000);
              const endLabel = o.end ? formatDateTime(o.end) : '<span style="color:#ef4444">ongoing</span>';
              return `<tr style="border-top:1px solid #2d2d2d">
                <td style="padding:0.4rem 0.6rem 0.4rem 0">${formatDateTime(o.start)}</td>
                <td style="padding:0.4rem 0.6rem">${endLabel}</td>
                <td style="padding:0.4rem 0 0.4rem 0.6rem;text-align:right;color:#94a3b8">${formatDowntime(durMins)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>`
    }
  </div>

  <div style="font-size:0.75rem;color:#334155;margin-top:1rem;display:flex;gap:1.5rem">
    <a href="/timelapse" style="color:#334155">Timelapse</a>
    <a href="/internet.json" style="color:#334155">JSON</a>
  </div>
</body>
</html>`;

  return new Response(html, { headers: { 'Content-Type': 'text/html' } });
}