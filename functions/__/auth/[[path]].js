// Reverse proxy: serve the Firebase auth handler from OUR origin.
//
// iOS (and increasingly all Safari) partitions third-party storage, which
// breaks signInWithRedirect round-trips through lasalle-stompers.firebaseapp.com
// — most painfully inside home-screen PWAs, where fresh installs (parents!)
// hang at sign-in forever. Firebase's documented fix: set authDomain to the
// app's own domain and proxy /__/auth/* to the firebaseapp.com handler so the
// whole redirect dance stays same-origin.
//
// Cloudflare Pages Functions: this file auto-deploys with the repo and routes
// stompers2016.com/__/auth/* (and the *.pages.dev aliases). Static assets are
// untouched. Harmless while authDomain still points at firebaseapp.com.
const UPSTREAM = 'https://lasalle-stompers.firebaseapp.com';

export async function onRequest({ request }) {
  const url = new URL(request.url);
  const upstream = UPSTREAM + url.pathname + url.search;
  const resp = await fetch(new Request(upstream, request));
  // Pass the response through untouched (headers matter to the auth handler).
  return new Response(resp.body, resp);
}
