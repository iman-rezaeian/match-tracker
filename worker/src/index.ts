/**
 * Cloudflare Worker — R2 Upload (Presigned URL) + CF Stream Live Input Provisioner
 *
 * CHANGE FROM PREVIOUS VERSION:
 *   /upload-url now returns a PRESIGNED S3 URL that lets the browser PUT directly
 *   to R2 (up to 5 GB). The old /put/:filename proxy route is kept as fallback
 *   for files under 100 MB but is no longer the primary path.
 *
 * AUTH: callers present a Firebase ID token (`idToken`), verified here against
 * Google's public signing certs. A shared password cannot work for this app —
 * the client is a static bundle, so any constant it holds is readable by anyone
 * who opens devtools. COACH_PASS is still accepted as a fallback while clients
 * update; remove it (and the secret) once no caller sends `password`.
 *
 * SECRETS (Worker → Settings → Variables and Secrets):
 *   - COACH_PASS         = <legacy shared password — DEPRECATED, remove after rollout>
 *   - CF_API_TOKEN       = <token with Stream:Edit>  (for Live Inputs)
 *   - CF_ACCOUNT_ID      = <your Cloudflare account id>
 *   - R2_ACCESS_KEY_ID   = <R2 S3 API token access key>
 *   - R2_SECRET_ACCESS_KEY = <R2 S3 API token secret key>
 *
 * BINDINGS (Worker → Settings → Bindings):
 *   - R2 Bucket: variable name "BUCKET", bucket "stompers-videos"
 *
 * CRON: every 15 min, mirrors the TeamSnap iCal feed into Firestore
 *   (see teamsnap.ts — needs TEAMSNAP_ICS_URL + FIREBASE_SA_JSON secrets).
 *
 * ENDPOINTS (all take { idToken } — legacy { password } still honoured):
 *   POST /upload-url       { idToken, filename, contentType? } → { uploadUrl (presigned S3), publicUrl }
 *   PUT  /put/:filename?auth=<idToken>  (raw body, fallback ≤100MB) → { ok, publicUrl }
 *   POST /live-input       { idToken, name }     → { uid, rtmpsUrl, streamKey, hlsUrl }
 *   POST /live-input/:uid/delete  { idToken }    → { ok }
 */

import { syncTeamsnap, teamsnapStatus } from './teamsnap';

const PUBLIC_BASE = 'https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev';
const R2_BUCKET = 'stompers-videos';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders },
  });

// ─── Firebase ID token verification ───────────────────────────────────────────
// The client is a static bundle, so it cannot hold a secret. Instead each caller
// sends the Firebase ID token it already has from Google sign-in, and we verify
// the RS256 signature against Google's published certs. That makes access
// per-user and revocable (sign the user out / remove them in Firebase) rather
// than one string that, once leaked, grants R2 writes and Stream provisioning
// to anyone forever.

const FIREBASE_PROJECT_ID = 'lasalle-stompers';
const GOOGLE_CERTS_URL =
  'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com';

// Google rotates these daily and serves a long max-age; cache per isolate.
let _certCache: { at: number; certs: Record<string, string> } | null = null;

async function googleCerts(): Promise<Record<string, string>> {
  if (_certCache && Date.now() - _certCache.at < 3600_000) return _certCache.certs;
  const r = await fetch(GOOGLE_CERTS_URL);
  if (!r.ok) throw new Error(`cert fetch failed: ${r.status}`);
  const certs = await r.json();
  _certCache = { at: Date.now(), certs };
  return certs;
}

const b64urlToBytes = (s) => {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=');
  const bin = atob(b64);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};

// Pull the DER public key out of a PEM X.509 cert and import it for RS256.
async function importCertKey(pem) {
  const body = pem.replace(/-----(BEGIN|END) CERTIFICATE-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  // WebCrypto can't import an X.509 cert directly, so walk the DER to the
  // SubjectPublicKeyInfo: it is the last SEQUENCE before the signature block,
  // and always begins with the RSA algorithm OID (1.2.840.113549.1.1.1).
  const oid = [0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01];
  let at = -1;
  for (let i = 0; i < der.length - oid.length; i++) {
    let hit = true;
    for (let j = 0; j < oid.length; j++) if (der[i + j] !== oid[j]) { hit = false; break; }
    if (hit) { at = i; break; }
  }
  if (at < 0) throw new Error('no RSA SPKI in cert');
  // Step back to the enclosing SEQUENCE header (0x30 0x82 len-hi len-lo).
  const start = at - 4;
  const len = (der[start + 2] << 8) | der[start + 3];
  const spki = der.slice(start, start + 4 + len);
  return crypto.subtle.importKey(
    'spki', spki, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
}

/** Returns the verified token payload, or null if the token is not valid. */
async function verifyFirebaseToken(idToken) {
  try {
    if (!idToken || typeof idToken !== 'string') return null;
    const [h, p, s] = idToken.split('.');
    if (!h || !p || !s) return null;
    const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
    const payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));
    if (header.alg !== 'RS256' || !header.kid) return null;

    // Claims must be checked as strictly as the signature: a valid signature on
    // a token minted for another project (or an expired one) is still a reject.
    const now = Math.floor(Date.now() / 1000);
    if (payload.aud !== FIREBASE_PROJECT_ID) return null;
    if (payload.iss !== `https://securetoken.google.com/${FIREBASE_PROJECT_ID}`) return null;
    if (!payload.sub) return null;
    if (typeof payload.exp !== 'number' || payload.exp <= now) return null;
    if (typeof payload.iat !== 'number' || payload.iat > now + 300) return null;

    const certs = await googleCerts();
    const pem = certs[header.kid];
    if (!pem) return null;
    const key = await importCertKey(pem);
    const ok = await crypto.subtle.verify(
      'RSASSA-PKCS1-v1_5', key,
      b64urlToBytes(s),
      new TextEncoder().encode(`${h}.${p}`));
    return ok ? payload : null;
  } catch (e) {
    console.error('token verify failed:', e);
    return null;
  }
}

/**
 * Authorize a request. Prefers a Firebase ID token; falls back to the legacy
 * shared password so existing clients keep working through the rollout.
 * Returns null when authorized, or a 401 Response to return to the caller.
 */
async function requireAuth(env, { idToken, password }) {
  if (idToken) {
    const payload = await verifyFirebaseToken(idToken);
    if (payload) return null;
    return json({ error: 'unauthorized' }, 401);
  }
  if (password && env.COACH_PASS && password === env.COACH_PASS) return null;
  return json({ error: 'unauthorized' }, 401);
}

// ─── S3 Presigned URL (AWS Signature V4) ──────────────────────────────────────

async function hmacSha256(key, data) {
  const cryptoKey = await crypto.subtle.importKey(
    'raw', key instanceof ArrayBuffer ? key : new TextEncoder().encode(key),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  return crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(data));
}

async function sha256Hex(data) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(data));
  return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function getSigningKey(secret, dateStamp, region, service) {
  let key = await hmacSha256('AWS4' + secret, dateStamp);
  key = await hmacSha256(key, region);
  key = await hmacSha256(key, service);
  key = await hmacSha256(key, 'aws4_request');
  return key;
}

async function createPresignedPutUrl(accessKeyId, secretAccessKey, accountId, bucket, objectKey, contentType, expiresIn = 3600) {
  const region = 'auto';
  const service = 's3';
  const host = `${accountId}.r2.cloudflarestorage.com`;
  const now = new Date();
  const amzDate = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const dateStamp = amzDate.slice(0, 8);
  const credential = `${accessKeyId}/${dateStamp}/${region}/${service}/aws4_request`;

  const params = new URLSearchParams();
  params.set('X-Amz-Algorithm', 'AWS4-HMAC-SHA256');
  params.set('X-Amz-Content-Sha256', 'UNSIGNED-PAYLOAD');
  params.set('X-Amz-Credential', credential);
  params.set('X-Amz-Date', amzDate);
  params.set('X-Amz-Expires', String(expiresIn));
  params.set('X-Amz-SignedHeaders', 'content-type;host');
  params.sort();

  const canonicalRequest = [
    'PUT',
    `/${bucket}/${objectKey}`,
    params.toString(),
    `content-type:${contentType}\nhost:${host}\n`,
    'content-type;host',
    'UNSIGNED-PAYLOAD',
  ].join('\n');

  const stringToSign = [
    'AWS4-HMAC-SHA256',
    amzDate,
    `${dateStamp}/${region}/${service}/aws4_request`,
    await sha256Hex(canonicalRequest),
  ].join('\n');

  const signingKey = await getSigningKey(secretAccessKey, dateStamp, region, service);
  const sig = await hmacSha256(signingKey, stringToSign);
  const signature = [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
  params.set('X-Amz-Signature', signature);

  return `https://${host}/${bucket}/${objectKey}?${params.toString()}`;
}

// ─── Live Input helpers ───────────────────────────────────────────────────────

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
        recording: { mode: 'automatic' },
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
    hlsUrl: `https://customer-${env.CF_STREAM_SUBDOMAIN || ''}.cloudflarestream.com/${r.uid}/manifest/video.m3u8`,
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

// Delete every R2 object under the given key prefixes (paginated; R2 list
// caps at 1000). Used by the game-delete wipe routes.
// Resolve the R2 bucket binding regardless of its configured name. The code
// has long used env.BUCKET while worker/wrangler.toml bound it as "R2" — that
// mismatch left env.BUCKET undefined and was the source of the /put 1101.
// Tolerating both names makes the deploy safe whatever the dashboard has.
function r2(env) {
  const b = env.BUCKET || env.R2;
  if (!b) throw new Error("no R2 bucket binding (expected BUCKET or R2)");
  return b;
}

async function wipePrefixes(env, prefixes) {
  const bucket = r2(env);
  let deleted = 0;
  for (const prefix of prefixes) {
    let cursor = undefined;
    do {
      const listed = await bucket.list({ prefix, cursor, limit: 1000 });
      const keys = (listed.objects || []).map((o) => o.key);
      if (keys.length) { await bucket.delete(keys); deleted += keys.length; }
      cursor = listed.truncated ? listed.cursor : undefined;
    } while (cursor);
  }
  return deleted;
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    // ---- POST /telemetry (UNAUTHENTICATED, deliberately) ----
    // The crash beacon in the HTML shell runs before the app mounts and often
    // before anyone is signed in — which is exactly when a crash report matters
    // — so it cannot present an ID token. It used to send the shared password
    // instead, putting a credential good for 5 GB uploads and Stream
    // provisioning into a public inline script. This endpoint takes its place:
    // no credential, writes only small JSON under a fixed prefix, and grants
    // nothing else. Worst case an abuser writes junk debug blobs.
    if (request.method === 'POST' && url.pathname === '/telemetry') {
      const raw = await request.text();
      if (raw.length > 16_384) return json({ error: 'too large' }, 413);
      try { JSON.parse(raw); } catch { return json({ error: 'bad json' }, 400); }
      const key = `debug/${new Date().toISOString().slice(0, 10)}/${Date.now()}-${
        Math.random().toString(36).slice(2, 10)}.json`;
      await r2(env).put(key, raw, { httpMetadata: { contentType: 'application/json' } });
      return json({ ok: true });
    }

    // ---- POST /upload-url (PRESIGNED — direct to R2, up to 5 GB) ----
    if (request.method === 'POST' && url.pathname === '/upload-url') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, idToken, filename, contentType } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
      if (!filename) return json({ error: 'filename required' }, 400);

      const safe = String(filename).replace(/[^a-zA-Z0-9._-]/g, '_');
      const ct = contentType || 'video/mp4';

      // If presigned URL secrets are configured, use direct-to-R2 upload
      if (env.R2_ACCESS_KEY_ID && env.R2_SECRET_ACCESS_KEY && env.CF_ACCOUNT_ID) {
        try {
          const uploadUrl = await createPresignedPutUrl(
            env.R2_ACCESS_KEY_ID, env.R2_SECRET_ACCESS_KEY,
            env.CF_ACCOUNT_ID, R2_BUCKET, safe, ct
          );
          return json({ uploadUrl, publicUrl: `${PUBLIC_BASE}/${safe}`, key: safe });
        } catch (err) {
          // Fall through to legacy proxy if presigned fails
          console.error('Presigned URL generation failed:', err);
        }
      }

      // Fallback: proxy through worker (≤100 MB limit)
      return json({
        uploadUrl: `${url.origin}/put/${encodeURIComponent(safe)}?auth=${encodeURIComponent(password)}`,
        publicUrl: `${PUBLIC_BASE}/${safe}`,
        key: safe,
      });
    }

    // ---- PUT /put/:filename (legacy proxy fallback, ≤100 MB) ----
    if (request.method === 'PUT' && url.pathname.startsWith('/put/')) {
      // Auth rides in the query string here: this is a raw-body PUT, so there
      // is no JSON envelope to carry it. Accepts an ID token or the legacy pass.
      const authParam = url.searchParams.get('auth');
      const _unauth = await requireAuth(env, { idToken: authParam, password: authParam });
      if (_unauth) return _unauth;
      const key = decodeURIComponent(url.pathname.slice('/put/'.length));
      if (!key) return json({ error: 'no key' }, 400);
      const contentType = request.headers.get('content-type') || 'video/mp4';
      await r2(env).put(key, request.body, { httpMetadata: { contentType } });
      return json({ ok: true, publicUrl: `${PUBLIC_BASE}/${key}` });
    }

    // ---- POST /live-input ----
    if (request.method === 'POST' && url.pathname === '/live-input') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, idToken, name } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
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

    // ---- GET /youtube-live — auto-detect the currently-live stream on the team channel ----
    if (request.method === 'POST' && url.pathname === '/youtube-live') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, idToken } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
      if (!env.YOUTUBE_API_KEY) {
        return json({ error: 'YOUTUBE_API_KEY not configured on worker' }, 500);
      }
      const handle = env.YOUTUBE_CHANNEL_HANDLE || 'Stompers2016';
      try {
        // Step 1: resolve channel ID from handle
        let channelId = env.YOUTUBE_CHANNEL_ID || null;
        if (!channelId) {
          const chRes = await fetch(
            `https://www.googleapis.com/youtube/v3/channels?part=id&forHandle=${encodeURIComponent(handle)}&key=${env.YOUTUBE_API_KEY}`
          );
          const chData = await chRes.json();
          if (!chData.items || chData.items.length === 0) {
            return json({ error: `Channel @${handle} not found` }, 404);
          }
          channelId = chData.items[0].id;
        }
        // Step 2: search for currently-live videos on this channel
        const searchRes = await fetch(
          `https://www.googleapis.com/youtube/v3/search?part=snippet&channelId=${channelId}&type=video&eventType=live&key=${env.YOUTUBE_API_KEY}`
        );
        const searchData = await searchRes.json();
        if (!searchData.items || searchData.items.length === 0) {
          return json({ live: false, videoId: null, channelId });
        }
        const videoId = searchData.items[0].id.videoId;
        const title = searchData.items[0].snippet.title;
        return json({ live: true, videoId, title, channelId });
      } catch (err) {
        return json({ error: String(err.message || err) }, 502);
      }
    }

    // ---- GET /youtube-playlist?id=<playlistId> — public read: titles + thumbs for a playlist ----
    // Metadata only (no video hosting). Edge-cached 1h to protect the YouTube API quota.
    if (request.method === 'GET' && url.pathname === '/youtube-playlist') {
      const id = url.searchParams.get('id') || '';
      if (!/^[A-Za-z0-9_-]+$/.test(id)) return json({ error: 'invalid playlist id' }, 400);
      if (!env.YOUTUBE_API_KEY) {
        return json({ error: 'YOUTUBE_API_KEY not configured on worker' }, 500);
      }

      const cache = caches.default;
      const cacheKey = new Request(url.toString(), request);
      const cached = await cache.match(cacheKey);
      if (cached) return cached;

      try {
        // Playlist title (one call).
        let title = '';
        const plRes = await fetch(
          `https://www.googleapis.com/youtube/v3/playlists?part=snippet&id=${encodeURIComponent(id)}&key=${env.YOUTUBE_API_KEY}`
        );
        const plData = await plRes.json();
        if (plData.items && plData.items.length) title = plData.items[0].snippet.title;

        // Items, paginated — cap at ~2 pages (100 videos) to bound quota.
        const items = [];
        let pageToken = '';
        for (let page = 0; page < 2; page++) {
          const itRes = await fetch(
            `https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&maxResults=50&playlistId=${encodeURIComponent(id)}&key=${env.YOUTUBE_API_KEY}${pageToken ? `&pageToken=${pageToken}` : ''}`
          );
          const itData = await itRes.json();
          if (itData.error) return json({ error: itData.error.message || 'youtube error' }, 502);
          for (const it of (itData.items || [])) {
            const videoId = it.contentDetails && it.contentDetails.videoId;
            const snip = it.snippet || {};
            // Skip private/deleted entries.
            if (!videoId || snip.title === 'Private video' || snip.title === 'Deleted video') continue;
            const thumbs = snip.thumbnails || {};
            const thumbnail = (thumbs.medium && thumbs.medium.url) || (thumbs.default && thumbs.default.url) || '';
            items.push({ videoId, title: snip.title || '', thumbnail, position: snip.position });
          }
          pageToken = itData.nextPageToken || '';
          if (!pageToken) break;
        }

        const res = new Response(JSON.stringify({ playlistId: id, title, items }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Cache-Control': 'public, max-age=3600',
            ...corsHeaders,
          },
        });
        await cache.put(cacheKey, res.clone());
        return res;
      } catch (err) {
        return json({ error: String(err.message || err) }, 502);
      }
    }

    // ---- POST /live-input/:uid/delete ----
    const delMatch = url.pathname.match(/^\/live-input\/([a-zA-Z0-9_-]+)\/delete$/);
    if (request.method === 'POST' && delMatch) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password, idToken } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
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
    // Wipe R2 reels/clips for a game (coach "Delete game" / "Delete videos").
    const vidWipe = url.pathname.match(/^\/game\/([a-zA-Z0-9_-]+)\/videos\/delete$/);
    if (request.method === 'POST' && vidWipe) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password, idToken } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
      try {
        const deleted = await wipePrefixes(env, [`tv_view/${vidWipe[1]}/`, `clips/${vidWipe[1]}/`]);
        return json({ ok: true, deleted });
      } catch (err) {
        return json({ error: String(err.message || err) }, 500);
      }
    }

    // ---- POST /game/:id/voice/delete ----
    // Wipe the coach's voice recordings (flat keys voice_<id>_*) on game
    // delete. SEPARATE from videos/delete so "Delete videos only" keeps voice.
    const voiceWipe = url.pathname.match(/^\/game\/([a-zA-Z0-9_-]+)\/voice\/delete$/);
    if (request.method === 'POST' && voiceWipe) {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password, idToken } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
      try {
        const deleted = await wipePrefixes(env, [`voice_${voiceWipe[1]}_`]);
        return json({ ok: true, deleted });
      } catch (err) {
        return json({ error: String(err.message || err) }, 500);
      }
    }

    // ---- GET /teamsnap/status ----
    // Read-only health check for the cron: how many events are mirrored, their
    // type split, and the newest few. Deliberately unauthenticated but
    // non-sensitive in aggregate — counts and types only, no venues, no times,
    // nothing that says where the kids will be.
    if (request.method === 'GET' && url.pathname === '/teamsnap/status') {
      try {
        const out = await teamsnapStatus(env);
        return json(out);
      } catch (err) {
        return json({ ok: false, error: String(err.message || err) }, 500);
      }
    }

    // ---- POST /teamsnap/sync ----
    // Manual kick of the same job the cron runs, so a coach who just edited
    // TeamSnap can pull immediately instead of waiting for the next tick.
    // Bounded by TeamSnap's own 4h CDN cache — a manual run right after an edit
    // may still get cached bytes and report skipped:'not-modified'.
    if (request.method === 'POST' && url.pathname === '/teamsnap/sync') {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const { password, idToken } = body || {};
      const _unauth = await requireAuth(env, { idToken, password });
      if (_unauth) return _unauth;
      try {
        return json(await syncTeamsnap(env));
      } catch (err) {
        return json({ ok: false, error: String(err.message || err) }, 500);
      }
    }

    return json({ error: 'not found' }, 404);
  },

  // Cron entry point. Errors are logged rather than thrown so one bad poll
  // never wedges the schedule — the next tick retries in 15 minutes.
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(
      syncTeamsnap(env)
        .then((r) => console.log('teamsnap sync', JSON.stringify(r)))
        .catch((e) => console.error('teamsnap sync failed', String(e && e.message || e)))
    );
  },
};
