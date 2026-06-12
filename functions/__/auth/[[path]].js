// Reverse proxy: serve the Firebase auth handler from OUR origin.
//
// iOS (and increasingly all Safari) partitions third-party storage, which
// breaks signInWithRedirect round-trips through lasalle-stompers.firebaseapp.com
// — most painfully inside home-screen PWAs, where fresh installs (parents!)
// hang at sign-in forever. Firebase's documented fix: set authDomain to the
// app's own domain and proxy /__/auth/* to the firebaseapp.com handler so the
// whole redirect dance stays same-origin.
//
// Two hard-won details:
//  - Forward only the headers the handler needs. Copying the inbound request
//    wholesale forwards Cloudflare hop headers (cdn-loop etc.) and Google's
//    frontend answers 503.
//  - redirect:'manual' — the handler's 302 back into the app must reach the
//    BROWSER; following it inside the proxy breaks the flow.
const UPSTREAM = 'https://lasalle-stompers.firebaseapp.com';
const FWD = ['accept', 'accept-language', 'content-type', 'user-agent', 'referer', 'origin', 'cookie'];

export async function onRequest({ request }) {
  const url = new URL(request.url);
  const headers = new Headers();
  for (const h of FWD) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  const init = { method: request.method, headers, redirect: 'manual' };
  if (request.method !== 'GET' && request.method !== 'HEAD') init.body = request.body;
  const resp = await fetch(UPSTREAM + url.pathname + url.search, init);
  return new Response(resp.body, resp);
}
