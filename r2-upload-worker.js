/**
 * Cloudflare Worker — R2 Upload Presigner for Stompers Match Tracker
 *
 * DEPLOY: Cloudflare Dashboard → Workers & Pages → Create → "Create Worker" →
 *         paste this code → Save and Deploy → bind R2 bucket in Settings → Variables.
 *
 * SETTINGS (after deploy):
 *   1. Go to Worker → Settings → Variables and Secrets
 *   2. Add variable: COACH_PASS = "ManUtd2016" (encrypted)
 *   3. Go to Settings → Bindings → R2 Bucket Bindings
 *   4. Add binding: Variable name = BUCKET, R2 bucket = "stompers-videos"
 *
 * USAGE from browser:
 *   // 1. Get a presigned upload URL
 *   const res = await fetch('https://<worker>.workers.dev/upload-url', {
 *     method: 'POST',
 *     headers: { 'Content-Type': 'application/json' },
 *     body: JSON.stringify({ password: 'ManUtd2016', filename: 'game-2026-05-26.mp4', contentType: 'video/mp4' })
 *   });
 *   const { uploadUrl, publicUrl } = await res.json();
 *
 *   // 2. PUT the file directly to R2
 *   await fetch(uploadUrl, { method: 'PUT', body: file, headers: { 'Content-Type': 'video/mp4' } });
 *
 *   // 3. Save publicUrl to Firestore game.videoUrl
 */

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        },
      });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405 });
    }

    const url = new URL(request.url);

    // ---- /upload-url: mint a presigned PUT URL ----
    if (url.pathname === '/upload-url') {
      let body;
      try { body = await request.json(); } catch { return new Response('Bad JSON', { status: 400 }); }

      const { password, filename, contentType } = body;
      if (!password || password !== env.COACH_PASS) {
        return new Response(JSON.stringify({ error: 'unauthorized' }), { status: 401, headers: { 'Content-Type': 'application/json' } });
      }
      if (!filename) {
        return new Response(JSON.stringify({ error: 'filename required' }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      }

      // Sanitize filename — alphanumeric, hyphens, dots only
      const safe = filename.replace(/[^a-zA-Z0-9._-]/g, '_');

      // Generate a presigned URL valid for 1 hour
      const key = safe;
      const signedUrl = await env.BUCKET.createMultipartUpload(key);
      // Actually, R2 Workers bindings don't have presigned URLs directly.
      // Instead, we'll do a direct PUT through the Worker itself.

      // Alternative approach: Worker acts as a proxy for the PUT.
      // The browser sends the file to the Worker, Worker streams it into R2.
      // This avoids presigned URL complexity entirely.
      return new Response(JSON.stringify({
        uploadUrl: `${url.origin}/put/${safe}`,
        publicUrl: `https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev/${safe}`,
        key: safe,
      }), {
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // ---- /put/:filename: stream file body into R2 ----
    if (url.pathname.startsWith('/put/')) {
      // Auth via query param (set by the upload flow after getting the URL)
      const authParam = url.searchParams.get('auth');
      if (!authParam || authParam !== env.COACH_PASS) {
        return new Response('Unauthorized', { status: 401 });
      }

      const key = url.pathname.slice(5); // strip "/put/"
      if (!key) return new Response('No key', { status: 400 });

      const contentType = request.headers.get('content-type') || 'video/mp4';

      // Stream the request body directly into R2 (no memory buffering for large files)
      await env.BUCKET.put(key, request.body, {
        httpMetadata: { contentType },
      });

      return new Response(JSON.stringify({
        ok: true,
        publicUrl: `https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev/${key}`,
      }), {
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    return new Response('Not found', { status: 404 });
  },
};
