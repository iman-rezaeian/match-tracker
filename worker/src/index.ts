/**
 * Cloudflare Worker — R2 Upload (Presigned URL) + CF Stream Live Input Provisioner
 *
 * CHANGE FROM PREVIOUS VERSION:
 *   /upload-url now returns a PRESIGNED S3 URL that lets the browser PUT directly
 *   to R2 (up to 5 GB). The old /put/:filename proxy route is kept as fallback
 *   for files under 100 MB but is no longer the primary path.
 *
 * SECRETS (Worker → Settings → Variables and Secrets):
 *   - COACH_PASS         = "ManUtd2016"
 *   - CF_API_TOKEN       = <token with Stream:Edit>  (for Live Inputs)
 *   - CF_ACCOUNT_ID      = <your Cloudflare account id>
 *   - R2_ACCESS_KEY_ID   = <R2 S3 API token access key>
 *   - R2_SECRET_ACCESS_KEY = <R2 S3 API token secret key>
 *
 * BINDINGS (Worker → Settings → Bindings):
 *   - R2 Bucket: variable name "BUCKET", bucket "stompers-videos"
 *
 * ENDPOINTS:
 *   POST /upload-url       { password, filename, contentType? } → { uploadUrl (presigned S3), publicUrl }
 *   PUT  /put/:filename?auth=<pass>  (raw body, fallback ≤100MB) → { ok, publicUrl }
 *   POST /live-input       { password, name }     → { uid, rtmpsUrl, streamKey, hlsUrl }
 *   POST /live-input/:uid/delete  { password }    → { ok }
 */

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

// ─── Main ─────────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    // ---- POST /upload-url (PRESIGNED — direct to R2, up to 5 GB) ----
    if (request.method === 'POST' && url.pathname === '/upload-url') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password, filename, contentType } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
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

    // ---- GET /youtube-live — auto-detect the currently-live stream on the team channel ----
    if (request.method === 'POST' && url.pathname === '/youtube-live') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
      const { password } = body || {};
      if (!password || password !== env.COACH_PASS) return json({ error: 'unauthorized' }, 401);
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

    return json({ error: 'not found' }, 404);
  },
};
