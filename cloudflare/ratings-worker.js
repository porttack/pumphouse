/**
 * pumphouse-ratings Worker
 *
 * Handles rating reads/writes at the Cloudflare edge so the Pi is not
 * involved in rating operations.
 *
 * Routes (all other requests pass through to the Pi via tunnel):
 *   GET  /api/ratings/YYYY-MM-DD   → read rating from KV
 *   POST /timelapse/YYYY-MM-DD/rate → write rating to KV
 *
 * KV binding: RATINGS (set in Worker Settings → Variables → KV Namespace Bindings)
 *
 * Deploy: paste this file into the Worker editor in the Cloudflare dashboard,
 * or use `wrangler deploy` with the wrangler.toml in this directory.
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Redirect root to /timelapse (belt-and-suspenders; redirect rule handles it too)
    if (url.pathname === '/') {
      return Response.redirect(url.origin + '/timelapse', 301);
    }

    // GET /api/ratings/YYYY-MM-DD  →  read from KV
    const getMatch = url.pathname.match(/^\/api\/ratings\/(\d{4}-\d{2}-\d{2})$/);
    if (getMatch && request.method === 'GET') {
      return handleGet(getMatch[1], env);
    }

    // POST /timelapse/YYYY-MM-DD/rate  →  write to KV (Pi not involved)
    const postMatch = url.pathname.match(/^\/timelapse\/(\d{4}-\d{2}-\d{2})\/rate$/);
    if (postMatch && request.method === 'POST') {
      return handlePost(postMatch[1], request, env);
    }

    // Everything else passes through to Pi via tunnel
    return fetch(request);
  }
};

async function handleGet(dateStr, env) {
  const data = await readRating(dateStr, env);
  const count = data.count || 0;
  const avg   = count > 0 ? Math.round(data.sum / count * 10) / 10 : null;
  return new Response(JSON.stringify({ count, avg }), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=60',
      'Access-Control-Allow-Origin': 'https://onblackberryhill.com',
    },
  });
}

async function handlePost(dateStr, request, env) {
  const cookie = request.headers.get('Cookie') || '';
  if (cookie.includes(`tl_rated_${dateStr}=`)) {
    // Already rated — return current stats without writing
    return new Response(JSON.stringify(await readRating(dateStr, env)), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  let body;
  try { body = await request.json(); } catch {
    return new Response('Bad request', { status: 400 });
  }
  const rating = parseInt(body.rating);
  if (![3, 4, 5].includes(rating)) {
    return new Response('Rating must be 3, 4, or 5', { status: 400 });
  }

  const current = await readRating(dateStr, env);
  const updated = { count: current.count + 1, sum: current.sum + rating };
  await env.RATINGS.put(dateStr, JSON.stringify(updated));

  const avg = Math.round(updated.sum / updated.count * 10) / 10;
  const resp = new Response(
    JSON.stringify({ count: updated.count, avg }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  const expires = new Date(Date.now() + 365 * 24 * 3600 * 1000).toUTCString();
  resp.headers.append('Set-Cookie',
    `tl_rated_${dateStr}=${rating}; Path=/; Expires=${expires}; SameSite=Lax`);
  return resp;
}

async function readRating(dateStr, env) {
  const raw = await env.RATINGS.get(dateStr);
  return raw ? JSON.parse(raw) : { count: 0, sum: 0 };
}
