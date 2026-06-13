/**
 * Cloudflare Worker — R2 Upload Proxy + CF Stream Live Input Provisioner
 *
 * Two responsibilities:
 *   A) Stream browser uploads directly into the R2 bucket (post-game 360° video)
 *   B) Mint Cloudflare Stream Live Inputs on demand for live game streaming
 *
 * Auth for both: shared coach password.
 *
 * DEPLOY:
 *   1. Cloudflare Dashboard → Workers & Pages → Create → "Create Worker"
 *   2. Paste this file, click Save and Deploy
 *   3. Worker → Settings → Variables and Secrets, add SECRETS:
 *        - COACH_PASS    = "ManUtd2016"
 *        - CF_API_TOKEN  = <token with Stream:Edit permission>  (only needed for Live Inputs)
 *        - CF_ACCOUNT_ID = <your account id>                    (only needed for Live Inputs)
 *   4. Worker → Settings → Bindings → R2 Bucket Bindings:
 *        - Variable name: BUCKET
 *        - R2 bucket:     stompers-videos
 *   5. Copy the Worker URL and paste it into R2_UPLOAD_WORKER in soccer_team_app.jsx
 *
 * ENDPOINTS:
 *   POST /upload-url       { password, filename } → { uploadUrl, publicUrl }
 *   PUT  /put/:filename?auth=<pass>  (raw file body) → { ok, publicUrl }
 *   POST /live-input       { password, name }     → { uid, rtmpsUrl, streamKey, hlsUrl, dashUrl }
 *   POST /live-input/:uid/delete  { password }    → { ok }
 *   POST /game/:id/videos/delete  { password }    → { ok, deleted } (wipes R2 tv_view/<id>/* + clips/<id>/*)
 */

const PUBLIC_BASE = 'https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders },
  });

async function createLiveInput(env, name) {
  const res = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}/stream/live_inputs`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.CF_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        meta: { name: name || 'stompers-live' },
        recording: { mode: 'automatic' }, // auto-record to a VOD when the stream ends
        defaultCreator: 'stompers-match-tracker',
      }),
    }
  );
  const data = await res.json();
  if (!res.ok || !data?.success) {
    throw new Error(data?.errors?.[0]?.message || `live_input create failed (${res.status})`);
  }
  const r = data.result;
  return {
    uid: r.uid,
    rtmpsUrl: r.rtmps?.url || 'rtmps://live.cloudflare.com:443/live/',
    streamKey: r.rtmps?.streamKey,
    // Cloudflare Stream HLS playback (works even before any recording exists)
    hlsUrl: `https://customer-${env.CF_STREAM_SUBDOMAIN || ''}.cloudflarestream.com/${r.uid}/manifest/video.m3u8`,
    // Fallback that works without subdomain: use the iframe form
    iframeUrl: `https://iframe.videodelivery.net/${r.uid}`,
    customerCode: env.CF_STREAM_SUBDOMAIN || null,
  };
}

async function deleteLiveInput(env, uid) {
  const res = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}/stream/live_inputs/${uid}`,
    {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${env.CF_API_TOKEN}` },
    }
  );
  if (!res.ok && res.status !== 404) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.errors?.[0]?.message || `live_input delete failed (${res.status})`);
  }
  return true;
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    // ---- POST /upload-url ----
    if (request.method === 'POST' && url.pathname === '/upload-url') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, filename } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
      if (!filename) return json({ error: 'filename required' }, 400);

      const safe = String(filename).replace(/[^a-zA-Z0-9._-]/g, '_');
      return json({
        uploadUrl: `${url.origin}/put/${encodeURIComponent(safe)}?auth=${encodeURIComponent(password)}`,
        publicUrl: `${PUBLIC_BASE}/${safe}`,
        key: safe,
      });
    }

    // ---- PUT /put/:filename ----
    if (request.method === 'PUT' && url.pathname.startsWith('/put/')) {
      const authParam = url.searchParams.get('auth');
      if (!authParam || authParam !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
      const key = decodeURIComponent(url.pathname.slice('/put/'.length));
      if (!key) return json({ error: 'no key' }, 400);
      const contentType = request.headers.get('content-type') || 'video/mp4';
      await env.BUCKET.put(key, request.body, { httpMetadata: { contentType } });
      return json({ ok: true, publicUrl: `${PUBLIC_BASE}/${key}` });
    }

    // ---- POST /live-input ----
    if (request.method === 'POST' && url.pathname === '/live-input') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, name } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
      if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID) {
        return json({ error: 'CF Stream not configured on worker (missing CF_API_TOKEN or CF_ACCOUNT_ID)' }, 500);
      }
      try {
        const info = await createLiveInput(env, name);
        return json(info);
      } catch (err) {
        return json({ error: String(err.message || err) }, 502);
      }
    }

    // ---- POST /live-input/:uid/delete ----
    const delMatch = url.pathname.match(/^\/live-input\/([a-zA-Z0-9_-]+)\/delete$/);
    if (request.method === 'POST' && delMatch) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
      if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID) {
        return json({ error: 'CF Stream not configured on worker' }, 500);
      }
      try {
        await deleteLiveInput(env, delMatch[1]);
        return json({ ok: true });
      } catch (err) {
        return json({ error: String(err.message || err) }, 502);
      }
    }

    // ---- POST /game/:id/videos/delete ----
    // Wipes everything in R2 under tv_view/<id>/* and clips/<id>/*. Used by
    // the coach "Delete game" + "Delete videos only" actions in the app. The
    // app handles Firestore cleanup separately (subcollections + game doc).
    const wipeMatch = url.pathname.match(/^\/game\/([a-zA-Z0-9_-]+)\/videos\/delete$/);
    if (request.method === 'POST' && wipeMatch) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);

      const gameId = wipeMatch[1];
      const prefixes = [`tv_view/${gameId}/`, `clips/${gameId}/`];
      let deleted = 0;
      try {
        for (const prefix of prefixes) {
          let cursor = undefined;
          // R2 list is paginated at 1000; loop until truncated=false.
          do {
            const listed = await env.BUCKET.list({ prefix, cursor, limit: 1000 });
            const keys = (listed.objects || []).map(o => o.key);
            if (keys.length) {
              await env.BUCKET.delete(keys);
              deleted += keys.length;
            }
            cursor = listed.truncated ? listed.cursor : undefined;
          } while (cursor);
        }
        return json({ ok: true, deleted });
      } catch (err) {
        return json({ error: String(err.message || err) }, 500);
      }
    }

    // ---- POST /game/:id/voice/delete ----
    // Wipes the coach's voice recordings for a game: flat keys
    // voice_<gameId>_*.{m4a,webm} (the upload path sanitizes slashes, so
    // these do NOT live under a folder prefix). Deliberately SEPARATE from
    // /videos/delete: "Delete game" calls both, "Delete videos only" must
    // keep the voice — it's source data like the event log, not a render.
    const voiceMatch = url.pathname.match(/^\/game\/([a-zA-Z0-9_-]+)\/voice\/delete$/);
    if (request.method === 'POST' && voiceMatch) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);

      const prefix = `voice_${voiceMatch[1]}_`;
      let deleted = 0;
      try {
        let cursor = undefined;
        do {
          const listed = await env.BUCKET.list({ prefix, cursor, limit: 1000 });
          const keys = (listed.objects || []).map(o => o.key);
          if (keys.length) {
            await env.BUCKET.delete(keys);
            deleted += keys.length;
          }
          cursor = listed.truncated ? listed.cursor : undefined;
        } while (cursor);
        return json({ ok: true, deleted });
      } catch (err) {
        return json({ error: String(err.message || err) }, 500);
      }
    }

    return json({ error: 'not found' }, 404);
  },
};

