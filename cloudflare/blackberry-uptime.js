// internet-uptime-worker.js
// Cloudflare Worker — Blackberry Hill Internet Uptime Monitor
//
// Probes the Pi's /ping endpoint on a cron schedule,
// stores results in KV, and serves a visual dashboard at /internet.
//
// Required KV bindings:  UPTIME_LOG, UPTIME_CURRENT
//
// Cron trigger: set in Workers dashboard under Triggers
//   Every 1 min: * * * * *
//
// Only writes to KV on state changes (up→down or down→up), so
// writes are ~2–10/day rather than once per poll. Free tier = 1,000/day.

// ─── Configuration ────────────────────────────────────────────────────────────

const TZ = 'America/Los_Angeles';

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
    // ── ONE-TIME SEED — remove this block once /internet/seed has been run ──
    if (url.pathname === '/internet/seed') {
      return seedData(env);
    }
    // ── END ONE-TIME SEED ────────────────────────────────────────────────────
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

  // Read last known state — cheap KV read, ~1,440/day, well within free tier.
  // Only write to the log when state changes (up↔down), keeping writes ~2–10/day.
  const lastJson = await env.UPTIME_CURRENT.get('latest');
  const last = lastJson ? JSON.parse(lastJson) : null;

  if (!last || last.up !== up) {
    const entry = JSON.stringify({ ts, up });
    const key = `log:${ts}`;
    await env.UPTIME_LOG.put(key, entry, { expirationTtl: KV_TTL_SECONDS });
    await env.UPTIME_CURRENT.put('latest', entry);
  }
}

// ─── KV Helpers ───────────────────────────────────────────────────────────────

// With state-change-only logging, entries are sparse (tens per month, not thousands).
// Pagination is kept for safety in case of historical dense data or backfills.
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

// Uses timestamp-based accounting so downtime matches the outage list exactly.
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
// Entries are sparse (state-change-only), so empty buckets always carry forward
// the last known state. Grey "no data" only appears before the first ever entry.
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
      // Carry forward — no entry means no state change, not missing data
      state = lastKnownState;
    } else {
      const ratio = inBucket.filter(e => e.up).length / inBucket.length;
      // 'partial' (amber) = any down entry in bucket; 'down' = all down
      state = ratio >= 1 ? 'up' : ratio > 0 ? 'partial' : 'down';
      lastKnownState = state;
    }

    const color = state === 'up'      ? '#22c55e'
                : state === 'partial' ? '#f59e0b'
                : state === 'down'    ? '#ef4444'
                : '#333';

    const pctLeft  = (i / buckets * 100).toFixed(3);
    const pctWidth = (1 / buckets * 100).toFixed(3);

    let tooltip = '';
    if (state === 'down' || state === 'partial') {
      const label = `${formatTime(bucketStart)} – ${formatTime(bucketEnd)}`;
      tooltip = `data-tip="${label}"`;
    }

    bars.push(
      `<div class="bar ${state === 'down' || state === 'partial' ? 'has-tip' : ''}" `
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

// Returns entries within the window, prepending the last entry before the window
// (clamped to the window start) so that makeTimeline and statLine both see the
// correct initial state even when no state change has occurred within the window.
function windowEntries(entries, windowMs, now) {
  const cutoff = now - windowMs;
  const inWindow = entries.filter(e => new Date(e.ts).getTime() >= cutoff);
  const seed = [...entries].reverse().find(e => new Date(e.ts).getTime() < cutoff);
  if (seed) return [{ ...seed, ts: new Date(cutoff).toISOString() }, ...inWindow];
  return inWindow;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

async function serveDashboard(env) {
  const keys = await listAllKeys(env, 'log:');
  const entries = await Promise.all(
    keys.map(k => env.UPTIME_LOG.get(k.name).then(v => JSON.parse(v)))
  );

  const now = Date.now();
  const hour = 60 * 60 * 1000;

  const last4h   = windowEntries(entries,  4      * hour, now);
  const last24h  = windowEntries(entries, 24      * hour, now);
  const last28d  = windowEntries(entries, 28 * 24 * hour, now);

  const latest = entries[entries.length - 1];
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
  <div class="subtitle">Internet connectivity · checks every 1 min · <a href="/timelapse" style="color:#64748b">Timelapse</a> · <a href="https://forecast.weather.gov/MapClick.php?lat=44.64196&lon=-124.04110" style="color:#64748b">Weather</a></div>

  <div class="current ${latest?.up ? 'up' : 'down'}">
    ${latest?.up ? '● Online' : '● Offline'}
    <span style="font-size:0.9rem;color:#64748b;font-weight:normal">
      — since ${latest ? formatDateTime(latest.ts) : 'unknown'} PT
    </span>
  </div>

  <div class="card">
    <h2>Past 4 Hours</h2>
    <div class="pct">${statLine(last4h, now)}</div>
    ${makeTimeline(last4h, 240, 4 * hour, now)}
    ${timeLabels(4 * hour, 4, now)}
    <div class="legend">
      <span><i class="dot" style="background:#22c55e"></i> Up</span>
      <span><i class="dot" style="background:#f59e0b"></i> Partial outage</span>
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
      <span><i class="dot" style="background:#f59e0b"></i> Partial outage</span>
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
      <span><i class="dot" style="background:#f59e0b"></i> Partial outage</span>
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

</body>
</html>`;

  return new Response(html, { headers: { 'Content-Type': 'text/html' } });
}

// ── ONE-TIME SEED — remove this function once /internet/seed has been run ────
//
// Visit /internet/seed once after deploying. It will:
//   1. Delete all log entries before 2026-03-12 (old dirty tunnel-status data)
//   2. Write baseline up:true at 2026-02-14 (28-day window start)
//   3. Write 8 outage pairs (down + up) from March 12 derived from events.csv
//      TANK_OUTAGE_RECOVERY entries (converted from PDT to UTC)
//
// Outages 9-11 on March 12 (after 9:53 PM local) are already in KV from
// the live worker and are not re-seeded here.
async function seedData(env) {
  const BASELINE_TS  = '2026-02-14T00:00:00.000Z';
  const BASELINE_KEY = `log:${BASELINE_TS}`;
  const CUTOFF_TS    = '2026-03-12T00:00:00.000Z';

  const existing = await env.UPTIME_LOG.get(BASELINE_KEY);
  if (existing) {
    return new Response('Already seeded — baseline entry exists, nothing to do.', { status: 200 });
  }

  // Delete all log entries before the cutoff (old dense / tunnel-status-era data)
  let deleted = 0;
  let cursor;
  do {
    const opts = { prefix: 'log:', limit: 1000 };
    if (cursor) opts.cursor = cursor;
    const result = await env.UPTIME_LOG.list(opts);
    for (const k of result.keys) {
      const ts = k.name.slice(4); // strip 'log:' prefix
      if (ts >= CUTOFF_TS) break; // keys are sorted lexicographically
      await env.UPTIME_LOG.delete(k.name);
      deleted++;
    }
    cursor = result.list_complete ? undefined : result.cursor;
  } while (cursor);

  // Write baseline: "up" at the start of the 28-day window
  await env.UPTIME_LOG.put(BASELINE_KEY,
    JSON.stringify({ ts: BASELINE_TS, up: true }), { expirationTtl: KV_TTL_SECONDS });

  // March 12 outages derived from events.csv TANK_OUTAGE_RECOVERY entries (PDT→UTC).
  // Each pair: go down at .start, come back up at .end.
  const outages = [
    { start: '2026-03-12T10:32:03.000Z', end: '2026-03-12T11:12:45.000Z' }, // 40.7 min
    { start: '2026-03-12T12:12:56.000Z', end: '2026-03-12T12:18:08.000Z' }, // 5.2 min
    { start: '2026-03-12T12:30:27.000Z', end: '2026-03-12T18:54:27.000Z' }, // 6.4 hrs
    { start: '2026-03-12T18:59:49.000Z', end: '2026-03-12T19:15:19.000Z' }, // 15.5 min
    { start: '2026-03-12T19:25:09.000Z', end: '2026-03-12T19:40:33.000Z' }, // 15.4 min
    { start: '2026-03-12T19:45:38.000Z', end: '2026-03-12T19:55:44.000Z' }, // 10.1 min
    { start: '2026-03-12T19:58:05.000Z', end: '2026-03-12T21:22:05.000Z' }, // 1.4 hrs
    { start: '2026-03-12T21:24:44.000Z', end: '2026-03-12T22:48:44.000Z' }, // 1.4 hrs
  ];
  for (const o of outages) {
    await env.UPTIME_LOG.put(`log:${o.start}`,
      JSON.stringify({ ts: o.start, up: false }), { expirationTtl: KV_TTL_SECONDS });
    await env.UPTIME_LOG.put(`log:${o.end}`,
      JSON.stringify({ ts: o.end, up: true }), { expirationTtl: KV_TTL_SECONDS });
  }

  return new Response(
    `Seeded OK — deleted ${deleted} old entries, wrote baseline + ${outages.length} outages`,
    { status: 200 }
  );
}
// ── END ONE-TIME SEED ─────────────────────────────────────────────────────────
