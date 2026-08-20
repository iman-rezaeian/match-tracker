import React, { useState, useEffect, useMemo, useRef, useImperativeHandle, forwardRef } from 'react';
import {
  Plus, Users, Trash2, Edit3, ChevronLeft,
  PlayCircle, Undo2, X, ChevronRight,
  BarChart3, Flag, Calendar, MapPin
} from 'lucide-react';

const STORAGE_KEYS = { ROSTER: 'roster', GAMES: 'games', WEIGHTS: 'weights', SCHEDULE: 'schedule', TEAM_LIVE_INPUT: 'team_live_input' };

const EVENT_TYPES = {
  GOAL:      { id: 'GOAL',      label: 'GOAL',      emoji: '⚽', tone: 'big-green',  requiresPlayer: true,  delta: 'us' },
  ASSIST:    { id: 'ASSIST',    label: 'ASSIST',    emoji: '🅰️', tone: 'soft-green', requiresPlayer: true },
  KEY_PASS:  { id: 'KEY_PASS',  label: 'KEY PASS',  emoji: '🔑', tone: 'soft-green', requiresPlayer: true },
  SAVE:      { id: 'SAVE',      label: 'SAVE',      emoji: '🧤', tone: 'blue',       requiresPlayer: true },
  SHOT_ON:   { id: 'SHOT_ON',   label: 'SHOT ON',   emoji: '🎯', tone: 'soft-green', requiresPlayer: true },
  SHOT_OFF:  { id: 'SHOT_OFF',  label: 'SHOT OFF',  emoji: '❌', tone: 'neutral',    requiresPlayer: true },
  BLOCK:     { id: 'BLOCK',     label: 'BLOCK',     emoji: '🛡️', tone: 'blue',       requiresPlayer: true },
  BALL_WIN:  { id: 'BALL_WIN',  label: 'BALL WIN',  emoji: '🔥', tone: 'soft-green', requiresPlayer: true },
  CLEAR:     { id: 'CLEAR',     label: 'CLEARANCE', emoji: '🧹', tone: 'blue',       requiresPlayer: true },
  KICK_OUT:  { id: 'KICK_OUT',  label: 'KICK OUT',  emoji: '🥾', tone: 'blue',       requiresPlayer: true },
  DUEL_WIN:  { id: 'DUEL_WIN',  label: '1V1 WIN',   emoji: '💪', tone: 'soft-green', requiresPlayer: true },
  DUEL_LOSE: { id: 'DUEL_LOSE', label: '1V1 LOSE',  emoji: '👎', tone: 'soft-red',   requiresPlayer: true },
  GIVE_GO:   { id: 'GIVE_GO',   label: 'GIVE-GO',   emoji: '🔁', tone: 'soft-green', requiresPlayer: true },
  GATES:     { id: 'GATES',     label: 'GATE PASS', emoji: '🚪', tone: 'soft-green', requiresPlayer: true },
  TURNOVER:  { id: 'TURNOVER',  label: 'TURNOVER',  emoji: '💨', tone: 'soft-red',   requiresPlayer: true },
  HOLDS_BALL:{ id: 'HOLDS_BALL',label: 'HOLDS BALL',emoji: '⏳', tone: 'yellow',     requiresPlayer: true },
  OPP_GOAL:  { id: 'OPP_GOAL',  label: 'OPP GOAL',  emoji: '⚽', tone: 'big-red',    requiresPlayer: false, delta: 'opp' },
  // Discipline & set-piece events (added 2026 season).
  // FOUL_ON / FOUL_BY = was the foul awarded to us, or against us?
  // PEN_AWARDED / PEN_CONCEDED = same idea for penalty kicks.
  // Coach taps these after the whistle; player picker follows (the player
  // who DREW the foul/penalty for us, or the player who COMMITTED it).
  FOUL_BY:      { id: 'FOUL_BY',      label: 'FOUL BY US',  emoji: '🟨', tone: 'soft-red',   requiresPlayer: true  },
  FOUL_ON:      { id: 'FOUL_ON',      label: 'FOUL ON US',  emoji: '🛑', tone: 'soft-green', requiresPlayer: true  },
  PEN_CONCEDED: { id: 'PEN_CONCEDED', label: 'PEN GIVEN',   emoji: '⚠️', tone: 'big-red',    requiresPlayer: true  },
  PEN_AWARDED:  { id: 'PEN_AWARDED',  label: 'PEN WON',     emoji: '🎯', tone: 'big-green',  requiresPlayer: true  },
  // Penalty OUTCOMES (added 2026). Converted pens are stored as GOAL / OPP_GOAL
  // carrying { penalty: true } (transparent to all score/stats/reel code that
  // keys on type) — the picker translates the PEN_GOAL_US / PEN_GOAL_OPP button
  // intents into those. Only the MISSES are their own stored types, and they
  // carry NO `delta` so the scoreboard is untouched.
  PEN_MISSED:     { id: 'PEN_MISSED',     label: 'PEN MISS',  emoji: '🚫', tone: 'soft-red', requiresPlayer: true  },
  OPP_PEN_MISSED: { id: 'OPP_PEN_MISSED', label: 'PEN SAVED', emoji: '🧤', tone: 'blue',     requiresPlayer: false },
  // POSITION is a silent event written by the tactical board on drag-end.
  // Carries { playerId, x, y } where x,y ∈ [0,1] over a half-field portrait
  // (own goal bottom, halfway line top). Filtered out of RECENT and stats.
  POSITION:  { id: 'POSITION',  label: 'POSITION',  emoji: '📍', tone: 'neutral',    requiresPlayer: true,  silent: true },
  // BOOKMARK is the live "something just happened" reflex tap: timestamp only,
  // no player, classified later from video in the Film Room confirm queue
  // (confirm replaces it with a real event stamped source:'bookmark-confirmed').
  // silent ⇒ never feeds scoring/involvement; it IS shown in RECENT/TIMELINE.
  BOOKMARK:  { id: 'BOOKMARK',  label: 'BOOKMARK',  emoji: '🔖', tone: 'yellow',     requiresPlayer: false, silent: true },
};

// Events that get an optional zone tag — trimmed (Phase 3.4) to the events
// where LOCATION is the insight: shots feed the shot map, turnovers show where
// we lose the ball, ball wins show pressing height. Everything else was tag
// noise the coach never filled in.
const EVENT_NEEDS_ZONE = new Set([
  'GOAL', 'SHOT_ON', 'SHOT_OFF', 'TURNOVER', 'BALL_WIN',
]);

// Events that get an optional pressure modifier — trimmed (Phase 3.4) to the
// decision-bearing events only (same set as EVENT_NEEDS_DECISION): pressure
// changes what a choice means, not what an outcome is worth.
const EVENT_NEEDS_PRESSURE = new Set([
  'KEY_PASS', 'GIVE_GO', 'GATES',
  'SHOT_ON', 'SHOT_OFF', 'TURNOVER',
]);

// Events that get an optional decision-quality flag (was this the right choice?).
// Focus on choice-points: passes, dribbles, shots, turnovers. Skip pure outcomes (GOAL, SAVE, BLOCK).
const EVENT_NEEDS_DECISION = new Set([
  'KEY_PASS', 'GIVE_GO', 'GATES',
  'SHOT_ON', 'SHOT_OFF', 'TURNOVER',
]);

// 3x3 field grid, normalized to attack direction (D = our defensive third, A = our attacking third).
const ZONES = [
  { id: 'D-L', row: 0, col: 0 }, { id: 'D-C', row: 0, col: 1 }, { id: 'D-R', row: 0, col: 2 },
  { id: 'M-L', row: 1, col: 0 }, { id: 'M-C', row: 1, col: 1 }, { id: 'M-R', row: 1, col: 2 },
  { id: 'A-L', row: 2, col: 0 }, { id: 'A-C', row: 2, col: 1 }, { id: 'A-R', row: 2, col: 2 },
];
const ZONE_LABEL = {
  'D-L': 'Def · Left',   'D-C': 'Def · Center',   'D-R': 'Def · Right',
  'M-L': 'Mid · Left',   'M-C': 'Mid · Center',   'M-R': 'Mid · Right',
  'A-L': 'Att · Left',   'A-C': 'Att · Center',   'A-R': 'Att · Right',
};

// R2 public bucket URL (stompers-videos bucket, CORS enabled).
const R2_PUBLIC = 'https://pub-27636b574e544724ab8c5d7c7e755a99.r2.dev';

// Cloudflare Worker URL for direct browser uploads. Leave '' to hide UPLOAD button
// and only show the LINK flow. Canonical worker source = worker/src/index.ts
// (wrangler project; deploy with `cd worker && npx wrangler deploy`). Paste the
// Worker URL here (no trailing slash), e.g. 'https://stompers-upload.<acct>.workers.dev'.
const R2_UPLOAD_WORKER = 'https://stompers-upload.rezaian-iman.workers.dev';

// Training Videos — YouTube playlist IDs surfaced in the TRAINING section.
// Section titles come from each playlist's own title (fetched via the worker),
// so adding/removing a playlist here is the only change needed.
const TRAINING_PLAYLISTS = [
  'PL1KXWwWfqmixlAhd_t--UOnux0EcGDo5o',
  'PL1KXWwWfqmiwkxyAZaAAyx8o9LT3oCHW9',
];
// Fixed, coach-friendly labels for the two training playlists (we don't trust
// the raw YouTube playlist title/thumbnail — they render messy). Keyed by id.
const TRAINING_PLAYLIST_META = {
  'PL1KXWwWfqmixlAhd_t--UOnux0EcGDo5o': { label: 'Soccer Training', icon: '⚽', gradient: 'linear-gradient(135deg,#3f6212,#1a2e05)', border: 'rgba(132,204,22,0.30)' },
  'PL1KXWwWfqmiwkxyAZaAAyx8o9LT3oCHW9': { label: 'Goalkeeper Training', icon: '🧤', gradient: 'linear-gradient(135deg,#0e7490,#155e75)', border: 'rgba(34,211,238,0.25)' },
};
const _trainingMeta = (id) => TRAINING_PLAYLIST_META[id] || { label: 'Training', icon: '🎬', gradient: 'linear-gradient(135deg,#44403c,#1c1917)', border: 'rgba(255,255,255,0.12)' };

// Lazy-load the YouTube IFrame Player API once. Returns a promise of window.YT.
// A persistent Player lets us loadVideoById() to autoplay across navigations
// (an iframe remount with autoplay=1 gets re-gated by iOS every time).
let _ytApiPromise = null;
function loadYouTubeIframeApi() {
  if (typeof window === 'undefined') return Promise.reject('no window');
  if (window.YT && window.YT.Player) return Promise.resolve(window.YT);
  if (_ytApiPromise) return _ytApiPromise;
  _ytApiPromise = new Promise((resolve) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { try { prev && prev(); } catch (e) {} resolve(window.YT); };
    const tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(tag);
  });
  return _ytApiPromise;
}

// Live-streaming provider toggle.
// 'off'        — no live UI anywhere; all live code paths stay in the bundle
//                so we can flip back without re-implementing anything
// 'youtube'    — free: coach pastes YouTube video ID, app embeds the iframe
// 'cloudflare' — paid ($5/mo): one-tap GO LIVE via Cloudflare Stream Live Input
// Switch to 'cloudflare' when you re-subscribe to Cloudflare Stream Starter
// Bundle, or to 'youtube' to use the free @Stompers2016 auto-detect path.
const LIVE_MODE = 'off';

// WORK_RATE_MIN_COVERAGE was here — the coverage floor below which the M/MIN
// work rate was hidden. Both the per-game and season work-rate columns are gone
// (they aggregated the contaminated per-player tracked distances), so there is no
// longer a rate to gate. post_game/config.py DIST_EST_MIN_COVERAGE still governs
// whether the PIPELINE extrapolates a distance into the analytics doc.

// ---- Usage tracking (feeds the owner-only VIEWERS page) ----
// The app owner is excluded from ALL tracking (he lives in the app building
// it, so his events are pure noise) and is the only one who sees the
// VIEWERS tile. Other coaches are tracked like everyone else — their usage
// is part of what the owner wants to understand.
const OWNER_EMAIL = 'rezaian.iman@gmail.com';
function isOwner() {
  return typeof window !== 'undefined'
    && (window.fbUserInfo?.email || '').toLowerCase() === OWNER_EMAIL;
}

// Usage events land in the `viewerLog` collection. Action vocabulary:
//   'login'             — written by the HTML-shell AuthGate on app open (one per visit)
//   'public:home' / 'public:game' / 'public:stats' — public page sections
//   'watch_highlights' / 'watch_full_game' / 'watch_360' — video opens (endTs on close → watch time)
//   'coach:<view>'      — coach app sections (filmRoom, stats, activeGame, …)
//   'watch_live' / 'watch_replay' — legacy rows from the old live-broadcast era
// Section pings dedupe per app session so browsing back and forth doesn't
// spam Firestore; video opens always log (each is a real watch).
const _usagePinged = new Set();
function trackUsage(action, extra = {}, oncePerSession = true) {
  if (typeof window === 'undefined' || !window.fbDb || !window.fbUserInfo) return null;
  const { email, name, photo } = window.fbUserInfo;
  if (!email || email.toLowerCase() === OWNER_EMAIL) return null;
  const key = action + '|' + JSON.stringify(extra);
  if (oncePerSession && _usagePinged.has(key)) return null;
  _usagePinged.add(key);
  const docRef = window.fbDb.collection('viewerLog').doc();
  docRef.set({
    email: email.toLowerCase(), name: name || '', photo: photo || '', action, ...extra,
    ts: window.firebase?.firestore?.FieldValue?.serverTimestamp?.() || new Date()
  }).catch(() => {});
  return docRef.id;
}
function untrackUsage(docId) {
  if (!docId || !window.fbDb) return;
  window.fbDb.collection('viewerLog').doc(docId).update({
    endTs: window.firebase?.firestore?.FieldValue?.serverTimestamp?.() || new Date()
  }).catch(() => {});
}

// ---- Modal history coordination -------------------------------------------
// Every full-screen modal pushes ONE history entry so the OS back gesture
// closes it instead of leaving the app. Three rules keep nested modals (the
// cue player inside the confirm queue, the reel player inside Analytics) and
// the App view stack from cascading each other closed:
//   1. A modal only closes itself when ITS OWN entry is no longer current —
//      a child modal popping above it must not close it (mIdx ordering).
//   2. The App's view-stack popstate handler ignores pops while any modal is
//      mounted (window.__modalStack) or expected from a modal cleanup
//      (window.__modalPopGuard) — otherwise closing a modal also popped a view.
//   3. Cleanup rewinds its own entry exactly once, flagged as expected.
// Runs ONCE per mount with onClose behind a ref — an [onClose]-keyed effect
// re-runs cleanup (history.back) on parent re-renders and the popstate closes
// the modal mid-use (the original confirm-queue bug).
function useModalHistory(tag, onClose) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const stack = (window.__modalStack = window.__modalStack || []);
    const myIdx = stack.length;
    stack.push(tag);
    window.history.pushState({ modal: tag, mIdx: myIdx }, '');
    const onPop = () => {
      const st = window.history.state;
      // Still current (or a child above us popped) → not our pop.
      if (st && typeof st.mIdx === 'number' && st.mIdx >= myIdx) return;
      onCloseRef.current();
    };
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      const i = stack.lastIndexOf(tag);
      if (i >= 0) stack.splice(i, 1);
      const st = window.history.state;
      // Our entry (or a child's) is still on the browser stack → in-app close
      // (X button). Rewind it, and flag the pop so the App view handler and
      // any parent modal ignore it. Closed-by-gesture leaves nothing behind.
      if (st && typeof st.mIdx === 'number' && st.mIdx >= myIdx) {
        window.__modalPopGuard = (window.__modalPopGuard || 0) + 1;
        window.history.back();
      }
    };
  }, []);
}

// Viewer buckets (owner-assigned): viewerTags/{email} → { email, buckets: [] }
const VIEWER_BUCKETS = ['coach', 'parent', 'player', 'unknown'];
const BUCKET_META = {
  coach:   { label: 'COACH',   emoji: '📋', chip: 'bg-amber-500/15 text-amber-300 border-amber-600' },
  parent:  { label: 'PARENT',  emoji: '👪', chip: 'bg-sky-500/15 text-sky-300 border-sky-600' },
  player:  { label: 'PLAYER',  emoji: '⚽', chip: 'bg-lime-500/15 text-lime-300 border-lime-600' },
  unknown: { label: 'UNKNOWN', emoji: '❓', chip: 'bg-stone-800 text-stone-400 border-stone-600' },
};

const TONE_CLASSES = {
  'big-green':  'bg-lime-500 hover:bg-lime-600 text-stone-950 shadow-lg shadow-lime-500/30 border-lime-400',
  'big-red':    'bg-red-500 hover:bg-red-600 text-white shadow-lg shadow-red-500/30 border-red-400',
  'soft-green': 'bg-lime-900/50 hover:bg-lime-900/70 text-lime-200 border-lime-600/60',
  'soft-red':   'bg-red-900/50 hover:bg-red-900/70 text-red-200 border-red-600/60',
  'blue':       'bg-sky-900/50 hover:bg-sky-900/70 text-sky-200 border-sky-600/60',
  'yellow':     'bg-yellow-900/50 hover:bg-yellow-900/70 text-yellow-200 border-yellow-600/60',
  'purple':     'bg-violet-900/50 hover:bg-violet-900/70 text-violet-200 border-violet-600/60',
  'neutral':    'bg-stone-800 hover:bg-stone-700 text-stone-200 border-stone-700',
};

// Live broadcast-TV penalty banner shown over the coach view the instant a
// penalty outcome is logged (the "during the game" moment). Auto-dismissed by
// the parent's timer; tapping it closes early. flash = { kind:'goal'|'miss',
// side:'us'|'them', saved:bool, playerLabel, ourScore, oppScore }.
function PenaltyFlash({ flash, onDismiss }) {
  const us = flash.side === 'us';
  const headline = flash.kind === 'goal'
    ? 'PENALTY GOAL'
    : (flash.saved ? 'PENALTY SAVED' : 'PENALTY MISSED');
  const emoji = flash.kind === 'goal' ? '⚽' : (flash.saved ? '🧤' : '🚫');
  // Tone: our goal / their miss = good (lime); their goal / our miss = bad (red).
  const good = (flash.kind === 'goal' && us) || (flash.kind === 'miss' && !us);
  const accent = good ? 'bg-lime-500 text-stone-950' : 'bg-red-500 text-white';
  return (
    <div
      onClick={onDismiss}
      className="fixed inset-x-0 top-16 z-[60] flex justify-center px-4 animate-[fadein_0.15s_ease-out]"
    >
      <div className="w-full max-w-md overflow-hidden rounded-2xl border border-white/15 bg-stone-950/95 shadow-2xl">
        <div className={`px-3 py-1.5 text-center font-display text-sm tracking-[0.3em] ${accent}`}>
          {emoji} {headline}
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 py-3 text-white">
          <span className="font-display tracking-wide text-xs text-left text-lime-300">LASALLE STOMPERS</span>
          <div className="font-display text-3xl tabular-nums text-center">
            <span className={flash.kind === 'goal' && us ? 'text-lime-300' : ''}>{flash.ourScore}</span>
            <span className="mx-1.5 text-stone-500">—</span>
            <span className={flash.kind === 'goal' && !us ? 'text-red-300' : ''}>{flash.oppScore}</span>
          </div>
          <span className="font-display tracking-wide text-xs text-right text-red-300">OPPONENT</span>
        </div>
        {flash.playerLabel && (
          <div className="px-4 pb-2.5 -mt-1 text-center text-[11px] tracking-wide text-stone-300 font-display">
            {flash.playerLabel}
          </div>
        )}
      </div>
    </div>
  );
}

const SEED_ROSTER = [
  { id: 'p_adam',      name: 'Ben Adam',         number: '3',  position: '' },
  { id: 'p_sharma',    name: 'Vince Sharma',     number: '5',  position: '' },
  { id: 'p_cardoso',   name: 'Maverick Cardoso', number: '6',  position: '' },
  { id: 'p_gibala',    name: 'Liam Gibala',      number: '7',  position: '' },
  { id: 'p_hahn',      name: 'Ben Hahn',         number: '8',  position: '' },
  { id: 'p_bowser',    name: 'Nolan Bowser',     number: '9',  position: '' },
  { id: 'p_yaacoub',   name: 'Khalid Yaacoub',   number: '10', position: '' },
  { id: 'p_hassoun',   name: 'Issa Hassoun',     number: '11', position: '' },
  { id: 'p_garland',   name: 'Liam Garland',     number: '14', position: '' },
  { id: 'p_rezaeian',  name: 'Arian Rezaeian',   number: '15', position: '' },
  { id: 'p_kerr',      name: 'Alexander Kerr',   number: '16', position: '' },
  { id: 'p_qian',      name: 'Jason Qian',       number: '17', position: '' },
  { id: 'p_shallvari', name: 'David Shallvari',  number: '18', position: '' },
  { id: 'p_duncan',    name: 'Jaedyn Duncan',    number: '19', position: '' },
  { id: 'p_perrotta',  name: 'Luca Perrotta',    number: '20', position: '' },
  { id: 'p_zaidan',    name: 'Gabriel Zaidan',   number: '21', position: '' },
];

const FONT_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800;900&display=swap');
  .font-display { font-family: 'Outfit', system-ui, sans-serif; font-weight: 800; letter-spacing: 0.03em; }
  .font-sans-pro { font-family: 'Outfit', system-ui, sans-serif; }
  html, body { background-color: #0c0a09; color: #e7e5e4; }
  body { font-family: 'Outfit', system-ui, sans-serif; }
  input, textarea, select {
    background-color: #1c1917 !important;
    color: #e7e5e4 !important;
    color-scheme: dark;
  }
  input::placeholder, textarea::placeholder { color: #78716c !important; }
  .stripes-bg {
    background-color: #0d2818;
    background-image:
      repeating-linear-gradient(
        135deg,
        rgba(132, 204, 22, 0.10) 0px,
        rgba(132, 204, 22, 0.10) 2px,
        transparent 2px,
        transparent 14px
      ),
      linear-gradient(135deg, #0d2818 0%, #11331d 50%, #0d2818 100%);
  }
`;

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

async function storageGet(key) {
  try {
    if (typeof window !== 'undefined' && window.storage?.get) {
      return await window.storage.get(key);
    }
  } catch (e) {}
  try {
    if (typeof localStorage !== 'undefined') {
      const value = localStorage.getItem(key);
      return value !== null ? { key, value } : null;
    }
  } catch (e) {}
  return null;
}

async function storageSet(key, value) {
  try {
    if (typeof window !== 'undefined' && window.storage?.set) {
      return await window.storage.set(key, value);
    }
  } catch (e) {}
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(key, value);
      return { key, value };
    }
  } catch (e) {}
  return null;
}

function formatClock(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

// Match format. 7v7 for Canadian festivals/tournaments, 9v9 for US tournaments
// from the 2026-27 season. Single source of truth for everything the format
// implies, so no caller hardcodes a squad cap or an on-field count.
//
// `halfMin` is only a SUGGESTION used when the coach hasn't set the clock
// themselves (9v9 is normally two 30-minute halves against 7v7's 25).
const FORMATS = {
  '7v7': { key: '7v7', onField: 7, squadCap: 12, halfMin: 25 },
  '9v9': { key: '9v9', onField: 9, squadCap: 16, halfMin: 30 },
};
const DEFAULT_FORMAT = '7v7';

// Format for a game / schedule item / setup draft. Games created before the
// format field existed are all 7v7, so an absent value means 7v7 rather than
// unknown — no migration needed.
function formatOf(gameOrSetup) {
  return FORMATS[gameOrSetup?.format] || FORMATS[DEFAULT_FORMAT];
}

// Display minute for a logged event using its period + the game's half length.
// The clock resets to 0 at the start of P2, so a goal at elapsed=240s in P2 of
// a 30-min-half game should display as 34' (30 + 4), not 4'. Round half-up.
function eventDisplayMinute(event, halfLengthMin) {
  const halfLen = Number(halfLengthMin) || 25;
  const m = Math.max(1, Math.round((event?.elapsed || 0) / 60));
  return m + ((event?.period || 1) === 2 ? halfLen : 0);
}

function computeElapsed(game) {
  if (game.elapsedAtPause === undefined) {
    return game.startedAt ? Math.floor((Date.now() - game.startedAt) / 1000) : 0;
  }
  if (game.clockRunning && game.segmentStartedAt) {
    return game.elapsedAtPause + Math.floor((Date.now() - game.segmentStartedAt) / 1000);
  }
  return game.elapsedAtPause;
}

// Is the game sitting at the halftime break? Prefers the explicit `halftime`
// flag set by pauseHalfTime; falls back to the old inference for games started
// before the flag existed (and for any doc where it was never written). The
// inference alone cannot distinguish the break from an injury pause, which is
// why the flag exists — see resumeClock.
function isHalfTime(game) {
  if (!game) return false;
  if (game.halftime === true) return true;
  if (game.halftime === false) return false;
  return game.period === 1 && game.clockRunning === false;
}

function onFieldAt(game, atTs = Date.now()) {
  const onField = new Set(game.startingLineup || []);
  const subs = (game.events || [])
    .filter(e => e.type === 'SUB' && e.at <= atTs)
    .sort((a, b) => a.at - b.at);
  for (const sub of subs) {
    if (sub.playerId) onField.delete(sub.playerId);
    if (sub.subOnPlayerId) onField.add(sub.subOnPlayerId);
  }
  return onField;
}

// Resolve who is the goalie at a given timestamp by walking game.gkChanges.
// `game.gkPlayerId` is the starting GK; `game.gkChanges = [{at, gkPlayerId}, …]`
// captures every swap mid-match. Returns the player id (or null if none set).
function currentGKAt(game, atTs = Date.now()) {
  if (!game) return null;
  let current = game.gkPlayerId || null;
  const changes = (game.gkChanges || []).filter(c => c.at <= atTs).sort((a, b) => a.at - b.at);
  for (const c of changes) current = c.gkPlayerId || null;
  return current;
}

function playerSeconds(playerId, game) {
  if (!game.startingLineup) return 0;
  const starting = game.startingLineup.includes(playerId);
  const subs = (game.events || []).filter(e => e.type === 'SUB').sort((a, b) => a.at - b.at);
  const intervals = [];
  let onSince = starting ? game.startedAt : null;
  for (const sub of subs) {
    if (sub.playerId === playerId && onSince !== null) {
      intervals.push([onSince, sub.at]);
      onSince = null;
    }
    if (sub.subOnPlayerId === playerId && onSince === null) {
      onSince = sub.at;
    }
  }
  if (onSince !== null) {
    const endTs = game.status === 'finished' && game.endedAt ? game.endedAt : Date.now();
    intervals.push([onSince, endTs]);
  }
  const pauses = (game.pausePeriods || []).map(p => [p.startedAt, p.endedAt || Date.now()]);
  let totalSec = 0;
  for (const [s, e] of intervals) {
    let secs = (e - s) / 1000;
    for (const [ps, pe] of pauses) {
      const oStart = Math.max(s, ps);
      const oEnd = Math.min(e, pe);
      if (oEnd > oStart) secs -= (oEnd - oStart) / 1000;
    }
    totalSec += Math.max(0, secs);
  }
  return Math.floor(totalSec);
}

// GK-only per-game context: OPP_GOALs conceded while this player was THE GK
// (per game.gkPlayerId + game.gkChanges timeline), weighted by gkFault tag,
// plus clean-sheet credit. Falls back to on-field-as-GK heuristic for legacy
// games that pre-date the gkPlayerId field.
function gkExtrasForGame(playerId, game) {
  // Build the GK timeline: [{ from, to, gkPlayerId }]
  const startTs = game.startedAt;
  const endTs = game.status === 'finished' && game.endedAt ? game.endedAt : Date.now();
  let gkTimeline;
  if (game.gkPlayerId || (game.gkChanges && game.gkChanges.length > 0)) {
    const segments = [];
    let current = game.gkPlayerId || null;
    let segStart = startTs;
    const changes = [...(game.gkChanges || [])].sort((a, b) => a.at - b.at);
    for (const c of changes) {
      segments.push({ from: segStart, to: c.at, gkPlayerId: current });
      current = c.gkPlayerId || null;
      segStart = c.at;
    }
    segments.push({ from: segStart, to: endTs, gkPlayerId: current });
    gkTimeline = segments.filter(s => s.gkPlayerId === playerId);
  } else {
    // Legacy fallback: any time the player was on the field counts as GK time.
    const subs = (game.events || []).filter(e => e.type === 'SUB').sort((a, b) => a.at - b.at);
    const intervals = [];
    const starting = (game.startingLineup || []).includes(playerId);
    let onSince = starting ? startTs : null;
    for (const sub of subs) {
      if (sub.playerId === playerId && onSince !== null) { intervals.push({ from: onSince, to: sub.at }); onSince = null; }
      if (sub.subOnPlayerId === playerId && onSince === null) { onSince = sub.at; }
    }
    if (onSince !== null) intervals.push({ from: onSince, to: endTs });
    gkTimeline = intervals;
  }
  if (gkTimeline.length === 0) return { oppGoalsConceded: 0, concededPenalty: 0, cleanSheets: 0, secondsAsGK: 0 };
  let conceded = 0;
  let concededPenalty = 0;
  for (const e of (game.events || [])) {
    if (e.type !== 'OPP_GOAL') continue;
    if (gkTimeline.some(seg => e.at >= seg.from && e.at <= seg.to)) {
      conceded++;
      if (e.gkFault === 'gk') concededPenalty += 6;
      else if (e.gkFault === 'unstoppable') concededPenalty += 0;
      else concededPenalty += 3;
    }
  }
  const secondsAsGK = gkTimeline.reduce((sum, seg) => sum + Math.max(0, (seg.to - seg.from) / 1000), 0);
  // v2: clean-sheet credit is PRO-RATED by the share of the game spent in
  // goal. The old 60s floor handed a 5-minute relief keeper the full bonus —
  // in a 5-keeper rotation, four partial keepers each banked a whole clean
  // sheet. Fractions also sum sensibly across the season.
  const gameSeconds = Math.max(1, (endTs - startTs) / 1000);
  const cleanSheets = (conceded === 0 && game.status === 'finished')
    ? Math.min(1, secondsAsGK / gameSeconds) : 0;
  return { oppGoalsConceded: conceded, concededPenalty, cleanSheets, secondsAsGK };
}

// ===== PERFORMANCE SCORE =====
// Weighted per-20-minute composite across 4 development pillars.
// Returns { overall, attacking, defending, decisions, involvement } rounded to 1 decimal.
// `position` (optional) — if 'GK', uses goalie-specific weights so keepers aren't
// unfairly penalized by low ATK/DEC opportunity, and saves count for more.
// `gkExtras` (optional, GK only) — { oppGoalsConceded, cleanSheets } aggregated
// across the games being scored. Adds clean-sheet bonus and conceded penalty.
// The four kinds of fixture, in the order they appear in the picker. `key` is
// what gets stored and what scoring weights are keyed on; nothing else may be
// stored in `gameType`, because an unrecognised value silently scores as a
// full-weight league game.
const GAME_TYPES = [
  { key: 'scrimmage', label: 'SCRIMMAGE' },
  { key: 'festival', label: 'FESTIVAL' },
  { key: 'tournament', label: 'TOURNAMENT' },
  { key: 'league', label: 'LEAGUE' },
];

/**
 * An item's scoring type.
 *
 * `gameType` is the field the picker writes. Every game and schedule item
 * created before it existed has only the free-text `tournament` box, whose value
 * scoring read directly — so fall back to it and keep 14 games of history
 * weighted exactly as they are today. The fallback only matches when the old
 * text happens to BE a type name ("Festival", "Scrimmage"), which is what the
 * real data holds; anything else lands on `league` at weight 1.0, which is what
 * the old code did with it anyway.
 */
function gameTypeOf(item) {
  const explicit = String(item?.gameType || '').toLowerCase();
  if (GAME_TYPES.some((t) => t.key === explicit)) return explicit;
  const legacy = String(item?.tournament || '').toLowerCase();
  if (GAME_TYPES.some((t) => t.key === legacy)) return legacy;
  return 'league';
}

// Default per-action point values + pillar weights. Coaches can override these
// in Settings → Scoring Weights. `mergeWeights` fills in any missing fields so
// older saved overrides still work if new actions are added later.
const DEFAULT_WEIGHTS = {
  points: {
    GOAL_atk: 10, ASSIST_atk: 8, KEY_PASS_atk: 4, SHOT_ON_atk: 3, SHOT_OFF_atk: 1,
    SAVE_def: 7, BLOCK_def: 5, BALL_WIN_def: 5, DUEL_WIN_def: 2, DUEL_LOSE_def: -2,
    CLEAR_def: 3, KICK_OUT_def: 1,
    GIVE_GO_dec: 6, GIVE_GO_PARTNER_dec: 3, GATES_dec: 4, KEY_PASS_dec: 3, ASSIST_dec: 3,
    HOLDS_BALL_dec: -4, TURNOVER_dec: -4, CLEAN_SHEET_def: 8,
    // Discipline & set-pieces.
    FOUL_ON_atk: 2,   FOUL_BY_def: -2,
    PEN_AWARDED_atk: 6, PEN_CONCEDED_def: -8,
    OWN_GOAL_def: -10,
  },
  gkPoints: {
    GOAL_atk: 10, ASSIST_atk: 8, KEY_PASS_atk: 10, SHOT_ON_atk: 3, SHOT_OFF_atk: 1,
    SAVE_def: 10, BLOCK_def: 5, BALL_WIN_def: 5, DUEL_WIN_def: 2, DUEL_LOSE_def: -2,
    CLEAR_def: 3, KICK_OUT_def: 1,
    GIVE_GO_dec: 6, GIVE_GO_PARTNER_dec: 3, GATES_dec: 4, KEY_PASS_dec: 6, ASSIST_dec: 3,
    HOLDS_BALL_dec: -4, TURNOVER_dec: -4, CLEAN_SHEET_def: 8,
    FOUL_ON_atk: 2,   FOUL_BY_def: -2,
    PEN_AWARDED_atk: 6, PEN_CONCEDED_def: -8,
    OWN_GOAL_def: -10,
  },
  pillars: {
    outfield: { atk: 30, def: 25, dec: 30, inv: 15 },
    gk:       { atk: 10, def: 55, dec: 25, inv: 10 },
  },
  // v2 fairness knobs: empirical-Bayes shrinkage prior (virtual minutes of
  // squad-average production added to every player's rate) and per-game-type
  // weights on the season aggregate (scrimmages count half by default).
  shrinkMinutes: 12,
  gameTypes: { scrimmage: 0.5, festival: 0.75, tournament: 1.0, league: 1.0, default: 1.0 },
};

const SCORING_VERSION = 2; // bumped 2026-06: shrinkage, INV cleanup, pro-rated clean sheet, game-type weights, season own-goal fix

function mergeWeights(w) {
  return {
    points:   { ...DEFAULT_WEIGHTS.points,   ...(w?.points   || {}) },
    gkPoints: { ...DEFAULT_WEIGHTS.gkPoints, ...(w?.gkPoints || {}) },
    pillars: {
      outfield: { ...DEFAULT_WEIGHTS.pillars.outfield, ...(w?.pillars?.outfield || {}) },
      gk:       { ...DEFAULT_WEIGHTS.pillars.gk,       ...(w?.pillars?.gk       || {}) },
    },
    shrinkMinutes: (w?.shrinkMinutes != null && !Number.isNaN(Number(w.shrinkMinutes)))
      ? Number(w.shrinkMinutes) : DEFAULT_WEIGHTS.shrinkMinutes,
    gameTypes: { ...DEFAULT_WEIGHTS.gameTypes, ...(w?.gameTypes || {}) },
  };
}

// Mistake events that already cost points in DEF/DEC. They no longer count
// toward Involvement (v2): at 15% INV weight they refunded ~12% of every
// penalty as "activity". Own goals are likewise excluded from INV.
const INV_EXCLUDED = new Set(['TURNOVER', 'DUEL_LOSE', 'FOUL_BY']);

// Pressure multiplier (plan 4.3): a decision (DEC-pillar) action executed under
// defensive pressure is worth more than the same action in space. Only DEC
// points are scaled — ATK/DEF/INV are unchanged — and only when the event is
// tagged `pressure==='pressure'`. Positive DEC actions get the bonus; the DEC
// mistakes (HOLDS_BALL, TURNOVER) are NOT amplified (losing the ball under
// pressure shouldn't be penalized extra — the base penalty already stands).
const PRESSURE_DEC_MULT = 1.5;

// Raw pillar POINTS (not rates) for one player over an event list, with
// outfield↔GK point values blended by f. Shared by the per-game score, the
// season aggregate (which weights per-game points by game type), and the
// squad-average prior used for shrinkage.
function pillarPoints(playerId, events, f, gkExtras, W) {
  const c = {};
  const cPressure = {}; // per-type counts of THIS player's events tagged pressure
  let partnerCount = 0; // give & go wall-pass credits earned by this player
  let partnerPressureCount = 0; // give & go partner credits earned under pressure
  let ownGoals = 0;     // own goals attributed via OPP_GOAL.ownGoalById
  let invCount = 0;     // involvement = positive/neutral actions only (v2)
  for (const e of events) {
    // Only real play events feed the score. Skip anything that isn't a known,
    // non-silent EVENT_TYPE — this excludes bookkeeping events that aren't in
    // EVENT_TYPES (SUB, GK_CHANGE) and silent ones (POSITION tactical-board
    // drags), so none of them inflate the Involvement pillar.
    const def = EVENT_TYPES[e.type];
    if (!def || def.silent) continue;
    const underPressure = e.pressure === 'pressure';
    if (e.playerId === playerId) {
      c[e.type] = (c[e.type] || 0) + 1;
      if (underPressure) cPressure[e.type] = (cPressure[e.type] || 0) + 1;
      if (!INV_EXCLUDED.has(e.type)) invCount++;
    }
    if (e.type === 'GIVE_GO' && e.partnerId === playerId) {
      partnerCount++;
      if (underPressure) partnerPressureCount++;
      invCount++;
    }
    if (e.type === 'OPP_GOAL' && e.ownGoalById === playerId) {
      ownGoals++; // costs DEF points; deliberately NOT involvement
    }
  }
  // Blended point value for a key: outfield (W.points) at f=0, GK (W.gkPoints) at f=1.
  const po = W.points, pg = W.gkPoints;
  const pt = (k) => { const a = po[k] || 0; return a + f * ((pg[k] || 0) - a); };
  const atk = (
    (c.GOAL || 0)        * pt('GOAL_atk') +
    (c.ASSIST || 0)      * pt('ASSIST_atk') +
    (c.KEY_PASS || 0)    * pt('KEY_PASS_atk') +
    (c.SHOT_ON || 0)     * pt('SHOT_ON_atk') +
    (c.SHOT_OFF || 0)    * pt('SHOT_OFF_atk') +
    (c.FOUL_ON || 0)     * pt('FOUL_ON_atk') +
    (c.PEN_AWARDED || 0) * pt('PEN_AWARDED_atk')
  );
  // Clean-sheet / conceded credit applies only to actual GK time (f > 0).
  const concededPenalty = f > 0 ? ((gkExtras && gkExtras.concededPenalty) || 0) : 0;
  const cleanSheets = f > 0 ? ((gkExtras && gkExtras.cleanSheets) || 0) : 0;
  const dfn = (
    (c.SAVE || 0)         * pt('SAVE_def') +
    (c.BLOCK || 0)        * pt('BLOCK_def') +
    (c.BALL_WIN || 0)     * pt('BALL_WIN_def') +
    (c.CLEAR || 0)        * pt('CLEAR_def') +
    (c.KICK_OUT || 0)     * pt('KICK_OUT_def') +
    (c.DUEL_WIN || 0)     * pt('DUEL_WIN_def') +
    (c.DUEL_LOSE || 0)    * pt('DUEL_LOSE_def') +
    (c.FOUL_BY || 0)      * pt('FOUL_BY_def') +
    (c.PEN_CONCEDED || 0) * pt('PEN_CONCEDED_def') +
    ownGoals              * pt('OWN_GOAL_def') +
    (f > 0 ? (-concededPenalty + cleanSheets * pt('CLEAN_SHEET_def')) : 0)
  );
  const dec = (
    (c.GIVE_GO || 0)    * pt('GIVE_GO_dec') +
    partnerCount        * pt('GIVE_GO_PARTNER_dec') +
    (c.GATES || 0)      * pt('GATES_dec') +
    (c.KEY_PASS || 0)   * pt('KEY_PASS_dec') +
    (c.ASSIST || 0)     * pt('ASSIST_dec') +
    (c.HOLDS_BALL || 0) * pt('HOLDS_BALL_dec') +
    (c.TURNOVER || 0)   * pt('TURNOVER_dec')
  );
  // Pressure bonus: the EXTRA DEC points (mult−1) for positive DEC actions made
  // under pressure. Only DEC_PRESSURE_EVENTS + the give-&-go partner credit are
  // amplified; DEC mistakes are deliberately left at base cost.
  const decPressureBonus = (PRESSURE_DEC_MULT - 1) * (
    (cPressure.GIVE_GO  || 0) * pt('GIVE_GO_dec') +
    partnerPressureCount      * pt('GIVE_GO_PARTNER_dec') +
    (cPressure.GATES    || 0) * pt('GATES_dec') +
    (cPressure.KEY_PASS || 0) * pt('KEY_PASS_dec') +
    (cPressure.ASSIST   || 0) * pt('ASSIST_dec')
  );
  return { atk, def: dfn, dec: dec + decPressureBonus, inv: invCount };
}

// Squad-average per-20-min pillar rates — the shrinkage prior. Computed with
// outfield point values for everyone (it's a prior, not a per-player score).
// `perPlayer` = [{ playerId, minutes }] for everyone who played.
function computeSquadRates(perPlayer, events, W) {
  const tot = { atk: 0, def: 0, dec: 0, inv: 0 };
  let totMin = 0;
  for (const { playerId, minutes } of perPlayer) {
    if (!minutes || minutes <= 0) continue;
    const p = pillarPoints(playerId, events, 0, null, W);
    tot.atk += p.atk; tot.def += p.def; tot.dec += p.dec; tot.inv += p.inv;
    totMin += minutes;
  }
  const ph = Math.max(totMin, 1) / 20;
  return { atk: tot.atk / ph, def: tot.def / ph, dec: tot.dec / ph, inv: tot.inv / ph };
}

// Squad DISTRIBUTION (mean + population std-dev) of each pillar's per-20-min
// rate across the players who played — the reference for z-scoring (plan 2.4).
// This answers "how does this player compare to the squad" rather than the raw
// rate's absolute units. `scored` = [{ atk, def, dec, inv }] of per-player
// pillar RATES (already shrunk — we compare the same numbers the coach sees, so
// the comparison is self-consistent). Needs ≥2 players for a meaningful spread;
// returns null std where it can't be computed (caller then hides the percentile).
function computeSquadPillarStats(scored) {
  const keys = ['atk', 'def', 'dec', 'inv'];
  const n = scored.length;
  const out = {};
  for (const k of keys) {
    if (n < 2) { out[k] = { mean: n === 1 ? scored[0][k] : 0, std: null, n }; continue; }
    const vals = scored.map(s => Number(s[k]) || 0);
    const mean = vals.reduce((a, b) => a + b, 0) / n;
    const variance = vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n;
    out[k] = { mean, std: Math.sqrt(variance), n };
  }
  return out;
}

// Standard-normal CDF (Abramowitz & Stegun 7.1.26 erf approximation) → the
// percentile for a z-score. Pure, no deps. Returns 0..100.
function normalCdf(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804014327 * Math.exp(-z * z / 2);
  let p = d * t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  p = z > 0 ? 1 - p : p;
  return p;
}

// A player's pillar rate → percentile vs squad (0..100), or null when the
// squad spread is undefined (fewer than 2 players, or zero variance — everyone
// identical, so "rank" is meaningless). value/stats units must match.
function pillarPercentile(value, stat) {
  if (!stat || stat.std == null || !(stat.std > 0)) return null;
  const z = ((Number(value) || 0) - stat.mean) / stat.std;
  return Math.round(normalCdf(z) * 100);
}

function computePerformanceScore(playerId, events, minutesPlayed, gkFraction, gkExtras = {}, weights, squadRates = null) {
  if (minutesPlayed <= 0) return { overall: 0, attacking: 0, defending: 0, decisions: 0, involvement: 0 };
  const W = mergeWeights(weights);
  // gkFraction ∈ [0,1] = share of minutes this player spent in goal. Point values
  // AND pillar weights are blended outfield↔GK by it, so a part-time keeper is
  // scored by their actual role mix (not 100% keeper for a brief stint).
  const f = Math.max(0, Math.min(1, Number(gkFraction) || 0));
  const pts = pillarPoints(playerId, events, f, gkExtras, W);
  // v2 shrinkage (empirical Bayes): every player gets `shrinkMinutes` virtual
  // minutes of squad-average production blended in, so a 6-minute cameo with
  // one lucky goal can't top a 40-minute starter. Converges to the raw rate
  // as real minutes accumulate. Skipped when no squadRates are provided.
  const M = squadRates ? Math.max(0, Number(W.shrinkMinutes) || 0) : 0;
  const rate = (p, sq) => M > 0
    ? (p + (M / 20) * (sq || 0)) / ((minutesPlayed + M) / 20)
    : p / (minutesPlayed / 20);
  const attacking = rate(pts.atk, squadRates && squadRates.atk);
  const defending = rate(pts.def, squadRates && squadRates.def);
  const decisions = rate(pts.dec, squadRates && squadRates.dec);
  const involvement = rate(pts.inv, squadRates && squadRates.inv);
  // Blend pillar weights outfield↔GK by the same fraction.
  const PO = W.pillars.outfield, PG = W.pillars.gk;
  const pil = {
    atk: PO.atk + f * (PG.atk - PO.atk),
    def: PO.def + f * (PG.def - PO.def),
    dec: PO.dec + f * (PG.dec - PO.dec),
    inv: PO.inv + f * (PG.inv - PO.inv),
  };
  const overall = (pil.atk * attacking + pil.def * defending + pil.dec * decisions + pil.inv * involvement) / 100;
  const r = (n) => Math.round(n * 10) / 10;
  return { overall: r(overall), attacking: r(attacking), defending: r(defending), decisions: r(decisions), involvement: r(involvement) };
}

function formatDate(iso) {
  const d = typeof iso === 'string' ? iso.slice(0, 10) : '';
  return new Date(d + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

// Soccer team names are usually "<city> <team-name>" (e.g. "Leamington FC",
// "Belle River FC", "Amherstburg Fusion"). Split on the LAST whitespace so
// multi-word cities like "Belle River" stay intact. Single-word names
// ("Test", "Opponent") fall back to no city overline.
function splitTeamName(full) {
  const s = String(full || '').trim();
  if (!s) return { city: '', name: '' };
  const i = s.lastIndexOf(' ');
  if (i === -1) return { city: '', name: s };
  return { city: s.slice(0, i).trim(), name: s.slice(i + 1).trim() };
}

function formatTime12(time24) {
  const [h, m] = time24.split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${suffix}`;
}

// The fixture's scoring TYPE — one of GAME_TYPES, never free text. Visibility
// escalates with stakes: scrimmage = quiet violet, festival = medium-pop teal,
// tournament = loudest amber. `league` gets neutral stone: it is the default and
// does not need to shout. Colours match what TournamentChip used to infer from
// the old overloaded text field, so nothing on screen shifts.
function GameTypeChip({ value }) {
  const t = String(value || '').toLowerCase();
  if (!t) return null;
  const cls = t === 'scrimmage' ? 'bg-violet-500/10 text-violet-400 border-violet-500/30'
            : t === 'festival'  ? 'bg-teal-500/20 text-teal-200 border-teal-400/50'
            : t === 'tournament' ? 'bg-amber-400/25 text-amber-100 border-amber-300/60'
            : 'bg-stone-700/40 text-stone-300 border-stone-600/60';
  return (
    <span className={`inline-block ${cls} border font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded`}>
      {t.toUpperCase()}
    </span>
  );
}

// The competition's NAME ("Canton Cup", "Gatorade Invitational"). One neutral
// style, deliberately: colouring by inference is what GameTypeChip now owns, and
// a name should not pick up scrimmage violet because of a substring.
//
// Suppressed when the name IS the type. Legacy items stored the type in this
// same free-text box, so `tournament: 'Festival'` would otherwise render a
// FESTIVAL type chip next to an identical FESTIVAL name chip. Comparison is
// against the RESOLVED type, so a name only vanishes when it is genuinely the
// duplicate — "Festival of Champions" still shows.
function TournamentChip({ value, gameType }) {
  if (!value) return null;
  if (String(value).trim().toLowerCase() === String(gameType || '').toLowerCase()) return null;
  return (
    <span className="inline-block bg-stone-700/40 text-stone-200 border border-stone-600/60 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
      {String(value).toUpperCase()}
    </span>
  );
}

// Match format badge. Only rendered for the NON-default format: every game
// before 2026-27 is 7v7, so tagging those adds noise to every row while telling
// the coach nothing. A visible chip means "this one was different".
function FormatChip({ value }) {
  if (!value || value === DEFAULT_FORMAT || !FORMATS[value]) return null;
  return (
    <span className="inline-block bg-sky-500/20 text-sky-200 border border-sky-400/50 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
      {String(value).toUpperCase()}
    </span>
  );
}

// Auth for the R2/Stream worker. This bundle is a public static file, so it
// cannot hold a secret — anything constant here is readable by anyone who opens
// devtools. Send the signed-in user's Firebase ID token instead; the worker
// verifies it against Google's public certs, which makes access per-user and
// revocable. Returns {} when signed out, so the worker answers 401 rather than
// the call failing in some other, more confusing way.
async function workerAuth() {
  try {
    const u = window.fbAuth && window.fbAuth.currentUser;
    if (u) return { idToken: await u.getIdToken() };
  } catch (e) {}
  return {};
}

// POST to the worker with the caller's ID token merged into the body. Returns a
// promise, so it drops into both `await workerPost(...)` and `.then()` chains —
// several call sites are fire-and-forget and can't be made async.
function workerPost(path, body = {}) {
  return workerAuth().then(auth => fetch(`${R2_UPLOAD_WORKER}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...auth, ...body }),
  }));
}

/* ---------- ACCESS GATE (request-access model, 2026-08-18) ----------
 * Sign-in (the shell AuthGate) proves WHO you are; this layer decides WHAT
 * you are: coach, approved family member, pending requester, or unknown.
 * Membership lives in allowedUsers/{email} (doc id = LOWERCASED email; the
 * Firestore rules compare token.email.lower()). We LISTEN to our own doc
 * rather than get() it so the waiting screen flips into the app the moment
 * the coach approves — no reload, no re-sign-in.
 */
function useAccess() {
  const [access, setAccess] = useState({ status: 'checking', playerIds: [], role: null });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbUserInfo) return undefined;
    const email = (window.fbUserInfo.email || '').toLowerCase();
    if (!email) { setAccess({ status: 'unknown', playerIds: [], role: null }); return undefined; }
    let cancelled = false;
    // Cold-start watchdog: if neither snapshot nor error arrives, re-arm.
    const stuck = setTimeout(() => { if (!cancelled && nonce < 3) setNonce(nonce + 1); }, 15000);
    const unsub = window.fbDb.collection('allowedUsers').doc(email).onSnapshot(
      (doc) => {
        if (cancelled) return;
        clearTimeout(stuck);
        if (doc.exists) {
          const d = doc.data() || {};
          setAccess({
            status: d.role === 'coach' ? 'coach' : 'allowed',
            playerIds: Array.isArray(d.playerIds) ? d.playerIds : [],
            role: d.role || 'parent',
          });
        } else {
          // Not on the list: distinguish "never asked" from "asked, waiting"
          // so reopening the app lands back on the waiting screen.
          window.fbDb.collection('accessRequests').doc(email).get().then((r) => {
            if (!cancelled) setAccess({ status: r.exists ? 'pending' : 'unknown', playerIds: [], role: null });
          }).catch(() => {
            if (!cancelled) setAccess({ status: 'unknown', playerIds: [], role: null });
          });
        }
      },
      () => {
        if (cancelled) return;
        clearTimeout(stuck);
        // Listener failure (flaky signal, rules mid-deploy): retry, then fall
        // through to the request screen rather than spin forever.
        if (nonce < 3) setTimeout(() => { if (!cancelled) setNonce(nonce + 1); }, 2000);
        else setAccess({ status: 'unknown', playerIds: [], role: null });
      }
    );
    return () => { cancelled = true; clearTimeout(stuck); unsub(); };
  }, [nonce]);
  return [access, setAccess];
}

function RequestAccessScreen({ onRequested }) {
  const u = (typeof window !== 'undefined' && window.fbUserInfo) || {};
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { if (typeof window !== 'undefined') window.__appReady = true; }, []);
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const email = (u.email || '').toLowerCase();
      await window.fbDb.collection('accessRequests').doc(email).set({
        email, name: u.name || '', photo: u.photo || '',
        note: note.trim(), ts: Date.now(), status: 'pending',
      });
      trackUsage('access:request', {}, false);
      onRequested();
    } catch (e) {
      setError(e && e.message ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <div className="min-h-screen stripes-bg text-white flex items-center justify-center p-6">
      <style>{FONT_STYLES}</style>
      <div className="max-w-sm w-full space-y-5">
        <div className="text-center space-y-3">
          <img src="./stompers_logo.png" alt="" className="w-20 h-20 mx-auto drop-shadow" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
          <h1 className="font-display text-3xl leading-tight">STOMPERS FAMILIES</h1>
          <p className="text-white/70 text-sm">
            This app is private to LaSalle Stompers 2016 families. Send a
            request and the coach will let you in.
          </p>
        </div>
        <div className="bg-white/10 rounded-2xl p-4 flex items-center gap-3">
          {u.photo && <img src={u.photo} className="w-10 h-10 rounded-full" referrerPolicy="no-referrer" />}
          <div className="min-w-0">
            <div className="font-bold text-sm truncate">{u.name || 'Google account'}</div>
            <div className="text-xs text-white/60 truncate">{u.email}</div>
          </div>
        </div>
        <div>
          <label className="text-xs font-bold tracking-wider text-white/60">WHICH PLAYER ARE YOU WITH? (OPTIONAL)</label>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Liam's grandpa"
            className="mt-1 w-full bg-stone-950/60 border border-white/20 rounded-xl px-3 py-3 text-sm text-white placeholder-white/30"
          />
        </div>
        <button
          onClick={submit}
          disabled={busy}
          className="w-full bg-lime-500 hover:bg-lime-400 text-stone-950 font-display text-lg rounded-xl py-3 active:scale-[0.98] disabled:opacity-60"
        >
          {busy ? 'SENDING…' : 'REQUEST ACCESS'}
        </button>
        {error && <p className="text-red-300 text-xs text-center">{error}</p>}
        <button onClick={() => { if (window.fbAuth) window.fbAuth.signOut(); }} className="w-full text-xs text-white/50 underline">
          Sign out
        </button>
      </div>
    </div>
  );
}

function AccessWaitingScreen() {
  const u = (typeof window !== 'undefined' && window.fbUserInfo) || {};
  useEffect(() => { if (typeof window !== 'undefined') window.__appReady = true; }, []);
  return (
    <div className="min-h-screen stripes-bg text-white flex items-center justify-center p-6">
      <style>{FONT_STYLES}</style>
      <div className="max-w-sm w-full text-center space-y-4">
        <div className="text-5xl">⏳</div>
        <h1 className="font-display text-3xl">REQUEST SENT</h1>
        <p className="text-white/70 text-sm">
          Waiting for the coach to approve <span className="font-bold">{u.email}</span>.
          The app opens by itself the moment he does — you can close this in
          the meantime.
        </p>
        <button onClick={() => { if (window.fbAuth) window.fbAuth.signOut(); }} className="text-xs text-white/50 underline">
          Not you? Sign out
        </button>
      </div>
    </div>
  );
}

/* ---------- ROUTER (client-side, 2026-06-12) ----------
 * Every surface lives in this one bundle, yet navigation used to go through
 * full page loads (?live=, ?coach) — each tap re-ran the bundle, re-inited
 * Firebase, re-restored auth and re-opened Firestore with NO timeout/retry,
 * so any stall in that chain read as an infinite spinner (constant on iPads,
 * masked on fast iPhones). Now: routes are state, navigation swaps views
 * in place, URLs stay shareable (?live= deep links parse on load and
 * pushState keeps them updated).
 *
 * History coordination: route entries carry {route, gameId}. CoachApp's own
 * view stack pushes {coachView} above the coach route entry, and modals push
 * {modal} above that — each layer's popstate handler only acts on its own
 * tagged entries, so the three coexist.
 */
function parseRoute() {
  const params = (typeof window !== 'undefined')
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams('');
  const liveGameId = params.get('live');
  if (liveGameId) return { kind: 'live', gameId: liveGameId };
  if (params.has('coach')) return { kind: 'coach' };
  return { kind: 'home' };
}

export default function App() {
  const [route, setRoute] = useState(parseRoute);
  const [access, setAccess] = useAccess();
  const navDepthRef = useRef(0);

  useEffect(() => {
    // Tag the base entry so popstate can route back to it.
    if (typeof window === 'undefined') return undefined;
    const st = window.history.state;
    if (!st || !st.route) {
      const r = parseRoute();
      window.history.replaceState({ route: r.kind, gameId: r.gameId || null }, '', window.location.href);
    }
    // Global navigation API (used by converted anchors across surfaces).
    window.__navigate = (r, opts) => {
      const url = r.kind === 'live' ? `./?live=${r.gameId}` : r.kind === 'coach' ? './?coach' : './';
      const state = { route: r.kind, gameId: r.gameId || null };
      if (opts && opts.replace) window.history.replaceState(state, '', url);
      else { window.history.pushState(state, '', url); navDepthRef.current += 1; }
      window.scrollTo(0, 0);
      setRoute(r);
    };
    // Back that degrades gracefully on deep links (no in-app history yet).
    window.__navBack = () => {
      if (navDepthRef.current > 0) { navDepthRef.current -= 1; window.history.back(); }
      else window.__navigate({ kind: 'home' }, { replace: true });
    };
    const onPop = () => {
      const s = window.history.state;
      if (s && s.route) setRoute({ kind: s.route, gameId: s.gameId || undefined });
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Access gating comes BEFORE routing: unknown/pending accounts see only
  // the request/waiting screens no matter which URL they arrived on.
  if (access.status === 'checking') return <PublicLoadingScreen />;
  if (access.status === 'unknown') {
    return <RequestAccessScreen onRequested={() => setAccess({ status: 'pending', playerIds: [], role: null })} />;
  }
  if (access.status === 'pending') return <AccessWaitingScreen />;

  if (route.kind === 'live') return <LiveScorePage key={route.gameId} gameId={route.gameId} />;
  if (route.kind !== 'coach') return <PublicHomePage access={access} />;
  return <CoachApp />;
}

function CoachApp() {
  // ---- coach app below ----
  const [unlocked, setUnlocked] = useState(false);
  const [roster, setRoster] = useState([]);
  const [games, setGames] = useState([]);
  const [schedule, setSchedule] = useState([]);
  // TeamSnap-synced calendar events (practices, tournaments, team events).
  // Written every 15 min by the Worker cron; read-only here. Local dev has no
  // Firestore, so this stays empty and the calendar simply shows fewer kinds.
  const [teamsnapEvents, setTeamsnapEvents] = useState([]);
  // When the cron last mirrored the feed, from the `zz-sync-meta` sentinel doc.
  // NOT any event's `modified` field — that is the feed's LAST-MODIFIED, i.e.
  // when the coach last edited the event in TeamSnap, which would read "22h
  // ago" one second after a clean sync and never advance while nobody edits.
  const [teamsnapSyncedAt, setTeamsnapSyncedAt] = useState(null);
  // Entry keys the coach has hidden. A Set of `buildCalendarModel` entry keys
  // (e.g. `ts:9230745-366776389`), loaded from teams/main/calendarHidden.
  const [hiddenEventKeys, setHiddenEventKeys] = useState(() => new Set());
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  // Team-wide Cloudflare Stream Live Input. Provisioned once, reused every
  // game so the coach can paste a single RTMPS URL + key into the Insta360
  // / OBS app and never touch it again. Shape: { uid, rtmpsUrl, streamKey,
  // hlsUrl, iframeUrl?, customerCode?, createdAt }.
  const [teamLiveInput, setTeamLiveInput] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [view, setView] = useState('home');
  const [editingPlayer, setEditingPlayer] = useState(null);
  const [activeGameId, setActiveGameId] = useState(null);
  const [viewingGameId, setViewingGameId] = useState(null);
  const [pendingEvent, setPendingEvent] = useState(null);
  const [toast, setToast] = useState(null);
  // Broadcast-TV penalty flash (live coach view): { kind, side, saved, playerLabel, ourScore, oppScore }
  const [penaltyFlash, setPenaltyFlash] = useState(null);
  // Clock-driven voice auto-recording: imperative handle to the live recorder,
  // plus a stash for the mic stream pre-acquired inside the kickoff tap (iOS).
  const voiceRef = useRef(null);
  const pendingMicRef = useRef(null);
  const [tick, setTick] = useState(0);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [pendingGameSetup, setPendingGameSetup] = useState(null);
  // When editing a scheduled game's pre-picked squad, we stash the schedule
  // item id (+ a snapshot of the current squad) so the SquadPickerView can
  // save back into the schedule and return to the schedule screen.
  const [editingScheduleSquad, setEditingScheduleSquad] = useState(null);
  // When returning from the squad picker, the calendar re-mounts; this hint
  // tells it to drop back into edit mode for the same item.
  const [resumeScheduleEditId, setResumeScheduleEditId] = useState(null);
  // Which screen sent the coach to the squad picker, so the detour returns to
  // where it started rather than always to the old schedule screen. Defaults to
  // the calendar: 'schedule' is no longer reachable from the dugout.
  const [squadPickerReturnView, setSquadPickerReturnView] = useState('calendar');
  // The season-wide opponent rename modal. It lives on App rather than inside a
  // view because the calendar and the schedule screen both open it.
  const [showOpponentManager, setShowOpponentManager] = useState(false);

  const askConfirm = (message, onYes, opts = {}) => {
    setConfirmDialog({ message, onYes, danger: !!opts.danger, yesLabel: opts.yesLabel || 'YES' });
  };

  // Recurring opponents dataset — derived from past games (most-recent first)
  // and the schedule. Grassroots/festival teams play the same opponents over
  // and over; we surface them as datalist suggestions on the game-setup and
  // schedule-edit inputs so the coach can pick instead of retyping (and
  // misspelling — which would break the schedule↔game-doc dedupe on the
  // public home card).
  const opponentSuggestions = useMemo(() => {
    const seen = new Map(); // key = lowercase trimmed name → first-seen canonical
    const add = (raw) => {
      const v = (raw || '').trim();
      if (!v) return;
      const k = v.toLowerCase();
      if (!seen.has(k)) seen.set(k, v);
    };
    // Games: newest first (by date, then endedAt).
    const gs = [...games].sort((a, b) => {
      const dc = new Date(b.date || 0) - new Date(a.date || 0);
      if (dc !== 0) return dc;
      return (b.endedAt || 0) - (a.endedAt || 0);
    });
    for (const g of gs) add(g.opponent);
    // Then schedule entries (typically future).
    for (const s of schedule) add(s.opponent);
    return Array.from(seen.values());
  }, [games, schedule]);

  // Per-view browser-history stack. Every time `view` changes to a deeper
  // step (gameSetup → squad → lineup → activeGame), we pushState. When the
  // user swipes back / hits the OS back gesture, popstate fires and we
  // restore the previous view from our mirrored stack. When in-app code
  // jumps back (e.g. an onBack button that calls setView('home') directly,
  // skipping intermediate steps), we call history.go(-N) to keep the
  // browser stack and our stack in sync. The `expectedPops` counter
  // distinguishes a popstate triggered by our own history.go(-N) (which
  // shouldn't run the back logic again) from a physical swipe.
  const viewStackRef = useRef(['home']);
  const expectedPopsRef = useRef(0);
  useEffect(() => {
    const stack = viewStackRef.current;
    const top = stack[stack.length - 1];
    if (view === top) return;
    const idx = stack.lastIndexOf(view);
    if (idx >= 0 && idx < stack.length - 1) {
      // In-app navigation BACK to a previous view in the stack: collapse the
      // stack and rewind the browser by the matching number of steps.
      const steps = stack.length - 1 - idx;
      viewStackRef.current = stack.slice(0, idx + 1);
      expectedPopsRef.current += 1; // history.go(-N) fires one popstate
      window.history.go(-steps);
    } else {
      // Forward navigation to a new view: push one entry.
      viewStackRef.current = [...stack, view];
      window.history.pushState({ coachView: view }, '');
    }
  }, [view]);

  useEffect(() => {
    const onPop = () => {
      if (expectedPopsRef.current > 0) {
        expectedPopsRef.current -= 1;
        return; // our own history.go(-N) — view already updated
      }
      // Modal coordination (see useModalHistory): a pop issued by a modal's
      // cleanup, or arriving while a modal is mounted, belongs to the modal
      // layer — the view stack must not move underneath it.
      if ((window.__modalPopGuard || 0) > 0) {
        window.__modalPopGuard -= 1;
        return;
      }
      if ((window.__modalStack || []).length > 0) return;
      const stack = viewStackRef.current;
      if (stack.length <= 1) return; // already at home; let the browser exit
      stack.pop();
      const prev = stack[stack.length - 1] || 'home';
      setView(prev);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // Auto-unlock for coaches based on Firestore role
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbUserInfo) return;
    const email = window.fbUserInfo.email?.toLowerCase();
    const toPublic = () => (window.__navigate
      ? window.__navigate({ kind: 'home' }, { replace: true })
      : window.location.replace('./'));
    if (!email) { toPublic(); return; }
    let cancelled = false;
    // The role lookup is a single get() with no SDK retry — when the first
    // attempt stalls ("Checking access…" forever), re-issue it a few times
    // before concluding anything.
    let attempts = 0;
    const tryUnlock = () => {
      attempts += 1;
      const timer = setTimeout(() => { if (!cancelled && attempts < 4) tryUnlock(); }, 8000);
      window.fbDb.collection('allowedUsers').doc(email).get().then((doc) => {
        if (cancelled) return;
        clearTimeout(timer);
        if (doc.exists && doc.data().role === 'coach') setUnlocked(true);
        else toPublic();
      }).catch(() => {
        if (cancelled) return;
        clearTimeout(timer);
        if (attempts >= 4) toPublic();
        else setTimeout(() => { if (!cancelled) tryUnlock(); }, 2000);
      });
    };
    tryUnlock();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    (async () => {
      let loadedRoster = null;
      try {
        const r = await storageGet(STORAGE_KEYS.ROSTER);
        if (r?.value) {
          const parsed = JSON.parse(r.value);
          if (Array.isArray(parsed) && parsed.length > 0) loadedRoster = parsed;
        }
      } catch (e) {}
      if (!loadedRoster) {
        loadedRoster = SEED_ROSTER;
        try { await storageSet(STORAGE_KEYS.ROSTER, JSON.stringify(SEED_ROSTER)); } catch (e) {}
      }
      setRoster(loadedRoster);

      try {
        const g = await storageGet(STORAGE_KEYS.GAMES);
        if (g?.value) setGames(JSON.parse(g.value));
      } catch (e) {}
      try {
        const w = await storageGet(STORAGE_KEYS.WEIGHTS);
        if (w?.value) setWeights(mergeWeights(JSON.parse(w.value)));
      } catch (e) {}
      try {
        const s = await storageGet(STORAGE_KEYS.SCHEDULE);
        if (s?.value) setSchedule(JSON.parse(s.value));
      } catch (e) {}
      setLoaded(true);
    })();
  }, []);

  // Team-wide live stream key loader (local-dev path; replaced in production
  // by the Firestore team-doc listener via _sync_html.py).
  useEffect(() => {
    (async () => {
      try {
        const tli = await storageGet(STORAGE_KEYS.TEAM_LIVE_INPUT);
        if (tli?.value) setTeamLiveInput(JSON.parse(tli.value));
      } catch (e) {}
    })();
  }, []);

  // Synced TeamSnap events. Separate effect (and a subcollection, not a team-doc
  // field) so it can be added without touching the loaders _sync_html.py
  // rewrites by exact source text.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('teams').doc('main').collection('teamsnapEvents')
      .onSnapshot(
        (snap) => {
          const list = [];
          let stamp = null;
          snap.forEach((d) => {
            // The cron writes its own clock as one more doc in this collection.
            // It is a sentinel, not an event — real ids look like
            // `9230745-366776389`, so it must not reach the merge.
            if (d.id === 'zz-sync-meta') { stamp = d.data()?.syncedAt ?? null; return; }
            list.push({ uid: d.id, ...d.data() });
          });
          setTeamsnapEvents(list);
          setTeamsnapSyncedAt(stamp);
        },
        (err) => console.error('teamsnapEvents listen failed', err)
      );
    return () => unsub();
  }, []);

  // Coach-hidden calendar entries. Its own collection, not a field on the
  // teamsnapEvents docs: the cron PATCHes every one of those docs on each run,
  // so a hide flag stored there would be gone within 15 minutes.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('teams').doc('main').collection('calendarHidden')
      .onSnapshot(
        (snap) => {
          const s = new Set();
          snap.forEach((d) => { const k = d.data()?.key; if (k) s.add(k); });
          setHiddenEventKeys(s);
        },
        (err) => console.error('calendarHidden listen failed', err)
      );
    return () => unsub();
  }, []);

  useEffect(() => {
    if (view !== 'activeGame') return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [view]);

  // Telemetry watchdog handshake: real content is on screen (see shell).
  useEffect(() => { if (loaded && typeof window !== 'undefined') window.__appReady = true; }, [loaded]);

  // Cold-start watchdog: the production Firestore listeners are injected by
  // the build with fixed deps and can't be re-armed in place — a wedged first
  // load gets up to two controlled reloads (sessionStorage-guarded against
  // loops), then stays on the loading screen for the human to judge.
  useEffect(() => {
    if (loaded) {
      try { sessionStorage.removeItem('coachColdStartReloads'); } catch (e) {}
      return undefined;
    }
    const t = setTimeout(() => {
      try {
        const k = 'coachColdStartReloads';
        const tries = Number(sessionStorage.getItem(k) || 0);
        if (tries < 2) { sessionStorage.setItem(k, String(tries + 1)); window.location.reload(); }
      } catch (e) {}
    }, 15000);
    return () => clearTimeout(t);
  }, [loaded]);

  // Usage analytics: one ping per coach-app section per session, feeding the
  // owner-only VIEWERS page (the owner himself is excluded inside trackUsage).
  useEffect(() => {
    if (view) trackUsage('coach:' + view);
  }, [view]);

  // Team-level live-input creds moved to the coach-only subdoc
  // teams/main/private/liveInput (2026-08). Production loads them here; the
  // legacy teamLiveInput field on the team doc is no longer read or written.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('teams').doc('main')
      .collection('private').doc('liveInput').onSnapshot(
        (doc) => {
          const d = doc.exists ? doc.data() : null;
          setTeamLiveInput(d && d.uid ? d : null);
        },
        () => {}
      );
    return unsub;
  }, []);

  const persistRoster = async (next) => {
    setRoster(next);
    try { await storageSet(STORAGE_KEYS.ROSTER, JSON.stringify(next)); } catch (e) {}
  };

  // Accepts either the next array or an updater (prev => next). The updater
  // form is what makes concurrent writes safe: React hands the CURRENT state
  // to the callback, so two taps in the same batch compose instead of the
  // second silently overwriting the first from a stale closure. See updateGame.
  const persistGames = async (nextOrFn) => {
    let resolved = null;
    setGames(prev => {
      resolved = typeof nextOrFn === 'function' ? nextOrFn(prev) : nextOrFn;
      return resolved;
    });
    try { await storageSet(STORAGE_KEYS.GAMES, JSON.stringify(resolved)); } catch (e) {}
  };

  const persistWeights = async (next) => {
    const merged = mergeWeights(next);
    setWeights(merged);
    try { await storageSet(STORAGE_KEYS.WEIGHTS, JSON.stringify(merged)); } catch (e) {}
  };

  const persistSchedule = async (next) => {
    setSchedule(next);
    try { await storageSet(STORAGE_KEYS.SCHEDULE, JSON.stringify(next)); } catch (e) {}
  };

  const persistTeamLiveInput = async (next) => {
    setTeamLiveInput(next);
    try {
      if (next) await storageSet(STORAGE_KEYS.TEAM_LIVE_INPUT, JSON.stringify(next));
      else await storageSet(STORAGE_KEYS.TEAM_LIVE_INPUT, '');
    } catch (e) {}
  };

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 1800);
  };

  // ':' and '/' are not usable in a Firestore doc id, and '__x__' is reserved
  // (Firestore rejects it outright, failing the whole write). Deterministic, so
  // unhide deletes exactly the doc hide created.
  // Entry keys look like `ts:9230745-366776389`; ':' and '/' cannot appear in a
  // Firestore doc id. The `h-` prefix is not decoration: Firestore REJECTS any id
  // matching `__…__` as reserved (400 INVALID_ARGUMENT), and a bare sanitise
  // would pass such a key straight through. Real keys are always prefixed today,
  // so this is belt-and-braces — but that exact rejection already took the sync
  // down once, and it fails the whole batch rather than one write.
  const hideDocId = (key) => `h-${String(key).replace(/[^A-Za-z0-9-]/g, '_')}`;

  const hideEvent = async (key) => {
    if (!window.fbDb || !key) return;
    try {
      await window.fbDb.collection('teams').doc('main').collection('calendarHidden')
        .doc(hideDocId(key)).set({ key, hiddenAt: Date.now() });
      showToast('Hidden from the calendar');
    } catch (e) { console.error('hide failed', e); showToast('⚠️ Could not hide that'); }
  };

  const unhideEvent = async (key) => {
    if (!window.fbDb || !key) return;
    try {
      await window.fbDb.collection('teams').doc('main').collection('calendarHidden')
        .doc(hideDocId(key)).delete();
      showToast('👁️ Restored');
    } catch (e) { console.error('unhide failed', e); showToast('⚠️ Could not restore that'); }
  };

  // Show the big TV-style penalty banner, then auto-dismiss (~2.8s).
  const triggerPenaltyFlash = (info) => {
    const _id = uid();
    setPenaltyFlash({ ...info, _id });
    setTimeout(() => setPenaltyFlash(cur => (cur && cur._id === _id) ? null : cur), 2800);
  };

  const upsertPlayer = (player) => {
    if (player.id) {
      persistRoster(roster.map(p => p.id === player.id ? player : p));
    } else {
      persistRoster([...roster, { ...player, id: uid() }]);
    }
    setEditingPlayer(null);
    setView('roster');
  };

  const removePlayer = (id) => {
    persistRoster(roster.filter(p => p.id !== id));
  };

  const startNewGame = (opponent, isHome, tournament, startingLineup, gkPlayerId, squad, halfLengthMin, homeColor, awayColor, liveInput, youtubeVideoId, kickoffPositions, autoRecord, format, gameType) => {
    const now = Date.now();
    const squadIds = (squad && squad.length > 0) ? squad : (startingLineup || []);
    // Seed POSITION events from the kickoff tactical board so the in-game
    // board opens with the formation the coach already set, and post-game
    // analytics has the kickoff slot for each starter.
    const initialEvents = [];
    if (Array.isArray(kickoffPositions)) {
      const startingSet = new Set(startingLineup || []);
      let i = 0;
      for (const s of kickoffPositions) {
        if (!s || !s.playerId || !startingSet.has(s.playerId)) continue;
        const cx = Math.max(0.04, Math.min(0.96, Number(s.x)));
        const cy = Math.max(0.04, Math.min(0.96, Number(s.y)));
        if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
        initialEvents.push({
          id: uid(),
          type: 'POSITION',
          playerId: s.playerId,
          period: 1,
          elapsed: 0,
          at: now + (i++),
          x: cx,
          y: cy,
        });
      }
    }
    const game = {
      id: uid(),
      opponent: opponent || 'Opponent',
      // `tournament` is now the competition's NAME and may be blank; `gameType`
      // is what scoring weights key on. Persist the tag explicitly so a played
      // game no longer depends on gameTypeOf's legacy text fallback — that
      // fallback only ever matched when the free text happened to BE a type
      // name, which is why a typo like "Gatorate Invitational" scored as a
      // full-weight league game.
      tournament: tournament || '',
      gameType: gameTypeOf({ gameType, tournament }),
      isHome: !!isHome,
      format: FORMATS[format] ? format : DEFAULT_FORMAT,
      halfLengthMin: halfLengthMin || 25,
      homeColor: homeColor || '#0a0a0a',
      awayColor: awayColor || '#dc2626',
      date: new Date().toLocaleDateString('en-CA'),
      ourScore: 0,
      oppScore: 0,
      period: 1,
      status: 'active',
      startedAt: now,
      clockRunning: true,
      elapsedAtPause: 0,
      segmentStartedAt: now,
      events: initialEvents,
      squad: squadIds,
      startingLineup: startingLineup || [],
      gkPlayerId: gkPlayerId || null,
      gkChanges: [],
      pausePeriods: [],
      autoRecord: !!autoRecord,
      // Only the PUBLIC piece of the live input lives on the game doc
      // (families play hlsUrl); rtmpsUrl/streamKey are credentials and go
      // to the coach-only private subdoc right below.
      ...(liveInput ? { liveInput: { hlsUrl: liveInput.hlsUrl || null, createdAt: liveInput.createdAt || now } } : {}),
      ...(youtubeVideoId ? { youtubeVideoId } : {}),
    };
    persistGames(prev => [game, ...prev]);
    if (liveInput && typeof window !== 'undefined' && window.fbDb) {
      window.fbDb.collection('teams').doc('main').collection('games').doc(game.id)
        .collection('private').doc('liveInput').set(liveInput)
        .catch((e) => console.error('live-input private save error:', e));
    }
    setActiveGameId(game.id);
    setView('activeGame');
  };

  // Mutate one game from its LATEST state. This used to map over the `games`
  // closure, so two writes dispatched in the same React batch both started
  // from the pre-batch snapshot and the second discarded the first — a goal
  // logged by rapid tapping could vanish, since logEvent auto-chains
  // GOAL -> ASSIST. Passing an updater down defers the read to commit time.
  const updateGame = (id, mutator) => {
    persistGames(prev => prev.map(g => g.id === id ? mutator(g) : g));
  };

  const logEvent = (gameId, eventType, playerId, extras = {}) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const ev = EVENT_TYPES[eventType];
    const elapsed = computeElapsed(game);
    const event = {
      id: uid(),
      type: eventType,
      playerId: playerId || null,
      period: game.period,
      elapsed,
      at: Date.now(),
      // Provenance (v2): how this event was captured. 'live' = sideline tap;
      // future capture paths (bookmark-confirmed, voice-confirmed,
      // video-added) stamp their own value via extras.
      source: 'live',
      ...extras,
    };
    // Derive from the LATEST game, not the `game` read above: logEvent
    // auto-chains GOAL -> ASSIST, so two taps can land in one React batch and
    // the second would otherwise append to a pre-first-tap events array and
    // recompute the score from the pre-goal value — erasing the goal.
    persistGames(prev => prev.map(g => g.id === gameId ? {
      ...g,
      events: [...g.events, event],
      ourScore: g.ourScore + (ev.delta === 'us' ? 1 : 0),
      oppScore: g.oppScore + (ev.delta === 'opp' ? 1 : 0),
    } : g));

    const player = roster.find(p => p.id === playerId);
    let playerLabel = '';
    if (player) playerLabel = `${player.name} #${player.number}`;
    else if (ev.requiresPlayer && playerId === null) playerLabel = 'Unknown';
    let suffix = '';
    if (eventType === 'GIVE_GO' && extras.partnerId) {
      const partner = roster.find(p => p.id === extras.partnerId);
      if (partner) suffix = ` → 🤝 ${partner.name} #${partner.number}`;
    }
    showToast(`${ev.emoji} ${ev.label}${playerLabel ? ` · ${playerLabel}` : ''}${suffix}`);

    // Broadcast-TV flash for penalty outcomes (converted pens arrive as
    // GOAL/OPP_GOAL with extras.penalty; misses are the dedicated types).
    const isPenGoal = (eventType === 'GOAL' || eventType === 'OPP_GOAL') && extras.penalty;
    const isPenMiss = eventType === 'PEN_MISSED' || eventType === 'OPP_PEN_MISSED';
    if (isPenGoal || isPenMiss) {
      const usSide = eventType === 'GOAL' || eventType === 'PEN_MISSED';
      triggerPenaltyFlash({
        kind: isPenGoal ? 'goal' : 'miss',
        side: usSide ? 'us' : 'them',
        // 'us miss' / 'them goal' = our keeper saved / their conversion etc.
        saved: eventType === 'OPP_PEN_MISSED',
        playerLabel,
        ourScore: updated.ourScore,
        oppScore: updated.oppScore,
      });
    }

    // Penalty goals have no assist — skip the ASSIST prompt for them.
    if (eventType === 'GOAL' && playerId && !extras.penalty) {
      setPendingEvent({ type: 'ASSIST', excludePlayerId: playerId, skippable: true });
    } else {
      setPendingEvent(null);
    }
  };

  const undoLastEvent = (gameId) => {
    const game = games.find(g => g.id === gameId);
    if (!game || game.events.length === 0) return;
    // Resolve WHICH event to drop against the latest state — an undo racing a
    // just-logged event would otherwise compute its index from a stale list
    // and remove the wrong one.
    persistGames(prev => prev.map(g => {
      if (g.id !== gameId) return g;
      // Skip silent POSITION events — undo should target the coach's last
      // visible action (GOAL, SUB, etc.), not their last tactical drag.
      let lastIdx = -1;
      for (let i = g.events.length - 1; i >= 0; i--) {
        if (g.events[i].type !== 'POSITION') { lastIdx = i; break; }
      }
      if (lastIdx === -1) return g;
      const ev = EVENT_TYPES[g.events[lastIdx].type];
      return {
        ...g,
        events: g.events.filter((_, i) => i !== lastIdx),
        ourScore: g.ourScore - (ev?.delta === 'us' ? 1 : 0),
        oppScore: g.oppScore - (ev?.delta === 'opp' ? 1 : 0),
      };
    }));
    showToast('Last event removed');
  };

  const deleteEvent = (gameId, eventId) => {
    persistGames(prev => prev.map(g => {
      if (g.id !== gameId) return g;
      const ev = g.events.find(e => e.id === eventId);
      if (!ev) return g;
      const evType = EVENT_TYPES[ev.type];
      return {
        ...g,
        events: g.events.filter(e => e.id !== eventId),
        ourScore: g.ourScore - (evType?.delta === 'us' ? 1 : 0),
        oppScore: g.oppScore - (evType?.delta === 'opp' ? 1 : 0),
      };
    }));
  };

  // Post-game tagging: patch zone/pressure/decision on an existing event.
  // Pass null/undefined for a field to remove that tag.
  // tagsConfirmed/tagDismissed are confirm-queue bookkeeping flags: confirmed
  // = coach reviewed the tags (even if left empty), dismissed = coach said
  // "don't ask about this event again". Both drain the queue permanently.
  const updateEvent = (gameId, eventId, patch) => {
    persistGames(prev => prev.map(g => {
      if (g.id !== gameId) return g;
      const events = g.events.map(e => {
        if (e.id !== eventId) return e;
        const next = { ...e };
        for (const k of ['zone', 'pressure', 'decision', 'tagsConfirmed', 'tagDismissed']) {
          if (k in patch) {
            if (patch[k] == null) delete next[k];
            else next[k] = patch[k];
          }
        }
        return next;
      });
      return { ...g, events };
    }));
  };

  // Confirm-queue: turn a live BOOKMARK (timestamp-only reflex tap) into a
  // real event. Replaces the bookmark in place, keeping its clock position
  // (period/elapsed/at) so the video index still lines up, and stamps the
  // provenance the bookmark channel earns. Score deltas mirror logEvent.
  const confirmBookmark = (gameId, bookmarkId, { type, playerId = null, extras = {} }) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const bm = game.events.find(e => e.id === bookmarkId && e.type === 'BOOKMARK');
    const ev = EVENT_TYPES[type];
    if (!bm || !ev) return;
    const confirmed = {
      // Keep the bookmark's id: the pipeline's broadcastEvents index is keyed
      // by event id, so the confirmed event stays cueable in the reel without
      // waiting for a pipeline re-run.
      id: bm.id,
      type,
      playerId: playerId || null,
      period: bm.period,
      elapsed: bm.elapsed,
      at: bm.at,
      source: 'bookmark-confirmed',
      ...extras,
    };
    persistGames(prev => prev.map(g => g.id === gameId ? {
      ...g,
      events: g.events.map(e => e.id === bookmarkId ? confirmed : e),
      ourScore: g.ourScore + (ev.delta === 'us' ? 1 : 0),
      oppScore: g.oppScore + (ev.delta === 'opp' ? 1 : 0),
    } : g));
    showToast(`${ev.emoji} ${ev.label} confirmed from 🔖`);
  };

  // Confirm-queue (voice): the post-game narration pipeline writes extracted
  // DRAFT events to the game doc's `voiceDrafts` array (loaded onto the game
  // object alongside events). Confirming one APPENDS a real event (source=
  // 'voice-confirmed'; mirrors confirmBookmark but appends since a draft has no
  // live bookmark to replace) AND removes it from voiceDrafts — both in ONE
  // persistGames write (which writes the whole game doc), so there's no race
  // with a separate Firestore update. Dismiss just removes the draft.
  const confirmVoiceDraft = (gameId, draft, playerId = null) => {
    const game = games.find(g => g.id === gameId);
    const ev = draft && EVENT_TYPES[draft.type];
    if (!game) return;
    if (!ev) {
      // A draft whose type this app doesn't know can never become an event.
      // Say so and drop it, rather than no-op and leave an Accept button that
      // does nothing sitting in the queue forever.
      showToast(`⚠️ "${draft?.type}" isn't a loggable event — draft dismissed`);
      dismissVoiceDraft(gameId, draft?.id);
      return;
    }
    const { type, period, elapsed, quote } = draft;
    // `elapsed` is game-clock seconds within the half, but `at` is wall clock,
    // so a 2nd-half draft has to skip the break. Assuming H2 begins exactly
    // halfLengthMin after kickoff ignored it entirely and landed every 2nd-half
    // voice event minutes early in `at`-keyed ordering. Derive the real start
    // from the pauses, the same way the Python side does; fall back to the
    // nominal half length only when the pause data is missing.
    const pausedMs = (game.pausePeriods || []).reduce(
      (acc, p) => acc + Math.max(0, (p.endedAt || p.startedAt || 0) - (p.startedAt || 0)), 0);
    const halfMs = (game.halfLengthMin || 25) * 60000;
    const h2OffsetMs = period === 2 ? halfMs + pausedMs : 0;
    const at = (game.startedAt || 0) + h2OffsetMs + (elapsed || 0) * 1000;
    const confirmed = {
      id: `v_${period}_${elapsed}_${type}_${playerId || 'na'}`,
      type, playerId: playerId || null, period, elapsed, at,
      source: 'voice-confirmed',
      ...(quote ? { voiceQuote: quote } : {}),
    };
    persistGames(prev => prev.map(g => g.id === gameId ? {
      ...g,
      events: [...g.events, confirmed],
      voiceDrafts: (g.voiceDrafts || []).filter(d => d.id !== draft.id),
      ourScore: g.ourScore + (ev.delta === 'us' ? 1 : 0),
      oppScore: g.oppScore + (ev.delta === 'opp' ? 1 : 0),
    } : g));
    showToast(`${ev.emoji} ${ev.label} confirmed from 🎙`);
  };

  const dismissVoiceDraft = (gameId, draftId) => {
    persistGames(prev => prev.map(g => g.id === gameId
      ? { ...g, voiceDrafts: (g.voiceDrafts || []).filter(d => d.id !== draftId) }
      : g));
  };

  const endGame = (gameId) => {
    updateGame(gameId, g => {
      const now = Date.now();
      const additional = g.clockRunning && g.segmentStartedAt
        ? Math.floor((now - g.segmentStartedAt) / 1000) : 0;
      const pp = [...(g.pausePeriods || [])];
      if (pp.length > 0 && pp[pp.length-1].endedAt === null) {
        pp[pp.length-1] = { ...pp[pp.length-1], endedAt: now };
      }
      return {
        ...g,
        status: 'finished',
        endedAt: now,
        clockRunning: false,
        elapsedAtPause: (g.elapsedAtPause || 0) + additional,
        segmentStartedAt: null,
        pausePeriods: pp,
        // A game ended from the halftime screen must not stay flagged as being
        // at the break — isHalfTime() would keep reporting true for it.
        halftime: false,
      };
    });
    setActiveGameId(null);
    setView('home');
    showToast('Game saved');
  };

  const deleteGame = async (gameId) => {
    // Coach "Delete game permanently" — nukes everything we know about:
    //   1. R2 objects under tv_view/<id>/ + clips/<id>/   (via worker)
    //   2. Firestore subcollections analytics/ + clips/   (paged batch delete)
    //   3. Firestore game doc teams/main/games/<id>
    //   4. Local games array
    // Each step is best-effort: if R2 wipe fails we still continue so the
    // game disappears from the public list; orphan R2 files can be cleaned
    // up by re-running this action or the audit script.
    try {
      await workerPost(`/game/${gameId}/videos/delete`);
    } catch (e) { console.warn('R2 wipe failed (continuing):', e); }
    // Voice recordings die with the game (privacy) — separate route so
    // "Delete videos only" keeps them. Best-effort until the worker with
    // this route is deployed.
    try {
      await workerPost(`/game/${gameId}/voice/delete`);
    } catch (e) { console.warn('voice wipe failed (continuing):', e); }

    try {
      if (window.fbDb) {
        const gameRef = window.fbDb.collection('teams').doc('main')
          .collection('games').doc(gameId);
        for (const sub of ['analytics', 'clips', 'public']) {
          const qs = await gameRef.collection(sub).get();
          await Promise.all(qs.docs.map(d => d.ref.delete()));
        }
        await gameRef.delete();
      }
    } catch (e) {
      console.error('Firestore delete failed:', e);
      showToast('⚠️ Cloud delete failed — see console');
    }

    persistGames(prev => prev.filter(g => g.id !== gameId));
    setView('home');
    showToast('🗑 Game deleted');
  };

  const deleteGameVideos = async (gameId) => {
    // Coach "Delete videos only" — wipes R2 reels + clips and clears the
    // public-facing video URL fields on the game doc, but KEEPS the
    // analytics subcollection (per-player stats, GK positioning, etc.) so
    // you can re-render the videos later by re-running the pipeline.
    try {
      const r = await workerPost(`/game/${gameId}/videos/delete`);
      if (!r.ok) throw new Error(`worker returned ${r.status}`);
    } catch (e) {
      console.error('R2 wipe failed:', e);
      showToast('⚠️ R2 wipe failed — see console');
      return;
    }

    try {
      if (window.fbDb && window.firebase) {
        const FieldValue = window.firebase.firestore.FieldValue;
        const gameRef = window.fbDb.collection('teams').doc('main')
          .collection('games').doc(gameId);
        // Clear the public-facing broadcast slice on the game doc itself.
        await gameRef.set({
          videoHighlightsUrl: FieldValue.delete(),
          videoHighlightsDurationS: FieldValue.delete(),
          videoFullGameUrl: FieldValue.delete(),
          videoFullGameDurationS: FieldValue.delete(),
          broadcastEvents: FieldValue.delete(),
        }, { merge: true });
        // Also clear the coach-facing fields on analytics/v1 so the
        // AnalyticsPanel's WATCH buttons disappear too.
        await gameRef.collection('analytics').doc('v1').set({
          tv_reel_url: FieldValue.delete(),
          tv_reel_duration_s: FieldValue.delete(),
          auto_highlights_url: FieldValue.delete(),
          auto_highlights_duration_s: FieldValue.delete(),
        }, { merge: true });
      }
    } catch (e) {
      console.warn('Firestore URL clear failed (R2 already wiped):', e);
    }
    showToast('🗑 Videos deleted (stats kept)');
  };

  const pauseHalfTime = (gameId) => {
    updateGame(gameId, g => {
      const now = Date.now();
      const additional = g.clockRunning && g.segmentStartedAt
        ? Math.floor((now - g.segmentStartedAt) / 1000) : 0;
      return {
        ...g,
        clockRunning: false,
        elapsedAtPause: (g.elapsedAtPause || 0) + additional,
        segmentStartedAt: null,
        pausePeriods: [...(g.pausePeriods || []), { startedAt: now, endedAt: null }],
        // Halftime is a STATE, not just "period 1 with the clock stopped" —
        // that inference can't tell the break apart from an injury pause, so
        // resuming the clock from the scorebug used to strand the game in
        // period 1 for the whole second half.
        halftime: true,
      };
    });
    showToast('⏸ Half time — clock paused');
  };

  const startSecondHalf = (gameId) => {
    updateGame(gameId, g => {
      const now = Date.now();
      const pp = [...(g.pausePeriods || [])];
      if (pp.length > 0 && pp[pp.length-1].endedAt === null) {
        pp[pp.length-1] = { ...pp[pp.length-1], endedAt: now };
      }
      return {
        ...g,
        clockRunning: true,
        period: 2,
        // Stash the 1st half's final elapsed before zeroing the clock, so the
        // "← Back to 1st half" undo can restore it. Without this the undo is
        // itself lossy: the clock restarts at 00:00 and every later 1st-half
        // event carries a wrong `elapsed` — the anchor the post-game identity
        // pipeline maps events onto video time with.
        elapsedAtHalfTime: computeElapsed(g),
        elapsedAtPause: 0,
        segmentStartedAt: now,
        pausePeriods: pp,
        halftime: false,
      };
    });
    showToast('▶ 2nd half started');
  };

  const resumeFirstHalf = (gameId) => {
    updateGame(gameId, g => {
      const now = Date.now();
      const pp = [...(g.pausePeriods || [])];
      if (pp.length > 0 && pp[pp.length-1].endedAt === null) {
        pp[pp.length-1] = { ...pp[pp.length-1], endedAt: now };
      }
      // Restore the elapsed we were at when the half ended. Falls back to the
      // current value for games paused before `elapsedAtHalfTime` existed.
      const restored = g.elapsedAtHalfTime != null
        ? g.elapsedAtHalfTime
        : (g.elapsedAtPause || 0);
      return {
        ...g,
        clockRunning: true,
        period: 1,
        elapsedAtPause: restored,
        segmentStartedAt: now,
        pausePeriods: pp,
        halftime: false,
      };
    });
    showToast('▶ Back to 1st half');
  };

  const pauseClock = (gameId) => {
    updateGame(gameId, g => {
      if (!g.clockRunning) return g;
      const now = Date.now();
      const additional = g.segmentStartedAt
        ? Math.floor((now - g.segmentStartedAt) / 1000) : 0;
      return {
        ...g,
        clockRunning: false,
        elapsedAtPause: (g.elapsedAtPause || 0) + additional,
        segmentStartedAt: null,
        pausePeriods: [...(g.pausePeriods || []), { startedAt: now, endedAt: null }],
        // Mark this pause as NOT the break. Without it, a 1st-half injury pause
        // falls through to isHalfTime()'s legacy inference (period 1 + clock
        // stopped), shows the HALF TIME screen, and refuses to resume.
        halftime: false,
      };
    });
    showToast('⏸ Clock paused');
  };

  const resumeClock = (gameId) => {
    // At halftime the only ways forward are START 2ND HALF (period 2) or
    // "← Back to 1st half" — both of which set the period deliberately. A
    // plain resume here would restart the clock still in period 1, so the
    // entire second half would be logged as period 1 with `elapsed` running
    // past the half length, silently corrupting every downstream mapping.
    const g0 = games.find(g => g.id === gameId);
    if (g0 && isHalfTime(g0)) {
      showToast('⏸ Half time — use START 2ND HALF');
      return;
    }
    updateGame(gameId, g => {
      if (g.clockRunning) return g;
      const now = Date.now();
      const pp = [...(g.pausePeriods || [])];
      if (pp.length > 0 && pp[pp.length-1].endedAt === null) {
        pp[pp.length-1] = { ...pp[pp.length-1], endedAt: now };
      }
      return {
        ...g,
        clockRunning: true,
        segmentStartedAt: now,
        pausePeriods: pp,
      };
    });
    showToast('▶ Clock resumed');
  };

  const logSubEvent = (gameId, offPlayerId, onPlayerId) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    // Validate against the LIVE lineup, not the picker's snapshot. Protects
    // against any race where the SUB picker was rendered with stale state
    // (e.g. rapid back-to-back taps before Firestore round-tripped).
    const liveOn = onFieldAt(game);
    if (!liveOn.has(offPlayerId)) {
      const p = roster.find(r => r.id === offPlayerId);
      showToast(`⚠️ ${p?.name || 'That player'} is already off the field`);
      setPendingEvent(null);
      return;
    }
    if (liveOn.has(onPlayerId)) {
      const p = roster.find(r => r.id === onPlayerId);
      showToast(`⚠️ ${p?.name || 'That player'} is already on the field`);
      setPendingEvent(null);
      return;
    }
    if (offPlayerId === onPlayerId) {
      showToast('⚠️ Pick a different player to sub on');
      setPendingEvent(null);
      return;
    }
    const subAt = Date.now();
    const elapsed = computeElapsed(game);
    const event = {
      id: uid(),
      type: 'SUB',
      playerId: offPlayerId,
      subOnPlayerId: onPlayerId,
      period: game.period,
      elapsed,
      at: subAt,
    };
    // Tactical-board continuity: if the player coming off has a known
    // position on the board, inherit it for the player coming on so the
    // formation stays intact across subs.
    const lastOffPos = [...game.events].reverse().find(
      e => e.type === 'POSITION' && e.playerId === offPlayerId
    );
    const inheritedEvents = [event];
    if (lastOffPos && typeof lastOffPos.x === 'number' && typeof lastOffPos.y === 'number') {
      inheritedEvents.push({
        id: uid(),
        type: 'POSITION',
        playerId: onPlayerId,
        period: game.period,
        elapsed,
        at: subAt,
        x: lastOffPos.x,
        y: lastOffPos.y,
      });
    }
    persistGames(prev => prev.map(g => g.id === gameId
      ? { ...g, events: [...g.events, ...inheritedEvents] } : g));
    const off = roster.find(p => p.id === offPlayerId);
    const on = roster.find(p => p.id === onPlayerId);
    showToast(`🔄 ${on?.name || '?'} IN · ${off?.name || '?'} OUT`);
    // If the player going off was the current GK, immediately prompt for the new keeper.
    const wasGK = currentGKAt(updated, subAt - 1) === offPlayerId;
    if (wasGK) {
      setPendingEvent({ type: 'NEW_GK', defaultGK: onPlayerId, at: subAt });
    } else {
      setPendingEvent(null);
    }
  };

  // Bulk lineup change: swap several players in one pass. Emits paired SUB
  // events for each net swap (one OFF player pairs with one ON player) so
  // minutes-played stays accurate. Extras get standalone SUB rows with the
  // "missing" side null — these are tolerated by playerSeconds / onFieldAt
  // (they iterate independently).
  //
  // `pairing` optionally supplies the off→on correspondence and each incoming
  // player's board slot: [{ offId, onId, x, y }]. The multi-sub flow passes it
  // so incoming players inherit a deliberate position (see
  // assignIncomingToSlots); without it, off and on are paired by index, which
  // is arbitrary but preserves the original halftime behaviour.
  const bulkReplaceLineup = (gameId, nextOnFieldIds, pairing = null) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const currentOn = onFieldAt(game);
    const next = new Set(nextOnFieldIds);
    const offNow = [...currentOn].filter(id => !next.has(id));
    const onNow  = [...nextOnFieldIds].filter(id => !currentOn.has(id));
    if (offNow.length === 0 && onNow.length === 0) {
      showToast('Lineup unchanged');
      return;
    }
    const baseAt = Date.now();
    const elapsed = computeElapsed(game);
    const newEvents = [];

    // Rows to emit: explicit pairing when given, else index-paired.
    const rows = pairing
      ? pairing.map(p => ({ offId: p.offId || null, onId: p.onId || null, x: p.x, y: p.y }))
      : Array.from({ length: Math.max(offNow.length, onNow.length) }, (_, i) => ({
          offId: offNow[i] || null,
          onId: onNow[i] || null,
        }));

    rows.forEach((row, i) => {
      const at = baseAt + i; // distinct timestamps so onFieldAt orders deterministically
      newEvents.push({
        id: uid(),
        type: 'SUB',
        playerId: row.offId,
        subOnPlayerId: row.onId,
        period: game.period,
        elapsed,
        at,
      });
      // Give the incoming player a board spot so the formation is intact and
      // the coach only drags the ones they actually want to move.
      if (row.onId && typeof row.x === 'number' && typeof row.y === 'number') {
        newEvents.push({
          id: uid(),
          type: 'POSITION',
          playerId: row.onId,
          period: game.period,
          elapsed,
          at,
          x: row.x,
          y: row.y,
        });
      }
    });

    persistGames(prev => prev.map(g => g.id === gameId
      ? { ...g, events: [...g.events, ...newEvents] } : g));
    const nOn = rows.filter(r => r.onId).length;
    const nOff = rows.filter(r => r.offId).length;
    showToast(`🔄 Lineup updated · ${nOn} on / ${nOff} off`);

    // If the keeper went off in this batch, prompt for the new one immediately —
    // same guard as the single-sub path, so the GK is never left undefined.
    const gkOff = rows.find(r => r.offId && currentGKAt(game, baseAt - 1) === r.offId);
    if (gkOff) {
      setPendingEvent({ type: 'NEW_GK', defaultGK: gkOff.onId || null, at: baseAt });
    }
  };

  // Silent: writes a POSITION event from the tactical board. No toast, no
  // score change, no RECENT feed pollution. Skips if the new spot is within
  // 3% of the last logged spot for this player (drag jitter / accidental).
  const addPositionEvent = (gameId, playerId, x, y) => {
    if (!gameId || !playerId) return;
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const cx = Math.max(0.04, Math.min(0.96, x));
    const cy = Math.max(0.04, Math.min(0.96, y));
    const last = [...game.events].reverse().find(
      e => e.type === 'POSITION' && e.playerId === playerId
    );
    if (last && Math.hypot((last.x ?? 0) - cx, (last.y ?? 0) - cy) < 0.03) return;
    const event = {
      id: uid(),
      type: 'POSITION',
      playerId,
      period: game.period,
      elapsed: computeElapsed(game),
      at: Date.now(),
      x: cx,
      y: cy,
    };
    persistGames(prev => prev.map(g => g.id === gameId
      ? { ...g, events: [...g.events, event] }
      : g));
  };

  // Batched variant: write many POSITION events in a single state update.
  // Used by the tactical board RESET button — calling addPositionEvent in a
  // loop would race because each call captures the stale `games` closure.
  // `slots` is an array of { playerId, x, y }. Each entry still goes through
  // the 3% jitter dedupe vs the player's last logged spot.
  const addPositionEventsBatch = (gameId, slots) => {
    if (!gameId || !slots || slots.length === 0) return;
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const lastByPlayer = {};
    for (const e of game.events) {
      if (e.type === 'POSITION') lastByPlayer[e.playerId] = e;
    }
    const elapsed = computeElapsed(game);
    const now = Date.now();
    const newEvents = [];
    for (const s of slots) {
      if (!s.playerId) continue;
      const cx = Math.max(0.04, Math.min(0.96, s.x));
      const cy = Math.max(0.04, Math.min(0.96, s.y));
      const last = lastByPlayer[s.playerId];
      if (last && Math.hypot((last.x ?? 0) - cx, (last.y ?? 0) - cy) < 0.03) continue;
      newEvents.push({
        id: uid(),
        type: 'POSITION',
        playerId: s.playerId,
        period: game.period,
        elapsed,
        at: now + newEvents.length, // preserve order within the batch
        x: cx,
        y: cy,
      });
    }
    if (newEvents.length === 0) return;
    persistGames(prev => prev.map(g => g.id === gameId
      ? { ...g, events: [...g.events, ...newEvents] }
      : g));
  };

  const setGameGK = (gameId, newGKPlayerId, atTs) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const now = atTs || Date.now();
    const prevGKId = currentGKAt(game, now - 1);
    const elapsed = computeElapsed(game);
    const onField = onFieldAt(game, now - 1);

    // Only the NEW events are built here; they are appended to the latest
    // events array inside the updater below, so a concurrent write isn't
    // overwritten by a copy of the pre-write list.
    const newEvents = [];

    // If swapping to a new GK who's currently on the bench, auto-sub them on
    // for the old GK (who must come off to make room). This only fires for the
    // standalone "SWAP GK" flow — when this is called as the follow-up to a
    // SUB-triggered GK pick, the new GK is already on the field.
    if (newGKPlayerId && prevGKId && newGKPlayerId !== prevGKId
        && onField.has(prevGKId) && !onField.has(newGKPlayerId)) {
      newEvents.push({
        id: uid(),
        type: 'SUB',
        playerId: prevGKId,
        subOnPlayerId: newGKPlayerId,
        period: game.period,
        elapsed,
        at: now,
      });
    }

    // Always log a visible GK_CHANGE event so the swap appears in the feed.
    newEvents.push({
      id: uid(),
      type: 'GK_CHANGE',
      playerId: newGKPlayerId,
      prevGKId: prevGKId || null,
      period: game.period,
      elapsed,
      at: now + 1, // ordered after the SUB above
    });

    const change = { at: now + 1, gkPlayerId: newGKPlayerId || null };
    persistGames(prev => prev.map(g => g.id === gameId ? {
      ...g,
      events: [...(g.events || []), ...newEvents],
      gkChanges: [...(g.gkChanges || []), change],
    } : g));
    const p = roster.find(pl => pl.id === newGKPlayerId);
    showToast(`🧤 ${p?.name || 'No GK'} now in goal`);
    setPendingEvent(null);
  };

  // Where each player has been pinned on the board, over every game we have
  // (including the one in progress). Feeds the multi-sub position matching.
  // Recomputed only when a POSITION event is added — cheap, but it walks every
  // game's event list, so it shouldn't run on every tick of the match clock.
  //
  // ⚠ MUST stay above the early returns below. It used to sit after them, which
  // made the hook COUNT depend on `unlocked`/`loaded`: the first render bailed at
  // "Checking access…" having run N hooks, then the unlocked re-render ran N+1,
  // and React threw #310 ("rendered more hooks than during the previous render")
  // — a black DUGOUT screen. Any new hook goes here, never after a return.
  const bandHist = useMemo(
    () => bandHistory(games),
    [games.reduce((n, g) => n + (g.events?.length || 0), 0), games.length]
  );

  // Season-wide opponent rename: opponent spelling drives the schedule↔game
  // dedupe, so a typo has to be fixable across every game AND schedule entry at
  // once. Hoisted out of the old schedule screen so the calendar owns it.
  const renameOpponent = async (oldName, newName) => {
    const o = (oldName || '').trim().toLowerCase();
    const n = (newName || '').trim();
    if (!o || !n) return 0;
    const matches = (s) => (s || '').trim().toLowerCase() === o;
    let count = 0;
    const nextSchedule = schedule.map((s) => {
      if (matches(s.opponent)) { count++; return { ...s, opponent: n }; }
      return s;
    });
    await Promise.all([
      persistGames(prev => prev.map((g) => {
        if (matches(g.opponent)) { count++; return { ...g, opponent: n }; }
        return g;
      })),
      persistSchedule(nextSchedule),
    ]);
    return count;
  };

  // Send the coach to the match-day squad picker for a schedule item, and
  // remember where to come back to.
  // `format` sizes the squad picker, and the calendar's onEditSquad payload omits
  // it (id/opponent/squadIds only). Reading it back off `schedule` would get the
  // STALE array: CalendarView calls onSaveEntry then onEditSquad synchronously, so
  // setSchedule has not re-rendered yet. A ref written by the save is the only
  // value that is current at that instant.
  const lastSavedGameRef = useRef(null);
  const openScheduleSquadPicker = (item, returnView) => {
    const fresh = lastSavedGameRef.current?.id === item.id ? lastSavedGameRef.current : null;
    const saved = schedule.find(s => s.id === item.id);
    setSquadPickerReturnView(returnView);
    setEditingScheduleSquad({
      itemId: item.id,
      opponent: item.opponent,
      format: item.format ?? fresh?.format ?? saved?.format,
      squadIds: item.squadIds || fresh?.squadIds || saved?.squadIds || [],
    });
    setView('scheduleSquad');
  };

  // Add-or-edit for the calendar's GameForm sheet. Mirrors
  // the old schedule form — same field set, same generated id shape — so
  // an item created here is indistinguishable from one created on the old screen.
  //
  // Games and non-games write DISJOINT field sets. A practice has no opponent,
  // so the old `if (!v.opponent) return` would have discarded every one of them
  // without a word; and writing a game's squad/colours onto a practice would
  // give the coach a READY badge on a keeper session.
  const saveCalendarEntry = (v, scheduleId) => {
    const type = v.type || 'game';
    if (!v.date) return;
    if (type === 'game' && !v.opponent) return;
    const fields = type === 'game'
      ? {
          type: 'game',
          opponent: v.opponent,
          date: v.date,
          time: v.time || '',
          gameType: v.gameType || 'league',
          tournament: v.tournament,
          location: v.location,
          field: v.field,
          isHome: v.isHome,
          format: v.format,
          halfLengthMin: v.halfLengthMin,
          homeColor: v.homeColor,
          awayColor: v.awayColor,
          squadIds: Array.isArray(v.squadIds) ? v.squadIds : [],
        }
      : {
          type,
          title: v.title || '',
          date: v.date,
          time: v.time || '',
          // Tag and competition name apply to sessions too — a practice at the
          // Canton Cup. An earlier pass dropped `tournament` here because the
          // form hid the input; now the form offers both, so both must persist
          // or the coach's pick would be silently discarded on save.
          gameType: v.gameType || 'league',
          tournament: v.tournament,
          location: v.location,
          field: v.field,
        };
    const label = type === 'game'
      ? `vs ${v.opponent}`
      : (v.title || ENTRY_TYPE_LABEL[type] || 'event').toLowerCase();
    if (scheduleId) {
      lastSavedGameRef.current = { id: scheduleId, ...fields };
      persistSchedule(schedule.map(s => (s.id === scheduleId ? { ...s, ...fields } : s)));
      showToast(`✏️ Updated ${label}`);
      return;
    }
    const item = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), ...fields };
    lastSavedGameRef.current = item;
    persistSchedule([...schedule, item]);
    const dateLabel = new Date(v.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' });
    showToast(`✅ Added ${label} · ${dateLabel}`);
  };

  // Pre-fill from a scheduled item and always route through the full
  // Game Setup → Squad → Lineup flow so the coach can review and tweak anything
  // (half length, colors, squad) before kickoff. Hoisted out of the HomeView
  // mount so the calendar's START button runs the identical path.
  const startScheduledGame = (item) => {
    setPendingGameSetup({
      opponent: item.opponent,
      tournament: item.tournament,
      // Carry the scheduled item's tag into setup so the coach's pick survives
      // the GameSetup detour and lands on the game doc at kickoff.
      gameType: gameTypeOf(item),
      fromSchedule: true,
      isHome: typeof item.isHome === 'boolean' ? item.isHome : true,
      format: item.format,
      halfLengthMin: item.halfLengthMin,
      homeColor: item.homeColor,
      awayColor: item.awayColor,
      squad: Array.isArray(item.squadIds) && item.squadIds.length > 0 ? item.squadIds : undefined,
    });
    setView('gameSetup');
  };

  // The merged calendar. Built here rather than inside CalendarView because the
  // dugout tile's "N upcoming" needs the same merged count the calendar shows —
  // counting `schedule` alone would miss every practice and tournament block.
  //
  // `today` is a LOCAL date key, not `toISOString().slice(0,10)`: this team is in
  // Eastern time, so UTC would roll the calendar over to tomorrow mid-evening.
  // Also see the bandHist note above — this hook must stay above the early
  // returns, or the hook count changes between renders and React throws #310.
  const calendarToday = useMemo(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    // Recomputed on navigation so an app left open overnight picks up the new
    // day the next time the coach moves between screens.
  }, [view]);
  // Which day the calendar should open on when arrived at from a NEXT UP tap.
  const [calendarJumpTo, setCalendarJumpTo] = useState(null);

  const calendarModel = useMemo(
    () => buildCalendarModel({ teamsnapEvents, schedule, games, today: calendarToday, hidden: hiddenEventKeys }),
    [teamsnapEvents, schedule, games, calendarToday, hiddenEventKeys]
  );
  // NEXT UP moved out of the calendar and onto the dugout home, so the home
  // screen needs the same selection the calendar used to render itself. Must sit
  // AFTER calendarModel: a const in a useMemo body is read at render time, so
  // referencing it above its declaration is a TDZ throw, not a hoist.
  const homeAgenda = useMemo(() => calAgenda(calendarModel, calendarToday), [calendarModel, calendarToday]);
  // Everything from today forward, off days excluded — they are the absence of
  // an event, so counting them would inflate "3 upcoming" on a quiet week.
  const calendarUpcomingCount = useMemo(() => {
    let n = 0;
    for (const [dayKey, list] of calendarModel.days) {
      if (dayKey < calendarToday) continue;
      for (const e of list) if (e.kind !== 'off' && !e.cancelled) n++;
    }
    return n;
  }, [calendarModel, calendarToday]);

  if (!unlocked) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-950">
        <style>{FONT_STYLES}</style>
        <div className="font-sans-pro text-stone-400">Checking access…</div>
      </div>
    );
  }

  if (!loaded) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-950">
        <style>{FONT_STYLES}</style>
        <div className="font-sans-pro text-stone-400">Loading…</div>
      </div>
    );
  }

  const activeGame = games.find(g => g.id === activeGameId) || games.find(g => g.status === 'active');
  const viewingGame = games.find(g => g.id === viewingGameId);

  return (
    <div className="min-h-screen bg-stone-950 font-sans-pro text-stone-100 max-w-2xl mx-auto sm:border-x sm:border-stone-900">
      <style>{FONT_STYLES}</style>

      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-stone-900 text-white px-5 py-3 rounded-full shadow-xl text-sm font-semibold animate-[fadein_0.15s_ease-out]">
          {toast}
        </div>
      )}

      {penaltyFlash && <PenaltyFlash flash={penaltyFlash} onDismiss={() => setPenaltyFlash(null)} />}

      {view === 'home' && (
        <HomeView
          roster={roster}
          games={games}
          upcomingCount={calendarUpcomingCount}
          agenda={homeAgenda}
          activeGame={activeGame}
          onGoRoster={() => setView('roster')}
          onResumeGame={() => { setActiveGameId(activeGame.id); setView('activeGame'); }}
          // Tapping a NEXT UP row lands on that entry's day in the calendar,
          // where the full row and its actions live.
          onOpenAgendaEntry={(entry) => { setCalendarJumpTo(entry.date); setView('calendar'); }}
          onViewStats={() => setView('stats')}
          onViewWeights={() => setView('weights')}
          onViewCalendar={() => setView('calendar')}
          onViewHelp={() => setView('help')}
          onViewViewers={() => setView('viewers')}
          onViewAccess={() => setView('access')}
          onViewFilmRoom={() => setView('filmRoom')}
          onViewTraining={() => setView('training')}
        />
      )}

      {view === 'roster' && (
        <RosterView
          roster={roster}
          onBack={() => setView('home')}
          onAdd={() => { setEditingPlayer({}); setView('playerForm'); }}
          onEdit={(p) => { setEditingPlayer(p); setView('playerForm'); }}
          onDelete={(p) => askConfirm(`Remove ${p.name} #${p.number} from the roster?`, () => removePlayer(p.id), { danger: true, yesLabel: 'REMOVE' })}
          onBulkPhotos={async (files) => {
            const updates = {};
            let matched = 0, skipped = 0;
            for (const file of files) {
              // Extract the first run of digits from the filename, e.g. "#10.PNG" -> 10.
              const m = file.name.match(/(\d+)/);
              if (!m) { skipped++; continue; }
              const num = m[1];
              const player = roster.find(p => String(p.number) === num);
              if (!player) { skipped++; continue; }
              try {
                updates[player.id] = await resizePhoto(file, 256, 0.85);
                matched++;
              } catch (e) {
                skipped++;
              }
            }
            if (matched === 0) {
              showToast(`No photos matched (skipped ${skipped})`);
              return;
            }
            const next = roster.map(p => updates[p.id] ? { ...p, photo: updates[p.id] } : p);
            await persistRoster(next);
            showToast(`📷 Imported ${matched} photo${matched === 1 ? '' : 's'}${skipped ? ` (skipped ${skipped})` : ''}`);
          }}
        />
      )}

      {view === 'playerForm' && (
        <PlayerForm
          player={editingPlayer}
          onSave={upsertPlayer}
          onCancel={() => { setEditingPlayer(null); setView('roster'); }}
        />
      )}

      {view === 'gameSetup' && (
        <GameSetup
          rosterCount={roster.length}
          initial={pendingGameSetup}
          opponentSuggestions={opponentSuggestions}
          onCancel={() => { setPendingGameSetup(null); setView('home'); }}
          onStart={(opponent, isHome, tournament, halfLengthMin, homeColor, awayColor, autoRecord, format, gameType) => {
            // Preserve any pre-picked squad / fromSchedule flag carried in from
            // a scheduled-game start, so it survives the GameSetup detour.
            setPendingGameSetup({ ...(pendingGameSetup || {}), opponent, isHome, tournament, halfLengthMin, homeColor, awayColor, autoRecord, format, gameType });
            setView('squad');
          }}
          onGoRoster={() => setView('roster')}
        />
      )}

      {view === 'squad' && pendingGameSetup && (
        <SquadPickerView
          roster={roster}
          setup={pendingGameSetup}
          initialSquad={pendingGameSetup.squad}
          onBack={() => setView('gameSetup')}
          onNext={(squad) => {
            setPendingGameSetup({ ...pendingGameSetup, squad });
            setView('lineup');
          }}
        />
      )}

      {view === 'lineup' && pendingGameSetup && (
        <StartingLineupView
          roster={roster}
          games={games}
          squad={pendingGameSetup.squad}
          setup={pendingGameSetup}
          teamLiveInput={teamLiveInput}
          onSaveTeamLiveInput={persistTeamLiveInput}
          onBack={() => { setView('squad'); }}
          onStart={(lineup, gkPlayerId, liveInput, youtubeVideoId, kickoffPositions) => {
            // Kickoff is a user gesture — if auto-record is on, acquire the mic
            // HERE (iOS won't grant it once we're out of the tap) and stash the
            // promise for the recorder to adopt when it mounts.
            if (pendingGameSetup.autoRecord && navigator.mediaDevices?.getUserMedia) {
              try { pendingMicRef.current = navigator.mediaDevices.getUserMedia({ audio: true }); }
              catch (e) { pendingMicRef.current = null; }
            }
            startNewGame(pendingGameSetup.opponent, pendingGameSetup.isHome, pendingGameSetup.tournament, lineup, gkPlayerId, pendingGameSetup.squad, pendingGameSetup.halfLengthMin, pendingGameSetup.homeColor, pendingGameSetup.awayColor, liveInput, youtubeVideoId, kickoffPositions, pendingGameSetup.autoRecord, pendingGameSetup.format, pendingGameSetup.gameType);
            setPendingGameSetup(null);
          }}
        />
      )}

      {view === 'activeGame' && activeGame && (
        <ActiveGameView
          game={activeGame}
          roster={roster}
          pendingEvent={pendingEvent}
          onSelectEvent={(type) => {
            if (type === '__MINS__') {
              setPendingEvent({ type: 'MINS_VIEW' });
              return;
            }
            if (type === 'SUB') {
              setPendingEvent({ type: 'SUB', step: 'OFF' });
              return;
            }
            if (type === 'OPP_GOAL') {
              setPendingEvent({ type: 'OPP_GOAL_FAULT' });
              return;
            }
            // Penalty outcome intents (UI-only — never stored as their own type).
            // Converted pens become a real GOAL/OPP_GOAL carrying {penalty:true}.
            if (type === 'PEN_GOAL_US') {
              // Taker picker follows; the penalty flag rides on pendingEvent and
              // is threaded into logEvent at onSelectPlayer.
              setPendingEvent({ type: 'GOAL', penalty: true });
              return;
            }
            if (type === 'PEN_GOAL_OPP') {
              // Opponent converted — log straight away, skipping the GK-fault
              // prompt (a penalty isn't the keeper's fault).
              logEvent(activeGame.id, 'OPP_GOAL', null, { penalty: true });
              return;
            }
            const ev = EVENT_TYPES[type];
            if (!ev.requiresPlayer) {
              logEvent(activeGame.id, type, null);
            } else {
              setPendingEvent({ type });
            }
          }}
          onResolveOppGoal={(fault) => {
            // fault: 'gk' | 'unstoppable' | 'own' | null (neutral / unsure)
            // If own goal, we still need to attribute the player — flip to a
            // dedicated picker step instead of logging immediately.
            if (fault === 'own') {
              setPendingEvent({ type: 'OPP_GOAL_OWN_PICK' });
              return;
            }
            logEvent(activeGame.id, 'OPP_GOAL', null, { gkFault: fault });
          }}
          onConfirmGK={(playerId) => setGameGK(activeGame.id, playerId)}
          onSwapGK={() => setPendingEvent({ type: 'NEW_GK', defaultGK: currentGKAt(activeGame) })}
          onMovePosition={(playerId, x, y) => addPositionEvent(activeGame.id, playerId, x, y)}
          onResetFormation={(slots) => addPositionEventsBatch(activeGame.id, slots)}
          onSelectPlayer={(playerId) => {
            if (pendingEvent?.type === 'SUB' && pendingEvent.step === 'OFF') {
              // Re-validate against the LIVE lineup at click time, not the
              // picker's snapshot — prevents stale state from advancing.
              const liveOn = onFieldAt(activeGame);
              if (!liveOn.has(playerId)) {
                const p = roster.find(r => r.id === playerId);
                showToast(`⚠️ ${p?.name || 'That player'} is already off the field`);
                setPendingEvent(null);
                return;
              }
              setPendingEvent({ type: 'SUB', step: 'ON', offPlayerId: playerId });
              return;
            }
            if (pendingEvent?.type === 'SUB' && pendingEvent.step === 'ON') {
              logSubEvent(activeGame.id, pendingEvent.offPlayerId, playerId);
              return;
            }
            // Give & go: first pick = initiator, then ask for the wall partner.
            if (pendingEvent?.type === 'GIVE_GO' && !pendingEvent.initiatorId) {
              setPendingEvent({ type: 'GIVE_GO_PARTNER', initiatorId: playerId });
              return;
            }
            if (pendingEvent?.type === 'GIVE_GO_PARTNER') {
              // playerId === null means coach tapped SKIP / unknown — log
              // the give & go with no partner credit.
              const extras = playerId ? { partnerId: playerId } : {};
              logEvent(activeGame.id, 'GIVE_GO', pendingEvent.initiatorId, extras);
              return;
            }
            if (pendingEvent?.type === 'OPP_GOAL_OWN_PICK') {
              // Own goal: opp's score still goes up (handled by OPP_GOAL.delta),
              // but we tag the responsible player on the event and credit a
              // negative weight to their Defense pillar via computePerformanceScore.
              logEvent(activeGame.id, 'OPP_GOAL', null, { gkFault: 'own', ownGoalById: playerId });
              return;
            }
            const t = typeof pendingEvent === 'string' ? pendingEvent : pendingEvent?.type;
            // Live flow is ruthlessly single-tap. Zone / pressure / decision modifiers are
            // applied post-game from GameDetail's TAG button, so the coach never misses
            // the next play. A penalty-goal pending event carries {penalty:true},
            // threaded into the stored GOAL here.
            logEvent(activeGame.id, t, playerId, pendingEvent?.penalty ? { penalty: true } : {});
          }}
          onCancelEvent={() => setPendingEvent(null)}
          onUndo={() => undoLastEvent(activeGame.id)}
          onPauseHalfTime={() => {
            // Half time → stop + upload the 1st-half voice segment.
            if (activeGame.autoRecord) voiceRef.current?.stopSegment();
            pauseHalfTime(activeGame.id);
          }}
          onStartSecondHalf={() => {
            startSecondHalf(activeGame.id);
            // This is a button tap → getUserMedia stays in-gesture for iOS.
            if (activeGame.autoRecord) voiceRef.current?.startSegment();
          }}
          onResumeFirstHalf={() => {
            resumeFirstHalf(activeGame.id);
            if (activeGame.autoRecord) voiceRef.current?.startSegment();
          }}
          onPauseClock={() => pauseClock(activeGame.id)}
          onResumeClock={() => resumeClock(activeGame.id)}
          onEnd={() => askConfirm('End game and save final score?', () => {
            // Final whistle → stop + upload the current segment (the unmount
            // effect also finalizes if navigation beats this).
            if (activeGame.autoRecord) voiceRef.current?.stopSegment();
            endGame(activeGame.id);
          })}
          onBack={() => askConfirm('Leave this game? The clock keeps running — you can resume from Home.', () => setView('home'), { yesLabel: 'LEAVE' })}
          onBulkReplaceLineup={(ids, pairing) => bulkReplaceLineup(activeGame.id, ids, pairing)}
          bandHist={bandHist}
          voiceRef={voiceRef}
          pendingMicRef={pendingMicRef}
          tick={tick}
        />
      )}

      {view === 'gameDetail' && viewingGame && (
        <GameDetail
          game={viewingGame}
          roster={roster}
          weights={weights}
          opponentSuggestions={opponentSuggestions}
          onBack={() => setView('home')}
          onDelete={() => {
            // In production, require the coach password before deleting a game.
            // Beta/dev hosts skip this so we can create and trash dummy games freely.
            const host = (typeof window !== 'undefined' && window.location.hostname) || '';
            const isProd = !/beta|localhost|127\.0\.0\.1|deploy-preview/i.test(host);
            if (isProd) {
              if (!window.confirm('Are you sure? This is a production game.')) return;
            }
            askConfirm('Delete this game permanently? Wipes the videos from R2, the stats from Firestore, and the game from this device.', () => deleteGame(viewingGame.id), { danger: true, yesLabel: 'DELETE' });
          }}
          onDeleteVideos={() => {
            askConfirm('Delete only the full-game + highlights videos from R2? Stats and game metadata are kept; you can re-run the pipeline later to regenerate the videos.', () => deleteGameVideos(viewingGame.id), { danger: true, yesLabel: 'DELETE VIDEOS' });
          }}
          onDeleteEvent={(eid) => deleteEvent(viewingGame.id, eid)}
          onUpdateEvent={(eid, patch) => updateEvent(viewingGame.id, eid, patch)}
          onUpdateGame={(patch) => updateGame(viewingGame.id, g => ({ ...g, ...patch }))}
        />
      )}

      {view === 'stats' && (
        <StatsView roster={roster} games={games} weights={weights} onBack={() => setView('home')} />
      )}

      {view === 'weights' && (
        <WeightsView weights={weights} onSave={persistWeights} onBack={() => setView('home')} />
      )}

      {view === 'help' && (
        <HelpView onBack={() => setView('home')} />
      )}

      {view === 'calendar' && (
        <CalendarView
          model={calendarModel}
          canEdit={true}
          today={calendarToday}
          syncedAt={teamsnapSyncedAt}
          roster={roster}
          opponentSuggestions={opponentSuggestions}
          // Without this the calendar would offer START mid-match.
          activeGame={activeGame}
          onOpenGame={(id) => { setViewingGameId(id); setView('gameDetail'); }}
          onStartGame={startScheduledGame}
          onSaveEntry={saveCalendarEntry}
          onDeleteGame={(scheduleId) => persistSchedule(schedule.filter(s => s.id !== scheduleId))}
          onToggleCancel={(scheduleId) => persistSchedule(
            schedule.map(s => (s.id === scheduleId ? { ...s, cancelled: !s.cancelled } : s))
          )}
          onEditSquad={(item) => openScheduleSquadPicker(item, 'calendar')}
          // Hiding is a coach power only. The raw sources come too, because the
          // HIDDEN panel has to re-merge without the filter to describe what it
          // is offering to restore.
          hiddenKeys={hiddenEventKeys}
          onHide={hideEvent}
          onUnhide={unhideEvent}
          teamsnapEvents={teamsnapEvents}
          schedule={schedule}
          games={games}
          onManageOpponents={() => setShowOpponentManager(true)}
          onBack={() => setView('home')}
          askConfirm={askConfirm}
          showToast={showToast}
          // The squad picker unmounts the calendar, taking its sheet state with
          // it. SquadPickerView already records which item was being edited on
          // both exits, so hand it back and the coach resumes where they were.
          resumeEditId={resumeScheduleEditId}
          onConsumedResumeEditId={() => setResumeScheduleEditId(null)}
          // NEXT UP lives on the dugout home now; showing it here too would be
          // the same four rows twice on adjacent screens.
          showAgenda={false}
          jumpToDate={calendarJumpTo}
          onConsumedJumpTo={() => setCalendarJumpTo(null)}
        />
      )}

      {/* The season-wide rename modal, mounted beside the calendar because
          CalendarView only raises the intent — it does not own the modal. */}
      {view === 'calendar' && showOpponentManager && (
        <OpponentManagerModal
          opponentSuggestions={opponentSuggestions}
          games={games}
          schedule={schedule}
          onClose={() => setShowOpponentManager(false)}
          onRename={renameOpponent}
          askConfirm={askConfirm}
          showToast={showToast}
        />
      )}


      {view === 'scheduleSquad' && editingScheduleSquad && (
        <SquadPickerView
          roster={roster}
          setup={{ opponent: editingScheduleSquad.opponent, format: editingScheduleSquad.format }}
          initialSquad={editingScheduleSquad.squadIds}
          title="EDIT MATCH-DAY SQUAD"
          subtitle={<>Pre-pick the squad now. On match day, tapping <span className="font-bold">START</span> will jump straight to the lineup.</>}
          nextLabel="SAVE SQUAD"
          onBack={() => {
            setResumeScheduleEditId(editingScheduleSquad.itemId);
            setEditingScheduleSquad(null);
            setView(squadPickerReturnView);
          }}
          onNext={(squadIds) => {
            const next = schedule.map(s => s.id === editingScheduleSquad.itemId ? { ...s, squadIds } : s);
            persistSchedule(next);
            showToast?.(`✅ Saved ${squadIds.length}-player squad`);
            setResumeScheduleEditId(editingScheduleSquad.itemId);
            setEditingScheduleSquad(null);
            setView(squadPickerReturnView);
          }}
        />
      )}

      {view === 'viewers' && (
        <ViewersPanel games={games} onBack={() => setView('home')} />
      )}

      {view === 'access' && (
        <ManageAccessPanel roster={roster} onBack={() => setView('home')} />
      )}

      {view === 'filmRoom' && (
        <FilmRoomView
          games={games}
          roster={roster}
          onBack={() => setView('home')}
          onUpdateEvent={updateEvent}
          onDeleteEvent={deleteEvent}
          onConfirmBookmark={confirmBookmark}
          onConfirmVoiceDraft={confirmVoiceDraft}
          onDismissVoiceDraft={dismissVoiceDraft}
        />
      )}

      {view === 'training' && (
        <TrainingVideosView onBack={() => setView('home')} />
      )}

      {confirmDialog && (
        <ConfirmDialog
          message={confirmDialog.message}
          danger={confirmDialog.danger}
          yesLabel={confirmDialog.yesLabel}
          onCancel={() => setConfirmDialog(null)}
          onConfirm={() => {
            const { onYes } = confirmDialog;
            setConfirmDialog(null);
            onYes();
          }}
        />
      )}
    </div>
  );
}

/* ---------- CONFIRM DIALOG ---------- */
function ConfirmDialog({ message, danger, yesLabel = 'YES', onCancel, onConfirm }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm animate-[fadein_0.15s_ease-out]"
      onClick={onCancel}
    >
      <div
        className="bg-stone-900 rounded-t-3xl sm:rounded-2xl w-full sm:max-w-sm p-5 pb-8 sm:pb-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-stone-100 text-center font-semibold text-base mb-5 pt-2">{message}</div>
        <div className="grid grid-cols-2 gap-2.5">
          <button
            onClick={onCancel}
            className="py-4 rounded-xl bg-stone-800 text-stone-300 font-display text-lg active:scale-[0.98] transition"
          >
            CANCEL
          </button>
          <button
            onClick={onConfirm}
            className={`py-4 rounded-xl font-display text-lg active:scale-[0.98] transition ${
              danger ? 'bg-red-500 text-white' : 'bg-lime-500 text-stone-950'
            }`}
          >
            {yesLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- HOME ---------- */
function HomeView({ roster, games, upcomingCount = 0, agenda = [], activeGame, onGoRoster, onResumeGame, onOpenAgendaEntry, onViewStats, onViewWeights, onViewCalendar, onViewHelp, onViewViewers, onViewFilmRoom, onViewTraining, onViewAccess }) {
  // Pending family requests → badge on the ACCESS tile. Errors stay silent
  // (rules not deployed yet / offline) — the tile just shows no count.
  const [pendingAccess, setPendingAccess] = useState(0);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const unsub = window.fbDb.collection('accessRequests').onSnapshot(
      (snap) => setPendingAccess(snap.size),
      () => setPendingAccess(0)
    );
    return unsub;
  }, []);
  const finishedGames = games.filter(g => g.status === 'finished')
    // Newest first by DATE then time-of-day (startedAt) — so two games on the
    // same festival day order correctly.
    .sort((a, b) => (b.date || '').localeCompare(a.date || '')
      || (b.endedAt || b.startedAt || 0) - (a.endedAt || a.startedAt || 0));
  const wins = finishedGames.filter(g => g.ourScore > g.oppScore).length;
  const losses = finishedGames.filter(g => g.ourScore < g.oppScore).length;
  const draws = finishedGames.filter(g => g.ourScore === g.oppScore).length;
  const [showWelcome, setShowWelcome] = useState(() => {
    try { return localStorage.getItem('stompers_welcome_dismissed') !== 'true'; } catch(e) { return true; }
  });
  const dismissWelcome = () => {
    try { localStorage.setItem('stompers_welcome_dismissed', 'true'); } catch(e) {}
    setShowWelcome(false);
  };

  // ---- PWA install state ----
  // Treat the app as "installed" if launched in standalone display mode OR via
  // iOS home-screen launcher (legacy navigator.standalone).
  const detectStandalone = () => {
    try {
      if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
      if (window.navigator && window.navigator.standalone) return true;
    } catch (e) {}
    return false;
  };
  const [isStandalone, setIsStandalone] = useState(detectStandalone);
  const [canPromptInstall, setCanPromptInstall] = useState(() => !!(typeof window !== 'undefined' && window.fbDeferredInstall));
  const [showInstallModal, setShowInstallModal] = useState(false);
  useEffect(() => {
    const onAvail = () => setCanPromptInstall(true);
    const onInstalled = () => { setCanPromptInstall(false); setIsStandalone(true); setShowInstallModal(false); };
    const mq = window.matchMedia ? window.matchMedia('(display-mode: standalone)') : null;
    const onMode = () => setIsStandalone(detectStandalone());
    window.addEventListener('stompers:installavailable', onAvail);
    window.addEventListener('stompers:installed', onInstalled);
    if (mq && mq.addEventListener) mq.addEventListener('change', onMode);
    return () => {
      window.removeEventListener('stompers:installavailable', onAvail);
      window.removeEventListener('stompers:installed', onInstalled);
      if (mq && mq.removeEventListener) mq.removeEventListener('change', onMode);
    };
  }, []);

  return (
    <div className="pb-24">
      <div className="stripes-bg text-white px-5 pt-16 pb-10 relative">
        <div className="absolute top-[calc(env(safe-area-inset-top,0px)+0.75rem)] right-4 flex items-center gap-2">
          {window.fbUserInfo && (
            <button
              onClick={() => { if (window.fbAuth) window.fbAuth.signOut(); }}
              aria-label="Sign out"
              className="h-9 px-2 rounded-full bg-white/10 hover:bg-white/20 flex items-center gap-1.5 border border-white/15 active:scale-95"
            >
              {window.fbUserInfo.photo && <img src={window.fbUserInfo.photo} className="w-5 h-5 rounded-full" referrerPolicy="no-referrer" />}
              <span className="text-[10px] text-white/70 font-bold">Sign Out</span>
            </button>
          )}
          {!isStandalone && (
            <button
              onClick={() => setShowInstallModal(true)}
              aria-label="Install app on this device"
              className="h-9 px-3 rounded-full bg-lime-400/90 hover:bg-lime-400 text-stone-100 font-display text-xs flex items-center gap-1 border border-lime-300 active:scale-95 shadow"
            >
              <span>📲</span><span>INSTALL</span>
            </button>
          )}
          <a
            href="./"
            onClick={(e) => { if (window.__navigate) { e.preventDefault(); window.__navigate({ kind: 'home' }); } }}
            aria-label="Exit coach — back to public scoreboard"
            className="h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95"
          >
            <span>📡</span><span>PUBLIC</span>
          </a>
          <button
            onClick={onViewHelp}
            aria-label="Help & onboarding"
            className="w-9 h-9 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-lg flex items-center justify-center border border-white/20 active:scale-95"
          >
            ?
          </button>
        </div>
        <div className="flex items-center gap-4 mt-12">
          <img
            src="./stompers_logo.png"
            alt=""
            className="w-24 h-24 shrink-0 drop-shadow"
            onError={(e) => { e.currentTarget.style.display = 'none'; }}
          />
          <div className="flex-1 min-w-0">
            <h1 className="font-display text-4xl leading-none">LASALLE STOMPERS</h1>
            <div className="font-display text-2xl text-lime-400 leading-tight">2016 BOYS SQUAD</div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 mt-6">
          <Stat label="WINS" value={wins} accent="text-lime-400" />
          <Stat label="DRAWS" value={draws} accent="text-stone-300" />
          <Stat label="LOSSES" value={losses} accent="text-red-400" />
        </div>
      </div>

      {activeGame && (
        <button
          onClick={onResumeGame}
          className="mx-4 -mt-4 w-[calc(100%-2rem)] bg-amber-100 border-2 border-amber-400 text-amber-900 rounded-2xl p-4 flex items-center justify-between shadow-md active:scale-[0.99] transition"
        >
          <div className="flex items-center gap-3 text-left">
            <div className="w-10 h-10 rounded-full bg-amber-400 flex items-center justify-center">
              <PlayCircle className="w-6 h-6 text-amber-900" />
            </div>
            <div>
              <div className="font-bold text-sm">GAME IN PROGRESS</div>
              <div className="text-xs">{activeGame.tournament || gameTypeOf(activeGame).toUpperCase()} · vs {activeGame.opponent} · {activeGame.ourScore}–{activeGame.oppScore}</div>
            </div>
          </div>
          <ChevronRight className="w-5 h-5" />
        </button>
      )}

      {showWelcome && !activeGame && (
        <div className="mx-4 -mt-4 bg-stone-900 border-2 border-stone-900 rounded-2xl p-4 shadow-md relative">
          <button
            onClick={dismissWelcome}
            aria-label="Dismiss welcome"
            className="absolute top-2 right-2 w-7 h-7 rounded-full bg-stone-800 hover:bg-stone-700 flex items-center justify-center"
          >
            <X className="w-4 h-4" />
          </button>
          <div className="text-2xl mb-1">👋</div>
          <div className="font-display text-xl leading-tight mb-1">NEW HERE?</div>
          <div className="text-sm text-stone-300 mb-3">
            Track your match-day roster, log live events, and get per-player performance scores. Take the quick tour first.
          </div>
          <button
            onClick={onViewHelp}
            className="w-full bg-stone-900 text-lime-400 font-display text-lg py-3 rounded-xl active:scale-[0.98] transition"
          >
            OPEN HELP
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 px-4 pt-3">
        <TileButton onClick={onGoRoster} icon={<Users className="w-6 h-6" />} label="ROSTER" sub={`${roster.length} players`} />
        <TileButton onClick={onViewStats} icon={<BarChart3 className="w-6 h-6" />} label="STATS" sub="Players · games · team" />
        {/* The count comes from the MERGED model, not `schedule` — practices,
            tournament blocks and tryouts are upcoming events too, and the tile
            would under-report the week without them. */}
        <TileButton onClick={onViewCalendar} icon={<Calendar className="w-6 h-6" />} label="CALENDAR" sub={`${upcomingCount} upcoming`} />
        <TileButton onClick={onViewFilmRoom} icon={<span className="text-2xl leading-none">🎥</span>} label="FILM ROOM" sub={`${finishedGames.length} game${finishedGames.length === 1 ? '' : 's'} · video · review`} />
        <TileButton onClick={onViewWeights} icon={<span className="text-2xl leading-none">⚙</span>} label="SCORING" sub="Tune weights" />
        {/* Owner-only: usage analytics across parents/coaches/players. Other
            coaches don't see this tile (local dev with no Firebase shows it). */}
        {(isOwner() || (typeof window !== 'undefined' && !window.fbDb)) && (
          <TileButton onClick={onViewViewers} icon={<span className="text-2xl leading-none">👁</span>} label="VIEWERS" sub="Usage analytics" />
        )}
        <TileButton
          onClick={onViewAccess}
          icon={<span className="text-2xl leading-none">🔑</span>}
          label="ACCESS"
          sub={pendingAccess > 0 ? `${pendingAccess} request${pendingAccess === 1 ? '' : 's'} waiting` : 'Family approvals'}
        />
        <TileButton onClick={onViewTraining} icon={<span className="text-2xl leading-none">🎓</span>} label="TRAINING" sub="Skill videos" />
      </div>

      {/* Next up. Lives here rather than inside the calendar so the first thing
          on the dugout home after the tiles is what is actually coming — the
          calendar itself suppresses its own copy via showAgenda={false}. Rows
          are deliberately terse: this is a glance, and tapping goes to the
          calendar where the full row and its actions live. */}
      {agenda.length > 0 && (
        <div className="px-4 pt-6">
          <h2 className="font-display text-xl mb-3">NEXT UP</h2>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
            {agenda.map((entry) => (
              <button
                key={entry.key}
                onClick={() => onOpenAgendaEntry?.(entry)}
                className="w-full flex items-center gap-3 p-3 text-left active:bg-stone-950"
              >
                <span
                  className="w-1.5 h-8 rounded-sm shrink-0"
                  style={{
                    background: calBarColor(entry) || 'transparent',
                    border: calBarColor(entry) ? 'none' : '1px dashed #57534e',
                  }}
                />
                <div className="w-11 shrink-0 text-center">
                  <div className="text-[10px] font-bold text-stone-500 tracking-widest leading-none">
                    {new Date(entry.date + 'T12:00').toLocaleDateString('en', { month: 'short' }).toUpperCase()}
                  </div>
                  <div className="font-display text-lg leading-none tabular-nums">
                    {new Date(entry.date + 'T12:00').getDate()}
                  </div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                    {entry.kind === 'off' ? 'No practice' : entryLabel(entry)}
                  </div>
                  <div className="text-xs text-stone-400 truncate">
                    {[entry.time ? formatTime12(entry.time) : (entry.allDay ? 'All day' : ''), entry.venue || entry.field]
                      .filter(Boolean).join(' · ')}
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 text-stone-400 shrink-0" />
              </button>
            ))}
          </div>
        </div>
      )}

      {showInstallModal && (
        <InstallModal
          canPrompt={canPromptInstall}
          onTriggerPrompt={async () => {
            const dp = (typeof window !== 'undefined') ? window.fbDeferredInstall : null;
            if (!dp) return;
            try {
              dp.prompt();
              await dp.userChoice;
            } catch (e) {}
            window.fbDeferredInstall = null;
            setCanPromptInstall(false);
            setShowInstallModal(false);
          }}
          onClose={() => setShowInstallModal(false)}
        />
      )}
    </div>
  );
}

/* ---------- INSTALL APP MODAL ---------- */
function InstallModal({ canPrompt, onTriggerPrompt, onClose }) {
  // Detect platform once per open.
  const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
  const isAndroid = /Android/.test(ua);
  const isSafari = /^((?!chrome|android|crios|fxios).)*safari/i.test(ua);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm animate-[fadein_0.15s_ease-out]"
      onClick={onClose}
    >
      <div
        className="bg-stone-900 rounded-t-3xl sm:rounded-2xl w-full sm:max-w-md p-5 pb-8 sm:pb-5 shadow-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 mb-3">
          <div className="text-2xl">📲</div>
          <div className="font-display text-2xl">INSTALL ON YOUR PHONE</div>
        </div>
        <p className="text-sm text-stone-300 mb-4">
          Adds a LaSalle Stompers icon to your home screen. Opens fullscreen — no browser bar — and works offline.
        </p>

        {canPrompt && (
          <button
            onClick={onTriggerPrompt}
            className="w-full bg-lime-500 text-stone-100 font-display text-xl py-4 rounded-xl border-2 border-lime-600 active:scale-[0.98] transition mb-4"
          >
            ⬇ INSTALL NOW
          </button>
        )}

        {isIOS && (
          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mb-3">
            <div className="font-display text-sm text-stone-200 mb-2">📱 iPhone / iPad (Safari)</div>
            <ol className="text-sm text-stone-200 space-y-1.5 list-decimal pl-5">
              <li>Tap the <strong>Share</strong> button <span className="inline-block px-1.5 py-0.5 bg-stone-900 border border-stone-700 rounded text-xs">⬆</span> at the bottom of Safari.</li>
              <li>Scroll down and tap <strong>Add to Home Screen</strong>.</li>
              <li>Tap <strong>Add</strong> in the top-right.</li>
            </ol>
            {!isSafari && (
              <p className="text-xs text-amber-700 mt-2 italic">Heads up: on iPhone, only <strong>Safari</strong> can install web apps. Open this page in Safari first.</p>
            )}
          </div>
        )}

        {isAndroid && !canPrompt && (
          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mb-3">
            <div className="font-display text-sm text-stone-200 mb-2">🤖 Android (Chrome)</div>
            <ol className="text-sm text-stone-200 space-y-1.5 list-decimal pl-5">
              <li>Tap the <strong>⋮</strong> menu (top-right of Chrome).</li>
              <li>Tap <strong>Install app</strong> or <strong>Add to Home screen</strong>.</li>
              <li>Confirm <strong>Install</strong>.</li>
            </ol>
            <p className="text-xs text-stone-400 mt-2 italic">If you don't see the option, reload the page and try again.</p>
          </div>
        )}

        {!isIOS && !isAndroid && !canPrompt && (
          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mb-3 text-sm text-stone-200">
            <p>Look for an <strong>install</strong> or <strong>add to home screen</strong> option in your browser's menu (usually under <strong>⋮</strong> or the address bar).</p>
          </div>
        )}

        <button
          onClick={onClose}
          className="w-full py-3 mt-2 rounded-xl bg-stone-800 text-stone-300 font-display text-base active:scale-[0.98] transition"
        >
          CLOSE
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div className="text-center">
      <div className={`font-display text-4xl ${accent}`}>{value}</div>
      <div className="text-[10px] text-white/60 font-bold tracking-widest">{label}</div>
    </div>
  );
}

function TileButton({ onClick, icon, label, sub }) {
  return (
    <button
      onClick={onClick}
      className="bg-stone-900 border border-stone-800 rounded-2xl p-4 text-left active:scale-[0.98] transition shadow-sm"
    >
      <div className="w-10 h-10 rounded-xl bg-stone-900 text-lime-400 flex items-center justify-center mb-2">
        {icon}
      </div>
      <div className="font-display text-lg leading-none">{label}</div>
      <div className="text-xs text-stone-400 mt-1">{sub}</div>
    </button>
  );
}

/* ---------- ROSTER ---------- */
function RosterView({ roster, onBack, onAdd, onEdit, onDelete, onBulkPhotos }) {
  const sorted = [...roster].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));
  const bulkInputRef = React.useRef(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const handleBulkFiles = async (files) => {
    if (!files || files.length === 0) return;
    setBulkBusy(true);
    try {
      await onBulkPhotos(Array.from(files));
    } finally {
      setBulkBusy(false);
      if (bulkInputRef.current) bulkInputRef.current.value = '';
    }
  };

  return (
    <div className="pb-24">
      <Header title="ROSTER" onBack={onBack} right={
        <button onClick={onAdd} className="bg-lime-500 text-stone-100 w-10 h-10 rounded-full flex items-center justify-center font-bold shadow active:scale-95 transition">
          <Plus className="w-5 h-5" />
        </button>
      } />

      <input
        ref={bulkInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={e => handleBulkFiles(e.target.files)}
      />

      {sorted.length > 0 && (
        <div className="px-4 pt-3">
          <button
            onClick={() => bulkInputRef.current?.click()}
            disabled={bulkBusy}
            className="w-full bg-amber-100 text-amber-900 border-2 border-amber-300 font-bold text-sm py-3 rounded-xl active:scale-[0.99] transition disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <span>📷</span>
            <span>{bulkBusy ? 'Importing…' : 'BULK IMPORT PHOTOS'}</span>
          </button>
          <div className="text-[11px] text-stone-400 mt-1.5 text-center px-2">
            Pick multiple files — names like <span className="font-mono">#10.PNG</span> match by jersey number.
          </div>
        </div>
      )}

      <div className="px-4 pt-4">
        {sorted.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-8 text-center">
            <Users className="w-10 h-10 text-stone-300 mx-auto mb-3" />
            <div className="font-display text-xl mb-1">NO PLAYERS YET</div>
            <div className="text-sm text-stone-400 mb-4">Add your squad to start logging games.</div>
            <button onClick={onAdd} className="bg-stone-900 text-white px-5 py-3 rounded-full font-bold text-sm">
              + Add first player
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {sorted.map(p => (
              <div key={p.id} className="bg-stone-900 border border-stone-800 rounded-xl p-3 flex items-center gap-3">
                <PlayerAvatar player={p} />
                <div className="flex-1 min-w-0">
                  <div className="font-bold truncate">{p.name}</div>
                  {p.position && <div className="text-xs text-stone-400 uppercase tracking-wide">{p.position}</div>}
                </div>
                <button onClick={() => onEdit(p)} className="w-9 h-9 rounded-full bg-stone-900 flex items-center justify-center active:scale-95">
                  <Edit3 className="w-4 h-4 text-stone-200" />
                </button>
                <button
                  onClick={() => onDelete(p)}
                  className="w-9 h-9 rounded-full bg-red-500/10 flex items-center justify-center active:scale-95"
                >
                  <Trash2 className="w-4 h-4 text-red-600" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- PLAYER FORM ---------- */
function PlayerForm({ player, onSave, onCancel }) {
  const [name, setName] = useState(player?.name || '');
  const [number, setNumber] = useState(player?.number || '');
  const [position, setPosition] = useState(player?.position || '');
  const [photo, setPhoto] = useState(player?.photo || '');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = React.useRef(null);

  const handlePhotoFile = async (file) => {
    if (!file) return;
    setUploading(true);
    try {
      const dataUrl = await resizePhoto(file, 256, 0.85);
      setPhoto(dataUrl);
    } catch (err) {
      alert('Could not read that image. Try a different one.');
    } finally {
      setUploading(false);
    }
  };

  const valid = name.trim().length > 0;
  const positions = ['GK', 'DEF', 'MID', 'FWD'];

  return (
    <div className="pb-24">
      <Header title={player?.id ? 'EDIT PLAYER' : 'ADD PLAYER'} onBack={onCancel} />

      <div className="px-4 pt-6 space-y-5">
        <Field label="PHOTO">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={e => handlePhotoFile(e.target.files?.[0])}
          />
          <div className="flex items-center gap-4">
            <div className="w-24 h-24 rounded-2xl overflow-hidden bg-stone-900 border-2 border-stone-800 flex items-center justify-center shrink-0">
              {photo ? (
                <img src={photo} alt="" className="w-full h-full object-cover" />
              ) : (
                <div className="text-stone-300 text-3xl">👤</div>
              )}
            </div>
            <div className="flex-1 flex flex-col gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="bg-stone-900 text-white font-bold text-sm px-4 py-2.5 rounded-xl active:scale-[0.98] transition disabled:opacity-50"
              >
                {uploading ? 'Loading…' : (photo ? '📷 Change photo' : '📷 Add photo')}
              </button>
              {photo && (
                <button
                  type="button"
                  onClick={() => setPhoto('')}
                  className="bg-stone-900 text-stone-300 font-bold text-xs px-4 py-2 rounded-xl active:scale-[0.98] transition"
                >
                  Remove
                </button>
              )}
            </div>
          </div>
        </Field>

        <Field label="NAME">
          <input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="First name"
            className="w-full bg-stone-900 border-2 border-stone-800 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
        </Field>

        <Field label="JERSEY NUMBER">
          <input
            type="number"
            inputMode="numeric"
            value={number}
            onChange={e => setNumber(e.target.value)}
            placeholder="0"
            className="w-full bg-stone-900 border-2 border-stone-800 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-2xl font-display tabular-nums"
          />
        </Field>

        <Field label="POSITION">
          <div className="grid grid-cols-4 gap-2">
            {positions.map(pos => (
              <button
                key={pos}
                type="button"
                onClick={() => setPosition(position === pos ? '' : pos)}
                className={`py-3 rounded-xl font-display text-lg border-2 transition ${
                  position === pos
                    ? 'bg-stone-900 text-lime-400 border-stone-900'
                    : 'bg-stone-900 text-stone-200 border-stone-800'
                }`}
              >
                {pos}
              </button>
            ))}
          </div>
        </Field>

        <button
          onClick={() => valid && onSave({ ...player, name: name.trim(), number, position, photo: photo || null })}
          disabled={!valid}
          className="w-full bg-lime-500 disabled:bg-stone-700 disabled:text-stone-400 text-stone-100 font-display text-2xl py-4 rounded-2xl shadow-lg shadow-lime-500/20 border-2 border-lime-600 disabled:border-stone-700 active:scale-[0.99] transition mt-4"
        >
          SAVE PLAYER
        </button>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-bold tracking-widest text-stone-400 mb-2">{label}</label>
      {children}
    </div>
  );
}

// Center-crop + resize an image File to a square JPEG data URL.
// Keeps photos small enough to live inside the team Firestore doc (<30KB each).
function resizePhoto(file, size = 256, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('read failed'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('decode failed'));
      img.onload = () => {
        const minSide = Math.min(img.width, img.height);
        const sx = (img.width - minSide) / 2;
        const sy = (img.height - minSide) / 2;
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, sx, sy, minSide, minSide, 0, 0, size, size);
        resolve(canvas.toDataURL('image/jpeg', quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

// Player avatar: photo if set, otherwise a colored number badge.
// `sizeClass` controls dimensions (e.g. 'w-12 h-12'); `numberClasses` controls
// the fallback badge bg/text colors so callers can encode GK / bench states.
function PlayerAvatar({ player, sizeClass = 'w-12 h-12', numberClasses = 'bg-stone-900 text-lime-400', textSize = 'text-2xl', rounded = 'rounded-lg' }) {
  if (player?.photo) {
    return (
      <div className={`${sizeClass} ${rounded} overflow-hidden bg-stone-900 shrink-0`}>
        <img src={player.photo} alt="" className="w-full h-full object-cover" />
      </div>
    );
  }
  return (
    <div className={`${sizeClass} ${rounded} ${numberClasses} flex items-center justify-center font-display ${textSize} tabular-nums shrink-0`}>
      {player?.number || '—'}
    </div>
  );
}

/* ---------- GAME SETUP ---------- */
function GameSetup({ rosterCount, onCancel, onStart, onGoRoster, initial, opponentSuggestions = [] }) {
  const [opponent, setOpponent] = useState(initial?.opponent || '');
  const [tournament, setTournament] = useState(initial?.tournament || '');
  // The fixture's scoring tag. Seeded through gameTypeOf so a game started from
  // a tagged schedule item keeps that tag, and an untagged one still derives it
  // from the old free-text box rather than defaulting to a full-weight league.
  const [gameType, setGameType] = useState(gameTypeOf(initial || {}));
  const [isHome, setIsHome] = useState(typeof initial?.isHome === 'boolean' ? initial.isHome : true);
  const [format, setFormat] = useState(initial?.format && FORMATS[initial.format] ? initial.format : DEFAULT_FORMAT);
  const [halfLengthMin, setHalfLengthMin] = useState(
    initial?.halfLengthMin ?? formatOf({ format: initial?.format }).halfMin
  );
  // Flipping format SUGGESTS its usual half length (9v9 is 2×30, 7v7 2×25), but
  // only until the coach sets the clock themselves — an explicit choice must
  // never be silently overwritten by a later format tap.
  const [halfLenTouched, setHalfLenTouched] = useState(
    typeof initial?.halfLengthMin === 'number'
  );
  const pickFormat = (next) => {
    setFormat(next);
    if (!halfLenTouched) setHalfLengthMin(FORMATS[next].halfMin);
  };
  const setHalfLenManually = (next) => {
    setHalfLenTouched(true);
    setHalfLengthMin(next);
  };
  const [homeColor, setHomeColor] = useState(initial?.homeColor || '#0a0a0a');
  const [awayColor, setAwayColor] = useState(initial?.awayColor || '#dc2626');
  // Auto-record narration with the game clock. Sticky across games via
  // localStorage; defaults ON the first time.
  const [autoRecord, setAutoRecord] = useState(() => {
    try { const v = localStorage.getItem('autoRecordVoice'); return v === null ? true : v === '1'; }
    catch (e) { return true; }
  });
  const isLightColor = (hex) => {
    try {
      const h = (hex || '').replace('#', '');
      const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      return (0.299 * r + 0.587 * g + 0.114 * b) > 160;
    } catch { return false; }
  };
  const STOMPERS_PRESETS = [
    { label: 'Black', color: '#0a0a0a' },
    { label: 'Green', color: '#16a34a' },
  ];

  if (rosterCount === 0) {
    return (
      <div>
        <Header title="NEW GAME" onBack={onCancel} />
        <div className="p-6 text-center">
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-8">
            <Users className="w-10 h-10 text-stone-300 mx-auto mb-3" />
            <div className="font-display text-xl mb-2">ADD PLAYERS FIRST</div>
            <div className="text-sm text-stone-400 mb-4">You need a roster before starting a game.</div>
            <button onClick={onGoRoster} className="bg-stone-900 text-white px-5 py-3 rounded-full font-bold text-sm">
              Go to Roster
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="pb-24">
      <Header title="NEW GAME" onBack={onCancel} />

      <div className="px-4 pt-6 space-y-5">
        <Field label="TAG">
          <div className="grid grid-cols-2 gap-2">
            {GAME_TYPES.map(gt => (
              <button
                key={gt.key}
                type="button"
                onClick={() => setGameType(gt.key)}
                className={`py-2.5 rounded-xl font-bold text-xs border-2 active:scale-95 transition ${
                  gameType === gt.key
                    ? 'bg-lime-500 text-stone-950 border-lime-400'
                    : 'bg-stone-900 text-stone-300 border-stone-800'
                }`}
              >{gt.label}</button>
            ))}
          </div>
        </Field>

        <Field label="COMPETITION NAME">
          <input
            type="text"
            value={tournament}
            onChange={e => setTournament(e.target.value)}
            placeholder="e.g. Canton Cup (optional)"
            className="w-full bg-stone-900 border-2 border-stone-800 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
        </Field>

        <Field label="OPPONENT">
          <input
            type="text"
            value={opponent}
            onChange={e => setOpponent(e.target.value)}
            placeholder="e.g., Lions FC"
            list="opponent-suggestions"
            autoComplete="off"
            className="w-full bg-stone-900 border-2 border-stone-800 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
          {opponentSuggestions.length > 0 && (
            <datalist id="opponent-suggestions">
              {opponentSuggestions.map((n) => <option key={n} value={n} />)}
            </datalist>
          )}
        </Field>

        <Field label="SIDE">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setIsHome(true)}
              className={`flex-1 py-3 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${isHome ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
            >🏠 HOME</button>
            <button
              type="button"
              onClick={() => setIsHome(false)}
              className={`flex-1 py-3 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${!isHome ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
            >✈️ AWAY</button>
          </div>
        </Field>

        <Field label="FORMAT">
          <div className="flex gap-2">
            {Object.values(FORMATS).map(f => (
              <button
                key={f.key}
                type="button"
                onClick={() => pickFormat(f.key)}
                className={`flex-1 py-3 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${format === f.key ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
              >{f.key.toUpperCase()}</button>
            ))}
          </div>
          <div className="text-[11px] text-stone-500 mt-1.5">
            {formatOf({ format }).onField} a side · squad soft limit {formatOf({ format }).squadCap}
          </div>
        </Field>

        <Field label="HALF LENGTH (MIN)">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setHalfLenManually(Math.max(1, halfLengthMin - 1))}
              className="w-14 h-14 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-2xl font-bold active:scale-95 transition"
              aria-label="Decrease half length"
            >−</button>
            <div className="flex-1 py-3 rounded-xl bg-stone-900 border-2 border-stone-800 text-center">
              <div className="font-display text-3xl text-stone-100 tabular-nums leading-none">{halfLengthMin}</div>
              <div className="text-[10px] uppercase tracking-widest text-stone-500 mt-1">minutes</div>
            </div>
            <button
              type="button"
              onClick={() => setHalfLenManually(Math.min(99, halfLengthMin + 1))}
              className="w-14 h-14 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-2xl font-bold active:scale-95 transition"
              aria-label="Increase half length"
            >+</button>
          </div>
          {!halfLenTouched && (
            <div className="text-[11px] text-stone-500 mt-1.5">Suggested for {format.toUpperCase()}</div>
          )}
        </Field>

        <Field label="LASALLE STOMPERS JERSEY">
          <div className="flex gap-2 items-center">
            {STOMPERS_PRESETS.map(p => (
              <button
                key={p.color}
                type="button"
                onClick={() => setHomeColor(p.color)}
                className={`flex-1 py-3 rounded-xl font-bold text-xs border-2 active:scale-95 transition ${homeColor === p.color ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                style={{ background: p.color, color: '#fff' }}
              >
                {p.label.toUpperCase()}
              </button>
            ))}
            <label
              className={`flex-1 relative py-3 rounded-xl font-bold text-xs border-2 active:scale-95 transition cursor-pointer flex items-center justify-center overflow-hidden ${STOMPERS_PRESETS.every(p => p.color !== homeColor) ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
              style={{ background: homeColor, color: isLightColor(homeColor) ? '#0a0a0a' : '#fff' }}
              title="Pick a custom color"
            >
              🎨 CUSTOM
              <input
                type="color"
                value={homeColor}
                onChange={e => setHomeColor(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
            </label>
          </div>
        </Field>

        <Field label="OPPONENT JERSEY">
          <div className="flex gap-2 items-center">
            {[
              { label: 'Red', color: '#dc2626' },
              { label: 'Blue', color: '#2563eb' },
              { label: 'White', color: '#f5f5f4' },
            ].map(p => (
              <button
                key={p.color}
                type="button"
                onClick={() => setAwayColor(p.color)}
                className={`flex-1 py-3 rounded-xl font-bold text-xs border-2 active:scale-95 transition ${awayColor === p.color ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                style={{ background: p.color, color: p.color === '#f5f5f4' ? '#0a0a0a' : '#fff' }}
              >
                {p.label.toUpperCase()}
              </button>
            ))}
            <label
              className={`flex-1 relative py-3 rounded-xl font-bold text-xs border-2 active:scale-95 transition cursor-pointer flex items-center justify-center overflow-hidden ${!['#dc2626', '#2563eb', '#f5f5f4'].includes(awayColor) ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
              style={{ background: awayColor, color: isLightColor(awayColor) ? '#0a0a0a' : '#fff' }}
              title="Pick a custom color"
            >
              🎨 CUSTOM
              <input
                type="color"
                value={awayColor}
                onChange={e => setAwayColor(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
            </label>
          </div>
        </Field>

        {/* Auto-record narration: starts at kickoff, pauses at half time,
            resumes at the 2nd half, saves at the final whistle. */}
        <button
          type="button"
          onClick={() => setAutoRecord(v => {
            const nv = !v;
            try { localStorage.setItem('autoRecordVoice', nv ? '1' : '0'); } catch (e) {}
            return nv;
          })}
          className={`w-full mt-1 flex items-center justify-between gap-3 rounded-2xl border-2 px-4 py-3 text-left active:scale-[0.99] transition ${autoRecord ? 'border-lime-600 bg-lime-950/30' : 'border-stone-700 bg-stone-900'}`}
        >
          <div className="min-w-0">
            <div className="font-display text-sm tracking-wide text-stone-100 flex items-center gap-1.5">
              <span>🎙</span><span>AUTO-RECORD NARRATION</span>
            </div>
            <div className="text-[11px] text-stone-400 leading-tight mt-0.5">
              Starts at kickoff · pauses at half time · saves at full time
            </div>
          </div>
          <span className={`shrink-0 w-11 h-6 rounded-full border-2 relative transition ${autoRecord ? 'bg-lime-500 border-lime-400' : 'bg-stone-700 border-stone-600'}`}>
            <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${autoRecord ? 'left-[1.4rem]' : 'left-0.5'}`} />
          </span>
        </button>

        <button
          onClick={() => onStart(opponent.trim() || 'Opponent', isHome, tournament.trim(), halfLengthMin, homeColor, awayColor, autoRecord, format, gameType)}
          className="w-full bg-lime-500 text-stone-100 font-display text-3xl py-5 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.99] transition mt-4 flex items-center justify-center gap-3"
        >
          <Flag className="w-7 h-7" />
          KICK OFF
        </button>
      </div>
    </div>
  );
}

/* ---------- MATCH-DAY SQUAD ---------- */
function SquadPickerView({ roster, setup, initialSquad, onBack, onNext, title, subtitle, nextLabel }) {
  const sorted = [...roster].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));
  // Squad size the format allows (12 for 7v7, 16 for 9v9). Still only a soft
  // warning — a tournament may say otherwise and the coach wins.
  const fmt = formatOf(setup);
  const SOFT_CAP = fmt.squadCap;
  const [selected, setSelected] = useState(() =>
    new Set(initialSquad && initialSquad.length > 0 ? initialSquad : sorted.map(p => p.id))
  );

  const toggle = (id) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const selectAll = () => setSelected(new Set(sorted.map(p => p.id)));
  const clearAll = () => setSelected(new Set());

  const overCap = selected.size > SOFT_CAP;
  const canProceed = selected.size > 0;

  return (
    <div className="pb-40">
      <Header title={title || "MATCH-DAY SQUAD"} onBack={onBack} />

      <div className="px-4 pt-4">
        <div className="text-xs text-stone-400 mb-1">vs {setup.opponent}</div>
        <div className="text-sm text-stone-200 mb-3">
          {subtitle || (<>Tap players who are <span className="font-bold">available for this match</span>. Unchecked players are OUT.
          Soft limit is <span className="font-bold">{SOFT_CAP}</span> ({fmt.key} max squad) — you can exceed it if you need to.</>)}
        </div>

        <div className="flex gap-2 mb-4">
          <button onClick={selectAll} className="flex-1 py-2 bg-stone-900 rounded-lg text-xs font-bold tracking-wider text-stone-200 active:scale-95">ALL IN</button>
          <button onClick={clearAll} className="flex-1 py-2 bg-stone-900 rounded-lg text-xs font-bold tracking-wider text-stone-200 active:scale-95">ALL OUT</button>
        </div>

        <div className="space-y-1.5">
          {sorted.map(p => {
            const on = selected.has(p.id);
            return (
              <button
                key={p.id}
                onClick={() => toggle(p.id)}
                className={`w-full flex items-center gap-3 p-2.5 rounded-xl border-2 text-left active:scale-[0.98] transition ${
                  on ? 'bg-lime-500/10 border-lime-400' : 'bg-stone-900 border-stone-800 opacity-60'
                }`}
              >
                <PlayerAvatar
                  player={p}
                  sizeClass="w-11 h-11"
                  textSize="text-xl"
                  numberClasses={on ? 'bg-stone-900 text-lime-400' : 'bg-stone-800 text-stone-400'}
                />
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">{p.name}</div>
                  <div className={`text-[10px] font-bold tracking-wider ${on ? 'text-lime-700' : 'text-stone-400'}`}>
                    {on ? 'AVAILABLE' : 'OUT'}{p.position ? ` · ${p.position}` : ''}
                  </div>
                </div>
                <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 ${
                  on ? 'bg-lime-500 border-lime-600' : 'bg-stone-900 border-stone-700'
                }`}>
                  {on && <span className="text-white text-sm font-bold">✓</span>}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-stone-900 border-t border-stone-800 p-4 shadow-xl">
        <div className={`text-center text-sm mb-2 ${overCap ? 'text-amber-700' : 'text-stone-300'}`}>
          <span className={`font-bold ${overCap ? 'text-amber-700' : 'text-lime-700'}`}>{selected.size}</span> in squad
          {overCap && <span className="ml-1 text-xs">⚠ over {SOFT_CAP}</span>}
        </div>
        <button
          onClick={() => canProceed && onNext(Array.from(selected))}
          disabled={!canProceed}
          className={`w-full font-display text-2xl py-4 rounded-2xl shadow-lg border-2 active:scale-[0.99] transition ${
            canProceed
              ? 'bg-stone-900 text-lime-400 border-stone-900'
              : 'bg-stone-800 text-stone-400 border-stone-700 cursor-not-allowed'
          }`}
        >
          {nextLabel || 'NEXT: STARTING LINEUP →'}
        </button>
      </div>
    </div>
  );
}

/* ---------- STARTING LINEUP ---------- */
function StartingLineupView({ roster, games, squad, setup, teamLiveInput, onSaveTeamLiveInput, onBack, onStart }) {
  // Only players in the matchday squad are eligible. Legacy fallback: whole roster.
  const squadSet = squad && squad.length > 0 ? new Set(squad) : null;
  const pool = squadSet ? roster.filter(p => squadSet.has(p.id)) : roster;
  const sorted = [...pool].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));
  const [selected, setSelected] = useState(() => new Set(sorted.map(p => p.id)));
  // Default the match GK to the first player with position='GK' if any.
  const [gkId, setGkId] = useState(() => {
    const defaultGK = sorted.find(p => p.position === 'GK');
    return defaultGK ? defaultGK.id : null;
  });
  // Live mirror of the kickoff tactical board's positions, kept in a ref so
  // START GAME can snapshot them without re-rendering every drag.
  const kickoffPositionsRef = useRef({});
  const onFieldPlayers = sorted.filter(p => selected.has(p.id));

  // Livestream attachment. Cloudflare Stream Live Inputs are persistent: one
  // input = one fixed RTMPS URL + Stream Key, reusable forever. We provision
  // a "team live input" ONCE, save it via onSaveTeamLiveInput, and reuse it
  // for every game so the coach pastes the key into the Insta360 / OBS app
  // exactly one time. After that, "GO LIVE" is a single tap — it just
  // attaches the saved team input to this game so the public page shows the
  // 🔴 LIVE badge and HLS player.
  const [attached, setAttached] = useState(false); // attach the saved team input to this game
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveErr, setLiveErr] = useState(null);
  const [showSetup, setShowSetup] = useState(false); // first-time setup modal
  const [pendingSetup, setPendingSetup] = useState(null); // freshly-created creds awaiting confirm
  const [showCreds, setShowCreds] = useState(false); // toggle visible creds panel
  const [copied, setCopied] = useState(null);

  // YouTube Live mode state — 🔴 LIVE button auto-detects the active stream
  // on the @Stompers2016 channel via YouTube Data API (called through worker).
  const [ytInput, setYtInput] = useState('');
  const [ytVideoId, setYtVideoId] = useState(null);
  const [ytBusy, setYtBusy] = useState(false);
  const [ytErr, setYtErr] = useState(null);

  // Auto-detect live stream from YouTube channel
  const onDetectLive = () => {
    if (ytBusy) return;
    setYtBusy(true);
    setYtErr(null);
    workerPost(`/youtube-live`)
      .then(r => r.json().then(j => r.ok ? j : Promise.reject(j.error || 'detection failed')))
      .then((data) => {
        if (data.live && data.videoId) {
          setYtVideoId(data.videoId);
          setYtInput(data.videoId);
        } else {
          setYtErr('No live stream detected — start streaming in Insta360 first');
        }
      })
      .catch((err) => setYtErr(String(err)))
      .finally(() => setYtBusy(false));
  };

  // Tap "GO LIVE". If the team has a saved live input, just attach it to
  // this game — no copying, no waiting. If not, kick off the one-time setup
  // flow that creates the persistent input and shows the credentials.
  const onGoLive = () => {
    if (teamLiveInput) { setAttached(true); return; }
    if (liveBusy) return;
    setLiveBusy(true);
    setLiveErr(null);
    workerPost(`/live-input`, { name: 'stompers-team-live' })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || 'live-input failed')))
      .then((info) => {
        setPendingSetup({ ...info, createdAt: Date.now() });
        setShowSetup(true);
      })
      .catch((err) => setLiveErr(String(err)))
      .finally(() => setLiveBusy(false));
  };

  const confirmSetup = async () => {
    if (!pendingSetup) return;
    await onSaveTeamLiveInput(pendingSetup);
    setAttached(true);
    setShowSetup(false);
    setPendingSetup(null);
  };

  const cancelSetup = () => {
    // User backed out before confirming — delete the freshly-created live
    // input so we don't leave orphans in Cloudflare Stream.
    if (pendingSetup?.uid) {
      workerPost(`/live-input/${pendingSetup.uid}/delete`).catch(() => {});
    }
    setPendingSetup(null);
    setShowSetup(false);
  };

  const resetTeamKey = async () => {
    const ok = window.confirm('Reset team stream key?\n\nThis deletes the current Cloudflare Stream Live Input. You\'ll need to paste a NEW RTMPS URL + Stream Key into the Insta360 / OBS app. Only do this if the key was compromised or you want a fresh start.');
    if (!ok) return;
    if (teamLiveInput?.uid) {
      try {
        await workerPost(`/live-input/${teamLiveInput.uid}/delete`);
      } catch (e) {}
    }
    await onSaveTeamLiveInput(null);
    setAttached(false);
  };

  const copy = (text, key) => {
    try {
      navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch (e) {}
  };

  const toggle = (id) => {
    const next = new Set(selected);
    if (next.has(id)) {
      next.delete(id);
      // If we unselect the chosen GK, clear the tag too.
      if (gkId === id) setGkId(null);
    } else {
      next.add(id);
    }
    setSelected(next);
  };

  const pickGK = (id, e) => {
    e.stopPropagation();
    // Picking GK auto-puts them on field.
    if (!selected.has(id)) {
      const next = new Set(selected);
      next.add(id);
      setSelected(next);
    }
    setGkId(gkId === id ? null : id);
  };

  const selectAll = () => setSelected(new Set(sorted.map(p => p.id)));
  const clearAll = () => { setSelected(new Set()); setGkId(null); };

  const gkPlayer = sorted.find(p => p.id === gkId);

  return (
    <div className="pb-8">
      <Header title="STARTING LINEUP" onBack={onBack} />

      <div className="px-4 pt-4">
        <div className="text-xs text-stone-400 mb-1">vs {setup.opponent}</div>
        <div className="text-sm text-stone-200 mb-3">Tap a player to put them on the field. Tap the <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-bold text-[10px]">🧤 GK</span> button on the right to assign the goalie.</div>

        <div className="flex gap-2 mb-4">
          <button onClick={selectAll} className="flex-1 py-2 bg-stone-900 rounded-lg text-xs font-bold tracking-wider text-stone-200 active:scale-95">ALL ON</button>
          <button onClick={clearAll} className="flex-1 py-2 bg-stone-900 rounded-lg text-xs font-bold tracking-wider text-stone-200 active:scale-95">ALL BENCH</button>
        </div>

        <div className="space-y-1.5">
          {sorted.map(p => {
            const on = selected.has(p.id);
            const isGK = gkId === p.id;
            const isDefaultGK = p.position === 'GK';
            return (
              <div
                key={p.id}
                className={`relative w-full flex items-stretch rounded-xl border-2 transition ${
                  on
                    ? (isGK ? 'bg-amber-500/10 border-amber-400' : 'bg-lime-500/10 border-lime-400')
                    : (isDefaultGK ? 'bg-amber-50/50 border-amber-200 opacity-60' : 'bg-stone-900 border-stone-800 opacity-60')
                }`}
              >
                {isGK && (
                  <div className="absolute -top-2 -left-2 bg-amber-400 text-stone-100 text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full border border-amber-600 shadow-sm flex items-center gap-0.5 z-10">
                    <span>🧤</span><span>GK</span>
                  </div>
                )}
                <button
                  onClick={() => toggle(p.id)}
                  className="flex-1 flex items-center gap-3 p-2.5 text-left active:scale-[0.98] transition"
                >
                  <PlayerAvatar
                    player={p}
                    sizeClass="w-11 h-11"
                    textSize="text-xl"
                    numberClasses={on
                      ? (isGK ? 'bg-amber-500 text-stone-100' : 'bg-stone-900 text-lime-400')
                      : 'bg-stone-800 text-stone-400'}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">{p.name}</div>
                    <div className={`text-[10px] font-bold tracking-wider ${
                      on ? (isGK ? 'text-amber-700' : 'text-lime-700') : 'text-stone-400'
                    }`}>
                      {on ? (isGK ? 'IN GOAL' : 'ON FIELD') : 'BENCH'}{isDefaultGK && !isGK ? ' · default GK' : ''}
                    </div>
                  </div>
                  <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 ${
                    on
                      ? (isGK ? 'bg-amber-500 border-amber-600' : 'bg-lime-500 border-lime-600')
                      : 'bg-stone-900 border-stone-700'
                  }`}>
                    {on && <span className="text-white text-sm font-bold">✓</span>}
                  </div>
                </button>
                <button
                  onClick={(e) => pickGK(p.id, e)}
                  className={`shrink-0 w-12 flex flex-col items-center justify-center text-[10px] font-extrabold tracking-wider border-l-2 active:scale-[0.95] transition ${
                    isGK
                      ? 'bg-amber-400 text-stone-100 border-amber-500'
                      : 'bg-stone-900 text-stone-400 border-stone-800 hover:text-amber-600'
                  }`}
                  aria-label={isGK ? 'Remove GK tag' : 'Set as GK'}
                >
                  <span className="text-lg leading-none">🧤</span>
                  <span className="mt-0.5 leading-none">GK</span>
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="px-4 mt-6 bg-stone-900 border-t border-stone-800 pt-4 pb-6">
        <div className="text-center text-sm text-stone-300 mb-1">
          <span className="font-bold text-lime-700">{selected.size}</span> on field · <span className="font-bold text-stone-400">{sorted.length - selected.size}</span> on bench
        </div>
        <div className="text-center text-xs mb-2">
          {gkPlayer ? (
            <span className="text-amber-700 font-bold">🧤 GK: {gkPlayer.name} #{gkPlayer.number}</span>
          ) : (
            <span className="text-red-600 font-bold">⚠ No goalie selected — tap the GK button on a player.</span>
          )}
        </div>

        {/* Livestream section — gated by LIVE_MODE.
            'youtube':    simple video ID input (free, paste once per game)
            'cloudflare': one-tap attach via persistent team Cloudflare Stream Live Input ($5/mo) */}
        {liveErr && (
          <div className="text-center text-[11px] text-red-400 mb-2">{liveErr}</div>
        )}

        {LIVE_MODE === 'youtube' && (
          <>
            {!ytVideoId ? (
              <div className="mb-2 space-y-2">
                <button
                  onClick={onDetectLive}
                  disabled={ytBusy}
                  className="w-full py-2.5 rounded-xl bg-stone-950 border-2 border-dashed border-red-700 text-sm font-bold text-red-300 active:scale-[0.99] transition disabled:opacity-50"
                >
                  {ytBusy ? '⏳ DETECTING STREAM…' : '🔴 GO LIVE'}
                </button>
                {ytErr && <div className="text-center text-[11px] text-red-400">{ytErr}</div>}
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={ytInput}
                    onChange={(e) => setYtInput(e.target.value.trim())}
                    placeholder="Or paste YouTube URL / Video ID"
                    className="flex-1 bg-stone-950 border border-stone-700 rounded-xl px-3 py-2 text-xs text-stone-300 placeholder-stone-600 outline-none focus:border-stone-500"
                  />
                  <button
                    onClick={() => { if (ytInput) setYtVideoId(ytInput); }}
                    disabled={!ytInput}
                    className="px-3 rounded-xl bg-stone-800 text-stone-300 text-xs font-bold active:scale-95 disabled:opacity-40"
                  >
                    SET
                  </button>
                </div>
              </div>
            ) : (
              <div className="mb-2 rounded-xl bg-red-950/40 border border-red-700 px-3 py-2 flex items-center justify-between">
                <span className="text-sm font-bold text-red-300 flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  LIVE: {ytVideoId}
                </span>
                <button onClick={() => { setYtVideoId(null); setYtInput(''); setYtErr(null); }} className="text-[10px] text-stone-400 tracking-wider active:text-stone-200">REMOVE</button>
              </div>
            )}
          </>
        )}

        {LIVE_MODE === 'cloudflare' && (
          <>
            {!teamLiveInput ? (
              <button
                onClick={onGoLive}
                disabled={liveBusy}
                className="w-full mb-2 py-2.5 rounded-xl bg-stone-950 border-2 border-dashed border-red-700 text-sm font-bold text-red-300 active:scale-[0.99] transition disabled:opacity-50"
              >
                {liveBusy ? '⏳ SETTING UP STREAM…' : '🔴 GO LIVE (one-time setup)'}
              </button>
            ) : !attached ? (
              <button
                onClick={() => setAttached(true)}
                className="w-full mb-2 py-2.5 rounded-xl bg-stone-950 border-2 border-dashed border-red-700 text-sm font-bold text-red-300 active:scale-[0.99] transition"
              >
                🔴 GO LIVE (Insta360 should be streaming)
              </button>
            ) : (
              <div className="mb-2 rounded-xl bg-red-950/40 border border-red-700 overflow-hidden">
                <div className="px-3 py-2 flex items-center justify-between">
                  <span className="text-sm font-bold text-red-300 flex items-center gap-2">
                    <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                    LIVE ATTACHED
                  </span>
                  <button onClick={() => setAttached(false)} className="text-[10px] text-stone-400 tracking-wider active:text-stone-200">REMOVE</button>
                </div>
                <div className="px-3 pb-2 text-[11px] text-stone-400">
                  The public page will show 🔴 LIVE the moment you tap START GAME.
                  Make sure the Insta360 / OBS app is pushing to your saved RTMPS key.
                </div>
                <div className="px-3 pb-3 flex items-center gap-3">
                  <button onClick={() => setShowCreds(s => !s)} className="text-[10px] text-stone-400 tracking-wider active:text-stone-200">
                    {showCreds ? 'HIDE' : 'SHOW'} STREAM KEY
                  </button>
                  <button onClick={resetTeamKey} className="text-[10px] text-stone-500 tracking-wider active:text-red-400">RESET KEY</button>
                </div>
                {showCreds && (
                  <div className="px-3 pb-3 space-y-2 text-[11px] border-t border-red-800/60 pt-2">
                    <div>
                      <div className="flex items-center justify-between text-stone-500 mb-0.5">
                        <span>RTMPS Server</span>
                        <button onClick={() => copy(teamLiveInput.rtmpsUrl, 'url')} className="text-lime-400 font-bold tracking-wider">{copied === 'url' ? 'COPIED ✓' : 'COPY'}</button>
                      </div>
                      <code className="block bg-stone-950 p-2 rounded text-lime-400 break-all">{teamLiveInput.rtmpsUrl}</code>
                    </div>
                    <div>
                      <div className="flex items-center justify-between text-stone-500 mb-0.5">
                        <span>Stream Key</span>
                        <button onClick={() => copy(teamLiveInput.streamKey, 'key')} className="text-lime-400 font-bold tracking-wider">{copied === 'key' ? 'COPIED ✓' : 'COPY'}</button>
                      </div>
                      <code className="block bg-stone-950 p-2 rounded text-lime-400 break-all">{teamLiveInput.streamKey}</code>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        <KickoffTacticalBoard
          players={onFieldPlayers}
          gkId={gkId}
          positionsRef={kickoffPositionsRef}
          learnedDefaults={useMemo(() => learnPlayerDefaults(games || []), [games])}
        />

        <button
          onClick={() => {
            const livePayload = LIVE_MODE === 'cloudflare' && attached ? teamLiveInput : null;
            const ytId = LIVE_MODE === 'youtube' ? ytVideoId : null;
            // Snapshot the kickoff formation so startNewGame can seed
            // POSITION events at t=0 — only for players who are actually
            // on the field at kickoff.
            const liveOnField = new Set(Array.from(selected));
            const snap = kickoffPositionsRef.current || {};
            const kickoffPositions = Object.entries(snap)
              .filter(([pid]) => liveOnField.has(pid))
              .map(([playerId, xy]) => ({ playerId, x: xy.x, y: xy.y }));
            onStart(Array.from(selected), gkId, livePayload, ytId, kickoffPositions);
          }}
          disabled={!gkId}
          className={`w-full font-display text-2xl py-4 rounded-2xl shadow-lg border-2 active:scale-[0.99] transition ${
            gkId
              ? 'bg-lime-500 text-stone-100 shadow-lime-500/30 border-lime-600'
              : 'bg-stone-800 text-stone-400 border-stone-700 cursor-not-allowed'
          }`}
        >
          ▶ START GAME
        </button>
      </div>

      {/* First-time stream setup modal — only shown the first time the coach
          taps GO LIVE. After confirming, the RTMPS key is saved to team
          storage and never asked for again. */}
      {showSetup && pendingSetup && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center p-4" onClick={cancelSetup}>
          <div className="bg-stone-900 rounded-2xl border border-red-800 max-w-md w-full p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-center mb-3">
              <div className="text-xs tracking-[0.2em] text-red-400 mb-1">ONE-TIME SETUP</div>
              <div className="font-display text-2xl text-stone-100">Team Stream Key</div>
            </div>
            <p className="text-sm text-stone-300 mb-3">
              Paste these into the <b>Insta360</b> app once (Settings → Live → Custom RTMP).
              After this, you'll never see this screen again — just tap GO LIVE before each match.
            </p>
            <div className="space-y-3 text-xs mb-4">
              <div>
                <div className="flex items-center justify-between text-stone-500 mb-1">
                  <span className="font-bold tracking-wider">RTMPS SERVER URL</span>
                  <button onClick={() => copy(pendingSetup.rtmpsUrl, 'surl')} className="text-lime-400 font-bold tracking-wider">{copied === 'surl' ? 'COPIED ✓' : 'COPY'}</button>
                </div>
                <code className="block bg-stone-950 p-2.5 rounded text-lime-400 break-all">{pendingSetup.rtmpsUrl}</code>
              </div>
              <div>
                <div className="flex items-center justify-between text-stone-500 mb-1">
                  <span className="font-bold tracking-wider">STREAM KEY</span>
                  <button onClick={() => copy(pendingSetup.streamKey, 'skey')} className="text-lime-400 font-bold tracking-wider">{copied === 'skey' ? 'COPIED ✓' : 'COPY'}</button>
                </div>
                <code className="block bg-stone-950 p-2.5 rounded text-lime-400 break-all">{pendingSetup.streamKey}</code>
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={cancelSetup} className="flex-1 py-3 rounded-xl bg-stone-800 text-stone-300 font-bold text-sm active:scale-95">
                CANCEL
              </button>
              <button onClick={confirmSetup} className="flex-[2] py-3 rounded-xl bg-lime-500 text-stone-100 font-bold text-sm active:scale-95">
                ✓ SAVED TO INSTA360
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- TACTICAL BOARD ---------- */
// Half-field portrait pitch (our goal at bottom, halfway line at top).
// Coordinates: x,y ∈ [0,1]. Drag-end writes a POSITION event so the formation
// snapshot survives reloads and feeds post-game analytics.

// Named slots for the tap-to-pick position picker. y=0 is the halfway line
// (opponent end), y=1 is our goal line. Strikers push high, defenders sit
// deep just in front of the box. These are the labels coaches typically
// shout from the sideline (LD/CD/RD, LM/CM/RM, LST/CST/RST).
const POSITION_SLOTS = [
  { key: 'LST', label: 'LST', x: 0.30, y: 0.28 },
  { key: 'CST', label: 'CST', x: 0.50, y: 0.22 },
  { key: 'RST', label: 'RST', x: 0.70, y: 0.28 },
  { key: 'LM',  label: 'LM',  x: 0.22, y: 0.50 },
  { key: 'CM',  label: 'CM',  x: 0.50, y: 0.50 },
  { key: 'RM',  label: 'RM',  x: 0.78, y: 0.50 },
  { key: 'LD',  label: 'LD',  x: 0.25, y: 0.72 },
  { key: 'CD',  label: 'CD',  x: 0.50, y: 0.74 },
  { key: 'RD',  label: 'RD',  x: 0.75, y: 0.72 },
];

// Snap each outfield player to the closest named POSITION_SLOTS slot to
// where they currently stand. If a slot is already claimed, the next-closest
// available slot wins. Greedy assignment by ascending distance — good enough
// for 6-10 players (a Hungarian-optimal solution would be overkill).
// Returns: { [playerId]: { x, y } }
function snapOutfieldToSlots(outfieldIds, currentPositions) {
  const pairs = [];
  for (const pid of outfieldIds) {
    const cur = currentPositions[pid] || { x: 0.5, y: 0.5 };
    for (const slot of POSITION_SLOTS) {
      const d = (slot.x - cur.x) ** 2 + (slot.y - cur.y) ** 2;
      pairs.push({ pid, slot, d });
    }
  }
  pairs.sort((a, b) => a.d - b.d);
  const assigned = {};
  const usedSlots = new Set();
  for (const { pid, slot } of pairs) {
    if (assigned[pid]) continue;
    if (usedSlots.has(slot.key)) continue;
    assigned[pid] = { x: slot.x, y: slot.y };
    usedSlots.add(slot.key);
    if (Object.keys(assigned).length === outfieldIds.length) break;
  }
  // Edge case: more outfielders than slots (9 slots → only matters at 10v10+).
  // Fall back to the player's current position so they're not displaced.
  for (const pid of outfieldIds) {
    if (!assigned[pid]) assigned[pid] = currentPositions[pid] || { x: 0.5, y: 0.5 };
  }
  return assigned;
}

// Coarse band each POSITION_SLOTS key belongs to. Multi-sub matching works at
// BAND level, not slot level, and that is a measured decision rather than a
// simplification: across 12 games of real POSITION pins, predicting a player's
// band from their history in PRIOR games is right ~59% of the time (chance
// ~33%), while predicting the exact slot is right only ~29%. Left/right/centre
// is genuinely not stable for these kids — several have near-equal LD and RD
// counts — so the band is the part of the signal that actually holds up.
const SLOT_BAND = {
  LST: 'F', CST: 'F', RST: 'F',
  LM:  'M', CM:  'M', RM:  'M',
  LD:  'D', CD:  'D', RD:  'D',
};

// Nearest named slot to an (x, y) board spot. POSITION events store raw drag
// coords, but the picker snaps to POSITION_SLOTS, so most real pins sit exactly
// on a slot and this is a no-op for them.
//
// POSITION_SLOTS holds outfield slots only, so a keeper pinned at the default
// (0.5, 0.94) would otherwise land on CD and be counted as a defender. Return
// 'GK' for pins that sit at or behind the deepest outfield slot instead.
function nearestSlotKey(x, y) {
  if (y >= 0.85) return 'GK';
  let best = null, bestD = Infinity;
  for (const s of POSITION_SLOTS) {
    const d = (s.x - x) ** 2 + (s.y - y) ** 2;
    if (d < bestD) { bestD = d; best = s.key; }
  }
  return best;
}

// How often each player has been pinned in each band, counted over every game
// passed in (history + the game in progress). Returns
// { [playerId]: { D: n, M: n, F: n } }. Players with no pins are simply absent,
// which callers must treat as "no preference" rather than "no position".
function bandHistory(games) {
  const hist = {};
  for (const g of games || []) {
    for (const e of g?.events || []) {
      if (e.type !== 'POSITION') continue;
      if (typeof e.x !== 'number' || typeof e.y !== 'number' || !e.playerId) continue;
      const band = SLOT_BAND[nearestSlotKey(e.x, e.y)];
      if (!band) continue; // GK slot: keeper handling is separate
      hist[e.playerId] = hist[e.playerId] || { D: 0, M: 0, F: 0 };
      hist[e.playerId][band] += 1;
    }
  }
  return hist;
}

// Share of a player's pins that fall in `band` (0..1). 0 when we've never
// pinned them, so a brand-new player never outranks someone with real history.
function bandAffinity(hist, playerId, band) {
  const h = hist[playerId];
  if (!h) return 0;
  const total = h.D + h.M + h.F;
  if (!total) return 0;
  return (h[band] || 0) / total;
}

// Match bench players to the slots just vacated.
//
// Zone fit leads, minutes break ties (the coach's call): for each vacated slot
// we want the bench player whose history most says "this band", and among
// equally-good fits we prefer the one who has played MORE — their legs are the
// freshest thing we can't measure, and it keeps the least-played kid available
// for the next rotation.
//
// Greedy by ascending cost, same shape as snapOutfieldToSlots: with <=9 slots
// and <=12 bench players a Hungarian solve buys nothing a coach would notice.
//
// vacatedSlots: [{ key, x, y, slotKey? }] — `key` must be unique per entry
// (two outgoing players can share a named slot); `slotKey` carries the named
// slot when `key` has been made unique. benchIds: [playerId]
// Returns { pairs: [{ slot, playerId }], leftoverSlots, leftoverBench }
function assignIncomingToSlots(vacatedSlots, benchIds, bandHist, secondsByPlayer) {
  const candidates = [];
  for (const slot of vacatedSlots) {
    const band = SLOT_BAND[slot.slotKey || slot.key];
    for (const pid of benchIds) {
      // Primary: band mismatch (0 = perfect fit). Secondary: minutes, scaled
      // small enough that it only ever separates equal fits.
      const fit = 1 - bandAffinity(bandHist, pid, band);
      const mins = (secondsByPlayer?.[pid] || 0) / 60;
      candidates.push({ slot, pid, cost: fit - Math.min(mins, 90) / 10000 });
    }
  }
  candidates.sort((a, b) => a.cost - b.cost);

  const pairs = [];
  const usedSlots = new Set();
  const usedPlayers = new Set();
  for (const c of candidates) {
    if (usedSlots.has(c.slot.key) || usedPlayers.has(c.pid)) continue;
    usedSlots.add(c.slot.key);
    usedPlayers.add(c.pid);
    pairs.push({ slot: c.slot, playerId: c.pid });
    if (pairs.length === Math.min(vacatedSlots.length, benchIds.length)) break;
  }
  return {
    pairs,
    leftoverSlots: vacatedSlots.filter(s => !usedSlots.has(s.key)),
    leftoverBench: benchIds.filter(p => !usedPlayers.has(p)),
  };
}

// Small modal that floats above the tactical board so the coach can tap a
// position label instead of dragging. Used by both KickoffTacticalBoard and
// the in-game TacticalBoard. Rendered as a centered overlay so it works
// regardless of where the player chip sits on the pitch.
function PositionPickerModal({ player, currentXY, isGK, onPick, onClose }) {
  if (!player) return null;
  const first = (player.name || '').split(' ')[0] || player.name || 'Player';
  // Highlight the slot the player is closest to so the current spot is obvious.
  const nearest = (() => {
    let best = null, bestD = Infinity;
    for (const s of POSITION_SLOTS) {
      const d = (s.x - currentXY.x) ** 2 + (s.y - currentXY.y) ** 2;
      if (d < bestD) { bestD = d; best = s.key; }
    }
    return bestD < 0.015 ? best : null;
  })();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="bg-stone-900 border border-stone-700 rounded-2xl p-4 w-full max-w-xs shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-center mb-3">
          <div className="text-[10px] tracking-[0.2em] text-stone-400">PICK POSITION</div>
          <div className="font-display text-xl text-stone-100 mt-0.5">
            {isGK ? '🧤 ' : ''}#{player.number || '?'} {first}
          </div>
        </div>
        {isGK ? (
          <div className="text-xs text-stone-300 text-center bg-stone-800 rounded-xl p-3 mb-3">
            Goalkeeper position is fixed.
            Drag on the board if you want to nudge it.
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2 mb-3">
            {POSITION_SLOTS.map(s => {
              const isCurrent = nearest === s.key;
              return (
                <button
                  key={s.key}
                  onClick={() => onPick(s)}
                  className={`py-3 rounded-xl font-display text-sm border-2 active:scale-95 transition ${
                    isCurrent
                      ? 'bg-lime-500 text-stone-950 border-lime-300'
                      : 'bg-stone-800 text-stone-100 border-stone-700 hover:bg-stone-700'
                  }`}
                >
                  {s.label}
                </button>
              );
            })}
          </div>
        )}
        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-stone-800 text-stone-300 font-bold text-sm tracking-wider active:scale-95"
        >
          CANCEL
        </button>
      </div>
    </div>
  );
}

// Snap an (x, y) coordinate to the nearest named position slot (LD/CM/RST…).
function nearestPositionSlot(x, y) {
  let best = null, bestD = Infinity;
  for (const s of POSITION_SLOTS) {
    const d = (s.x - x) ** 2 + (s.y - y) ** 2;
    if (d < bestD) { bestD = d; best = s; }
  }
  return best;
}

// Scan all finished games' POSITION events and learn each player's most
// frequent named slot. Recent games (last 5) count double so seasonal role
// shifts win out over ancient data. Returns:
//   { [playerId]: { slot: <POSITION_SLOTS entry>, count, weight } }
// Only players with at least one POSITION event get an entry; everyone else
// falls back to the default-formation grid in the caller.
function learnPlayerDefaults(games) {
  if (!Array.isArray(games) || games.length === 0) return {};
  // Sort newest first by date, then by endedAt for tournament days.
  const sorted = [...games].sort((a, b) => {
    const dc = new Date(b.date || 0) - new Date(a.date || 0);
    if (dc !== 0) return dc;
    return (b.endedAt || 0) - (a.endedAt || 0);
  });
  // { [playerId]: { [slotKey]: weightedCount } }
  const tally = {};
  // Minimum dwell time (seconds) for an in-game POSITION event to count.
  // Anything shorter is treated as a coach drag-correction.
  const MIN_DWELL_S = 10;
  sorted.forEach((g, gameIdx) => {
    if (!Array.isArray(g.events)) return;
    const w = gameIdx < 5 ? 2 : 1; // recency weighting
    // Group POSITION events by (playerId, period) so the dwell-time rule is
    // computed within the same half. Pre-kickoff drag corrections (all at
    // elapsed=0 in period 1) collapse to just the final position because
    // every earlier event has an immediate next event in the same group.
    const grouped = {}; // key = `${playerId}|${period}` -> [events sorted by at]
    for (const ev of g.events) {
      if (ev.type !== 'POSITION' || !ev.playerId) continue;
      if (typeof ev.x !== 'number' || typeof ev.y !== 'number') continue;
      const period = ev.period || 1;
      const key = `${ev.playerId}|${period}`;
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(ev);
    }
    for (const arr of Object.values(grouped)) {
      arr.sort((a, b) => (a.at || 0) - (b.at || 0));
      for (let i = 0; i < arr.length; i++) {
        const ev = arr[i];
        const next = arr[i + 1];
        if (next) {
          // Use wall-clock `at` (ms) — robust whether or not `elapsed` was
          // updated for the new event.
          const dwellMs = (next.at || 0) - (ev.at || 0);
          if (dwellMs < MIN_DWELL_S * 1000) continue;
        }
        // ev "stuck" — either >=10s before being changed, or the last
        // position of the period (no revision came).
        const slot = nearestPositionSlot(ev.x, ev.y);
        if (!slot) continue;
        if (!tally[ev.playerId]) tally[ev.playerId] = {};
        tally[ev.playerId][slot.key] = (tally[ev.playerId][slot.key] || 0) + w;
      }
    }
  });
  const out = {};
  for (const pid of Object.keys(tally)) {
    let bestKey = null, bestW = 0;
    for (const [k, w] of Object.entries(tally[pid])) {
      if (w > bestW) { bestW = w; bestKey = k; }
    }
    if (!bestKey) continue;
    const slot = POSITION_SLOTS.find(s => s.key === bestKey);
    if (!slot) continue;
    out[pid] = { slot, weight: bestW };
  }
  return out;
}

function computeDefaultFormation(outfield, totalOnField) {
  // Pick a sensible default row split by total on-field count. Includes GK
  // count so common 7v7/9v9/5v5 line up cleanly.
  const n = outfield.length;
  let rows;
  if (totalOnField <= 5)      rows = [2, n - 2];                    // 5v5 → 2-2
  else if (totalOnField === 7) rows = [2, 3, 1];                    // 7v7 → 2-3-1
  else if (totalOnField === 8) rows = [3, 3, 1];                    // 8v8 → 3-3-1
  else if (totalOnField === 9) rows = [3, 3, 2];                    // 9v9 → 3-3-2
  else if (totalOnField >= 11) rows = [4, 3, 3];                    // 11v11 → 4-3-3
  else {
    // Fallback: split into ~3 rows.
    const r = Math.max(1, Math.round(n / 3));
    rows = [r, r, Math.max(1, n - 2 * r)];
  }
  // Make rows sum to n by trimming/padding the front line.
  let sum = rows.reduce((a, b) => a + b, 0);
  while (sum > n) { rows[rows.length - 1]--; sum--; }
  while (sum < n) { rows[rows.length - 1]++; sum++; }
  rows = rows.filter(c => c > 0);
  const ys = rows.length === 3 ? [0.80, 0.55, 0.28]
            : rows.length === 2 ? [0.72, 0.32]
            : [0.50];
  const out = {};
  let idx = 0;
  rows.forEach((cnt, rowIdx) => {
    for (let i = 0; i < cnt; i++) {
      const x = (i + 1) / (cnt + 1);
      const p = outfield[idx++];
      if (p) out[p.id] = { x, y: ys[rowIdx] };
    }
  });
  return out;
}

// Kickoff-time variant of the tactical board for the Starting Lineup screen.
// The in-game TacticalBoard derives state from game.events (POSITION events),
// but at lineup time we don't have a game yet. This version keeps positions
// in local state and exposes them via getPositions() so the parent can read
// the final formation when the coach taps START GAME and seed it into the
// new game as POSITION events at t=0.
function KickoffTacticalBoard({ players, gkId, positionsRef, learnedDefaults }) {
  const sorted = useMemo(
    () => [...players].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0)),
    [players]
  );
  const outfield = useMemo(() => sorted.filter(p => p.id !== gkId), [sorted, gkId]);
  const totalOnField = sorted.length;
  const defaults = useMemo(
    () => computeDefaultFormation(outfield, totalOnField),
    [outfield.map(p => p.id).join(','), totalOnField]
  );
  const gkDefault = { x: 0.5, y: 0.94 };

  // Resolve learned-vs-default seeding once per (player set, gk) change.
  // Rule: learned defaults from past games win first-come-first-served by
  // jersey number. If two players have learned the same slot, the lower
  // jersey number keeps it and the other falls back to the grid default.
  const learnedSeed = useMemo(() => {
    if (!learnedDefaults || Object.keys(learnedDefaults).length === 0) return {};
    const taken = new Set();
    const seed = {};
    // sorted is already by jersey number — first-come wins.
    for (const p of outfield) {
      const entry = learnedDefaults[p.id];
      if (!entry || !entry.slot) continue;
      if (taken.has(entry.slot.key)) continue;
      taken.add(entry.slot.key);
      seed[p.id] = { x: entry.slot.x, y: entry.slot.y };
    }
    return seed;
  }, [outfield.map(p => p.id).join(','), learnedDefaults]);

  // Seed positions from learned defaults first, then grid defaults, whenever
  // the player set or GK changes. Preserve anything the coach has already
  // dragged for players that remain on the field.
  const [positions, setPositions] = useState({});
  useEffect(() => {
    setPositions(prev => {
      const next = {};
      for (const p of sorted) {
        if (prev[p.id]) { next[p.id] = prev[p.id]; continue; }
        if (p.id === gkId) { next[p.id] = gkDefault; continue; }
        if (learnedSeed[p.id]) { next[p.id] = learnedSeed[p.id]; continue; }
        next[p.id] = defaults[p.id] || { x: 0.5, y: 0.5 };
      }
      return next;
    });
  }, [sorted.map(p => p.id).join(','), gkId, defaults, learnedSeed]);

  // Publish the latest positions to the parent via the ref so START GAME
  // can seed POSITION events without a re-render dance.
  useEffect(() => {
    if (positionsRef) positionsRef.current = positions;
  }, [positions, positionsRef]);

  const [collapsed, setCollapsed] = useState(false);
  const [drag, setDrag] = useState(null);
  const [picker, setPicker] = useState(null); // { playerId } when tap (no drag)
  const fieldRef = useRef(null);
  const clamp01 = (v) => Math.max(0.04, Math.min(0.96, v));
  // Pixel distance below which a pointerdown→pointerup is treated as a tap
  // (open position picker) instead of a drag. Generous enough for finger taps
  // on iPad where the touch can wobble a few pixels.
  const TAP_PX = 8;

  const handlePointerDown = (playerId) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
    const pos = positions[playerId] || { x: 0.5, y: 0.5 };
    setDrag({
      playerId,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      x: pos.x,
      y: pos.y,
      moved: false,
    });
  };
  const handlePointerMove = (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const rect = fieldRef.current?.getBoundingClientRect();
    if (!rect) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    const moved = drag.moved || (dx * dx + dy * dy) > (TAP_PX * TAP_PX);
    if (!moved) { setDrag(d => d ? { ...d, moved: false } : d); return; }
    const x = clamp01((e.clientX - rect.left) / rect.width);
    const y = clamp01((e.clientY - rect.top) / rect.height);
    setDrag(d => d ? { ...d, x, y, moved: true } : d);
  };
  const handlePointerUp = () => {
    if (!drag) return;
    const { playerId, x, y, moved } = drag;
    setDrag(null);
    if (moved) {
      setPositions(prev => ({ ...prev, [playerId]: { x, y } }));
    } else {
      // Tap (no drag) → open the position picker for this player.
      setPicker({ playerId });
    }
  };

  const handleReset = () => {
    // Snap each outfield player to their nearest named position slot (with
    // greedy conflict resolution). GK stays in goal. This keeps the coach's
    // intent intact instead of bulldozing back to a grid formation.
    const snapped = snapOutfieldToSlots(
      outfield.map(p => p.id),
      positions
    );
    const next = {};
    for (const p of sorted) {
      next[p.id] = p.id === gkId ? gkDefault : (snapped[p.id] || defaults[p.id] || { x: 0.5, y: 0.5 });
    }
    setPositions(next);
  };

  if (sorted.length === 0) return null;

  return (
    <div className="mt-4 mb-3 bg-stone-900/80 border border-stone-800 rounded-2xl p-3">
      <div className="w-full flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={() => setCollapsed(c => !c)}
          className="flex items-center gap-2 active:scale-[0.99]"
        >
          <h3 className="font-display text-lg flex items-center gap-2">
            <span>🧭</span><span>KICKOFF FORMATION</span>
          </h3>
          <span className="text-[10px] text-stone-400 tracking-widest">
            {totalOnField}-A-SIDE · {collapsed ? 'SHOW' : 'HIDE'}
          </span>
        </button>
        {!collapsed && (
          <button
            type="button"
            onClick={handleReset}
            className="text-[10px] font-bold text-stone-300 bg-stone-800 px-2.5 py-1 rounded-full active:scale-95 tracking-widest"
          >
            RESET
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          <div
            ref={fieldRef}
            className="relative w-full mx-auto select-none touch-none overflow-hidden rounded-xl"
            style={{ aspectRatio: '4 / 3', maxWidth: '420px' }}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            <svg viewBox="0 0 100 75" className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
              <rect x="0" y="0" width="100" height="75" fill="#0e3a22" />
              {[0, 1, 2, 3].map(i => (
                <rect key={i} x="0" y={i * 18.75} width="100" height="9.4" fill="#0a3320" />
              ))}
              <rect x="2" y="2" width="96" height="71" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <line x1="2" y1="2" x2="98" y2="2" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <path d="M 42 2 A 8 8 0 0 0 58 2" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <rect x="20" y="55" width="60" height="18" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <rect x="35" y="66" width="30" height="7" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <path d="M 42 55 A 8 8 0 0 1 58 55" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <line x1="42" y1="73" x2="58" y2="73" stroke="#ffffff" strokeWidth="1.2" />
            </svg>
            {sorted.map(p => {
              const isDragging = drag?.playerId === p.id;
              const pos = isDragging ? { x: drag.x, y: drag.y } : (positions[p.id] || { x: 0.5, y: 0.5 });
              const isGK = p.id === gkId;
              const label = p.number || (p.name ? p.name[0] : '?');
              const first = (p.name || '').split(' ')[0];
              return (
                <div
                  key={p.id}
                  onPointerDown={handlePointerDown(p.id)}
                  className={`absolute flex flex-col items-center pointer-events-auto touch-none ${isDragging ? 'z-20' : 'z-10'}`}
                  style={{
                    left: `${pos.x * 100}%`,
                    top: `${pos.y * 100}%`,
                    transform: 'translate(-50%, -50%)',
                  }}
                >
                  <div
                    className={`rounded-full flex items-center justify-center font-bold text-sm text-white shadow-lg border-2 ${isGK
                      ? 'bg-amber-500 border-amber-200 text-stone-900'
                      : 'bg-lime-600 border-lime-300'
                      } ${isDragging ? 'scale-110 ring-2 ring-white/70' : ''}`}
                    style={{ width: '34px', height: '34px' }}
                  >
                    {label}
                  </div>
                  <div className="mt-0.5 text-[9px] leading-none font-bold text-white/85 bg-stone-950/60 px-1 rounded">
                    {first}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-2 text-[10px] text-stone-500 text-center">
            Tap a player to pick a position (LD/CD/RD · LM/CM/RM · LST/CST/RST).
            Drag to fine-tune. RESET snaps each player to their nearest open position.
          </div>
        </>
      )}
      {picker && (() => {
        const p = sorted.find(x => x.id === picker.playerId);
        if (!p) return null;
        const cur = positions[p.id] || { x: 0.5, y: 0.5 };
        return (
          <PositionPickerModal
            player={p}
            currentXY={cur}
            isGK={p.id === gkId}
            onPick={(slot) => {
              setPositions(prev => ({ ...prev, [p.id]: { x: slot.x, y: slot.y } }));
              setPicker(null);
            }}
            onClose={() => setPicker(null)}
          />
        );
      })()}
    </div>
  );
}

function TacticalBoard({ game, roster, gameGKId, onMove, onReset }) {
  const onField = useMemo(() => onFieldAt(game), [game.events]);
  const players = useMemo(
    () => roster.filter(p => onField.has(p.id))
      .sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0)),
    [roster, onField]
  );
  const totalOnField = players.length;
  const outfield = players.filter(p => p.id !== gameGKId);

  // Last persisted position per player.
  const latest = useMemo(() => {
    const m = {};
    for (const e of game.events) {
      if (e.type === 'POSITION') m[e.playerId] = { x: e.x, y: e.y };
    }
    return m;
  }, [game.events]);

  const defaults = useMemo(
    () => computeDefaultFormation(outfield, totalOnField),
    [outfield.map(p => p.id).join(','), totalOnField]
  );
  const gkDefault = { x: 0.5, y: 0.94 };

  const positions = {};
  for (const p of players) {
    if (latest[p.id]) positions[p.id] = latest[p.id];
    else if (p.id === gameGKId) positions[p.id] = gkDefault;
    else positions[p.id] = defaults[p.id] || { x: 0.5, y: 0.5 };
  }

  const [collapsed, setCollapsed] = useState(false);
  const [drag, setDrag] = useState(null); // { playerId, x, y, moved }
  const [picker, setPicker] = useState(null); // { playerId } when tap (no drag)
  const fieldRef = useRef(null);

  const clamp01 = (v) => Math.max(0.04, Math.min(0.96, v));
  const TAP_PX = 8;

  const handlePointerDown = (playerId) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
    const pos = positions[playerId];
    setDrag({
      playerId,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      x: pos.x,
      y: pos.y,
      moved: false,
    });
  };
  const handlePointerMove = (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const rect = fieldRef.current?.getBoundingClientRect();
    if (!rect) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    const moved = drag.moved || (dx * dx + dy * dy) > (TAP_PX * TAP_PX);
    if (!moved) return;
    const x = clamp01((e.clientX - rect.left) / rect.width);
    const y = clamp01((e.clientY - rect.top) / rect.height);
    setDrag(d => d ? { ...d, x, y, moved: true } : d);
  };
  const handlePointerUp = () => {
    if (!drag) return;
    const { playerId, x, y, moved } = drag;
    setDrag(null);
    if (moved) {
      if (onMove) onMove(playerId, x, y);
    } else {
      setPicker({ playerId });
    }
  };

  // Snap every on-field outfield player to their nearest named position
  // slot (with greedy conflict resolution). GK stays in goal. Coach
  // doesn't have to drop players on exact spots — Reset tidies them up.
  const handleReset = () => {
    if (!onReset) return;
    const snapped = snapOutfieldToSlots(
      outfield.map(p => p.id),
      positions
    );
    const slots = players.map(p => {
      const pos = p.id === gameGKId ? gkDefault : (snapped[p.id] || positions[p.id] || { x: 0.5, y: 0.5 });
      return { playerId: p.id, x: pos.x, y: pos.y };
    });
    onReset(slots);
  };

  if (players.length === 0) return null;

  return (
    <div className="mt-5 mb-2 bg-stone-900/80 border border-stone-800 rounded-2xl p-3">
      <div className="w-full flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={() => setCollapsed(c => !c)}
          className="flex items-center gap-2 active:scale-[0.99]"
        >
          <h3 className="font-display text-lg flex items-center gap-2">
            <span>🧭</span><span>TACTICAL BOARD</span>
          </h3>
          <span className="text-[10px] text-stone-400 tracking-widest">
            {totalOnField}-A-SIDE · {collapsed ? 'SHOW' : 'HIDE'}
          </span>
        </button>
        {!collapsed && (
          <button
            type="button"
            onClick={handleReset}
            className="text-[10px] font-bold text-stone-300 bg-stone-800 px-2.5 py-1 rounded-full active:scale-95 tracking-widest"
          >
            RESET
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          <div
            ref={fieldRef}
            className="relative w-full mx-auto select-none touch-none overflow-hidden rounded-xl"
            style={{ aspectRatio: '4 / 3', maxWidth: '420px' }}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            <svg
              viewBox="0 0 100 75"
              className="absolute inset-0 w-full h-full"
              preserveAspectRatio="none"
            >
              {/* Pitch fill with subtle horizontal stripes */}
              <rect x="0" y="0" width="100" height="75" fill="#0e3a22" />
              {[0, 1, 2, 3].map(i => (
                <rect key={i} x="0" y={i * 18.75} width="100" height="9.4" fill="#0a3320" />
              ))}
              {/* Touchlines */}
              <rect x="2" y="2" width="96" height="71" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              {/* Halfway line + center arc (top edge = halfway) */}
              <line x1="2" y1="2" x2="98" y2="2" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <path d="M 42 2 A 8 8 0 0 0 58 2" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              {/* Penalty area (own goal at bottom) */}
              <rect x="20" y="55" width="60" height="18" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              <rect x="35" y="66" width="30" height="7" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              {/* Penalty arc */}
              <path d="M 42 55 A 8 8 0 0 1 58 55" fill="none" stroke="#ffffff" strokeWidth="0.4" opacity="0.55" />
              {/* Goal */}
              <line x1="42" y1="73" x2="58" y2="73" stroke="#ffffff" strokeWidth="1.2" />
            </svg>
            {players.map(p => {
              const isDragging = drag?.playerId === p.id;
              const pos = isDragging ? { x: drag.x, y: drag.y } : positions[p.id];
              const isGK = p.id === gameGKId;
              const label = p.number || (p.name ? p.name[0] : '?');
              const first = (p.name || '').split(' ')[0];
              return (
                <div
                  key={p.id}
                  onPointerDown={handlePointerDown(p.id)}
                  className={`absolute flex flex-col items-center pointer-events-auto touch-none ${isDragging ? 'z-20' : 'z-10'}`}
                  style={{
                    left: `${pos.x * 100}%`,
                    top: `${pos.y * 100}%`,
                    transform: 'translate(-50%, -50%)',
                  }}
                >
                  <div
                    className={`rounded-full flex items-center justify-center font-bold text-sm text-white shadow-lg border-2 ${isGK
                      ? 'bg-amber-500 border-amber-200 text-stone-900'
                      : 'bg-lime-600 border-lime-300'
                      } ${isDragging ? 'scale-110 ring-2 ring-white/70' : ''}`}
                    style={{ width: '34px', height: '34px' }}
                  >
                    {label}
                  </div>
                  <div className="mt-0.5 text-[9px] leading-none font-bold text-white/85 bg-stone-950/60 px-1 rounded">
                    {first}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-2 text-[10px] text-stone-500 text-center">
            Tap a player to pick a position. Drag to fine-tune.
            Subs inherit the position of the player they replace.
          </div>
        </>
      )}
      {picker && (() => {
        const p = players.find(x => x.id === picker.playerId);
        if (!p) return null;
        const cur = positions[p.id] || { x: 0.5, y: 0.5 };
        return (
          <PositionPickerModal
            player={p}
            currentXY={cur}
            isGK={p.id === gameGKId}
            onPick={(slot) => {
              if (onMove) onMove(p.id, slot.x, slot.y);
              setPicker(null);
            }}
            onClose={() => setPicker(null)}
          />
        );
      })()}
    </div>
  );
}

/* ---------- ACTIVE GAME ---------- */
/* ---------- LIVE VOICE RECORDER (3.6 capture channel) ----------
 * Records sideline narration inside the PWA while the coach runs the game.
 * Sync is DETERMINISTIC: we store recordingStartedAt (wall-clock ms) with the
 * audio, and every logged event already carries `at` — so each goal/sub/period
 * tap is an exact audio anchor. No "say kickoff" cues, no drift.
 * Robustness: 10s chunks persisted to IndexedDB as they arrive (a crash loses
 * seconds, not the game); screen wake-lock while recording; auto-restart into
 * the same take if iOS hiccups the recorder; auto stop+upload on unmount
 * (= FINAL WHISTLE). Audio uploads to R2 voice/<gameId>/ and the segment is
 * recorded on the game doc (voiceSegments) for the extraction pipeline.
 * NOTE: keep the PWA foregrounded while recording — iOS suspends background
 * web apps. The wake-lock prevents auto-lock; don't switch apps mid-half.
 */
function _voiceDb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open('voiceRec', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('chunks', { keyPath: 'k' });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function _voicePut(k, v) {
  const db = await _voiceDb();
  return new Promise((res, rej) => {
    const tx = db.transaction('chunks', 'readwrite');
    tx.objectStore('chunks').put({ k, v });
    tx.oncomplete = res; tx.onerror = () => rej(tx.error);
  });
}
async function _voiceList() {
  const db = await _voiceDb();
  return new Promise((res, rej) => {
    const tx = db.transaction('chunks', 'readonly');
    const rq = tx.objectStore('chunks').getAll();
    rq.onsuccess = () => res(rq.result || []); rq.onerror = () => rej(rq.error);
  });
}
async function _voiceClear(prefix) {
  const rows = await _voiceList();
  const db = await _voiceDb();
  return new Promise((res) => {
    const tx = db.transaction('chunks', 'readwrite');
    rows.filter(r => r.k.startsWith(prefix)).forEach(r => tx.objectStore('chunks').delete(r.k));
    tx.oncomplete = res; tx.onerror = res;
  });
}
const _voiceMime = () => {
  if (typeof MediaRecorder === 'undefined') return null;
  for (const m of ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm']) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return '';
};

async function _voiceUpload(gameId, startedAt, blob, mime, durationS) {
  const ext = (mime || '').includes('mp4') ? 'm4a' : 'webm';
  const contentType = mime || 'audio/mp4';
  const filename = `voice_${gameId}_live_${startedAt}.${ext}`;
  // DEPLOYED worker contract (older than the repo's r2-upload-worker.js,
  // which has an unpublished /put proxy): /upload-url returns a PRESIGNED
  // direct-to-R2 PUT. Content-Type is part of the signature — the PUT must
  // send exactly the type we asked to sign. Mirrors the video upload flow.
  const r = await workerPost(`/upload-url`, { filename, contentType });
  if (!r.ok) throw new Error(`upload-url ${r.status}`);
  const { uploadUrl, publicUrl } = await r.json();
  const put = await fetch(uploadUrl, { method: 'PUT', headers: { 'Content-Type': contentType }, body: blob });
  if (!put.ok) throw new Error(`PUT ${put.status}`);
  // Append the segment on the game doc for the voice pipeline (3.6).
  // Best effort: if the game was deleted, the audio still lives in R2 and
  // recovery must not wedge on a missing doc.
  try {
    if (window.fbDb) {
      // Raw dugout audio index is COACH-ONLY: it lives in the voice/
      // subcollection (rules lock it), never on the family-readable game doc.
      const ref = window.fbDb.collection('teams').doc('main').collection('games').doc(gameId)
        .collection('voice').doc('segments');
      const seg = { startedAt, url: publicUrl, durationS, mime };
      const fv = (typeof firebase !== 'undefined' && firebase.firestore) ? firebase.firestore.FieldValue : null;
      if (fv) await ref.set({ list: fv.arrayUnion(seg) }, { merge: true });
      else {
        const snap = await ref.get();
        const cur = (snap.exists && snap.data().list) || [];
        await ref.set({ list: [...cur, seg] }, { merge: true });
      }
    }
  } catch (e) { console.warn('voice segment index write failed (audio is in R2):', e); }
  return publicUrl;
}

const VoiceRecorder = forwardRef(function VoiceRecorder({ game, pendingMicRef }, ref) {
  const [state, setState] = useState('idle'); // idle | rec | uploading | done | err
  const [secs, setSecs] = useState(0);
  const [paused, setPaused] = useState(false);   // mic muted for an on-field shout
  const [pauseLeft, setPauseLeft] = useState(0);  // auto-resume countdown (s)
  const recRef = useRef(null);
  const pauseTimerRef = useRef(null);
  const lpRef = useRef(null);                     // long-press (= stop) tracker
  const AUTO_RESUME_S = 5;

  // Recover orphaned chunks from ANY previous crashed/failed session — keys
  // carry their own gameId, so a take stranded on an already-finished game
  // still uploads to the right doc the next time any live game is open.
  useEffect(() => {
    (async () => {
      try {
        const rows = await _voiceList();
        if (!rows.length) return;
        const bySeg = {};
        rows.forEach(r => {
          const [gid, seg] = r.k.split('|');
          if (!gid || !seg) return;
          (bySeg[`${gid}|${seg}`] = bySeg[`${gid}|${seg}`] || []).push(r);
        });
        for (const [pref, list] of Object.entries(bySeg)) {
          const [gid, seg] = pref.split('|');
          list.sort((a, b) => Number(a.k.split('|')[2]) - Number(b.k.split('|')[2]));
          const mime = _voiceMime() || 'audio/mp4';
          const blob = new Blob(list.map(r => r.v), { type: mime });
          if (blob.size > 20000) {
            await _voiceUpload(gid, Number(seg), blob, mime, Math.round(list.length * 10));
          }
          await _voiceClear(`${pref}|`);
        }
      } catch (e) { console.warn('voice recovery failed:', e); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game.id]);

  const start = async (streamOverride) => {
    try {
      const mime = _voiceMime();
      if (mime == null) { setState('err'); return; }
      // streamOverride = a MediaStream pre-acquired inside a user gesture
      // (the kickoff tap) — required on iOS, which won't grant getUserMedia
      // outside a gesture. Falls back to acquiring our own here.
      const stream = streamOverride || await navigator.mediaDevices.getUserMedia({ audio: true });
      const startedAt = Date.now();
      const rec = { stream, startedAt, chunks: [], idx: 0, mime, wakeLock: null, stopping: false };
      recRef.current = rec;
      const newMR = () => {
        const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        rec.mr = mr;
        mr.ondataavailable = (e) => {
          if (!e.data || !e.data.size) return;
          rec.chunks.push(e.data);
          _voicePut(`${game.id}|${startedAt}|${rec.idx++}`, e.data).catch(() => {});
        };
        mr.onerror = () => { if (!rec.stopping) { try { mr.stop(); } catch (e) {} } };
        mr.onstop = () => {
          if (!rec.stopping) {
            // iOS hiccup mid-take: restart into the same take.
            try { newMR(); } catch (e) { setState('err'); }
            return;
          }
          finalize();
        };
        mr.start(10000); // 10s chunks → IndexedDB as we go
      };
      const finalize = async () => {
        try { rec.stream.getTracks().forEach(t => t.stop()); } catch (e) {}
        try { rec.wakeLock && rec.wakeLock.release(); } catch (e) {}
        setState('uploading');
        try {
          const blob = new Blob(rec.chunks, { type: rec.mime || 'audio/mp4' });
          const durationS = Math.round((Date.now() - rec.startedAt) / 1000);
          await _voiceUpload(game.id, rec.startedAt, blob, rec.mime, durationS);
          await _voiceClear(`${game.id}|${rec.startedAt}|`);
          setState('done');
        } catch (e) {
          console.error('voice upload failed (chunks kept on device for recovery):', e);
          setState('err'); // chunks stay in IndexedDB → recovered next open
        }
      };
      newMR();
      try { rec.wakeLock = await navigator.wakeLock?.request('screen'); } catch (e) {}
      document.addEventListener('visibilitychange', async () => {
        if (document.visibilityState === 'visible' && recRef.current && !recRef.current.stopping) {
          try { recRef.current.wakeLock = await navigator.wakeLock?.request('screen'); } catch (e) {}
        }
      });
      setState('rec');
      setSecs(0);
    } catch (e) {
      console.error('mic failed:', e);
      setState('err');
    }
  };

  const stop = () => {
    const rec = recRef.current;
    if (rec) rec.stopping = true;
    if (pauseTimerRef.current) { clearInterval(pauseTimerRef.current); pauseTimerRef.current = null; }
    setPaused(false); setPauseLeft(0);
    if (!rec) return;
    try { rec.mr.stop(); } catch (e) { setState('err'); }
  };

  // Pause = MUTE the mic track (records silence) rather than pausing the
  // recorder, so the take's timeline length is preserved and stays aligned to
  // the game clock for video sync — the shout is replaced by ~silence, not cut.
  const resumeMic = () => {
    try { recRef.current?.stream?.getAudioTracks().forEach(t => { t.enabled = true; }); } catch (e) {}
    if (pauseTimerRef.current) { clearInterval(pauseTimerRef.current); pauseTimerRef.current = null; }
    setPaused(false); setPauseLeft(0);
  };
  const pauseMic = () => {
    const rec = recRef.current;
    if (!rec || rec.stopping) return;
    try { rec.stream.getAudioTracks().forEach(t => { t.enabled = false; }); } catch (e) {}
    setPaused(true); setPauseLeft(AUTO_RESUME_S);
    if (pauseTimerRef.current) clearInterval(pauseTimerRef.current);
    pauseTimerRef.current = setInterval(() => {
      setPauseLeft(s => { if (s <= 1) { resumeMic(); return 0; } return s - 1; });
    }, 1000);
  };

  // Recording timer.
  useEffect(() => {
    if (state !== 'rec') return undefined;
    const id = setInterval(() => setSecs(s => s + 1), 1000);
    return () => clearInterval(id);
  }, [state]);

  // FINAL WHISTLE / leaving the game view stops + uploads automatically.
  useEffect(() => () => {
    if (pauseTimerRef.current) clearInterval(pauseTimerRef.current);
    const rec = recRef.current;
    if (rec && !rec.stopping) { rec.stopping = true; try { rec.mr.stop(); } catch (e) {} }
  }, []);

  // Imperative control for clock-driven auto-recording — the parent calls these
  // from the half-time / 2nd-half / resume / full-time handlers. startSegment
  // may carry a pre-acquired stream (kickoff); otherwise it acquires its own
  // (those calls happen inside a button tap, so iOS allows it).
  useImperativeHandle(ref, () => ({
    startSegment: (stream) => { if (state !== 'rec') start(stream); },
    stopSegment: () => { if (state === 'rec') stop(); },
    isRecording: () => state === 'rec',
  }), [state]);

  // Kickoff auto-start: adopt the mic stream pre-acquired during the START GAME
  // tap (the iOS gesture). Runs once on mount when auto-record is on; if no
  // stream was stashed it tries a plain getUserMedia (may need a manual tap on
  // iOS — the documented fallback).
  useEffect(() => {
    if (!game.autoRecord) return;
    let cancelled = false;
    (async () => {
      let stream;
      try { stream = pendingMicRef && pendingMicRef.current ? await pendingMicRef.current : undefined; }
      catch (e) { stream = undefined; }
      if (pendingMicRef) pendingMicRef.current = null;
      if (!cancelled) start(stream);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (typeof MediaRecorder === 'undefined') return null;
  const mmss = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  if (state === 'rec') {
    // Tap = pause/resume (mute the mic to shout to the field; auto-resumes in
    // AUTO_RESUME_S or on the next tap). Long-press = stop the take.
    const press = () => {
      lpRef.current = { fired: false };
      lpRef.current.t = setTimeout(() => { lpRef.current.fired = true; stop(); }, 650);
    };
    const release = () => {
      const lp = lpRef.current; if (!lp) return;
      clearTimeout(lp.t); lpRef.current = null;
      if (!lp.fired) { paused ? resumeMic() : pauseMic(); }
    };
    const cancel = () => { if (lpRef.current) { clearTimeout(lpRef.current.t); lpRef.current = null; } };
    return (
      <button
        onPointerDown={press}
        onPointerUp={release}
        onPointerLeave={cancel}
        className={`shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 active:scale-95 transition flex items-center gap-1 ${paused ? 'bg-amber-500/20 text-amber-300 border-amber-500/60' : 'bg-red-500/15 text-red-300 border-red-600/60'}`}
        title={paused ? 'Paused (muted) — tap to resume now' : 'Recording — tap to pause for a shout · hold to stop'}
      >
        {paused ? (
          <>
            <span>⏸</span>
            <span>PAUSED {pauseLeft}s · TAP</span>
          </>
        ) : (
          <>
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span>{mmss}</span>
          </>
        )}
      </button>
    );
  }
  const canStart = state === 'idle' || state === 'err' || state === 'done';
  return (
    <button
      onClick={canStart ? () => start() : undefined}
      className={`shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 active:scale-95 transition flex items-center gap-1 ${state === 'err' ? 'bg-red-500/15 text-red-400 border-red-700' : state === 'uploading' ? 'bg-stone-800 text-stone-400 border-stone-700' : 'bg-stone-900 text-stone-300 border-stone-800'}`}
      title={game.autoRecord ? 'Auto-recording with the game clock — tap to start manually' : 'Record sideline narration (synced to the game clock)'}
    >
      <span>🎙</span>
      <span>{state === 'uploading' ? 'SAVING…' : state === 'done' ? 'REC ✓' : state === 'err' ? 'REC ⚠' : 'REC'}</span>
      {game.autoRecord && <span className="text-[8px] font-extrabold tracking-widest text-lime-400/80 border border-lime-600/50 rounded px-1 leading-none">AUTO</span>}
    </button>
  );
});

function ActiveGameView({ game, roster, pendingEvent, onSelectEvent, onSelectPlayer, onResolveOppGoal, onConfirmGK, onSwapGK, onMovePosition, onResetFormation, onCancelEvent, onUndo, onPauseHalfTime, onStartSecondHalf, onResumeFirstHalf, onPauseClock, onResumeClock, onEnd, onBack, onBulkReplaceLineup, bandHist, voiceRef, pendingMicRef, tick }) {
  const elapsed = computeElapsed(game);
  const recent = [...game.events].reverse().filter(e => e.type !== 'POSITION').slice(0, 6);
  // Match-day squad limits who can be picked / subbed on. Legacy games without
  // a `squad` field fall back to the whole roster.
  const squadSet = game.squad && game.squad.length > 0 ? new Set(game.squad) : null;
  const squadRoster = squadSet ? roster.filter(p => squadSet.has(p.id)) : roster;
  const playersSorted = [...squadRoster].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));
  const gameGKId = currentGKAt(game);
  // Live seconds-on-field per squad player, refreshed by `tick`.
  const secondsByPlayer = useMemo(() => {
    const m = {};
    for (const p of squadRoster) m[p.id] = playerSeconds(p.id, game);
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game, tick]);

  const inHalfTimeBreak = isHalfTime(game);
  const inSecondHalf = game.period === 2;
  // "In the 1st half" must stay true through an injury pause — only the
  // halftime break leaves it. Deriving it from `clockRunning` alone used to
  // hide the HALF TIME button the moment the coach paused for anything else.
  const inFirstHalf = game.period === 1 && !inHalfTimeBreak;

  // Halftime bulk-lineup modal: coach taps players to flip on/off the field
  // in a single pass instead of doing one SUB at a time.
  const [halftimePicker, setHalftimePicker] = useState(false);
  // Multi-sub modal: keep-these-on flow with automatic position matching.
  const [multiSubOpen, setMultiSubOpen] = useState(false);

  // Last pinned board spot per player in THIS game — the slot an outgoing
  // player vacates, which the incoming player inherits.
  const boardPositions = useMemo(() => {
    const m = {};
    for (const e of game.events) {
      if (e.type === 'POSITION' && typeof e.x === 'number' && typeof e.y === 'number') {
        m[e.playerId] = { x: e.x, y: e.y };
      }
    }
    return m;
  }, [game.events]);

  const statusLabel = inHalfTimeBreak ? 'HALF TIME' : inSecondHalf ? '2ND HALF' : '1ST HALF';
  const statusColor = inHalfTimeBreak ? 'bg-amber-400 text-stone-100' : 'bg-stone-900 text-lime-400';

  const tacticalBoard = (
    <TacticalBoard
      game={game}
      roster={squadRoster}
      gameGKId={gameGKId}
      onMove={onMovePosition}
      onReset={onResetFormation}
    />
  );

  return (
    <div className="min-h-screen flex flex-col">
      <div className="stripes-bg text-white px-4 pt-[calc(env(safe-area-inset-top,0px)+0.5rem)] pb-2">
        <div className="flex items-center justify-between mb-1">
          {/* Context-aware back: a tap closes an open picker (sub/goal/MINS/
              tag) first — only on the bare game screen does it leave (with a
              confirm via onBack). Stops reflex taps from ejecting mid-game. */}
          <button
            onClick={() => { if (pendingEvent) onCancelEvent(); else onBack(); }}
            className="text-white/70 active:scale-95"
            aria-label={pendingEvent ? 'Close' : 'Leave game'}
          >
            <ChevronLeft className="w-6 h-6" />
          </button>
          <div className="text-center flex-1">
            <div className="text-xs text-white font-bold tracking-wide truncate">
              {game.tournament || gameTypeOf(game).toUpperCase()}
            </div>
            <div className="text-[10px] text-white/50">
              {formatDate(game.date)}
            </div>
          </div>
          <div className="w-6" />
        </div>

        <div className="grid grid-cols-3 items-center gap-3">
          <div className="text-center">
            <div className="text-[10px] font-bold tracking-widest text-lime-400">LASALLE STOMPERS</div>
            <div className="font-display text-5xl leading-none tabular-nums">{game.ourScore}</div>
          </div>
          <div className="flex flex-col items-center gap-1.5">
            <button
              onClick={game.clockRunning ? onPauseClock : onResumeClock}
              className="text-center active:scale-95 transition"
            >
              <div className="text-[10px] font-bold tracking-widest text-white/50">
                {game.clockRunning === false ? '⏸ PAUSED' : '⏱ CLOCK'}
              </div>
              <div className={`font-display text-2xl tabular-nums ${game.clockRunning === false ? 'text-white/50' : 'text-white/90'}`}>
                {formatClock(elapsed)}
              </div>
              <div className="text-[9px] text-white/40">tap to {game.clockRunning ? 'pause' : 'resume'}</div>
            </button>
            {/* Recorder lives in the scorebug (always-mounted header) so it
                survives every event picker — same reason it's not in the
                control row below. */}
            <VoiceRecorder ref={voiceRef} game={game} pendingMicRef={pendingMicRef} />
          </div>
          <div className="text-center">
            <div className="text-[10px] font-bold tracking-widest text-red-400 truncate">{game.opponent || 'OPPONENT'}</div>
            <div className="font-display text-5xl leading-none tabular-nums">{game.oppScore}</div>
          </div>
        </div>
      </div>

      {!pendingEvent && (
        <div className="px-4 pt-2 flex items-center gap-2">
          <div className={`${statusColor} flex-1 rounded-full py-1.5 text-center font-display text-sm tracking-widest shadow`}>
            {statusLabel}
          </div>
          {(() => {
            const gk = roster.find(p => p.id === gameGKId);
            return (
              <button
                onClick={onSwapGK}
                className={`shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 active:scale-95 transition flex items-center gap-1 ${
                  gk
                    ? 'bg-amber-400 text-stone-100 border-amber-500 shadow'
                    : 'bg-red-500/15 text-red-700 border-red-300 animate-pulse'
                }`}
                title="Tap to swap goalie"
              >
                <span>🧤</span>
                <span className="truncate max-w-[80px]">{gk ? `#${gk.number || '?'} ${gk.name.split(' ')[0]}` : 'NO GK'}</span>
              </button>
            );
          })()}
          {!inHalfTimeBreak && (
            <button
              onClick={() => onSelectEvent('__MINS__')}
              className="shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 bg-stone-900 text-stone-300 border-stone-800 active:scale-95 transition flex items-center gap-1"
              title="Live minutes played"
            >
              <span>⏱</span>
              <span>MINS</span>
            </button>
          )}
          {/* Reflex bookmark — one tap stamps the clock; classify it later
              from video in the Film Room confirm queue. Insurance channel
              for moments the coach can't break down live. */}
          {!inHalfTimeBreak && (
            <button
              onClick={() => onSelectEvent('BOOKMARK')}
              className="shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 bg-amber-500/15 text-amber-300 border-amber-600/60 active:scale-95 transition flex items-center gap-1"
              title="Bookmark this moment — classify it later from video"
            >
              <span>🔖</span>
              <span>MARK</span>
            </button>
          )}
        </div>
      )}


      <div className="flex-1 flex flex-col px-4 pt-2">
        {pendingEvent?.type === 'MINS_VIEW' ? (() => {
          const onField = onFieldAt(game);
          const rows = [...playersSorted].sort((a, b) => (secondsByPlayer[b.id] || 0) - (secondsByPlayer[a.id] || 0));
          const maxSec = Math.max(1, ...rows.map(p => secondsByPlayer[p.id] || 0));
          return (
            <div className="flex flex-col h-full min-h-0">
              <div className="flex items-center justify-between mb-3 shrink-0">
                <div>
                  <div className="text-xs text-stone-400 font-bold tracking-widest">LIVE</div>
                  <div className="font-display text-3xl flex items-center gap-2"><span>⏱</span><span>MINUTES</span></div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto pb-6 space-y-1.5">
                {rows.map(p => {
                  const sec = secondsByPlayer[p.id] || 0;
                  const min = Math.round(sec / 60);
                  const isOn = onField.has(p.id);
                  const isGK = p.id === gameGKId;
                  const pct = Math.round((sec / maxSec) * 100);
                  return (
                    <div key={p.id} className={`relative rounded-xl border-2 p-2.5 flex items-center gap-3 ${
                      isGK ? 'bg-amber-500/10 border-amber-300' : isOn ? 'bg-lime-500/10 border-lime-300' : 'bg-stone-900 border-stone-800'
                    }`}>
                      <div className="absolute inset-y-0 left-0 rounded-xl opacity-30" style={{ width: `${pct}%`, background: isGK ? '#fbbf24' : isOn ? '#a3e635' : '#e7e5e4' }} />
                      <div className="relative z-10 flex items-center gap-3 w-full">
                        <PlayerAvatar player={p} sizeClass="w-10 h-10" textSize="text-lg" numberClasses={isGK ? 'bg-amber-500 text-stone-100' : isOn ? 'bg-stone-900 text-lime-400' : 'bg-stone-800 text-stone-400'} />
                        <div className="flex-1 min-w-0">
                          <div className="font-bold text-sm truncate">{p.name}</div>
                          <div className="text-[10px] font-bold tracking-wider text-stone-400">
                            {isGK ? '🧤 IN GOAL' : isOn ? 'ON FIELD' : 'BENCH'}{p.position ? ` · ${p.position}` : ''}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="font-display text-2xl tabular-nums leading-none">{min}</div>
                          <div className="text-[9px] font-bold tracking-wider text-stone-400">MIN</div>
                        </div>
                      </div>
                    </div>
                  );
                })}
                {rows.length === 0 && (
                  <div className="bg-stone-900 border border-stone-800 rounded-xl p-6 text-center text-stone-400 text-sm">
                    No squad players.
                  </div>
                )}
              </div>
            </div>
          );
        })() : pendingEvent?.type === 'NEW_GK' ? (() => {
          const onField = onFieldAt(game);
          // Allow picking the new goalie from the entire match-day squad —
          // a fresh keeper coming off the bench is the common case.
          const candidates = playersSorted;
          const currentGKPlayer = roster.find(p => p.id === gameGKId);
          return (
            <div className="flex flex-col h-full min-h-0">
              <div className="flex items-center justify-between mb-3 shrink-0">
                <div>
                  <div className="text-xs text-stone-400 font-bold tracking-widest">NEW GOALIE</div>
                  <div className="font-display text-3xl flex items-center gap-2">
                    <span>🧤</span>
                    <span>WHO'S IN GOAL?</span>
                  </div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
                  <X className="w-5 h-5" />
                </button>
              </div>
              {currentGKPlayer && (
                <div className="mb-3 bg-stone-900 border border-stone-800 rounded-xl px-3 py-2 text-xs text-stone-300">
                  Current GK: <span className="font-bold text-stone-100">{currentGKPlayer.name} #{currentGKPlayer.number}</span>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2.5 pb-6 overflow-y-auto">
                {candidates.length === 0 && (
                  <div className="col-span-2 bg-stone-900 border border-stone-800 rounded-xl p-6 text-center text-stone-400 text-sm">
                    No players on the field.
                  </div>
                )}
                {candidates.map(p => {
                  const isDefault = p.id === pendingEvent.defaultGK;
                  const isCurrent = p.id === gameGKId;
                  const isOn = onField.has(p.id);
                  return (
                    <button
                      key={p.id}
                      onClick={() => onConfirmGK(p.id)}
                      className={`relative rounded-xl p-3 flex items-center gap-3 active:scale-[0.97] transition text-left border-2 ${
                        isDefault ? 'bg-amber-100 border-amber-500' : 'bg-stone-900 border-stone-800 hover:border-amber-400'
                      }`}
                    >
                      {isCurrent && (
                        <div className="absolute -top-2 -right-2 bg-stone-700 text-white text-[9px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full shadow-sm">CURRENT</div>
                      )}
                      <PlayerAvatar player={p} numberClasses="bg-amber-500 text-stone-100" />
                      <div className="min-w-0 flex-1">
                        <div className="font-bold text-sm truncate">{p.name}</div>
                        <div className="text-[10px] text-amber-700 font-bold tracking-wider">
                          {isDefault ? 'SUGGESTED' : (isOn ? 'ON FIELD' : 'BENCH')}{p.position === 'GK' ? ' · DEFAULT GK' : ''}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
              <button
                onClick={() => onConfirmGK(null)}
                className="mt-1 w-full bg-stone-900 text-stone-300 border border-stone-700 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
              >
                NO GOALIE (empty net)
              </button>
            </div>
          );
        })() : pendingEvent?.type === 'OPP_GOAL_FAULT' ? (() => {
          const onField = onFieldAt(game);
          const gkPlayer = roster.find(p => p.id === gameGKId);
          const gkOnField = gkPlayer && onField.has(gkPlayer.id) ? [gkPlayer] : [];
          return (
            <div className="flex flex-col h-full min-h-0">
              <div className="flex items-center justify-between mb-4 shrink-0">
                <div>
                  <div className="text-xs text-stone-400 font-bold tracking-widest">OPP GOAL — GK FAULT?</div>
                  <div className="font-display text-3xl flex items-center gap-2">
                    <span>🚨</span>
                    <span>WHY?</span>
                  </div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {gkOnField.length > 0 && (
                <div className="mb-3 bg-amber-500/10 border border-amber-300 rounded-xl px-3 py-2 flex items-center gap-2">
                  <span className="text-lg">🧤</span>
                  <div className="text-xs text-amber-800">
                    <span className="font-bold tracking-wider">GK ON FIELD:</span>{' '}
                    {gkOnField.map(p => `${p.name} #${p.number}`).join(', ')}
                  </div>
                </div>
              )}
              {gkOnField.length === 0 && (
                <div className="mb-3 bg-stone-900 border border-stone-800 rounded-xl px-3 py-2 text-xs text-stone-300">
                  No goalie set for this match. Pick a tag anyway — it'll be saved for record.
                </div>
              )}

              <button
                onClick={() => onResolveOppGoal('gk')}
                className="mb-2 w-full bg-red-500/15 text-red-300 border-2 border-red-400 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
              >
                <span className="text-3xl">🧤</span>
                <span>GK FAULT</span>
              </button>
              <button
                onClick={() => onResolveOppGoal('unstoppable')}
                className="mb-2 w-full bg-stone-900 text-stone-100 border-2 border-stone-400 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
              >
                <span className="text-3xl">😮</span>
                <span>UNSTOPPABLE</span>
              </button>
              <button
                onClick={() => onResolveOppGoal(null)}
                className="w-full bg-stone-900 text-stone-300 border border-stone-700 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
              >
                NEUTRAL / UNSURE
              </button>
              <button
                onClick={() => onResolveOppGoal('own')}
                className="mt-3 w-full bg-amber-900/30 text-amber-200 border border-amber-600/60 font-display text-base py-3 rounded-xl active:scale-[0.98] transition flex items-center justify-center gap-2"
              >
                <span className="text-lg">🙃</span>
                <span>OWN GOAL — PICK PLAYER</span>
              </button>
            </div>
          );
        })() : pendingEvent?.type === 'OPP_GOAL_OWN_PICK' ? (() => {
          const onField = onFieldAt(game);
          const ourPlayers = playersSorted.filter(p => onField.has(p.id)).sort((a, b) => {
            // GK last for own goals (rare); outfield by number
            const ag = a.id === gameGKId ? 1 : 0;
            const bg = b.id === gameGKId ? 1 : 0;
            if (ag !== bg) return ag - bg;
            return (parseInt(a.number) || 0) - (parseInt(b.number) || 0);
          });
          return (
            <PlayerPicker
              event={{ emoji: '🙃', label: 'OWN GOAL — WHO?', requiresPlayer: true }}
              players={ourPlayers}
              gameGKId={gameGKId}
              skippable={false}
              onPick={onSelectPlayer}
              onSkip={onCancelEvent}
              onUnknown={null}
              onCancel={onCancelEvent}
              emptyMessage="No players on the field."
            />
          );
        })() : pendingEvent ? (() => {
          const isSub = pendingEvent.type === 'SUB';
          const isGGPartner = pendingEvent.type === 'GIVE_GO_PARTNER';
          let pickerEvent, pickerPlayers, pickerSkippable, pickerOnUnknown;
          // GK floats to top so the keeper is instantly visible
          const gkFirst = (a, b) => {
            const ag = a.id === gameGKId ? 0 : 1;
            const bg = b.id === gameGKId ? 0 : 1;
            if (ag !== bg) return ag - bg;
            return (parseInt(a.number) || 0) - (parseInt(b.number) || 0);
          };
          if (isSub) {
            const onField = onFieldAt(game);
            if (pendingEvent.step === 'OFF') {
              pickerEvent = { emoji: '🔄', label: 'WHO\'S OFF?', requiresPlayer: true };
              pickerPlayers = playersSorted.filter(p => onField.has(p.id)).sort(gkFirst);
            } else {
              pickerEvent = { emoji: '🔄', label: 'WHO\'S ON?', requiresPlayer: true };
              pickerPlayers = playersSorted.filter(p => !onField.has(p.id) && p.id !== pendingEvent.offPlayerId).sort(gkFirst);
            }
            pickerSkippable = false;
            pickerOnUnknown = null;
          } else if (isGGPartner) {
            // Step 2 of give & go: pick the wall-pass partner (or skip).
            pickerEvent = { emoji: '🤝', label: 'WALL PASS PARTNER?', requiresPlayer: true };
            const onField = onFieldAt(game);
            pickerPlayers = playersSorted
              .filter(p => onField.has(p.id) && p.id !== pendingEvent.initiatorId)
              .sort(gkFirst);
            pickerSkippable = true;
            pickerOnUnknown = () => onSelectPlayer(null);
          } else {
            pickerEvent = EVENT_TYPES[pendingEvent.type];
            const onField = onFieldAt(game);
            pickerPlayers = playersSorted
              .filter(p => onField.has(p.id) && p.id !== pendingEvent.excludePlayerId)
              .sort(gkFirst);
            pickerSkippable = pendingEvent.skippable;
            pickerOnUnknown = pendingEvent.skippable ? () => onSelectPlayer(null) : null;
          }
          return (
            <PlayerPicker
              event={pickerEvent}
              players={pickerPlayers}
              gameGKId={gameGKId}
              secondsByPlayer={isSub ? secondsByPlayer : null}
              skippable={pickerSkippable}
              onPick={onSelectPlayer}
              onSkip={onCancelEvent}
              onUnknown={pickerOnUnknown}
              onCancel={onCancelEvent}
              emptyMessage={isSub && pickerPlayers.length === 0
                ? (pendingEvent.step === 'OFF' ? 'No one is on the field.' : 'Everyone is already on the field.')
                : (isGGPartner && pickerPlayers.length === 0 ? 'No teammates available — tap SKIP.' : (!isSub && !isGGPartner && pickerPlayers.length === 0 ? 'No players on the field.' : null))}
            />
          );
        })() : inHalfTimeBreak ? (
          <div className="flex flex-col items-center text-center pt-6">
            <div className="text-6xl mb-3">⏸️</div>
            <div className="font-display text-4xl mb-2">HALF TIME</div>
            <div className="text-stone-400 text-sm mb-6 max-w-xs">
              Clock is paused. Make any halftime subs or GK swap, then tap to kick off the 2nd half.
            </div>
            <button
              onClick={onStartSecondHalf}
              className="w-full max-w-sm bg-lime-500 text-stone-100 font-display text-2xl py-5 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.98] transition flex items-center justify-center gap-2"
            >
              <span>▶</span>
              <span>START 2ND HALF</span>
            </button>
            <button
              onClick={onResumeFirstHalf}
              className="mt-3 text-stone-400 font-bold text-sm active:scale-95"
            >
              ← Back to 1st half
            </button>

            {/* Halftime substitutions — same SUB / SWAP GK flow as live play. */}
            <div className="mt-6 w-full max-w-sm grid grid-cols-2 gap-2">
              <button
                onClick={() => onSelectEvent('SUB')}
                className={`${TONE_CLASSES['purple']} border-2 rounded-2xl py-3 flex items-center justify-center gap-2 active:scale-[0.97] transition`}
              >
                <span className="text-2xl">🔄</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SUBSTITUTION</span>
              </button>
              <button
                onClick={onSwapGK}
                className="bg-amber-900/50 text-amber-200 border-2 border-amber-600/60 rounded-2xl py-3 flex items-center justify-center gap-2 active:scale-[0.97] transition"
              >
                <span className="text-2xl">🧤</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SWAP GK</span>
              </button>
            </div>

            {/* Rotate several players at once, keeping the shape. */}
            <button
              onClick={() => setMultiSubOpen(true)}
              className="mt-2 w-full max-w-sm bg-purple-950/60 text-purple-100 border-2 border-purple-600/60 rounded-2xl py-3 flex items-center justify-center gap-2 active:scale-[0.97] transition"
            >
              <span className="text-2xl">👥</span>
              <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SUB MULTIPLE</span>
            </button>

            {/* Bulk lineup re-pick: faster than chaining individual SUBs when
                many players rotate at halftime. */}
            <button
              onClick={() => setHalftimePicker(true)}
              className="mt-2 w-full max-w-sm bg-sky-900/50 text-sky-200 border-2 border-sky-600/60 rounded-2xl py-3 flex items-center justify-center gap-2 active:scale-[0.97] transition"
            >
              <span className="text-2xl">🔁</span>
              <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">PICK 2ND-HALF LINEUP</span>
            </button>

            <div className="w-full">
              {tacticalBoard}
            </div>

            <div className="mt-5 w-full">
              <h3 className="font-display text-lg mb-2 text-left">RECENT</h3>
              <div className="space-y-1.5">
                {recent.length === 0 && (
                  <div className="text-sm text-stone-400 italic text-left">No events yet.</div>
                )}
                {recent.map(e => <EventRow key={e.id} event={e} roster={roster} />)}
              </div>
            </div>
          </div>
        ) : (
          <>
            {/* Positive actions (top zone) — each adds to performance score.
                GOAL is emphasised. ASSIST is captured automatically from the
                GOAL flow, so it has no standalone button. */}
            <div className="rounded-2xl border border-lime-700/40 bg-lime-950/30 p-2">
              <div className="flex items-center justify-between px-1 pb-1.5">
                <div className="flex items-center gap-1.5 text-lime-300 font-display text-xs tracking-widest">
                  <span className="w-5 h-5 rounded-full bg-lime-500 text-stone-950 flex items-center justify-center font-bold text-sm leading-none">+</span>
                  <span>EARN</span>
                </div>
                <div className="text-[10px] text-lime-400/70 font-bold tracking-wider">RAISES SCORE</div>
              </div>
              <div className="grid grid-cols-4 gap-2">
                {['GOAL', 'SHOT_ON', 'SHOT_OFF', 'KEY_PASS', 'GIVE_GO', 'GATES', 'BALL_WIN', 'DUEL_WIN', 'SAVE', 'BLOCK', 'CLEAR', 'KICK_OUT', 'FOUL_ON', 'PEN_AWARDED'].map(id => {
                  const ev = EVENT_TYPES[id];
                  const big = id === 'GOAL';
                  return (
                    <button
                      key={ev.id}
                      onClick={() => onSelectEvent(ev.id)}
                      className={`relative ${TONE_CLASSES[ev.tone]} border-2 rounded-2xl ${big ? 'py-3.5' : 'py-2.5'} flex flex-col items-center justify-center gap-1 active:scale-[0.97] transition`}
                    >
                      <span className="absolute top-1 right-1.5 text-[10px] font-extrabold text-lime-300/80">+</span>
                      <div className={`${big ? 'text-3xl' : 'text-2xl'}`}>{ev.emoji}</div>
                      <div className={`font-sans-pro font-extrabold tracking-tight ${big ? 'text-base' : 'text-xs'} leading-none text-center`}>{ev.label}</div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Negative actions (bottom zone) — each subtracts from performance score. */}
            <div className="mt-2 rounded-2xl border border-red-700/40 bg-red-950/30 p-2">
              <div className="flex items-center justify-between px-1 pb-1.5">
                <div className="flex items-center gap-1.5 text-red-300 font-display text-xs tracking-widest">
                  <span className="w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center font-bold text-sm leading-none">−</span>
                  <span>LOSE</span>
                </div>
                <div className="text-[10px] text-red-400/70 font-bold tracking-wider">LOWERS SCORE</div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {['TURNOVER', 'HOLDS_BALL', 'DUEL_LOSE', 'FOUL_BY', 'PEN_CONCEDED'].map(id => {
                  const ev = EVENT_TYPES[id];
                  return (
                    <button
                      key={ev.id}
                      onClick={() => onSelectEvent(ev.id)}
                      className={`relative ${TONE_CLASSES[ev.tone]} border-2 rounded-2xl py-2.5 flex flex-col items-center justify-center gap-1 active:scale-[0.97] transition`}
                    >
                      <span className="absolute top-1 right-1.5 text-[10px] font-extrabold text-red-300/80">−</span>
                      <div className="text-2xl">{ev.emoji}</div>
                      <div className="font-sans-pro font-extrabold tracking-tight text-xs leading-none text-center">{ev.label}</div>
                    </button>
                  );
                })}
              </div>
              <button
                onClick={() => onSelectEvent('OPP_GOAL')}
                className={`relative mt-2 w-full ${TONE_CLASSES['big-red']} border-2 rounded-2xl py-2.5 flex items-center justify-center gap-3 active:scale-[0.97] transition`}
              >
                <span className="absolute top-1 right-2 text-[10px] font-extrabold text-red-100">−</span>
                <span className="text-2xl">⚽</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-lg leading-none">OPP GOAL</span>
              </button>
            </div>

            {/* Penalties — converted pens log as GOAL/OPP_GOAL + {penalty:true}
                (so the scoreboard/stats/reel see a normal goal); the misses are
                their own no-delta events. Outcome buttons are direct (no PEN-WON
                two-step), per the coach. */}
            <div className="mt-2 rounded-2xl border border-stone-600/50 bg-stone-900/40 p-2">
              <div className="flex items-center justify-between px-1 pb-1.5">
                <div className="flex items-center gap-1.5 text-stone-200 font-display text-xs tracking-widest">
                  <span className="text-base leading-none">⚪</span>
                  <span>PENALTIES</span>
                </div>
                <div className="text-[10px] text-stone-400 font-bold tracking-wider">SHOOTOUT / SPOT-KICK</div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { intent: 'PEN_GOAL_US',    tone: 'big-green', emoji: '⚽', label: 'PEN GOAL' },
                  { intent: 'PEN_MISSED',     tone: 'soft-red',  emoji: '🚫', label: 'PEN MISS' },
                  { intent: 'PEN_GOAL_OPP',   tone: 'big-red',   emoji: '⚽', label: 'OPP PEN' },
                  { intent: 'OPP_PEN_MISSED', tone: 'blue',      emoji: '🧤', label: 'PEN SAVED' },
                ].map(b => (
                  <button
                    key={b.intent}
                    onClick={() => onSelectEvent(b.intent)}
                    className={`${TONE_CLASSES[b.tone]} border-2 rounded-2xl py-2.5 flex items-center justify-center gap-2 active:scale-[0.97] transition`}
                  >
                    <span className="text-2xl">{b.emoji}</span>
                    <span className="font-sans-pro font-extrabold tracking-tight text-sm leading-none">{b.label}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 mt-2">
              <button
                onClick={() => onSelectEvent('SUB')}
                className={`${TONE_CLASSES['purple']} border-2 rounded-2xl py-2.5 flex items-center justify-center gap-2 active:scale-[0.97] transition`}
              >
                <span className="text-2xl">🔄</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SUBSTITUTION</span>
              </button>
              <button
                onClick={onSwapGK}
                className="bg-amber-900/50 text-amber-200 border-2 border-amber-600/60 rounded-2xl py-2.5 flex items-center justify-center gap-2 active:scale-[0.97] transition"
              >
                <span className="text-2xl">🧤</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SWAP GK</span>
              </button>
            </div>

            {/* Rotate several players at once instead of chaining single SUBs. */}
            <button
              onClick={() => setMultiSubOpen(true)}
              className="mt-2 w-full bg-purple-950/60 text-purple-100 border-2 border-purple-600/60 rounded-2xl py-2.5 flex items-center justify-center gap-2 active:scale-[0.97] transition"
            >
              <span className="text-2xl">👥</span>
              <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SUB MULTIPLE</span>
            </button>

            {inFirstHalf ? (
              <button
                onClick={onPauseHalfTime}
                className="mt-2 w-full bg-amber-500 text-stone-950 font-display text-xl py-3 rounded-2xl active:scale-[0.99] transition border-2 border-amber-400 shadow"
              >
                ⏸ HALF TIME
              </button>
            ) : (
              <button
                onClick={onEnd}
                className="mt-2 w-full bg-stone-900 text-white font-display text-xl py-3 rounded-2xl active:scale-[0.99] transition border-2 border-stone-700"
              >
                FINAL WHISTLE
              </button>
            )}

            {/* POSITION-staleness nudge: the board is identity's main prior
                AND the formation source — surface drift before it costs data. */}
            {(() => {
              if (game.clockRunning === false || elapsed < 180) return null;
              const placed = new Set((game.events || [])
                .filter(e => e.type === 'POSITION' && (e.period || 1) === game.period)
                .map(e => e.playerId));
              const missing = [...onFieldAt(game)].filter(pid => !placed.has(pid) && pid !== gameGKId);
              if (missing.length === 0) return null;
              return (
                <div className="mt-2 bg-amber-500/10 border border-amber-600/50 rounded-xl px-3 py-2 text-[11px] text-amber-300 leading-snug">
                  🧭 {missing.length} on-field player{missing.length === 1 ? '' : 's'} not placed on the board this half — drag them (or tap RESET) so tracking knows who's where.
                </div>
              );
            })()}

            {tacticalBoard}

            <div className="mt-5 flex-1 min-h-0">
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-display text-lg">RECENT</h3>
                {game.events.length > 0 && (
                  <button
                    onClick={onUndo}
                    className="text-xs font-bold text-stone-400 flex items-center gap-1 active:scale-95 bg-stone-900 px-3 py-1.5 rounded-full"
                  >
                    <Undo2 className="w-3.5 h-3.5" /> UNDO
                  </button>
                )}
              </div>
              <div className="space-y-1.5">
                {recent.length === 0 && (
                  <div className="text-sm text-stone-400 italic">No events yet — tap an action above when something happens.</div>
                )}
                {recent.map(e => <EventRow key={e.id} event={e} roster={roster} />)}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="pb-6"></div>
      {halftimePicker && (
        <HalftimeLineupPicker
          players={playersSorted}
          gameGKId={gameGKId}
          initialOnField={onFieldAt(game)}
          secondsByPlayer={secondsByPlayer}
          onCancel={() => setHalftimePicker(false)}
          onSave={(ids) => {
            onBulkReplaceLineup(ids);
            setHalftimePicker(false);
          }}
        />
      )}
      {multiSubOpen && (
        <MultiSubPicker
          players={playersSorted}
          onFieldIds={[...onFieldAt(game)]}
          gameGKId={gameGKId}
          secondsByPlayer={secondsByPlayer}
          bandHist={bandHist}
          currentPositions={boardPositions}
          onCancel={() => setMultiSubOpen(false)}
          onCommit={(ids, pairing) => {
            onBulkReplaceLineup(ids, pairing);
            setMultiSubOpen(false);
          }}
        />
      )}
    </div>
  );
}

function HalftimeLineupPicker({ players, gameGKId, initialOnField, secondsByPlayer, onCancel, onSave }) {
  const [selected, setSelected] = useState(() => new Set(initialOnField));
  const toggle = (pid) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid); else next.add(pid);
      return next;
    });
  };
  const onCount = selected.size;
  return (
    <div className="fixed inset-0 z-50 bg-stone-950/95 flex flex-col">
      <div className="px-4 pt-[calc(env(safe-area-inset-top,0px)+0.75rem)] pb-3 border-b border-stone-800 flex items-center justify-between">
        <div>
          <div className="font-display text-xl text-white leading-none">2ND-HALF LINEUP</div>
          <div className="text-xs text-stone-400 mt-1">Tap to toggle. Selected go on field at the whistle.</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-display text-lime-400 leading-none">{onCount}</div>
          <div className="text-[10px] text-stone-500 uppercase tracking-wide">on field</div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="grid grid-cols-2 gap-2">
          {players.map(p => {
            const isOn = selected.has(p.id);
            const isGK = p.id === gameGKId;
            const secs = secondsByPlayer?.[p.id] || 0;
            const mins = Math.floor(secs / 60);
            return (
              <button
                key={p.id}
                onClick={() => toggle(p.id)}
                className={`rounded-2xl border-2 px-3 py-3 flex flex-col items-start gap-1 active:scale-[0.97] transition text-left ${
                  isOn
                    ? 'bg-lime-900/40 border-lime-500/70 text-lime-100'
                    : 'bg-stone-900/60 border-stone-700 text-stone-400'
                }`}
              >
                <div className="flex items-center gap-2 w-full">
                  <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${isOn ? 'bg-lime-700/60 text-white' : 'bg-stone-800 text-stone-400'}`}>#{p.number}</span>
                  {isGK && <span className="text-xs px-1.5 py-0.5 rounded bg-amber-700/60 text-amber-100">GK</span>}
                  <span className={`ml-auto text-[10px] font-mono ${isOn ? 'text-lime-300' : 'text-stone-500'}`}>{mins}'</span>
                </div>
                <div className="font-sans-pro font-extrabold text-sm leading-tight">{p.name}</div>
              </button>
            );
          })}
        </div>
      </div>
      <div className="px-4 py-3 pb-[calc(env(safe-area-inset-bottom,0px)+0.75rem)] border-t border-stone-800 flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 bg-stone-800 text-stone-200 border-2 border-stone-700 rounded-2xl py-3 font-display"
        >
          CANCEL
        </button>
        <button
          onClick={() => onSave([...selected])}
          className="flex-1 bg-lime-700 text-white border-2 border-lime-500 rounded-2xl py-3 font-display active:scale-[0.97] transition"
        >
          SAVE LINEUP
        </button>
      </div>
    </div>
  );
}

/* ---- Multi-player substitution -------------------------------------------
 * Opens with every outfielder marked to come OFF and the keeper kept on, so
 * "sub everyone except the goalie" is zero taps. Tap a row to keep that player
 * on instead. Incoming players are matched to the slots being vacated by
 * position history (band-level; see SLOT_BAND for why not exact slots).
 *
 * Commits immediately — the coach fixes the shape on the tactical board, which
 * is why the incoming players arrive with a real board position rather than
 * unplaced. Blocked while more players are going off than the bench can cover,
 * with an explicit PLAY SHORT override for injuries / send-offs.
 */
function MultiSubPicker({
  players, onFieldIds, gameGKId, secondsByPlayer, bandHist, currentPositions, onCancel, onCommit,
}) {
  const onFieldSet = useMemo(() => new Set(onFieldIds), [onFieldIds]);
  const onFieldPlayers = players.filter(p => onFieldSet.has(p.id));
  const benchPlayers = players.filter(p => !onFieldSet.has(p.id));

  // Everyone on the field is going off unless kept. The keeper starts kept —
  // the coach subs them separately when needed.
  const [keptIds, setKeptIds] = useState(
    () => new Set(onFieldPlayers.filter(p => p.id === gameGKId).map(p => p.id))
  );
  const toggleKeep = (pid) => setKeptIds(prev => {
    const next = new Set(prev);
    if (next.has(pid)) next.delete(pid); else next.add(pid);
    return next;
  });

  const offPlayers = onFieldPlayers.filter(p => !keptIds.has(p.id));
  const nOff = offPlayers.length;
  const nBench = benchPlayers.length;
  const short = nOff > nBench;

  // One vacated slot per outgoing player: the slot they currently occupy is
  // what the incoming player inherits (nearest named slot for a hand-dragged
  // spot). Two players can legitimately sit on the same named slot when the
  // coach dragged them together, so each entry gets a unique `key` — keying by
  // slot name alone would collapse the duplicates and silently leave a bench
  // player on the bench.
  const vacatedSlots = offPlayers.map((p, i) => {
    const cur = currentPositions[p.id];
    const name = cur ? nearestSlotKey(cur.x, cur.y) : 'CM';
    const s = POSITION_SLOTS.find(t => t.key === name)
      || POSITION_SLOTS.find(t => t.key === 'CM');
    return { key: `${s.key}#${i}`, slotKey: s.key, x: s.x, y: s.y, offId: p.id };
  });

  const assignment = useMemo(
    () => assignIncomingToSlots(vacatedSlots, benchPlayers.map(p => p.id), bandHist, secondsByPlayer),
    [vacatedSlots.map(s => s.key).join(','), benchPlayers.map(p => p.id).join(','), bandHist, secondsByPlayer]
  );

  const nameOf = (pid) => players.find(p => p.id === pid)?.name || '?';
  const bandLabel = { D: 'DEF', M: 'MID', F: 'FWD' };
  const mins = (pid) => Math.floor((secondsByPlayer?.[pid] || 0) / 60);

  const commit = () => {
    // Each vacated slot carries its own outgoing player, so pair straight off
    // that. Slots nobody was left to fill become off-only rows (playing short).
    const byKey = new Map(assignment.pairs.map(p => [p.slot.key, p.playerId]));
    const pairing = vacatedSlots.map(s => ({
      offId: s.offId,
      onId: byKey.get(s.key) || null,
      x: s.x,
      y: s.y,
    }));
    const nextOnField = [
      ...onFieldPlayers.filter(p => keptIds.has(p.id)).map(p => p.id),
      ...pairing.filter(r => r.onId).map(r => r.onId),
    ];
    onCommit(nextOnField, pairing);
  };

  return (
    <div className="fixed inset-0 z-50 bg-stone-950/95 flex flex-col">
      <div className="px-4 pt-[calc(env(safe-area-inset-top,0px)+0.75rem)] pb-3 border-b border-stone-800 flex items-center justify-between">
        <div>
          <div className="font-display text-xl text-white leading-none">SUB MULTIPLE</div>
          <div className="text-xs text-stone-400 mt-1">Tap a player to KEEP them on. The rest come off.</div>
        </div>
        <div className="text-right">
          <div className={`text-2xl font-display leading-none ${short ? 'text-red-400' : 'text-lime-400'}`}>{nOff}</div>
          <div className="text-[10px] text-stone-500 uppercase tracking-wide">going off</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="text-[11px] font-bold text-stone-500 uppercase tracking-wider px-1 mb-1.5">On field</div>
        <div className="grid grid-cols-2 gap-2">
          {onFieldPlayers.map(p => {
            const kept = keptIds.has(p.id);
            const isGK = p.id === gameGKId;
            return (
              <button
                key={p.id}
                onClick={() => toggleKeep(p.id)}
                className={`rounded-2xl border-2 px-3 py-3 flex flex-col items-start gap-1 active:scale-[0.97] transition text-left ${
                  kept
                    ? 'bg-lime-900/40 border-lime-500/70 text-lime-100'
                    : 'bg-purple-950/50 border-purple-600/60 text-purple-100'
                }`}
              >
                <div className="flex items-center gap-2 w-full">
                  <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${kept ? 'bg-lime-700/60 text-white' : 'bg-purple-800/70 text-purple-100'}`}>#{p.number}</span>
                  {isGK && <span className="text-xs px-1.5 py-0.5 rounded bg-amber-700/60 text-amber-100">GK</span>}
                  <span className="ml-auto text-[10px] font-mono text-stone-400">{mins(p.id)}'</span>
                </div>
                <div className="font-sans-pro font-extrabold text-sm leading-tight">{p.name}</div>
                <div className={`text-[10px] font-bold tracking-wide ${kept ? 'text-lime-300' : 'text-purple-300'}`}>
                  {kept ? 'STAYING ON' : '→ COMING OFF'}
                </div>
              </button>
            );
          })}
        </div>

        {/* Who comes on, and where they'll land. Read-only: the board is where
            the coach adjusts, per the agreed commit-immediately flow. */}
        <div className="text-[11px] font-bold text-stone-500 uppercase tracking-wider px-1 mt-4 mb-1.5">
          Coming on {nOff > 0 && `· ${assignment.pairs.length} of ${nBench} on bench`}
        </div>
        {nOff === 0 ? (
          <div className="text-sm text-stone-500 italic px-1">Nobody selected to come off.</div>
        ) : (
          <div className="space-y-1.5">
            {offPlayers.map((p, i) => {
              const slot = vacatedSlots[i];
              const inId = assignment.pairs.find(x => x.slot.key === slot.key)?.playerId;
              const band = SLOT_BAND[slot.slotKey];
              const aff = inId && band ? bandAffinity(bandHist, inId, band) : 0;
              return (
                <div key={p.id} className="flex items-center gap-2 rounded-xl bg-stone-900/60 border border-stone-700 px-3 py-2">
                  <span className="text-xs font-mono text-stone-500 w-9">{slot.slotKey}</span>
                  <span className="text-sm text-stone-400 truncate flex-1">{p.name.split(' ')[0]}</span>
                  <span className="text-stone-600">→</span>
                  <span className="text-sm font-bold text-lime-200 truncate flex-1">
                    {inId ? nameOf(inId).split(' ')[0] : <span className="text-red-400 font-normal italic">nobody</span>}
                  </span>
                  {inId && (
                    <span className="text-[10px] font-mono text-stone-500">
                      {band ? bandLabel[band] : ''} {Math.round(100 * aff)}%
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <div className="text-[10px] text-stone-600 mt-2 px-1 leading-snug">
          Positions come from each player's history on the board. Drag on the pitch to adjust after.
        </div>
      </div>

      <div className="px-4 py-3 pb-[calc(env(safe-area-inset-bottom,0px)+0.75rem)] border-t border-stone-800">
        {short && (
          <div className="text-[11px] text-red-300 text-center mb-2 leading-snug">
            {nOff} going off but only {nBench} on the bench — keep {nOff - nBench} more on,
            or play short.
          </div>
        )}
        <div className="flex gap-2">
          <button
            onClick={onCancel}
            className="flex-1 bg-stone-800 text-stone-200 border-2 border-stone-700 rounded-2xl py-3 font-display"
          >
            CANCEL
          </button>
          <button
            onClick={commit}
            disabled={nOff === 0 || short}
            className={`flex-1 rounded-2xl py-3 font-display border-2 transition ${
              nOff === 0 || short
                ? 'bg-stone-800/60 text-stone-600 border-stone-800 cursor-not-allowed'
                : 'bg-lime-700 text-white border-lime-500 active:scale-[0.97]'
            }`}
          >
            SUB {nOff} PLAYER{nOff === 1 ? '' : 'S'}
          </button>
        </div>
        {short && (
          <ShortHandedButton count={nOff} available={nBench} onConfirm={commit} />
        )}
      </div>
    </div>
  );
}

/* Two-tap override for deliberately continuing short-handed (injury, send-off).
 * Separate from the main commit so it can never be hit by accident. */
function ShortHandedButton({ count, available, onConfirm }) {
  const [armed, setArmed] = useState(false);
  return (
    <button
      onClick={() => (armed ? onConfirm() : setArmed(true))}
      className={`mt-2 w-full rounded-2xl py-2.5 font-display text-sm border-2 transition active:scale-[0.98] ${
        armed
          ? 'bg-red-700 text-white border-red-400'
          : 'bg-stone-900 text-red-300 border-red-800/70'
      }`}
    >
      {armed
        ? `TAP AGAIN — PLAY SHORT (${count - available} fewer on field)`
        : 'PLAY SHORT'}
    </button>
  );
}

function PlayerPicker({ event, players, gameGKId, secondsByPlayer, skippable, onPick, onSkip, onUnknown, onCancel, emptyMessage }) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <div className="text-xs text-stone-400 font-bold tracking-widest">WHO?</div>
          <div className="font-display text-3xl flex items-center gap-2">
            <span>{event.emoji}</span>
            <span>{event.label}</span>
          </div>
        </div>
        <button onClick={onCancel} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
          <X className="w-5 h-5" />
        </button>
      </div>

      {skippable && onUnknown && (
        <button
          onClick={onUnknown}
          className="mb-2 w-full bg-stone-900 text-lime-400 font-display text-xl py-4 rounded-xl border-2 border-stone-900 active:scale-[0.98] transition flex items-center justify-center gap-2"
        >
          <span>❓</span>
          <span>UNKNOWN PLAYER</span>
        </button>
      )}

      {skippable && (
        <button
          onClick={onSkip}
          className="mb-3 w-full bg-stone-950 text-stone-300 font-display text-sm py-2.5 rounded-xl border border-stone-800 active:scale-[0.98] transition"
        >
          NO {event.label}
        </button>
      )}

      <div className="grid grid-cols-2 gap-2.5 pb-6 overflow-y-auto">
        {players.length === 0 && emptyMessage && (
          <div className="col-span-2 bg-stone-900 border border-stone-800 rounded-xl p-6 text-center text-stone-400 text-sm">
            {emptyMessage}
          </div>
        )}
        {players.map(p => {
          // In-game: highlight the actual match GK. Outside a game (no gameGKId),
          // fall back to the player's roster position.
          const isGK = gameGKId ? (p.id === gameGKId) : (p.position === 'GK');
          return (
            <button
              key={p.id}
              onClick={() => onPick(p.id)}
              className={`relative rounded-xl p-3 flex items-center gap-3 active:scale-[0.97] transition text-left border-2 ${
                isGK
                  ? 'bg-amber-500/10 border-amber-400 hover:border-amber-500'
                  : 'bg-stone-900 border-stone-800 hover:border-stone-900'
              }`}
            >
              {isGK && (
                <div className="absolute -top-2 -right-2 bg-amber-400 text-stone-100 text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full border border-amber-600 shadow-sm flex items-center gap-0.5">
                  <span>🧤</span><span>GK</span>
                </div>
              )}
              <PlayerAvatar
                player={p}
                numberClasses={isGK ? 'bg-amber-500 text-stone-100' : 'bg-stone-900 text-lime-400'}
              />
              <div className="min-w-0 flex-1">
                <div className="font-bold text-sm truncate">{p.name}</div>
                {secondsByPlayer ? (
                  <div className={`text-[10px] font-bold tracking-wider ${isGK ? 'text-amber-700' : 'text-stone-400'}`}>
                    {Math.round((secondsByPlayer[p.id] || 0) / 60)} min{p.position ? ` · ${p.position}` : ''}
                  </div>
                ) : (p.position && (
                  <div className={`text-[10px] font-bold tracking-wider ${isGK ? 'text-amber-700' : 'text-stone-400'}`}>
                    {p.position}
                  </div>
                ))}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- YOUTUBE EMBED ---------- */
function YouTubeEmbed({ videoId, live = false, interactive = false, fill = false }) {
  // Sanitize videoId
  let id = videoId || '';
  if (id.includes('youtube.com') || id.includes('youtu.be')) {
    try {
      const u = new URL(id.includes('http') ? id : `https://${id}`);
      id = u.searchParams.get('v') || u.pathname.split('/').pop() || id;
    } catch (e) {}
  }
  id = id.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 20);
  if (!id) return null;

  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  // Interactive mode (on-demand training playback): normal YouTube controls,
  // keyboard, fullscreen, and no click-blocking overlay. Default (live scorebug)
  // stays locked-down and overlay-blocked so taps can't pause/share the stream.
  const params = interactive
    ? [
        'autoplay=1', 'rel=0', 'modestbranding=1', 'playsinline=1',
        'iv_load_policy=3',
        origin ? `origin=${encodeURIComponent(origin)}` : '',
      ].filter(Boolean).join('&')
    : [
        'autoplay=1', 'mute=1', 'rel=0', 'modestbranding=1', 'playsinline=1',
        'controls=0', 'disablekb=1', 'iv_load_policy=3', 'fs=0',
        'showinfo=0', 'cc_load_policy=0',
        live ? 'live=1' : '',
        origin ? `origin=${encodeURIComponent(origin)}` : '',
      ].filter(Boolean).join('&');
  const src = `https://www.youtube-nocookie.com/embed/${id}?${params}`;

  // `fill` fills the parent (caller controls aspect, e.g. a 9:16 portrait
  // shorts frame); default keeps the 16:9 box for inline/landscape use.
  return (
    <div className={fill ? 'relative w-full h-full bg-black' : 'relative w-full aspect-video rounded-xl overflow-hidden bg-black'}>
      <iframe
        src={src}
        className="absolute inset-0 w-full h-full"
        frameBorder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
        referrerPolicy="strict-origin-when-cross-origin"
        title={interactive ? 'Training video' : 'Match stream'}
      />
      {/* Click-blocking overlay — prevents accidental taps on YT pause/share/title.
          Omitted in interactive mode so users can scrub/fullscreen. */}
      {!interactive && <div className="absolute inset-0" aria-hidden="true" />}
    </div>
  );
}

/* ---------- 360° VIDEO PLAYER ---------- */
function loadThreeJS() {
  if (window.THREE) return Promise.resolve(window.THREE);
  if (window._threePromise) return window._threePromise;
  window._threePromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js';
    s.onload = () => resolve(window.THREE);
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return window._threePromise;
}

function loadHlsJS() {
  if (window.Hls) return Promise.resolve(window.Hls);
  if (window._hlsPromise) return window._hlsPromise;
  window._hlsPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js';
    s.onload = () => resolve(window.Hls);
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return window._hlsPromise;
}

// Attach an HLS source (.m3u8) to a <video> element. Uses native HLS on Safari,
// falls back to hls.js on Chrome/Firefox. Returns a cleanup function.
//
// NOTE: The Insta360 X5 (and other 360 cameras) ships multi-channel spatial /
// ambisonic audio inside an AAC container. Chrome's AAC decoder rejects the
// channel layout with PipelineStatus::DECODER_ERROR_NOT_SUPPORTED, which
// breaks the whole pipeline. To stay robust, we use a custom playlist loader
// that strips audio tracks from the master manifest — playing video-only.
// (Match audio doesn't matter for parents 30ft from the field anyway.)
async function attachHls(video, url) {
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    return () => { video.removeAttribute('src'); video.load(); };
  }
  const Hls = await loadHlsJS();
  if (!Hls.isSupported()) {
    video.src = url; // last-ditch fallback
    return () => {};
  }
  class VideoOnlyPLoader extends Hls.DefaultConfig.loader {
    load(context, config, callbacks) {
      const origSuccess = callbacks.onSuccess;
      callbacks.onSuccess = (response, stats, ctx, networkDetails) => {
        if (typeof response.data === 'string' && response.data.indexOf('#EXTM3U') >= 0) {
          response.data = response.data
            .replace(/#EXT-X-MEDIA:TYPE=AUDIO[^\n]*\n/g, '')
            .replace(/,AUDIO="[^"]*"/g, '');
        }
        origSuccess(response, stats, ctx, networkDetails);
      };
      super.load(context, config, callbacks);
    }
  }
  const hls = new Hls({
    // CF Stream's LL-HLS is off by default — keep lowLatencyMode false to
    // avoid hls.js spuriously rebuffering / stalling on standard live HLS.
    lowLatencyMode: false,
    // Treat live streams as infinite so the player keeps polling the manifest
    // for new segments instead of stopping at the current seekable end.
    liveDurationInfinity: true,
    backBufferLength: 30,
    pLoader: VideoOnlyPLoader,
  });
  hls.loadSource(url);
  hls.attachMedia(video);
  // Auto-recover from buffer stalls common on live streams when the player
  // catches up to the live edge faster than new segments arrive.
  hls.on(Hls.Events.ERROR, (_evt, data) => {
    if (!data || !data.fatal) return;
    try {
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
      else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
    } catch (e) {}
  });
  return () => { try { hls.destroy(); } catch {} };
}

function VideoPlayer360({ videoUrl, seekTo, onClose, events = [], gameInfo, dotsMode: initialDotsMode = 'all', lockDots = false, initialTvMode = true }) {
  const wrapperRef = useRef(null);
  const placeholderRef = useRef(null);
  const containerRef = useRef(null);
  const videoRef = useRef(null);
  const rendererRef = useRef(null);
  const cameraRef = useRef(null);
  const animRef = useRef(null);
  const hlsCleanupRef = useRef(null);
  const stateRef = useRef({ lon: 0, lat: 0, targetLon: 0, targetLat: 0, fov: 75, targetFov: 75 });
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(true);
  const [tvMode, setTvMode] = useState(initialTvMode);
  const [gyroActive, setGyroActive] = useState(false);
  const [dotsMode, setDotsMode] = useState(initialDotsMode);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isPortrait, setIsPortrait] = useState(typeof window !== 'undefined' ? window.innerHeight > window.innerWidth : true);
  // Current page orientation angle (0/90/180/270). Used in fullscreen to keep
  // the video locked to landscape-primary regardless of iOS auto-rotate.
  const [screenAngle, setScreenAngle] = useState(() => {
    if (typeof window === 'undefined') return 0;
    const a = screen.orientation ? screen.orientation.angle : (window.orientation || 0);
    const norm = ((a % 360) + 360) % 360;
    return norm;
  });
  // When entering fullscreen on a touch device, lock the video frame to
  // landscape-primary via CSS rotation that compensates for the current page
  // angle — so the video stays landscape no matter how iOS rotates the page.
  const [fullscreenRotated, setFullscreenRotated] = useState(false);
  const rotatedRef = useRef(false);
  // Live page-angle ref so pointer/gyro handlers (inside the long-lived
  // useEffect closure) can read the current orientation without re-binding.
  const screenAngleRef = useRef(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [rect, setRect] = useState({ top: 0, left: 0, width: 0, height: 0 });
  const hideTimerRef = useRef(null);

  // Track the 360° watch (endTs on unmount → watch time)
  useEffect(() => {
    const docId = trackUsage('watch_360', { gameId: gameInfo?.gameId || null }, false);
    return () => { untrackUsage(docId); };
  }, []);

  // Fullscreen toggle — portal + position:fixed (escapes parent transforms; works on iOS PWA)
  const toggleFullscreen = () => setIsFullscreen(f => {
    const next = !f;
    if (next) {
      document.body.style.overflow = 'hidden';
      document.documentElement.style.overflow = 'hidden';
      // 1) Try real Fullscreen API on the wrapper. On iPad / Android Chrome /
      //    iOS 16.4+ Safari this enters TRUE fullscreen (hides PWA status bar).
      try {
        const el = wrapperRef.current;
        const req = el?.requestFullscreen || el?.webkitRequestFullscreen;
        if (req) { const p = req.call(el); if (p && p.catch) p.catch(() => {}); }
      } catch (e) {}
      // 2) Lock orientation. Works on Android, and inside real fullscreen on iOS 16.4+.
      try { screen.orientation?.lock?.('landscape').catch(() => {}); } catch (e) {}
      // 3) No CSS-rotation fallback. On iOS PWA standalone (which honors
      //    neither Fullscreen API nor orientation.lock) we just fill the
      //    viewport with 100dvw/100dvh. If the user holds the phone portrait,
      //    the video letterboxes 16:9 and we show a "rotate phone" hint.
      //    When they rotate physically, dvw/dvh swap and the video fills
      //    landscape naturally. The iOS status bar stays visible — Apple
      //    limitation that no software workaround can fix in standalone mode.
      setFullscreenRotated(false);
      rotatedRef.current = false;
    } else {
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
      try { screen.orientation?.unlock?.(); } catch (e) {}
      try {
        if (document.exitFullscreen) document.exitFullscreen().catch(() => {});
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } catch (e) {}
      setFullscreenRotated(false);
      rotatedRef.current = false;
    }
    // Reset gyro baseline so it re-calibrates to the new orientation
    if (containerRef.current?._resetGyroBaseline) containerRef.current._resetGyroBaseline();
    setControlsVisible(true);
    return next;
  });

  // Auto-hide controls after 3s of inactivity while in fullscreen
  useEffect(() => {
    if (!isFullscreen) { setControlsVisible(true); return; }
    const arm = () => {
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
      hideTimerRef.current = setTimeout(() => setControlsVisible(false), 3000);
    };
    arm();
    return () => { if (hideTimerRef.current) clearTimeout(hideTimerRef.current); };
  }, [isFullscreen, controlsVisible]);

  // Cleanup body overflow if unmounted while fullscreen
  useEffect(() => () => {
    document.body.style.overflow = '';
    document.documentElement.style.overflow = '';
    try { screen.orientation?.unlock?.(); } catch(e) {}
  }, []);

  const showControls = () => {
    if (!isFullscreen) return;
    setControlsVisible(true);
  };
  const toggleControls = () => {
    if (!isFullscreen) return;
    setControlsVisible(v => !v);
  };

  // Track placeholder bounding rect so the portal'd wrapper can overlay it in inline mode.
  // Canvas stays mounted in body the whole time — never re-parented — so iOS Safari
  // doesn't lose the WebGL context.
  useEffect(() => {
    if (isFullscreen) return;
    const update = () => {
      if (!placeholderRef.current) return;
      const r = placeholderRef.current.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
    update();
    const raf = requestAnimationFrame(update);
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    let ro;
    if (typeof ResizeObserver !== 'undefined' && placeholderRef.current) {
      ro = new ResizeObserver(update);
      ro.observe(placeholderRef.current);
      ro.observe(document.documentElement);
    }
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
      if (ro) ro.disconnect();
    };
  }, [isFullscreen]);

  // Track orientation. Always-on so fullscreen wrapperStyle can compensate
  // for iOS auto-rotate and keep the video locked to landscape-primary.
  // Gyro baseline only resets when NOT in fullscreen (avoid mid-watch jitter).
  useEffect(() => {
    const onOrient = () => {
      const a = screen.orientation ? screen.orientation.angle : (window.orientation || 0);
      const norm = ((a % 360) + 360) % 360;
      screenAngleRef.current = norm;
      setScreenAngle(norm);
      setIsPortrait(window.innerHeight > window.innerWidth);
      if (!isFullscreen && containerRef.current?._resetGyroBaseline) {
        containerRef.current._resetGyroBaseline();
      }
    };
    window.addEventListener('resize', onOrient);
    window.addEventListener('orientationchange', onOrient);
    onOrient(); // seed screenAngleRef on mount
    return () => {
      window.removeEventListener('resize', onOrient);
      window.removeEventListener('orientationchange', onOrient);
    };
  }, [isFullscreen]);

  // Trigger renderer resize when fullscreen toggles or inline rect changes
  useEffect(() => {
    const t = setTimeout(() => {
      if (!containerRef.current || !rendererRef.current || !cameraRef.current) return;
      const nw = containerRef.current.clientWidth;
      const nh = containerRef.current.clientHeight;
      if (nw === 0 || nh === 0) return;
      cameraRef.current.aspect = nw / nh;
      cameraRef.current.updateProjectionMatrix();
      rendererRef.current.setSize(nw, nh);
    }, 50);
    return () => clearTimeout(t);
  }, [isFullscreen, isPortrait, rect.width, rect.height]);

  // TV mode constrains vertical look + narrows FOV (cinematic). 3D gyro mode
  // unlocks the full 360° sphere so parents can look anywhere via gyro/touch.
  // Toggleable via the 📺 / 🌐 button.
  useEffect(() => {
    const st = stateRef.current;
    st.tvMode = tvMode;
    if (tvMode) {
      st.targetFov = 40;
      st.targetLat = Math.max(-45, Math.min(10, st.targetLat));
    } else {
      // Snap to a comfortable wide-angle when opening 3D mode
      st.targetFov = Math.max(st.targetFov, 75);
    }
  }, [tvMode]);

  // Load Three.js and set up scene
  useEffect(() => {
    let cancelled = false;
    loadThreeJS().then((THREE) => {
      if (cancelled || !containerRef.current) return;

      const container = containerRef.current;
      const w = container.clientWidth || 640;
      const h = container.clientHeight || 360;

      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(w, h);
      container.appendChild(renderer.domElement);
      rendererRef.current = renderer;

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(75, w / h, 0.1, 1100);
      camera.position.set(0, 0, 0.01);
      cameraRef.current = camera;

      const geometry = new THREE.SphereGeometry(500, 64, 40);
      geometry.scale(-1, 1, 1);

      const video = videoRef.current;
      if (!video) return;

      // HLS (.m3u8) needs hls.js on non-Safari; plain MP4 just sets src directly.
      const isHls = /\.m3u8(\?|$)/i.test(videoUrl);
      if (isHls) {
        attachHls(video, videoUrl).then(fn => { hlsCleanupRef.current = fn; }).catch(() => {});
      } else {
        video.src = videoUrl;
      }

      const texture = new THREE.VideoTexture(video);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;

      const material = new THREE.MeshBasicMaterial({ map: texture });
      scene.add(new THREE.Mesh(geometry, material));

      video.addEventListener('loadedmetadata', () => {
        setDuration(video.duration);
        setReady(true);
        video.play().catch(() => {});
      });
      // iOS sometimes silently rejects autoplay even when muted. Retry play()
      // on the first user gesture inside the viewer (drag, tap, pinch).
      const kickPlay = () => { try { video.play().catch(()=>{}); } catch(e) {} };
      container.addEventListener('pointerdown', kickPlay, { once: true });
      container.addEventListener('touchstart', kickPlay, { once: true, passive: true });
      video.addEventListener('timeupdate', () => setCurrentTime(video.currentTime));
      video.addEventListener('play', () => setPlaying(true));
      video.addEventListener('pause', () => setPlaying(false));
      video.load();

      // Pointer handling
      let dragLast = null;
      const pointers = new Map();
      let pinchStartDist = 0, pinchStartFov = 75;

      const pointerDist = () => {
        const pts = [...pointers.values()];
        if (pts.length < 2) return 0;
        return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      };

      const canvas = renderer.domElement;
      canvas.style.touchAction = 'none';

      canvas.addEventListener('pointerdown', (e) => {
        canvas.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 1) dragLast = { x: e.clientX, y: e.clientY };
        else if (pointers.size === 2) { dragLast = null; pinchStartDist = pointerDist(); pinchStartFov = stateRef.current.fov; }
      });
      canvas.addEventListener('pointermove', (e) => {
        if (!pointers.has(e.pointerId)) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const st = stateRef.current;
        if (pointers.size === 1 && dragLast) {
          let dx = e.clientX - dragLast.x, dy = e.clientY - dragLast.y;
          // The wrapper may be CSS-rotated to keep the video locked to
          // landscape-primary regardless of the page's actual orientation.
          // Pointer events arrive in unrotated screen space, so we apply the
          // INVERSE of the wrapper's rotation to get the delta in content space.
          // Wrapper rotation by screenAngle: 0→+90°, 90→ 0, 180→−90°, 270→180°.
          if (rotatedRef.current) {
            const sx = dx, sy = dy;
            const a = screenAngleRef.current;
            if (a === 0) {           // wrapper rotated +90° CW → inverse 90° CCW
              dx = sy;  dy = -sx;
            } else if (a === 180) {  // wrapper rotated −90° (CCW) → inverse 90° CW
              dx = -sy; dy = sx;
            } else if (a === 270) {  // wrapper rotated 180° → inverse 180°
              dx = -sx; dy = -sy;
            }
            // a === 90: no rotation → leave dx/dy as-is
          }
          // Pan gain — similar boost to gyro so a small finger swipe moves
          // the view a useful amount. Yaw is more sensitive than pitch.
          const PAN_YAW_GAIN = 2.5;
          const PAN_PITCH_GAIN = 1.6;
          const sensitivity = 0.1 * (st.fov / 75);
          const dLon = -dx * sensitivity * PAN_YAW_GAIN;
          const dLat = dy * sensitivity * PAN_PITCH_GAIN;
          st.targetLon += dLon;
          st.targetLat += dLat;
          const maxLat = st.tvMode ? [45, 10] : [85, 85];
          st.targetLat = Math.max(-maxLat[0], Math.min(maxLat[1], st.targetLat));
          // Shift gyro anchor so drag and gyro compose (don't fight)
          gyroAnchorLon += dLon;
          gyroAnchorLat += dLat;
          gyroSmoothedLon += dLon;
          gyroSmoothedLat += dLat;
          dragLast = { x: e.clientX, y: e.clientY };
        } else if (pointers.size === 2 && pinchStartDist > 0) {
          const ratio = pinchStartDist / pointerDist();
          const minFov = st.tvMode ? 25 : 25;
          const maxFov = st.tvMode ? 60 : 110;
          st.targetFov = Math.max(minFov, Math.min(maxFov, pinchStartFov * ratio));
        }
      });
      const endPointer = (e) => {
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinchStartDist = 0;
        if (pointers.size === 0) dragLast = null;
        if (pointers.size === 1) { const p = [...pointers.values()][0]; dragLast = { x: p.x, y: p.y }; }
      };
      canvas.addEventListener('pointerup', endPointer);
      canvas.addEventListener('pointercancel', endPointer);
      canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const st = stateRef.current;
        const maxFov = st.tvMode ? 60 : 110;
        st.targetFov = Math.max(25, Math.min(maxFov, st.targetFov + e.deltaY * 0.05));
      }, { passive: false });

      // Gyroscope support (like YouTube 360°)
      // Uses absolute orientation relative to the position when gyro was enabled.
      let gyroEnabled = false;
      let gyroBaseAlpha = null, gyroBaseBeta = null, gyroBaseGamma = null;
      let gyroAnchorLon = 0, gyroAnchorLat = 0;
      let gyroSmoothedLon = 0, gyroSmoothedLat = 0;

      const onDeviceOrientation = (e) => {
        if (e.alpha == null || e.beta == null || e.gamma == null) return;
        const st = stateRef.current;

        // On first reading, capture baseline and current view as anchor
        if (gyroBaseAlpha == null) {
          gyroBaseAlpha = e.alpha;
          gyroBaseBeta = e.beta;
          gyroBaseGamma = e.gamma;
          gyroAnchorLon = st.targetLon;
          gyroAnchorLat = st.targetLat;
          gyroSmoothedLon = st.targetLon;
          gyroSmoothedLat = st.targetLat;
          return;
        }

        // Delta from baseline
        let dAlpha = e.alpha - gyroBaseAlpha;
        if (dAlpha > 180) dAlpha -= 360;
        if (dAlpha < -180) dAlpha += 360;
        let dBeta = e.beta - gyroBaseBeta;
        let dGamma = e.gamma - gyroBaseGamma;

        // Map axes based on screen orientation (portrait vs landscape).
        // When the video is CSS-rotated 90° cw (rotatedRef), pin the mapping
        // to landscape-left regardless of the actual device angle — otherwise
        // a 180° flip would invert gyro mid-watch.
        const screenAngle = rotatedRef.current ? 90 : (screen.orientation?.angle ?? window.orientation ?? 0);
        let dLon, dLat;
        if (screenAngle === 0 || screenAngle === 180) {
          // Portrait: alpha→yaw, beta→pitch
          dLon = -dAlpha;
          dLat = dBeta;
        } else if (screenAngle === 90) {
          // Landscape left (home button on right)
          dLon = -dAlpha;
          dLat = -dGamma;
        } else {
          // Landscape right (home button on left, screenAngle === -90 or 270)
          dLon = -dAlpha;
          dLat = dGamma;
        }

        // Compute desired position (anchor + offset)
        // Sensitivity multiplier: small head turns pan the view further so you
        // don't have to rotate the phone 180° to see across the field.
        const GYRO_YAW_GAIN = 2.2;
        const GYRO_PITCH_GAIN = 1.4;
        const desiredLon = gyroAnchorLon + dLon * GYRO_YAW_GAIN;
        let desiredLat = gyroAnchorLat + dLat * GYRO_PITCH_GAIN;

        // Clamp lat to TV mode or free limits (no lon clamp)
        const maxLat = st.tvMode ? [45, 10] : [85, 85];
        desiredLat = Math.max(-maxLat[0], Math.min(maxLat[1], desiredLat));

        // Low-pass filter for smooth movement
        gyroSmoothedLon += (desiredLon - gyroSmoothedLon) * 0.25;
        gyroSmoothedLat += (desiredLat - gyroSmoothedLat) * 0.25;

        st.targetLon = gyroSmoothedLon;
        st.targetLat = gyroSmoothedLat;
      };

      // Request permission on iOS 13+
      const enableGyro = () => {
        if (gyroEnabled) return;
        if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
          DeviceOrientationEvent.requestPermission().then(state => {
            if (state === 'granted') {
              gyroEnabled = true;
              window.addEventListener('deviceorientation', onDeviceOrientation);
            }
          }).catch(() => {});
        } else if ('DeviceOrientationEvent' in window) {
          gyroEnabled = true;
          window.addEventListener('deviceorientation', onDeviceOrientation);
        }
      };
      // Auto-enable on Android (no permission needed); on iOS triggered by user gesture
      if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission !== 'function') {
        enableGyro();
      }
      container._enableGyro = enableGyro;
      container._disableGyro = () => {
        gyroEnabled = false;
        gyroBaseAlpha = null;
        gyroBaseBeta = null;
        gyroBaseGamma = null;
        window.removeEventListener('deviceorientation', onDeviceOrientation);
      };
      // Reset gyro baseline on orientation change (prevents jitter during fullscreen rotation)
      container._resetGyroBaseline = () => {
        gyroBaseAlpha = null;
        gyroBaseBeta = null;
        gyroBaseGamma = null;
      };

      // Render loop
      const animate = () => {
        animRef.current = requestAnimationFrame(animate);
        const st = stateRef.current;
        st.lon += (st.targetLon - st.lon) * 0.18;
        st.lat += (st.targetLat - st.lat) * 0.18;
        st.fov += (st.targetFov - st.fov) * 0.18;
        camera.fov = st.fov;
        camera.updateProjectionMatrix();
        const phi = THREE.MathUtils.degToRad(90 - st.lat);
        const theta = THREE.MathUtils.degToRad(st.lon);
        camera.lookAt(new THREE.Vector3(
          Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta)
        ));
        renderer.render(scene, camera);
      };
      animate();

      // Resize handler
      const onResize = () => {
        if (!containerRef.current) return;
        const nw = containerRef.current.clientWidth;
        const nh = containerRef.current.clientHeight;
        camera.aspect = nw / nh;
        camera.updateProjectionMatrix();
        renderer.setSize(nw, nh);
      };
      window.addEventListener('resize', onResize);
      container._cleanup = () => {
        window.removeEventListener('resize', onResize);
        if (container._disableGyro) container._disableGyro();
      };
    });

    return () => {
      cancelled = true;
      if (animRef.current) cancelAnimationFrame(animRef.current);
      if (hlsCleanupRef.current) { try { hlsCleanupRef.current(); } catch {} hlsCleanupRef.current = null; }
      if (videoRef.current) { videoRef.current.pause(); videoRef.current.src = ''; }
      if (rendererRef.current) rendererRef.current.dispose();
      if (containerRef.current) {
        if (containerRef.current._cleanup) containerRef.current._cleanup();
        containerRef.current.innerHTML = '';
      }
    };
  }, [videoUrl]);

  // Seek when seekTo changes
  useEffect(() => {
    if (seekTo != null && videoRef.current && isFinite(seekTo.time)) {
      videoRef.current.currentTime = seekTo.time;
    }
  }, [seekTo]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) videoRef.current.play();
    else videoRef.current.pause();
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !videoRef.current.muted;
    setMuted(videoRef.current.muted);
  };

  const fmtTime = (t) => {
    if (!isFinite(t)) return '0:00';
    const m = Math.floor(t / 60), s = Math.floor(t % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  // Derive period & displayed minute from playback time (TV-broadcast style).
  // Find the smallest event.elapsed where event.period === 2 → that's when 2nd half started.
  const halfLen = (gameInfo && gameInfo.halfLengthMin) || 25;
  const half2StartElapsed = (events || [])
    .filter(e => e.period === 2 && isFinite(e.elapsed))
    .reduce((min, e) => Math.min(min, e.elapsed), Infinity);
  const isLive = gameInfo && gameInfo.status === 'active';
  const isFinished = gameInfo && gameInfo.status === 'finished';
  const videoEnded = duration > 0 && currentTime >= duration - 0.5;
  // 1-second ticker so the live scorebug minute advances even when the video
  // element doesn't fire timeupdate (e.g. while buffering at the live edge).
  const [, setLiveTick] = useState(0);
  useEffect(() => {
    if (!isLive || !gameInfo?.clockRunning) return;
    const id = setInterval(() => setLiveTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [isLive, gameInfo?.clockRunning]);
  // For LIVE matches, mirror the coach-app match clock (wall-clock based) so
  // the scorebug stays in sync with the page header. For replays, derive from
  // video currentTime as before.
  const liveElapsedSec = (() => {
    if (!isLive || !gameInfo) return 0;
    if (gameInfo.elapsedAtPause === undefined) {
      return gameInfo.startedAt ? Math.floor((Date.now() - gameInfo.startedAt) / 1000) : 0;
    }
    if (gameInfo.clockRunning && gameInfo.segmentStartedAt) {
      return gameInfo.elapsedAtPause + Math.floor((Date.now() - gameInfo.segmentStartedAt) / 1000);
    }
    return gameInfo.elapsedAtPause || 0;
  })();
  const inSecondHalf = isLive
    ? (gameInfo && gameInfo.period >= 2)
    : (isFinite(half2StartElapsed)
        ? currentTime >= half2StartElapsed
        : (gameInfo && gameInfo.period >= 2));
  const displayedMinute = isLive
    ? Math.floor(liveElapsedSec / 60) + (inSecondHalf ? halfLen : 0)
    : (inSecondHalf
        ? (isFinite(half2StartElapsed)
            ? Math.floor((currentTime - half2StartElapsed) / 60) + halfLen
            : Math.floor(currentTime / 60) + halfLen)
        : Math.floor(currentTime / 60));

  // Auto-pick readable text color for a jersey background (WCAG luminance)
  const textOnColor = (hex) => {
    if (!hex || typeof hex !== 'string') return '#fff';
    const h = hex.replace('#', '');
    const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
    if (full.length !== 6) return '#fff';
    const r = parseInt(full.slice(0, 2), 16) / 255;
    const g = parseInt(full.slice(2, 4), 16) / 255;
    const b = parseInt(full.slice(4, 6), 16) / 255;
    const toLin = (c) => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    const lum = 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
    return lum > 0.5 ? '#0a0a0a' : '#ffffff';
  };
  const homeColor = (gameInfo && gameInfo.homeColor) || '#65a30d';
  const awayColor = (gameInfo && gameInfo.awayColor) || '#dc2626';

  // Compute running score based on goal events up to current playback time
  const runningScore = useMemo(() => {
    let home = 0, away = 0;
    (events || []).forEach(e => {
      if (!isFinite(e.elapsed)) return;
      if (e.elapsed > currentTime) return;
      if (e.type === 'GOAL') home++;
      else if (e.type === 'OPP_GOAL') away++;
    });
    return { home, away };
  }, [events, currentTime]);

  // Wrapper style. In rotated fullscreen we compensate for the current page
  // orientation so the video is ALWAYS displayed in landscape-primary physical
  // orientation (camera lens at top of landscape view), regardless of whether
  // iOS rotation lock is on or off, and regardless of how the user holds the
  // phone. Uses dvw/dvh so the wrapper extends behind the iOS PWA status bar.
  const baseFs = { position: 'fixed', top: 0, left: 0, zIndex: 99999, background: '#000', borderRadius: 0 };
  const wrapperStyle = isFullscreen
    ? (fullscreenRotated
        ? (() => {
            const a = screenAngle;
            if (a === 0) {
              // Page is portrait → rotate 90° CW
              return { ...baseFs, width: '100dvh', height: '100dvw',
                transform: 'translate(100dvw, 0) rotate(90deg)', transformOrigin: 'top left' };
            } else if (a === 90) {
              // Page is landscape-primary → no CSS rotation needed
              return { ...baseFs, width: '100dvw', height: '100dvh' };
            } else if (a === 180) {
              // Page is portrait upside-down → rotate -90° to get landscape-primary
              return { ...baseFs, width: '100dvh', height: '100dvw',
                transform: 'translate(0, 100dvh) rotate(-90deg)', transformOrigin: 'top left' };
            } else {
              // Page is landscape-secondary (270°) → rotate 180° to flip back to primary
              return { ...baseFs, width: '100dvw', height: '100dvh',
                transform: 'translate(100dvw, 100dvh) rotate(180deg)', transformOrigin: 'top left' };
            }
          })()
        : { ...baseFs, width: '100dvw', height: '100dvh' })
    : { position: 'fixed', top: rect.top, left: rect.left, width: rect.width, height: rect.height, zIndex: 10 };

  const playerNode = (
    <div ref={wrapperRef} style={wrapperStyle} className={`bg-black overflow-hidden ${isFullscreen ? '' : 'rounded-2xl border border-stone-800'}`}>
      {/* Top-right floating fullscreen toggle (YouTube-style icon).
          Lives outside the controls bar so it stays reachable in fullscreen
          even after controls auto-hide. */}
      <button
        onClick={(e) => { e.stopPropagation(); toggleFullscreen(); showControls(); }}
        className={`absolute z-20 ${isFullscreen ? 'w-11 h-11' : 'w-9 h-9'} rounded-full bg-black/60 flex items-center justify-center text-white active:scale-95 transition-opacity duration-300 ${(!isFullscreen || controlsVisible) ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        style={{
          // In rotated fullscreen the wrapper is already oriented so its top-right
          // maps to the landscape view's top-right — plain 12px puts the button
          // flush at landscape top-right (no safe-area-inset gap).
          top: isFullscreen ? (fullscreenRotated ? '12px' : 'max(env(safe-area-inset-top, 0px) + 8px, 12px)') : '8px',
          right: isFullscreen ? '12px' : (onClose ? '44px' : '8px'),
        }}
        aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
      >
        {isFullscreen ? (
          // Exit fullscreen — four arrows pointing inward (YouTube minimize)
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 4 9 9 4 9" />
            <polyline points="15 4 15 9 20 9" />
            <polyline points="9 20 9 15 4 15" />
            <polyline points="15 20 15 15 20 15" />
          </svg>
        ) : (
          // Enter fullscreen — four corner brackets pointing outward (YouTube expand)
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 9 4 4 9 4" />
            <polyline points="20 9 20 4 15 4" />
            <polyline points="4 15 4 20 9 20" />
            <polyline points="20 15 20 20 15 20" />
          </svg>
        )}
      </button>
      {/* Close button (only when not fullscreen and onClose provided) */}
      {!isFullscreen && onClose && (
        <button
          onClick={onClose}
          className="absolute top-2 right-2 z-10 w-8 h-8 rounded-full bg-black/60 flex items-center justify-center text-white active:scale-95"
        >
          <X className="w-4 h-4" />
        </button>
      )}
      {/* Score overlay — modern broadcast scorebug */}
      {gameInfo && (() => {
        // Row 2 text: half + minute (LIVE shown as a red blinking dot, not text).
        let statusLabel;
        if (isFinished && videoEnded) {
          statusLabel = 'FT';
        } else {
          statusLabel = inSecondHalf ? `2ND · ${displayedMinute}'` : `1ST · ${displayedMinute}'`;
        }
        const showLiveDot = isLive && !(isFinished && videoEnded);
        // Shared background so both rows + corner fillets read as one piece.
        const bugBg = 'linear-gradient(135deg, rgba(15,15,18,0.92) 0%, rgba(28,28,32,0.88) 100%)';
        // Approximated solid for the tiny fillet pieces (gradient is subtle, this is invisible at 8px).
        const filletColor = 'rgba(22,22,25,0.91)';
        return (
          <div className={`absolute z-10 pointer-events-none flex flex-col items-center ${isFullscreen ? (fullscreenRotated ? 'top-[14px] left-3' : 'top-[max(env(safe-area-inset-top,0px)+10px,14px)] left-3') : 'top-2 left-2'}`}>
            {/* Row 1 — score (rounded all corners) */}
            <div
              className="rounded-2xl shadow-2xl border border-white/15 overflow-hidden backdrop-blur-md"
              style={{ background: bugBg }}
            >
              <div className="flex items-stretch text-[11px]">
                <div className="flex items-center pl-1.5 pr-2.5 py-1.5">
                  <div className="w-[6px] h-5 rounded-sm mr-2" style={{ background: homeColor, border: '1px solid rgba(255,255,255,0.9)', boxShadow: `0 0 6px ${homeColor}80` }} />
                  <span className="font-bold tracking-wider truncate max-w-[5rem] text-white" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}>{(gameInfo.home || 'STM').toUpperCase()}</span>
                </div>
                <div className="px-2.5 py-1.5 flex items-center gap-1.5 bg-black/30">
                  <span className="font-display tabular-nums text-white text-base leading-none" style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}>{runningScore.home}</span>
                  <span className="text-white/30 text-xs">–</span>
                  <span className="font-display tabular-nums text-white text-base leading-none" style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}>{runningScore.away}</span>
                </div>
                <div className="flex items-center pl-2.5 pr-1.5 py-1.5">
                  <span className="font-bold tracking-wider truncate max-w-[5rem] text-white" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}>{(gameInfo.away || 'OPP').toUpperCase()}</span>
                  <div className="w-[6px] h-5 rounded-sm ml-2" style={{ background: awayColor, border: '1px solid rgba(255,255,255,0.9)', boxShadow: `0 0 6px ${awayColor}80` }} />
                </div>
              </div>
            </div>
            {/* Row 2 wrapper — relative for the corner fillets */}
            <div className="relative -mt-px">
              {/* Left fillet — concave curve from row 1 down to row 2's top-left */}
              <span
                aria-hidden
                className="absolute left-[-8px] top-0 w-2 h-2"
                style={{
                  background: filletColor,
                  WebkitMaskImage: 'radial-gradient(circle at bottom right, transparent 8px, black 8.5px)',
                  maskImage: 'radial-gradient(circle at bottom right, transparent 8px, black 8.5px)',
                }}
              />
              {/* Right fillet */}
              <span
                aria-hidden
                className="absolute right-[-8px] top-0 w-2 h-2"
                style={{
                  background: filletColor,
                  WebkitMaskImage: 'radial-gradient(circle at bottom left, transparent 8px, black 8.5px)',
                  maskImage: 'radial-gradient(circle at bottom left, transparent 8px, black 8.5px)',
                }}
              />
              {/* Row 2 — fixed width sized for longest content ("2ND · 90+5'" + live dot) */}
              <div
                className="rounded-b-2xl shadow-lg border border-t-0 border-white/15 backdrop-blur-md flex items-center justify-center gap-1.5 px-3 py-1 w-[150px]"
                style={{ background: bugBg }}
              >
                {showLiveDot && (
                  <span className="relative flex items-center justify-center mr-0.5">
                    <span className="absolute w-3.5 h-3.5 rounded-full bg-red-500/60 animate-ping" />
                    <span className="relative w-2.5 h-2.5 rounded-full bg-red-500" style={{ boxShadow: '0 0 8px rgba(239,68,68,0.9)' }} />
                  </span>
                )}
                <span className="font-display tabular-nums text-white text-[13px] font-extrabold tracking-[0.15em] leading-none" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.7)' }}>{statusLabel}</span>
              </div>
            </div>
          </div>
        );
      })()}
      {/* Canvas container — single DOM node always, styled by mode.
          In rotated fullscreen the wrapper itself is already rotated and sized
          to landscape, so the canvas just fills 100%. The old portrait-letterbox
          path is only used for desktop (non-touch) where we don't rotate. */}
      <div
        ref={containerRef}
        onClick={toggleControls}
        className="bg-black"
        style={
          isFullscreen && isPortrait && !fullscreenRotated
            ? { position: 'absolute', left: 0, right: 0, top: '50%', transform: 'translateY(-50%)', width: '100vw', height: 'calc(100vw * 9 / 16)' }
            : { width: '100%', height: '100%' }
        }
      />
      {/* Portrait fullscreen hint — appears when user is in fullscreen but
          still holding the phone portrait (typically iOS PWA, where neither
          Fullscreen API nor orientation.lock work). Auto-vanishes when they
          rotate to landscape. */}
      {isFullscreen && isPortrait && (
        <div className="absolute left-0 right-0 z-20 pointer-events-none flex justify-center"
             style={{ bottom: 'calc(50% - 100vw * 9 / 32 - 44px)' }}>
          <div className="bg-black/70 text-white text-xs px-3 py-1.5 rounded-full flex items-center gap-1.5">
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="5" y="2" width="14" height="20" rx="2"/>
              <path d="M9 18h6"/>
            </svg>
            Rotate phone for landscape
          </div>
        </div>
      )}
      {/* Hidden <video> element — MUST be in the DOM as JSX (not createElement) or
          iOS Safari refuses to decode frames into the Three.js VideoTexture. */}
      <video
        ref={videoRef}
        muted
        playsInline
        webkit-playsinline="true"
        crossOrigin="anonymous"
        preload="auto"
        style={{ display: 'none' }}
      />
      {/* Controls — slim bar with YouTube-style SVG icons */}
      <div className={`px-3 py-1.5 flex items-center gap-1.5 bg-black/85 absolute bottom-0 left-0 right-0 transition-opacity duration-300 ${isFullscreen ? 'pb-[max(env(safe-area-inset-bottom,0px),6px)]' : ''} ${isFullscreen && !controlsVisible ? 'opacity-0 pointer-events-none' : 'opacity-100'}`} onClick={(e) => { e.stopPropagation(); showControls(); }}>
        {/* Play / Pause */}
        <button onClick={togglePlay} className="w-10 h-10 rounded-full flex items-center justify-center text-white active:scale-95" aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? (
            <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>
          ) : (
            <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
          )}
        </button>
        {/* Rewind 10s — hidden for live, no seekable history */}
        {!isLive && (
        <button onClick={() => { if (videoRef.current) videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - 10); }}
          className="w-10 h-10 rounded-full flex items-center justify-center text-white active:scale-95" aria-label="Rewind 10 seconds">
          <svg viewBox="0 0 24 24" className="w-7 h-7">
            <path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            <text x="12" y="17" textAnchor="middle" fontSize="8" fontWeight="800" fill="currentColor" fontFamily="system-ui, -apple-system, sans-serif">10</text>
          </svg>
        </button>
        )}
        {/* Forward 10s — hidden for live */}
        {!isLive && (
        <button onClick={() => { if (videoRef.current) videoRef.current.currentTime = Math.min(duration, videoRef.current.currentTime + 10); }}
          className="w-10 h-10 rounded-full flex items-center justify-center text-white active:scale-95" aria-label="Forward 10 seconds">
          <svg viewBox="0 0 24 24" className="w-7 h-7">
            <path d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            <text x="12" y="17" textAnchor="middle" fontSize="8" fontWeight="800" fill="currentColor" fontFamily="system-ui, -apple-system, sans-serif">10</text>
          </svg>
        </button>
        )}
        {/* Jump-to-live button — live only */}
        {isLive && (
          <button
            onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              try {
                const end = v.seekable.length ? v.seekable.end(v.seekable.length - 1) : 0;
                if (end > 0) v.currentTime = Math.max(0, end - 1);
                if (v.paused) v.play().catch(() => {});
              } catch (e) {}
            }}
            className="flex items-center gap-1 px-2 h-7 rounded-full bg-red-600 active:scale-95"
            aria-label="Jump to live"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
            <span className="text-[10px] font-bold tracking-widest text-white">LIVE</span>
          </button>
        )}
        <div className="flex-1 relative">
          {!isLive && (
          <input
            type="range" min="0" max={Math.floor(duration) || 1} value={Math.floor(currentTime)}
            onChange={(e) => { if (videoRef.current) videoRef.current.currentTime = Number(e.target.value); }}
            className="w-full h-0.5 accent-lime-400 block"
          />
          )}
          {duration > 0 && events.filter(e => {
            if (e.elapsed <= 0 || e.elapsed > duration) return false;
            if (dotsMode === 'goals') return e.type === 'GOAL' || e.type === 'OPP_GOAL';
            // Silent events aren't coach actions and don't belong on the
            // scrubber. Naming SUB/GK_CHANGE here missed POSITION, which the
            // tactical board emits on every drag — hundreds of dots a game.
            if (EVENT_TYPES[e.type]?.silent) return false;
            return e.type !== 'SUB' && e.type !== 'GK_CHANGE';
          }).map(e => {
            const pct = (e.elapsed / duration) * 100;
            const ev = EVENT_TYPES[e.type];
            const color = e.type === 'GOAL' ? 'bg-lime-400' : e.type === 'OPP_GOAL' ? 'bg-red-500' : e.type === 'SHOT_ON' || e.type === 'SHOT_OFF' ? 'bg-yellow-400' : e.type === 'SAVE' || e.type === 'BLOCK' ? 'bg-sky-400' : 'bg-stone-400';
            return (
              <button
                key={e.id}
                onClick={() => { if (videoRef.current) videoRef.current.currentTime = e.elapsed; }}
                title={`${ev?.label || e.type} @ ${fmtTime(e.elapsed)}`}
                className={`absolute -top-1.5 w-2 h-2 rounded-full ${color} border border-stone-950 hover:scale-150 transition`}
                style={{ left: `calc(${pct}% - 4px)` }}
              />
            );
          })}
        </div>
        {!isLive && (
          <span className="text-[11px] text-white/80 tabular-nums whitespace-nowrap">{fmtTime(currentTime)} / {fmtTime(duration)}</span>
        )}
        {/* Mute / Unmute — YouTube-style speaker icon */}
        <button onClick={toggleMute} className="w-9 h-9 rounded-full flex items-center justify-center text-white active:scale-95" aria-label={muted ? 'Unmute' : 'Mute'}>
          {muted ? (
            <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor">
              <path d="M3 9v6h4l5 4V5L7 9H3z"/>
              <path d="M16.5 9l5 6m0-6l-5 6" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor">
              <path d="M3 9v6h4l5 4V5L7 9H3z"/>
              <path d="M15.5 8.5c1.5 1 2.5 2.5 2.5 3.5s-1 2.5-2.5 3.5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round"/>
              <path d="M18 6c2.5 1.5 4 4 4 6s-1.5 4.5-4 6" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round"/>
            </svg>
          )}
        </button>
        {!lockDots && (
          <button
            onClick={() => setDotsMode(dotsMode === 'all' ? 'goals' : 'all')}
            title={dotsMode === 'all' ? 'Showing all events — tap for goals only' : 'Showing goals only — tap for all events'}
            className={`text-[10px] font-bold px-2 py-1 rounded ${dotsMode === 'goals' ? 'bg-stone-800 text-stone-400' : 'bg-lime-500 text-black'} active:scale-95`}
          >
            {dotsMode === 'all' ? '● ALL' : '⚽ GOALS'}
          </button>
        )}
        {/* TV / 3D mode toggle — lets parents switch between cinematic
            broadcast view (TV) and free 360° look-anywhere (3D, pairs nicely
            with the gyro toggle below). */}
        <button onClick={() => setTvMode((v) => !v)}
          className={`w-9 h-9 rounded-full flex items-center justify-center active:scale-95 text-sm ${tvMode ? 'bg-lime-500 text-black' : 'text-white'}`}
          aria-label={tvMode ? 'Switch to 3D mode' : 'Switch to TV mode'}
          title={tvMode ? 'TV mode — tap for 3D 360°' : '3D 360° — tap for TV mode'}
        >
          {tvMode ? '📺' : '🌐'}
        </button>
        {/* Gyro toggle — compass-style SVG */}
        <button onClick={() => {
          const c = containerRef.current;
          if (!c) return;
          if (gyroActive) {
            if (c._disableGyro) c._disableGyro();
            setGyroActive(false);
          } else {
            if (c._enableGyro) c._enableGyro();
            setGyroActive(true);
          }
        }}
          className={`w-9 h-9 rounded-full flex items-center justify-center active:scale-95 ${gyroActive ? 'bg-lime-500 text-black' : 'text-white'}`}
          aria-label={gyroActive ? 'Disable gyro' : 'Enable gyro'}
          title={gyroActive ? 'Gyro on — tap to disable' : 'Gyro off — tap to enable'}
        >
          {/* Phone-with-motion-arcs — universal "tilt to control" icon */}
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="8" y="3" width="8" height="14" rx="1.5" transform="rotate(20 12 10)"/>
            <path d="M4 18c1.5 1.5 4 2.5 8 2.5s6.5-1 8-2.5"/>
            <path d="M3 15.5l1 2.5 2.5-1"/>
            <path d="M21 15.5l-1 2.5-2.5-1"/>
          </svg>
        </button>
      </div>
    </div>
  );

  return (
    <>
      {/* Placeholder reserves layout space in original tree */}
      <div ref={placeholderRef} className={isFullscreen ? 'hidden' : 'aspect-video max-h-[70vh] w-full'} />
      {typeof ReactDOM !== 'undefined' && ReactDOM.createPortal
        ? ReactDOM.createPortal(playerNode, document.body)
        : playerNode}
    </>
  );
}

function EventRow({ event, roster, onDelete, onTag, onSeek }) {
  const isSub = event.type === 'SUB';
  const isGKChange = event.type === 'GK_CHANGE';
  const ev = isSub
    ? { emoji: '🔄', label: 'SUB', requiresPlayer: true }
    : isGKChange
    ? { emoji: '🧤', label: 'GK SWAP', requiresPlayer: false }
    : (EVENT_TYPES[event.type] || { emoji: '•', label: event.type, requiresPlayer: false });
  const player = roster.find(p => p.id === event.playerId);
  const subOnPlayer = isSub ? roster.find(p => p.id === event.subOnPlayerId) : null;
  const prevGK = isGKChange ? roster.find(p => p.id === event.prevGKId) : null;
  const partner = event.type === 'GIVE_GO' && event.partnerId
    ? roster.find(p => p.id === event.partnerId) : null;
  return (
    <div className={`bg-stone-900 border border-stone-800 rounded-lg px-3 py-2 flex items-center gap-3 ${onSeek ? 'cursor-pointer active:bg-stone-800' : ''}`}
      onClick={onSeek || undefined}
    >
      <div className="text-xl">{ev.emoji}</div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-bold">
          {ev.label}
          {event.penalty && (
            <span className="ml-1.5 align-middle text-[9px] font-extrabold tracking-widest px-1 py-0.5 rounded bg-stone-700 text-stone-200">PEN</span>
          )}
        </div>
        {isSub ? (
          <div className="text-xs text-stone-300 truncate">
            {subOnPlayer ? `${subOnPlayer.name} #${subOnPlayer.number}` : '?'} <span className="text-lime-600 font-bold">IN</span>
            {' · '}
            {player ? `${player.name} #${player.number}` : '?'} <span className="text-stone-400 font-bold">OUT</span>
          </div>
        ) : isGKChange ? (
          <div className="text-xs text-stone-300 truncate">
            {player ? `${player.name} #${player.number}` : 'No GK'} <span className="text-amber-700 font-bold">IN GOAL</span>
            {prevGK && <span className="text-stone-400"> · {prevGK.name} #{prevGK.number} OUT</span>}
          </div>
        ) : (
          <>
            {player && (
              <div className="text-xs text-stone-300 truncate">
                {player.name} · #{player.number}
                {partner && (
                  <span className="text-stone-400"> → 🤝 {partner.name} #{partner.number}</span>
                )}
              </div>
            )}
            {!player && ev.requiresPlayer && <div className="text-xs text-stone-400 italic">Unknown player</div>}
            {!player && !ev.requiresPlayer && event.type !== 'OPP_GOAL' && event.type !== 'OPP_PEN_MISSED' && <div className="text-xs text-stone-400">No player</div>}
            {event.type === 'OPP_GOAL' && (
              <div className="mt-0.5">
                {event.gkFault === 'gk' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-red-500/15 text-red-700 border border-red-300">🧤 GK FAULT</span>
                )}
                {event.gkFault === 'unstoppable' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-stone-800 text-stone-300 border border-stone-700">😮 UNSTOPPABLE</span>
                )}
                {event.gkFault === 'own' && (() => {
                  const ogPlayer = roster.find(p => p.id === event.ownGoalById);
                  return (
                    <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-700 border border-amber-300">
                      🙃 OWN GOAL{ogPlayer ? ` · ${ogPlayer.name} #${ogPlayer.number}` : ''}
                    </span>
                  );
                })()}
                {!event.gkFault && (
                  <span className="text-[10px] text-stone-400 italic">unmarked</span>
                )}
              </div>
            )}
            {(event.zone || event.pressure || event.decision) && (
              <div className="mt-1 flex flex-wrap gap-1">
                {event.zone && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-stone-800 text-stone-200 border border-stone-700">
                    📍 {event.zone}
                  </span>
                )}
                {event.pressure === 'pressure' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-orange-900/40 text-orange-200 border border-orange-700">
                    ⚡ PRESSURE
                  </span>
                )}
                {event.pressure === 'open' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-lime-900/40 text-lime-200 border border-lime-700">
                    🆓 OPEN
                  </span>
                )}
                {event.decision === 'good' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-lime-900/40 text-lime-200 border border-lime-700">
                    🎯 GOOD
                  </span>
                )}
                {event.decision === 'forced' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-200 border border-amber-700">
                    🤔 FORCED
                  </span>
                )}
                {event.decision === 'bad' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-red-900/40 text-red-200 border border-red-700">
                    ❌ POOR
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </div>
      <div className={`text-xs tabular-nums shrink-0 ${onSeek ? 'text-lime-400 font-bold' : 'text-stone-400'}`}>
        {onSeek && <span className="mr-1">▶</span>}{formatClock(event.elapsed)} · P{event.period}
      </div>
      {onTag && (
        <button
          onClick={(e) => { e.stopPropagation(); onTag(event); }}
          className={`w-7 h-7 rounded-full flex items-center justify-center active:scale-95 shrink-0 text-[11px] font-extrabold tracking-wider ${event.zone || event.pressure || event.decision ? 'bg-lime-500/15 text-lime-300 border border-lime-700' : 'bg-stone-800 text-stone-400 border border-stone-700'}`}
          title="Tag zone / pressure / decision"
        >
          🏷
        </button>
      )}
      {onDelete && (
        <button onClick={(e) => { e.stopPropagation(); onDelete(event.id); }} className="w-7 h-7 rounded-full bg-red-500/10 flex items-center justify-center active:scale-95 shrink-0">
          <Trash2 className="w-3.5 h-3.5 text-red-600" />
        </button>
      )}
    </div>
  );
}

/* ---------- PILLAR MINI BAR ---------- */
function PillarMini({ label, value }) {
  const color = value >= 6 ? 'bg-lime-500' : value >= 3 ? 'bg-sky-400' : value >= 0 ? 'bg-stone-700' : 'bg-red-400';
  const width = Math.min(100, Math.max(5, (Math.abs(value) / 15) * 100));
  return (
    <div>
      <div className="text-[9px] font-bold tracking-wider text-stone-400 mb-0.5">{label}</div>
      <div className="h-2 bg-stone-900 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${width}%` }}></div>
      </div>
      <div className="text-[10px] font-display tabular-nums text-stone-300 mt-0.5">{value}</div>
    </div>
  );
}

// Percentile-vs-squad variant of PillarMini (plan 2.4 "vs Squad" view). `pct` is
// 0..100 or null (squad spread undefined → too few players / everyone equal).
// Bar fills to the percentile; color escalates with rank. Top/bottom quartiles
// get a plain-language tag so a coach reads strength/weakness without stats.
function PillarPct({ label, pct }) {
  if (pct == null) {
    return (
      <div>
        <div className="text-[9px] font-bold tracking-wider text-stone-400 mb-0.5">{label}</div>
        <div className="h-2 bg-stone-900 rounded-full overflow-hidden"></div>
        <div className="text-[10px] font-display tabular-nums text-stone-600 mt-0.5">—</div>
      </div>
    );
  }
  const color = pct >= 75 ? 'bg-lime-500' : pct >= 40 ? 'bg-sky-400' : 'bg-red-400';
  const tag = pct >= 90 ? `top ${100 - pct}%` : pct >= 75 ? `${pct}th` : pct <= 10 ? `bottom ${pct}%` : `${pct}th`;
  return (
    <div>
      <div className="text-[9px] font-bold tracking-wider text-stone-400 mb-0.5">{label}</div>
      <div className="h-2 bg-stone-900 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(4, pct)}%` }}></div>
      </div>
      <div className="text-[10px] font-display tabular-nums text-stone-300 mt-0.5">{tag}</div>
    </div>
  );
}

/* ---------- FIELD CALIBRATION (post-game analytics) ----------
 *
 * Coach taps the 4 corners of the pitch (TL, TR, BR, BL clockwise) on the
 * first frame of the uploaded 360° video. We compute a pixel→meters
 * homography and store it under `teams/main/fields/<name>` so every future
 * game on the same field re-uses it.
 *
 * The post_game/ Python pipeline reads this doc to convert detected player
 * pixels into real field coordinates (meters). See post_game/calibration.py.
 */

// Direct Linear Transform — solve 8 unknowns (h11..h32, with h33=1) from 4
// point correspondences. Returns a flat row-major 3x3 array, or null on
// degenerate input. No external deps.
function solveHomography4Point(srcPx, dstM) {
  if (!Array.isArray(srcPx) || srcPx.length !== 4 || !Array.isArray(dstM) || dstM.length !== 4) return null;
  // Build 8x9 augmented matrix [A | -b], then solve Ah = b for h (length 8).
  const A = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = srcPx[i];
    const [u, v] = dstM[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  // Gauss-Jordan elimination on 8x9.
  const n = 8;
  for (let col = 0; col < n; col++) {
    // partial pivot
    let pivot = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(A[r][col]) > Math.abs(A[pivot][col])) pivot = r;
    }
    if (Math.abs(A[pivot][col]) < 1e-10) return null; // singular
    if (pivot !== col) { const tmp = A[col]; A[col] = A[pivot]; A[pivot] = tmp; }
    const div = A[col][col];
    for (let c = col; c <= n; c++) A[col][c] /= div;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = A[r][col];
      if (f === 0) continue;
      for (let c = col; c <= n; c++) A[r][c] -= f * A[col][c];
    }
  }
  const h = A.map(row => row[n]);
  return [
    [h[0], h[1], h[2]],
    [h[3], h[4], h[5]],
    [h[6], h[7], 1],
  ];
}

function applyHomography(H, x, y) {
  const u = H[0][0] * x + H[0][1] * y + H[0][2];
  const v = H[1][0] * x + H[1][1] * y + H[1][2];
  const w = H[2][0] * x + H[2][1] * y + H[2][2];
  return [u / w, v / w];
}

const CORNER_LABELS = ['TOP-LEFT', 'TOP-RIGHT', 'BOTTOM-RIGHT', 'BOTTOM-LEFT'];
const CORNER_HINTS = [
  'Far-left corner of the pitch (your team\'s left as you face the field).',
  'Far-right corner of the pitch.',
  'Near-right corner closest to the camera.',
  'Near-left corner closest to the camera.',
];

// Build a small lime-green numbered pin sprite for the 3D scene.
function makeCalibPinSprite(THREE, idx) {
  const c = document.createElement('canvas');
  c.width = 64; c.height = 64;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#84cc16';
  ctx.strokeStyle = '#0c0a09';
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.arc(32, 32, 24, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#0c0a09';
  ctx.font = 'bold 30px Outfit, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(idx), 32, 35);
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, depthWrite: false, transparent: true });
  const s = new THREE.Sprite(mat);
  s.scale.set(28, 28, 1);
  s.renderOrder = 999;
  return s;
}

function FieldCalibrationModal({ videoUrl, onCancel, onSave }) {
  const containerRef = useRef(null);
  const cleanupRef = useRef(null);
  const sceneRef = useRef(null);
  const pinsRef = useRef([]); // [{ sprite, px, py }]
  const stateRef = useRef({
    yaw: 0, pitch: -0.25, fov: 90,
    targetYaw: 0, targetPitch: -0.25, targetFov: 90,
  });
  const lensFrontRef = useRef(true);
  const naturalSizeRef = useRef({ w: 0, h: 0 });

  const [ready, setReady] = useState(false);
  const [loadErr, setLoadErr] = useState(null);
  const [naturalSize, setNaturalSize] = useState({ w: 0, h: 0 });
  const [points, setPoints] = useState([]); // [{x, y}] in NATURAL equirect pixel coords
  const [lensFront, setLensFront] = useState(true);
  // Nominal U10 7v7 pitch, used only to give the 4-point homography a metric
  // frame. This is the LEGACY in-PWA calibrator: it emits no solver tag, so the
  // Run-Analysis quality gate rejects it (post_game/calibration_qc.py) and the
  // real calibration is done in the Streamlit UI, which asks the coach for the
  // measured field length and stores it per field. So these numbers never reach
  // a published metric — including for 9v9, where they would be wrong by ~1.5x
  // on width. Do NOT wire the match format in here to "fix" that; the fix is
  // that this path stays blocked.
  const LENGTH_M = 50;
  const WIDTH_M = 35;
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);

  // Swipe-back closes the modal (coordinated with nested modals + view stack).
  useModalHistory('calibrate', onCancel);
  // Lock body scroll so the page underneath keeps its position.
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    return () => {
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      window.scrollTo(0, scrollY);
    };
  }, []);

  // Front/Back lens toggle snaps camera yaw to that hemisphere.
  useEffect(() => {
    lensFrontRef.current = lensFront;
    stateRef.current.targetYaw = 0;
  }, [lensFront]);

  // Three.js scene: equirectangular sphere viewer with drag/pinch/tap.
  useEffect(() => {
    let cancelled = false;
    let raf = 0;
    loadThreeJS().then((THREE) => {
      if (cancelled || !containerRef.current) return;
      const container = containerRef.current;
      const W = container.clientWidth || 640;
      const H = container.clientHeight || 360;

      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(W, H);
      container.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      sceneRef.current = scene;
      const camera = new THREE.PerspectiveCamera(stateRef.current.fov, W / H, 0.1, 1100);
      camera.position.set(0, 0, 0.01);

      const geometry = new THREE.SphereGeometry(500, 64, 40);
      geometry.scale(-1, 1, 1);

      const video = document.createElement('video');
      video.crossOrigin = 'anonymous';
      video.playsInline = true;
      video.muted = true;
      video.preload = 'auto';
      // Mirror the TV mode 360 viewer: HLS (.m3u8) needs hls.js on non-Safari,
      // plain MP4 just sets src directly.
      let hlsCleanup = null;
      const isHls = /\.m3u8(\?|$)/i.test(videoUrl);
      if (isHls) {
        attachHls(video, videoUrl).then(fn => { hlsCleanup = fn; }).catch(() => {
          setLoadErr('Failed to load HLS stream. Try a different video.');
        });
      } else {
        video.src = videoUrl;
      }

      const texture = new THREE.VideoTexture(video);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      const sphere = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ map: texture }));
      scene.add(sphere);

      video.addEventListener('loadedmetadata', () => {
        const ns = { w: video.videoWidth, h: video.videoHeight };
        naturalSizeRef.current = ns;
        setNaturalSize(ns);
        try { video.currentTime = Math.min(0.5, (video.duration || 1) / 2); } catch (e) {}
      });
      video.addEventListener('seeked', () => setReady(true));
      video.addEventListener('error', () => setLoadErr('Video failed to load (CORS on R2 bucket?).'));

      // ---- Pointer: drag = rotate, pinch = zoom (FOV), tap = place pin ----
      const canvas = renderer.domElement;
      canvas.style.touchAction = 'none';
      canvas.style.cursor = 'crosshair';
      const pointers = new Map();
      let dragLast = null;
      let downAt = 0;
      let downPos = null;
      let movedFar = false;
      let pinchStartDist = 0;
      let pinchStartFov = 90;
      const dist = () => {
        const pts = [...pointers.values()];
        return pts.length < 2 ? 0 : Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      };
      canvas.addEventListener('pointerdown', (e) => {
        try { canvas.setPointerCapture(e.pointerId); } catch {}
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 1) {
          dragLast = { x: e.clientX, y: e.clientY };
          downAt = performance.now();
          downPos = { x: e.clientX, y: e.clientY };
          movedFar = false;
        } else if (pointers.size === 2) {
          dragLast = null;
          pinchStartDist = dist();
          pinchStartFov = stateRef.current.fov;
          movedFar = true;
        }
      });
      canvas.addEventListener('pointermove', (e) => {
        if (!pointers.has(e.pointerId)) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const st = stateRef.current;
        if (pointers.size === 1 && dragLast) {
          const dx = e.clientX - dragLast.x, dy = e.clientY - dragLast.y;
          if (downPos && Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y) > 8) movedFar = true;
          const s = 0.1 * (st.fov / 75) * Math.PI / 180;
          st.targetYaw -= dx * s;
          st.targetPitch += dy * s;
          st.targetPitch = Math.max(-Math.PI/2 + 0.05, Math.min(Math.PI/2 - 0.05, st.targetPitch));
          dragLast = { x: e.clientX, y: e.clientY };
        } else if (pointers.size === 2 && pinchStartDist > 0) {
          const ratio = pinchStartDist / dist();
          st.targetFov = Math.max(20, Math.min(110, pinchStartFov * ratio));
        }
      });
      const raycaster = new THREE.Raycaster();
      const placePin = (clientX, clientY) => {
        if (pinsRef.current.length >= 4) return;
        const ns = naturalSizeRef.current;
        if (!ns.w || !ns.h) return;
        const rect = renderer.domElement.getBoundingClientRect();
        const ndc = new THREE.Vector2(
          ((clientX - rect.left) / rect.width) * 2 - 1,
          -((clientY - rect.top) / rect.height) * 2 + 1,
        );
        raycaster.setFromCamera(ndc, camera);
        const hits = raycaster.intersectObject(sphere, false);
        if (!hits.length) return;
        const p = hits[0].point.clone();
        const r = p.length();
        const lat = Math.asin(p.y / r);
        const lon = Math.atan2(p.x, -p.z);
        // Standard equirect mapping: lon=0 → image x = W/2, lat=+π/2 → y=0.
        const px = ((lon + Math.PI) / (2 * Math.PI)) * ns.w;
        const py = ((Math.PI / 2 - lat) / Math.PI) * ns.h;
        const idx = pinsRef.current.length + 1;
        const sprite = makeCalibPinSprite(THREE, idx);
        // Park sprite a bit inside the sphere so it never z-fights.
        sprite.position.copy(p).multiplyScalar(0.85);
        scene.add(sprite);
        pinsRef.current.push({ sprite, px, py });
        setPoints(pinsRef.current.map(pp => ({ x: pp.px, y: pp.py })));
      };
      const endPointer = (e) => {
        const wasTap = pointers.size === 1 && !movedFar && (performance.now() - downAt) < 350;
        const tapPos = downPos;
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinchStartDist = 0;
        if (pointers.size === 0) {
          dragLast = null;
          if (wasTap && tapPos) placePin(tapPos.x, tapPos.y);
        } else if (pointers.size === 1) {
          const p = [...pointers.values()][0];
          dragLast = { x: p.x, y: p.y };
        }
      };
      canvas.addEventListener('pointerup', endPointer);
      canvas.addEventListener('pointercancel', endPointer);
      canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const st = stateRef.current;
        st.targetFov = Math.max(20, Math.min(110, st.targetFov + e.deltaY * 0.05));
      }, { passive: false });

      const onResize = () => {
        const w2 = container.clientWidth, h2 = container.clientHeight;
        if (!w2 || !h2) return;
        renderer.setSize(w2, h2);
        camera.aspect = w2 / h2;
        camera.updateProjectionMatrix();
      };
      window.addEventListener('resize', onResize);

      const tick = () => {
        const st = stateRef.current;
        st.yaw += (st.targetYaw - st.yaw) * 0.25;
        st.pitch += (st.targetPitch - st.pitch) * 0.25;
        st.fov += (st.targetFov - st.fov) * 0.25;
        camera.fov = st.fov;
        camera.updateProjectionMatrix();
        const baseYaw = lensFrontRef.current ? 0 : Math.PI;
        const Y = st.yaw + baseYaw;
        camera.lookAt(
          Math.sin(Y) * Math.cos(st.pitch),
          Math.sin(st.pitch),
          -Math.cos(Y) * Math.cos(st.pitch),
        );
        renderer.render(scene, camera);
        raf = requestAnimationFrame(tick);
      };
      tick();

      cleanupRef.current = () => {
        cancelAnimationFrame(raf);
        window.removeEventListener('resize', onResize);
        try { if (hlsCleanup) hlsCleanup(); } catch {}
        try { renderer.dispose(); } catch {}
        try { texture.dispose(); } catch {}
        try { geometry.dispose(); } catch {}
        try { container.removeChild(renderer.domElement); } catch {}
        try { video.pause(); video.removeAttribute('src'); video.load(); } catch {}
        sceneRef.current = null;
        pinsRef.current = [];
      };
    });
    return () => {
      cancelled = true;
      if (cleanupRef.current) cleanupRef.current();
    };
  }, [videoUrl]);

  const undo = () => {
    const last = pinsRef.current.pop();
    if (last && sceneRef.current) sceneRef.current.remove(last.sprite);
    setPoints(pinsRef.current.map(pp => ({ x: pp.px, y: pp.py })));
  };
  const reset = () => {
    if (sceneRef.current) pinsRef.current.forEach(p => sceneRef.current.remove(p.sprite));
    pinsRef.current = [];
    setPoints([]);
  };

  const canSave = points.length === 4;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setSaveErr(null);
    const src = points.map(p => [p.x, p.y]);
    const dst = [[0, 0], [LENGTH_M, 0], [LENGTH_M, WIDTH_M], [0, WIDTH_M]];
    const H = solveHomography4Point(src, dst);
    if (!H) {
      setSaveErr('Could not compute homography — corners may be collinear. Try again.');
      setSaving(false);
      return;
    }
    // Firestore does NOT allow nested arrays. Flatten 2D arrays to objects
    // ({p0:{x,y}…}) and 3×3 homography to a flat 9-element array.
    const srcObj = { p0: { x: src[0][0], y: src[0][1] }, p1: { x: src[1][0], y: src[1][1] }, p2: { x: src[2][0], y: src[2][1] }, p3: { x: src[3][0], y: src[3][1] } };
    const dstObj = { p0: { x: dst[0][0], y: dst[0][1] }, p1: { x: dst[1][0], y: dst[1][1] }, p2: { x: dst[2][0], y: dst[2][1] }, p3: { x: dst[3][0], y: dst[3][1] } };
    const Hflat = [H[0][0], H[0][1], H[0][2], H[1][0], H[1][1], H[1][2], H[2][0], H[2][1], H[2][2]];
    const calibration = {
      length_m: LENGTH_M,
      width_m: WIDTH_M,
      src_points_px: srcObj,
      dst_points_m: dstObj,
      homography_flat: Hflat,
      video_frame_w: Number(naturalSize.w) || 0,
      video_frame_h: Number(naturalSize.h) || 0,
      created_at: Date.now(),
    };
    try {
      onSave(calibration);
    } catch (err) {
      setSaveErr(String(err));
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-stretch sm:items-center justify-center p-0 sm:p-4"
      onClick={onCancel}
    >
      <div
        className="bg-stone-950 border border-stone-800 w-full sm:max-w-4xl sm:rounded-2xl overflow-hidden flex flex-col max-h-screen"
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between px-4 pb-3 stripes-bg text-white border-b border-stone-800"
          style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
        >
          <h3 className="font-display text-base truncate pr-3">🎯 FIELD CALIBRATION</h3>
          <button
            onClick={onCancel}
            className="shrink-0 h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95"
          >
            CLOSE ✕
          </button>
        </div>
        <div className="px-4 py-2 text-[11px] text-stone-300 border-b border-stone-800 flex items-center gap-2">
          <div className="flex-1 min-w-0">
            {points.length < 4 ? (
              <>Tap the <strong className="text-lime-400">{CORNER_LABELS[points.length]}</strong> corner. <span className="text-stone-500 hidden sm:inline">{CORNER_HINTS[points.length]}</span></>
            ) : (
              <span className="text-lime-400">All 4 corners marked. SAVE below.</span>
            )}
          </div>
          <div className="shrink-0 flex rounded-full bg-stone-900 border border-stone-700 p-0.5 text-[10px] font-display">
            <button
              onClick={() => setLensFront(true)}
              className={`px-2.5 py-1 rounded-full ${lensFront ? 'bg-lime-500 text-stone-950' : 'text-stone-300'}`}
            >FRONT</button>
            <button
              onClick={() => setLensFront(false)}
              className={`px-2.5 py-1 rounded-full ${!lensFront ? 'bg-lime-500 text-stone-950' : 'text-stone-300'}`}
            >BACK</button>
          </div>
        </div>
        <p className="px-4 pb-2 text-[10px] text-stone-500 border-b border-stone-800">
          Drag to rotate · pinch to zoom · tap to place a pin. Use FRONT/BACK if the field is behind you.
        </p>
        <div ref={containerRef} className="relative flex-1 bg-stone-900 min-h-[50vh] touch-none select-none">
          {!ready && !loadErr && (
            <div className="absolute inset-0 flex items-center justify-center text-stone-400 text-xs pointer-events-none">Loading 360° view…</div>
          )}
          {loadErr && (
            <div className="absolute inset-0 flex items-center justify-center text-red-400 text-xs px-6 text-center pointer-events-none">{loadErr}</div>
          )}
        </div>
        <div className="px-4 py-3 border-t border-stone-800 space-y-2 bg-stone-950">
          <div className="flex gap-2">
            <button onClick={undo} disabled={!points.length} className="flex-1 py-2 rounded-lg bg-stone-800 text-stone-300 text-xs font-bold active:scale-95 disabled:opacity-30">UNDO</button>
            <button onClick={reset} disabled={!points.length} className="flex-1 py-2 rounded-lg bg-stone-800 text-stone-300 text-xs font-bold active:scale-95 disabled:opacity-30">RESET</button>
          </div>
          <p className="text-[10px] text-stone-500 text-center">Nominal pitch 50 × 35 m. For accurate distances (and any 9v9 pitch), calibrate in the analysis UI, which uses the measured field length.</p>
          {saveErr && <p className="text-[10px] text-red-400 text-center">{saveErr}</p>}
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            className="w-full py-3 rounded-lg bg-lime-500 text-stone-950 text-sm font-bold active:scale-95 disabled:opacity-40"
          >
            {saving ? 'SAVING…' : `SAVE CALIBRATION${points.length < 4 ? ` (${points.length}/4 corners)` : ''}`}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- FILM ROOM ----------
 * Coach-only list of finished games sorted newest-first. Each row opens the
 * AnalyticsPanel for that game. Also the entry point for the (upcoming)
 * season-wide aggregate analytics view.
 */
// Loads the configured playlists from the worker. One entry per id:
// { id, title, items, status: 'loading'|'ok'|'error' }.
function useTrainingPlaylists() {
  const [playlists, setPlaylists] = useState(
    () => TRAINING_PLAYLISTS.map(id => ({ id, title: '', items: [], status: 'loading' }))
  );
  const load = (id) => {
    setPlaylists(prev => prev.map(p => p.id === id ? { ...p, status: 'loading' } : p));
    fetch(`${R2_UPLOAD_WORKER}/youtube-playlist?id=${encodeURIComponent(id)}`)
      .then(r => r.json().then(j => r.ok ? j : Promise.reject(j.error || 'load failed')))
      .then(data => setPlaylists(prev => prev.map(p => p.id === id
        ? { ...p, title: data.title || 'Training', items: data.items || [], status: 'ok' }
        : p)))
      .catch(() => setPlaylists(prev => prev.map(p => p.id === id ? { ...p, status: 'error' } : p)));
  };
  useEffect(() => { TRAINING_PLAYLISTS.forEach(load); }, []);
  return { playlists, reload: load };
}

// Full-screen Training hub: one tile per playlist → opens the shorts player.
function TrainingHub({ onBack }) {
  const { playlists, reload } = useTrainingPlaylists();
  const [open, setOpen] = useState(null); // the playlist object being viewed

  return (
    <div className="fixed inset-0 z-50 bg-stone-950 text-stone-100 overflow-y-auto" style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
      <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 bg-stone-950/90 backdrop-blur border-b border-stone-800">
        <span className="font-display text-base">🎬 Training Videos</span>
        <button onClick={onBack} className="h-9 px-3 rounded-full bg-white/10 hover:bg-white/20 text-xs flex items-center active:scale-95">‹ Back</button>
      </div>
      <div className="p-4 space-y-3 max-w-xl mx-auto">
        {playlists.map(pl => {
          const meta = _trainingMeta(pl.id);
          if (pl.status === 'error') {
            return (
              <div key={pl.id} className="rounded-2xl border border-stone-800 bg-stone-900 p-5 flex items-center justify-between">
                <span className="flex items-center gap-3 min-w-0">
                  <span className="text-2xl">{meta.icon}</span>
                  <span className="text-left min-w-0">
                    <span className="block font-display text-base text-white">{meta.label}</span>
                    <span className="block text-[11px] text-stone-400">Couldn't load</span>
                  </span>
                </span>
                <button onClick={() => reload(pl.id)} className="text-xs font-bold text-lime-400 bg-lime-500/10 hover:bg-lime-500/20 px-4 py-2 rounded-lg active:scale-95">RETRY</button>
              </div>
            );
          }
          const loading = pl.status !== 'ok';
          const sub = loading ? 'Loading…' : `${pl.items.length} video${pl.items.length === 1 ? '' : 's'}`;
          return (
            <button
              key={pl.id}
              disabled={loading || pl.items.length === 0}
              onClick={() => setOpen(pl)}
              className="w-full rounded-2xl p-4 flex items-center justify-between active:scale-[0.98] transition disabled:opacity-60"
              style={{ background: meta.gradient, border: `1px solid ${meta.border}` }}
            >
              <span className="flex items-center gap-3 min-w-0">
                <span className="text-3xl">{meta.icon}</span>
                <span className="text-left min-w-0">
                  <span className="block font-display text-lg text-white">{meta.label}</span>
                  <span className="block text-[11px] text-white/70">{sub}</span>
                </span>
              </span>
              <span className="text-white/70 shrink-0 text-lg">›</span>
            </button>
          );
        })}
      </div>
      {open && <TrainingShortsPlayer playlist={open} onBack={() => setOpen(null)} />}
    </div>
  );
}

// Portrait shorts player: swipe ←/→ or ↑/↓ (or ⏮/⏭) for next/prev — each new
// video AUTOPLAYS. ↻ replay. Uses a persistent YouTube IFrame-API player and
// loadVideoById() so autoplay survives navigation (an iframe remount with
// autoplay=1 gets re-gated by iOS on every change).
function TrainingShortsPlayer({ playlist, onBack }) {
  const items = playlist.items || [];
  const title = _trainingMeta(playlist.id).label || playlist.title || 'Training';
  const [idx, setIdx] = useState(0);
  const [paused, setPaused] = useState(false);
  const idxRef = useRef(0);
  const playerHostRef = useRef(null);
  const playerRef = useRef(null);
  const readyRef = useRef(false);
  const loadedIdxRef = useRef(-1);
  const touch = useRef(null);

  const go = (d) => setIdx(i => Math.min(items.length - 1, Math.max(0, i + d)));
  const replay = () => {
    const p = playerRef.current;
    if (p && p.seekTo) { p.seekTo(0, true); p.playVideo && p.playVideo(); }
  };
  const togglePlay = () => {
    const p = playerRef.current;
    if (!p || !p.getPlayerState) return;
    if (p.getPlayerState() === 1) p.pauseVideo(); else p.playVideo();
  };

  // Create the persistent player once.
  useEffect(() => {
    let cancelled = false;
    if (!items.length) return;
    loadYouTubeIframeApi().then((YT) => {
      if (cancelled || !playerHostRef.current || playerRef.current) return;
      playerRef.current = new YT.Player(playerHostRef.current, {
        videoId: items[0].videoId,
        playerVars: { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1, controls: 0, iv_load_policy: 3 },
        events: {
          onReady: (e) => {
            readyRef.current = true;
            loadedIdxRef.current = 0;
            try { const f = e.target.getIframe(); f.style.width = '100%'; f.style.height = '100%'; } catch (err) {}
            // If the user already navigated before the player was ready, sync.
            if (idxRef.current !== 0) { e.target.loadVideoById(items[idxRef.current].videoId); loadedIdxRef.current = idxRef.current; }
            else { e.target.playVideo && e.target.playVideo(); }
          },
          onStateChange: (e) => { setPaused(e.data === 2); }, // 2 = paused
        },
      });
    }).catch(() => {});
    return () => {
      cancelled = true;
      try { playerRef.current && playerRef.current.destroy && playerRef.current.destroy(); } catch (e) {}
      playerRef.current = null; readyRef.current = false;
    };
  }, []);

  // On navigation, load + autoplay the new video via the existing player.
  useEffect(() => {
    idxRef.current = idx;
    const p = playerRef.current;
    if (p && readyRef.current && loadedIdxRef.current !== idx && items[idx]) {
      p.loadVideoById(items[idx].videoId); // loadVideoById autoplays
      loadedIdxRef.current = idx;
    }
  }, [idx]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') go(1);
      else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') go(-1);
      else if (e.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [items.length]);

  const cur = items[idx];
  if (!cur) return null;

  // Gesture overlay sits ON TOP of the iframe (an iframe swallows touch events,
  // so an outer handler never sees swipes over the video). Tap = play/pause,
  // swipe ←/↑ = next, →/↓ = previous.
  const onTouchStart = (e) => { touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }; };
  const onTouchEnd = (e) => {
    if (!touch.current) return;
    const dx = e.changedTouches[0].clientX - touch.current.x;
    const dy = e.changedTouches[0].clientY - touch.current.y;
    touch.current = null;
    e.preventDefault(); // suppress the synthetic click so it doesn't double-fire
    const dist = Math.abs(dx) > Math.abs(dy) ? dx : dy;
    if (Math.abs(dist) < 45) { togglePlay(); return; } // tap
    go(dist < 0 ? 1 : -1);
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black flex flex-col">
      <div className="absolute top-0 inset-x-0 z-20 flex items-center justify-between px-3 gap-2" style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 8px)', paddingBottom: 8, background: 'linear-gradient(180deg,rgba(0,0,0,0.65),transparent)' }}>
        <span className="text-xs font-display truncate">{title}</span>
        <span className="text-[11px] bg-black/55 rounded-full px-2 py-0.5 shrink-0 tabular-nums">{idx + 1} / {items.length}</span>
        <button onClick={onBack} aria-label="Close" className="shrink-0 h-9 w-9 rounded-full bg-white/15 flex items-center justify-center active:scale-95"><X className="w-5 h-5" /></button>
      </div>

      <div className="flex-1 flex items-center justify-center min-h-0">
        <div className="relative h-full bg-black" style={{ aspectRatio: '9 / 16', maxWidth: '100%' }}>
          {/* Persistent player host — the IFrame API replaces this div with the iframe. */}
          <div className="absolute inset-0"><div ref={playerHostRef} className="w-full h-full" /></div>
          {/* Gesture layer: tap = play/pause, swipe = prev/next. */}
          <div
            className="absolute inset-0 z-10"
            onTouchStart={onTouchStart}
            onTouchEnd={onTouchEnd}
            onClick={togglePlay}
          />
          {paused && (
            <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
              <div className="h-16 w-16 rounded-full bg-black/45 flex items-center justify-center text-3xl">▶</div>
            </div>
          )}
        </div>
      </div>

      <div className="absolute bottom-0 inset-x-0 z-20 px-4 pt-8" style={{ paddingBottom: 'max(env(safe-area-inset-bottom, 0px), 12px)', background: 'linear-gradient(0deg,rgba(0,0,0,0.78),transparent)' }}>
        <div className="text-[12px] text-stone-200 mb-3 line-clamp-2">{cur.title}</div>
        <div className="flex items-center justify-center">
          <button onClick={replay} className="h-11 px-5 rounded-full bg-white/15 flex items-center gap-1 text-xs active:scale-95">↻ Replay</button>
        </div>
        <div className="text-center text-[10px] text-stone-400 mt-2">Swipe for next / previous · tap to pause</div>
      </div>
    </div>
  );
}

// Coach full-screen wrapper — reuses the same hub/player flow.
function TrainingVideosView({ onBack }) {
  return <TrainingHub onBack={onBack} />;
}

/* ---------- CONFIRM QUEUE (Phase 3.1) ----------
 * Shared post-game landing zone for everything that still needs a coach
 * decision: live BOOKMARKs (classify into a real event) and taggable events
 * missing zone / pressure / decision. Voice drafts (Phase 3.6) will land here
 * too once the voice pipeline exists. Each card cues the TV reel at the event
 * (via the pipeline's broadcastEvents index) and pre-selects the pipeline's
 * suggestedZone / suggestedPressure, so confirming is usually one tap.
 */

// Everything in one finished game that still needs a coach decision, in match
// order. tagsConfirmed/tagDismissed (set by the queue) drain it permanently.
function confirmQueueItemsForGame(game) {
  const items = [];
  for (const e of game.events || []) {
    if (e.type === 'BOOKMARK') { items.push({ game, event: e, kind: 'bookmark' }); continue; }
    const def = EVENT_TYPES[e.type];
    if (!def || def.silent) continue;
    if (e.tagsConfirmed || e.tagDismissed) continue;
    const needZone = EVENT_NEEDS_ZONE.has(e.type) && !e.zone;
    const needPressure = EVENT_NEEDS_PRESSURE.has(e.type) && !e.pressure;
    const needDecision = EVENT_NEEDS_DECISION.has(e.type) && !e.decision;
    if (needZone || needPressure || needDecision) items.push({ game, event: e, kind: 'tags' });
  }
  items.sort((a, b) => (a.event.period - b.event.period)
    || (a.event.elapsed - b.event.elapsed)
    || ((a.event.at || 0) - (b.event.at || 0)));
  return items;
}

/* ---------- COACH-LOG KICKOFF FORMATION ----------
 * The formation card's source of truth is the COACH BOARD AT KICKOFF — the
 * shape the coach set up — never tracked positions, and never the end-of-half
 * board (in-game drags mirror live play for identity anchoring, so the last
 * drag of a half is mid-action noise, not a formation). Computed client-side
 * from game.events so it's correct without a pipeline re-run; the pipeline's
 * formation.py mirrors the same definition for the analytics doc.
 */

// Exact 1-D 3-row split: rows are contiguous in depth, so try every pair of
// split points and keep the minimum within-row variance. Deterministic
// (no KMeans jitter); rows read defense → attack.
function formationLabelFromDepths(depths) {
  const xs = [...depths].sort((a, b) => a - b);
  const n = xs.length;
  if (n === 0) return null;
  if (n < 4) return `(${n} outfield)`;
  const varSum = (arr) => {
    const m = arr.reduce((s, v) => s + v, 0) / arr.length;
    return arr.reduce((s, v) => s + (v - m) * (v - m), 0);
  };
  let best = null;
  for (let i = 1; i < n - 1; i++) {
    for (let j = i + 1; j < n; j++) {
      const cost = varSum(xs.slice(0, i)) + varSum(xs.slice(i, j)) + varSum(xs.slice(j));
      if (!best || cost < best.cost) best = { cost, counts: [i, j - i, n - j] };
    }
  }
  return best.counts.join('-');
}

// Board state at wall-clock instant T: latest drag per player ≤ T (any
// period — the board persists), restricted to who was ON FIELD at T (lineup
// + subs ≤ T), GK at T excluded. A single instant is always a consistent
// board — mixing drags from different moments was the original mislabeling.
function boardLabelAt(game, T) {
  const events = [...(game.events || [])].sort((a, b) => (a.at || 0) - (b.at || 0));
  const on = new Set(game.startingLineup || []);
  let gk = game.gkPlayerId || null;
  const depthByPid = {};
  for (const e of events) {
    if ((e.at || 0) > T) break;
    if (e.type === 'SUB') {
      if (e.playerId) on.delete(e.playerId);
      if (e.subOnPlayerId) on.add(e.subOnPlayerId);
    } else if (e.type === 'GK_CHANGE' && e.playerId) {
      gk = e.playerId;
    } else if (e.type === 'POSITION' && typeof e.y === 'number') {
      depthByPid[e.playerId] = 1 - e.y; // board y=1 = own goal → depth from own goal
    }
  }
  const depths = Object.entries(depthByPid)
    .filter(([pid]) => on.has(pid) && pid !== gk)
    .map(([, d]) => d);
  if (depths.length < 4) return null;
  return formationLabelFromDepths(depths);
}

// Formation per period — COACH'S RULE (2026-06-11): RESET batches are THE
// reference. One reset → that board; several → every reset board votes
// (majority shape, earliest reset breaks ties); none → the dragged board at
// the period's last drag. Resets are slot-snapped board writes (≥4 near-
// simultaneous POSITION events), so raw drag coords never dilute them.
// A period with no board activity at all inherits the previous period.
function coachKickoffFormation(game, period) {
  const events = [...(game.events || [])].sort((a, b) => (a.at || 0) - (b.at || 0));
  const periodPos = events.filter(e => e.type === 'POSITION' && typeof e.y === 'number'
    && (e.period || 1) === period);
  // RESET/kickoff batches: runs of ≥4 near-simultaneous POSITION events.
  const batchEnds = [];
  let run = [];
  for (const e of periodPos) {
    if (run.length && (e.at || 0) - (run[run.length - 1].at || 0) <= 2) run.push(e);
    else run = [e];
    if (run.length === 4) batchEnds.push(run[3].at || 0);
    else if (run.length > 4) batchEnds[batchEnds.length - 1] = run[run.length - 1].at || 0;
  }
  if (batchEnds.length > 0) {
    const labels = batchEnds.map(T => boardLabelAt(game, T)).filter(Boolean);
    if (labels.length > 0) {
      const counts = {};
      let best = null;
      for (const l of labels) {
        counts[l] = (counts[l] || 0) + 1;
        if (!best || counts[l] > counts[best]) best = l; // earliest wins ties
      }
      return best;
    }
  }
  if (periodPos.length > 0) {
    const label = boardLabelAt(game, periodPos[periodPos.length - 1].at || 0);
    if (label) return label;
  }
  return period > 1 ? coachKickoffFormation(game, period - 1) : null;
}

// Tracklets still needing an identity decision: unassigned by the pipeline
// and not yet decided by the coach (identityOverrides is live on the game
// doc, so this count drains in real time as FIX IDS saves land).
function identityReviewCount(game, analyticsDoc) {
  const tl = (analyticsDoc && analyticsDoc.tracklets) || [];
  const ov = game.identityOverrides || {};
  return tl.filter(t => !t.player_id && !(String(t.tracklet_id) in ov)).length;
}

// The pipeline's broadcastEvents entry for an event — by id first (bookmark-
// confirmed events keep the bookmark's id), then by clock as a fallback.
// `idxOverride` is the index fetched from games/<id>/public/broadcast — the
// pipeline moved broadcastEvents OFF the game doc (2026-06-13), so the inline
// game.broadcastEvents fallback only fires for legacy, not-yet-re-run games.
function broadcastEntryFor(game, event, idxOverride) {
  const idx = idxOverride || game.broadcastEvents || [];
  return idx.find(b => b.id === event.id)
    || idx.find(b => b.period === event.period
        && b.elapsed != null && Math.abs(b.elapsed - event.elapsed) <= 2)
    || null;
}

// 3×3 zone selector (same field-reads-goal-up convention as TagSheet).
function ZoneGridMini({ value, onChange }) {
  return (
    <div className="grid grid-cols-3 grid-rows-3 gap-1.5">
      {['A', 'M', 'D'].flatMap(band =>
        ['L', 'C', 'R'].map(side => {
          const id = `${band}-${side}`;
          const isSel = value === id;
          const baseTone = band === 'A'
            ? 'bg-lime-900/40 border-lime-800 text-lime-200'
            : band === 'M'
            ? 'bg-stone-800 border-stone-700 text-stone-200'
            : 'bg-red-950/40 border-red-900 text-red-200';
          return (
            <button
              key={id}
              onClick={() => onChange(isSel ? null : id)}
              className={`rounded-lg border-2 ${baseTone}${isSel ? ' ring-2 ring-amber-400 ring-offset-2 ring-offset-stone-950' : ''} active:scale-[0.97] transition flex items-center justify-center py-2`}
            >
              <span className="text-[10px] tracking-widest font-bold">{ZONE_LABEL[id]}</span>
            </button>
          );
        })
      )}
    </div>
  );
}

// Event types a bookmark can become. OPP_GOAL is excluded on purpose — its
// fault/own-goal sub-flow only exists live; a missed opponent goal is an
// edit-the-score situation, not a queue classification.
const BOOKMARK_CLASSIFY_TYPES = [
  'GOAL', 'ASSIST', 'SHOT_ON', 'SHOT_OFF', 'KEY_PASS', 'GIVE_GO', 'GATES',
  'BALL_WIN', 'DUEL_WIN', 'SAVE', 'BLOCK', 'CLEAR', 'KICK_OUT',
  'FOUL_ON', 'PEN_AWARDED',
  'TURNOVER', 'HOLDS_BALL', 'DUEL_LOSE', 'FOUL_BY', 'PEN_CONCEDED',
];

// Per-game identity summary card: the queue's front door into the FIX IDS
// grid (the grid stays the tool — batch visual scanning beats card-by-card
// for dozens of tracklets).
function IdentityQueueCard({ item, onOpen, onSkip }) {
  const { game, count } = item;
  return (
    <div className="bg-stone-900 border border-stone-800 rounded-2xl overflow-hidden">
      <div className="px-4 py-3 border-b border-stone-800 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] text-stone-500 tracking-wider truncate">
            vs {game.opponent} · {formatDate(game.date)}
          </div>
          <div className="font-display text-lg leading-tight flex items-center gap-2">
            <span>🪪</span>
            <span className="truncate">IDENTITY REVIEW</span>
          </div>
          <div className="text-[11px] text-stone-400 tracking-wider">
            {count} tracked player{count === 1 ? '' : 's'} still unidentified
          </div>
        </div>
        <span className="shrink-0 inline-flex items-center justify-center min-w-[2rem] h-8 px-2 rounded-full bg-lime-500/15 text-lime-300 border border-lime-700 font-display text-base">{count}</span>
      </div>
      <div className="p-4 text-xs text-stone-400 leading-relaxed">
        The pipeline couldn't assign these tracklets to a player. Fixing them feeds
        minutes, distance and heatmaps to the right kids — worst-confidence first,
        with a video crop per tracklet. Saves apply on the next pipeline run.
      </div>
      <div className="p-4 pt-0 border-t-0 flex gap-2">
        <button
          onClick={onSkip}
          className="flex-1 py-3 rounded-xl bg-stone-950 text-stone-300 border border-stone-700 font-display text-sm active:scale-[0.97] transition"
        >SKIP FOR NOW</button>
        <button
          onClick={onOpen}
          className="flex-1 py-3 rounded-xl bg-lime-500 text-stone-950 font-display text-sm active:scale-[0.97] transition"
        >🪪 OPEN FIX IDS</button>
      </div>
    </div>
  );
}

function ConfirmQueueCard({ item, roster, onConfirm, onDismiss, onSkip, onCue, broadcastEvents }) {
  const { game, event, kind } = item;
  const def = EVENT_TYPES[event.type] || { emoji: '•', label: event.type };
  const player = roster.find(p => p.id === event.playerId);
  const entry = broadcastEntryFor(game, event, broadcastEvents);
  const canCue = !!(game.videoFullGameUrl && entry && entry.tvReelTimeS != null);

  // Bookmark classification state.
  const [type, setType] = useState(null);
  const [pickPlayerId, setPickPlayerId] = useState(null);

  // Tag state — pipeline suggestions pre-selected where the coach hasn't
  // tagged yet (the whole point of 3.3: confirm beats create).
  const needZone = EVENT_NEEDS_ZONE.has(event.type);
  const needPressure = EVENT_NEEDS_PRESSURE.has(event.type);
  const needDecision = EVENT_NEEDS_DECISION.has(event.type);
  const suggestedZone = (!event.zone && entry?.suggestedZone) || null;
  const suggestedPressure = (!event.pressure && entry?.suggestedPressure) || null;
  const [zone, setZone] = useState(event.zone || suggestedZone);
  const [pressure, setPressure] = useState(event.pressure || suggestedPressure);
  const [decision, setDecision] = useState(event.decision || null);

  const squadSet = game.squad && game.squad.length > 0 ? new Set(game.squad) : null;
  const squad = (squadSet ? roster.filter(p => squadSet.has(p.id)) : roster)
    .sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));

  const confirmDisabled = kind === 'bookmark' && !type;
  const handleConfirm = () => {
    if (kind === 'bookmark') onConfirm({ type, playerId: pickPlayerId });
    else onConfirm({
      ...(needZone ? { zone: zone || null } : {}),
      ...(needPressure ? { pressure: pressure || null } : {}),
      ...(needDecision ? { decision: decision || null } : {}),
      tagsConfirmed: true,
    });
  };

  return (
    <div className="bg-stone-900 border border-stone-800 rounded-2xl overflow-hidden">
      {/* Header: which game, which moment */}
      <div className="px-4 py-3 border-b border-stone-800 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] text-stone-500 tracking-wider truncate">
            vs {game.opponent} · {formatDate(game.date)}
          </div>
          <div className="font-display text-lg leading-tight flex items-center gap-2">
            <span>{def.emoji}</span>
            <span className="truncate">{def.label}</span>
          </div>
          <div className="text-[11px] text-stone-400 tracking-wider truncate">
            {player ? `${player.name} #${player.number}` : kind === 'bookmark' ? 'Tap-and-run bookmark' : 'No player'}
            {' · '}{formatClock(event.elapsed)} P{event.period}
          </div>
        </div>
        <span className={`shrink-0 text-[10px] font-extrabold tracking-wider px-2 py-1 rounded-full border ${kind === 'bookmark' ? 'bg-amber-500/15 text-amber-300 border-amber-700' : 'bg-stone-800 text-stone-300 border-stone-700'}`}>
          {kind === 'bookmark' ? '🔖 CLASSIFY' : '🏷 TAG'}
        </span>
      </div>

      {/* Cue the moment in the TV reel */}
      <div className="px-4 pt-3">
        <button
          onClick={canCue ? () => onCue(game, entry, def) : undefined}
          disabled={!canCue}
          className={`w-full flex items-center justify-center gap-2 font-display rounded-xl px-4 py-3 border-2 active:scale-[0.98] transition ${canCue ? 'bg-stone-800 border-stone-600 text-white' : 'bg-stone-900 border-stone-800 text-stone-600 cursor-not-allowed'}`}
        >
          <span>▶</span>
          <span>{canCue ? 'CUE VIDEO AT THIS MOMENT' : 'NO REEL FOR THIS MOMENT'}</span>
        </button>
      </div>

      <div className="p-4 space-y-4">
        {kind === 'bookmark' ? (
          <>
            <div>
              <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2">WHAT HAPPENED?</div>
              <div className="grid grid-cols-4 gap-1.5">
                {BOOKMARK_CLASSIFY_TYPES.map(id => {
                  const ev = EVENT_TYPES[id];
                  const isSel = type === id;
                  return (
                    <button
                      key={id}
                      onClick={() => setType(isSel ? null : id)}
                      className={`${TONE_CLASSES[ev.tone]} border-2 rounded-xl py-2 flex flex-col items-center justify-center gap-0.5 active:scale-[0.97] transition ${isSel ? 'ring-2 ring-amber-400 ring-offset-2 ring-offset-stone-950' : ''}`}
                    >
                      <span className="text-lg leading-none">{ev.emoji}</span>
                      <span className="font-sans-pro font-extrabold tracking-tight text-[9px] leading-none text-center">{ev.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div>
              <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2">WHO? <span className="text-stone-600">(optional)</span></div>
              <div className="grid grid-cols-4 gap-1.5">
                {squad.map(p => {
                  const isSel = pickPlayerId === p.id;
                  return (
                    <button
                      key={p.id}
                      onClick={() => setPickPlayerId(isSel ? null : p.id)}
                      className={`rounded-xl border-2 py-1.5 px-1 text-center active:scale-[0.97] transition ${isSel ? 'bg-lime-900/60 border-lime-500 text-lime-100' : 'bg-stone-950 border-stone-800 text-stone-300'}`}
                    >
                      <div className="font-display text-sm leading-none">#{p.number}</div>
                      <div className="text-[9px] truncate">{p.name.split(' ')[0]}</div>
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        ) : (
          <>
            {needZone && (
              <div>
                <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2 flex items-center justify-between">
                  <span>📍 ZONE</span>
                  {suggestedZone && zone === suggestedZone && (
                    <span className="text-[9px] text-sky-400 tracking-wider font-bold">✦ SUGGESTED FROM TRACKING</span>
                  )}
                </div>
                <ZoneGridMini value={zone} onChange={setZone} />
              </div>
            )}
            {needPressure && (
              <div>
                <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2 flex items-center justify-between">
                  <span>⚡ PRESSURE</span>
                  {suggestedPressure && pressure === suggestedPressure && (
                    <span className="text-[9px] text-sky-400 tracking-wider font-bold">✦ SUGGESTED FROM TRACKING</span>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setPressure(pressure === 'open' ? null : 'open')}
                    className={`rounded-xl border-2 py-3 font-display text-base active:scale-[0.97] transition ${pressure === 'open' ? 'bg-lime-900/60 border-lime-500 text-lime-100' : 'bg-stone-950 border-stone-800 text-stone-300'}`}
                  >🆓 OPEN</button>
                  <button
                    onClick={() => setPressure(pressure === 'pressure' ? null : 'pressure')}
                    className={`rounded-xl border-2 py-3 font-display text-base active:scale-[0.97] transition ${pressure === 'pressure' ? 'bg-orange-900/60 border-orange-500 text-orange-100' : 'bg-stone-950 border-stone-800 text-stone-300'}`}
                  >⚡ PRESSURE</button>
                </div>
              </div>
            )}
            {needDecision && (
              <div>
                <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2">🎯 DECISION</div>
                <div className="grid grid-cols-3 gap-2">
                  {[['good', '🎯 GOOD', 'bg-lime-900/60 border-lime-500 text-lime-100'],
                    ['forced', '🤔 FORCED', 'bg-amber-900/60 border-amber-500 text-amber-100'],
                    ['bad', '❌ POOR', 'bg-red-900/60 border-red-500 text-red-100']].map(([id, lbl, sel]) => (
                    <button
                      key={id}
                      onClick={() => setDecision(decision === id ? null : id)}
                      className={`rounded-xl border-2 py-3 font-display text-sm active:scale-[0.97] transition ${decision === id ? sel : 'bg-stone-950 border-stone-800 text-stone-300'}`}
                    >{lbl}</button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="p-4 border-t border-stone-800 flex gap-2">
        <button
          onClick={onDismiss}
          className="px-4 py-3 rounded-xl bg-red-950/40 text-red-300 border border-red-900 font-display text-sm active:scale-[0.97] transition"
          title={kind === 'bookmark' ? 'Delete this bookmark' : "Don't ask about this event again"}
        >🗑</button>
        <button
          onClick={onSkip}
          className="flex-1 py-3 rounded-xl bg-stone-950 text-stone-300 border border-stone-700 font-display text-sm active:scale-[0.97] transition"
        >SKIP FOR NOW</button>
        <button
          onClick={handleConfirm}
          disabled={confirmDisabled}
          className={`flex-1 py-3 rounded-xl font-display text-sm active:scale-[0.97] transition ${confirmDisabled ? 'bg-stone-800 text-stone-500 cursor-not-allowed' : 'bg-lime-500 text-stone-950'}`}
        >✓ CONFIRM</button>
      </div>
    </div>
  );
}

// Stable per-card key: event cards key on the event, identity summary cards
// on their game (they have no single event).
function queueItemKey(item) {
  return item.event ? item.event.id : `identity:${item.game.id}`;
}

function ConfirmQueueView({ items, roster, onClose, onUpdateEvent, onDeleteEvent, onConfirmBookmark }) {
  // Session-local skips: "ask me again next time", unlike dismiss (persisted).
  const [skippedIds, setSkippedIds] = useState(() => new Set());
  const [cue, setCue] = useState(null); // { game, entry, def }
  const [fixIds, setFixIds] = useState(null); // identity item whose FIX IDS grid is open

  // Swipe-back closes the queue; the cue player nests above it cleanly.
  useModalHistory('confirmQueue', onClose);

  // FIX IDS save path: direct game-doc write (same as AnalyticsPanel's
  // fallback) — the games snapshot listener syncs local state right back.
  const saveOverrides = async (gameId, overrides) => {
    if (!window.fbDb) return;
    await window.fbDb.collection('teams').doc('main').collection('games')
      .doc(gameId).update({ identityOverrides: overrides });
  };

  const remaining = items.filter(i => !skippedIds.has(queueItemKey(i)));
  const current = remaining[0] || null;

  // The pipeline moved the per-event reel index (broadcastEvents) OFF the game
  // doc into games/<id>/public/broadcast (2026-06-13), so game.broadcastEvents
  // is empty for re-run games and the cue button would always read "NO REEL".
  // Lazily fetch the subcollection for the game on screen (cached per id, with
  // a legacy on-doc fallback) — mirrors AnalyticsPanel's broadcast fetch.
  const [bEventsByGame, setBEventsByGame] = useState({}); // gameId -> events[]
  useEffect(() => {
    const g = current && current.game;
    if (!g || !g.id || bEventsByGame[g.id] != null) return;
    if (Array.isArray(g.broadcastEvents) && g.broadcastEvents.length) {
      setBEventsByGame(prev => ({ ...prev, [g.id]: g.broadcastEvents }));
      return;
    }
    if (!window.fbDb) return;
    window.fbDb.collection('teams').doc('main').collection('games').doc(g.id)
      .collection('public').doc('broadcast').get()
      .then(s => setBEventsByGame(prev => ({ ...prev, [g.id]: (s.exists && s.data().events) || [] })))
      .catch(() => setBEventsByGame(prev => ({ ...prev, [g.id]: [] })));
  }, [current && current.game && current.game.id]);

  const curBEvents = (current && current.game && bEventsByGame[current.game.id])
    || (current && current.game && current.game.broadcastEvents) || [];

  return (
    <div className="fixed inset-0 bg-stone-950 z-50 overflow-y-auto pb-10">
      <div
        className="stripes-bg text-white px-4 pb-3 flex items-center justify-between sticky top-0 z-10"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <button onClick={onClose} aria-label="Close" className="h-9 w-9 rounded-full bg-white/15 hover:bg-white/25 flex items-center justify-center active:scale-95">
          <X className="w-5 h-5" />
        </button>
        <h2 className="font-display text-lg">✅ CONFIRM QUEUE</h2>
        <div className="w-9 text-right font-display text-sm tabular-nums text-white/80">{remaining.length}</div>
      </div>

      <div className="px-4 pt-4 max-w-2xl mx-auto">
        {current && current.kind === 'identity' ? (
          <IdentityQueueCard
            key={queueItemKey(current)}
            item={current}
            onOpen={() => setFixIds(current)}
            onSkip={() => setSkippedIds(prev => new Set([...prev, queueItemKey(current)]))}
          />
        ) : current ? (
          <ConfirmQueueCard
            key={queueItemKey(current) + ':' + current.kind}
            item={current}
            roster={roster}
            broadcastEvents={curBEvents}
            onCue={(game, entry, def) => setCue({ game, entry, def, events: curBEvents })}
            onSkip={() => setSkippedIds(prev => new Set([...prev, queueItemKey(current)]))}
            onDismiss={() => {
              if (current.kind === 'bookmark') onDeleteEvent(current.game.id, current.event.id);
              else onUpdateEvent(current.game.id, current.event.id, { tagDismissed: true });
            }}
            onConfirm={(sel) => {
              if (current.kind === 'bookmark') onConfirmBookmark(current.game.id, current.event.id, sel);
              else onUpdateEvent(current.game.id, current.event.id, sel);
            }}
          />
        ) : (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-8 text-center">
            <div className="text-3xl mb-2">🎉</div>
            <div className="font-display text-xl mb-1">QUEUE CLEAR</div>
            <div className="text-sm text-stone-400">
              {skippedIds.size > 0
                ? `${skippedIds.size} skipped — they'll be back next visit.`
                : 'Every bookmark and tag is handled.'}
            </div>
            <button onClick={onClose} className="mt-4 px-6 py-2.5 rounded-xl bg-lime-500 text-stone-950 font-display active:scale-[0.97]">DONE</button>
          </div>
        )}
      </div>

      {cue && (
        <BroadcastVideoPlayer
          url={cue.game.videoFullGameUrl}
          doc={{
            broadcast_events: cue.events || cue.game.broadcastEvents || [],
            halfLengthMin: cue.game.halfLengthMin,
            home_name: cue.game.broadcastHomeName || 'Stompers',
            away_name: cue.game.broadcastAwayName || (cue.game.opponent || 'OPP'),
            home_color: cue.game.broadcastHomeColor || cue.game.homeColor,
            away_color: cue.game.broadcastAwayColor || cue.game.awayColor,
          }}
          label={`CUE — ${cue.def.label}`}
          timeKey="tvReelTimeS"
          startAtS={Math.max(0, (cue.entry.tvReelTimeS || 0) - 6)}
          onClose={() => setCue(null)}
        />
      )}

      {/* FIX IDS grid hosted by the queue: the batch identity tool stays the
          grid (card-by-card would be far slower for dozens of tracklets);
          the queue is just its front door. Saves write identityOverrides on
          the game doc → the identity card's count drains live. */}
      {fixIds && (
        <IdentityFixView
          doc={fixIds.doc}
          roster={roster}
          game={fixIds.game}
          onSave={(overrides) => saveOverrides(fixIds.game.id, overrides)}
          onClose={() => setFixIds(null)}
        />
      )}
    </div>
  );
}

function FilmRoomView({ games, roster, onBack, onUpdateEvent, onDeleteEvent, onConfirmBookmark, onConfirmVoiceDraft, onDismissVoiceDraft }) {
  const [openGameId, setOpenGameId] = useState(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const finished = useMemo(() => (
    (games || [])
      .filter(g => g.status === 'finished')
      .sort((a, b) => (b.date || '').localeCompare(a.date || '') || (b.endedAt || 0) - (a.endedAt || 0))
  ), [games]);
  const openGame = finished.find(g => g.id === openGameId) || null;

  // Analytics docs per game — the queue's identity cards need each game's
  // tracklet list. Fetched once per game-id set (NOT per games-state change:
  // confirming a tag re-renders games every time); offline persistence makes
  // repeat visits cheap.
  const [analyticsDocs, setAnalyticsDocs] = useState({});
  const finishedIdsKey = finished.map(g => g.id).join(',');
  useEffect(() => {
    if (!window.fbDb || finished.length === 0) return undefined;
    let cancelled = false;
    Promise.all(finished.map(g => (
      window.fbDb.collection('teams').doc('main')
        .collection('games').doc(g.id)
        .collection('analytics').doc('v1').get()
        .then(snap => [g.id, snap.exists ? snap.data() : null])
        .catch(() => [g.id, null])
    ))).then(results => {
      if (cancelled) return;
      const map = {};
      results.forEach(([id, d]) => { if (d) map[id] = d; });
      setAnalyticsDocs(map);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedIdsKey]);

  // Voice DRAFTS — the post-game narration pipeline writes extracted events to
  // each game doc's `voiceDrafts` field; production loads it straight onto the
  // game object (games listener spreads the whole doc). Accept/dismiss mutate it
  // via persistGames (parent handlers) in one atomic game-doc write — no
  // separate fetch/update needed.
  const voiceDraftsByGame = useMemo(() => {
    const m = {};
    for (const g of finished) if ((g.voiceDrafts || []).length) m[g.id] = g.voiceDrafts;
    return m;
  }, [finished]);
  const voiceDraftCount = useMemo(
    () => Object.values(voiceDraftsByGame).reduce((n, arr) => n + arr.length, 0),
    [voiceDraftsByGame]);

  // Identity summary cards (one per game with unresolved tracklets) lead the
  // queue — they're the batch task; the per-event cards follow. The count
  // updates live as FIX IDS saves land (identityOverrides on the game doc).
  const queueItems = useMemo(() => {
    const identity = finished
      .map(g => ({ game: g, kind: 'identity', doc: analyticsDocs[g.id], count: identityReviewCount(g, analyticsDocs[g.id]) }))
      .filter(i => i.count > 0);
    // Newest game first, match order within a game (finished is newest-first).
    return [...identity, ...finished.flatMap(confirmQueueItemsForGame)];
  }, [finished, analyticsDocs]);

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 pb-12">
      <div
        className="stripes-bg text-white px-4 pb-3 flex items-center justify-between"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <button onClick={onBack} aria-label="Back" className="h-9 w-9 rounded-full bg-white/15 hover:bg-white/25 flex items-center justify-center active:scale-95">
          <ChevronRight className="w-5 h-5 rotate-180" />
        </button>
        <h2 className="font-display text-lg">🎥 FILM ROOM</h2>
        <div className="w-9" />
      </div>

      <div className="px-4 pt-4 max-w-2xl mx-auto space-y-3">
        <div className="text-xs text-stone-500 uppercase tracking-wider">
          {finished.length} finished game{finished.length === 1 ? '' : 's'}
        </div>

        {/* CONFIRM QUEUE — bookmarks + untagged events across all games */}
        <button
          onClick={() => setQueueOpen(true)}
          disabled={queueItems.length === 0}
          className={`w-full bg-stone-900 border rounded-2xl p-4 flex items-center gap-3 transition ${queueItems.length === 0 ? 'border-stone-800 opacity-60 cursor-not-allowed' : 'border-amber-600/50 hover:border-amber-400/70 active:scale-[0.99]'}`}
        >
          <div className="w-10 h-10 rounded-lg bg-amber-500/15 text-amber-300 flex items-center justify-center text-xl">✅</div>
          <div className="flex-1 text-left">
            <div className="font-display text-base">CONFIRM QUEUE</div>
            <div className="text-xs text-stone-400">
              {queueItems.length === 0
                ? 'Nothing to review — bookmarks, tags and identity land here'
                : `${queueItems.length} to review · bookmarks · tags · identity`}
            </div>
          </div>
          {queueItems.length > 0 && (
            <span className="shrink-0 inline-flex items-center justify-center min-w-[2rem] h-8 px-2 rounded-full bg-amber-500 text-stone-950 font-display text-base">{queueItems.length}</span>
          )}
        </button>

        {/* VOICE DRAFTS — events extracted from the post-game narration, to confirm */}
        <button
          onClick={() => setVoiceOpen(true)}
          disabled={voiceDraftCount === 0}
          className={`w-full bg-stone-900 border rounded-2xl p-4 flex items-center gap-3 transition ${voiceDraftCount === 0 ? 'border-stone-800 opacity-60 cursor-not-allowed' : 'border-lime-600/50 hover:border-lime-400/70 active:scale-[0.99]'}`}
        >
          <div className="w-10 h-10 rounded-lg bg-lime-500/15 text-lime-300 flex items-center justify-center text-xl">🎙</div>
          <div className="flex-1 text-left">
            <div className="font-display text-base">VOICE DRAFTS</div>
            <div className="text-xs text-stone-400">
              {voiceDraftCount === 0
                ? 'Events extracted from your narration land here to confirm'
                : `${voiceDraftCount} draft${voiceDraftCount === 1 ? '' : 's'} from narration · tap to review`}
            </div>
          </div>
          {voiceDraftCount > 0 && (
            <span className="shrink-0 inline-flex items-center justify-center min-w-[2rem] h-8 px-2 rounded-full bg-lime-500 text-stone-950 font-display text-base">{voiceDraftCount}</span>
          )}
        </button>

        {/* SEASON ANALYTICS used to be a button here, opening a modal with its own
            per-player table — a season roll-up buried inside a per-GAME screen,
            duplicating the one on the Home grid. Both now live in the single STATS
            destination (Home → STATS). Film Room keeps video and review.

            The game list below still opens AnalyticsPanel, and so does STATS →
            GAMES. That is deliberate: the panel is where the TV reel and the
            highlights live, so the video route needs it too. One panel reached from
            two places is not the duplication that was the problem — that was two
            DIFFERENT per-player tables of the same season. */}

        {finished.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
            No finished games yet. Reels and review items show up here after you end a match.
          </div>
        ) : (
          <div className="space-y-2">
            {finished.map(g => {
              const result = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
              const resultClass = result === 'W' ? 'bg-lime-500/15 text-lime-300 border-lime-500/40'
                               : result === 'L' ? 'bg-red-500/15 text-red-300 border-red-500/40'
                                                : 'bg-stone-500/15 text-stone-300 border-stone-500/40';
              const pendingCount = queueItems.filter(i => i.game.id === g.id).length;
              return (
                <button
                  key={g.id}
                  onClick={() => setOpenGameId(g.id)}
                  className="w-full bg-stone-900 border border-stone-800 hover:border-lime-500/40 rounded-2xl p-3 flex items-center gap-3 active:scale-[0.99] transition"
                >
                  <span className={`shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-lg border font-display text-base ${resultClass}`}>{result}</span>
                  <div className="flex-1 min-w-0 text-left">
                    <div className="font-bold text-sm truncate">vs {g.opponent}</div>
                    <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                      <GameTypeChip value={gameTypeOf(g)} />
                      <TournamentChip value={g.tournament} gameType={gameTypeOf(g)} />
                      <FormatChip value={g.format} />
                      <span>{formatDate(g.date)}</span>
                      {pendingCount > 0 && (
                        <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-700">
                          ✅ {pendingCount} to review
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-display text-xl tabular-nums leading-none">{g.ourScore}<span className="text-stone-500 mx-0.5">–</span>{g.oppScore}</div>
                    <div className="text-[10px] text-lime-400 mt-1 font-bold tracking-wider">🎬 OPEN</div>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        <div className="text-[10px] text-stone-600 leading-snug px-1 pt-1">
          Looking for numbers? Season and per-player stats live in{' '}
          <span className="text-stone-400 font-bold">STATS</span> on the home screen.
        </div>
      </div>

      {openGame && (
        <AnalyticsPanel
          game={openGame}
          roster={roster}
          onClose={() => setOpenGameId(null)}
          onSeekVideo={() => setOpenGameId(null)}
        />
      )}

      {queueOpen && (
        <ConfirmQueueView
          items={queueItems}
          roster={roster}
          onClose={() => setQueueOpen(false)}
          onUpdateEvent={onUpdateEvent}
          onDeleteEvent={onDeleteEvent}
          onConfirmBookmark={onConfirmBookmark}
        />
      )}

      {voiceOpen && (
        <VoiceDraftsQueue
          draftsByGame={voiceDraftsByGame}
          games={finished}
          roster={roster}
          onClose={() => setVoiceOpen(false)}
          onAccept={(gameId, draft, playerId) => onConfirmVoiceDraft(gameId, draft, playerId)}
          onDismiss={(gameId, draftId) => onDismissVoiceDraft(gameId, draftId)}
        />
      )}
    </div>
  );
}

/* Voice-drafts confirm queue: events the post-game narration pipeline extracted
 * (game doc `voiceDrafts`), grouped by game. Each row lets the coach set/confirm
 * the player and ACCEPT (→ a real 'voice-confirmed' event) or DISMISS. Voice
 * complements the live log — these are mostly the granular events (ball-wins,
 * corners, fouls) the coach narrated but didn't tap. */
function VoiceDraftsQueue({ draftsByGame, games, roster, onClose, onAccept, onDismiss }) {
  const rows = useMemo(() => (games || [])
    .filter(g => (draftsByGame[g.id] || []).length)
    .flatMap(g => [...(draftsByGame[g.id] || [])]
      .sort((a, b) => (a.period - b.period) || (a.elapsed - b.elapsed))
      .map(d => ({ game: g, draft: d }))), [games, draftsByGame]);

  return (
    <div className="fixed inset-0 z-50 bg-stone-950/95 flex flex-col" style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
      <div className="stripes-bg text-white px-4 py-3 flex items-center justify-between shrink-0">
        <h2 className="font-display text-lg">🎙 VOICE DRAFTS</h2>
        <button onClick={onClose} className="h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-sm active:scale-95">DONE</button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4 max-w-2xl mx-auto w-full space-y-2">
        {rows.length === 0 ? (
          <div className="text-center text-sm text-stone-400 py-10">All voice drafts reviewed. 🎉</div>
        ) : (
          <>
            <div className="text-xs text-stone-500 mb-1">
              Extracted from your narration — mostly the granular events you didn't tap live.
              Set the player if needed, then ACCEPT or DISMISS.
            </div>
            {rows.map(({ game, draft }) => (
              <VoiceDraftRow
                key={`${game.id}_${draft.id}`}
                game={game} draft={draft} roster={roster}
                onAccept={onAccept} onDismiss={onDismiss}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function VoiceDraftRow({ game, draft, roster, onAccept, onDismiss }) {
  const [pid, setPid] = useState(draft.playerId || '');
  const ev = EVENT_TYPES[draft.type] || { emoji: '•', label: draft.type };
  return (
    <div className="bg-stone-900 border border-stone-800 rounded-2xl p-3">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-lg">{ev.emoji}</span>
        <span className="font-display">{ev.label}</span>
        <span className="text-stone-500 tabular-nums">{formatClock(draft.elapsed)} · P{draft.period}</span>
        <span className="ml-auto text-[10px] text-stone-500">vs {game.opponent}</span>
      </div>
      {draft.quote && <div className="mt-1 text-xs text-stone-400 italic">“{draft.quote}”</div>}
      <div className="mt-2 flex items-center gap-2">
        <select
          value={pid}
          onChange={e => setPid(e.target.value)}
          className="flex-1 bg-stone-800 border border-stone-700 rounded-lg px-2 py-1.5 text-sm"
        >
          <option value="">— no player —</option>
          {roster.map(p => <option key={p.id} value={p.id}>#{p.number} {p.name}</option>)}
        </select>
        <button
          onClick={() => onAccept(game.id, draft, pid || null)}
          className="px-3 py-1.5 rounded-lg bg-lime-500 text-stone-950 font-display text-sm active:scale-95"
        >ACCEPT</button>
        <button
          onClick={() => onDismiss(game.id, draft.id)}
          className="px-3 py-1.5 rounded-lg bg-stone-800 border border-stone-700 text-stone-300 text-sm active:scale-95"
        >DISMISS</button>
      </div>
    </div>
  );
}

/* ---------- SEASON AGGREGATION ----------
 * Aggregates per-game analytics summary docs into season-to-date and last-N
 * rollups. Best practice for U10 (15-25 game season): show both SEASON and
 * LAST 5 in a tab toggle so coaches compare all-time vs. recent form with one
 * tap. Last 3 is too noisy (one outlier swings 33%); last 10 overlaps
 * season-to-date.
 *
 * This used to be a modal (`SeasonAnalyticsView`) opened from inside FILM ROOM —
 * a per-GAME screen — which is how the season roll-up came to be hidden two taps
 * deep behind a video browser while a second, different season screen sat on the
 * Home grid. It is now a hook consumed by the SEASON and TEAM tabs of the single
 * STATS destination. See STATS_CONSOLIDATION_PLAN.md.
 */
const ROLLING_WINDOW = 5;

// Fetches every finished game's analytics summary and derives the season/rolling
// window plus the per-player aggregate. One fetch serves all three STATS tabs.
function useSeasonAnalytics(games, mode) {
  const [docs, setDocs] = useState({}); // gameId -> analytics doc
  const [loading, setLoading] = useState(true);

  // Fetch the SUMMARY doc for every finished game in parallel — not analytics/v1.
  //
  // This view fans out over every game at once, and the full docs run 420-970 KB
  // each: ~5 MB across seven games, of which ~3.4 MB is `identity_assignments`,
  // a per-track array this view never reads (it touches player_stats and nothing
  // else). That download plus the main-thread JSON parse is what made the view
  // open to a black screen on a phone, and it grew with every game played.
  // analytics/summary carries the same fields at ~2.5 KB — 0.018 MB total.
  //
  // Falls back to v1 per-game if a summary is missing, so a game analysed by an
  // older pipeline still appears (slowly) rather than vanishing from the season.
  useEffect(() => {
    if (!window.fbDb || !games || games.length === 0) { setLoading(false); return; }
    let cancelled = false;
    Promise.all(games.map(g => {
      const analytics = window.fbDb.collection('teams').doc('main')
        .collection('games').doc(g.id).collection('analytics');
      return analytics.doc('summary').get()
        .then(snap => snap.exists
          ? [g.id, snap.data()]
          : analytics.doc('v1').get().then(f => [g.id, f.exists ? f.data() : null]))
        .catch(() => [g.id, null]);
    })).then(results => {
      if (cancelled) return;
      const map = {};
      results.forEach(([id, d]) => { if (d) map[id] = d; });
      setDocs(map);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [games]);

  // Games (newest-first) that actually have analytics docs.
  const gamesWithAnalytics = useMemo(() => (
    (games || []).filter(g => docs[g.id])
  ), [games, docs]);

  // Window: SEASON = all, ROLLING = newest-first slice of ROLLING_WINDOW.
  // Auto-fall-back to SEASON if fewer than ROLLING_WINDOW games available.
  const windowGames = useMemo(() => {
    if (mode === 'rolling' && gamesWithAnalytics.length >= ROLLING_WINDOW) {
      return gamesWithAnalytics.slice(0, ROLLING_WINDOW);
    }
    return gamesWithAnalytics;
  }, [mode, gamesWithAnalytics]);

  const fellBackToSeason = mode === 'rolling' && gamesWithAnalytics.length < ROLLING_WINDOW;

  // gameId -> playerId -> tagged click stats, for the games the coach tagged.
  //
  // ⚠ Skips games with `oriented: false`. Teams switch ends at half time, so
  // depth figures are only comparable once each half is flipped into a common
  // frame; when the keeper's median sits mid-pitch the pipeline REFUSES to guess
  // the orientation rather than risk mirroring a whole half. Such a game's depth
  // and thirds are in an undefined frame, and averaging it into a season figure
  // would silently mirror half its contribution. Excluded, not blended.
  const clickByGame = useMemo(() => {
    const out = {};
    Object.entries(docs).forEach(([gid, d]) => {
      const cs = (d && d.click_stats) || null;
      if (!cs || cs.oriented === false) return;
      const players = cs.players || [];
      if (players.length) {
        out[gid] = Object.fromEntries(players.map(p => [p.player_id, p]));
      }
    });
    return out;
  }, [docs]);

  // Per-player season aggregate: MINUTES (from the coach's SUB taps) and thirds
  // occupancy from TAGGED games only.
  //
  // Distance, sprints and work-rate columns were removed here for the same reason
  // as on the per-game card: they aggregate the tracked per-player source, which
  // carries ~23% wrong-child contamination, so a season mean is a more confident
  // presentation of the same wrong number. A sortable table made it worse — the
  // top row read as "hardest worker of the season".
  const playerAgg = useMemo(() => {
    const byPid = new Map();
    // Iterate oldest-first so series sparkline reads left-to-right chronologically.
    [...windowGames].reverse().forEach(g => {
      const stats = docs[g.id]?.player_stats || [];
      stats.forEach(s => {
        const pid = s.player_id;
        if (!pid) return;
        let row = byPid.get(pid);
        if (!row) {
          row = { pid, games: 0, tagged: 0, minutes: 0,
                  attPct: 0, midPct: 0, defPct: 0, minSeries: [] };
          byPid.set(pid, row);
        }
        row.games += 1;
        row.minutes += s.minutes_played || 0;
        row.minSeries.push(s.minutes_played || 0);
        // Thirds occupancy comes from TAGGED positions when the game was tagged,
        // and is skipped entirely when it wasn't. Averaging a tagged game's
        // thirds together with a tracked game's would blend a ±1.7 m measurement
        // into a ~23%-contaminated one and report the mean as a season figure.
        const cp = clickByGame[g.id] && clickByGame[g.id][pid];
        if (cp) {
          row.tagged += 1;
          row.attPct += cp.pct_attacking_third || 0;
          row.midPct += cp.pct_middle_third || 0;
          row.defPct += cp.pct_defensive_third || 0;
        }
      });
    });
    return [...byPid.values()].map(r => ({
      ...r,
      avgMin: r.games ? r.minutes / r.games : 0,
      // Averaged over TAGGED games only, hence the separate denominator.
      avgAttPct: r.tagged ? r.attPct / r.tagged : null,
      avgMidPct: r.tagged ? r.midPct / r.tagged : null,
      avgDefPct: r.tagged ? r.defPct / r.tagged : null,
    }));
  }, [windowGames, docs, clickByGame]);

  // Team rollup
  const teamAgg = useMemo(() => {
    let gf = 0, ga = 0, w = 0, d = 0, l = 0, cleanSheets = 0;
    windowGames.forEach(g => {
      gf += g.ourScore || 0;
      ga += g.oppScore || 0;
      if (g.ourScore > g.oppScore) w++;
      else if (g.ourScore < g.oppScore) l++;
      else d++;
      if ((g.oppScore || 0) === 0) cleanSheets++;
    });
    return { games: windowGames.length, gf, ga, gd: gf - ga, w, d, l, cleanSheets };
  }, [windowGames]);

  // playerAgg is keyed by pid so the STATS table can merge it against the
  // event-derived stats that view computes itself. The two sources stay separate
  // all the way to the cell: never summed, averaged together, or substituted for
  // one another.
  const aggByPid = useMemo(
    () => Object.fromEntries(playerAgg.map(r => [r.pid, r])), [playerAgg]);

  return { docs, loading, gamesWithAnalytics, windowGames, fellBackToSeason,
           clickByGame, playerAgg, aggByPid, teamAgg };
}

/* TEAM tab — record, goals and the shot map for the selected window, plus a
 * pointer to the per-game team shape. Every number here comes from the coach's
 * taps or from team-level shape; none of it is per-player tracking. */
function SeasonTeamTab({ season, mode }) {
  const { teamAgg, windowGames } = season;
  return (
    <div className="space-y-3">
      <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
        <div className="text-xs text-stone-500 uppercase mb-3">
          Record — {mode === 'season' ? 'season' : `last ${windowGames.length}`}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div><div className="text-2xl font-display tabular-nums text-lime-400">{teamAgg.w}</div><div className="text-[10px] text-stone-500 uppercase">Wins</div></div>
          <div><div className="text-2xl font-display tabular-nums text-stone-300">{teamAgg.d}</div><div className="text-[10px] text-stone-500 uppercase">Draws</div></div>
          <div><div className="text-2xl font-display tabular-nums text-red-400">{teamAgg.l}</div><div className="text-[10px] text-stone-500 uppercase">Losses</div></div>
        </div>
        <div className="grid grid-cols-4 gap-2 text-center mt-4 pt-3 border-t border-stone-800">
          <div><div className="text-lg font-display tabular-nums">{teamAgg.gf}</div><div className="text-[10px] text-stone-500 uppercase">GF</div></div>
          <div><div className="text-lg font-display tabular-nums">{teamAgg.ga}</div><div className="text-[10px] text-stone-500 uppercase">GA</div></div>
          <div><div className={`text-lg font-display tabular-nums ${teamAgg.gd > 0 ? 'text-lime-400' : teamAgg.gd < 0 ? 'text-red-400' : ''}`}>{teamAgg.gd > 0 ? '+' : ''}{teamAgg.gd}</div><div className="text-[10px] text-stone-500 uppercase">GD</div></div>
          <div><div className="text-lg font-display tabular-nums">{teamAgg.cleanSheets}</div><div className="text-[10px] text-stone-500 uppercase">CS</div></div>
        </div>
      </section>

      <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
        <div className="text-xs text-stone-500 uppercase mb-2">Shots</div>
        <ShotMap games={windowGames} />
      </section>

      <div className="text-[10px] text-stone-600 leading-snug px-1">
        Per-game team shape — field tilt, compactness, momentum — sits on each game
        in the GAMES tab. Those are team-level measurements, so they survive the
        identity problem that retired the per-player movement numbers.
      </div>
    </div>
  );
}

/* GAMES tab — one row per finished game, opening that game's analytics panel.
 *
 * This list used to be FILM ROOM's job, which is why the per-game analytics deck
 * ended up behind a video browser. Film Room keeps the video; the numbers live
 * here, next to the season numbers they belong with. */
function SeasonGamesTab({ games, tagged, onOpenGame }) {
  if (!games.length) {
    return (
      <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
        No finished games yet. They appear here once you end a match.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {games.map(g => {
        const result = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
        const resultClass = result === 'W' ? 'bg-lime-500/15 text-lime-300 border-lime-500/40'
          : result === 'L' ? 'bg-red-500/15 text-red-300 border-red-500/40'
            : 'bg-stone-500/15 text-stone-300 border-stone-500/40';
        return (
          <button
            key={g.id}
            onClick={() => onOpenGame(g.id)}
            className="w-full bg-stone-900 border border-stone-800 hover:border-lime-500/40 rounded-2xl p-3 flex items-center gap-3 active:scale-[0.99] transition"
          >
            <span className={`shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-lg border font-display text-base ${resultClass}`}>{result}</span>
            <div className="flex-1 min-w-0 text-left">
              <div className="font-bold text-sm truncate">vs {g.opponent}</div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                <GameTypeChip value={gameTypeOf(g)} />
                <TournamentChip value={g.tournament} gameType={gameTypeOf(g)} />
                <FormatChip value={g.format} />
                <span>{formatDate(g.date)}</span>
                {tagged[g.id] && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-lime-500/15 text-lime-300 border border-lime-700">
                    🖱 TAGGED
                  </span>
                )}
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="font-display text-xl tabular-nums leading-none">{g.ourScore}<span className="text-stone-500 mx-0.5">–</span>{g.oppScore}</div>
              <div className="text-[10px] text-lime-400 mt-1 font-bold tracking-wider">📊 OPEN</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/* SEASON tab — ONE per-player table, replacing the two that used to exist on
 * separate screens (both showing GP and MIN, neither acknowledging the other).
 *
 * Columns are grouped by SOURCE with a visible divider, because the two groups do
 * not have the same standing: the left comes from the coach's own taps, the right
 * from positions he tagged himself. They are never combined into one number, and
 * a player with no tagged games shows "—" rather than a tracked substitute — the
 * whole point of the consolidation. See METRICS_INVENTORY.md. */
function SeasonPlayersTab({ rows, sortKey, setSortKey, onOpenPlayer, nTagged }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between px-1">
        <div className="text-[10px] text-stone-500 uppercase tracking-wider">Sort</div>
        <div className="flex gap-1 text-[10px]">
          {[['score', 'SCORE'], ['avgMin', 'MIN'], ['goals', 'G'], ['name', 'A-Z']].map(([k, label]) => (
            <button
              key={k}
              onClick={() => setSortKey(k)}
              className={`px-1.5 py-0.5 rounded font-bold ${sortKey === k ? 'bg-lime-500 text-stone-950' : 'bg-stone-800 text-stone-400 hover:text-stone-200'}`}
            >{label}</button>
          ))}
        </div>
      </div>

      <div className="bg-stone-900 border border-stone-800 rounded-2xl overflow-hidden">
        {/* Source banner: the columns below mean different things and the coach
            must be able to see which is which without reading a footnote. */}
        <div className="grid grid-cols-[1fr_9.5rem] text-[9px] font-bold tracking-wider">
          <div className="px-3 py-1.5 bg-stone-800/60 text-stone-300">FROM YOUR TAPS</div>
          <div className="px-2 py-1.5 bg-lime-500/10 text-lime-300 border-l border-lime-800/60">FROM YOUR TAGS</div>
        </div>
        <div className="grid grid-cols-[1.75rem_1fr_1.75rem_2.25rem_1.5rem_1.5rem_2.5rem_3rem_6.5rem] gap-1 px-3 py-2 text-[9px] font-bold tracking-wider text-stone-400 border-b border-stone-800">
          <div>#</div>
          <div>PLAYER</div>
          <div className="text-center">GP</div>
          <div className="text-center">MIN</div>
          <div className="text-center">G</div>
          <div className="text-center">A</div>
          <div className="text-center">SCORE</div>
          <div className="text-center border-l border-lime-800/60">TAGGED</div>
          <div className="text-center">WHERE HE PLAYED</div>
        </div>
        <div className="divide-y divide-stone-800">
          {rows.map(r => (
            <button
              key={r.pid}
              onClick={() => onOpenPlayer(r.pid)}
              className="w-full grid grid-cols-[1.75rem_1fr_1.75rem_2.25rem_1.5rem_1.5rem_2.5rem_3rem_6.5rem] gap-1 px-3 py-3 items-center text-left active:bg-stone-950 transition"
            >
              <PlayerAvatar player={r.player} sizeClass="w-7 h-7" textSize="text-xs" numberClasses="bg-stone-900 text-stone-100" />
              <div className="min-w-0">
                <div className="font-bold text-xs truncate">{r.player?.name || r.pid}</div>
                {r.player?.position && <div className="text-[9px] text-stone-500 font-bold tracking-wider">{r.player.position}</div>}
              </div>
              <div className="text-center font-display text-xs tabular-nums text-stone-200">{r.gamesPlayed}</div>
              <div className="text-center font-display text-xs tabular-nums text-sky-700">{r.minutes}</div>
              <div className="text-center font-display text-xs tabular-nums text-lime-700">{r.goals}</div>
              <div className="text-center font-display text-xs tabular-nums text-stone-200">{r.assists}</div>
              {/* SCORE, with a logging-coverage dot. The score is a rate over the
                  events the coach managed to tap, and DEF logging ran as low as
                  15% of minutes on some games — so a thin score must not look as
                  firm as a well-logged one. Amber under 50%, red under 30%. */}
              <div className={`text-center font-display text-sm tabular-nums relative ${r.score >= 6 ? 'text-lime-600' : r.score >= 3 ? 'text-stone-100' : 'text-stone-400'}`}>
                {r.score}
                {r.scoreCoverage != null && r.scoreCoverage < 50 && (
                  <span
                    title={`Only ~${Math.round(r.scoreCoverage)}% of his minutes carried event logging — treat this score as provisional`}
                    className={`absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full ${r.scoreCoverage < 30 ? 'bg-red-500' : 'bg-amber-400'}`}
                  />
                )}
              </div>
              <div className="text-center font-display text-xs tabular-nums border-l border-lime-800/60 text-stone-400">
                {r.tagged ? `${r.tagged}/${r.gamesWithDocs}` : '—'}
              </div>
              <div>
                {r.avgDefPct == null ? (
                  <div className="text-[9px] text-stone-600 text-center">not tagged</div>
                ) : (
                  <ThirdsBar def={r.avgDefPct} mid={r.avgMidPct} att={r.avgAttPct} />
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="text-[10px] text-stone-600 leading-snug px-1">
        <span className="text-stone-400">Left of the divider</span> comes from your
        taps during the game — exact. <span className="text-lime-600">Right of it</span>
        {' '}comes from the moments you tagged in the film-room sampler, accurate to
        about ±1.7 m, averaged over tagged games only.
        {nTagged === 0
          ? ' No games tagged yet — about 10 minutes of tagging per game fills that side in.'
          : ` ${nTagged} game${nTagged === 1 ? '' : 's'} tagged so far.`}
        {' '}Tap a player for his full breakdown.
      </div>
    </div>
  );
}

/* A compact def/mid/att occupancy bar, for one table cell. */
function ThirdsBar({ def, mid, att }) {
  return (
    <div>
      <div className="flex h-1.5 rounded-full overflow-hidden">
        <div style={{ width: `${def || 0}%`, background: '#ef4444' }} />
        <div style={{ width: `${mid || 0}%`, background: '#eab308' }} />
        <div style={{ width: `${att || 0}%`, background: '#a3e635' }} />
      </div>
      <div className="flex justify-between text-[8px] text-stone-500 mt-0.5 tabular-nums">
        <span>{Math.round(def || 0)}</span>
        <span>{Math.round(mid || 0)}</span>
        <span>{Math.round(att || 0)}</span>
      </div>
    </div>
  );
}

/* ---------- Sparkline (inline mini-chart) ---------- */
function Sparkline({ values, color = '#a3e635', w = 56, h = 16 }) {
  if (!values || values.length === 0) return <span className="text-stone-600">—</span>;
  if (values.length === 1) return <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`).join(' ');
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="inline-block align-middle">
      <polyline fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" points={pts} />
    </svg>
  );
}

function SparkBlock({ label, values, color, fmt }) {
  const last = values && values.length > 0 ? values[values.length - 1] : 0;
  return (
    <div>
      <div className="text-[9px] text-stone-500 uppercase">{label}</div>
      <div className="flex items-end gap-2 mt-0.5">
        <span className="font-display tabular-nums text-base leading-none" style={{ color }}>{fmt(last)}</span>
        <Sparkline values={values} color={color} w={70} h={20} />
      </div>
    </div>
  );
}

/* ---------- BROADCAST VIDEO PLAYER + OVERLAY ----------
 * Flat <video> player for the post-game tv_reel + auto_highlights mp4s.
 * On top, draws a live scorebug + goal/sub/card popups synced to the
 * video clock, using the `broadcast_events` index written by the Python
 * pipeline (post_game/pipeline.py → _build_broadcast_events_index).
 *
 * `timeKey` selects which reel timeline each event maps onto:
 *   'tvReelTimeS'         — full TV reel
 *   'autoHighlightsTimeS' — condensed auto-highlights reel
 */
// Shared scorebug chrome — keep in sync with the live in-game scorebug.
const SCOREBUG_BG = 'linear-gradient(135deg, rgba(15,15,18,0.92) 0%, rgba(28,28,32,0.88) 100%)';
const SCOREBUG_FILLET = 'rgba(22,22,25,0.91)';

// Readable text color (black/white) over an arbitrary jersey hex (WCAG luminance).
// Mirrors textOnColor() in the live VideoPlayer; used so a black jersey doesn't
// produce black "GOAL" text on a black banner.
function readableTextOn(hex) {
  if (!hex || typeof hex !== 'string') return '#ffffff';
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  if (full.length !== 6) return '#ffffff';
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const toLin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const lum = 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
  return lum > 0.5 ? '#0a0a0a' : '#ffffff';
}

function BroadcastVideoPlayer({ url, doc, label, onClose, timeKey, startAtS = null, labelsUrl = null, roster = [] }) {
  const videoRef = useRef(null);
  const [now, setNow] = useState(0);
  // Fit (letterbox, whole frame) vs Fill (crop to fill the screen). The reel is
  // 16:9 but phones in landscape are wider (~20:9), so Fit leaves side bars.
  const [fillMode, setFillMode] = useState(false);

  // REVIEW LABELS (3.7): name chips over tracked players, from the pipeline's
  // keyframe JSON (review_labels_url). Coach-only — the prop is simply not
  // passed on public surfaces. Fetched lazily on first toggle.
  const [showLabels, setShowLabels] = useState(false);
  const [labelData, setLabelData] = useState(null); // {players, frames, sampleHz}
  const labelIdxRef = useRef(0); // last keyframe index (playback is mostly forward)
  useEffect(() => {
    if (!showLabels || !labelsUrl || labelData) return;
    fetch(labelsUrl).then(r => r.json()).then(d => {
      if (d && Array.isArray(d.frames)) setLabelData(d);
    }).catch(() => {});
  }, [showLabels, labelsUrl, labelData]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return undefined;
    let raf = 0;
    const tick = () => {
      setNow(v.currentTime || 0);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [url]);

  // Cue mode (confirm queue): open the reel already seeked to the event.
  // Seek as soon as metadata is in; harmless no-op when startAtS is null.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || startAtS == null) return undefined;
    const seek = () => { try { v.currentTime = Math.max(0, startAtS); } catch (e) {} };
    if (v.readyState >= 1) seek();
    else {
      v.addEventListener('loadedmetadata', seek, { once: true });
      return () => v.removeEventListener('loadedmetadata', seek);
    }
    return undefined;
  }, [url, startAtS]);

  // Swipe-back closes the player; nests above the confirm queue / Analytics
  // without cascading them closed.
  useModalHistory('broadcast', onClose);

  // Index events on the chosen timeline and drop any without a valid time
  // (those events sit outside the rendered reel windows).
  const allEvents = (doc?.broadcast_events || [])
    .map(e => ({ ...e, t: e[timeKey] }))
    .filter(e => e.t != null && Number.isFinite(e.t))
    .sort((a, b) => a.t - b.t);

  // Running score AT current playhead (based on the latest event with t <= now).
  let ourScore = 0;
  let oppScore = 0;
  let currentPeriod = 1;
  let currentElapsed = 0;
  let anchor = null;   // the event the clock is currently reading from
  for (const e of allEvents) {
    if (e.t <= now + 0.01) {
      if (e.ourScoreAfter != null) ourScore = e.ourScoreAfter;
      if (e.oppScoreAfter != null) oppScore = e.oppScoreAfter;
      if (e.period) currentPeriod = e.period;
      if (e.elapsed != null) { currentElapsed = e.elapsed; anchor = e; }
    } else break;
  }
  // Row-2 label: half + minute, matching the live in-game scorebug ("1ST · 27'").
  //
  // ⚠ The minute must ADVANCE WITH the clip, not freeze on the last event and
  // then jump. On a HIGHLIGHTS reel only the events inside a rendered window carry
  // a reel time at all — measured on real games, 18 of 121 / 21 of 106 / 8 of 112 —
  // so latching on "the last event before the playhead" showed a minute from a
  // different part of the match and looked like it was just counting up.
  //
  // Interpolate instead: game time advances at the same rate as playback within
  // the clip we are in, anchored on the nearest event. `nextEv` bounds it so the
  // number cannot run past the following event's own clock (a clip boundary is a
  // cut in game time, and without the bound the minute would sail through it).
  const halfLenMin = Number(doc?.halfLengthMin) || 25;
  // Prefer the SEGMENT index: it covers the whole reel, where events cover only
  // the instants inside rendered windows. Each segment says where it starts in the
  // reel and what the game clock read there, so the minute is exact everywhere.
  const reelMeta = timeKey === 'tvReelTimeS' ? doc?.tv_reel : doc?.auto_highlights;
  const segs = (reelMeta && Array.isArray(reelMeta.segments)) ? reelMeta.segments : [];
  const seg = (() => {
    let found = null;
    for (const s of segs) {
      if (s.reel_start_s == null || s.clock_s == null) continue;
      const len = Math.max(0, (s.end_s || 0) - (s.start_s || 0));
      if (now + 0.01 >= s.reel_start_s && now <= s.reel_start_s + len) return s;
      if (now > s.reel_start_s) found = s;   // last one we've passed
    }
    return found;
  })();

  let clockPeriod = currentPeriod;
  let clockElapsed = currentElapsed || 0;
  if (seg) {
    // Game time advances at playback rate within a clip.
    clockElapsed = (seg.clock_s || 0) + Math.max(0, now - seg.reel_start_s);
    if (seg.period) clockPeriod = seg.period;
  } else if (anchor) {
    // No segment index (older analytics docs): fall back to interpolating from
    // the nearest event, bounded by the next one so the number cannot sail
    // through a clip boundary — a cut is a jump in game time, not elapsed time.
    const nextEv = allEvents.find(e => e.t > now + 0.01) || null;
    clockElapsed = (anchor.elapsed || 0) + Math.max(0, now - anchor.t);
    if (nextEv && nextEv.period === currentPeriod && nextEv.elapsed != null) {
      clockElapsed = Math.min(clockElapsed, nextEv.elapsed);
    }
  }
  const minuteNum = Math.max(1, Math.floor(clockElapsed / 60) + 1)
    + (clockPeriod === 2 ? halfLenMin : 0);
  const statusLabel = `${clockPeriod === 2 ? '2ND' : '1ST'} · ${minuteNum}'`;

  // --- Active popup selection ---------------------------------------
  // Only GOAL and SUB events show popups. Sub events that fire within
  // SUB_GROUP_S of each other (back-to-back tags by the coach) are merged
  // into a single popup whose anchor time = the LAST sub in the group.
  const GOAL_POPUP_S = 7.5;     // big TV-style scorers graphic stays ~7s
  const SUB_POPUP_S = 5;        // smaller sub bug
  const SUB_GROUP_S = 30;       // back-to-back subs grouped if within 30s

  // Pre-build sub groups (each group is contiguous SUB events within SUB_GROUP_S).
  const subGroups = (() => {
    // Only show subs where we actually know who came on/off — an empty
    // "IN — OUT —" bug is noise.
    const subs = allEvents.filter(e =>
      (e.type === 'SUB' || e.type === 'SUBSTITUTION') &&
      (e.inFirstName || e.inJerseyNumber != null || e.outFirstName || e.outJerseyNumber != null)
    );
    const groups = [];
    for (const s of subs) {
      const last = groups[groups.length - 1];
      if (last && s.t - last[last.length - 1].t <= SUB_GROUP_S) {
        last.push(s);
      } else {
        groups.push([s]);
      }
    }
    return groups;
  })();

  // Find the active popup AT current playhead.
  // Priority: goal popup wins over sub popup if both are within their window.
  const activePopup = (() => {
    // 1. Most recent goal in last GOAL_POPUP_S seconds
    for (let i = allEvents.length - 1; i >= 0; i--) {
      const e = allEvents[i];
      if (e.t > now + 0.01) continue;
      if (now - e.t > GOAL_POPUP_S) break;
      const isGoal = e.type === 'GOAL' || e.type === 'OPP_GOAL' || e.type === 'OPPONENT_GOAL' || e.type === 'GOAL_AGAINST';
      if (isGoal) return { kind: 'goal', ev: e, elapsed: now - e.t, holdEnd: GOAL_POPUP_S - 0.6 };
      // Penalty miss/save — broadcast moment too (whichever happened most
      // recently in the window wins, same as goals).
      if (e.type === 'PEN_MISSED' || e.type === 'OPP_PEN_MISSED') {
        return { kind: 'penmiss', ev: e, elapsed: now - e.t, holdEnd: GOAL_POPUP_S - 0.6 };
      }
    }
    // 2. Otherwise most recent sub group anchored on its LAST sub
    for (let i = subGroups.length - 1; i >= 0; i--) {
      const g = subGroups[i];
      const anchor = g[g.length - 1].t;
      if (anchor > now + 0.01) continue;
      if (now - anchor > SUB_POPUP_S) break;
      return { kind: 'sub', group: g, elapsed: now - anchor, holdEnd: SUB_POPUP_S - 0.5 };
    }
    return null;
  })();

  // For the goal popup: collect all GOAL scorers per side UP TO (and including)
  // the popup event. Each entry: { num, first, minutes: ['12', '41'] }.
  const goalScorers = (() => {
    if (!activePopup || activePopup.kind !== 'goal') return { us: [], them: [] };
    const cutT = activePopup.ev.t + 0.01;
    const us = new Map();
    const them = new Map();
    for (const e of allEvents) {
      if (e.t > cutT) break;
      const halfLen = Number(doc?.halfLengthMin) || 25;
      const minStr = `${Math.max(1, Math.floor((e.elapsed || 0) / 60) + 1) + ((e.period || 1) === 2 ? halfLen : 0)}'`;
      const isOurGoal = e.type === 'GOAL';
      const isOppGoal = e.type === 'OPP_GOAL' || e.type === 'OPPONENT_GOAL' || e.type === 'GOAL_AGAINST';
      if (isOurGoal) {
        const key = e.playerId || `?${e.jerseyNumber || ''}`;
        const entry = us.get(key) || { num: e.jerseyNumber, first: e.playerFirstName || 'Goal', goals: [] };
        entry.goals.push({ min: minStr, aFirst: e.assistFirstName || null, aNum: e.assistJerseyNumber, pen: !!e.penalty });
        us.set(key, entry);
      } else if (isOppGoal) {
        // Opponent: we don't know scorer name — just collect minutes under "OPP".
        const entry = them.get('opp') || { num: null, first: null, goals: [] };
        entry.goals.push({ min: minStr, pen: !!e.penalty });
        them.set('opp', entry);
      }
    }
    return { us: [...us.values()], them: [...them.values()] };
  })();

  // Full team names (CSS truncates if a name is very long) — matches the live
  // in-game scorebug. Do NOT hard-slice to 4 chars ("WIND"/"STOM").
  const homeName = (doc?.home_name || 'Stompers').toUpperCase();
  const awayName = (doc?.away_name || 'OPP').toUpperCase();
  const homeColor = doc?.home_color || '#A3E635';
  const awayColor = doc?.away_color || '#F87171';

  return (
    <div className="fixed inset-0 bg-black z-[60]">
      {/* Video fills the whole screen; chrome floats on top (no persistent band). */}
      {url ? (
        <video
          ref={videoRef}
          src={url}
          controls
          playsInline
          autoPlay
          className="absolute inset-0 w-full h-full"
          style={{ objectFit: fillMode ? 'cover' : 'contain' }}
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-stone-400 text-sm p-6">Video not available yet.</div>
      )}

      {/* Soft top scrim so the floating scorebug + buttons stay legible over bright video. */}
      <div className="absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-black/55 to-transparent pointer-events-none z-10" />

      {/* REVIEW LABELS overlay (3.7): lerped name chips above each tracked
          player's feet, mapped from reel-crop coords through the current
          fit/fill letterboxing. */}
      {showLabels && labelData && (() => {
        const v = videoRef.current;
        if (!v) return null;
        const frames = labelData.frames;
        // Bracketing keyframes around `now` (cached index; rewinds re-scan).
        let i = Math.min(labelIdxRef.current, frames.length - 1);
        if (frames[i][0] > now) i = 0;
        while (i + 1 < frames.length && frames[i + 1][0] <= now) i++;
        labelIdxRef.current = i;
        const [t0, e0] = frames[i];
        const next = frames[i + 1];
        // Short linger only: a chip outliving its track sails over empty grass.
        if (Math.abs(now - t0) > 1.2 && (!next || Math.abs(now - next[0]) > 1.2)) return null;
        const byIdx1 = next ? Object.fromEntries(next[1].map(e => [e[0], e])) : {};
        const alpha = next && next[0] > t0 ? Math.min(1, Math.max(0, (now - t0) / (next[0] - t0))) : 0;
        // Displayed video rect under contain/cover letterboxing.
        const cw = v.clientWidth, ch = v.clientHeight;
        const va = (v.videoWidth && v.videoHeight) ? v.videoWidth / v.videoHeight : 16 / 9;
        const ca = cw / Math.max(ch, 1);
        let w, h;
        if (fillMode ? ca <= va : ca > va) { h = ch; w = ch * va; } else { w = cw; h = cw / va; }
        const ox = (cw - w) / 2, oy = (ch - h) / 2;
        // "?<trackletId>" = tracked but unassigned (a FIX IDS worklist item);
        // rendered dimmed so named chips stay the focus.
        const nameOf = (pid) => {
          if (pid.startsWith('?')) return pid;
          const p = roster.find(r => r.id === pid);
          return p ? `${p.name.split(' ')[0]}${p.number != null ? ` ${p.number}` : ''}` : pid.slice(-4);
        };
        return (
          <div className="absolute inset-0 pointer-events-none z-[15] overflow-hidden">
            {e0.map(([idx, x0, y0]) => {
              // Lerp only across plausible motion: a big inter-keyframe jump
              // is a track gap or identity swap, and sweeping the chip across
              // the field between the two points draws a label on empty grass.
              const e1 = byIdx1[idx];
              const plausible = e1 && Math.hypot(e1[1] - x0, e1[2] - y0) < 0.12;
              const nx = plausible ? x0 + (e1[1] - x0) * alpha : x0;
              const ny = plausible ? y0 + (e1[2] - y0) * alpha : y0;
              if (!plausible && alpha > 0.6) return null; // near the far keyframe: don't show stale spot
              if (nx < -0.02 || nx > 1.02 || ny < 0 || ny > 1.05) return null;
              const pid = labelData.players[idx];
              const unknown = pid.startsWith('?');
              return (
                <div
                  key={idx}
                  className={`absolute text-[10px] font-bold rounded px-1 leading-tight whitespace-nowrap ${unknown ? 'text-stone-300/80 bg-black/40 border border-white/15' : 'text-white bg-black/60 border border-white/30'}`}
                  style={{ left: ox + nx * w, top: oy + ny * h, transform: 'translate(-50%, -130%)', textShadow: '0 1px 1px rgba(0,0,0,0.9)' }}
                >
                  {nameOf(pid)}
                </div>
              );
            })}
          </div>
        );
      })()}

      {/* Floating controls — top-right overlay (replaces the old solid band). */}
      <div
        className="absolute z-20 flex items-center gap-2"
        style={{ right: 'max(env(safe-area-inset-right, 0px), 12px)', top: 'max(env(safe-area-inset-top, 0px), 12px)' }}
      >
        <span className="hidden sm:block text-white/85 font-display text-xs truncate max-w-[34vw] pr-1" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.8)' }}>{label}</span>
        {labelsUrl && (
          <button
            onClick={() => setShowLabels(s => !s)}
            className={`h-9 px-3 rounded-full font-display text-xs border active:scale-95 backdrop-blur-sm ${showLabels ? 'bg-lime-500/80 text-stone-950 border-lime-300' : 'bg-black/55 hover:bg-black/75 text-white border-white/25'}`}
            title="Name labels over tracked players (review mode)"
          >🏷 LABELS</button>
        )}
        <button
          onClick={() => setFillMode(f => !f)}
          className="h-9 px-3 rounded-full bg-black/55 hover:bg-black/75 text-white font-display text-xs border border-white/25 active:scale-95 backdrop-blur-sm"
          title={fillMode ? 'Show the whole frame (letterboxed)' : 'Fill the screen (crops edges)'}
        >{fillMode ? '⤡ FIT' : '⤢ FILL'}</button>
        <button
          onClick={onClose}
          className="h-9 px-3 rounded-full bg-black/55 hover:bg-black/75 text-white font-display text-xs border border-white/25 active:scale-95 backdrop-blur-sm"
        >CLOSE ✕</button>
      </div>

        {/* SCOREBUG — persistent, top-left. Mirrors the live in-game scorebug
            (rounded two-row card with concave fillets + glowing jersey chips).
            Full team names (wrap to 2 lines) instead of a 4-char abbreviation. */}
        <div
          className="absolute z-20 pointer-events-none select-none flex flex-col items-center"
          style={{ left: 'max(env(safe-area-inset-left, 0px), 12px)', top: 'max(env(safe-area-inset-top, 0px), 12px)' }}
        >
          {/* Row 1 — score (rounded all corners) */}
          <div
            className="rounded-2xl shadow-2xl border border-white/15 overflow-hidden backdrop-blur-md"
            style={{ background: SCOREBUG_BG }}
          >
            <div className="flex items-stretch text-[11px]">
              <div className="flex items-center pl-1.5 pr-2.5 py-1.5">
                <div className="w-[6px] h-5 rounded-sm mr-2 shrink-0" style={{ background: homeColor, border: '1px solid rgba(255,255,255,0.9)', boxShadow: `0 0 6px ${homeColor}80` }} />
                <span className="font-bold tracking-wider text-white max-w-[7rem] leading-[1.05]" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}>{homeName}</span>
              </div>
              <div className="px-2.5 py-1.5 flex items-center gap-1.5 bg-black/30">
                <span className="font-display tabular-nums text-white text-base leading-none" style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}>{ourScore}</span>
                <span className="text-white/30 text-xs">–</span>
                <span className="font-display tabular-nums text-white text-base leading-none" style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}>{oppScore}</span>
              </div>
              <div className="flex items-center pl-2.5 pr-1.5 py-1.5">
                <span className="font-bold tracking-wider text-white max-w-[7rem] leading-[1.05] text-right" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}>{awayName}</span>
                <div className="w-[6px] h-5 rounded-sm ml-2 shrink-0" style={{ background: awayColor, border: '1px solid rgba(255,255,255,0.9)', boxShadow: `0 0 6px ${awayColor}80` }} />
              </div>
            </div>
          </div>
          {/* Row 2 wrapper — relative for the corner fillets */}
          <div className="relative -mt-px">
            <span aria-hidden className="absolute left-[-8px] top-0 w-2 h-2" style={{ background: SCOREBUG_FILLET, WebkitMaskImage: 'radial-gradient(circle at bottom right, transparent 8px, black 8.5px)', maskImage: 'radial-gradient(circle at bottom right, transparent 8px, black 8.5px)' }} />
            <span aria-hidden className="absolute right-[-8px] top-0 w-2 h-2" style={{ background: SCOREBUG_FILLET, WebkitMaskImage: 'radial-gradient(circle at bottom left, transparent 8px, black 8.5px)', maskImage: 'radial-gradient(circle at bottom left, transparent 8px, black 8.5px)' }} />
            <div
              className="rounded-b-2xl shadow-lg border border-t-0 border-white/15 backdrop-blur-md flex items-center justify-center gap-1.5 px-3 py-1 w-[150px]"
              style={{ background: SCOREBUG_BG }}
            >
              <span className="font-display tabular-nums text-white text-[13px] font-extrabold tracking-[0.15em] leading-none" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.7)' }}>{statusLabel}</span>
            </div>
          </div>
        </div>

        {/* EVENT POPUP — TV-style big scorers card for goals, small bug for subs */}
        {activePopup && activePopup.kind === 'goal' && (
          <BroadcastGoalCard
            elapsed={activePopup.elapsed}
            holdEnd={activePopup.holdEnd}
            homeName={homeName}
            awayName={awayName}
            homeColor={homeColor}
            awayColor={awayColor}
            ourScore={ourScore}
            oppScore={oppScore}
            scorers={goalScorers}
            scoringSide={activePopup.ev.team === 'them' ? 'them' : 'us'}
            penalty={!!activePopup.ev.penalty}
            assistFirst={activePopup.ev.assistFirstName || null}
            assistNum={activePopup.ev.assistJerseyNumber ?? null}
          />
        )}
        {activePopup && activePopup.kind === 'penmiss' && (
          <BroadcastPenaltyCard
            elapsed={activePopup.elapsed}
            holdEnd={activePopup.holdEnd}
            homeName={homeName}
            awayName={awayName}
            ourScore={ourScore}
            oppScore={oppScore}
            ev={activePopup.ev}
          />
        )}
        {activePopup && activePopup.kind === 'sub' && (
          <BroadcastSubBug
            elapsed={activePopup.elapsed}
            holdEnd={activePopup.holdEnd}
            subs={activePopup.group}
          />
        )}
    </div>
  );
}

/* ---- Big TV-style "GOAL" scorers card --------------------------------
 * Pops up after every goal. Shows current score big, then both teams'
 * goalscorer list with the minute each goal was scored.
 *
 *     STOMPERS    2  —  1    OPPONENT
 *     #7 ARIA       12', 41'
 *     #11 SAMI      28'                           35'
 *
 * Holds ~7s, fades out.
 */
function BroadcastGoalCard({ elapsed, holdEnd, homeName, awayName, homeColor, awayColor, ourScore, oppScore, scorers, scoringSide, penalty, assistFirst, assistNum }) {
  let opacity = 1;
  let scale = 1;
  if (elapsed < 0.4) {
    opacity = elapsed / 0.4;
    scale = 0.92 + 0.08 * opacity;
  } else if (elapsed > holdEnd) {
    const t = Math.min(1, (elapsed - holdEnd) / 0.6);
    opacity = 1 - t;
  }

  const renderLine = (s, side) => {
    const label = s.first ? `#${s.num ?? '?'} ${s.first.toUpperCase()}` : 'OPPONENT';
    const goals = s.goals || [];
    const mins = (
      <span className="flex flex-wrap gap-x-1.5 gap-y-0.5">
        {goals.map((g, i) => (
          <span key={i} className="tabular-nums text-stone-300">
            {g.min}
            {g.pen && <span className="text-amber-300/90 not-italic"> (P)</span>}
            {/* assist only known for our goals */}
            {g.aFirst && (
              <span className="text-lime-300/80 not-italic"> 🅰{g.aFirst.toUpperCase()}{g.aNum != null ? ` #${g.aNum}` : ''}</span>
            )}
            {i < goals.length - 1 ? ',' : ''}
          </span>
        ))}
      </span>
    );
    return (
      <div key={`${side}-${label}`} className={`flex ${side === 'us' ? 'justify-start' : 'justify-end'} items-baseline gap-2 text-[11px]`}>
        {side === 'us' && <span className="font-display tracking-wide text-white shrink-0">{label}</span>}
        {mins}
        {side === 'them' && <span className="font-display tracking-wide text-white shrink-0">{label}</span>}
      </div>
    );
  };

  // Assist only known for OUR goals (opponent scorer/assist unknown).
  const assistLabel = (scoringSide === 'us' && assistFirst)
    ? `🅰 ${assistFirst.toUpperCase()}${assistNum != null ? ` #${assistNum}` : ''}`
    : null;

  return (
    <div
      className="absolute inset-0 pointer-events-none select-none flex items-end justify-center pb-[7%] sm:pb-[16%]"
      style={{ opacity, transition: 'opacity 80ms linear' }}
    >
      <div
        className="rounded-lg border border-black/60 shadow-2xl overflow-hidden"
        style={{
          background: 'rgba(0,0,0,0.82)',
          backdropFilter: 'blur(8px)',
          width: 'min(78vw, 440px)',
          transform: `scale(${scale})`,
          transformOrigin: 'center bottom',
          transition: 'transform 80ms linear',
        }}
      >
        {/* Top banner */}
        <div className="px-3 py-1 text-center text-[9px] tracking-[0.3em]" style={{ background: scoringSide === 'us' ? homeColor : awayColor, color: readableTextOn(scoringSide === 'us' ? homeColor : awayColor) }}>
          ⚽ {penalty ? 'PENALTY GOAL' : 'GOAL'}
        </div>

        {/* Score line */}
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 py-2 text-white">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="inline-block w-1.5 h-5 rounded-sm shrink-0" style={{ background: homeColor }} />
            <span className="font-display text-sm sm:text-base tracking-wide leading-tight whitespace-nowrap truncate">{homeName}</span>
          </div>
          <div className="font-display text-xl sm:text-2xl tabular-nums text-center px-1">
            <span className={scoringSide === 'us' ? 'text-lime-300' : ''}>{ourScore}</span>
            <span className="mx-1.5 text-stone-500">—</span>
            <span className={scoringSide === 'them' ? 'text-red-300' : ''}>{oppScore}</span>
          </div>
          <div className="flex items-center gap-1.5 min-w-0 justify-end">
            <span className="font-display text-sm sm:text-base tracking-wide leading-tight whitespace-nowrap truncate text-right">{awayName}</span>
            <span className="inline-block w-1.5 h-5 rounded-sm shrink-0" style={{ background: awayColor }} />
          </div>
        </div>

        {/* Assist line for the just-scored goal */}
        {assistLabel && (
          <div className="px-3 pb-1.5 -mt-1 text-center text-[10px] tracking-wide text-lime-200/90 font-display">
            {assistLabel}
          </div>
        )}

        {/* Scorers lists */}
        {(scorers.us.length > 0 || scorers.them.length > 0) && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 px-3 pb-2.5 border-t border-white/10 pt-2">
            <div className="space-y-0.5">
              {scorers.us.map(s => renderLine(s, 'us'))}
            </div>
            <div className="space-y-0.5">
              {scorers.them.map(s => renderLine(s, 'them'))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---- TV-style "PENALTY MISSED / SAVED" card -------------------------
 * Mirrors BroadcastGoalCard's fade/scale, fired for PEN_MISSED (our taker
 * missed) and OPP_PEN_MISSED (opponent missed / our keeper saved). Score is
 * unchanged — this is purely the broadcast moment.
 */
function BroadcastPenaltyCard({ elapsed, holdEnd, homeName, awayName, ourScore, oppScore, ev }) {
  let opacity = 1;
  let scale = 1;
  if (elapsed < 0.4) {
    opacity = elapsed / 0.4;
    scale = 0.92 + 0.08 * opacity;
  } else if (elapsed > holdEnd) {
    opacity = 1 - Math.min(1, (elapsed - holdEnd) / 0.6);
  }
  const saved = ev.type === 'OPP_PEN_MISSED';   // opponent missed / we saved
  const headline = saved ? 'PENALTY SAVED' : 'PENALTY MISSED';
  const emoji = saved ? '🧤' : '🚫';
  const taker = (!saved && ev.playerFirstName)
    ? `#${ev.jerseyNumber ?? '?'} ${ev.playerFirstName.toUpperCase()}`
    : (saved ? null : 'OPPONENT');
  return (
    <div
      className="absolute inset-0 pointer-events-none select-none flex items-end justify-center pb-[7%] sm:pb-[16%]"
      style={{ opacity, transition: 'opacity 80ms linear' }}
    >
      <div
        className="rounded-lg border border-black/60 shadow-2xl overflow-hidden"
        style={{ background: 'rgba(0,0,0,0.82)', backdropFilter: 'blur(8px)', width: 'min(78vw, 440px)', transform: `scale(${scale})`, transformOrigin: 'center bottom', transition: 'transform 80ms linear' }}
      >
        <div className={`px-3 py-1 text-center text-[9px] tracking-[0.3em] font-bold ${saved ? 'bg-sky-500 text-stone-950' : 'bg-red-500 text-white'}`}>
          {emoji} {headline}
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 py-2 text-white">
          <span className="font-display text-sm sm:text-base tracking-wide truncate">{homeName}</span>
          <div className="font-display text-xl sm:text-2xl tabular-nums text-center px-1">
            {ourScore}<span className="mx-1.5 text-stone-500">—</span>{oppScore}
          </div>
          <span className="font-display text-sm sm:text-base tracking-wide text-right truncate">{awayName}</span>
        </div>
        {taker && (
          <div className="px-3 pb-2 -mt-1 text-center text-[11px] tracking-wide text-stone-300 font-display">{taker}</div>
        )}
      </div>
    </div>
  );
}

/* ---- Small "SUB" lower-third bug, supports grouped subs ------------- */
function BroadcastSubBug({ elapsed, holdEnd, subs }) {
  let opacity = 1;
  let translate = 0;
  if (elapsed < 0.4) {
    opacity = elapsed / 0.4;
    translate = (1 - opacity) * 40;
  } else if (elapsed > holdEnd) {
    const t = Math.min(1, (elapsed - holdEnd) / 0.5);
    opacity = 1 - t;
  }
  const renderPlayer = (first, num) =>
    first ? `#${num ?? '?'} ${first}` : (num != null ? `#${num}` : '—');

  return (
    <div
      className="absolute pointer-events-none select-none text-white"
      style={{
        right: 'max(env(safe-area-inset-right, 0px), 12px)',
        bottom: 56,
        opacity,
        transform: `translateX(${translate}px)`,
        transition: 'opacity 80ms linear, transform 80ms linear',
      }}
    >
      <div
        className="rounded-md px-3 py-2 border border-black/40 shadow-lg min-w-[200px] max-w-[320px]"
        style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      >
        <div className="text-[10px] tracking-[0.25em] text-sky-300 mb-1">
          ⇄ {subs.length > 1 ? `${subs.length} SUBSTITUTIONS` : 'SUBSTITUTION'}
        </div>
        <div className="space-y-1">
          {subs.map((s, i) => (
            <div key={s.id || i} className="text-xs leading-tight grid grid-cols-[auto_1fr] gap-x-2">
              <span className="text-lime-300 font-bold">IN</span>
              <span className="font-display">{renderPlayer(s.inFirstName, s.inJerseyNumber)}</span>
              <span className="text-stone-500 font-bold">OUT</span>
              <span className="font-display text-stone-300">{renderPlayer(s.outFirstName, s.outJerseyNumber)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------- POST-GAME ANALYTICS PANEL ----------
 * Reads `teams/main/games/<gameId>/analytics/v1` written by the Python
 * pipeline (post_game/pipeline.py). Shows per-player physical stats, team
 * formation, GK positioning, and links to highlight clips.
 * Coach-only — purely a read view here.
 */
/* ---- Player heatmap: canonical pitch, BOTTOM = our net, TOP = opponent ----
 * grid is the flattened row-major heatmap_grid (rows×cols), row 0 = our-net end
 * (computed half-canonical in stats.py), col 0 = consistent left. We draw a
 * vertical pitch and shade each cell by occupancy; row 0 is rendered at the
 * bottom so "bottom-left" always = our-half left side, in both halves.
 */
function PlayerHeatmap({ grid, rows, cols }) {
  if (!grid || !grid.length || !rows || !cols) {
    return <div className="text-[11px] text-stone-500 py-2">No positional data.</div>;
  }
  // Normalise against a HIGH-PERCENTILE cell, not the raw max. One poisoned
  // cell (halftime-huddle time counted as pitch time, a mis-assigned
  // touchline stander) used to become the reference and push every genuine
  // cell under the paint floor — the map rendered as four lonely squares.
  // The p95 nonzero cell is the effective peak; anything above it saturates.
  // On healthy smooth grids p95 ≈ max, so this is a no-op there.
  const nonzero = grid.filter((v) => v > 0).sort((a, b) => a - b);
  const max = (nonzero.length
    ? nonzero[Math.min(nonzero.length - 1, Math.floor(0.95 * (nonzero.length - 1)))]
    : 0) || Math.max(...grid) || 1;
  // Build display rows top→bottom = opponent-net → our-net (data row index high→low).
  const displayRows = [];
  for (let r = rows - 1; r >= 0; r--) {
    const cells = [];
    for (let c = 0; c < cols; c++) cells.push(grid[r * cols + c] || 0);
    displayRows.push(cells);
  }
  // Cells below this share of the peak are left UNPAINTED.
  //
  // ⚠ Why a threshold is required, not a nicety: these grids come from a kernel
  // density estimate with a 6 m bandwidth, so every cell is strictly positive —
  // the far end of the pitch holds ~0.0001 rather than 0. The previous ramp
  // treated "not exactly zero" as "present" and applied a 0.25 alpha FLOOR, so
  // all 96 cells painted and a keeper whose mass is 78% in his own two rows
  // rendered as a uniform green sheet over the whole pitch (measured: alpha
  // spanned only 0.25→0.37 across a 4000x range in the data). The pitch markings
  // showing through that wash are the "bands" it appeared to show.
  const FLOOR = 0.04;
  const heat = (v) => {
    const rel = Math.min(1, v / max);            // cells above the p95 peak saturate
    if (!(rel >= FLOOR)) return 'transparent';   // also catches 0 and NaN
    // Rescale the surviving range across the FULL alpha span so the peak reads
    // as a peak. Gamma < 1 still lifts the mid-range without flattening it.
    const a = Math.pow((rel - FLOOR) / (1 - FLOOR), 0.75);
    const alpha = 0.10 + 0.80 * a;
    if (a < 0.5) {
      const t = a / 0.5; // green→yellow
      return `rgba(${Math.round(120 + 135 * t)}, 200, 40, ${alpha})`;
    }
    const t = (a - 0.5) / 0.5; // yellow→red
    return `rgba(255, ${Math.round(200 - 160 * t)}, 30, ${alpha})`;
  };
  return (
    <div className="flex flex-col items-center gap-1 py-2">
      <div className="text-[9px] tracking-widest text-stone-500">OPPONENT NET ↑</div>
      <div
        className="relative rounded-md overflow-hidden border border-emerald-200/20"
        style={{ width: 132, aspectRatio: `${cols} / ${rows}`, background: 'linear-gradient(180deg,#14532d,#0b3d22)' }}
      >
        {/* Heat cells UNDER the markings: painted cells are semi-transparent, so
            when the lines sat on top of the pitch and under the heat, a shaded
            cell washed them out and the halfway line read as a "band" of heat. */}
        <div className="absolute inset-0 grid" style={{ gridTemplateColumns: `repeat(${cols},1fr)`, gridTemplateRows: `repeat(${rows},1fr)` }}>
          {displayRows.flatMap((cells, ri) => cells.map((v, ci) => (
            <div key={`${ri}-${ci}`} style={{ background: heat(v) }} />
          )))}
        </div>
        {/* pitch markings, drawn last so they stay legible over the heat */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute left-0 right-0 top-1/2 h-px bg-white/25" />
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-7 h-7 rounded-full border border-white/25" />
          <div className="absolute left-[30%] right-[30%] top-0 h-[10%] border-x border-b border-white/20" />
          <div className="absolute left-[30%] right-[30%] bottom-0 h-[10%] border-x border-t border-white/20" />
        </div>
      </div>
      <div className="text-[9px] tracking-widest text-lime-400">OUR NET ↓</div>
    </div>
  );
}

// Coach identity-correction screen. Lists the pipeline's stitched tracklets
// (worst-confidence first) with a representative crop + current assignment, and
// lets the coach reassign each to the right roster player (or mark "not a
// player"). Corrections are written to game.identityOverrides and applied by the
// post_game pipeline on the next re-run (see PLAYER_ID_CORRECTION_UI.md).
function IdentityFixView({ doc, roster, game, onSave, onClose }) {
  const allTracklets = (doc && doc.tracklets) || [];
  const [overrides, setOverrides] = useState(() => ({ ...(game.identityOverrides || {}) }));
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState(null);
  const [zoom, setZoom] = useState(null); // thumb_url shown full-screen for recognition
  // Snapshot of the last-saved overrides. Decided+saved tracklets drop off the
  // list, and "unsaved" = how many differ from this snapshot (→ 0 right after Save).
  const [savedOverrides, setSavedOverrides] = useState(() => ({ ...(game.identityOverrides || {}) }));
  // View filter: TO-DO (undecided + unassigned) · UNREVIEWED (everything you
  // haven't decided — hides COACH / YOUR CALL so you can audit the pipeline's
  // own guesses) · ALL (every segment, decided included).
  const [viewMode, setViewMode] = useState('todo'); // 'todo' | 'unreviewed' | 'all'
  const savedSet = new Set(Object.keys(savedOverrides));
  const unsavedCount = (() => {
    const ids = new Set([...Object.keys(overrides), ...Object.keys(savedOverrides)]);
    let n = 0; ids.forEach(id => { if (overrides[id] !== savedOverrides[id]) n++; }); return n;
  })();

  // Review priority: most IMPORTANT first = uncertain AND high player-time.
  // A confidently-assigned tracklet scores ~0 (nothing to do); a long unassigned
  // one scores high (worth rescuing); a 1-second fragment barely registers.
  const importance = (t) => (1 - (t.confidence || 0)) * ((t.minutes || 0) + 0.2);
  const tracklets = [...allTracklets]
    .filter(t => {
      // ALL exists to double-check past decisions (a wrong name spotted on
      // the labeled reel lives in a decided tracklet, not a pending one).
      if (viewMode === 'all') return true;
      if (savedSet.has(String(t.tracklet_id))) return false; // your decided ones
      if (viewMode === 'unreviewed') return true;            // pipeline guesses + unassigned
      return !t.player_id; // TO-DO: only the still-unassigned ones
    })
    .sort((a, b) => importance(b) - importance(a));
  const remainingUnassigned = allTracklets.filter(t => !t.player_id && !savedSet.has(String(t.tracklet_id))).length;

  // No own history entry: this is a sub-modal of AnalyticsPanel. Pushing/popping
  // here fired a popstate that AnalyticsPanel's listener also caught, cascading
  // both closed and kicking the coach out to the dugout. CLOSE just unmounts it
  // (stays on the Analytics panel); swipe-back falls through to close Analytics.
  // Esc still closes it for desktop.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Per-event reel index used by fieldInfoAt() to map a tracklet's video time
  // → game clock. Moved off the game doc into games/<id>/public/broadcast
  // (2026-06-13), so game.broadcastEvents is empty for re-run games — fetch the
  // subcollection (legacy on-doc fallback retained). See [[broadcast-events]].
  const [bEvents, setBEvents] = useState(() =>
    (Array.isArray(game.broadcastEvents) && game.broadcastEvents.length) ? game.broadcastEvents : null);
  useEffect(() => {
    if (bEvents != null || !window.fbDb || !game?.id) return;
    window.fbDb.collection('teams').doc('main').collection('games').doc(game.id)
      .collection('public').doc('broadcast').get()
      .then(s => setBEvents((s.exists && s.data().events) || []))
      .catch(() => setBEvents([]));
  }, [game?.id]);

  const rosterById = Object.fromEntries(roster.map(p => [p.id, p]));
  // VLM jersey-number identity suggestions (game.identityDrafts, written by
  // tracking/vlm_identity.py). Keyed by tracklet_id → shown as a one-click
  // Accept chip on undecided tracklets; accepting just calls setSel with the
  // suggested player, flowing into the same overrides→save path as a manual pick.
  const draftByTl = Object.fromEntries(
    ((game.identityDrafts) || []).map(d => [String(d.trackletId), d]));
  // Camera on-field-window corrections (analytics.sub_corrections, written by
  // the pipeline from accepted tracklets). Keyed by player id → shown on that
  // player's tracklet rows as "on-field logged→camera" so the coach sees the
  // late-SUB-tap fix. `c` clock = {period, elapsed(s)}.
  const subCorrById = Object.fromEntries(
    ((doc && doc.sub_corrections) || []).map(c => [String(c.playerId), c]));
  // Physically-impossible spans (analytics.player_conflicts): moments where this
  // player was detected in two places at once, i.e. two different kids welded
  // under one identity by a tracker/stitch merge. The pipeline already EXCLUDED
  // these from distance/speed/heatmap; we surface them so the coach knows which
  // of his labelled tracklets are in conflict rather than trusting a silent gap.
  const conflictByPlayer = (doc && doc.player_conflicts) || {};
  // tracklet_id → player, for the stitched tracklets most implicated in that
  // player's conflicts. Keyed by TRACKLET (what FIX-IDS rows are), not raw
  // track_id — the pipeline publishes both, and they are different id spaces.
  const conflictTracks = {};
  Object.entries(conflictByPlayer).forEach(([pid, v]) => {
    ((v && v.worst_tracklets) || []).forEach(w => {
      conflictTracks[String(w.tracklet_id)] = pid;
    });
  });
  const _clkStr = (c) => c ? `${c.period === 2 ? 'H2 ' : ''}${fmt(c.elapsed)}` : null;
  // Only offer players who actually dressed for THIS game (squad), not the whole
  // team roster. Fall back to the starting lineup, then the full roster.
  const squadIds = new Set((game.squad && game.squad.length ? game.squad : (game.startingLineup || [])));
  const sortedRoster = [...roster]
    .filter(p => squadIds.size === 0 || squadIds.has(p.id))
    .sort((a, b) => ((a.number ?? 999) - (b.number ?? 999)));
  const pname = (pid) => {
    const p = rosterById[pid];
    if (!p) return pid ? '(unknown)' : 'unassigned';
    return p.number != null ? `#${p.number} ${p.name}` : p.name;
  };
  // Round to whole seconds FIRST, then split — rounding the remainder alone
  // produces "25:60" when the fraction lands on .5+ of the 59th second.
  const fmt = (s) => { const t = Math.round(s || 0); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; };

  // Override value: a player id, or a labelled non-player sentinel. The labels
  // (ref/opp/other) are kept on the game doc so the pipeline can learn from them
  // later; all of them drop the tracklet from analytics for now.
  const NONPLAYER = { '__opp__': '⚪ Opponent', '__ref__': '🟨 Referee', '__other__': '🚫 Coach / other' };
  const selFor = (tl) => {
    const id = String(tl.tracklet_id);
    if (!(id in overrides)) return '__auto__';
    const v = overrides[id];
    if (v == null || v === '__none__') return '__other__'; // legacy "not a player"
    return v;
  };
  // Who was on field vs bench (and their rough board role) around a
  // tracklet's midpoint — so the picker only makes you choose among kids who
  // could plausibly be in frame. Video time → game clock is estimated through
  // the nearest broadcastEvents entry (clock ≈ video during play).
  const _sortedEvents = useMemo(
    () => [...(game.events || [])].sort((a, b) => (a.at || 0) - (b.at || 0)),
    [game.events]);
  const fieldInfoAt = (tl) => {
    const idx = bEvents || game.broadcastEvents || [];
    if (!idx.length || tl.t_start_s == null) return null;
    const mid = ((tl.t_start_s || 0) + (tl.t_end_s || 0)) / 2;
    let near = null;
    for (const e of idx) {
      if (e.videoTimeS == null || e.elapsed == null) continue;
      if (!near || Math.abs(e.videoTimeS - mid) < Math.abs(near.videoTimeS - mid)) near = e;
    }
    if (!near) return null;
    const period = near.period || 1;
    const elapsed = Math.max(0, (near.elapsed || 0) + (mid - near.videoTimeS));
    const before = (e) => (e.period || 1) < period
      || ((e.period || 1) === period && (e.elapsed || 0) <= elapsed);
    const on = new Set(game.startingLineup || []);
    let gk = game.gkPlayerId || null;
    const pos = {};
    for (const e of _sortedEvents) {
      if (!before(e)) continue;
      if (e.type === 'SUB') {
        if (e.playerId) on.delete(e.playerId);
        if (e.subOnPlayerId) on.add(e.subOnPlayerId);
      } else if (e.type === 'GK_CHANGE' && e.playerId) {
        gk = e.playerId;
      } else if (e.type === 'POSITION' && typeof e.y === 'number') {
        pos[e.playerId] = e;
      }
    }
    const roleOf = (pid) => {
      if (pid === gk) return '🧤 GK';
      const p = pos[pid];
      if (!p) return '';
      const d = 1 - p.y; // board y=1 = own goal
      const band = d < 1 / 3 ? 'DEF' : d < 2 / 3 ? 'MID' : 'ATT';
      return band + (p.x < 1 / 3 ? '·L' : p.x < 2 / 3 ? '·C' : '·R');
    };
    return { on, roleOf, clock: `${period === 2 ? '2nd' : '1st'} half ${Math.floor(elapsed / 60)}'` };
  };

  const setSel = (tl, val) => {
    const id = String(tl.tracklet_id);
    setSavedMsg(null);
    setOverrides(prev => {
      const next = { ...prev };
      if (val === '__auto__') delete next[id];
      else next[id] = val; // player id OR a non-player sentinel
      return next;
    });
  };

  const doSave = async () => {
    setSaving(true);
    try {
      await onSave(overrides);
      // Snapshot what we just saved → "unsaved" resets to 0 and decided rows drop off.
      setSavedOverrides({ ...overrides });
      setSavedMsg('✓ Saved — re-run analytics on the Mac to apply.');
    }
    catch (e) { setSavedMsg('Save failed: ' + e); }
    setSaving(false);
  };

  const confBadge = (c) => {
    const pct = Math.round((c || 0) * 100);
    const cls = c >= 0.75 ? 'bg-lime-500/20 text-lime-300'
      : c >= 0.5 ? 'bg-amber-500/20 text-amber-300'
      : 'bg-rose-500/20 text-rose-300';
    return <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${cls}`}>{pct}%</span>;
  };

  return (
    <div className="fixed inset-0 bg-stone-950 z-[60] overflow-y-auto">
      <div
        className="sticky top-0 stripes-bg text-white border-b border-stone-800 px-4 pb-3 flex items-center justify-between z-10"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <h2 className="font-display text-lg truncate pr-3">🪪 FIX PLAYER IDS</h2>
        <button
          onClick={onClose}
          className="shrink-0 h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs border border-white/20 active:scale-95"
        >
          CLOSE ✕
        </button>
      </div>

      <div className="px-4 py-3 text-stone-400 text-xs leading-relaxed border-b border-stone-800">
        Cards are sorted <span className="text-stone-200">most important first</span> (uncertain + most
        play-time). Assign each, or mark non-players. Tap <span className="text-lime-300">SAVE</span> —
        decided ones drop off so you only see what's left. Then re-run
        <code className="text-stone-300"> ./run_analytics.sh {game.id}</code> on the Mac to apply.
      </div>

      <div className="sticky top-[3.5rem] z-10 px-4 py-2 bg-stone-900/95 backdrop-blur border-b border-stone-800 flex items-center justify-between gap-2">
        <span className="text-xs text-stone-300 shrink-0">
          {remainingUnassigned} to identify{unsavedCount ? ` · ${unsavedCount} unsaved` : ''}
        </span>
        <div className="shrink-0 flex rounded-full border border-stone-700 overflow-hidden">
          {[['todo', 'TO-DO'], ['unreviewed', 'UNREVIEWED'], ['all', 'ALL']].map(([id, lbl]) => (
            <button
              key={id}
              onClick={() => setViewMode(id)}
              className={`h-7 px-2 text-[10px] font-bold tracking-wider active:scale-95 ${viewMode === id ? 'bg-white/15 text-white' : 'bg-stone-800 text-stone-500'}`}
            >
              {lbl}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {savedMsg && <span className="text-[11px] text-lime-300">{savedMsg}</span>}
          <button
            onClick={doSave}
            disabled={saving}
            className="h-8 px-4 rounded-full bg-lime-500 text-stone-950 font-display text-xs active:scale-95 disabled:opacity-50"
          >
            {saving ? 'SAVING…' : 'SAVE'}
          </button>
        </div>
      </div>

      <div className="p-3 space-y-2 pb-24">
        {tracklets.map((tl) => {
          const overridden = String(tl.tracklet_id) in overrides;
          return (
            <div
              key={tl.tracklet_id}
              className={`flex gap-3 rounded-xl border p-2 ${overridden ? 'border-lime-500/50 bg-lime-500/5' : 'border-stone-800 bg-stone-900'}`}
            >
              {tl.thumb_url
                ? <button onClick={() => setZoom(tl.thumb_url)} className="relative w-16 h-24 shrink-0 rounded-lg overflow-hidden bg-stone-800 active:scale-95" aria-label="Enlarge photo">
                    <img src={tl.thumb_url} alt="" className="w-full h-full object-cover" loading="lazy" />
                    <span className="absolute bottom-0.5 right-0.5 h-5 w-5 rounded bg-black/60 flex items-center justify-center text-[11px] leading-none">⤢</span>
                  </button>
                : <div className="w-16 h-24 rounded-lg bg-stone-800 shrink-0 flex items-center justify-center text-stone-600 text-2xl">?</div>}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Title shows the EFFECTIVE decision: a coach-rejected
                      tracklet is "not a player", not "unassigned" (the
                      pipeline drops it, so the record holds no player). */}
                  {(() => {
                    const sel = selFor(tl);
                    if (sel !== '__auto__' && NONPLAYER[sel]) {
                      return <span className="text-stone-400 text-sm font-semibold truncate">{NONPLAYER[sel]}</span>;
                    }
                    return <span className="text-stone-200 text-sm font-semibold truncate">{pname(tl.player_id)}</span>;
                  })()}
                  {confBadge(tl.confidence)}
                  {tl.status === 'coach' && <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-sky-500/20 text-sky-300">COACH</span>}
                  {tl.status !== 'coach' && selFor(tl) !== '__auto__' && savedSet.has(String(tl.tracklet_id)) && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-sky-500/20 text-sky-300">YOUR CALL</span>
                  )}
                </div>
                <div className="text-[11px] text-stone-500 mt-0.5">
                  {Math.round(tl.minutes || 0)} min · {fmt(tl.t_start_s)}–{fmt(tl.t_end_s)} <span className="text-stone-700">· #{tl.tracklet_id}</span>
                </div>
                {/* Camera on-field-window correction for this player (from the
                    accepted tracklets) — shows the late-SUB-tap fix so the coach
                    trusts the adjusted minutes. Only when an edge actually moved. */}
                {(() => {
                  const c = subCorrById[String(tl.player_id)];
                  if (!c || (!c.onCorrected && !c.offCorrected)) return null;
                  const arrow = (from, to) => `${_clkStr(from) || '?'}→${_clkStr(to)}`;
                  const on = c.onCorrected && `on ${arrow(c.onLogged, c.onCorrected)}`;
                  const off = c.offCorrected && `off ${arrow(c.offLogged, c.offCorrected)}`;
                  return (
                    <div className="text-[11px] text-violet-300/80 mt-0.5">
                      🎥 on-field {[on, off].filter(Boolean).join(' · ')} <span className="text-violet-400/60">(camera-corrected)</span>
                    </div>
                  );
                })()}
                {/* Physics conflict: this tracklet is one of the tracks that put a
                    player in two places at once, so it has two different kids in it.
                    Worth re-checking before trusting the label. */}
                {(() => {
                  const cpid = conflictTracks[String(tl.tracklet_id)];
                  if (!cpid) return null;
                  const c = conflictByPlayer[cpid] || {};
                  return (
                    <div className="text-[11px] text-sky-300/80 mt-0.5">
                      🧹 conflicts with another track of {pname(cpid)}
                      {c.median_separation_m ? ` (~${c.median_separation_m}m apart)` : ''} —
                      <span className="text-sky-400/60"> likely two players in this clip</span>
                    </div>
                  );
                })()}
                <select
                  value={selFor(tl)}
                  onChange={(e) => setSel(tl, e.target.value)}
                  className="mt-2 w-full bg-stone-800 border border-stone-700 rounded-lg px-2 py-1.5 text-sm text-stone-100 focus:outline-none focus:border-lime-500"
                >
                  <option value="__auto__">Auto: {pname(tl.player_id)}</option>
                  {(() => {
                    const fi = fieldInfoAt(tl);
                    const opt = (p) => {
                      const r = fi ? fi.roleOf(p.id) : '';
                      return <option key={p.id} value={p.id}>{p.number != null ? `#${p.number} ` : ''}{p.name}{r ? ` · ${r}` : ''}</option>;
                    };
                    if (!fi) {
                      return <optgroup label="Our players (this game)">{sortedRoster.map(opt)}</optgroup>;
                    }
                    const onField = sortedRoster.filter(p => fi.on.has(p.id));
                    const bench = sortedRoster.filter(p => !fi.on.has(p.id));
                    return (
                      <>
                        <optgroup label={`On field around ${fi.clock}`}>{onField.map(opt)}</optgroup>
                        {bench.length > 0 && <optgroup label="On bench then">{bench.map(opt)}</optgroup>}
                      </>
                    );
                  })()}
                  <optgroup label="Not our player">
                    <option value="__opp__">⚪ Opponent</option>
                    <option value="__ref__">🟨 Referee</option>
                    <option value="__other__">🚫 Coach / spectator / other</option>
                  </optgroup>
                </select>
                {/* VLM jersey-number suggestion: one-click Accept sets the picker
                    to the read player. Only on UNDECIDED tracklets whose suggested
                    player exists — the coach still reviews (it's a suggestion). */}
                {(() => {
                  const d = draftByTl[String(tl.tracklet_id)];
                  if (!d || !d.suggestedPlayerId || selFor(tl) !== '__auto__') return null;
                  if (!rosterById[d.suggestedPlayerId]) return null;
                  return (
                    <button
                      onClick={() => setSel(tl, d.suggestedPlayerId)}
                      className="mt-2 w-full flex items-center justify-between gap-2 rounded-lg border border-violet-500/40 bg-violet-500/10 px-2 py-1.5 text-left active:scale-[0.99]"
                    >
                      <span className="text-[12px] text-violet-200 truncate">
                        <span className="font-bold">🔎 #{d.jerseyNumber}</span> {pname(d.suggestedPlayerId)}
                        {d.reasoning ? <span className="text-violet-300/70"> — {d.reasoning}</span> : null}
                      </span>
                      <span className="shrink-0 flex items-center gap-1.5">
                        {confBadge(d.confidence)}
                        <span className="px-2 py-0.5 rounded bg-violet-500/30 text-violet-100 text-[11px] font-bold">Accept</span>
                      </span>
                    </button>
                  );
                })()}
              </div>
            </div>
          );
        })}
        {tracklets.length === 0 && (
          <div className="text-center text-stone-400 text-sm py-12">
            <div className="text-3xl mb-2">✅</div>
            {viewMode === 'all' ? 'No segments in this game.'
              : viewMode === 'unreviewed' ? 'Nothing unreviewed — every segment carries one of your decisions.'
              : 'All players identified — nothing unassigned left.'}
            {viewMode === 'todo' && (
              <div className="mt-3 space-x-4">
                <button onClick={() => setViewMode('unreviewed')} className="text-xs text-lime-400 underline">
                  Audit the pipeline's guesses
                </button>
                <button onClick={() => setViewMode('all')} className="text-xs text-lime-400 underline">
                  Show everything
                </button>
              </div>
            )}
            {viewMode === 'unreviewed' && (
              <div className="mt-3">
                <button onClick={() => setViewMode('all')} className="text-xs text-lime-400 underline">
                  Show everything incl. your decisions
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {zoom && (
        <div
          className="fixed inset-0 z-[70] bg-black/95 flex flex-col items-center justify-center p-4"
          onClick={() => setZoom(null)}
        >
          <img src={zoom} alt="" className="max-w-full max-h-[85vh] object-contain rounded-lg" style={{ imageRendering: 'auto' }} />
          <div className="mt-4 text-stone-300 text-xs">Tap anywhere to close</div>
        </div>
      )}
    </div>
  );
}

/* ---------- MOMENTUM CHART (4.1) ----------
 * 5-minute buckets of for-vs-against momentum from the coach log. We only
 * log OUR team's events, so "against" is proxied by what our log implies
 * about the opponent: a SAVE/BLOCK/CLEAR means they were attacking; a
 * TURNOVER hands them the ball. Pure client-side from game.events.
 */
const MOMENTUM_FOR = { GOAL: 3, SHOT_ON: 2, SHOT_OFF: 1, BALL_WIN: 1, PEN_AWARDED: 1, OPP_PEN_MISSED: 2 };
const MOMENTUM_AGAINST = { OPP_GOAL: 3, SAVE: 2, BLOCK: 1, CLEAR: 1, KICK_OUT: 1, TURNOVER: 1, PEN_CONCEDED: 1, PEN_MISSED: 1 };
const MOMENTUM_BUCKET_S = 300;

// TEAM SHAPE (Tier 2) — surfaces the per-second team_time_series the pipeline
// already writes (compactness / width / depth of the outfield block) but which
// nothing rendered. Identity-ROBUST: it's the aggregate shape of team-0, so it
// holds up even when per-player identity assignment is weak. Three sparklines
// over match time with a half divider + each series' average — a coach reads
// "we got stretched in the second half" or "we sat compact all game" at a glance.
const TEAM_SHAPE_SERIES = [
  { key: 'compactness_m', label: 'COMPACT', color: '#a3e635', hint: 'avg spacing between players — lower = tighter block' },
  { key: 'width_m',       label: 'WIDTH',   color: '#38bdf8', hint: 'side-to-side spread' },
  { key: 'depth_m',       label: 'DEPTH',   color: '#f59e0b', hint: 'front-to-back spread' },
];

// Full-match time series (x = seconds, with an optional halftime rule). Distinct
// from the generic `Sparkline` above, which plots evenly-spaced values and takes
// no `times`. These were both named `Sparkline`; function declarations hoist
// last-wins, so this one silently shadowed the other and threw on every
// Season Analytics row with 2+ games. Keep the names distinct.
function TimeSeriesSparkline({ times, values, color, halfT }) {
  // Downsample to ~120 points so the SVG stays light on a full-match series.
  const n = values.length;
  if (n < 2) return null;
  const step = Math.max(1, Math.floor(n / 120));
  const pts = [];
  for (let i = 0; i < n; i += step) pts.push([times[i], values[i]]);
  if (pts[pts.length - 1][0] !== times[n - 1]) pts.push([times[n - 1], values[n - 1]]);
  const t0 = times[0], t1 = times[n - 1] || t0 + 1;
  const vs = pts.map(p => p[1]);
  const vmin = Math.min(...vs), vmax = Math.max(...vs);
  const W = 100, H = 28, pad = 2;
  const sx = (t) => pad + (W - 2 * pad) * ((t - t0) / (t1 - t0 || 1));
  const sy = (v) => pad + (H - 2 * pad) * (1 - (v - vmin) / (vmax - vmin || 1));
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`).join(' ');
  const hx = halfT != null && halfT > t0 && halfT < t1 ? sx(halfT) : null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: '28px' }}>
      {hx != null && <line x1={hx} y1={0} x2={hx} y2={H} stroke="#57534e" strokeWidth="0.5" strokeDasharray="1.5 1.5" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function TeamShapeChart({ doc, halfLenS }) {
  const ts = doc && doc.team_time_series;
  const times = (ts && ts.times_s) || [];
  if (times.length < 3) return null; // older games / too little data
  const avg = (arr) => arr && arr.length ? arr.reduce((a, b) => a + (Number(b) || 0), 0) / arr.length : null;
  return (
    <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
      <div className="text-[10px] tracking-widest text-stone-400 mb-2">
        TEAM SHAPE <span className="text-stone-600">· outfield block over the match</span>
      </div>
      <div className="space-y-2">
        {TEAM_SHAPE_SERIES.map(s => {
          const vals = (ts[s.key] || []);
          const a = avg(vals);
          if (a == null) return null;
          return (
            <div key={s.key} className="flex items-center gap-2">
              <div className="w-14 shrink-0">
                <div className="text-[9px] font-bold tracking-wider" style={{ color: s.color }}>{s.label}</div>
                <div className="text-[10px] tabular-nums text-stone-300">{a.toFixed(1)}m</div>
              </div>
              <div className="flex-1 min-w-0" title={s.hint}>
                <TimeSeriesSparkline times={times} values={vals} color={s.color} halfT={halfLenS} />
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-between mt-1 text-[9px] text-stone-600">
        <span>KICKOFF</span><span>HALF</span><span>FULL TIME</span>
      </div>
    </div>
  );
}

function MomentumChart({ game }) {
  const halfLenS = (game.halfLengthMin || 25) * 60;
  const totalS = halfLenS * 2;
  const nBuckets = Math.max(2, Math.ceil(totalS / MOMENTUM_BUCKET_S));
  const buckets = Array.from({ length: nBuckets }, () => ({ pos: 0, neg: 0, goals: [] }));
  const tOf = (e) => {
    // Game-clock seconds across both halves; stoppage clamps into its half.
    const p = e.period === 2 ? 1 : 0;
    return p * halfLenS + Math.min(e.elapsed || 0, halfLenS - 1);
  };
  let any = false;
  for (const e of game.events || []) {
    const idx = Math.min(nBuckets - 1, Math.floor(tOf(e) / MOMENTUM_BUCKET_S));
    if (e.type === 'GOAL') buckets[idx].goals.push('us');
    if (e.type === 'OPP_GOAL') buckets[idx].goals.push('them');
    if (MOMENTUM_FOR[e.type]) { buckets[idx].pos += MOMENTUM_FOR[e.type]; any = true; }
    else if (MOMENTUM_AGAINST[e.type]) { buckets[idx].neg += MOMENTUM_AGAINST[e.type]; any = true; }
  }
  if (!any) return null;
  const maxV = Math.max(1, ...buckets.map(b => Math.max(b.pos, b.neg)));
  const halfIdx = Math.floor(nBuckets / 2);
  return (
    <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] tracking-widest text-stone-400">MOMENTUM</div>
        <div className="flex gap-3 text-[9px] text-stone-500">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-lime-500" />US</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500" />THEM</span>
        </div>
      </div>
      <div className="flex items-stretch gap-[3px]" style={{ height: '84px' }}>
        {buckets.map((b, i) => (
          <div key={i} className={`flex-1 flex flex-col ${i === halfIdx ? 'border-l border-stone-600/60 pl-[3px]' : ''}`}>
            {/* top half: us */}
            <div className="flex-1 flex flex-col justify-end items-center">
              {b.goals.filter(g => g === 'us').map((_, j) => (
                <span key={j} className="text-[9px] leading-none">⚽</span>
              ))}
              <div
                className="w-full rounded-t-sm bg-lime-500/80"
                style={{ height: `${(b.pos / maxV) * 100}%`, minHeight: b.pos > 0 ? '3px' : 0 }}
              />
            </div>
            <div className="h-px bg-stone-600/80" />
            {/* bottom half: them */}
            <div className="flex-1 flex flex-col justify-start items-center">
              <div
                className="w-full rounded-b-sm bg-red-500/70"
                style={{ height: `${(b.neg / maxV) * 100}%`, minHeight: b.neg > 0 ? '3px' : 0 }}
              />
              {b.goals.filter(g => g === 'them').map((_, j) => (
                <span key={j} className="text-[9px] leading-none">⚽</span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="flex justify-between mt-1 text-[9px] text-stone-600">
        <span>KICKOFF</span><span>HALF</span><span>FULL TIME</span>
      </div>
    </div>
  );
}

/* ---------- SHOT MAP (4.2) ----------
 * 3×3 half-field chart of GOAL / SHOT_ON / SHOT_OFF by their coach zone tag,
 * attack at the top (same convention as the tag grids). Works per game or
 * across a season (pass any list of games). Untagged shots are surfaced as
 * the audit line — they're exactly what the confirm queue drains.
 */
function ShotMap({ games }) {
  const cells = {}; // 'A-L' -> { goals, on, off }
  let untagged = 0;
  let total = 0;
  for (const g of games || []) {
    for (const e of g.events || []) {
      if (e.type !== 'GOAL' && e.type !== 'SHOT_ON' && e.type !== 'SHOT_OFF') continue;
      total++;
      if (!e.zone || !ZONE_LABEL[e.zone]) { untagged++; continue; }
      const c = cells[e.zone] || (cells[e.zone] = { goals: 0, on: 0, off: 0 });
      if (e.type === 'GOAL') c.goals++;
      else if (e.type === 'SHOT_ON') c.on++;
      else c.off++;
    }
  }
  if (total === 0) return null;
  const maxN = Math.max(1, ...Object.values(cells).map(c => c.goals + c.on + c.off));
  return (
    <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] tracking-widest text-stone-400">SHOT MAP</div>
        <div className="text-[9px] text-stone-500">⬆ OUR ATTACK</div>
      </div>
      <div className="grid grid-cols-3 grid-rows-3 gap-1" style={{ direction: 'ltr' }}>
        {['A', 'M', 'D'].flatMap(band =>
          ['L', 'C', 'R'].map(side => {
            const id = `${band}-${side}`;
            const c = cells[id];
            const n = c ? c.goals + c.on + c.off : 0;
            const alpha = n ? 0.12 + 0.5 * (n / maxN) : 0;
            return (
              <div
                key={id}
                className="rounded-lg border border-stone-800 flex flex-col items-center justify-center py-2.5 min-h-[52px]"
                style={{ background: n ? `rgba(163, 230, 53, ${alpha})` : 'rgba(28,25,23,0.4)' }}
              >
                {n > 0 ? (
                  <>
                    <div className="font-display text-lg leading-none text-white">{n}</div>
                    <div className="text-[9px] text-stone-300 mt-0.5">
                      {c.goals > 0 && <span>⚽{c.goals} </span>}
                      {c.on > 0 && <span>🎯{c.on} </span>}
                      {c.off > 0 && <span>❌{c.off}</span>}
                    </div>
                  </>
                ) : (
                  <div className="text-[10px] text-stone-700">·</div>
                )}
              </div>
            );
          })
        )}
      </div>
      <div className="flex justify-between mt-1.5 text-[9px]">
        <span className="text-stone-600">{total - untagged} of {total} shots zone-tagged</span>
        {untagged > 0 && <span className="text-amber-400/90">✅ {untagged} untagged — tag them in the confirm queue</span>}
      </div>
    </div>
  );
}

/* ---------- FORMATION EDIT SHEET ----------
 * Per-half manual correction for the formation label. The computed value
 * (coach's resets-first rule) stays the default; a coach pick is stored in
 * game.formationOverrides = { "<period>": "2-3-1" } and always wins on
 * display. AUTO clears the override back to the computed label.
 */
function FormationEditSheet({ period, current, computed, onSave, onClose }) {
  // All ways to split this many outfield players into 3 positive rows.
  const n = (current || computed || '').split('-').reduce((s, v) => s + (parseInt(v) || 0), 0) || 6;
  const shapes = [];
  for (let d = 1; d <= n - 2; d++) {
    for (let m = 1; m <= n - d - 1; m++) shapes.push(`${d}-${m}-${n - d - m}`);
  }
  return (
    <div className="fixed inset-0 bg-black/70 z-[60] flex items-end sm:items-center justify-center p-0 sm:p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-stone-950 border-t-2 sm:border-2 border-stone-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md"
      >
        <div className="flex items-center justify-between p-4 border-b border-stone-800">
          <div>
            <div className="font-display text-lg leading-none">FORMATION — {period === 2 ? '2ND' : '1ST'} HALF</div>
            <div className="text-[11px] text-stone-400 tracking-wider mt-1">
              Computed: {computed || '—'}{current && current !== computed ? ` · your pick: ${current}` : ''}
            </div>
          </div>
          <button onClick={onClose} className="w-10 h-10 rounded-full bg-stone-800 flex items-center justify-center active:scale-95 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-4 gap-2">
            {shapes.map(s => (
              <button
                key={s}
                onClick={() => { onSave(s); onClose(); }}
                className={`rounded-xl border-2 py-3 font-display text-base active:scale-[0.97] transition ${s === (current || computed) ? 'bg-lime-900/60 border-lime-500 text-lime-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
              >
                {s}
              </button>
            ))}
          </div>
          <button
            onClick={() => { onSave(null); onClose(); }}
            className={`mt-3 w-full py-3 rounded-xl font-display text-sm active:scale-[0.97] transition ${current ? 'bg-stone-900 text-stone-300 border border-stone-700' : 'bg-lime-500/15 text-lime-300 border border-lime-700'}`}
          >
            ↺ AUTO — use the computed label
          </button>
        </div>
      </div>
    </div>
  );
}

function AnalyticsPanel({ game, roster, onClose, onSeekVideo, onDeleteVideos, onUpdateGame }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [clips, setClips] = useState([]);
  const [broadcastOpen, setBroadcastOpen] = useState(null); // 'tv_reel' | 'auto_highlights' | null
  const [openHeatmap, setOpenHeatmap] = useState(null); // player_id whose heatmap is expanded
  const [showFix, setShowFix] = useState(false); // IdentityFixView open?
  const [editFormation, setEditFormation] = useState(null); // period being edited

  // Persist coach identity corrections. Prefer the app's updateGame path (keeps
  // local state synced + writes teams/main/games/<id> where the pipeline reads);
  // fall back to a direct Firestore write when this panel is mounted without it
  // (e.g. the Film Room browser).
  const saveOverrides = async (overrides) => {
    if (onUpdateGame) { onUpdateGame({ identityOverrides: overrides }); return; }
    if (window.fbDb && game?.id) {
      await window.fbDb.collection('teams').doc('main').collection('games')
        .doc(game.id).update({ identityOverrides: overrides });
    }
  };

  // Per-half manual formation correction (null label clears back to AUTO).
  // Same persistence pattern as identity overrides.
  const saveFormationOverride = async (period, label) => {
    const cur = { ...(game.formationOverrides || {}) };
    if (label) cur[String(period)] = label;
    else delete cur[String(period)];
    if (onUpdateGame) { onUpdateGame({ formationOverrides: cur }); return; }
    if (window.fbDb && game?.id) {
      await window.fbDb.collection('teams').doc('main').collection('games')
        .doc(game.id).update({ formationOverrides: cur });
    }
  };

  // Swipe-back closes the panel; the reel player nests above it cleanly
  // (history coordination + run-once details live in useModalHistory).
  useModalHistory('analytics', onClose);
  // Lock body scroll so the page underneath keeps its scroll position when
  // the modal closes (otherwise iOS resets to top).
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    return () => {
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      window.scrollTo(0, scrollY);
    };
  }, []);

  useEffect(() => {
    if (!window.fbDb || !game?.id) { setLoading(false); return; }
    let cancelled = false;
    const ref = window.fbDb.collection('teams').doc('main')
      .collection('games').doc(game.id)
      .collection('analytics').doc('v1');
    ref.get()
      .then(snap => { if (!cancelled) { setDoc(snap.exists ? snap.data() : null); setLoading(false); } })
      .catch(e => { if (!cancelled) { setErr(String(e)); setLoading(false); } });
    const clipsRef = window.fbDb.collection('teams').doc('main')
      .collection('games').doc(game.id).collection('clips');
    clipsRef.get()
      .then(qs => { if (!cancelled) setClips(qs.docs.map(d => d.data())); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [game?.id]);

  const playerName = (pid) => {
    const p = roster.find(r => r.id === pid);
    if (!p) return pid || '—';
    return p.number != null ? `#${p.number} ${p.name}` : p.name;
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-stone-950 z-50 flex items-center justify-center text-stone-300" style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
        Loading analytics…
      </div>
    );
  }

  // ---- derived analytics view-model (team summary + cards) ----
  const rosterById = Object.fromEntries(roster.map(p => [p.id, p]));
  const pstats = [...((doc && doc.player_stats) || [])].sort((a, b) => (b.minutes_played || 0) - (a.minutes_played || 0));
  // Coach-tagged positions, keyed by player. Written by tracking.click_publish
  // after a click-sampling session. When a player appears here his positional
  // figures come from his tags instead of from tracking — see the per-player card.
  const clickStats = (doc && doc.click_stats) || null;
  const clickByPlayer = Object.fromEntries(
    ((clickStats && clickStats.players) || []).map(p => [p.player_id, p]));
  const clickShape = (clickStats && clickStats.heatmap_shape) || [12, 8];
  // Team minutes come from the coach's SUB taps, so unlike a tracked peak they
  // don't depend on identity or coverage at all.
  const teamMinutes = pstats.reduce((s, p) => s + (p.minutes_played || 0), 0);
  // teamKm / teamSprints / teamThirds removed — all three were aggregates OVER
  // the retired per-player tracked numbers, and aggregating does not repair a
  // contaminated input when the operation is a sum or a mean over paths. Team
  // SHAPE metrics (field tilt, compactness) are kept instead: they read the set
  // of positions at an instant, so they never depend on which name is attached.
  // goals from the event log (GOAL = us, OPP_GOAL = them), with minute
  const _hl = game.halfLengthMin || 25;
  const goals = (game.events || [])
    .filter(e => e.type === 'GOAL' || e.type === 'OPP_GOAL')
    .map(e => ({
      us: e.type === 'GOAL',
      min: Math.max(1, Math.floor((e.elapsed || 0) / 60) + 1) + ((e.period || 1) === 2 ? _hl : 0),
      pid: e.playerId,
    }))
    .sort((a, b) => a.min - b.min);
  const gameMaxMin = Math.max(_hl * 2, ...goals.map(g => g.min));
  const result = game.ourScore > game.oppScore ? 'WIN' : game.ourScore < game.oppScore ? 'LOSS' : 'DRAW';
  // per-player derived: position label + identity confidence
  const posLabel = (s) => {
    if (game.gkPlayerId && s.player_id === game.gkPlayerId) return 'GOALKEEPER';
    const t = [s.pct_defensive_third || 0, s.pct_middle_third || 0, s.pct_attacking_third || 0];
    const i = t.indexOf(Math.max(...t));
    return ['DEFENDER', 'MIDFIELDER', 'FORWARD'][i];
  };
  // Per-player identity confidence = track-time-weighted average of the
  // assigned tracks' confidences (how much of this player's tracked data is
  // reliable). NOT the max — almost everyone has one perfect track, which made
  // every badge read ~100%.
  const playerConf = (pid) => {
    const a = (doc.identity_assignments || []).filter(x => x.player_id === pid && x.confidence != null);
    if (!a.length) return null;
    let wsum = 0, csum = 0;
    for (const x of a) { const w = (x.minutes_on_field || 0) + 0.01; wsum += w; csum += w * x.confidence; }
    return wsum ? csum / wsum : null;
  };

  return (
    <div className="fixed inset-0 bg-stone-950 z-50 overflow-y-auto">
      <div
        className="sticky top-0 stripes-bg text-white border-b border-stone-800 px-4 pb-3 flex items-center justify-between z-10"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <h2 className="font-display text-lg truncate pr-3">📊 ANALYTICS — {game.opponent}</h2>
        <div className="shrink-0 flex items-center gap-2">
          {doc && Array.isArray(doc.tracklets) && doc.tracklets.length > 0 && (
            <button
              onClick={() => setShowFix(true)}
              className="h-9 px-3 rounded-full bg-lime-500/20 hover:bg-lime-500/30 text-lime-300 font-display text-xs flex items-center gap-1 border border-lime-500/40 active:scale-95"
            >
              🪪 FIX IDS
            </button>
          )}
          <button
            onClick={onClose}
            className="h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95"
          >
            CLOSE ✕
          </button>
        </div>
      </div>
      {showFix && (
        <IdentityFixView
          doc={doc}
          roster={roster}
          game={game}
          onSave={saveOverrides}
          onClose={() => setShowFix(false)}
        />
      )}

      {err && (
        <div className="m-4 p-3 bg-red-900/40 border border-red-800 rounded-xl text-sm text-red-200">
          Error loading analytics: {err}
        </div>
      )}

      {!doc && !err && (
        <div className="m-4 p-4 bg-stone-900 border border-stone-800 rounded-xl text-sm text-stone-300 space-y-2">
          <div className="font-bold">Analytics not yet available for this game.</div>
          <div className="text-stone-400">
            On your Mac, run:
          </div>
          <pre className="bg-black/60 p-2 rounded text-xs overflow-x-auto">
{`cd ~/match-tracker
./run_analytics.sh ${game.id}`}
          </pre>
          <button
            onClick={async () => {
              const cmd = `cd ~/match-tracker && ./run_analytics.sh ${game.id}`;
              try {
                if (navigator.clipboard && window.isSecureContext) {
                  await navigator.clipboard.writeText(cmd);
                } else {
                  const ta = document.createElement('textarea');
                  ta.value = cmd; document.body.appendChild(ta);
                  ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
                }
                alert('Command copied. Paste it into Terminal on your Mac.');
              } catch {
                prompt('Copy this command:', cmd);
              }
            }}
            className="w-full mt-1 py-2 rounded-lg bg-lime-600 text-stone-950 text-xs font-bold active:scale-95"
          >✎ COPY COMMAND</button>
          <div className="text-stone-500 text-xs">
            A browser tab will open to mark the 4 field corners (first time only).
            After SAVE, the pipeline runs and results land here automatically.
          </div>
        </div>
      )}

      {doc && (
        <div className="p-4 space-y-5 max-w-3xl mx-auto">
          {/* Broadcast videos — prominent at top */}
          {(doc.auto_highlights_url || doc.tv_reel_url) && (
            <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4 space-y-2">
              <div className="text-xs text-stone-500 uppercase mb-1">Match Video</div>
              {doc.auto_highlights_url && (
                <button
                  onClick={() => setBroadcastOpen('auto_highlights')}
                  className="w-full flex items-center justify-between bg-lime-600 hover:bg-lime-500 text-stone-950 font-display rounded-lg px-4 py-3 active:scale-[0.98]"
                >
                  <span className="flex items-center gap-2">▶ WATCH HIGHLIGHTS</span>
                  <span className="text-xs tabular-nums opacity-80">
                    {doc.auto_highlights_duration_s
                      ? `${Math.floor(doc.auto_highlights_duration_s / 60)}:${String(Math.floor(doc.auto_highlights_duration_s % 60)).padStart(2, '0')}`
                      : ''}
                  </span>
                </button>
              )}
              {doc.tv_reel_url && (
                <button
                  onClick={() => setBroadcastOpen('tv_reel')}
                  className="w-full flex items-center justify-between bg-stone-800 hover:bg-stone-700 text-white font-display rounded-lg px-4 py-3 border border-stone-700 active:scale-[0.98]"
                >
                  <span className="flex items-center gap-2">▶ WATCH FULL GAME</span>
                  <span className="text-xs tabular-nums text-stone-400">
                    {doc.tv_reel_duration_s
                      ? `${Math.floor(doc.tv_reel_duration_s / 60)}:${String(Math.floor(doc.tv_reel_duration_s % 60)).padStart(2, '0')}`
                      : ''}
                  </span>
                </button>
              )}
              {onDeleteVideos && (doc.auto_highlights_url || doc.tv_reel_url) && (
                <button
                  onClick={() => { onClose?.(); onDeleteVideos(); }}
                  className="w-full mt-1 text-[11px] text-red-400 hover:text-red-300 tracking-wider py-2 active:scale-95"
                  title="Wipe the videos from R2 but keep player stats"
                >
                  🗑 DELETE VIDEOS ONLY (keep stats)
                </button>
              )}
            </section>
          )}

          {/* Team summary */}
          <section className="rounded-2xl p-4 border border-stone-800" style={{ background: 'linear-gradient(160deg,#16271b,#121214)' }}>
            <div className="flex items-start justify-between mb-3">
              <div className="min-w-0">
                <div className="text-[10px] tracking-widest text-stone-400">{(game.tournament || gameTypeOf(game)).toUpperCase()}{game.date ? ` · ${game.date}` : ''}</div>
                <div className="text-white font-display text-lg leading-tight truncate">Stompers <span className="text-stone-500">vs</span> {game.opponent || 'OPP'}</div>
              </div>
              <div className="text-right shrink-0 pl-3">
                <div className="font-display text-3xl text-white leading-none">{game.ourScore}<span className="text-stone-600">–</span>{game.oppScore}</div>
                <div className={`text-[10px] tracking-widest font-bold mt-0.5 ${result === 'WIN' ? 'text-lime-400' : result === 'LOSS' ? 'text-red-400' : 'text-stone-400'}`}>{result}</div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 mb-3">
              {/* TOP KM/H removed: it is one player's single peak, the least
                  reproducible number we had (repeatability 0.476).

                  KM TOTAL and SPRINTS removed too. They are plain SUMS over the
                  per-player tracked distances, so they inherit every defect of
                  the per-player numbers that were just retired — summing a
                  3-4x-low distance over 12 players gives a 3-4x-low team
                  distance, and swap teleports inflate the sprint count. Being
                  aggregates did NOT rescue them: unlike team SHAPE (a function of
                  the set of positions at an instant, so permutation-invariant and
                  identity-proof), a distance sum integrates each player's PATH,
                  which is exactly what wrong-child contamination corrupts.
                  MINUTES and PLAYERS come from the coach's own taps. */}
              {[[teamMinutes.toFixed(0), 'MINUTES'], [pstats.length, 'PLAYERS'], [goals.length, 'GOAL EVENTS']].map(([v, l]) => (
                <div key={l} className="rounded-xl border border-stone-700/60 p-2 text-center" style={{ background: 'linear-gradient(160deg,#202024,#161618)' }}>
                  <div className="text-white font-display text-lg leading-none">{v}</div>
                  <div className="text-[9px] text-stone-400 mt-1">{l}</div>
                </div>
              ))}
            </div>
            {goals.length > 0 && (
              <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3 mb-3">
                <div className="text-[10px] tracking-widest text-stone-400 mb-2">GOALS</div>
                <div className="relative h-8">
                  <div className="absolute left-0 right-0 top-1/2 h-px bg-stone-700" />
                  {goals.map((g, i) => (
                    <div key={i} className="absolute -translate-x-1/2 flex flex-col items-center" style={{ left: `${Math.min(96, Math.max(2, (g.min / gameMaxMin) * 100))}%`, [g.us ? 'bottom' : 'top']: '50%' }}>
                      {g.us
                        ? (<><div className="w-2 h-2 rounded-full bg-lime-400" /><span className="text-[8px] text-lime-300 leading-none mt-0.5">{g.min}'</span></>)
                        : (<><span className="text-[8px] text-red-300 leading-none mb-0.5">{g.min}'</span><div className="w-2 h-2 rounded-full bg-red-500" /></>)}
                    </div>
                  ))}
                </div>
                <div className="text-[9px] text-stone-500 mt-1 truncate">
                  <span className="text-lime-400">●</span> {goals.filter(g => g.us).map(g => (rosterById[g.pid]?.name?.split(' ')[0] || 'Goal') + " " + g.min + "'").join(' · ') || '—'}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
                <div className="text-[10px] tracking-widest text-stone-400 mb-2">FORMATION <span className="text-stone-600">· tap to edit</span></div>
                {/* Coach override > coach-board computation > doc snapshot
                    fallback (games without POSITION events). */}
                {(doc.formation_snapshots || []).length ? doc.formation_snapshots.map(f => {
                  const override = (game.formationOverrides || {})[String(f.period)];
                  const computed = coachKickoffFormation(game, f.period) || f.label;
                  return (
                    <button
                      key={f.period}
                      onClick={() => setEditFormation(f.period)}
                      className="w-full flex items-center gap-2 text-white active:scale-[0.98] transition text-left"
                    >
                      <span className="text-[10px] text-stone-500 w-7">{f.period === 2 ? '2ND' : '1ST'}</span>
                      <span className="font-display text-base">{override || computed}</span>
                      {override && <span className="text-[9px] font-bold tracking-wider text-amber-400">✎</span>}
                    </button>
                  );
                }) : <div className="text-stone-500 text-xs">—</div>}
              </div>
              {/* "TIME BY THIRD" was here: a minutes-weighted average of the
                  per-player TRACKED thirds. It was a second, worse answer to the
                  question FIELD TILT below already answers — and it inherited the
                  per-player identity contamination, while field tilt is computed
                  from the team CENTROID and is therefore permutation-invariant.
                  Two bars claiming where the game lived, disagreeing, was exactly
                  the duplication to remove. */}
            </div>
            <div className="mt-2 space-y-2">
              <MomentumChart game={game} />
              <TeamShapeChart doc={doc} halfLenS={(game.halfLengthMin || 25) * 60} />
              <ShotMap games={[game]} />
              {/* 4.6 — team-centroid third occupancy: where the game lived.
                  Prefers per-half (field_tilt.byHalf, newer pipeline runs) and
                  falls back to the game-wide bar for older docs. */}
              {doc.field_tilt && (
                <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
                  <div className="text-[10px] tracking-widest text-stone-400 mb-2">FIELD TILT <span className="text-stone-600">· where the game lived (possession proxy)</span></div>
                  {Array.isArray(doc.field_tilt.byHalf) && doc.field_tilt.byHalf.length ? (
                    <div className="space-y-2">
                      {doc.field_tilt.byHalf.map(h => (
                        <div key={h.period}>
                          <div className="flex items-center gap-2">
                            <span className="w-5 shrink-0 text-[9px] font-bold text-stone-400">H{h.period}</span>
                            <div className="flex-1 flex h-2 rounded-full overflow-hidden">
                              <div style={{ width: `${h.att_pct || 0}%`, background: '#a3e635' }} title="Their third" />
                              <div style={{ width: `${h.mid_pct || 0}%`, background: '#eab308' }} />
                              <div style={{ width: `${h.def_pct || 0}%`, background: '#ef4444' }} />
                            </div>
                          </div>
                          <div className="flex justify-between text-[9px] text-stone-500 ml-7">
                            <span className="text-lime-300">Their {(h.att_pct || 0).toFixed(0)}%</span>
                            <span className="text-yellow-300">Mid {(h.mid_pct || 0).toFixed(0)}%</span>
                            <span className="text-red-300">Ours {(h.def_pct || 0).toFixed(0)}%</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <>
                      <div className="flex h-2 rounded-full overflow-hidden mb-1">
                        <div style={{ width: `${doc.field_tilt.att_pct || 0}%`, background: '#a3e635' }} title="Their half deep" />
                        <div style={{ width: `${doc.field_tilt.mid_pct || 0}%`, background: '#eab308' }} />
                        <div style={{ width: `${doc.field_tilt.def_pct || 0}%`, background: '#ef4444' }} />
                      </div>
                      <div className="flex justify-between text-[9px] text-stone-500">
                        <span className="text-lime-300">Their third {(doc.field_tilt.att_pct || 0).toFixed(0)}%</span>
                        <span className="text-yellow-300">Middle {(doc.field_tilt.mid_pct || 0).toFixed(0)}%</span>
                        <span className="text-red-300">Our third {(doc.field_tilt.def_pct || 0).toFixed(0)}%</span>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="text-[9px] text-stone-600 mt-3">Generated {doc.generated_at_ms ? new Date(doc.generated_at_ms).toLocaleString() : '—'}</div>
          </section>

          {/* Per-player deck cards */}
          <div className="text-stone-300 font-display text-sm tracking-wide px-1 mt-1">PLAYERS</div>
          {pstats.length === 0 ? (
            <div className="text-sm text-stone-400">No player stats.</div>
          ) : (
            <div className="space-y-3">
              {pstats.map(s => {
                const p = rosterById[s.player_id];
                // Coach-tagged positions for this player, if he tagged this game.
                // This is the ONLY per-player positional source the card renders —
                // see the block comments below for why the tracked fallback was
                // removed rather than kept behind a caption.
                const cp = clickByPlayer[s.player_id] || null;
                // Coverage = tracked time / coach minutes. Retained purely to tell
                // the coach how much the camera saw of an UNTAGGED player, as
                // context for the tagging prompt. It no longer grades any number,
                // because no tracked per-player number is shown.
                const coverage = (s.tracked_seconds != null && (s.minutes_played || 0) > 0)
                  ? (s.tracked_seconds / 60) / s.minutes_played : null;
                const coveragePct = coverage != null ? Math.min(100, Math.round(coverage * 100)) : null;
                // The per-tile trust grading (amber/red borders + corner dots, plus
                // a dashed "—" state) is gone with the movement tiles it graded.
                // Every tile now shown is either the coach's own SUB minutes or a
                // figure derived from his own tags, so there is no per-tile trust
                // to communicate. Grading tiles was a way of shipping numbers
                // known to be unreliable; removing the numbers is the fix.
                return (
                  <div key={s.player_id} className="rounded-2xl border border-stone-800 bg-stone-900 p-4">
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <PlayerAvatar player={p} sizeClass="w-11 h-11" rounded="rounded-xl" textSize="text-lg" />
                        <div className="min-w-0">
                          <div className="text-white font-display text-base leading-tight truncate">{p?.name || s.player_id}</div>
                          <div className="flex items-center gap-2">
                            {p?.number != null && <span className="text-[10px] text-stone-400 tabular-nums">#{p.number}</span>}
                          </div>
                        </div>
                      </div>
                      <div className="text-right shrink-0 pl-2">
                        {/* Only the TAGGED badge survives. IDENTITY %, ⚠ INFLATED,
                            ⚠ LOW TRACKING and 🧹 CLEANED were all quality marks on
                            the tracked movement numbers; with those numbers gone
                            they grade nothing. They also can't be shown over
                            tagged positions — a "48% IDENTITY" badge above
                            positions the coach assigned himself would undermine
                            the more trustworthy source. */}
                        {cp && (
                          <>
                            <div className="text-[9px] text-stone-500">TAGGED</div>
                            <div className="text-xs font-bold text-lime-400">● {cp.n_clicks}</div>
                          </>
                        )}
                      </div>
                    </div>
                    {/* TOP km/h was removed here on purpose. It is the p99 of a
                        smoothed speed series over a player's REAL-motion steps, so
                        for anyone with thin coverage it is effectively one sample:
                        split-half repeatability measured 0.476, and the raw-max
                        variant scored -0.20 (worse than chance). It rendered as the
                        most authoritative number on the card while being the least
                        reproducible. `top_speed_ms` still ships in the analytics doc
                        because the personalized sprint threshold is derived from the
                        median of prior games' values (firestore_io
                        .collect_prior_player_top_speeds) — it just isn't shown.
                        A p95-speed replacement (repeatability 0.651) is the intended
                        successor but is not computed yet; don't add a headline number
                        back until it is validated on the two ground-truth games. */}
                    {/* ONE set of numbers per player, and only numbers the source
                        can carry.

                        TAGGED: positions the coach assigned himself, ±1.7 m. No
                        distance/speed/sprints — a tag samples a position, and
                        between two samples 30 s apart a child could have run 5 m
                        or 80 m, so no estimator recovers the path.

                        UNTAGGED: movement tiles are GONE. They were AVG km/h,
                        M/MIN and SPRINTS off the tracked source, which carries
                        ~23% wrong-child contamination and a median 6 s track
                        life, so distance runs 3-4x low while swaps push sprints
                        up. Four separate caveat blocks below used to explain, per
                        player, why the three numbers above them shouldn't be
                        read — which is the tell that they shouldn't have been
                        rendered. A number on a card gets quoted regardless of its
                        footnote. MIN stays: it comes from the coach's own SUB
                        taps, not from tracking. */}
                    <div className={`grid ${cp ? 'grid-cols-4' : 'grid-cols-1'} gap-1.5 mb-1`}>
                      {(cp ? [
                        [`${(s.minutes_played || 0).toFixed(0)}'`, 'MIN'],
                        [`${(cp.avg_depth_m || 0).toFixed(1)}`, 'AVG m OUT'],
                        // Subtracting two 1-dp floats is not 1-dp: 46.6-11.2 is
                        // 35.400000000000006 in IEEE 754, and it rendered raw.
                        [`${((cp.p90_depth_m || 0) - (cp.p10_depth_m || 0)).toFixed(1)}`, 'RANGE m'],
                        [`${Math.round(cp.area_covered_m2 || 0)}`, 'AREA m²'],
                      ] : [
                        [`${(s.minutes_played || 0).toFixed(0)}'`, 'MINUTES PLAYED'],
                      ]).map(([v, l]) => (
                        <div key={l} className="rounded-xl border border-stone-700/60 p-2 text-center" style={{ background: 'linear-gradient(160deg,#202024,#161618)' }}>
                          <div className="text-white font-display text-sm leading-none">{v}</div>
                          <div className="text-[8px] text-stone-400 mt-1 leading-tight">{l}</div>
                        </div>
                      ))}
                    </div>
                    {cp && (
                      <div className="text-[9px] text-lime-600/90 mb-2 leading-snug">
                        🖱 Positions from {cp.n_clicks} moments you tagged yourself, accurate
                        to ±{cp.pos_err_m} m. No distance or speed — tagging shows where he
                        was, not the path between.
                      </div>
                    )}
                    {/* One statement replaces four. The old card carried separate
                        blocks for low coverage, swap pollution, conflict cleaning
                        and work-rate caveats — all of them explaining why the
                        movement numbers above them were wrong. With those numbers
                        gone there is nothing to caveat, so this says what IS
                        available and how to get it, once. */}
                    {!cp && (
                      <div className="text-[9px] text-stone-500 mb-2 leading-snug">
                        🖱 Not tagged yet — positions, heatmap and territory need about
                        10 minutes of tagging in the film-room sampler
                        {coveragePct != null && <span> (the camera tracked {coveragePct}% of his
                          minutes, but can't reliably tell which child is which, so those
                          tracks can't carry his name)</span>}.
                      </div>
                    )}
                    {/* ONE set of positional numbers per player, from ONE source:
                        the coach's own tags. There is deliberately no tracked
                        fallback. The tracked per-player position carries ~23%
                        wrong-child contamination and can rest on 12% coverage, so
                        rendering it in the same bar and the same heatmap widget as
                        the tagged version — differing only by a caption — invited
                        exactly the cross-reading the coach objected to. A player
                        without tags shows no position rather than a wrong one. */}
                    {cp && (
                      <>
                        <div className="mb-2">
                          <div className="flex h-2 rounded-full overflow-hidden">
                            <div style={{ width: `${cp.pct_defensive_third || 0}%`, background: '#ef4444' }} />
                            <div style={{ width: `${cp.pct_middle_third || 0}%`, background: '#eab308' }} />
                            <div style={{ width: `${cp.pct_attacking_third || 0}%`, background: '#a3e635' }} />
                          </div>
                          <div className="flex justify-between text-[9px] text-stone-500 mt-1">
                            <span>Def {(cp.pct_defensive_third || 0).toFixed(0)}%</span>
                            <span>Mid {(cp.pct_middle_third || 0).toFixed(0)}%</span>
                            <span>Att {(cp.pct_attacking_third || 0).toFixed(0)}%</span>
                          </div>
                          <div className="text-[8px] text-lime-600 mt-0.5">
                            from your {cp.n_clicks} tagged positions · L {cp.pct_left}% C {cp.pct_centre}% R {cp.pct_right}%
                          </div>
                        </div>
                        <div className="rounded-xl border border-lime-800/50 bg-stone-950/40">
                          <div className="text-[8px] text-lime-600 px-2 pt-1">
                            Where he played · avg {cp.avg_depth_m} m out, territory {cp.p10_depth_m}–{cp.p90_depth_m} m · ±{cp.pos_err_m} m
                            {cp.drift && cp.drift.significant &&
                              ` · 2nd half ${cp.drift.depth_m > 0 ? '+' : ''}${cp.drift.depth_m} m ${cp.drift.depth_m > 0 ? 'higher' : 'deeper'}`}
                          </div>
                          <PlayerHeatmap grid={cp.heatmap} rows={clickShape[0]} cols={clickShape[1]} />
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Identity-review track list removed — there's no coach-side review
              mechanism, and per-track confidence is meaningless to a coach. Each
              player card now carries an identity-confidence badge instead.
              Formation is shown in the team summary above. */}

          {/* Per-event highlight clips intentionally removed — replaced
              by the broadcast-style auto-highlights + full TV reel videos
              (rendered with on-screen score / goal / sub overlays). */}
        </div>
      )}
      {broadcastOpen && (
        <BroadcastVideoPlayer
          url={broadcastOpen === 'tv_reel' ? doc?.tv_reel_url : doc?.auto_highlights_url}
          doc={doc}
          label={broadcastOpen === 'tv_reel' ? `FULL GAME — ${game.opponent}` : `HIGHLIGHTS — ${game.opponent}`}
          timeKey={broadcastOpen === 'tv_reel' ? 'tvReelTimeS' : 'autoHighlightsTimeS'}
          labelsUrl={broadcastOpen === 'tv_reel' ? (doc?.review_labels_url || null) : null}
          roster={roster}
          onClose={() => setBroadcastOpen(null)}
        />
      )}
      {editFormation != null && (
        <FormationEditSheet
          period={editFormation}
          current={(game.formationOverrides || {})[String(editFormation)] || null}
          computed={coachKickoffFormation(game, editFormation)
            || (doc?.formation_snapshots || []).find(f => f.period === editFormation)?.label
            || null}
          onSave={(label) => saveFormationOverride(editFormation, label)}
          onClose={() => setEditFormation(null)}
        />
      )}
    </div>
  );
}

/* ---------- GAME DETAIL ---------- */
function GameDetail({ game, roster, weights, opponentSuggestions = [], onBack, onDelete, onDeleteVideos, onDeleteEvent, onUpdateEvent, onUpdateGame }) {
  const events = [...game.events].filter(e => e.type !== 'POSITION').sort((a, b) => a.at - b.at);
  const result = game.ourScore > game.oppScore ? 'WIN' : game.ourScore < game.oppScore ? 'LOSS' : 'DRAW';
  const resultColor = result === 'WIN' ? 'text-lime-400' : result === 'LOSS' ? 'text-red-400' : 'text-white/70';
  const [shareMsg, setShareMsg] = useState(null);
  const [scoreView, setScoreView] = useState('raw'); // 'raw' | 'squad' — pillar display mode
  const [taggingEvent, setTaggingEvent] = useState(null);
  const [showVideo, setShowVideo] = useState(false);
  const [seekTo, setSeekTo] = useState(null);
  const [linkingVideo, setLinkingVideo] = useState(false);
  const [linkInput, setLinkInput] = useState('');
  const [uploadPct, setUploadPct] = useState(null); // null = idle, 0-100 = uploading
  const [uploadErr, setUploadErr] = useState(null);
  const fileInputRef = useRef(null);
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveErr, setLiveErr] = useState(null);
  const [showLive, setShowLive] = useState(false);
  const [showLiveCreds, setShowLiveCreds] = useState(false);
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [showEditInfo, setShowEditInfo] = useState(false);

  // Each time the user enters a different game from the DUGOUT list, scroll
  // back to the top — otherwise the view keeps the previous page's scroll
  // position and lands mid-page (e.g. on the TIMELINE section).
  useEffect(() => {
    try { window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); }
    catch { window.scrollTo(0, 0); }
  }, [game.id]);

  // The stream credentials (rtmpsUrl/streamKey/uid) live in the coach-only
  // games/<id>/private/liveInput subdoc; the game doc carries only hlsUrl.
  // This state holds the secrets while the coach has the panel open.
  const [liveCreds, setLiveCreds] = useState(null);
  const livePrivRef = () => (typeof window !== 'undefined' && window.fbDb)
    ? window.fbDb.collection('teams').doc('main').collection('games').doc(game.id).collection('private').doc('liveInput')
    : null;
  useEffect(() => {
    if (!showLiveCreds || liveCreds || !game.liveInput) return undefined;
    const ref = livePrivRef();
    if (!ref) return undefined;
    let cancelled = false;
    ref.get()
      .then((d) => { if (!cancelled && d.exists) setLiveCreds(d.data()); })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showLiveCreds, liveCreds, game.id, game.liveInput]);

  const goLive = () => {
    if (!R2_UPLOAD_WORKER) { setLiveErr('Worker URL not configured'); return; }
    setLiveBusy(true);
    setLiveErr(null);
    workerPost('/live-input', { name: `stompers-${game.id}` })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || 'live-input failed')))
      .then((info) => {
        const full = { ...info, createdAt: Date.now() };
        setLiveCreds(full);
        const ref = livePrivRef();
        if (ref) ref.set(full).catch((e) => console.error('live-input private save error:', e));
        // Public doc: playback URL only — never the stream key.
        onUpdateGame({ liveInput: { hlsUrl: full.hlsUrl || null, createdAt: full.createdAt } });
        setShowLiveCreds(true);
        setShowLive(true);
      })
      .catch((err) => setLiveErr(String(err)))
      .finally(() => setLiveBusy(false));
  };

  const stopLive = () => {
    setLiveBusy(true);
    const ref = livePrivRef();
    const uidPromise = (liveCreds && liveCreds.uid)
      ? Promise.resolve(liveCreds.uid)
      : (ref ? ref.get().then((d) => (d.exists ? (d.data() || {}).uid : null)).catch(() => null)
             : Promise.resolve(game.liveInput && game.liveInput.uid));
    uidPromise
      .then((inputUid) => (inputUid ? workerPost(`/live-input/${inputUid}/delete`) : null))
      .catch(() => {})
      .finally(() => {
        if (ref) ref.delete().catch(() => {});
        onUpdateGame({ liveInput: null });
        setLiveCreds(null);
        setShowLive(false);
        setShowLiveCreds(false);
        setLiveBusy(false);
      });
  };

  const handleUpload = (file) => {
    if (!file) return;
    setUploadErr(null);
    setUploadPct(0);
    // Filename: <gameId>-<sanitized-original>
    const ext = (file.name.match(/\.[a-zA-Z0-9]+$/) || ['.mp4'])[0];
    const base = file.name.replace(/\.[a-zA-Z0-9]+$/, '').replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 40);
    const filename = `${game.id}-${base}${ext}`;
    const contentType = file.type || 'video/mp4';
    workerPost(`/upload-url`, { filename, contentType })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || 'upload-url failed')))
      .then(({ uploadUrl, publicUrl }) => new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', uploadUrl);
        xhr.setRequestHeader('Content-Type', contentType);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setUploadPct(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve(publicUrl) : reject(`PUT failed ${xhr.status}`);
        xhr.onerror = () => reject('network error');
        xhr.send(file);
      }))
      .then((publicUrl) => {
        onUpdateGame({ videoUrl: publicUrl });
        setUploadPct(null);
      })
      .catch((err) => {
        setUploadErr(String(err));
        setUploadPct(null);
      });
  };

  const shareLiveLink = async () => {
    const url = `${window.location.origin}${window.location.pathname}?live=${game.id}`;
    const title = `Stompers vs ${game.opponent}`;
    try {
      if (navigator.share) {
        await navigator.share({ title, text: `Live score \u2014 ${title}`, url });
        return;
      }
    } catch (e) { /* user cancelled */ }
    try {
      await navigator.clipboard.writeText(url);
      setShareMsg('Link copied!');
      setTimeout(() => setShareMsg(null), 1800);
    } catch (e) {
      setShareMsg(url);
      setTimeout(() => setShareMsg(null), 4000);
    }
  };

  const tally = useMemo(() => {
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, CLEAR: 0, KICK_OUT: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GIVE_GO_WALL: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0 });
    const map = {};
    for (const e of events) {
      if (e.type === 'SUB') continue;
      if (e.playerId) {
        map[e.playerId] = map[e.playerId] || init();
        if (map[e.playerId][e.type] !== undefined) map[e.playerId][e.type]++;
      }
      // Give & go wall partner gets credit too.
      if (e.type === 'GIVE_GO' && e.partnerId) {
        map[e.partnerId] = map[e.partnerId] || init();
        map[e.partnerId].GIVE_GO_WALL++;
      }
    }
    for (const p of roster) {
      const sec = playerSeconds(p.id, game);
      if (sec > 0) {
        map[p.id] = map[p.id] || init();
        map[p.id].seconds = sec;
      }
    }
    return map;
  }, [events, roster, game]);

  return (
    <div className="pb-24">
      <div className="stripes-bg text-white px-4 pt-12 pb-5">
        <div className="flex items-center justify-between mb-3">
          <button onClick={onBack} className="text-white/70 active:scale-95">
            <ChevronLeft className="w-6 h-6" />
          </button>
          <div className={`font-display text-xl ${resultColor}`}>{result}</div>
          <div className="flex items-center gap-2">
            <button
              onClick={shareLiveLink}
              title="Share live scoreboard link"
              className="h-9 px-3 rounded-full bg-lime-400/90 text-stone-100 flex items-center gap-1 active:scale-95 font-bold text-xs"
            >
              📡 SHARE
            </button>
            <button
              onClick={onDelete}
              className="w-9 h-9 rounded-full bg-white/10 flex items-center justify-center active:scale-95"
            >
              <Trash2 className="w-4 h-4 text-white/70" />
            </button>
          </div>
        </div>
        {shareMsg && (
          <div className="text-center text-xs text-lime-300 -mt-1 mb-2">{shareMsg}</div>
        )}
        <div className="text-center text-xs text-white/70 mb-1 flex items-center justify-center gap-1.5">
          <span>{game.tournament || gameTypeOf(game).toUpperCase()} · {formatDate(game.date)}</span>
          <FormatChip value={game.format} />
        </div>
        <div className="text-center font-display text-2xl">vs {game.opponent}</div>
        <div className="text-center font-display text-6xl tabular-nums mt-2">
          {game.ourScore} <span className="text-white/40">–</span> {game.oppScore}
        </div>
      </div>

      {/* Live Stream Section */}
      {game.youtubeVideoId && (
        <div className="px-4 pt-4">
          <div className="bg-red-950/40 border border-red-800 rounded-xl px-4 py-2 flex items-center gap-2 mb-3">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-sm font-bold text-red-300">{game.status === 'active' ? '🔴 LIVE' : '▶ REPLAY'}</span>
          </div>
          <YouTubeEmbed videoId={game.youtubeVideoId} live={game.status === 'active'} />
        </div>
      )}
      {!game.youtubeVideoId && LIVE_MODE !== 'off' && (game.liveInput || game.status === 'active') && (
        <div className="px-4 pt-4">
          {game.liveInput ? (
            <>
              <button
                onClick={() => setShowLive(!showLive)}
                className="w-full bg-red-950/40 border border-red-800 rounded-xl px-4 py-3 flex items-center justify-between active:scale-[0.98] transition"
              >
                <span className="text-sm font-bold text-red-300 flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  LIVE NOW
                </span>
                <span className="text-xs text-stone-400">{showLive ? 'HIDE' : 'WATCH'}</span>
              </button>
              {showLive && game.liveInput.hlsUrl && (
                <div className="mt-3">
                  <VideoPlayer360
                    videoUrl={game.liveInput.hlsUrl}
                    events={game.events || []}
                    onClose={() => setShowLive(false)}
                    gameInfo={{ home: 'Stompers', away: game.opponent || 'OPP', homeScore: game.ourScore, awayScore: game.oppScore, period: game.period || 1, halfLengthMin: game.halfLengthMin || 25, homeColor: game.homeColor, awayColor: game.awayColor, status: game.status, gameId: game.id }}
                  />
                </div>
              )}
              {showLiveCreds && liveCreds && liveCreds.streamKey && (
                <div className="mt-3 bg-stone-900 border border-stone-800 rounded-xl p-3 space-y-2 text-xs">
                  <p className="text-stone-400 font-bold">📡 Stream from X5 / OBS using these credentials:</p>
                  <div>
                    <div className="text-stone-500">RTMPS Server</div>
                    <code className="block bg-stone-800 p-2 rounded text-lime-400 break-all">{liveCreds.rtmpsUrl}</code>
                  </div>
                  <div>
                    <div className="text-stone-500">Stream Key</div>
                    <code className="block bg-stone-800 p-2 rounded text-lime-400 break-all">{liveCreds.streamKey}</code>
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={() => setShowLiveCreds(false)}
                      className="flex-1 py-2 rounded-lg bg-stone-800 text-stone-300 font-bold active:scale-95"
                    >HIDE CREDENTIALS</button>
                    <button
                      onClick={() => { if (confirm('Stop the live stream? This deletes the Cloudflare Live Input.')) stopLive(); }}
                      disabled={liveBusy}
                      className="flex-1 py-2 rounded-lg bg-red-700 text-white font-bold active:scale-95 disabled:opacity-50"
                    >END LIVE</button>
                  </div>
                </div>
              )}
              {!showLiveCreds && (
                <button
                  onClick={() => setShowLiveCreds(true)}
                  className="mt-2 text-[10px] text-stone-500 underline w-full text-center"
                >show stream credentials</button>
              )}
            </>
          ) : (
            <>
              {liveErr && <p className="text-[10px] text-red-400 text-center mb-2">{liveErr}</p>}
              <button
                onClick={goLive}
                disabled={liveBusy || !R2_UPLOAD_WORKER}
                className="w-full bg-stone-900 border border-dashed border-red-700 rounded-xl px-4 py-3 flex items-center justify-center gap-2 active:scale-[0.98] transition disabled:opacity-40"
              >
                <span className="text-sm text-red-400">
                  {liveBusy ? '⏳ STARTING…' : R2_UPLOAD_WORKER ? '🔴 GO LIVE (Cloudflare Stream)' : '🔴 GO LIVE (deploy worker first)'}
                </span>
              </button>
            </>
          )}
        </div>
      )}

      {/* 360° Video Section */}
      <div className="px-4 pt-4">
        {game.videoUrl ? (
          <>
            <button
              onClick={() => setShowVideo(!showVideo)}
              className="w-full bg-stone-900 border border-stone-800 rounded-xl px-4 py-3 flex items-center justify-between active:scale-[0.98] transition"
            >
              <span className="text-sm font-bold">🎥 360° GAME VIDEO</span>
              <span className="text-xs text-stone-400">{showVideo ? 'HIDE' : 'WATCH'}</span>
            </button>
            {showVideo && (
              <div className="mt-3">
                <VideoPlayer360
                  videoUrl={game.videoUrl}
                  seekTo={seekTo}
                  events={events}
                  onClose={() => setShowVideo(false)}
                  gameInfo={{ home: 'Stompers', away: game.opponent || 'OPP', homeScore: game.ourScore, awayScore: game.oppScore, period: game.period || 1, halfLengthMin: game.halfLengthMin || 25, homeColor: game.homeColor, awayColor: game.awayColor, status: game.status, gameId: game.id }}
                />
                <p className="text-[10px] text-stone-500 mt-1 text-center">Tap an event below to jump to that moment</p>
              </div>
            )}
          </>
        ) : (
          <>
            {uploadPct !== null ? (
              <div className="bg-stone-900 border border-stone-800 rounded-xl p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-stone-400">Uploading…</span>
                  <span className="text-xs text-lime-400 font-bold tabular-nums">{uploadPct}%</span>
                </div>
                <div className="w-full h-1.5 bg-stone-800 rounded-full overflow-hidden">
                  <div className="h-full bg-lime-500 transition-all" style={{ width: `${uploadPct}%` }} />
                </div>
              </div>
            ) : !linkingVideo ? (
              <div className="space-y-2">
                {uploadErr && <p className="text-[10px] text-red-400 text-center">Upload failed: {uploadErr}</p>}
                <div className="flex gap-2">
                  {R2_UPLOAD_WORKER && (
                    <>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="video/*"
                        className="hidden"
                        onChange={(e) => { handleUpload(e.target.files?.[0]); e.target.value = ''; }}
                      />
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="flex-1 bg-stone-900 border border-dashed border-lime-700 rounded-xl px-4 py-3 flex items-center justify-center gap-2 active:scale-[0.98] transition"
                      >
                        <span className="text-sm text-lime-400">⬆️ UPLOAD 360° VIDEO</span>
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => setLinkingVideo(true)}
                    className="flex-1 bg-stone-900 border border-dashed border-stone-700 rounded-xl px-4 py-3 flex items-center justify-center gap-2 active:scale-[0.98] transition"
                  >
                    <span className="text-sm text-stone-400">🔗 LINK 360° VIDEO</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="bg-stone-900 border border-stone-800 rounded-xl p-3 space-y-2">
                <p className="text-xs text-stone-400">Enter R2 filename (e.g. <code className="text-lime-400">game-may-26.mp4</code>) or full URL:</p>
                <input
                  type="text"
                  value={linkInput}
                  onChange={(e) => setLinkInput(e.target.value)}
                  placeholder="latest.mp4"
                  className="w-full px-3 py-2 rounded-lg bg-stone-800 border border-stone-700 text-sm text-white placeholder:text-stone-500"
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => { setLinkingVideo(false); setLinkInput(''); }}
                    className="flex-1 py-2 rounded-lg bg-stone-800 text-stone-300 text-xs font-bold active:scale-95"
                  >CANCEL</button>
                  <button
                    onClick={() => {
                      const val = linkInput.trim();
                      if (!val) return;
                      const url = val.startsWith('http') ? val : `${R2_PUBLIC}/${val}`;
                      onUpdateGame({ videoUrl: url });
                      setLinkingVideo(false);
                      setLinkInput('');
                    }}
                    disabled={!linkInput.trim()}
                    className="flex-1 py-2 rounded-lg bg-lime-600 text-stone-950 text-xs font-bold active:scale-95 disabled:opacity-40"
                  >SAVE</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Field calibration is done on the Mac during analytics. The PWA
          no longer offers a calibration modal. */}

      {/* Post-game analytics tab */}
      {game.videoUrl && (
        <div className="px-4 pt-3">
          <button
            onClick={() => setShowAnalytics(true)}
            className="w-full bg-stone-900 border border-stone-800 rounded-xl px-4 py-3 flex items-center justify-between active:scale-[0.98] transition"
          >
            <span className="text-sm font-bold">📊 ANALYTICS</span>
            <span className="text-xs text-stone-400">DISTANCE · HEATMAPS · FORMATION</span>
          </button>
          <button
            onClick={async () => {
              try {
                if (navigator.clipboard && window.isSecureContext) {
                  await navigator.clipboard.writeText(game.id);
                } else {
                  const ta = document.createElement('textarea');
                  ta.value = game.id; document.body.appendChild(ta);
                  ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
                }
                alert(`Game ID copied:\n${game.id}\n\nOn your Mac, run:\n./run_analytics.sh ${game.id}`);
              } catch {
                prompt('Game ID (copy this):', game.id);
              }
            }}
            className="w-full mt-2 text-[10px] text-stone-500 text-center active:text-lime-400 py-1"
            title="Copy game-id for ./run_analytics.sh on your Mac"
          >
            game-id: <span className="font-mono text-stone-400">{game.id}</span> · tap to copy
          </button>
        </div>
      )}

      {showAnalytics && (
        <AnalyticsPanel
          game={game}
          roster={roster}
          onClose={() => setShowAnalytics(false)}
          onSeekVideo={(t) => { setSeekTo(t); setShowVideo(true); setShowAnalytics(false); }}
          onDeleteVideos={onDeleteVideos}
          onUpdateGame={onUpdateGame}
        />
      )}

      <div className="px-4 pt-5">
        <button
          onClick={() => setShowEditInfo(v => !v)}
          className="w-full flex items-center justify-between bg-stone-900 border border-stone-800 rounded-xl px-4 py-3 active:scale-[0.99]"
        >
          <span className="font-display text-sm tracking-wider text-stone-200">⚙ EDIT GAME INFO</span>
          <span className="text-xs text-stone-400">{showEditInfo ? 'CLOSE' : 'OPEN'}</span>
        </button>
        {showEditInfo && (
          <GameInfoEditor
            game={game}
            opponentSuggestions={opponentSuggestions}
            onSave={(patch) => { onUpdateGame(patch); setShowEditInfo(false); }}
            onCancel={() => setShowEditInfo(false)}
          />
        )}
      </div>

      <div className="px-4 pt-5">
        <h3 className="font-display text-xl mb-2">TIMELINE</h3>
        {events.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
            No events recorded.
          </div>
        ) : (
          <div className="space-y-1.5">
            {events.map(e => (
              <EventRow
                key={e.id}
                event={e}
                roster={roster}
                onDelete={onDeleteEvent}
                onSeek={showVideo && game.videoUrl ? () => setSeekTo({ time: e.elapsed, _t: Date.now() }) : null}
                onTag={EVENT_TYPES[e.type] && (EVENT_NEEDS_ZONE.has(e.type) || EVENT_NEEDS_PRESSURE.has(e.type) || EVENT_NEEDS_DECISION.has(e.type))
                  ? () => setTaggingEvent(e)
                  : null}
              />
            ))}
          </div>
        )}
      </div>

      {/* Performance Scores */}
      {Object.keys(tally).length > 0 && (
        <div className="px-4 pt-5">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-display text-xl">PERFORMANCE SCORES</h3>
            {/* Raw per-20 rates vs percentile-vs-squad (plan 2.4). Raw is the
                default/public view; "vs Squad" re-expresses each pillar as a
                percentile rank so strengths/weaknesses read at a glance. */}
            <div className="flex rounded-lg overflow-hidden border border-stone-700 text-[11px] font-bold tracking-wider">
              {['raw', 'squad'].map(m => (
                <button
                  key={m}
                  onClick={() => setScoreView(m)}
                  className={`px-2.5 py-1 ${scoreView === m ? 'bg-lime-600 text-stone-950' : 'bg-stone-900 text-stone-400'}`}
                >
                  {m === 'raw' ? 'RAW' : 'VS SQUAD'}
                </button>
              ))}
            </div>
          </div>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800">
            {(() => {
              // Squad-average pillar rates for this game — the shrinkage prior
              // (v2): short-minute cameos get pulled toward the squad mean.
              const squadRates = computeSquadRates(
                Object.entries(tally)
                  .filter(([, s]) => (s.seconds || 0) > 0)
                  .map(([pid, s]) => ({ playerId: pid, minutes: (s.seconds || 0) / 60 })),
                events, mergeWeights(weights),
              );
              const scored = Object.entries(tally)
              .map(([pid, stats]) => {
                const min = Math.round((stats.seconds || 0) / 60);
                // Treat as GK for scoring if they served any GK time in this game.
                const wasGKThisGame = (game.gkPlayerId === pid) || (game.gkChanges || []).some(c => c.gkPlayerId === pid);
                const gkExtras = wasGKThisGame ? gkExtrasForGame(pid, game) : undefined;
                // Blend GK vs outfield scoring by the share of this game's minutes spent in goal.
                const gkFraction = (wasGKThisGame && stats.seconds > 0) ? (gkExtras.secondsAsGK || 0) / stats.seconds : 0;
                const score = computePerformanceScore(pid, events, min, gkFraction, gkExtras, weights, squadRates);
                return { pid, stats, min, score, gkExtras };
              })
              .sort((a, b) => b.score.overall - a.score.overall);
              // Squad distribution of the SHOWN pillar rates → percentiles. GK
              // is excluded from the reference set: their pillar mix is scored
              // on a different weighting, so mixing them skews the outfield
              // spread. (A GK still gets a raw score; just no cross-role rank.)
              const pillarStats = computeSquadPillarStats(
                scored.filter(s => s.min > 0 && (s.score.attacking != null))
                  .map(s => ({ atk: s.score.attacking, def: s.score.defending, dec: s.score.decisions, inv: s.score.involvement })),
              );
              return scored
              .map(({ pid, stats, min, score }) => {
                const player = roster.find(p => p.id === pid);
                const parts = [];
                if (stats.GOAL) parts.push(`${stats.GOAL}G`);
                if (stats.ASSIST) parts.push(`${stats.ASSIST}A`);
                if (stats.KEY_PASS) parts.push(`${stats.KEY_PASS}🔑`);
                if (stats.BALL_WIN) parts.push(`${stats.BALL_WIN}🔥`);
                if (stats.SHOT_ON) parts.push(`${stats.SHOT_ON} on`);
                if (stats.SAVE) parts.push(`${stats.SAVE} saves`);
                if (stats.BLOCK) parts.push(`${stats.BLOCK} blocks`);
                if (stats.CLEAR) parts.push(`${stats.CLEAR}🧹`);
                if (stats.KICK_OUT) parts.push(`${stats.KICK_OUT}🥾`);
                if (stats.DUEL_WIN || stats.DUEL_LOSE) parts.push(`1v1: ${stats.DUEL_WIN || 0}-${stats.DUEL_LOSE || 0}`);
                if (stats.GIVE_GO) parts.push(`${stats.GIVE_GO} g&g`);
                if (stats.GIVE_GO_WALL) parts.push(`${stats.GIVE_GO_WALL} wall`);
                if (stats.GATES) parts.push(`${stats.GATES} gates`);
                if (stats.TURNOVER) parts.push(`${stats.TURNOVER}💨`);
                if (stats.HOLDS_BALL) parts.push(`${stats.HOLDS_BALL}⏳`);
                return (
                  <div key={pid} className="p-3">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-stone-900 text-lime-400 flex items-center justify-center font-display text-xl tabular-nums">
                        {player?.number || '—'}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-bold text-sm">{player?.name || 'Removed player'}</div>
                        <div className="text-xs text-stone-400">{parts.join(' · ') || '—'}{min > 0 ? ` · ${min}min` : ''}</div>
                      </div>
                      <div className={`font-display text-2xl tabular-nums ${score.overall >= 8 ? 'text-lime-600' : score.overall >= 4 ? 'text-stone-100' : score.overall >= 0 ? 'text-stone-400' : 'text-red-600'}`}>
                        {score.overall}
                      </div>
                    </div>
                    <div className="grid grid-cols-4 gap-1.5 mt-2">
                      {scoreView === 'squad' ? (
                        <>
                          <PillarPct label="ATK" pct={pillarPercentile(score.attacking, pillarStats.atk)} />
                          <PillarPct label="DEF" pct={pillarPercentile(score.defending, pillarStats.def)} />
                          <PillarPct label="DEC" pct={pillarPercentile(score.decisions, pillarStats.dec)} />
                          <PillarPct label="INV" pct={pillarPercentile(score.involvement, pillarStats.inv)} />
                        </>
                      ) : (
                        <>
                          <PillarMini label="ATK" value={score.attacking} />
                          <PillarMini label="DEF" value={score.defending} />
                          <PillarMini label="DEC" value={score.decisions} />
                          <PillarMini label="INV" value={score.involvement} />
                        </>
                      )}
                    </div>
                  </div>
                );
              });
            })()}
          </div>
        </div>
      )}

      {taggingEvent && (
        <TagSheet
          event={taggingEvent}
          roster={roster}
          onSave={(patch) => { onUpdateEvent(taggingEvent.id, patch); setTaggingEvent(null); }}
          onClose={() => setTaggingEvent(null)}
        />
      )}
    </div>
  );
}

/* ---------- EDIT GAME INFO (post-hoc metadata fixes) ---------- */
function GameInfoEditor({ game, opponentSuggestions = [], onSave, onCancel }) {
  const [opponent, setOpponent] = useState(game.opponent || '');
  const [tournament, setTournament] = useState(game.tournament || '');
  // Resolved, not raw: a played game from before the tag existed carries only
  // the old free-text value, and gameTypeOf turns "Festival" into `festival`
  // so the picker opens on the tag the game is actually being scored as.
  const [gameType, setGameType] = useState(gameTypeOf(game));
  const [halfLengthMin, setHalfLengthMin] = useState(Number(game.halfLengthMin) || 25);
  const [isHome, setIsHome] = useState(typeof game.isHome === 'boolean' ? game.isHome : true);
  const [homeColor, setHomeColor] = useState(game.homeColor || '#0a0a0a');
  const [awayColor, setAwayColor] = useState(game.awayColor || '#dc2626');
  return (
    <div className="mt-2 bg-stone-900 border border-stone-800 rounded-2xl p-4 space-y-3">
      <div className="text-[11px] text-amber-300/80 bg-amber-900/20 border border-amber-800/50 rounded-lg px-3 py-2">
        Changing half length only updates how minutes are <em>displayed</em>. Stored event timestamps and minutes-played are kept exactly as recorded.
      </div>

      <Field label="OPPONENT">
        <input
          type="text"
          value={opponent}
          onChange={(e) => setOpponent(e.target.value)}
          list="opponent-suggestions"
          className="w-full bg-stone-950 border border-stone-800 rounded-lg px-3 py-2"
        />
        {opponentSuggestions.length > 0 && (
          <datalist id="opponent-suggestions">
            {opponentSuggestions.map((n) => <option key={n} value={n} />)}
          </datalist>
        )}
      </Field>

      <Field label="TAG">
        <div className="grid grid-cols-2 gap-2">
          {GAME_TYPES.map((gt) => (
            <button
              key={gt.key}
              type="button"
              onClick={() => setGameType(gt.key)}
              className={`py-2 rounded-lg font-bold text-xs border active:scale-95 transition ${
                gameType === gt.key
                  ? 'bg-lime-500 text-stone-950 border-lime-400'
                  : 'bg-stone-950 text-stone-300 border-stone-800'
              }`}
            >{gt.label}</button>
          ))}
        </div>
      </Field>

      <Field label="COMPETITION NAME">
        <input
          type="text"
          value={tournament}
          onChange={(e) => setTournament(e.target.value)}
          placeholder="e.g. Canton Cup (optional)"
          className="w-full bg-stone-950 border border-stone-800 rounded-lg px-3 py-2"
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="HALF LENGTH (MIN)">
          <div className="flex items-center bg-stone-950 border border-stone-800 rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setHalfLengthMin(m => Math.max(1, m - 1))}
              className="px-3 py-2 text-stone-300 active:scale-95"
            >−</button>
            <div className="flex-1 text-center font-display text-2xl tabular-nums">{halfLengthMin}</div>
            <button
              type="button"
              onClick={() => setHalfLengthMin(m => Math.min(99, m + 1))}
              className="px-3 py-2 text-stone-300 active:scale-95"
            >+</button>
          </div>
        </Field>
        <Field label="VENUE">
          <div className="grid grid-cols-2 gap-1 bg-stone-950 border border-stone-800 rounded-lg p-1">
            <button
              type="button"
              onClick={() => setIsHome(true)}
              className={`py-2 rounded-md font-bold text-sm ${isHome ? 'bg-lime-500 text-stone-950' : 'text-stone-300'}`}
            >HOME</button>
            <button
              type="button"
              onClick={() => setIsHome(false)}
              className={`py-2 rounded-md font-bold text-sm ${!isHome ? 'bg-lime-500 text-stone-950' : 'text-stone-300'}`}
            >AWAY</button>
          </div>
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="OUR JERSEY">
          <div className="flex items-center gap-2 bg-stone-950 border border-stone-800 rounded-lg px-2 py-1.5">
            <input type="color" value={homeColor} onChange={(e) => setHomeColor(e.target.value)} className="w-9 h-9 bg-transparent border-0 cursor-pointer" />
            <input type="text" value={homeColor} onChange={(e) => setHomeColor(e.target.value)} className="flex-1 bg-transparent text-xs font-mono" />
          </div>
        </Field>
        <Field label="OPP JERSEY">
          <div className="flex items-center gap-2 bg-stone-950 border border-stone-800 rounded-lg px-2 py-1.5">
            <input type="color" value={awayColor} onChange={(e) => setAwayColor(e.target.value)} className="w-9 h-9 bg-transparent border-0 cursor-pointer" />
            <input type="text" value={awayColor} onChange={(e) => setAwayColor(e.target.value)} className="flex-1 bg-transparent text-xs font-mono" />
          </div>
        </Field>
      </div>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onCancel}
          className="flex-1 py-2.5 rounded-lg bg-stone-800 text-stone-200 font-bold active:scale-95"
        >CANCEL</button>
        <button
          onClick={() => onSave({
            opponent: opponent.trim() || 'Opponent',
            tournament: tournament.trim(),
            gameType,
            halfLengthMin: Number(halfLengthMin) || 25,
            isHome,
            homeColor,
            awayColor,
          })}
          className="flex-1 py-2.5 rounded-lg bg-lime-500 text-stone-950 font-bold active:scale-95"
        >SAVE</button>
      </div>
    </div>
  );
}

/* ---------- TAG SHEET (post-game modifier tagging) ---------- */
function TagSheet({ event, roster, onSave, onClose }) {
  const ev = EVENT_TYPES[event.type] || { emoji: '•', label: event.type };
  const player = roster.find(p => p.id === event.playerId);
  const allowZone = EVENT_NEEDS_ZONE.has(event.type);
  const allowPressure = EVENT_NEEDS_PRESSURE.has(event.type);
  const allowDecision = EVENT_NEEDS_DECISION.has(event.type);

  const [zone, setZone] = useState(event.zone || null);
  const [pressure, setPressure] = useState(event.pressure || null);
  const [decision, setDecision] = useState(event.decision || null);

  const dirty = (zone || null) !== (event.zone || null)
    || (pressure || null) !== (event.pressure || null)
    || (decision || null) !== (event.decision || null);

  const handleSave = () => {
    onSave({
      ...(allowZone ? { zone: zone || null } : {}),
      ...(allowPressure ? { pressure: pressure || null } : {}),
      ...(allowDecision ? { decision: decision || null } : {}),
    });
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-stone-950 border-t-2 sm:border-2 border-stone-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md max-h-[92vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between p-4 border-b border-stone-800 sticky top-0 bg-stone-950">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-2xl">{ev.emoji}</span>
            <div className="min-w-0">
              <div className="font-display text-lg leading-none truncate">{ev.label}</div>
              <div className="text-[11px] text-stone-400 tracking-wider truncate">
                {player ? `${player.name} #${player.number}` : 'No player'} · {formatClock(event.elapsed)} P{event.period}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="w-10 h-10 rounded-full bg-stone-800 flex items-center justify-center active:scale-95 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-5">
          {allowZone && (
            <div>
              <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2 flex items-center justify-between">
                <span>📍 ZONE</span>
                {zone && <button onClick={() => setZone(null)} className="text-stone-500 text-[10px] tracking-wider">clear</button>}
              </div>
              <div className="grid grid-cols-3 grid-rows-3 gap-1.5 aspect-[3/2]">
                {['A', 'M', 'D'].flatMap(band =>
                  ['L', 'C', 'R'].map(side => {
                    const id = `${band}-${side}`;
                    const isSel = zone === id;
                    const baseTone = band === 'A'
                      ? 'bg-lime-900/40 border-lime-800 text-lime-200'
                      : band === 'M'
                      ? 'bg-stone-800 border-stone-700 text-stone-200'
                      : 'bg-red-950/40 border-red-900 text-red-200';
                    const selTone = isSel ? ' ring-2 ring-amber-400 ring-offset-2 ring-offset-stone-950' : '';
                    return (
                      <button
                        key={id}
                        onClick={() => setZone(id)}
                        className={`rounded-lg border-2 ${baseTone}${selTone} active:scale-[0.97] transition flex flex-col items-center justify-center py-2`}
                      >
                        <div className="text-[10px] tracking-widest font-bold">{ZONE_LABEL[id]}</div>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          )}

          {allowPressure && (
            <div>
              <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2 flex items-center justify-between">
                <span>⚡ PRESSURE</span>
                {pressure && <button onClick={() => setPressure(null)} className="text-stone-500 text-[10px] tracking-wider">clear</button>}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => setPressure('open')}
                  className={`rounded-xl border-2 py-3 font-display text-base active:scale-[0.97] transition ${pressure === 'open' ? 'bg-lime-900/60 border-lime-500 text-lime-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
                >
                  🆓 OPEN
                </button>
                <button
                  onClick={() => setPressure('pressure')}
                  className={`rounded-xl border-2 py-3 font-display text-base active:scale-[0.97] transition ${pressure === 'pressure' ? 'bg-orange-900/60 border-orange-500 text-orange-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
                >
                  ⚡ PRESSURE
                </button>
              </div>
            </div>
          )}

          {allowDecision && (
            <div>
              <div className="text-[11px] tracking-widest font-bold text-stone-400 mb-2 flex items-center justify-between">
                <span>🎯 DECISION</span>
                {decision && <button onClick={() => setDecision(null)} className="text-stone-500 text-[10px] tracking-wider">clear</button>}
              </div>
              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={() => setDecision('good')}
                  className={`rounded-xl border-2 py-3 font-display text-sm active:scale-[0.97] transition ${decision === 'good' ? 'bg-lime-900/60 border-lime-500 text-lime-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
                >
                  🎯 GOOD
                </button>
                <button
                  onClick={() => setDecision('forced')}
                  className={`rounded-xl border-2 py-3 font-display text-sm active:scale-[0.97] transition ${decision === 'forced' ? 'bg-amber-900/60 border-amber-500 text-amber-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
                >
                  🤔 FORCED
                </button>
                <button
                  onClick={() => setDecision('bad')}
                  className={`rounded-xl border-2 py-3 font-display text-sm active:scale-[0.97] transition ${decision === 'bad' ? 'bg-red-900/60 border-red-500 text-red-100' : 'bg-stone-900 border-stone-800 text-stone-300'}`}
                >
                  ❌ POOR
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-stone-800 sticky bottom-0 bg-stone-950 flex gap-2">
          <button
            onClick={onClose}
            className="flex-1 bg-stone-900 text-stone-300 border border-stone-700 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
          >
            CANCEL
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty}
            className={`flex-1 font-display text-base py-3 rounded-xl active:scale-[0.98] transition border-2 ${dirty ? 'bg-lime-500 text-stone-950 border-lime-400' : 'bg-stone-900 text-stone-600 border-stone-800'}`}
          >
            SAVE
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- STATS ---------- */
function StatsView({ roster, games, weights, onBack }) {
  const [detailPlayerId, setDetailPlayerId] = useState(null);
  // The single stats destination: SEASON (per-player), GAMES (one game), TEAM.
  // Before this, season stats lived here AND in a modal inside FILM ROOM, with
  // two different per-player tables that both showed GP and MIN. See
  // STATS_CONSOLIDATION_PLAN.md.
  const [tab, setTab] = useState('season');
  const [mode, setMode] = useState('season'); // window: 'season' | 'rolling'
  const [sortKey, setSortKey] = useState('score');
  const [openGameId, setOpenGameId] = useState(null);
  const finished = useMemo(() => games
    .filter(g => g.status === 'finished')
    .sort((a, b) => (b.date || '').localeCompare(a.date || '') || (b.endedAt || 0) - (a.endedAt || 0)),
  [games]);

  const stats = useMemo(() => {
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, CLEAR: 0, KICK_OUT: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GIVE_GO_WALL: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0, gamesPlayed: 0, totalSeconds: 0, gkSeconds: 0, cleanSheets: 0, oppGoalsConceded: 0, gamesAsGK: 0 });
    const map = {};
    for (const p of roster) map[p.id] = init();
    for (const g of finished) {
      const seen = new Set();
      for (const e of g.events) {
        if (e.type === 'SUB') continue;
        if (e.playerId && map[e.playerId]) {
          if (map[e.playerId][e.type] !== undefined) map[e.playerId][e.type]++;
          seen.add(e.playerId);
        }
        // Give & go wall partner credit
        if (e.type === 'GIVE_GO' && e.partnerId && map[e.partnerId]) {
          map[e.partnerId].GIVE_GO_WALL++;
          seen.add(e.partnerId);
        }
      }
      for (const p of roster) {
        const sec = playerSeconds(p.id, g);
        if (sec > 0) {
          if (map[p.id]) map[p.id].totalSeconds += sec;
          seen.add(p.id);
        }
        // Aggregate GK time + clean sheets + goals conceded for any player who served as GK in this game
        const servedAsGK = (g.gkPlayerId === p.id) || (g.gkChanges || []).some(c => c.gkPlayerId === p.id);
        if (servedAsGK && map[p.id]) {
          const gx = gkExtrasForGame(p.id, g);
          map[p.id].gkSeconds += gx.secondsAsGK || 0;
          map[p.id].cleanSheets += gx.cleanSheets || 0;
          map[p.id].oppGoalsConceded += gx.oppGoalsConceded || 0;
          if ((gx.secondsAsGK || 0) > 0) map[p.id].gamesAsGK += 1;
        }
      }
      for (const pid of seen) if (map[pid]) map[pid].gamesPlayed++;
    }
    return map;
  }, [roster, finished]);

  // Season performance score per player (v2): per-game pillar POINTS weighted
  // by game type (scrimmage 0.5× etc. — tunable in ⚙ Scoring), summed and
  // divided by weighted minutes, then shrunk toward the squad-average rate.
  // Building from per-game points (instead of pooling all events) also means
  // per-game gk blending and own goals are handled exactly like the game view
  // — the old pooled filter silently dropped OPP_GOAL.ownGoalById events.
  const seasonScores = useMemo(() => {
    const W = mergeWeights(weights);
    const M = Math.max(0, Number(W.shrinkMinutes) || 0);
    const typeWeight = (g) => {
      const t = gameTypeOf(g);
      return (W.gameTypes[t] != null) ? Number(W.gameTypes[t]) : Number(W.gameTypes.default);
    };
    // ---- Per-pillar LOGGING WEIGHT -------------------------------------------
    // The coach taps while coaching, so how much gets logged varies enormously
    // between games and between pillar types. Measured over 12 games:
    //
    //   total action events/game   95 -> 18   (5x swing)
    //   DEF share of those         60% -> 3%  (he now taps mostly goals/shots)
    //   DEC share                  21% +- 5%  (stable)
    //
    // The old rate divided each pillar's points by ALL minutes played, so a
    // player's DEF rate was (points from the few heavily-logged games) / (minutes
    // from every game) — diluted toward zero by games where the coach was busy.
    // That penalised players for the coach's attention, not their play, and it
    // put whoever happened to play in the two June games on top of the table.
    //
    // Fix: weight each game's MINUTES, per pillar, by how much of that pillar was
    // logged there relative to the season's best-logged game. A game with a
    // tenth of the usual DEF logging contributes a tenth of its minutes to the
    // DEF denominator, so the rate stays comparable instead of being watered
    // down. Points are unchanged — nothing is invented, the denominator is just
    // made to match the numerator's actual coverage.
    // ⚠ Only DISCRETIONARY events count toward coverage. Some taps track things
    // that happen TO the team and get logged whatever the coach is doing —
    // measured over 12 games:
    //
    //   SAVE      logged in 11/12 games, stdev 2.3  (tracks opponent shots)
    //   DUEL_WIN  ZERO in 8/12 games,    stdev 6.7  (pure coach attention)
    //
    // Weighting a keeper's denominator down by DEF coverage inflated his rate
    // 3.6x (44 saves over 88 weighted minutes instead of 313) and put him at
    // double the next player. Outcome events — goals, assists, shots, saves,
    // penalties, own goals — are excluded from the coverage calculation for that
    // reason: their count does not scale with how much the coach was tapping.
    const PILLAR_TYPES = {
      atk: ['KEY_PASS', 'FOUL_ON'],
      def: ['BLOCK', 'BALL_WIN', 'CLEAR', 'KICK_OUT', 'DUEL_WIN', 'DUEL_LOSE', 'FOUL_BY'],
      dec: ['GIVE_GO', 'GATES', 'HOLDS_BALL', 'TURNOVER'],
    };
    // Events per logged minute, per pillar, per game — an intensity, so a short
    // appearance in a well-logged game isn't mistaken for thin logging.
    const gameLog = new Map();
    for (const g of finished) {
      const counts = { atk: 0, def: 0, dec: 0 };
      for (const e of (g.events || [])) {
        for (const k of ['atk', 'def', 'dec']) {
          if (PILLAR_TYPES[k].includes(e.type)) counts[k] += 1;
        }
      }
      // Team minutes actually played in this game, as the exposure base.
      let teamMin = 0;
      for (const p of roster) teamMin += playerSeconds(p.id, g) / 60;
      const base = Math.max(teamMin, 1);
      gameLog.set(g.id, {
        atk: counts.atk / base, def: counts.def / base, dec: counts.dec / base,
        inv: (counts.atk + counts.def + counts.dec) / base,
      });
    }
    // Normalise against the best-logged game so the weight is a 0..1 coverage.
    const peak = { atk: 0, def: 0, dec: 0, inv: 0 };
    for (const v of gameLog.values()) {
      for (const k of ['atk', 'def', 'dec', 'inv']) peak[k] = Math.max(peak[k], v[k]);
    }
    // Floor so a game with genuinely sparse logging still counts a little — a
    // player who only ever appeared in thin games must not get a 0 denominator
    // and an explosive rate. 0.15 keeps the worst game at 15% weight.
    const LOG_FLOOR = 0.15;
    // Share of each pillar's POINTS that comes from discretionary events. The
    // rest (goals, assists, shots, saves, clean sheets) is logged regardless of
    // the coach's attention, so only this share of the denominator may be
    // discounted — otherwise a keeper's consistently-logged saves get divided by
    // a shrunken DEF denominator and his rate explodes.
    //
    // Rough, deliberately: these are fixed shares rather than a per-player
    // decomposition, because the point is to stop over-correcting, not to model
    // the mix exactly. ATK is nearly all outcome events (goals/assists/shots),
    // DEF is mixed (saves are outcome, duels/blocks are discretionary), DEC is
    // almost entirely discretionary.
    const DISCRETIONARY_SHARE = { atk: 0.15, def: 0.5, dec: 0.85, inv: 0.5 };
    const logWeight = (gid, k) => {
      const v = gameLog.get(gid);
      if (!v || !(peak[k] > 0)) return 1;
      const cov = Math.max(LOG_FLOOR, Math.min(1, v[k] / peak[k]));
      const s = DISCRETIONARY_SHARE[k];
      // Blend toward 1: only the discretionary share of the exposure shrinks.
      return (1 - s) + s * cov;
    };

    // Weighted per-player sums + the squad prior in one pass.
    const sums = {};  // pid -> { atk, def, dec, inv, wmin: {per pillar}, wgkmin }
    const squadTot = { atk: 0, def: 0, dec: 0, inv: 0 };
    const squadMin = { atk: 0, def: 0, dec: 0, inv: 0 };
    for (const g of finished) {
      const w = typeWeight(g);
      if (!(w > 0)) continue;
      const ev = g.events || [];
      const lw = {
        atk: logWeight(g.id, 'atk'), def: logWeight(g.id, 'def'),
        dec: logWeight(g.id, 'dec'), inv: logWeight(g.id, 'inv'),
      };
      for (const p of roster) {
        const sec = playerSeconds(p.id, g);
        if (sec <= 0) continue;
        const min = sec / 60;
        const servedAsGK = (g.gkPlayerId === p.id) || (g.gkChanges || []).some(c => c.gkPlayerId === p.id);
        const gx = servedAsGK ? gkExtrasForGame(p.id, g) : null;
        const f = (servedAsGK && sec > 0) ? Math.min(1, (gx.secondsAsGK || 0) / sec) : 0;
        const pts = pillarPoints(p.id, ev, f, gx, W);
        const row = sums[p.id] || (sums[p.id] = {
          atk: 0, def: 0, dec: 0, inv: 0,
          wmin: { atk: 0, def: 0, dec: 0, inv: 0 }, rawMin: 0, wgkmin: 0,
        });
        row.atk += w * pts.atk; row.def += w * pts.def;
        row.dec += w * pts.dec; row.inv += w * pts.inv;
        for (const k of ['atk', 'def', 'dec', 'inv']) row.wmin[k] += w * min * lw[k];
        row.rawMin += w * min;
        row.wgkmin += w * min * f;
        // Squad prior: outfield values for everyone (it's a prior, not a score).
        const pop = pillarPoints(p.id, ev, 0, null, W);
        squadTot.atk += w * pop.atk; squadTot.def += w * pop.def;
        squadTot.dec += w * pop.dec; squadTot.inv += w * pop.inv;
        for (const k of ['atk', 'def', 'dec', 'inv']) squadMin[k] += w * min * lw[k];
      }
    }
    const squadRates = {};
    for (const k of ['atk', 'def', 'dec', 'inv']) {
      squadRates[k] = squadTot[k] / (Math.max(squadMin[k], 1) / 20);
    }
    const map = {};
    const r = (n) => Math.round(n * 10) / 10;
    for (const p of roster) {
      const row = sums[p.id];
      if (!row || row.rawMin <= 0) continue;
      // Each pillar divides by ITS OWN logging-weighted minutes.
      const rate = (pts, sq, wmin) => (pts + (M / 20) * sq) / ((wmin + M) / 20);
      const attacking = rate(row.atk, squadRates.atk, row.wmin.atk);
      const defending = rate(row.def, squadRates.def, row.wmin.def);
      const decisions = rate(row.dec, squadRates.dec, row.wmin.dec);
      const involvement = rate(row.inv, squadRates.inv, row.wmin.inv);
      // Pillar mix blended by the weighted share of season minutes in goal.
      const f = Math.min(1, row.wgkmin / row.rawMin);
      const PO = W.pillars.outfield, PG = W.pillars.gk;
      const pil = {
        atk: PO.atk + f * (PG.atk - PO.atk),
        def: PO.def + f * (PG.def - PO.def),
        dec: PO.dec + f * (PG.dec - PO.dec),
        inv: PO.inv + f * (PG.inv - PO.inv),
      };
      const overall = (pil.atk * attacking + pil.def * defending + pil.dec * decisions + pil.inv * involvement) / 100;
      map[p.id] = {
        overall: r(overall), attacking: r(attacking), defending: r(defending),
        decisions: r(decisions), involvement: r(involvement),
        // How much of this player's minutes carried logging, per pillar. Surfaced
        // so a score resting on thin taps reads as provisional rather than
        // authoritative — the coach asked how much of the view is his taps, and
        // the answer belongs on screen, not in a doc.
        coverage: {
          atk: r(100 * row.wmin.atk / Math.max(row.rawMin, 1)),
          def: r(100 * row.wmin.def / Math.max(row.rawMin, 1)),
          dec: r(100 * row.wmin.dec / Math.max(row.rawMin, 1)),
        },
      };
    }
    return map;
  }, [roster, finished, stats, weights]);

  // The merged per-player rows: event stats (left of the divider) and tagged
  // positions (right of it), joined on player id but never blended.
  const season = useSeasonAnalytics(finished, mode);
  const taggedGameIds = useMemo(
    () => Object.fromEntries(Object.keys(season.clickByGame).map(id => [id, true])),
    [season.clickByGame]);

  const rows = useMemo(() => {
    const out = roster.map(p => {
      const s = stats[p.id] || {};
      const sc = seasonScores[p.id] || {};
      const agg = season.aggByPid[p.id] || null;
      return {
        pid: p.id,
        player: p,
        gamesPlayed: s.gamesPlayed || 0,
        minutes: Math.round((s.totalSeconds || 0) / 60),
        goals: s.GOAL || 0,
        assists: s.ASSIST || 0,
        score: sc.overall || 0,
        // Worst pillar coverage: the score is only as solid as its thinnest input,
        // and DEF is usually the thin one.
        scoreCoverage: sc.coverage
          ? Math.min(sc.coverage.atk, sc.coverage.def, sc.coverage.dec) : null,
        // Tag side. `gamesWithDocs` is the denominator for "3/7": games in the
        // window that HAVE analytics at all, so the ratio reads as "tagged out of
        // analysed" rather than out of played.
        tagged: agg ? agg.tagged : 0,
        gamesWithDocs: agg ? agg.games : 0,
        avgDefPct: agg ? agg.avgDefPct : null,
        avgMidPct: agg ? agg.avgMidPct : null,
        avgAttPct: agg ? agg.avgAttPct : null,
        minSeries: agg ? agg.minSeries : [],
      };
    });
    const cmp = {
      score: (a, b) => b.score - a.score,
      avgMin: (a, b) => (b.gamesPlayed ? b.minutes / b.gamesPlayed : 0)
        - (a.gamesPlayed ? a.minutes / a.gamesPlayed : 0),
      goals: (a, b) => b.goals - a.goals,
      name: (a, b) => (a.player?.name || '').localeCompare(b.player?.name || ''),
    }[sortKey] || ((a, b) => b.score - a.score);
    return out.sort(cmp);
  }, [roster, stats, seasonScores, season.aggByPid, sortKey]);

  const detailPlayer = roster.find(p => p.id === detailPlayerId);
  const openGame = finished.find(g => g.id === openGameId) || null;
  const nTagged = Object.keys(season.clickByGame).length;

  const TABS = [
    ['season', 'SEASON', 'per player'],
    ['games', 'GAMES', `${finished.length}`],
    ['team', 'TEAM', 'shape'],
  ];

  return (
    <div className="pb-24">
      <Header title="STATS" onBack={onBack} />

      {/* Tabs. Three questions a coach actually asks — how is this kid doing,
          what happened in that game, how is the team playing — rather than one
          scroll that mixes them. */}
      <div className="px-4 pt-4">
        <div className="bg-stone-900 border border-stone-800 rounded-2xl p-1.5 flex gap-1">
          {TABS.map(([k, label, sub]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`flex-1 py-2 rounded-xl font-display text-sm transition ${tab === k ? 'bg-lime-500 text-stone-950' : 'text-stone-300 hover:bg-stone-800'}`}
            >
              {label}
              <span className={`ml-1 text-[9px] font-normal ${tab === k ? 'text-stone-800' : 'text-stone-500'}`}>{sub}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Window toggle applies to SEASON and TEAM; GAMES is a list, not an
          aggregate, so it always shows everything. */}
      {tab !== 'games' && season.gamesWithAnalytics.length > 0 && (
        <div className="px-4 pt-2">
          <div className="flex gap-1 text-[10px]">
            <button
              onClick={() => setMode('season')}
              className={`px-2 py-1 rounded font-bold ${mode === 'season' ? 'bg-stone-700 text-stone-100' : 'bg-stone-900 text-stone-500'}`}
            >SEASON · {season.gamesWithAnalytics.length}</button>
            <button
              onClick={() => setMode('rolling')}
              className={`px-2 py-1 rounded font-bold ${mode === 'rolling' ? 'bg-stone-700 text-stone-100' : 'bg-stone-900 text-stone-500'}`}
            >LAST {ROLLING_WINDOW}</button>
            {season.fellBackToSeason && (
              <span className="px-1 py-1 text-amber-500">
                need {ROLLING_WINDOW - season.gamesWithAnalytics.length} more — showing season
              </span>
            )}
          </div>
        </div>
      )}

      <div className="px-4 pt-3">
        {tab === 'season' && (
          roster.length === 0 ? (
            <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
              Add players to track stats.
            </div>
          ) : (
            <>
              <div className="text-xs text-stone-400 mb-2">
                Based on {finished.length} completed game{finished.length === 1 ? '' : 's'}.
              </div>
              <details className="bg-stone-900 border border-stone-800 rounded-xl mb-3 text-stone-300">
                <summary className="cursor-pointer select-none px-3 py-2 text-xs font-bold text-stone-200">ⓘ How the SCORE works</summary>
                <div className="px-3 pb-3 text-xs text-stone-400 space-y-1.5">
                  <p>It's a <b className="text-stone-200">per-20-minute development rating</b>, not a goal tally — a blend of four pillars:</p>
                  <p><b className="text-lime-400">ATK</b> goals/assists/shots · <b className="text-sky-400">DEF</b> saves/blocks/wins · <b className="text-amber-400">DEC</b> smart passes vs turnovers · <b className="text-stone-200">INV</b> total involvement.</p>
                  <p>Because it's a <i>rate</i>, more minutes spread a player's actions thinner, and turnovers count against the Decisions pillar. So a high-volume scorer who also gives the ball away can rank below a tidy player in fewer minutes — by design. Tune the weights in <b className="text-stone-200">⚙ Scoring</b>.</p>
                  <p><b className="text-stone-200">v{SCORING_VERSION} (Jun 2026) recalibration:</b> short-minute scores are <i>shrunk</i> toward the squad average (no more one-lucky-goal cameo topping the table); mistakes (turnovers, lost 1v1s, fouls, own goals) no longer earn Involvement credit; GK clean-sheet credit is pro-rated by time in goal; and scrimmages count less toward the season score (tune in ⚙ Scoring → FAIRNESS).</p>
                  <p><b className="text-amber-400">Logging coverage (Aug 2026):</b> you tap while you coach, so how much gets logged swings a lot between games — across the season the action events per game ran 95 down to 18, and defensive taps fell from 60% of them to about 3%. Each pillar now divides by the minutes that <i>actually carried logging for that pillar</i>, instead of by every minute played. So a quiet game no longer waters down a defender's rating, and nobody is penalised for the games you were too busy to tap. A <span className="text-amber-400">●</span> next to a score means under half his minutes carried logging (<span className="text-red-400">●</span> under 30%) — treat those as provisional.</p>
                </div>
              </details>
              <SeasonPlayersTab
                rows={rows}
                sortKey={sortKey}
                setSortKey={setSortKey}
                onOpenPlayer={setDetailPlayerId}
                nTagged={nTagged}
              />
            </>
          )
        )}

        {tab === 'games' && (
          <SeasonGamesTab
            games={finished}
            tagged={taggedGameIds}
            onOpenGame={setOpenGameId}
          />
        )}

        {tab === 'team' && (
          season.loading ? (
            <div className="p-10 text-center text-stone-400 animate-pulse">Loading team analytics…</div>
          ) : season.gamesWithAnalytics.length === 0 ? (
            <div className="bg-stone-900 border border-stone-800 rounded-2xl p-4 text-sm text-stone-300">
              No analytics yet for any finished game. Run <code className="text-lime-400">./run_analytics.sh &lt;gameId&gt;</code> on your Mac first.
            </div>
          ) : (
            <SeasonTeamTab season={season} mode={mode} />
          )
        )}
      </div>

      {detailPlayer && (
        <PlayerStatsDetail
          player={detailPlayer}
          stats={stats[detailPlayer.id] || {}}
          score={seasonScores[detailPlayer.id] || {}}
          row={rows.find(r => r.pid === detailPlayer.id) || null}
          // Only the games where THIS player was tagged, so the detail sheet
          // fetches a couple of docs rather than the whole season.
          taggedGames={finished.filter(g => season.clickByGame[g.id]
            && season.clickByGame[g.id][detailPlayer.id])}
          onClose={() => setDetailPlayerId(null)}
        />
      )}

      {openGame && (
        <AnalyticsPanel
          game={openGame}
          roster={roster}
          onClose={() => setOpenGameId(null)}
          onSeekVideo={() => setOpenGameId(null)}
        />
      )}
    </div>
  );
}

/* Pools a player's TAGGED heatmaps across games, fetched on demand.
 *
 * Heatmaps are deliberately excluded from analytics/summary (96 floats per player
 * per game, and no season screen drew one), so this reaches for the full
 * analytics/v1 docs — but ONLY for the games where this player was tagged, and
 * only when a detail sheet is actually opened. That keeps the fan-out that caused
 * the season-view black screen from coming back through a side door.
 *
 * ⚠ Skips `oriented: false` games. Teams switch ends at half time; when the
 * pipeline cannot resolve which way we attacked it refuses to guess rather than
 * risk mirroring a half, and pooling such a grid would smear the result.
 */
function usePlayerSeasonHeatmap(playerId, games, open) {
  const [state, setState] = useState({ loading: false, grid: null, rows: 12, cols: 8, nGames: 0, nClicks: 0 });
  const gameIds = useMemo(() => (games || []).map(g => g.id).join(','), [games]);

  useEffect(() => {
    if (!open || !playerId || !window.fbDb || !gameIds) return undefined;
    let cancelled = false;
    setState(s => ({ ...s, loading: true }));
    const ids = gameIds.split(',');
    Promise.all(ids.map(id => window.fbDb.collection('teams').doc('main')
      .collection('games').doc(id).collection('analytics').doc('v1').get()
      .then(snap => (snap.exists ? snap.data() : null))
      .catch(() => null)))
      .then(docs => {
        if (cancelled) return;
        let acc = null, rows = 12, cols = 8, nGames = 0, nClicks = 0;
        docs.forEach(d => {
          const cs = d && d.click_stats;
          if (!cs || cs.oriented === false) return;
          const me = (cs.players || []).find(p => p.player_id === playerId);
          if (!me || !Array.isArray(me.heatmap) || !me.heatmap.length) return;
          const shape = cs.heatmap_shape || [12, 8];
          // A grid of a different shape cannot be summed cell-wise. Rather than
          // resample (and invent detail), keep the first shape seen and skip the
          // rest — shape only changes if the pipeline's grid is retuned.
          if (acc && (shape[0] !== rows || shape[1] !== cols)) return;
          rows = shape[0]; cols = shape[1];
          // Each game's grid is already normalised to sum 1, so weight by the
          // player's click count: a 90-click game should count for more than a
          // 12-click one, and an unweighted mean would treat them equally.
          const w = me.n_clicks || 1;
          if (!acc) acc = new Array(me.heatmap.length).fill(0);
          if (acc.length !== me.heatmap.length) return;
          for (let i = 0; i < acc.length; i++) acc[i] += (me.heatmap[i] || 0) * w;
          nGames += 1; nClicks += w;
        });
        if (acc) {
          const total = acc.reduce((a, b) => a + b, 0) || 1;
          acc = acc.map(v => v / total);
        }
        setState({ loading: false, grid: acc, rows, cols, nGames, nClicks });
      });
    return () => { cancelled = true; };
  }, [playerId, gameIds, open]);

  return state;
}

/* ---------- PLAYER STATS DETAIL ---------- */
function PlayerStatsDetail({ player, stats, score, row, taggedGames, onClose }) {
  // Fetch ONLY the games where this player was tagged — never the whole season.
  // The full docs are ~1 MB each; fanning out over all of them is exactly what
  // made the season view open to a black screen.
  const heat = usePlayerSeasonHeatmap(player.id, taggedGames, !!(row && row.tagged));
  const min = Math.round((stats.totalSeconds || 0) / 60);
  const gkMin = Math.round((stats.gkSeconds || 0) / 60);
  const isGK = gkMin > 0 || player.position === 'GK';
  const rows = [
    { label: 'Games played', value: stats.gamesPlayed || 0 },
    { label: 'Minutes played', value: min },
    ...(isGK ? [
      { label: 'Minutes as GK', value: gkMin, accent: 'text-sky-400' },
      { label: 'Games as GK', value: stats.gamesAsGK || 0, accent: 'text-sky-400' },
      // v2: clean sheets are pro-rated by GK stint share, so they can be fractional.
      { label: 'Clean sheets', value: Math.round((stats.cleanSheets || 0) * 10) / 10, accent: 'text-lime-500' },
      { label: 'Goals conceded (as GK)', value: stats.oppGoalsConceded || 0, accent: 'text-red-400' },
    ] : []),
    { label: 'Goals', value: stats.GOAL || 0, accent: 'text-lime-700' },
    { label: 'Assists', value: stats.ASSIST || 0, accent: 'text-lime-700' },
    { label: 'Key passes', value: stats.KEY_PASS || 0, accent: 'text-lime-700' },
    { label: 'Shots on target', value: stats.SHOT_ON || 0 },
    { label: 'Shots off target', value: stats.SHOT_OFF || 0 },
    { label: 'Ball wins', value: stats.BALL_WIN || 0, accent: 'text-lime-700' },
    { label: 'Saves', value: stats.SAVE || 0 },
    { label: 'Blocks', value: stats.BLOCK || 0 },
    { label: 'Clearances', value: stats.CLEAR || 0, accent: 'text-lime-700' },
    { label: 'Kick-outs (under pressure)', value: stats.KICK_OUT || 0 },
    { label: '1v1 duels won', value: stats.DUEL_WIN || 0, accent: 'text-lime-700' },
    { label: '1v1 duels lost', value: stats.DUEL_LOSE || 0, accent: 'text-red-700' },
    { label: 'Give-and-go', value: stats.GIVE_GO || 0 },
    { label: 'Gate passes', value: stats.GATES || 0 },
    { label: 'Turnovers', value: stats.TURNOVER || 0, accent: 'text-red-700' },
    { label: 'Holds ball too long', value: stats.HOLDS_BALL || 0, accent: 'text-amber-700' },
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-stone-900 rounded-t-3xl sm:rounded-2xl w-full sm:max-w-md max-h-[85vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-stone-900 border-b border-stone-800 px-5 py-4 flex items-center gap-3 z-10">
          <div className="w-12 h-12 rounded-xl bg-stone-900 text-lime-400 flex items-center justify-center font-display text-2xl tabular-nums">
            {player.number || '—'}
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-display text-xl truncate">{player.name}</div>
            {player.position && <div className="text-xs text-stone-400 font-bold tracking-wider">{player.position}</div>}
          </div>
          <button onClick={onClose} className="w-10 h-10 rounded-full bg-stone-900 flex items-center justify-center active:scale-95">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-5">
          <div className="bg-stone-950 rounded-xl p-4 mb-4">
            <div className="flex items-center justify-between mb-3">
              <div className="font-display text-lg">PERFORMANCE SCORE</div>
              <div className={`font-display text-3xl tabular-nums ${(score.overall || 0) >= 6 ? 'text-lime-600' : (score.overall || 0) >= 3 ? 'text-stone-100' : 'text-stone-400'}`}>{score.overall || 0}</div>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <PillarMini label="ATK" value={score.attacking || 0} />
              <PillarMini label="DEF" value={score.defending || 0} />
              <PillarMini label="DEC" value={score.decisions || 0} />
              <PillarMini label="INV" value={score.involvement || 0} />
            </div>
          </div>

          {/* WHERE HE PLAYED — pooled across the games the coach tagged. This is
              the ±1.7 m source; there is deliberately no tracked fallback, so a
              player with no tagged games sees a prompt rather than a wrong map. */}
          {row && (
            <div className="bg-stone-950 rounded-xl p-4 mb-4">
              <div className="flex items-center justify-between mb-2">
                <div className="font-display text-base">WHERE HE PLAYED</div>
                {row.tagged > 0 && (
                  <div className="text-[10px] text-lime-500">
                    {row.tagged} tagged game{row.tagged === 1 ? '' : 's'}
                  </div>
                )}
              </div>
              {row.tagged === 0 ? (
                <div className="text-[11px] text-stone-500 leading-snug">
                  Not tagged yet. Tagging a game in the film-room sampler takes about
                  10 minutes and gives his heatmap, territory and thirds — accurate to
                  about ±1.7 m. The camera can't reliably tell your players apart on its
                  own, so there is nothing to show until then.
                </div>
              ) : (
                <>
                  {row.avgDefPct != null && (
                    <div className="mb-2">
                      <ThirdsBar def={row.avgDefPct} mid={row.avgMidPct} att={row.avgAttPct} />
                      <div className="flex justify-between text-[9px] text-stone-500 mt-1">
                        <span>Def</span><span>Mid</span><span>Att</span>
                      </div>
                    </div>
                  )}
                  {heat.loading ? (
                    <div className="text-[11px] text-stone-500 animate-pulse py-6 text-center">Pooling his tagged positions…</div>
                  ) : heat.grid ? (
                    <>
                      <PlayerHeatmap grid={heat.grid} rows={heat.rows} cols={heat.cols} />
                      <div className="text-[9px] text-stone-600 mt-1">
                        {heat.nClicks} tagged positions over {heat.nGames} game{heat.nGames === 1 ? '' : 's'},
                        each game weighted by how many times you tagged him.
                      </div>
                    </>
                  ) : (
                    <div className="text-[11px] text-stone-500">
                      No pooled map available — his tagged games don't carry heatmap
                      grids yet. Re-run analytics for those games to generate them.
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          <div className="divide-y divide-stone-800">
            {rows.map(r => (
              <div key={r.label} className="flex items-center justify-between py-3">
                <div className="text-sm text-stone-200">{r.label}</div>
                <div className={`font-display text-2xl tabular-nums ${r.accent || 'text-stone-100'}`}>{r.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- LOCK SCREEN (removed — coach access is now role-based) ---------- */

/* ---------- HEADER ---------- */
function WeightsView({ weights, onSave, onBack }) {
  const [draft, setDraft] = useState(() => mergeWeights(weights));
  const [tab, setTab] = useState('actions'); // 'actions' | 'pillars'

  const setPoint = (group, key, raw) => {
    // Allow empty string (mid-edit) or minus sign without committing yet.
    const v = raw === '' || raw === '-' ? raw : Number(raw);
    setDraft(d => ({ ...d, [group]: { ...d[group], [key]: v } }));
  };
  const setPillar = (role, key, raw) => {
    const v = raw === '' ? '' : Number(raw);
    setDraft(d => ({ ...d, pillars: { ...d.pillars, [role]: { ...d.pillars[role], [key]: v } } }));
  };

  const normalize = (w) => {
    const fix = (obj) => {
      const out = {};
      for (const [k, v] of Object.entries(obj)) {
        const n = typeof v === 'number' ? v : Number(v);
        out[k] = Number.isFinite(n) ? n : 0;
      }
      return out;
    };
    return {
      points: fix(w.points),
      gkPoints: fix(w.gkPoints),
      pillars: { outfield: fix(w.pillars.outfield), gk: fix(w.pillars.gk) },
      shrinkMinutes: Math.max(0, Number(w.shrinkMinutes) || 0),
      gameTypes: fix(w.gameTypes),
    };
  };

  const setFairness = (key, raw) => {
    const v = raw === '' ? '' : Number(raw);
    setDraft(d => key === 'shrinkMinutes'
      ? { ...d, shrinkMinutes: v }
      : { ...d, gameTypes: { ...d.gameTypes, [key]: v } });
  };

  const saveAndExit = async () => {
    await onSave(normalize(draft));
    onBack();
  };

  const resetDefaults = () => setDraft(mergeWeights(DEFAULT_WEIGHTS));

  // Field labels: [key, label, emoji, pillar].
  const ATK_ROWS = [
    ['GOAL_atk',     'Goal',         '⚽'],
    ['ASSIST_atk',   'Assist',       '🅰️'],
    ['KEY_PASS_atk', 'Key pass',     '🔑'],
    ['SHOT_ON_atk',  'Shot on',      '🎯'],
    ['SHOT_OFF_atk', 'Shot off',     '❌'],
  ];
  const DEF_ROWS = [
    ['SAVE_def',        'Save',         '🧤'],
    ['BLOCK_def',       'Block',        '🛡️'],
    ['BALL_WIN_def',    'Ball win',     '🔥'],
    ['CLEAR_def',       'Clearance',    '🧹'],
    ['KICK_OUT_def',    'Kick out (under pressure)', '🥾'],
    ['DUEL_WIN_def',    '1v1 win',      '💪'],
    ['DUEL_LOSE_def',   '1v1 lose',     '💢'],
    ['CLEAN_SHEET_def', 'Clean sheet (GK)', '🧱'],
  ];
  const DEC_ROWS = [
    ['GIVE_GO_dec',         'Give & go (initiator)',    '🔄'],
    ['GIVE_GO_PARTNER_dec', 'Give & go (wall partner)', '🤝'],
    ['GATES_dec',      'Gates',       '🚪'],
    ['KEY_PASS_dec',   'Key pass',    '🔑'],
    ['ASSIST_dec',     'Assist',      '🅰️'],
    ['HOLDS_BALL_dec', 'Holds ball',  '🛑'],
    ['TURNOVER_dec',   'Turnover',    '🔁'],
  ];

  const renderPointRow = (group, [key, label, emoji]) => {
    const val = draft[group][key];
    const isNeg = typeof val === 'number' && val < 0;
    return (
      <div key={key} className="flex items-center justify-between gap-3 py-2 border-b border-stone-800 last:border-b-0">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-lg">{emoji}</span>
          <span className="text-sm text-stone-200 truncate">{label}</span>
        </div>
        <input
          type="number"
          step="1"
          inputMode="numeric"
          value={val}
          onChange={(e) => setPoint(group, key, e.target.value)}
          className={`w-20 text-center font-display text-lg py-1 rounded-lg border-2 ${isNeg ? 'border-red-300 bg-red-500/10 text-red-700' : 'border-stone-800 bg-stone-900 text-stone-100'} focus:outline-none focus:border-stone-500`}
        />
      </div>
    );
  };

  const PointsSection = ({ group }) => (
    <div className="space-y-4">
      <div className="bg-stone-900 rounded-2xl border-2 border-lime-500/30 px-4 py-3">
        <div className="font-display text-sm text-lime-700 mb-1">ATTACKING</div>
        {ATK_ROWS.map(r => renderPointRow(group, r))}
      </div>
      <div className="bg-stone-900 rounded-2xl border-2 border-sky-500/30 px-4 py-3">
        <div className="font-display text-sm text-sky-700 mb-1">DEFENDING</div>
        {DEF_ROWS.map(r => renderPointRow(group, r))}
      </div>
      <div className="bg-stone-900 rounded-2xl border-2 border-amber-200 px-4 py-3">
        <div className="font-display text-sm text-amber-700 mb-1">DECISIONS</div>
        {DEC_ROWS.map(r => renderPointRow(group, r))}
      </div>
      <p className="text-xs text-stone-400 px-1">
        Points per action. Negative values penalize the score. Goalkeepers and outfield players use the same per-action values — their roles differ via the PILLAR mix.
      </p>
    </div>
  );

  const PillarsSection = () => {
    const sum = (role) => ['atk', 'def', 'dec', 'inv'].reduce((s, k) => s + (Number(draft.pillars[role][k]) || 0), 0);
    const renderPillar = (role, label) => {
      const s = sum(role);
      const ok = s === 100;
      return (
        <div className="bg-stone-900 rounded-2xl border-2 border-stone-800 px-4 py-3">
          <div className="flex items-center justify-between mb-2">
            <div className="font-display text-sm text-stone-200">{label}</div>
            <div className={`text-xs font-display ${ok ? 'text-lime-700' : 'text-red-600'}`}>Σ {s}%</div>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {[['atk', 'ATK'], ['def', 'DEF'], ['dec', 'DEC'], ['inv', 'INV']].map(([k, lab]) => (
              <label key={k} className="flex flex-col items-center gap-1">
                <span className="text-[10px] font-display text-stone-400">{lab}</span>
                <input
                  type="number"
                  step="1"
                  min="0"
                  max="100"
                  inputMode="numeric"
                  value={draft.pillars[role][k]}
                  onChange={(e) => setPillar(role, k, e.target.value)}
                  className="w-full text-center font-display text-base py-1 rounded-lg border-2 border-stone-800 bg-stone-900 focus:outline-none focus:border-stone-500"
                />
              </label>
            ))}
          </div>
        </div>
      );
    };
    return (
      <div className="space-y-4">
        {renderPillar('outfield', 'OUTFIELD pillar mix')}
        {renderPillar('gk',       'GOALKEEPER pillar mix')}
        <p className="text-xs text-stone-400 px-1">
          Overall score = weighted blend of the four pillars (ATK · DEF · DEC · INV). Values are percentages — each row should sum to 100.
        </p>
      </div>
    );
  };

  const FairnessSection = () => (
    <div className="space-y-4">
      <div className="bg-stone-900 rounded-2xl border-2 border-violet-500/30 px-4 py-3">
        <div className="font-display text-sm text-violet-400 mb-1">SHRINKAGE (small-sample fairness)</div>
        <div className="flex items-center justify-between gap-3 py-2">
          <span className="text-sm text-stone-200">Virtual minutes of squad-average play</span>
          <input
            type="number" step="1" min="0" inputMode="numeric"
            value={draft.shrinkMinutes}
            onChange={(e) => setFairness('shrinkMinutes', e.target.value)}
            className="w-20 text-center font-display text-lg py-1 rounded-lg border-2 border-stone-800 bg-stone-900 text-stone-100 focus:outline-none focus:border-stone-500"
          />
        </div>
        <p className="text-xs text-stone-400">
          Every player's rate is blended with this many minutes of squad-average production, so a 6-minute cameo with one lucky goal can't top a 40-minute starter. 0 disables it. Scores converge to the raw rate as real minutes accumulate.
        </p>
      </div>
      <div className="bg-stone-900 rounded-2xl border-2 border-teal-500/30 px-4 py-3">
        <div className="font-display text-sm text-teal-400 mb-1">GAME-TYPE WEIGHT (season score)</div>
        {[['scrimmage', 'Scrimmage'], ['festival', 'Festival'], ['default', 'Everything else (league / tournament)']].map(([k, label]) => (
          <div key={k} className="flex items-center justify-between gap-3 py-2 border-b border-stone-800 last:border-b-0">
            <span className="text-sm text-stone-200">{label}</span>
            <input
              type="number" step="0.05" min="0" max="1" inputMode="decimal"
              value={draft.gameTypes[k]}
              onChange={(e) => setFairness(k, e.target.value)}
              className="w-20 text-center font-display text-lg py-1 rounded-lg border-2 border-stone-800 bg-stone-900 text-stone-100 focus:outline-none focus:border-stone-500"
            />
          </div>
        ))}
        <p className="text-xs text-stone-400 mt-1">
          How much each game type counts toward the SEASON score (per-game scores are unaffected). 0 excludes a type entirely.
        </p>
      </div>
      <p className="text-[10px] text-stone-500 px-1">Scoring v{SCORING_VERSION} · recalibrated Jun 2026</p>
    </div>
  );

  const TabBtn = ({ id, label }) => (
    <button
      onClick={() => setTab(id)}
      className={`flex-1 py-2 font-display text-sm rounded-xl border-2 transition ${tab === id ? 'bg-stone-900 text-white border-stone-900' : 'bg-stone-900 text-stone-300 border-stone-800'}`}
    >
      {label}
    </button>
  );

  return (
    <div className="pb-32">
      <Header
        title="SCORING WEIGHTS"
        onBack={onBack}
        right={
          <button
            onClick={resetDefaults}
            className="text-xs font-display text-stone-400 hover:text-stone-100 px-2 py-1 rounded-lg border border-stone-800"
          >
            RESET
          </button>
        }
      />

      <div className="px-4 pt-3 flex items-center gap-2">
        <TabBtn id="actions" label="ACTIONS" />
        <TabBtn id="pillars" label="PILLARS" />
        <TabBtn id="fairness" label="FAIRNESS" />
      </div>

      <div className="px-4 pt-4">
        {tab === 'actions' && <PointsSection group="points" />}
        {tab === 'pillars' && <PillarsSection />}
        {tab === 'fairness' && <FairnessSection />}
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-stone-900 border-t border-stone-800 px-4 py-3 flex gap-2">
        <button
          onClick={onBack}
          className="flex-1 py-3 rounded-xl border-2 border-stone-800 bg-stone-900 text-stone-200 font-display"
        >
          CANCEL
        </button>
        <button
          onClick={saveAndExit}
          className="flex-1 py-3 rounded-xl bg-lime-500 hover:bg-lime-600 text-stone-100 font-display border-2 border-lime-600"
        >
          SAVE
        </button>
      </div>
    </div>
  );
}

/* =========================================================================
   CALENDAR — one month grid shared by the dugout and the parent view.

   The dugout and the parent view mount the SAME component; `canEdit` is the
   only difference. Parents get a read-only surface, the coach gets the row
   actions and the game form that used to live permanently open at the top of
   the old schedule screen.

   Detail rows deliberately reuse the old schedule row's chip vocabulary —
   TournamentChip, FormatChip, the field pill, the map link, the squad count,
   the READY badge. No new row language: two dialects for the same game is how
   the two screens drifted apart in the first place.
   ========================================================================= */

/* ---------- calendar helpers (pure, no React) ---------- */

const CAL_WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

/** 'YYYY-MM' for a 'YYYY-MM-DD'. */
function calMonthOf(dayKey) {
  return (dayKey || '').slice(0, 7);
}

/** Step a 'YYYY-MM' by whole months. */
function calShiftMonth(month, delta) {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7)) - 1 + delta;
  const y2 = y + Math.floor(m / 12);
  const m2 = ((m % 12) + 12) % 12;
  return `${y2}-${String(m2 + 1).padStart(2, '0')}`;
}

/** Every 'YYYY-MM-DD' in a month, plus the weekday its 1st falls on. */
function calMonthDays(month) {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  // Day 0 of the next month is the last day of this one — avoids a leap-year table.
  const count = new Date(y, m, 0).getDate();
  const days = [];
  for (let d = 1; d <= count; d += 1) {
    days.push(`${month}-${String(d).padStart(2, '0')}`);
  }
  return { days, firstWeekday: new Date(y, m - 1, 1).getDay() };
}

function calMonthLabel(month) {
  return new Date(`${month}-01T12:00`).toLocaleDateString('en', { month: 'long', year: 'numeric' });
}

function calDayLabel(dayKey) {
  return new Date(`${dayKey}T12:00`).toLocaleDateString('en', { weekday: 'short', month: 'short', day: 'numeric' });
}

/** "Synced 8m ago". Null when we have no sync stamp to be honest about. */
function calSyncedLabel(syncedAt) {
  if (!syncedAt) return null;
  const mins = Math.max(0, Math.round((Date.now() - Number(syncedAt)) / 60000));
  if (mins < 1) return 'Synced just now';
  if (mins < 60) return `Synced ${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `Synced ${hrs}h ago`;
  return `Synced ${Math.round(hrs / 24)}d ago`;
}

/**
 * The bar colour for an entry, or null when it must not draw one.
 *
 * Wraps the model's entryColor() rather than calling it directly. entryColor()
 * now returns null for an off day correctly, but it did not always: it resolved
 * kinds with `??`, and ENTRY_COLORS.off IS null, which `??` treats as absent — so
 * off days fell through to team-event grey and painted the one bar the design
 * forbids. The model was fixed to look the kind up with `in`; this guard stays as
 * a cheap belt-and-braces so a future refactor of that lookup cannot silently
 * reintroduce a grey bar on a day that means "nothing is happening".
 */
function calBarColor(entry) {
  if (!entry || entry.kind === 'off') return null;
  return entryColor(entry);
}

/**
 * How to name an entry in a row, a toast or a confirm. Module-level because the
 * dugout home's NEXT UP list renders the same rows as the calendar, and two
 * copies of this would drift.
 */
function entryLabel(entry) {
  if (entry.opponent) return `vs ${entry.opponent}`;
  if (entry.title) return entry.title;
  const base = (entry.kind || '').replace(/_own$/, '').replace(/_/g, ' ');
  return base ? base.charAt(0).toUpperCase() + base.slice(1) : 'this event';
}

/**
 * The next few entries at or after `today`, walked across month boundaries on
 * purpose: paging the grid to December must not hide next week. Shared by the
 * calendar and the dugout home so the two lists can never disagree.
 */
function calAgenda(model, today, limit = 4) {
  const out = [];
  for (const dayKey of [...model.days.keys()].sort()) {
    if (dayKey < today) continue;
    for (const entry of model.days.get(dayKey)) {
      if (out.length >= limit) return out;
      out.push(entry);
    }
  }
  return out;
}

/** The SVG pattern id for a kind's cancelled cross-hatch. */
const calHatchId = (kind) => `calx-${kind}`;

/**
 * "Scrimmage vs Caboto" -> "Caboto". TeamSnap titles are free text, so this is
 * a best-effort prefill the coach can correct, never an authoritative parse.
 */
function calOpponentFromTitle(title) {
  const t = (title || '').trim();
  const m = t.match(/\b(?:vs\.?|versus|@|at)\s+(.+)$/i);
  return (m ? m[1] : t).replace(/\(.*?\)/g, '').trim();
}

/** True when a schedule item carries everything START needs to skip setup. */
function calIsReady(item) {
  return Array.isArray(item?.squadIds) && item.squadIds.length > 0
    && typeof item.isHome === 'boolean'
    && typeof item.halfLengthMin === 'number'
    && !!item.homeColor && !!item.awayColor;
}

// ── BEGIN calendarModel (generated) ──
// Generated by scripts/inline_calendar_model.py from js/calendarModel.mjs.
// Do not edit here — edit the module and re-run the script.
/**
 * Merge the three schedule sources into one day-keyed model.
 *
 * Pure by design — no React, no window, no clock. `today` is a parameter so the
 * whole thing is unit-testable, which matters because this is where the bugs
 * live: three sources describe overlapping reality and only some of the overlap
 * is duplication.
 */

const ENTRY_COLORS = {
  game_scheduled: '#378ADD',
  game_unscheduled: '#378ADD',
  won: '#639922',
  lost: '#E24B4A',
  drawn: '#5F5E5A',
  practice: '#7F77DD',
  tryout: '#EF9F27',
  team_event: '#B4B2A9',
  tournament_block: '#378ADD',
  off: null, // an off day means nothing is happening; a bar would say the opposite
  // Coach-created non-game events reuse their TeamSnap hues: which system owns
  // an event is a maintenance detail, not something the coach should have to see.
  practice_own: '#7F77DD',
  tryout_own: '#EF9F27',
  team_event_own: '#B4B2A9',
};

/** Cancelled bars keep their kind's hue at the 600 stop with an X over it. */
const CANCELLED_FILL = {
  game_scheduled: '#185FA5',
  game_unscheduled: '#185FA5',
  tournament_block: '#185FA5',
  practice: '#534AB7',
  tryout: '#854F0B',
  team_event: '#B4B2A9',
  practice_own: '#534AB7',
  tryout_own: '#854F0B',
  team_event_own: '#B4B2A9',
};

/** Light strokes read on the dark fills; grey has no dark end, so it needs black. */
const CANCELLED_STROKE = {
  game_scheduled: '#E6F1FB',
  game_unscheduled: '#E6F1FB',
  tournament_block: '#E6F1FB',
  practice: '#EEEDFE',
  tryout: '#FAEEDA',
  team_event: '#2C2C2A',
  practice_own: '#EEEDFE',
  tryout_own: '#FAEEDA',
  team_event_own: '#2C2C2A',
};

/**
 * Kinds a hide flag applies to: everything TeamSnap owns. The cron re-mirrors
 * the feed every 15 minutes and the feed is read-only, so these cannot be
 * deleted — only hidden locally. Coach-created entries are genuinely deletable
 * and must NOT appear here, or delete would silently become hide.
 */
const HIDEABLE_KINDS = new Set([
  'practice', 'tryout', 'team_event', 'game_unscheduled', 'tournament_block',
]);

const COMPETITION = /\b(tournament|festival|invitational|classic|cup)\b/i;
const norm = (s) => (s || '').trim().toLowerCase();
const dayKey = (d) => (d || '').slice(0, 10);
const monthKey = (d) => (d || '').slice(0, 7);

/** Sort within a day: timed entries by clock, all-day entries last. */
function byTime(a, b) {
  if (!a.time && b.time) return 1;
  if (a.time && !b.time) return -1;
  if (a.time !== b.time) return a.time.localeCompare(b.time);
  return (a.title || '').localeCompare(b.title || '');
}

function buildCalendarModel({ teamsnapEvents = [], schedule = [], games = [], today, hidden = [] }) {
  const hiddenKeys = hidden instanceof Set ? hidden : new Set(hidden);
  const days = new Map();
  const push = (entry) => {
    const k = dayKey(entry.date);
    if (!k) return;
    // Filtered here, before the entry reaches a day, so a day whose only event
    // is hidden drops out of the map entirely instead of rendering an empty cell.
    if (hiddenKeys.has(entry.key)) return;
    if (!days.has(k)) days.set(k, []);
    days.get(k).push(entry);
  };

  // 1. Finished games win outright: a played game is the most authoritative
  //    record of a day, so it shadows whatever was scheduled for it.
  const finishedKeys = new Set();
  for (const g of games) {
    if (g.status !== 'finished') continue;
    const k = dayKey(g.date);
    finishedKeys.add(`${k}|${norm(g.opponent)}`);
    const our = Number(g.ourScore) || 0;
    const opp = Number(g.oppScore) || 0;
    push({
      key: `game:${g.id}`,
      kind: 'game_finished',
      date: k,
      time: g.time || '',
      title: `vs ${g.opponent}`,
      opponent: g.opponent || '',
      tournament: g.tournament || '',
      field: g.field || '',
      location: g.location || '',
      venue: '', arrival: '', allDay: false, cancelled: false,
      result: our > opp ? 'won' : our < opp ? 'lost' : 'drawn',
      ourScore: our, oppScore: opp,
      gameId: g.id, scheduleId: null, teamsnapUid: g.teamsnapUid || null,
      raw: g,
    });
  }

  // 2. Coach-owned schedule items. Games unless the same fixture already
  //    finished; anything with a `type` other than `game` is a practice, tryout
  //    or team event the coach created in the app rather than in TeamSnap.
  const scheduledDays = new Set();
  for (const s of schedule) {
    const k = dayKey(s.date);
    // No `type` means game: every item predates the field.
    const sType = s.type || 'game';
    if (sType !== 'game') {
      push({
        key: `sched:${s.id}`,
        kind: `${sType}_own`,
        date: k,
        time: s.time || '',
        // A non-game item has no opponent, so `vs undefined` must not appear.
        title: s.title || sType.replace('_', ' '),
        opponent: '',
        tournament: '',
        field: s.field || '',
        location: s.location || '',
        venue: '', arrival: '', allDay: false,
        cancelled: !!s.cancelled,
        result: null, ourScore: null, oppScore: null,
        gameId: null, scheduleId: s.id, teamsnapUid: null,
        raw: s,
      });
      continue;
    }
    if (finishedKeys.has(`${k}|${norm(s.opponent)}`)) continue;
    // Only GAME days go in here: a coach-created practice on a tournament day
    // would otherwise absorb the all-day block below and hide it.
    scheduledDays.add(k);
    push({
      key: `sched:${s.id}`,
      kind: 'game_scheduled',
      date: k,
      time: s.time || '',
      title: `vs ${s.opponent}`,
      opponent: s.opponent || '',
      tournament: s.tournament || '',
      field: s.field || '',
      location: s.location || '',
      venue: '', arrival: '', allDay: false,
      cancelled: !!s.cancelled,
      result: null, ourScore: null, oppScore: null,
      gameId: null, scheduleId: s.id, teamsnapUid: s.teamsnapUid || null,
      raw: s,
    });
  }

  // 3. TeamSnap events. An all-day COMPETITION block on a day that already has
  //    coach games is not an event in its own right — it is those games'
  //    context, so it contributes a title and no bar. Aug 22 2026 is the real
  //    case: one Gatorade block (plus an ECSL one) over two entered games.
  for (const ev of teamsnapEvents) {
    if (ev.missingFromFeed) continue;
    const k = dayKey(ev.date);
    const isBlock = ev.type === 'game' && (ev.allDay || COMPETITION.test(ev.title || ''));

    if (isBlock && scheduledDays.has(k)) {
      for (const e of days.get(k) || []) {
        if (e.kind === 'game_scheduled' && !e.tournament) e.tournament = ev.title;
        if (e.kind === 'game_scheduled' && !e.teamsnapUid) e.teamsnapUid = ev.uid;
      }
      continue;
    }

    // TeamSnap's own type vocabulary is not ours. A timed `game` it knows about
    // but the coach has not set up (the 7 scrimmages in the current feed) is
    // `game_unscheduled`: it draws as a game and offers "schedule this", but it
    // has no squad, colours or START because none of that exists yet.
    const kind = isBlock
      ? 'tournament_block'
      : ev.type === 'game'
        ? 'game_unscheduled'
        : (ev.type || 'team_event');

    push({
      key: `ts:${ev.uid}`,
      kind,
      date: k,
      time: ev.time || '',
      title: ev.title || '',
      opponent: '',
      tournament: isBlock ? (ev.title || '') : '',
      field: '',
      location: ev.address || '',
      venue: ev.venue || '',
      // Midnight is TeamSnap's placeholder, not a real arrival time.
      arrival: /^12:00\s*AM$/i.test(ev.arrival || '') ? '' : (ev.arrival || ''),
      allDay: !!ev.allDay,
      cancelled: !!ev.canceled,
      result: null, ourScore: null, oppScore: null,
      gameId: null, scheduleId: null, teamsnapUid: ev.uid,
      raw: ev,
    });
  }

  for (const list of days.values()) list.sort(byTime);

  const byMonth = new Map();
  for (const k of [...days.keys()].sort()) {
    const m = monthKey(k);
    if (!byMonth.has(m)) byMonth.set(m, []);
    byMonth.get(m).push(k);
  }

  return { days, byMonth, today };
}

/**
 * The bar colour for an entry, or null when it should not draw one.
 *
 * `off` deliberately returns null: an off day means nothing is happening, so a
 * bar would say the opposite. Note the lookup cannot use `??` to fall back —
 * `ENTRY_COLORS.off` IS null, and `??` treats that as absent and would hand back
 * team-event grey, drawing exactly the bar the spec forbids. Use `in` so a
 * declared null stays null and only genuinely unknown kinds fall back.
 */
function entryColor(entry) {
  if (entry.kind === 'game_finished') return ENTRY_COLORS[entry.result] || null;
  if (entry.kind in ENTRY_COLORS) return ENTRY_COLORS[entry.kind];
  return ENTRY_COLORS.team_event;
}
// ── END calendarModel (generated) ──

/* ---------- MONTH GRID ---------- */
// Bars, not text: at seven columns there is no room for a title, and the whole
// point of the grid is shape-of-the-month at a glance. The rows below carry
// the detail.
function CalendarMonthGrid({ model, month, selected, today, onSelect }) {
  const { days, firstWeekday } = calMonthDays(month);
  const blanks = [];
  for (let i = 0; i < firstWeekday; i += 1) blanks.push(i);

  return (
    <div className="bg-stone-900 border border-stone-800 rounded-2xl p-2">
      <div className="grid grid-cols-7 gap-1 mb-1">
        {CAL_WEEKDAYS.map((w, i) => (
          <div key={i} className="text-center text-[10px] font-bold text-stone-500 tracking-widest py-1">
            {w}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {blanks.map((i) => <div key={`b${i}`} />)}
        {days.map((dayKey) => {
          const entries = model.days.get(dayKey) || [];
          const isToday = dayKey === today;
          const isSelected = dayKey === selected;
          const anyCancelled = entries.some((e) => e.cancelled);
          // Cap at three bars — a fourth makes every bar too thin to read. The
          // count badge still reports the true total so nothing hides.
          const bars = entries.slice(0, 3);
          return (
            <button
              key={dayKey}
              onClick={() => onSelect?.(dayKey)}
              className={`relative min-h-[46px] rounded-lg p-1 flex flex-col items-stretch text-left active:scale-95 transition ${
                isSelected ? 'bg-lime-500/15' : 'bg-stone-950/60'
              } ${
                isToday ? 'border-2 border-lime-400'
                : anyCancelled ? 'border border-dashed border-red-800/70'
                : isSelected ? 'border border-lime-500/50'
                : 'border border-stone-800'
              }`}
            >
              <div className={`text-[11px] font-bold leading-none tabular-nums ${
                isToday ? 'text-lime-300' : isSelected ? 'text-stone-100' : 'text-stone-400'
              }`}>
                {Number(dayKey.slice(8, 10))}
              </div>
              <div className="mt-1 space-y-0.5">
                {bars.map((entry) => {
                  const color = calBarColor(entry);
                  // Off days render no bar at all: their entire meaning is that
                  // nothing is happening, and a bar would say the opposite.
                  if (!color) return null;
                  if (entry.cancelled) {
                    return (
                      <svg key={entry.key} className="w-full block" height="6" preserveAspectRatio="none">
                        <rect
                          width="100%"
                          height="6"
                          rx="1.5"
                          fill={`url(#${calHatchId(entry.kind)})`}
                        />
                      </svg>
                    );
                  }
                  return (
                    <div
                      key={entry.key}
                      className="h-1.5 rounded-[2px]"
                      style={{ background: color }}
                    />
                  );
                })}
              </div>
              {entries.length > 1 && (
                <span className="absolute top-0.5 right-0.5 bg-stone-800 text-stone-300 text-[9px] font-bold leading-none rounded-full px-1 py-0.5 tabular-nums">
                  {entries.length}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- DAY DETAIL ROWS ---------- */
// One renderer per entry kind. Every kind the model emits is handled — a missing
// branch is a blank row in production, which is the failure mode nobody notices
// until a parent reports "my kid's practice vanished".
//
// Which verbs a row offers is decided by WHO OWNS the event, not by what it is.
// App-owned entries (game_scheduled and the three *_own kinds) get edit, cancel
// and delete. TeamSnap-owned entries get hide only: the feed is read-only, and
// the cron re-mirrors it every 15 minutes, so an edit would revert and a delete
// is not possible at all.
function CalendarDayRows({
  entries, canEdit, activeGame, onOpenGame, onStartGame,
  onEditEntry, onDeleteEntry, onToggleCancelEntry, onScheduleFrom, onHideEntry,
}) {
  if (!entries || entries.length === 0) {
    return (
      <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-stone-400 text-sm">
        Nothing scheduled.{canEdit ? ' Tap the day again to add a game, practice or event.' : ''}
      </div>
    );
  }

  const mapLink = (loc) => (
    <a
      href={loc.startsWith('http') ? loc : `https://maps.google.com/?q=${encodeURIComponent(loc)}`}
      target="_blank" rel="noopener noreferrer"
      className="text-xs text-blue-400 underline flex items-center gap-1 mt-0.5"
      onClick={(e) => e.stopPropagation()}
    >
      <MapPin className="w-3 h-3" /> {loc.startsWith('http') ? 'View Map' : loc}
    </a>
  );

  const cancelledBadge = (
    <span className="inline-block bg-red-500/15 text-red-300 border border-red-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
      CANCELLED
    </span>
  );

  // The kind's swatch, hatched when cancelled. 8px here rather than the grid's
  // 6px, so the strokes get 1.0px instead of 0.8px (see the detail defs).
  const swatch = (entry) => {
    const color = calBarColor(entry);
    if (!color) return <span className="w-2 h-8 rounded-sm bg-stone-800 shrink-0" />;
    if (entry.cancelled) {
      return (
        <svg className="shrink-0" width="8" height="32" aria-hidden="true">
          <rect width="8" height="32" rx="2" fill={`url(#${calHatchId(entry.kind)}-detail)`} />
        </svg>
      );
    }
    return <span className="w-2 h-8 rounded-sm shrink-0" style={{ background: color }} />;
  };

  const shell = (entry, children, extra = '') => (
    <div
      key={entry.key}
      className={`bg-stone-900 border rounded-xl p-3 flex items-center gap-3 ${
        entry.cancelled ? 'border-red-900/60 opacity-70' : 'border-stone-800'
      } ${extra}`}
    >
      {swatch(entry)}
      {children}
    </div>
  );

  // Edit / cancel / delete for an app-owned entry. Shared by games and by the
  // three coach-created kinds so a practice's buttons are the same buttons in
  // the same order — the coach should not have to relearn the row per type.
  const ownerActions = (entry, extraAbove = null) => (
    <div className="flex flex-col gap-1.5 shrink-0 items-end">
      {extraAbove}
      <div className="flex gap-1.5">
        <button
          onClick={() => onEditEntry?.(entry)}
          className="w-8 h-8 rounded-full bg-amber-500/15 text-amber-400 flex items-center justify-center active:scale-90 transition text-sm"
          title="Edit"
        >
          ✏️
        </button>
        <button
          onClick={() => onToggleCancelEntry?.(entry)}
          className={`w-8 h-8 rounded-full flex items-center justify-center active:scale-90 transition text-sm ${
            entry.cancelled ? 'bg-lime-500/15 text-lime-400' : 'bg-stone-800 text-stone-300'
          }`}
          title={entry.cancelled ? 'Restore' : 'Mark cancelled'}
        >
          {entry.cancelled ? '↻' : '🚫'}
        </button>
        <button
          onClick={() => onDeleteEntry?.(entry)}
          className="w-8 h-8 rounded-full bg-red-500/10 text-red-500 flex items-center justify-center active:scale-90 transition"
          title="Delete"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );

  // The only verb available on a TeamSnap-owned row. Deliberately NOT a bin
  // icon: nothing is deleted, and the confirm the handler raises says so.
  //
  // Gated on HIDEABLE_KINDS rather than on the caller's case label, so the set
  // in the merge stays the single source of truth. A kind that becomes
  // app-owned later loses this button without anyone editing the row markup.
  const hideButton = (entry) => (
    HIDEABLE_KINDS.has(entry.kind) ? (
      <button
        onClick={() => onHideEntry?.(entry)}
        className="px-2.5 h-8 rounded-full bg-stone-800 text-stone-300 flex items-center justify-center active:scale-90 transition text-[11px] font-bold tracking-wider shrink-0"
        title="Hide from the calendar"
      >
        HIDE
      </button>
    ) : null
  );

  const renderRow = (entry) => {
    switch (entry.kind) {
      /* ---- 1. a finished game: result, score, tap through to AnalyticsPanel ---- */
      case 'game_finished': {
        const badge = entry.result === 'won' ? 'W' : entry.result === 'lost' ? 'L' : 'D';
        const badgeCls = entry.result === 'won' ? 'bg-lime-500/15 text-lime-300'
          : entry.result === 'lost' ? 'bg-red-500/15 text-red-300'
          : 'bg-stone-800 text-stone-300';
        return (
          <button
            key={entry.key}
            onClick={() => onOpenGame?.(entry.gameId)}
            className="w-full bg-stone-900 border border-stone-800 rounded-xl p-3 flex items-center gap-3 active:scale-[0.99] transition text-left"
          >
            <div className={`w-10 h-10 rounded-lg flex items-center justify-center font-display text-base shrink-0 ${badgeCls}`}>
              {badge}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-sm truncate">vs {entry.opponent}</div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                <GameTypeChip value={gameTypeOf(entry.raw)} />
                <TournamentChip value={entry.tournament} gameType={gameTypeOf(entry.raw)} />
                <FormatChip value={entry.raw?.format} />
                {entry.time && <span>{formatTime12(entry.time)}</span>}
              </div>
            </div>
            <div className="font-display text-xl tabular-nums shrink-0">
              {entry.ourScore}–{entry.oppScore}
            </div>
            <ChevronRight className="w-4 h-4 text-stone-400 shrink-0" />
          </button>
        );
      }

      /* ---- 2. a coach-scheduled game: the full detail row ---- */
      case 'game_scheduled': {
        const item = entry.raw || {};
        const ready = calIsReady(item);
        return shell(entry, (
          <>
            <div className="flex-1 min-w-0">
              <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                vs {entry.opponent}
              </div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                {entry.cancelled && cancelledBadge}
                <GameTypeChip value={gameTypeOf(item)} />
                <TournamentChip value={entry.tournament} gameType={gameTypeOf(item)} />
                <FormatChip value={item.format} />
                {entry.time && <span>{formatTime12(entry.time)}</span>}
                {entry.field && (
                  <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                    📍 {entry.field}
                  </span>
                )}
                {Array.isArray(item.squadIds) && item.squadIds.length > 0 && (
                  <span className="inline-flex items-center gap-1 bg-lime-500/15 text-lime-300 border border-lime-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                    👥 {item.squadIds.length}
                  </span>
                )}
                {ready && (
                  <span className="inline-block bg-lime-500/20 text-lime-200 border border-lime-400/60 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                    READY
                  </span>
                )}
              </div>
              {entry.location && mapLink(entry.location)}
            </div>
            {canEdit && ownerActions(entry, (
              !activeGame && !entry.cancelled && onStartGame ? (
                <button
                  onClick={() => onStartGame(item)}
                  className="px-3 py-1.5 bg-lime-500 text-stone-100 font-display text-xs rounded-lg active:scale-95 transition"
                >
                  START
                </button>
              ) : null
            ))}
          </>
        ));
      }

      /* ---- 3. a TeamSnap fixture the coach hasn't set up ----
         It has a real kickoff time, so it is a game — but no squad, no colours
         and no half length exist yet, so there is deliberately no START and no
         READY here. "Schedule this" is the affordance that creates them. */
      case 'game_unscheduled':
        return shell(entry, (
          <>
            <div className="flex-1 min-w-0">
              <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                {entry.title || 'Game'}
              </div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                {entry.cancelled && cancelledBadge}
                <span className="inline-block bg-stone-800 text-stone-400 border border-stone-700 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                  NOT SET UP
                </span>
                {entry.time && <span>{formatTime12(entry.time)}</span>}
                {entry.venue && <span className="truncate">{entry.venue}</span>}
              </div>
              {entry.location && mapLink(entry.location)}
            </div>
            {canEdit && (
              <div className="flex items-center gap-1.5 shrink-0">
                {!entry.cancelled && (
                  <button
                    onClick={() => onScheduleFrom?.(entry)}
                    className="px-3 py-1.5 bg-stone-800 text-lime-300 border border-lime-500/40 font-display text-xs rounded-lg active:scale-95 transition"
                  >
                    SCHEDULE
                  </button>
                )}
                {hideButton(entry)}
              </div>
            )}
          </>
        ));

      /* ---- 4. an all-day competition day with no coach games on it yet ---- */
      case 'tournament_block':
        return shell(entry, (
          <>
            <div className="flex-1 min-w-0">
              <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                {entry.title || 'Tournament'}
              </div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                {entry.cancelled && cancelledBadge}
                <span className="inline-block bg-amber-400/25 text-amber-100 border border-amber-300/60 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                  ALL DAY
                </span>
                {entry.arrival && <span>Arrive {entry.arrival}</span>}
                {entry.venue && <span className="truncate">{entry.venue}</span>}
              </div>
              {entry.location && mapLink(entry.location)}
            </div>
            {canEdit && (
              <div className="flex items-center gap-1.5 shrink-0">
                {!entry.cancelled && (
                  <button
                    onClick={() => onScheduleFrom?.(entry)}
                    className="px-3 py-1.5 bg-stone-800 text-lime-300 border border-lime-500/40 font-display text-xs rounded-lg active:scale-95 transition"
                  >
                    + GAME
                  </button>
                )}
                {hideButton(entry)}
              </div>
            )}
          </>
        ));

      /* ---- 5-7. TeamSnap owns these. An edit here would revert within 15
         minutes when the cron next runs and the feed cannot be deleted from, so
         the only verb offered is hide — which is local to this app. ---- */
      case 'practice':
      case 'tryout':
      case 'team_event': {
        const label = entry.kind === 'practice' ? 'PRACTICE'
          : entry.kind === 'tryout' ? 'TRYOUT' : 'TEAM EVENT';
        return shell(entry, (
          <>
            <div className="flex-1 min-w-0">
              <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                {entry.title || label}
              </div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                {entry.cancelled && cancelledBadge}
                <span className="inline-block bg-stone-800 text-stone-400 border border-stone-700 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                  {label}
                </span>
                {entry.time && <span>{formatTime12(entry.time)}</span>}
                {entry.arrival && <span>Arrive {entry.arrival}</span>}
                {entry.venue && <span className="truncate">{entry.venue}</span>}
              </div>
              {entry.location && mapLink(entry.location)}
            </div>
            {canEdit && hideButton(entry)}
          </>
        ));
      }

      /* ---- 9-11. The same three things, created by the coach in this app.
         These live in the `schedule` array, so unlike their TeamSnap twins they
         are genuinely editable and deletable. No START: only a game has a
         lineup to start. ---- */
      case 'practice_own':
      case 'tryout_own':
      case 'team_event_own': {
        const label = entry.kind === 'practice_own' ? 'PRACTICE'
          : entry.kind === 'tryout_own' ? 'TRYOUT' : 'TEAM EVENT';
        // A coach-created session can now carry a competition tag and name —
        // a practice AT the Canton Cup. The model leaves `tournament` blank for
        // non-games, so both come off `raw`, the saved schedule item itself.
        const ownItem = entry.raw || {};
        const ownType = ownItem.gameType ? gameTypeOf(ownItem) : '';
        return shell(entry, (
          <>
            <div className="flex-1 min-w-0">
              <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                {entry.title || label}
              </div>
              <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                {entry.cancelled && cancelledBadge}
                <span className="inline-block bg-stone-800 text-stone-400 border border-stone-700 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                  {label}
                </span>
                <GameTypeChip value={ownType} />
                <TournamentChip value={ownItem.tournament} gameType={ownType} />
                {entry.time && <span>{formatTime12(entry.time)}</span>}
                {entry.field && (
                  <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                    📍 {entry.field}
                  </span>
                )}
              </div>
              {entry.location && mapLink(entry.location)}
            </div>
            {canEdit && ownerActions(entry)}
          </>
        ));
      }

      /* ---- 8. an explicit day off. Nothing is happening, so there is
         nothing to act on. ---- */
      case 'off':
        return (
          <div
            key={entry.key}
            className="bg-stone-950/60 border border-dashed border-stone-800 rounded-xl p-3 flex items-center gap-3"
          >
            <span className="text-stone-600 text-lg shrink-0">🌙</span>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-sm text-stone-400 truncate">No practice</div>
              {entry.title && <div className="text-xs text-stone-500 truncate mt-0.5">{entry.title}</div>}
            </div>
          </div>
        );

      // Unreachable while buildCalendarModel only emits the kinds above,
      // but a silent blank row would be worse than a visible fallback.
      default:
        return shell(entry, (
          <div className="flex-1 min-w-0">
            <div className="font-bold text-sm truncate">{entry.title || 'Event'}</div>
            {entry.time && <div className="text-xs text-stone-400 mt-0.5">{formatTime12(entry.time)}</div>}
          </div>
        ));
    }
  };

  return <div className="space-y-2">{entries.map(renderRow)}</div>;
}

/* ---------- CALENDAR VIEW ---------- */
function CalendarView({
  model, canEdit = false, today, syncedAt = null,
  roster = [], opponentSuggestions = [], activeGame = null,
  onOpenGame, onStartGame, onSaveEntry, onDeleteGame, onToggleCancel,
  onEditSquad, onManageOpponents, onBack, askConfirm, showToast,
  // Hiding is coach-only and needs the raw sources to rebuild the list of what
  // is hidden — the main model has already filtered those entries out.
  hiddenKeys = null, onHide, onUnhide,
  teamsnapEvents = [], schedule = [], games = [],
  // Set by the caller when returning from the squad picker, so the coach lands
  // back in the edit sheet they left instead of a closed calendar. Mirrors
  // the old screen's initialEditId/onConsumedInitialEditId pair.
  resumeEditId = null, onConsumedResumeEditId,
  // The dugout shows NEXT UP on its own home screen, below the calendar tile,
  // so it suppresses the in-calendar copy rather than showing it twice.
  showAgenda = true,
  // Open on a specific day — set when arriving from a NEXT UP tap.
  jumpToDate = null, onConsumedJumpTo,
}) {
  const [month, setMonth] = useState(() => calMonthOf(today) || calMonthOf(new Date().toISOString()));
  const [selected, setSelected] = useState(today);
  // { mode: 'new'|'edit', scheduleId, seed } — null when the sheet is closed.
  const [sheet, setSheet] = useState(null);
  // Bumped on every open so a blank form re-seeds even when it was blank before.
  const [formNonce, setFormNonce] = useState(0);
  const [showHidden, setShowHidden] = useState(false);

  // Re-open the sheet for the game whose squad was just picked. The sheet is
  // internal state and dies when the view unmounts for the picker detour, so
  // without this the coach silently loses their place mid-edit.
  useEffect(() => {
    if (!resumeEditId || !canEdit) return;
    for (const entries of model.days.values()) {
      const hit = entries.find((e) => e.scheduleId === resumeEditId);
      if (hit) {
        setSelected(hit.date);
        setMonth(calMonthOf(hit.date));
        setSheet({ mode: 'edit', scheduleId: resumeEditId, seed: hit.raw || null });
        setFormNonce((n) => n + 1);
        break;
      }
    }
    onConsumedResumeEditId?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeEditId]);

  // Arriving from a NEXT UP tap: select that entry's day and page the grid to it.
  useEffect(() => {
    if (!jumpToDate) return;
    setSelected(jumpToDate);
    setMonth(calMonthOf(jumpToDate));
    onConsumedJumpTo?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpToDate]);

  const selectedEntries = model.days.get(selected) || [];
  const syncedLabel = calSyncedLabel(syncedAt);

  // The one thing a month grid is worse at than the old flat list: seeing
  // everything upcoming at once. Deliberately NOT scoped to the displayed
  // month — it walks every day the model holds, so paging to December never
  // hides next week.
  const agenda = useMemo(() => calAgenda(model, today), [model, today]);

  // What the coach has hidden. The main model dropped these on purpose, so the
  // only way to describe them is to merge again with the filter off and pick
  // out the keys in the hidden set. Doubles the merge for ~120 events, which is
  // the price of the panel existing at all — without it, hiding is a one-way
  // door with nothing to reopen.
  const hiddenEntries = useMemo(() => {
    if (!canEdit || !hiddenKeys || hiddenKeys.size === 0) return [];
    const unfiltered = buildCalendarModel({ teamsnapEvents, schedule, games, today });
    const out = [];
    for (const dayKey of [...unfiltered.days.keys()].sort()) {
      for (const entry of unfiltered.days.get(dayKey)) {
        if (hiddenKeys.has(entry.key)) out.push(entry);
      }
    }
    return out;
  }, [canEdit, hiddenKeys, teamsnapEvents, schedule, games, today]);

  const handleUnhide = (entry) => {
    onUnhide?.(entry.key);
  };

  const openSheet = (next) => {
    setFormNonce((n) => n + 1);
    setSheet(next);
  };
  const closeSheet = () => setSheet(null);

  const handleDaySelect = (dayKey) => {
    if (dayKey === selected && canEdit && (model.days.get(dayKey) || []).length === 0) {
      // Second tap on an already-selected empty day is the "add here" gesture.
      openSheet({ mode: 'new', scheduleId: null, seed: { date: dayKey } });
      return;
    }
    setSelected(dayKey);
  };

  const handleEditEntry = (entry) => {
    openSheet({ mode: 'edit', scheduleId: entry.scheduleId, seed: null });
  };

  // Prefill from what the day already knows: the date always, the tournament
  // and venue from an all-day block, the time and opponent when TeamSnap gave
  // us a real fixture.
  const handleScheduleFrom = (entry) => {
    openSheet({
      mode: 'new',
      scheduleId: null,
      seed: {
        date: entry.date,
        time: entry.kind === 'game_unscheduled' ? (entry.time || '') : '',
        tournament: entry.kind === 'tournament_block' ? (entry.title || '') : (entry.tournament || ''),
        location: entry.location || entry.venue || '',
        opponent: entry.kind === 'game_unscheduled' ? calOpponentFromTitle(entry.title) : '',
      },
    });
  };

  // A game is named by its opponent, everything else by its title. Going
  // through `entry.opponent` unconditionally would put "vs " with nothing after
  // it in every practice's confirm, toast and agenda row.
  //
  // Title is optional on a non-game, so it needs a floor: without one an
  // untitled practice renders a blank row. Falls back to the kind's own name
  // ("Practice"), which is what the day rows already show.
  const handleDeleteEntry = (entry) => {
    const label = `${entryLabel(entry)}${entry.date ? ' on ' + new Date(entry.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' }) : ''}`;
    const run = () => {
      if (sheet?.scheduleId === entry.scheduleId) closeSheet();
      onDeleteGame?.(entry.scheduleId);
      showToast?.(`🗑 Deleted ${label}`);
    };
    if (askConfirm) askConfirm(`Delete ${label} from the schedule?`, run, { danger: true, yesLabel: 'DELETE' });
    else run();
  };

  const handleToggleCancelEntry = (entry) => {
    onToggleCancel?.(entry.scheduleId);
    showToast?.(entry.cancelled ? `↩ Restored ${entryLabel(entry)}` : `⛔ Cancelled ${entryLabel(entry)}`);
  };

  // Hide, not delete — and the confirm has to say so, because the bin-shaped
  // mental model is wrong here. TeamSnap keeps sending the event; all this does
  // is stop showing it, for the coach AND for parents reading the same flags.
  const handleHideEntry = (entry) => {
    const run = () => onHide?.(entry.key);
    // One flowing string, not paragraphs: ConfirmDialog renders `message` in a
    // plain div with no whitespace-pre-line, so newlines would collapse anyway.
    const msg = `Hide "${entryLabel(entry)}"? TeamSnap will keep sending this event. Hiding removes it from the calendar for you and for parents.`;
    if (askConfirm) askConfirm(msg, run, { yesLabel: 'HIDE' });
    else run();
  };

  // The item being edited, looked up fresh out of the model so an external
  // write (a rename, a squad pick) is reflected.
  const editingItem = useMemo(() => {
    if (!sheet || sheet.mode !== 'edit' || !sheet.scheduleId) return null;
    for (const list of model.days.values()) {
      for (const e of list) if (e.scheduleId === sheet.scheduleId) return e.raw || null;
    }
    return null;
  }, [model, sheet]);

  // Memoise the form seed on a *value* signature plus a nonce — never on the
  // schedule array. In production one team-doc onSnapshot sets roster, weights
  // AND schedule together, so a roster write hands back a brand-new schedule
  // array; keying on it would re-seed and wipe the form mid-typing.
  const formSig = editingItem ? JSON.stringify([
    editingItem.type, editingItem.title,
    editingItem.opponent, editingItem.date, editingItem.time, editingItem.tournament,
    editingItem.gameType,
    editingItem.location, editingItem.field, editingItem.isHome, editingItem.format,
    editingItem.halfLengthMin, editingItem.homeColor, editingItem.awayColor,
    editingItem.squadIds,
  ]) : '';
  const seedSig = sheet && sheet.mode === 'new' ? JSON.stringify(sheet.seed || {}) : '';
  const formInitial = useMemo(() => {
    if (editingItem) {
      return {
        // Absent `type` means game: every item written before the field existed.
        type: editingItem.type || 'game',
        title: editingItem.title || '',
        opponent: editingItem.opponent || '',
        date: editingItem.date || '',
        time: editingItem.time || '',
        tournament: editingItem.tournament || '',
        gameType: editingItem.gameType || '',
        location: editingItem.location || '',
        field: editingItem.field || '',
        isHome: editingItem.isHome,
        format: editingItem.format,
        halfLengthMin: typeof editingItem.halfLengthMin === 'number' ? editingItem.halfLengthMin : undefined,
        homeColor: editingItem.homeColor,
        awayColor: editingItem.awayColor,
        squadIds: Array.isArray(editingItem.squadIds) ? editingItem.squadIds : [],
      };
    }
    const seed = (sheet && sheet.seed) || {};
    return {
      type: seed.type || 'game',
      title: seed.title || '',
      opponent: seed.opponent || '',
      date: seed.date || selected || today,
      time: seed.time || '',
      tournament: seed.tournament || '',
      gameType: seed.gameType || '',
      location: seed.location || '',
      field: seed.field || '',
      squadIds: [],
      nonce: formNonce,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheet?.mode, sheet?.scheduleId, formSig, seedSig, formNonce]);

  const handleSubmit = (values) => {
    onSaveEntry?.(values, sheet?.mode === 'edit' ? sheet.scheduleId : null);
    closeSheet();
  };

  // GameForm hands out the LIVE values so the caller can persist what the coach
  // has typed *before* the squad-picker detour navigates away. Reading
  // `formInitial` here instead would silently discard every edit since the last
  // save — which is the exact thing the detour exists to avoid.
  const handleEditSquad = (values) => {
    if (!sheet || sheet.mode !== 'edit' || !sheet.scheduleId) return;
    onSaveEntry?.(values, sheet.scheduleId);
    onEditSquad?.({
      id: sheet.scheduleId,
      opponent: values.opponent || 'Opponent',
      squadIds: values.squadIds,
    });
  };

  const legend = [
    { label: 'Game', color: ENTRY_COLORS.game_scheduled },
    { label: 'Won', color: ENTRY_COLORS.won },
    { label: 'Lost', color: ENTRY_COLORS.lost },
    { label: 'Drawn', color: ENTRY_COLORS.drawn },
    { label: 'Practice', color: ENTRY_COLORS.practice },
    { label: 'Tryout', color: ENTRY_COLORS.tryout },
    { label: 'Team event', color: ENTRY_COLORS.team_event },
  ];

  // Two mounts, two shapes. The dugout routes here as a full screen (onBack
  // gives it a Header), so it owns the viewport and paints its own surface.
  // The parent view embeds it inline beneath the tile grid, where a
  // viewport-tall lighter panel would look like a rendering fault. Decide it
  // here rather than leaving callers to override the root from outside.
  const rootClass = onBack
    ? 'min-h-screen bg-stone-900 pb-8'
    : 'bg-transparent pb-2';

  return (
    <div className={rootClass}>
      {/* One <defs> for the whole view. Per-cell patterns would redefine the
          same six fills on every render of every day of the month. */}
      <svg width="0" height="0" className="absolute" aria-hidden="true">
        <defs>
          {Object.keys(CANCELLED_FILL).map((kind) => (
            <React.Fragment key={kind}>
              {/* 6px tile / 0.8px strokes for the grid bars. */}
              <pattern id={calHatchId(kind)} width="6" height="6" patternUnits="userSpaceOnUse">
                <rect width="6" height="6" fill={CANCELLED_FILL[kind]} />
                <path d="M0,0 L6,6 M6,0 L0,6" stroke={CANCELLED_STROKE[kind]} strokeWidth="0.8" />
              </pattern>
              {/* 8px tile / 1.0px strokes for the larger detail-row badge. */}
              <pattern id={`${calHatchId(kind)}-detail`} width="8" height="8" patternUnits="userSpaceOnUse">
                <rect width="8" height="8" fill={CANCELLED_FILL[kind]} />
                <path d="M0,0 L8,8 M8,0 L0,8" stroke={CANCELLED_STROKE[kind]} strokeWidth="1" />
              </pattern>
            </React.Fragment>
          ))}
        </defs>
      </svg>

      {onBack ? (
        <Header
          title="CALENDAR"
          onBack={onBack}
          right={canEdit ? (
            <div className="flex items-center gap-2">
              {/* The only route to a game that is not already on the calendar.
                  The dugout's big START GAME button is gone, and the
                  tap-an-empty-day gesture only works on days with no events at
                  all — so without this an unplanned scrimmage could not be
                  started. Opens on the selected day. */}
              <button
                onClick={() => openSheet({ mode: 'new', scheduleId: null, seed: { date: selected || today } })}
                className="text-xs font-display text-lime-400 hover:text-lime-300 px-2 py-1 rounded-lg border border-lime-700/60 bg-lime-500/10"
              >
                + EVENT
              </button>
              {onManageOpponents && (
                <button
                  onClick={onManageOpponents}
                  className="text-xs font-display text-stone-400 hover:text-stone-100 px-2 py-1 rounded-lg border border-stone-800"
                >
                  🏷️
                </button>
              )}
            </div>
          ) : null}
        />
      ) : (
        <div className="px-4 pt-4 flex items-center justify-between gap-2">
          <h2 className="font-display text-2xl">CALENDAR</h2>
          {canEdit && onManageOpponents && (
            <button
              onClick={onManageOpponents}
              className="text-xs font-display text-stone-400 hover:text-stone-100 px-2 py-1 rounded-lg border border-stone-800"
            >
              🏷️ OPPONENTS
            </button>
          )}
        </div>
      )}

      {/* Month stepper + freshness. `Synced Nm ago` is not decoration:
          TeamSnap's CDN caches the feed for hours, so the calendar can
          legitimately be behind what the coach sees in TeamSnap. */}
      <div className="px-4 pt-4">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setMonth((m) => calShiftMonth(m, -1))}
              className="w-8 h-8 rounded-full bg-stone-800 text-stone-300 flex items-center justify-center active:scale-90 transition"
              title="Previous month"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <div className="font-display text-lg px-1 min-w-[9.5rem] text-center">{calMonthLabel(month)}</div>
            <button
              onClick={() => setMonth((m) => calShiftMonth(m, 1))}
              className="w-8 h-8 rounded-full bg-stone-800 text-stone-300 flex items-center justify-center active:scale-90 transition"
              title="Next month"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
          {syncedLabel && <div className="text-[11px] text-stone-500 shrink-0">{syncedLabel}</div>}
        </div>

        {canEdit && (
          <button
            onClick={() => openSheet({ mode: 'new', scheduleId: null, seed: { date: selected || today } })}
            className="w-full mb-2 bg-stone-900 border border-stone-800 text-lime-400 font-display text-base py-2.5 rounded-xl active:scale-[0.99] transition"
          >
            + ADD TO THIS DAY
          </button>
        )}

        {/* Hidden entries. Only shown when there are some — and it has to exist,
            because a hidden entry is by definition absent from the grid, so this
            panel is the coach's only way to see or undo what they hid. */}
        {canEdit && hiddenEntries.length > 0 && (
          <div className="mb-2">
            <button
              onClick={() => setShowHidden((v) => !v)}
              className="w-full flex items-center justify-between text-xs font-display text-stone-400 hover:text-stone-100 px-3 py-2 rounded-xl border border-stone-800 bg-stone-900"
            >
              <span>HIDDEN ({hiddenEntries.length})</span>
              <span>{showHidden ? 'HIDE ▲' : 'SHOW ▼'}</span>
            </button>
            {showHidden && (
              <div className="mt-2 bg-stone-900 border border-stone-800 rounded-xl p-3 space-y-2">
                <p className="text-[11px] text-stone-400 leading-snug">
                  These still come from TeamSnap — they are just not shown to you or to parents.
                </p>
                {hiddenEntries.map((entry) => (
                  <div key={`hidden:${entry.key}`} className="flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="font-bold text-sm truncate">{entryLabel(entry)}</div>
                      <div className="text-xs text-stone-500 truncate">
                        {[
                          entry.date ? new Date(entry.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' }) : '',
                          entry.time ? formatTime12(entry.time) : (entry.allDay ? 'All day' : ''),
                        ].filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    <button
                      onClick={() => handleUnhide(entry)}
                      className="px-3 py-1.5 bg-stone-800 text-lime-300 border border-lime-500/40 font-display text-xs rounded-lg active:scale-95 transition shrink-0"
                    >
                      RESTORE
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <CalendarMonthGrid
          model={model}
          month={month}
          selected={selected}
          today={today}
          onSelect={handleDaySelect}
        />
      </div>

      {/* Colour guide, directly under the grid and always open. It was collapsed
          behind a SHOW/HIDE toggle on the theory that it teaches the grid once —
          but the coach asked for it visible, and a legend you have to go looking
          for is a legend nobody reads. */}
      <div className="px-4 pt-3">
        <div className="bg-stone-900 border border-stone-800 rounded-xl p-3 flex flex-wrap gap-x-4 gap-y-2">
          {legend.map((l) => (
            <span key={l.label} className="flex items-center gap-1.5 text-[11px] text-stone-300">
              <span className="w-3 h-1.5 rounded-[2px]" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
          <span className="flex items-center gap-1.5 text-[11px] text-stone-300">
            <svg width="12" height="6" aria-hidden="true">
              <rect width="12" height="6" rx="1.5" fill={`url(#${calHatchId('practice')})`} />
            </svg>
            Cancelled
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-stone-300">
            <span className="w-3 h-1.5 rounded-[2px] border border-dashed border-stone-600" />
            Off day (no bar)
          </span>
        </div>
      </div>

      {/* Selected day */}
      <div className="px-4 pt-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-display text-xl">{calDayLabel(selected)}</h3>
          <div className="text-xs text-stone-400 font-semibold">
            {selectedEntries.length} {selectedEntries.length === 1 ? 'event' : 'events'}
          </div>
        </div>
        <CalendarDayRows
          entries={selectedEntries}
          canEdit={canEdit}
          activeGame={activeGame}
          onOpenGame={onOpenGame}
          onStartGame={onStartGame}
          onEditEntry={handleEditEntry}
          onDeleteEntry={handleDeleteEntry}
          onToggleCancelEntry={handleToggleCancelEntry}
          onScheduleFrom={handleScheduleFrom}
          onHideEntry={handleHideEntry}
        />
      </div>

      {/* Next up — across month boundaries on purpose. */}
      {showAgenda && agenda.length > 0 && (
        <div className="px-4 pt-6">
          <h3 className="font-display text-xl mb-3">NEXT UP</h3>
          <div className="space-y-2">
            {agenda.map((entry) => {
              const color = calBarColor(entry);
              return (
                <button
                  key={`agenda:${entry.key}`}
                  onClick={() => {
                    setMonth(calMonthOf(entry.date));
                    setSelected(entry.date);
                  }}
                  className="w-full bg-stone-900 border border-stone-800 rounded-xl p-3 flex items-center gap-3 active:scale-[0.99] transition text-left"
                >
                  <span
                    className="w-1.5 h-8 rounded-sm shrink-0"
                    style={{ background: color || 'transparent', border: color ? 'none' : '1px dashed #57534e' }}
                  />
                  <div className="w-11 shrink-0 text-center">
                    <div className="text-[10px] font-bold text-stone-500 tracking-widest leading-none">
                      {new Date(entry.date + 'T12:00').toLocaleDateString('en', { month: 'short' }).toUpperCase()}
                    </div>
                    <div className="font-display text-lg leading-none tabular-nums">
                      {new Date(entry.date + 'T12:00').getDate()}
                    </div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className={`font-bold text-sm truncate ${entry.cancelled ? 'line-through text-stone-400' : ''}`}>
                      {entry.kind === 'off' ? 'No practice' : entryLabel(entry)}
                    </div>
                    <div className="text-xs text-stone-400 truncate">
                      {[entry.time ? formatTime12(entry.time) : (entry.allDay ? 'All day' : ''), entry.venue || entry.field]
                        .filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-stone-400 shrink-0" />
                </button>
              );
            })}
          </div>
        </div>
      )}


      {/* Edit / add sheet — the shared GameForm. */}
      {sheet && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4" onClick={closeSheet}>
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-stone-950 border-t-2 sm:border-2 border-stone-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md max-h-[92vh] overflow-y-auto"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-stone-800 sticky top-0 bg-stone-950">
              {/* Neutral wording: the form's own heading names the type, and it
                  changes live as the coach taps the picker. */}
              <div className="font-display text-lg">
                {sheet.mode === 'edit' ? 'EDIT EVENT' : 'ADD EVENT'}
              </div>
              <button onClick={closeSheet} className="text-stone-400 hover:text-stone-100 text-2xl leading-none px-2">×</button>
            </div>
            <div className="p-4">
              <GameForm
                initial={formInitial}
                opponentSuggestions={opponentSuggestions}
                editing={sheet.mode === 'edit'}
                onSubmit={handleSubmit}
                onCancel={closeSheet}
                onEditSquad={handleEditSquad}
                showToast={showToast}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- GAME FORM (mounted by the calendar's edit sheet) ---------- */
// The four things a coach can put on a day. `game` first and default, because
// every schedule item written before the type field existed is one — the merge
// reads a missing `type` as 'game' for exactly that reason.
const ENTRY_TYPES = [
  { key: 'game', short: 'GAME', label: 'GAME' },
  { key: 'practice', short: 'PRACTICE', label: 'PRACTICE' },
  { key: 'tryout', short: 'TRYOUT', label: 'TRYOUT' },
  { key: 'team_event', short: 'EVENT', label: 'TEAM EVENT' },
];
const ENTRY_TYPE_LABEL = ENTRY_TYPES.reduce((m, t) => { m[t.key] = t.label; return m; }, {});

// Extracted from the old schedule screen so the calendar's sheet mounts the *same*
// inputs rather than a copy of them. Owns all its input state; the caller owns
// persistence and decides what `initial` is. `type` decides which inputs exist.
function GameForm({ initial, opponentSuggestions = [], editing, onSubmit, onCancel, onEditSquad, showToast }) {
  const init = initial || {};
  // A day holds more than games. Everything but `game` is a coach-owned
  // practice/tryout/team event with no opponent, so `type` gates which inputs
  // exist at all — see ENTRY_TYPES below.
  const [type, setType] = useState(init.type || 'game');
  const [title, setTitle] = useState(init.title || '');
  const [opponent, setOpponent] = useState(init.opponent || '');
  const [date, setDate] = useState(init.date || '');
  const [time, setTime] = useState(init.time || '');
  const [tournament, setTournament] = useState(init.tournament || '');
  // Seed from the RESOLVER, not the raw field: editing a legacy game whose only
  // record of its type is the old free-text "Festival" must preselect FESTIVAL,
  // or saving it would silently reweight it to a full-value league game.
  const [gameType, setGameType] = useState(gameTypeOf(init));
  const [location, setLocation] = useState(init.location || '');
  const [field, setField] = useState(init.field || '');
  // Optional match-day pre-fill — saved on the schedule item, used when the
  // coach taps START on match day to skip setup screens.
  const [isHome, setIsHome] = useState(typeof init.isHome === 'boolean' ? init.isHome : true);
  const [format, setFormat] = useState(FORMATS[init.format] ? init.format : DEFAULT_FORMAT);
  const [halfLengthMin, setHalfLengthMin] = useState(
    typeof init.halfLengthMin === 'number' ? init.halfLengthMin : FORMATS[FORMATS[init.format] ? init.format : DEFAULT_FORMAT].halfMin
  );
  // Same suggest-but-never-override rule as GameSetup: flipping format moves the
  // clock only while the coach hasn't set it themselves.
  const [halfLenTouched, setHalfLenTouched] = useState(typeof init.halfLengthMin === 'number');
  const pickFormat = (next) => {
    setFormat(next);
    if (!halfLenTouched) setHalfLengthMin(FORMATS[next].halfMin);
  };
  const setHalfLenManually = (next) => {
    setHalfLenTouched(true);
    setHalfLengthMin(next);
  };
  const [homeColor, setHomeColor] = useState(init.homeColor || '#0a0a0a');
  const [awayColor, setAwayColor] = useState(init.awayColor || '#dc2626');
  const [squadIds, setSquadIds] = useState(Array.isArray(init.squadIds) ? init.squadIds : []);
  const [showSetup, setShowSetup] = useState(false);
  const isLightColor = (hex) => {
    try {
      const h = (hex || '').replace('#', '');
      const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      return (0.299 * r + 0.587 * g + 0.114 * b) > 160;
    } catch { return false; }
  };

  // Re-seed every field when the caller hands us a different game (or resets to
  // a blank form), so reopening the sheet for another day shows that day.
  React.useEffect(() => {
    const it = initial || {};
    setType(it.type || 'game');
    setTitle(it.title || '');
    setOpponent(it.opponent || '');
    setDate(it.date || '');
    setTime(it.time || '');
    setTournament(it.tournament || '');
    setGameType(gameTypeOf(it));
    setLocation(it.location || '');
    setField(it.field || '');
    setIsHome(typeof it.isHome === 'boolean' ? it.isHome : true);
    const fmt = FORMATS[it.format] ? it.format : DEFAULT_FORMAT;
    setFormat(fmt);
    setHalfLengthMin(typeof it.halfLengthMin === 'number' ? it.halfLengthMin : FORMATS[fmt].halfMin);
    setHalfLenTouched(typeof it.halfLengthMin === 'number');
    setHomeColor(it.homeColor || '#0a0a0a');
    setAwayColor(it.awayColor || '#dc2626');
    setSquadIds(Array.isArray(it.squadIds) ? it.squadIds : []);
    setShowSetup(
      typeof it.isHome === 'boolean' ||
      typeof it.halfLengthMin === 'number' ||
      !!it.homeColor ||
      !!it.awayColor ||
      (Array.isArray(it.squadIds) && it.squadIds.length > 0)
    );
  }, [initial]);

  const isGame = type === 'game';

  const values = () => ({
    type,
    title: title.trim(),
    opponent: opponent.trim(),
    date,
    time: time || '',
    tournament: tournament.trim(),
    gameType,
    location: location.trim(),
    field: field.trim(),
    isHome,
    format,
    halfLengthMin,
    homeColor,
    awayColor,
    squadIds: Array.isArray(squadIds) ? squadIds : [],
  });

  // A game is nothing without an opponent; a practice is fine with just a date,
  // so the guard has to move with the type or the SAVE button never enables.
  const canSubmit = isGame ? !!(opponent.trim() && date) : !!date;

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit?.(values());
  };

  return (
    <div className={`bg-stone-900 border rounded-2xl p-4 space-y-3 ${editing ? 'border-amber-500/60 ring-1 ring-amber-500/30' : 'border-stone-800'}`}>
      <div className="font-display text-lg flex items-center gap-2">
        {editing ? <><span>✏️</span><span>EDIT {ENTRY_TYPE_LABEL[type]}</span></> : <span>ADD {ENTRY_TYPE_LABEL[type]}</span>}
      </div>

      {/* What kind of event this is. Editing an existing item keeps its type:
          a practice cannot become a game, because the two carry different
          fields and switching would leave the saved item half-populated. */}
      <div>
        <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">TYPE</div>
        <div className="grid grid-cols-4 gap-1.5">
          {ENTRY_TYPES.map((t) => (
            <button
              key={t.key}
              type="button"
              disabled={editing && t.key !== type}
              onClick={() => setType(t.key)}
              className={`py-2 rounded-xl text-[11px] font-bold border-2 active:scale-95 transition disabled:opacity-30 ${
                type === t.key ? 'bg-lime-500 text-stone-950 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'
              }`}
            >{t.short}</button>
          ))}
        </div>
      </div>

      {isGame ? (
        <>
          <input
            type="text"
            placeholder="Opponent *"
            value={opponent}
            onChange={e => setOpponent(e.target.value)}
            list="opponent-suggestions"
            autoComplete="off"
            className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
          />
          {opponentSuggestions.length > 0 && (
            <datalist id="opponent-suggestions">
              {opponentSuggestions.map((n) => <option key={n} value={n} />)}
            </datalist>
          )}
        </>
      ) : (
        <input
          type="text"
          placeholder="e.g. Extra keeper session"
          value={title}
          onChange={e => setTitle(e.target.value)}
          className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
        />
      )}
      <div className="grid grid-cols-2 gap-3">
        <label className="relative block">
          <span className="text-xs font-semibold text-stone-400 mb-1 block">DATE *</span>
          <div className="w-full border border-stone-700 bg-stone-900 rounded-xl px-3 py-2.5 text-sm min-h-[42px] flex items-center">
            {date ? new Date(date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' }) : <span className="text-stone-400">Select date</span>}
          </div>
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
          />
        </label>
        <label className="relative block">
          <span className="text-xs font-semibold text-stone-400 mb-1 block">TIME</span>
          <div className="w-full border border-stone-700 bg-stone-900 rounded-xl px-3 py-2.5 text-sm min-h-[42px] flex items-center">
            {time ? formatTime12(time) : <span className="text-stone-400">Select time</span>}
          </div>
          <input
            type="time"
            value={time}
            onChange={e => setTime(e.target.value)}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
          />
        </label>
      </div>
      {/* Tag + name, on EVERY event type. The tag is a fixed vocabulary because
          it picks a scoring weight — typing "Scrimmage vs Caboto" into a text
          box used to score at full league value. The name stays free text
          beside it, and a practice can carry both: the session at the Canton
          Cup. */}
      <div>
        <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">TAG</div>
        <div className="grid grid-cols-4 gap-1.5">
          {GAME_TYPES.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setGameType(t.key)}
              className={`py-2 rounded-xl text-[10px] font-bold border-2 active:scale-95 transition ${
                gameType === t.key ? 'bg-lime-500 text-stone-950 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'
              }`}
            >{t.label}</button>
          ))}
        </div>
      </div>
      <input
        type="text"
        placeholder="Competition name (e.g. Canton Cup)"
        value={tournament}
        onChange={e => setTournament(e.target.value)}
        className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
      />
      <input
        type="text"
        placeholder="Location (address or Google Maps link)"
        value={location}
        onChange={e => setLocation(e.target.value)}
        className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
      />
      <input
        type="text"
        placeholder="Field # / name (e.g., Field 4)"
        value={field}
        onChange={e => setField(e.target.value)}
        className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
      />

      {/* ---- Optional match-day pre-fill (squad, home/away, half, colors) ----
          Games only: every control in here — side, format, half length, both
          kit colours, squad — describes a fixture, and none of it is saved for
          a practice. */}
      {isGame && (
      <button
        type="button"
        onClick={() => setShowSetup(s => !s)}
        className="w-full flex items-center justify-between bg-stone-950 border border-stone-800 rounded-xl px-3 py-2.5 text-sm text-stone-200 active:scale-[0.99]"
      >
        <span className="font-semibold tracking-wide">
          ⚙️ MATCH-DAY SETUP <span className="text-stone-500 font-normal">(optional)</span>
        </span>
        <span className="text-stone-400 text-xs">{showSetup ? 'HIDE ▲' : 'SHOW ▼'}</span>
      </button>
      )}
      {isGame && showSetup && (
        <div className="space-y-3 bg-stone-950/60 border border-stone-800 rounded-xl p-3">
          <p className="text-[11px] text-stone-400 leading-snug">
            Pre-fill what you know now. If you set <span className="font-bold text-stone-200">everything</span> (squad + home/away + half length + both colors), tapping START on match day jumps straight to the starting lineup — no setup screens.
          </p>

          {/* Home / Away */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">SIDE</div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setIsHome(true)}
                className={`flex-1 py-2.5 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${isHome ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
              >🏠 HOME</button>
              <button
                type="button"
                onClick={() => setIsHome(false)}
                className={`flex-1 py-2.5 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${!isHome ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
              >✈️ AWAY</button>
            </div>
          </div>

          {/* Format — 7v7 (Canada) vs 9v9 (US tournaments) */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">FORMAT</div>
            <div className="flex gap-2">
              {Object.values(FORMATS).map(f => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => pickFormat(f.key)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-bold border-2 active:scale-95 transition ${format === f.key ? 'bg-lime-500/15 text-lime-300 border-lime-400' : 'bg-stone-900 text-stone-400 border-stone-800'}`}
                >{f.key.toUpperCase()}</button>
              ))}
            </div>
          </div>

          {/* Half length */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">HALF LENGTH (MIN)</div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setHalfLenManually(Math.max(1, halfLengthMin - 1))}
                className="w-11 h-11 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-xl font-bold active:scale-95 transition"
              >−</button>
              <div className="flex-1 py-2 rounded-xl bg-stone-900 border-2 border-stone-800 text-center">
                <div className="font-display text-2xl text-stone-100 tabular-nums leading-none">{halfLengthMin}</div>
              </div>
              <button
                type="button"
                onClick={() => setHalfLenManually(Math.min(99, halfLengthMin + 1))}
                className="w-11 h-11 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-xl font-bold active:scale-95 transition"
              >+</button>
            </div>
            {!halfLenTouched && (
              <div className="text-[10px] text-stone-500 mt-1">Suggested for {format.toUpperCase()}</div>
            )}
          </div>

          {/* Stompers jersey */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">LASALLE STOMPERS JERSEY</div>
            <div className="flex gap-2 items-center">
              {[{ label: 'Black', color: '#0a0a0a' }, { label: 'Green', color: '#16a34a' }].map(p => (
                <button
                  key={p.color}
                  type="button"
                  onClick={() => setHomeColor(p.color)}
                  className={`flex-1 py-2.5 rounded-xl font-bold text-xs border-2 active:scale-95 transition ${homeColor === p.color ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                  style={{ background: p.color, color: '#fff' }}
                >{p.label.toUpperCase()}</button>
              ))}
              <label
                className={`flex-1 relative py-2.5 rounded-xl font-bold text-xs border-2 active:scale-95 transition cursor-pointer flex items-center justify-center overflow-hidden ${!['#0a0a0a', '#16a34a'].includes(homeColor) ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                style={{ background: homeColor, color: isLightColor(homeColor) ? '#0a0a0a' : '#fff' }}
              >
                🎨 CUSTOM
                <input type="color" value={homeColor} onChange={e => setHomeColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
              </label>
            </div>
          </div>

          {/* Opponent jersey */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">OPPONENT JERSEY</div>
            <div className="flex gap-2 items-center">
              {[
                { label: 'Red', color: '#dc2626' },
                { label: 'Blue', color: '#2563eb' },
                { label: 'White', color: '#f5f5f4' },
              ].map(p => (
                <button
                  key={p.color}
                  type="button"
                  onClick={() => setAwayColor(p.color)}
                  className={`flex-1 py-2.5 rounded-xl font-bold text-xs border-2 active:scale-95 transition ${awayColor === p.color ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                  style={{ background: p.color, color: p.color === '#f5f5f4' ? '#0a0a0a' : '#fff' }}
                >{p.label.toUpperCase()}</button>
              ))}
              <label
                className={`flex-1 relative py-2.5 rounded-xl font-bold text-xs border-2 active:scale-95 transition cursor-pointer flex items-center justify-center overflow-hidden ${!['#dc2626', '#2563eb', '#f5f5f4'].includes(awayColor) ? 'border-lime-400 ring-2 ring-lime-400/40' : 'border-stone-800'}`}
                style={{ background: awayColor, color: isLightColor(awayColor) ? '#0a0a0a' : '#fff' }}
              >
                🎨 CUSTOM
                <input type="color" value={awayColor} onChange={e => setAwayColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
              </label>
            </div>
          </div>

          {/* Squad */}
          <div>
            <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">SQUAD (AVAILABLE PLAYERS)</div>
            <button
              type="button"
              onClick={() => {
                if (!editing) {
                  showToast?.('💡 Save the game first, then edit its squad.');
                  return;
                }
                // The caller persists this form state before navigating away to
                // the squad picker, so the in-progress edit isn't lost.
                onEditSquad?.(values());
              }}
              className={`w-full flex items-center justify-between gap-3 px-3 py-3 rounded-xl border-2 active:scale-[0.99] transition ${
                Array.isArray(squadIds) && squadIds.length > 0
                  ? 'bg-lime-500/10 border-lime-400 text-stone-100'
                  : 'bg-stone-900 border-stone-800 text-stone-300'
              }`}
            >
              <span className="flex items-center gap-2">
                <span className="text-xl">👥</span>
                <span className="font-bold text-sm">
                  {Array.isArray(squadIds) && squadIds.length > 0
                    ? `${squadIds.length} player${squadIds.length === 1 ? '' : 's'} picked`
                    : (editing ? 'Tap to pick squad' : 'Save game first, then pick squad')}
                </span>
              </span>
              <span className="text-xs text-stone-400">{editing ? 'EDIT →' : ''}</span>
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        {editing && (
          <button
            onClick={onCancel}
            className="flex-1 bg-stone-900 text-stone-300 border border-stone-700 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
          >
            CANCEL
          </button>
        )}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className={`flex-1 font-display text-lg py-3 rounded-xl disabled:opacity-40 active:scale-[0.98] transition ${editing ? 'bg-amber-500 text-stone-950 border-2 border-amber-400' : 'bg-stone-900 text-lime-400'}`}
        >
          {editing ? 'SAVE CHANGES' : 'ADD TO SCHEDULE'}
        </button>
      </div>
    </div>
  );
}
function OpponentManagerModal({ opponentSuggestions, games, schedule, onClose, onRename, askConfirm, showToast }) {
  const [renaming, setRenaming] = useState(null); // { from: string, to: string }
  const norm = (s) => (s || '').trim().toLowerCase();
  // Count usages per opponent so the coach can see how many docs a rename
  // (or how much demo data) is involved.
  const counts = useMemo(() => {
    const m = new Map();
    const bump = (k, slot) => {
      if (!k) return;
      const e = m.get(k) || { games: 0, schedule: 0 };
      e[slot] += 1;
      m.set(k, e);
    };
    for (const g of games) bump(norm(g.opponent), 'games');
    for (const s of schedule) bump(norm(s.opponent), 'schedule');
    return m;
  }, [games, schedule]);
  const sorted = [...opponentSuggestions].sort((a, b) => a.localeCompare(b));

  const doRename = async () => {
    const from = renaming?.from;
    const to = (renaming?.to || '').trim();
    if (!from || !to) return;
    if (norm(from) === norm(to)) { setRenaming(null); return; }
    const exists = opponentSuggestions.some((n) => norm(n) === norm(to));
    const apply = async () => {
      try {
        const n = await onRename(from, to);
        showToast?.(n > 0 ? `Renamed ${n} entr${n === 1 ? 'y' : 'ies'} → "${to}"` : 'Nothing to rename');
      } catch (e) {
        showToast?.('Rename failed');
      }
      setRenaming(null);
    };
    if (exists) {
      askConfirm?.(
        `"${to}" already exists. Merge "${from}" into it? This updates all games and schedule entries.`,
        apply,
      );
    } else {
      apply();
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="bg-stone-900 border border-stone-800 rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md max-h-[85vh] flex flex-col">
        <div className="px-4 py-3 border-b border-stone-800 flex items-center justify-between">
          <div className="font-display text-lg">MANAGE OPPONENTS</div>
          <button onClick={onClose} className="text-stone-400 hover:text-stone-100 text-2xl leading-none px-2">×</button>
        </div>
        <div className="px-4 py-2 text-xs text-stone-400 border-b border-stone-800">
          Rename a team to fix a typo or merge duplicates. Updates every game and schedule entry that uses the old name. To remove a test/demo team, delete the underlying game(s) from the past-games list.
        </div>
        <div className="flex-1 overflow-y-auto divide-y divide-stone-800">
          {sorted.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-stone-500">No opponents yet.</div>
          )}
          {sorted.map((name) => {
            const c = counts.get(norm(name)) || { games: 0, schedule: 0 };
            const isRenaming = renaming?.from === name;
            return (
              <div key={name} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">{name}</div>
                    <div className="text-[11px] text-stone-500 mt-0.5">
                      {c.games > 0 && `${c.games} game${c.games === 1 ? '' : 's'}`}
                      {c.games > 0 && c.schedule > 0 && ' · '}
                      {c.schedule > 0 && `${c.schedule} scheduled`}
                      {c.games === 0 && c.schedule === 0 && 'unused'}
                    </div>
                  </div>
                  {!isRenaming && (
                    <button
                      onClick={() => setRenaming({ from: name, to: name })}
                      className="text-xs font-bold text-lime-400 hover:text-lime-300 px-3 py-1.5 rounded-lg border border-stone-800"
                    >
                      RENAME
                    </button>
                  )}
                </div>
                {isRenaming && (
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="text"
                      value={renaming.to}
                      onChange={(e) => setRenaming({ from: name, to: e.target.value })}
                      autoFocus
                      className="flex-1 bg-stone-950 border border-stone-700 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
                    />
                    <button onClick={doRename} className="text-xs font-bold bg-lime-500 text-stone-900 px-3 py-1.5 rounded-lg">SAVE</button>
                    <button onClick={() => setRenaming(null)} className="text-xs font-bold text-stone-400 px-2 py-1.5">CANCEL</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="px-4 py-3 border-t border-stone-800">
          <button onClick={onClose} className="w-full py-2.5 rounded-xl bg-stone-800 text-stone-200 font-display text-sm">
            DONE
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- VIEWERS PANEL (owner-only usage analytics) ----------
 * Rebuilt 2026-06-11: the old page was designed around live broadcasting
 * ("who's watching now"), which no longer exists. This version answers:
 * which features/sections get used, how much, and by which group — with
 * owner-assigned buckets (coach/parent/player/unknown) per viewer.
 * The owner is excluded from tracking at the source (trackUsage + the
 * shell AuthGate) AND filtered here so legacy rows don't pollute history.
 */

// Friendly labels for the viewerLog action vocabulary. Unknown actions render
// raw so new instrumentation shows up instead of disappearing.
const USAGE_ACTION_META = {
  'login':            { label: 'App opens',       emoji: '🚪', group: 'visits' },
  'public:home':      { label: 'Public home',     emoji: '🏠', group: 'public' },
  'public:game':      { label: 'Game pages',      emoji: '🏟️', group: 'public' },
  'watch_highlights': { label: 'Highlights reel', emoji: '🎬', group: 'video' },
  'watch_full_game':  { label: 'Full-game reel',  emoji: '📺', group: 'video' },
  'watch_360':        { label: '360° video',      emoji: '🌐', group: 'video' },
  'watch_live':       { label: 'Live (legacy)',   emoji: '🔴', group: 'video' },
  'watch_replay':     { label: 'Replay (legacy)', emoji: '▶️', group: 'video' },
};
const COACH_VIEW_LABELS = {
  home: 'Coach home', activeGame: 'Live logging', gameDetail: 'Game detail',
  filmRoom: 'Film room', stats: 'Season stats', weights: 'Scoring weights',
  schedule: 'Schedule', roster: 'Roster', squad: 'Matchday squad',
  lineup: 'Lineup setup', help: 'Help', training: 'Training videos',
  viewers: 'Viewers page',
};
function usageActionMeta(action) {
  if (USAGE_ACTION_META[action]) return USAGE_ACTION_META[action];
  if (action && action.startsWith('coach:')) {
    const v = action.slice(6);
    return { label: COACH_VIEW_LABELS[v] || `Coach: ${v}`, emoji: '📋', group: 'coach' };
  }
  return { label: action || '?', emoji: '•', group: 'other' };
}

const BUCKET_BAR_COLOR = {
  coach: '#fbbf24', parent: '#38bdf8', player: '#a3e635', unknown: '#78716c',
};

/* ---------- MANAGE ACCESS (coach): family approvals ----------
 * Pending accessRequests → approve (with a roster player picker) or deny.
 * Approve = write allowedUsers/{email} {role:'parent', playerIds} then
 * delete the request; the requester's waiting screen flips live off their
 * own-doc listener. Members list shows every allowed account; removing a
 * parent bounces them back to the request screen on their next open.
 * NOTE: writes here need the 2026-08 rules deploy (allowedUsers was
 * console-only before) — until then approvals surface a permission error.
 */
function ManageAccessPanel({ roster, onBack }) {
  const [requests, setRequests] = useState([]);
  const [members, setMembers] = useState([]);
  const [pickerFor, setPickerFor] = useState(null);   // email being approved / edited
  const [picked, setPicked] = useState([]);           // playerIds in the open picker
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb) return undefined;
    const u1 = window.fbDb.collection('accessRequests').onSnapshot(
      (snap) => setRequests(snap.docs.map((d) => ({ ...d.data(), email: d.id })).sort((a, b) => (a.ts || 0) - (b.ts || 0))),
      () => setRequests([])
    );
    const u2 = window.fbDb.collection('allowedUsers').onSnapshot(
      (snap) => setMembers(snap.docs.map((d) => ({ ...d.data(), email: d.id }))),
      () => setMembers([])
    );
    return () => { u1(); u2(); };
  }, []);

  const sortedRoster = [...(roster || [])].sort((a, b) => (parseInt(a.number) || 0) - (parseInt(b.number) || 0));
  const nameOf = (pid) => {
    const p = (roster || []).find((x) => x.id === pid);
    return p ? `${(p.name || '').trim().split(/\s+/)[0]} #${p.number}` : pid;
  };
  const openPicker = (email, initial) => { setPickerFor(email); setPicked(initial || []); setError(null); };
  const togglePick = (pid) => setPicked((cur) => (cur.includes(pid) ? cur.filter((x) => x !== pid) : [...cur, pid]));

  const approve = async (req) => {
    setBusy(true); setError(null);
    try {
      await window.fbDb.collection('allowedUsers').doc(req.email).set({
        role: 'parent',
        name: req.name || '',
        playerIds: picked,
        addedVia: 'request',
        addedAt: Date.now(),
      });
      await window.fbDb.collection('accessRequests').doc(req.email).delete();
      setPickerFor(null); setPicked([]);
    } catch (e) { setError(e && e.message ? e.message : String(e)); }
    setBusy(false);
  };
  const deny = async (req) => {
    if (!window.confirm(`Deny and remove the request from ${req.email}?`)) return;
    setBusy(true); setError(null);
    try { await window.fbDb.collection('accessRequests').doc(req.email).delete(); }
    catch (e) { setError(e && e.message ? e.message : String(e)); }
    setBusy(false);
  };
  const saveKids = async (member) => {
    setBusy(true); setError(null);
    try {
      await window.fbDb.collection('allowedUsers').doc(member.email).set({ playerIds: picked }, { merge: true });
      setPickerFor(null); setPicked([]);
    } catch (e) { setError(e && e.message ? e.message : String(e)); }
    setBusy(false);
  };
  const remove = async (member) => {
    if (!window.confirm(`Remove ${member.email}? They'll be back to "request access" next time they open the app.`)) return;
    setBusy(true); setError(null);
    try { await window.fbDb.collection('allowedUsers').doc(member.email).delete(); }
    catch (e) { setError(e && e.message ? e.message : String(e)); }
    setBusy(false);
  };

  const PlayerPicker = ({ onConfirm, confirmLabel }) => (
    <div className="mt-3 bg-stone-950 border border-stone-800 rounded-xl p-3">
      <div className="text-[10px] text-stone-500 tracking-wider mb-2">LINK TO PLAYER(S) — THEY ONLY EVER SEE THEIR OWN KID'S DATA</div>
      <div className="grid grid-cols-2 gap-1.5">
        {sortedRoster.map((p) => (
          <button
            key={p.id}
            onClick={() => togglePick(p.id)}
            className={`px-2 py-1.5 rounded-lg text-xs font-bold text-left border ${picked.includes(p.id) ? 'bg-lime-500 text-stone-950 border-lime-400' : 'bg-stone-900 text-stone-300 border-stone-700'}`}
          >
            #{p.number} {(p.name || '').trim().split(/\s+/)[0]}
          </button>
        ))}
      </div>
      <div className="flex gap-2 mt-3">
        <button onClick={onConfirm} disabled={busy} className="flex-1 bg-lime-500 text-stone-950 font-display rounded-lg py-2 text-sm disabled:opacity-60">
          {picked.length === 0 ? `${confirmLabel} (NO PLAYER LINK)` : `${confirmLabel} · ${picked.length} PLAYER${picked.length === 1 ? '' : 'S'}`}
        </button>
        <button onClick={() => { setPickerFor(null); setPicked([]); }} className="px-3 bg-stone-800 text-stone-300 rounded-lg text-sm">Cancel</button>
      </div>
    </div>
  );

  const parents = members.filter((m) => m.role !== 'coach').sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email));
  const coaches = members.filter((m) => m.role === 'coach').sort((a, b) => (a.email || '').localeCompare(b.email || ''));

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 pb-12">
      <div className="stripes-bg text-white px-4 pt-[calc(env(safe-area-inset-top,0px)+1rem)] pb-4 flex items-center gap-3">
        <button onClick={onBack} className="bg-white/15 hover:bg-white/25 text-xs font-bold tracking-widest px-3 py-2 rounded-lg flex items-center gap-1">
          <ChevronLeft className="w-4 h-4" /> BACK
        </button>
        <div>
          <div className="font-display text-2xl leading-none">ACCESS</div>
          <div className="text-[11px] text-white/60 mt-0.5">Who can open the family app</div>
        </div>
      </div>

      <div className="px-4 pt-4 max-w-2xl mx-auto space-y-6">
        {error && <div className="bg-red-950/50 border border-red-800 text-red-200 text-xs rounded-xl px-3 py-2">{error}</div>}

        <div>
          <h3 className="font-display text-lg text-stone-200 mb-2">REQUESTS {requests.length > 0 && <span className="text-lime-400">({requests.length})</span>}</h3>
          {requests.length === 0 ? (
            <div className="text-stone-500 text-sm bg-stone-900 border border-stone-800 rounded-xl px-3 py-4 text-center">No pending requests.</div>
          ) : (
            <div className="space-y-2">
              {requests.map((req) => (
                <div key={req.email} className="bg-stone-900 border border-stone-800 rounded-xl p-3">
                  <div className="flex items-center gap-3">
                    {req.photo && <img src={req.photo} className="w-9 h-9 rounded-full" referrerPolicy="no-referrer" />}
                    <div className="flex-1 min-w-0">
                      <div className="font-bold text-sm truncate">{req.name || req.email}</div>
                      <div className="text-xs text-stone-400 truncate">{req.email}</div>
                      {req.note && <div className="text-xs text-lime-300/80 mt-0.5 truncate">"{req.note}"</div>}
                    </div>
                    {pickerFor !== req.email && (
                      <>
                        <button onClick={() => openPicker(req.email, [])} className="bg-lime-500 text-stone-950 font-display text-xs px-3 py-2 rounded-lg">APPROVE</button>
                        <button onClick={() => deny(req)} className="bg-stone-800 text-stone-300 text-xs px-3 py-2 rounded-lg">Deny</button>
                      </>
                    )}
                  </div>
                  {pickerFor === req.email && <PlayerPicker onConfirm={() => approve(req)} confirmLabel="APPROVE" />}
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <h3 className="font-display text-lg text-stone-200 mb-2">FAMILIES ({parents.length})</h3>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
            {parents.map((m) => (
              <div key={m.email} className="p-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">
                      {m.name || m.email}
                      {m.relationship && <span className="text-stone-500 font-normal"> · {m.relationship}</span>}
                    </div>
                    <div className="text-xs text-stone-400 truncate">{m.email}</div>
                    <div className="text-xs text-lime-300/80 mt-0.5 truncate">
                      {(m.playerIds || []).length ? (m.playerIds || []).map(nameOf).join(', ') : 'No player link'}
                    </div>
                  </div>
                  {pickerFor !== m.email && (
                    <>
                      <button onClick={() => openPicker(m.email, m.playerIds || [])} className="bg-stone-800 text-stone-200 text-xs px-2.5 py-2 rounded-lg">Kids</button>
                      <button onClick={() => remove(m)} className="bg-red-950/60 border border-red-900 text-red-300 text-xs px-2.5 py-2 rounded-lg">Remove</button>
                    </>
                  )}
                </div>
                {pickerFor === m.email && <PlayerPicker onConfirm={() => saveKids(m)} confirmLabel="SAVE" />}
              </div>
            ))}
            {parents.length === 0 && <div className="text-stone-500 text-sm px-3 py-4 text-center">No approved families yet.</div>}
          </div>
        </div>

        <div>
          <h3 className="font-display text-lg text-stone-200 mb-2">COACHES</h3>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
            {coaches.map((m) => (
              <div key={m.email} className="p-3 flex items-center gap-3">
                <span className="text-lg">🪑</span>
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">{m.name || m.email}</div>
                  <div className="text-xs text-stone-400 truncate">{m.email}{(m.playerIds || []).length ? ` · ${(m.playerIds || []).map(nameOf).join(', ')}` : ''}</div>
                </div>
                <span className="text-[10px] text-stone-500 tracking-wider">MANAGED BY OWNER</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ViewersPanel({ games = [], onBack }) {
  const [logs, setLogs] = useState([]);
  const [tags, setTags] = useState({});           // email -> explicit buckets
  const [coachEmails, setCoachEmails] = useState(() => new Set());
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState(14); // days
  const [bucketFilter, setBucketFilter] = useState('all');
  const [tab, setTab] = useState('overview');     // overview | people | log

  // viewerLog (range-scoped, live)
  useEffect(() => {
    if (!window.fbDb) { setLoading(false); return; }
    const since = new Date();
    since.setDate(since.getDate() - timeRange);
    const unsub = window.fbDb.collection('viewerLog')
      .where('ts', '>=', since)
      .orderBy('ts', 'desc')
      .limit(1000)
      .onSnapshot((snap) => {
        setLogs(snap.docs.map(d => ({ id: d.id, ...d.data() })));
        setLoading(false);
      }, () => setLoading(false));
    return unsub;
  }, [timeRange]);

  // viewerTags: owner-assigned buckets per email
  useEffect(() => {
    if (!window.fbDb) return undefined;
    const unsub = window.fbDb.collection('viewerTags').onSnapshot((snap) => {
      const m = {};
      snap.docs.forEach(d => { const x = d.data(); if (x.email) m[x.email] = x.buckets || []; });
      setTags(m);
    }, () => {});
    return unsub;
  }, []);

  // allowedUsers: untagged coach-role emails default to the coach bucket
  useEffect(() => {
    if (!window.fbDb) return;
    window.fbDb.collection('allowedUsers').get().then((snap) => {
      setCoachEmails(new Set(snap.docs.filter(d => d.data().role === 'coach').map(d => d.id)));
    }).catch(() => {});
  }, []);

  const bucketsOf = (email) => {
    const explicit = tags[email];
    if (explicit && explicit.length > 0) return explicit;
    if (coachEmails.has(email)) return ['coach'];
    return ['unknown'];
  };
  const toggleBucket = (email, bucket) => {
    if (!window.fbDb) return;
    const cur = new Set(bucketsOf(email).filter(b => b !== 'unknown'));
    if (cur.has(bucket)) cur.delete(bucket); else cur.add(bucket);
    window.fbDb.collection('viewerTags').doc(email).set({
      email,
      buckets: [...cur],
      updatedAt: window.firebase?.firestore?.FieldValue?.serverTimestamp?.() || new Date(),
    }).catch(() => {});
  };

  // --- shared helpers ---
  const tsOf = (l) => (l.ts?.toDate ? l.ts.toDate() : (l.ts ? new Date(l.ts) : null));
  const durSecOf = (l) => {
    if (!l.endTs) return 0;
    const a = tsOf(l);
    const b = l.endTs?.toDate ? l.endTs.toDate() : new Date(l.endTs);
    if (!a || !b) return 0;
    const s = (b.getTime() - a.getTime()) / 1000;
    return s > 0 && s < 6 * 3600 ? s : 0; // ignore broken/stale sessions
  };
  const fmtWatch = (sec) => {
    if (sec < 60) return `${Math.round(sec)}s`;
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    return `${(sec / 3600).toFixed(1)}h`;
  };
  const fmtTs = (ts) => {
    if (!ts) return '';
    const d = ts.toDate ? ts.toDate() : new Date(ts);
    return d.toLocaleString('en', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  };
  const fmtDay = (iso) => new Date(iso + 'T12:00').toLocaleDateString('en', { weekday: 'short', month: 'short', day: 'numeric' });
  const relativeTime = (date) => {
    if (!date) return 'Never';
    const mins = Math.floor((Date.now() - date.getTime()) / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };
  const gameName = (gameId) => {
    const g = (games || []).find(x => x.id === gameId);
    return g ? `vs ${g.opponent}` : null;
  };

  // --- derived data (owner always excluded; legacy rows included) ---
  const allLogs = logs.filter(l => (l.email || '').toLowerCase() !== OWNER_EMAIL);
  const fLogs = bucketFilter === 'all'
    ? allLogs
    : allLogs.filter(l => bucketsOf((l.email || '').toLowerCase()).includes(bucketFilter));

  const uniqueEmails = [...new Set(fLogs.map(l => (l.email || '').toLowerCase()))];
  const visitCount = fLogs.filter(l => l.action === 'login').length;
  const videoLogs = fLogs.filter(l => usageActionMeta(l.action).group === 'video');
  const watchSec = videoLogs.reduce((s, l) => s + durSecOf(l), 0);

  // Per-feature aggregation
  const featMap = {};
  fLogs.forEach(l => {
    const key = l.action || '?';
    if (!featMap[key]) featMap[key] = { action: key, count: 0, people: new Set(), byBucket: { coach: 0, parent: 0, player: 0, unknown: 0 }, watchSec: 0 };
    const f = featMap[key];
    f.count++;
    const em = (l.email || '').toLowerCase();
    f.people.add(em);
    // attribute the event to the viewer's FIRST bucket (primary identity)
    const b = bucketsOf(em)[0];
    f.byBucket[b] = (f.byBucket[b] || 0) + 1;
    f.watchSec += durSecOf(l);
  });
  const features = Object.values(featMap).sort((a, b) => b.count - a.count);
  const featMax = Math.max(1, ...features.map(f => f.count));

  // Per-person aggregation
  const peopleMap = {};
  allLogs.forEach(l => {
    const em = (l.email || '').toLowerCase();
    if (!peopleMap[em]) peopleMap[em] = { email: em, name: l.name, photo: l.photo, visits: 0, video: 0, watchSec: 0, sections: new Set(), lastSeen: null };
    const p = peopleMap[em];
    if (l.name && !p.name) p.name = l.name;
    if (l.photo && !p.photo) p.photo = l.photo;
    if (l.action === 'login') p.visits++;
    const meta = usageActionMeta(l.action);
    if (meta.group === 'video') { p.video++; p.watchSec += durSecOf(l); }
    else if (l.action !== 'login') p.sections.add(meta.label);
    const ts = tsOf(l);
    if (ts && (!p.lastSeen || ts > p.lastSeen)) p.lastSeen = ts;
  });
  const people = Object.values(peopleMap)
    .filter(p => bucketFilter === 'all' || bucketsOf(p.email).includes(bucketFilter))
    .sort((a, b) => (b.lastSeen || 0) - (a.lastSeen || 0));

  // Top games by attention (page visits + video opens carrying a gameId)
  const gameMap = {};
  fLogs.forEach(l => {
    if (!l.gameId) return;
    if (!gameMap[l.gameId]) gameMap[l.gameId] = { gameId: l.gameId, count: 0, watchSec: 0 };
    gameMap[l.gameId].count++;
    gameMap[l.gameId].watchSec += durSecOf(l);
  });
  const topGames = Object.values(gameMap)
    .filter(g => gameName(g.gameId))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  // Daily activity bars
  const dailyCounts = {};
  for (let i = 0; i < timeRange; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    dailyCounts[d.toLocaleDateString('en-CA')] = 0;
  }
  fLogs.forEach(l => {
    const ts = tsOf(l);
    if (!ts) return;
    const key = ts.toLocaleDateString('en-CA');
    if (key in dailyCounts) dailyCounts[key]++;
  });
  const dailyArr = Object.entries(dailyCounts).sort(([a], [b]) => a.localeCompare(b));
  const peakDay = dailyArr.reduce((best, [day, n]) => (n > best.n ? { day, n } : best), { day: '', n: 0 });

  const DayBars = ({ data }) => {
    const maxVal = Math.max(...data.map(([, n]) => n), 1);
    return (
      <div className="flex items-end gap-[2px] h-10">
        {data.map(([day, n]) => (
          <div key={day} className="flex-1" title={`${fmtDay(day)}: ${n} events`}>
            <div
              className={`w-full rounded-sm min-h-[2px] ${day === new Date().toLocaleDateString('en-CA') ? 'bg-lime-400' : 'bg-stone-600'}`}
              style={{ height: `${Math.max((n / maxVal) * 100, 5)}%` }}
            />
          </div>
        ))}
      </div>
    );
  };

  const Avatar = ({ name, email, photo, size = 'w-8 h-8 text-xs' }) => (
    photo
      ? <img src={photo} className={`${size.split(' ').slice(0, 2).join(' ')} rounded-full`} referrerPolicy="no-referrer" />
      : <div className={`${size} rounded-full bg-stone-700 flex items-center justify-center text-stone-400 font-bold`}>{(name || email || '?')[0].toUpperCase()}</div>
  );

  const BucketChips = ({ email, editable }) => (
    <div className="flex flex-wrap gap-1">
      {VIEWER_BUCKETS.map(b => {
        const active = bucketsOf(email).includes(b);
        if (!editable && !active) return null;
        const meta = BUCKET_META[b];
        return (
          <button
            key={b}
            disabled={!editable}
            onClick={() => editable && toggleBucket(email, b)}
            className={`text-[9px] font-extrabold tracking-wider px-1.5 py-0.5 rounded border transition ${active ? meta.chip : 'bg-stone-950 text-stone-600 border-stone-800'} ${editable ? 'active:scale-95' : ''}`}
          >
            {meta.emoji} {meta.label}
          </button>
        );
      })}
    </div>
  );

  const TabBtn = ({ id, label, count }) => (
    <button
      onClick={() => setTab(id)}
      className={`flex-1 px-3 py-1.5 text-xs font-bold tracking-wider rounded-lg transition ${tab === id ? 'bg-stone-800 text-white' : 'text-stone-500 hover:text-stone-300'}`}
    >
      {label}{count != null && <span className="ml-1 text-stone-500">({count})</span>}
    </button>
  );

  return (
    <div className="min-h-screen bg-stone-950 pb-20">
      <Header title="VIEWERS" onBack={onBack} />

      {loading ? (
        <div className="p-6 text-center text-stone-500 animate-pulse">Loading usage analytics…</div>
      ) : (
        <div className="px-4 pt-4 space-y-5 max-w-2xl mx-auto">

          {/* Time range */}
          <div className="flex gap-1.5 justify-center">
            {[7, 14, 30, 90].map(d => (
              <button
                key={d}
                onClick={() => setTimeRange(d)}
                className={`px-3 py-1 text-[10px] font-bold tracking-wider rounded-full border transition ${timeRange === d ? 'bg-lime-500/20 border-lime-500/50 text-lime-400' : 'border-stone-700 text-stone-500 hover:text-stone-300'}`}
              >
                {d}D
              </button>
            ))}
          </div>

          {/* Bucket filter */}
          <div className="flex gap-1.5 justify-center flex-wrap">
            <button
              onClick={() => setBucketFilter('all')}
              className={`text-[10px] font-extrabold tracking-wider px-2 py-1 rounded-full border transition ${bucketFilter === 'all' ? 'bg-white/10 border-stone-400 text-white' : 'border-stone-700 text-stone-500'}`}
            >
              ALL
            </button>
            {VIEWER_BUCKETS.map(b => (
              <button
                key={b}
                onClick={() => setBucketFilter(bucketFilter === b ? 'all' : b)}
                className={`text-[10px] font-extrabold tracking-wider px-2 py-1 rounded-full border transition ${bucketFilter === b ? BUCKET_META[b].chip : 'border-stone-700 text-stone-500'}`}
              >
                {BUCKET_META[b].emoji} {BUCKET_META[b].label}S
              </button>
            ))}
          </div>

          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-white">{uniqueEmails.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">VIEWERS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-white">{visitCount}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">APP OPENS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-sky-400">{videoLogs.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">VIDEO VIEWS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-lime-400">{fmtWatch(watchSec)}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">WATCH TIME</div>
            </div>
          </div>

          {/* Daily activity */}
          {dailyArr.length > 1 && (
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] text-stone-500 font-bold tracking-wider">DAILY ACTIVITY</span>
                {peakDay.n > 0 && (
                  <span className="text-[10px] text-stone-500">Peak: <span className="text-white font-bold">{peakDay.n}</span> on {fmtDay(peakDay.day)}</span>
                )}
              </div>
              <DayBars data={dailyArr} />
              <div className="flex justify-between mt-1.5">
                <span className="text-[9px] text-stone-600">{fmtDay(dailyArr[0]?.[0])}</span>
                <span className="text-[9px] text-stone-600">Today</span>
              </div>
            </div>
          )}

          {/* Tabs */}
          <div className="flex gap-1 bg-stone-900/50 rounded-lg p-1">
            <TabBtn id="overview" label="FEATURES" />
            <TabBtn id="people" label="PEOPLE" count={people.length} />
            <TabBtn id="log" label="LOG" count={fLogs.length} />
          </div>

          {/* Tab: feature usage */}
          {tab === 'overview' && (
            <div className="space-y-4">
              <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-[10px] text-stone-500 font-bold tracking-wider">FEATURE USAGE</h3>
                  <div className="flex gap-2">
                    {VIEWER_BUCKETS.map(b => (
                      <span key={b} className="flex items-center gap-1 text-[9px] text-stone-500">
                        <span className="w-2 h-2 rounded-sm" style={{ background: BUCKET_BAR_COLOR[b] }} />{BUCKET_META[b].label}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="space-y-2.5">
                  {features.map(f => {
                    const meta = usageActionMeta(f.action);
                    return (
                      <div key={f.action}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs text-stone-300">{meta.emoji} {meta.label}</span>
                          <span className="text-[10px] text-stone-500">
                            <span className="text-white font-bold">{f.count}</span>
                            {' · '}{f.people.size} {f.people.size === 1 ? 'person' : 'people'}
                            {f.watchSec > 30 ? ` · ${fmtWatch(f.watchSec)}` : ''}
                          </span>
                        </div>
                        <div className="flex h-2.5 rounded-full overflow-hidden bg-stone-800" style={{ width: `${Math.max((f.count / featMax) * 100, 6)}%`, minWidth: '24px' }}>
                          {VIEWER_BUCKETS.map(b => (
                            f.byBucket[b] > 0 && (
                              <div key={b} style={{ width: `${(f.byBucket[b] / f.count) * 100}%`, background: BUCKET_BAR_COLOR[b] }} />
                            )
                          ))}
                        </div>
                      </div>
                    );
                  })}
                  {features.length === 0 && <p className="text-stone-500 text-sm text-center py-2">No activity in this period.</p>}
                </div>
              </div>

              {topGames.length > 0 && (
                <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
                  <h3 className="text-[10px] text-stone-500 font-bold tracking-wider mb-3">TOP GAMES</h3>
                  <div className="space-y-2">
                    {topGames.map((g, i) => (
                      <div key={g.gameId} className="flex items-center gap-2">
                        <span className="text-[10px] text-stone-600 w-4">{i + 1}.</span>
                        <span className="text-sm text-white flex-1 truncate">{gameName(g.gameId)}</span>
                        <span className="text-xs text-stone-400">{g.count} views{g.watchSec > 30 ? ` · ${fmtWatch(g.watchSec)}` : ''}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tab: people + bucket tagging */}
          {tab === 'people' && (
            <div className="space-y-2">
              <p className="text-[10px] text-stone-500 text-center">Tap a bucket chip to tag a viewer — multiple buckets allowed. Coaches from the allow-list are pre-bucketed.</p>
              {people.map(p => (
                <div key={p.email} className="bg-stone-900 border border-stone-800 rounded-xl px-4 py-3">
                  <div className="flex items-center gap-3">
                    <Avatar name={p.name} email={p.email} photo={p.photo} size="w-9 h-9 text-sm" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-white font-medium truncate">{p.name || p.email?.split('@')[0]}</div>
                      <div className="text-[10px] text-stone-500 truncate">{p.email}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-[10px] text-stone-400">
                        🚪 {p.visits}{p.video > 0 && <span className="text-sky-400 ml-1.5">▶ {p.video}</span>}{p.watchSec > 30 && <span className="text-lime-400 ml-1.5">{fmtWatch(p.watchSec)}</span>}
                      </div>
                      <div className="text-[9px] text-stone-600 mt-0.5">{relativeTime(p.lastSeen)}</div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <BucketChips email={p.email} editable />
                    {p.sections.size > 0 && (
                      <span className="text-[9px] text-stone-600 truncate max-w-[40%]" title={[...p.sections].join(', ')}>
                        {[...p.sections].slice(0, 2).join(' · ')}{p.sections.size > 2 ? ` +${p.sections.size - 2}` : ''}
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {people.length === 0 && <p className="text-stone-500 text-sm text-center py-4">No viewers in this period.</p>}
            </div>
          )}

          {/* Tab: raw log */}
          {tab === 'log' && (
            <div className="space-y-1">
              {fLogs.slice(0, 60).map(l => {
                const meta = usageActionMeta(l.action);
                const gname = l.gameId ? gameName(l.gameId) : null;
                return (
                  <div key={l.id} className="flex items-center gap-2 py-2 border-b border-stone-800/40">
                    <Avatar name={l.name} email={l.email} photo={l.photo} size="w-6 h-6 text-[10px]" />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs text-stone-200 truncate block">{l.name || l.email?.split('@')[0]}</span>
                    </div>
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-stone-800 text-stone-300 truncate max-w-[140px]">
                      {meta.emoji} {meta.label}{gname ? ` · ${gname}` : ''}{durSecOf(l) > 30 ? ` · ${fmtWatch(durSecOf(l))}` : ''}
                    </span>
                    <span className="text-[10px] text-stone-600 w-20 text-right shrink-0">{fmtTs(l.ts)}</span>
                  </div>
                );
              })}
              {fLogs.length === 0 && <p className="text-stone-500 text-sm text-center py-4">No activity yet.</p>}
              {fLogs.length > 60 && <p className="text-stone-600 text-xs text-center py-2">Showing 60 of {fLogs.length} events</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function HelpView({ onBack }) {
  const [openId, setOpenId] = useState('welcome');
  const toggle = (id) => setOpenId(prev => prev === id ? null : id);

  const Section = ({ id, emoji, title, summary, children }) => {
    const isOpen = openId === id;
    return (
      <div className="bg-stone-900 border-2 border-stone-800 rounded-2xl overflow-hidden">
        <button
          onClick={() => toggle(id)}
          className="w-full text-left px-4 py-3 flex items-start gap-3 active:bg-stone-950"
        >
          <span className="text-2xl leading-none">{emoji}</span>
          <div className="flex-1 min-w-0">
            <div className="font-display text-base leading-tight">{title}</div>
            {summary && <div className="text-xs text-stone-400 mt-0.5">{summary}</div>}
          </div>
          <ChevronRight className={`w-5 h-5 text-stone-400 mt-1 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
        </button>
        {isOpen && (
          <div className="px-4 pb-4 pt-1 text-sm text-stone-200 leading-relaxed space-y-2 border-t border-stone-800">
            {children}
          </div>
        )}
      </div>
    );
  };

  const Pill = ({ children, tone = 'stone' }) => {
    const tones = {
      stone: 'bg-stone-800 text-stone-300 border-stone-700',
      lime:  'bg-lime-500/15 text-lime-300 border-lime-400',
      red:   'bg-red-500/15 text-red-300 border-red-400',
      amber: 'bg-amber-100 text-amber-900 border-amber-400',
      sky:   'bg-sky-500/15 text-sky-800 border-sky-400',
      purple:'bg-purple-100 text-purple-800 border-purple-400',
    };
    return <span className={`inline-block px-2 py-0.5 rounded-md text-[11px] font-bold border ${tones[tone]} mx-0.5`}>{children}</span>;
  };

  const Step = ({ n, children }) => (
    <div className="flex gap-2">
      <div className="shrink-0 w-5 h-5 rounded-full bg-stone-900 text-lime-400 text-[11px] font-bold flex items-center justify-center mt-0.5">{n}</div>
      <div className="flex-1">{children}</div>
    </div>
  );

  return (
    <div className="pb-24 bg-stone-950 min-h-screen">
      <Header title="HELP & GUIDE" onBack={onBack} />

      <div className="px-4 pt-3 space-y-2.5">
        <Section
          id="welcome"
          emoji="👋"
          title="Welcome"
          summary="What this app does, in one screen"
        >
          <p>
            This is your <strong>match-day tracker</strong>. Before each game you build a
            squad of available players, set the starting lineup, then log every meaningful
            moment as it happens. After the whistle you get per-player ratings, minutes,
            and season totals.
          </p>
          <p>The basic flow is always the same:</p>
          <Step n={1}>Open <Pill tone="lime">CALENDAR</Pill> → tap the game's day → <Pill tone="lime">START</Pill> → squad → starting 7.</Step>
          <Step n={2}>During play, tap actions as they happen.</Step>
          <Step n={3}>End the game → scores save → review in <Pill>STATS</Pill> &amp; past games.</Step>
          <p className="text-xs text-stone-400">All data syncs to the cloud, so other coaches with the link see the same roster and live games.</p>
        </Section>

        <Section id="start" emoji="🚀" title="1 · Starting a match" summary="Opponent → squad → lineup → GK">
          <p>Open <Pill tone="lime">CALENDAR</Pill>, tap the day, then <Pill tone="lime">START</Pill> on the game — it arrives pre-filled with the opponent, tag, colours and squad you set when you scheduled it. For a game that is not on the calendar, tap <Pill tone="lime">+ EVENT</Pill> in the calendar header first. Either way you walk the same wizard:</p>
          <Step n={1}><strong>Game setup</strong>: opponent name, the <strong>tag</strong> (scrimmage / festival / tournament / league — this sets how much the game counts toward season scores), an optional competition name, and the <strong>format</strong> — 7v7 or 9v9. Picking 9v9 suggests a 30-minute half and raises the squad limit to 16; you can still override both.</Step>
          <Step n={2}><strong>Squad picker</strong>: tick who's actually available today. You'll see a soft warning over the squad limit for the format (12 for 7v7, 16 for 9v9) — but no hard limit.</Step>
          <Step n={3}><strong>Starting lineup</strong>: pick the 7 (or 9 for 9v9) who start on the field. Everyone else begins on the bench.</Step>
          <Step n={4}><strong>Goalkeeper</strong>: tap the player wearing the gloves. You can change keeper at any time during the game.</Step>
          <p className="text-xs text-stone-400">If you mis-pick the squad, tap <ChevronLeft className="inline w-3 h-3" /> to go back at any step — nothing is saved until kick-off.</p>
        </Section>

        <Section id="clock" emoji="⏱" title="2 · The clock & periods" summary="First half, half time, second half">
          <p>The clock starts as soon as the lineup is confirmed. It runs as <strong>1st half → half-time pause → 2nd half</strong>.</p>
          <ul className="list-disc pl-5 space-y-1">
            <li>Tap <Pill tone="amber">PAUSE FOR HALF TIME</Pill> when the ref blows for halftime — minutes stop counting.</li>
            <li>Tap <Pill tone="lime">START 2ND HALF</Pill> at kickoff.</li>
            <li>If you paused too early, <Pill>RESUME 1ST HALF</Pill> undoes the pause.</li>
            <li>Tap <Pill tone="red">END GAME</Pill> at the final whistle. The score and all stats lock in.</li>
          </ul>
        </Section>

        <Section id="actions" emoji="🎯" title="3 · Logging actions (EARN / LOSE)" summary="Tap event → tap player">
          <p>The action grid is split into two zones so the value of each tap is obvious:</p>
          <div className="bg-lime-500/10 border-2 border-lime-300 rounded-xl p-3">
            <div className="font-display text-sm text-lime-800 mb-1">EARN <span className="text-lime-700">+</span></div>
            Good things your players do: <Pill tone="lime">⚽ GOAL</Pill> <Pill tone="lime">🔑 KEY PASS</Pill> <Pill tone="lime">🎯 SHOT ON</Pill> <Pill tone="lime">🧤 SAVE</Pill> <Pill tone="lime">🛡 BLOCK</Pill> <Pill tone="lime">🔥 BALL WIN</Pill> <Pill tone="lime">💪 1V1 WIN</Pill> <Pill tone="lime">🔄 GIVE &amp; GO</Pill> <Pill tone="lime">🚪 GATES</Pill>
          </div>
          <div className="bg-red-500/10 border-2 border-red-300 rounded-xl p-3">
            <div className="font-display text-sm text-red-800 mb-1">LOSE <span className="text-red-700">−</span></div>
            Things that hurt the score: <Pill tone="red">🛑 HOLDS BALL</Pill> <Pill tone="red">🔁 TURNOVER</Pill> <Pill tone="red">💢 1V1 LOSE</Pill> <Pill tone="red">🚨 OPP GOAL</Pill>
          </div>
          <p>Flow for every action:</p>
          <Step n={1}>Tap the action button.</Step>
          <Step n={2}>The picker shows everyone <strong>currently on the field</strong>. Tap the player.</Step>
          <Step n={3}>For <Pill tone="lime">GOAL</Pill> you're asked if there was an assist — pick the assister, or <em>NO ASSIST</em>.</Step>
          <Step n={4}>For <Pill tone="lime">🔄 GIVE &amp; GO</Pill> you're asked for the <strong>wall-pass partner</strong> — the teammate who returned the ball. Initiator gets full credit, partner gets half. Tap <em>SKIP</em> if you didn't catch who.</Step>
          <Step n={5}>For <Pill tone="red">OPP GOAL</Pill> you'll be asked whose fault it was: <em>GK</em>, <em>UNSTOPPABLE</em>, or <em>NEUTRAL</em>. Affects the keeper's score.</Step>
          <p className="text-xs text-stone-400">Tapped the wrong thing? Use <Pill>↶ UNDO</Pill> in the RECENT panel to remove the last event.</p>
          <p>Saw something but can't break it down right now? Tap <Pill tone="amber">🔖 MARK</Pill> — one tap stamps the moment with no player or type. After the game, the <strong>Film Room → Confirm Queue</strong> cues the video at each bookmark so you can classify it calmly.</p>
        </Section>

        <Section id="subs" emoji="🔄" title="4 · Substitutions & GK swaps" summary="Two-tap flow, validates lineup">
          <p>Tap <Pill tone="purple">SUBSTITUTION</Pill> then:</p>
          <Step n={1}><strong>Who's OFF?</strong> Picker shows only players on the field.</Step>
          <Step n={2}><strong>Who's ON?</strong> Picker shows only bench players (with minutes played so far so you can spread time fairly).</Step>
          <p>If the player coming off is the current keeper, you'll be prompted to pick a new GK immediately.</p>
          <p>To change the keeper without subbing anyone, tap <Pill tone="amber">SWAP GK</Pill>. You can pick from the entire squad (field <em>or</em> bench).</p>
          <p className="text-xs text-stone-400">The system rejects impossible subs — you can't take the same player off twice, and you can't sub someone in who's already on the field.</p>
        </Section>

        <Section id="minutes" emoji="⏲" title="5 · Tracking minutes played" summary="Make sure no one sits all game">
          <p>Tap <Pill>⏱ MINUTES</Pill> on the action screen at any time to see live minutes for every squad player, sorted highest → lowest. Use it before each sub so you can keep playing time balanced.</p>
          <p>The substitution ON picker also shows each bench player's current minutes, so you can sub on whoever needs the time.</p>
        </Section>

        <Section id="scoring" emoji="📊" title="6 · How scores are calculated" summary="Four pillars · normalized per 20 min">
          <p>Every player gets an <strong>overall score</strong> built from four pillars:</p>
          <ul className="list-disc pl-5 space-y-1">
            <li><Pill tone="lime">ATK</Pill> Attacking — goals, assists, key passes, shots.</li>
            <li><Pill tone="sky">DEF</Pill> Defending — saves, blocks, ball wins, 1v1s, clean sheet (GK).</li>
            <li><Pill tone="amber">DEC</Pill> Decisions — give &amp; go, gates, holds-ball/turnover penalties.</li>
            <li><Pill>INV</Pill> Involvement — sheer number of actions per minute on the pitch.</li>
          </ul>
          <p>Each pillar is normalized to a "per 20 minutes" rate so a substitute who plays 10 minutes is compared fairly against a starter who plays 40. Since v{SCORING_VERSION} (Jun 2026), rates are also <strong>shrunk toward the squad average</strong> with a few virtual minutes, so tiny samples can't dominate; mistakes no longer count as Involvement; clean sheets are pro-rated by GK time; and the season score weights games by type (scrimmages count half by default).</p>
          <p>Outfield players use a balanced blend; goalkeepers use a defence-heavy blend (DEF counts ~55%, ATK only ~10%).</p>
        </Section>

        <Section id="weights" emoji="⚙" title="7 · Tuning scoring weights" summary="Adjust how much each action is worth">
          <p>From Home tap <Pill>⚙ SCORING</Pill>. Three tabs:</p>
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>ACTIONS</strong> — points per action. The same values apply to outfield players and the keeper.</li>
            <li><strong>PILLARS</strong> — how much each pillar (ATK · DEF · DEC · INV) contributes to the overall score. Outfield and GK have separate mixes — the GK row is DEF-heavy because that's where keepers earn their rating. Each row should sum to 100% — the header turns red if it doesn't.</li>
            <li><strong>FAIRNESS</strong> — the shrinkage prior (virtual minutes of squad-average play) and per-game-type season weights (scrimmage / festival / everything else).</li>
          </ul>
          <p>Negative numbers (red boxes) penalize the score. Tap <Pill>RESET</Pill> in the top-right to restore defaults. All past games re-score live with the new weights — nothing is baked in.</p>
        </Section>

        <Section id="history" emoji="📜" title="8 · Past games & season stats" summary="Where to find recorded matches">
          <p>The <Pill>CALENDAR</Pill> grid colours every finished match by its result — green won, red lost, grey drawn. Tap the day, then the game, to see the timeline of every event, per-player scores, and minutes. <Pill>🎥 FILM ROOM</Pill> lists the same games with their video.</p>
          <p>Tap <Pill>STATS</Pill> for season-aggregate per-player numbers — total minutes, goals, season performance score, etc.</p>
          <p>To delete a game tap into it and use the trash button (top-right) — that wipes the videos from R2, the analytics from Firestore, and removes the game from the public list. To delete <em>just</em> the videos (and keep the stats so you can re-run the pipeline later), open <strong>Analytics</strong> and tap "🗑 DELETE VIDEOS ONLY". To remove a single mis-logged event, tap the trash next to it in the event list.</p>
          <p><strong>FILM ROOM → ✅ CONFIRM QUEUE</strong> gathers everything that still needs a decision after the final whistle: 🔖 bookmarks to classify, plus shots/turnovers/ball-wins missing a zone and decision events missing pressure. Each card can cue the TV reel at that exact moment, and the tracking pipeline pre-fills its best guess for zone and pressure — confirm or correct, one tap each.</p>
        </Section>

        <Section id="tips" emoji="💡" title="9 · Tips for coaches" summary="Get the most out of the app">
          <ul className="list-disc pl-5 space-y-1.5">
            <li><strong>Pre-load your squad</strong> at the field before warm-ups. It only takes a minute and you won't have to fiddle once the whistle blows.</li>
            <li><strong>One person, one phone.</strong> Don't have two coaches both logging on different devices — duplicates will appear.</li>
            <li><strong>Log events generously.</strong> Even rough tallies are useful for season trends. Missed one? No problem — undo is for the most recent only, so just keep going.</li>
            <li><strong>Use ⏱ MINUTES</strong> early in the 2nd half — it makes sub decisions much faster.</li>
            <li><strong>Don't stress accuracy.</strong> The pillars normalize across players so even imperfect logging produces useful relative scores.</li>
            <li><strong>Reset welcome:</strong> If another coach takes over the device, clear <code>stompers_welcome_dismissed</code> in browser storage (or use a fresh browser profile) to show this guide on first launch.</li>
          </ul>
        </Section>

        <Section id="data" emoji="☁" title="10 · Where data lives" summary="Cloud + offline behavior">
          <p>The deployed web app stores everything in Firebase (a Google cloud database). Anyone with the URL is editing the <em>same</em> shared team data, in real time.</p>
          <p>If your phone goes offline mid-game, you can keep logging — entries queue and sync when you reconnect. Just don't close the tab before the sync completes.</p>
        </Section>

        <Section id="install" emoji="📲" title="11 · Install on your phone" summary="iPhone + Android — feels like a real app">
          <p>The app is a <strong>PWA</strong> — install it to your home screen and it launches fullscreen with its own icon, no browser bar, and works offline.</p>
          <p className="text-xs text-stone-400">Tap the green <Pill tone="lime">📲 INSTALL</Pill> button on the home screen at any time to open these steps.</p>

          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mt-2">
            <div className="font-display text-sm text-stone-200 mb-2">📱 iPhone &amp; iPad (Safari only)</div>
            <Step n={1}>Open the app in <strong>Safari</strong> (not Chrome — Apple doesn't allow other browsers to install PWAs on iOS).</Step>
            <Step n={2}>Tap the <strong>Share</strong> button <Pill>⬆</Pill> at the bottom of Safari.</Step>
            <Step n={3}>Scroll down and tap <strong>Add to Home Screen</strong>.</Step>
            <Step n={4}>Confirm with <strong>Add</strong> in the top-right. Done!</Step>
          </div>

          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mt-2">
            <div className="font-display text-sm text-stone-200 mb-2">🤖 Android (Chrome, Edge, Samsung Internet)</div>
            <p className="text-sm">On most Android browsers a one-tap install banner appears the first time you visit. If you missed it:</p>
            <Step n={1}>Tap the <strong>⋮</strong> menu (top-right of the browser).</Step>
            <Step n={2}>Tap <strong>Install app</strong> (or <strong>Add to Home screen</strong>).</Step>
            <Step n={3}>Confirm <strong>Install</strong>.</Step>
            <p className="text-xs text-stone-400 mt-1">After install, launch from the home-screen icon — it runs fullscreen with no browser UI.</p>
          </div>

          <p className="text-xs text-stone-400 mt-2">Sharing the app with another parent or coach? Just send them the same URL — they install on their own device and see the same shared team data in real time.</p>
        </Section>

        <Section id="live" emoji="📡" title="12 · Public scoreboard & coach access" summary="How URLs are split between parents and coaches">
          <p>The site has <strong>three URLs</strong>, each for a different audience:</p>
          <div className="bg-stone-950 border border-stone-800 rounded-xl p-3 mt-2 text-sm space-y-2">
            <div>
              <div className="font-display text-sm text-stone-200">👩‍👦 Parents — public scoreboard</div>
              <div className="text-xs text-stone-300">The bare URL (e.g. <code>stompers2016.com</code>) shows the live or most recent game scoreboard plus a list of all past games. First name + jersey number only — no full names.</div>
            </div>
            <div>
              <div className="font-display text-sm text-stone-200">📎 Share a specific game</div>
              <div className="text-xs text-stone-300">Tap <Pill tone="lime">📡 SHARE</Pill> on any game to copy a link like <code>?live=GAME_ID</code> — great for tournament recaps or revisiting a past match in a TeamSnap event post.</div>
            </div>
            <div>
              <div className="font-display text-sm text-stone-200">🔑 Coaches — password-gated app</div>
              <div className="text-xs text-stone-300">Coach app lives at <code>?coach</code> (e.g. <code>stompers2016.com/?coach</code>). Bookmark this URL on your phone — enter the coach password once and it remembers your device.</div>
            </div>
          </div>
          <p className="text-xs text-stone-400 mt-2">Parents never need a password or to install anything — they just open the bare URL and bookmark it. Coaches install the PWA on the <code>?coach</code> URL for the full app experience.</p>
        </Section>

        <div className="text-center text-xs text-stone-400 pt-2 pb-1">
          Tap a row to expand · {String.fromCharCode(169)} LaSalle Stompers
        </div>
      </div>
    </div>
  );
}

function Header({ title, onBack, right }) {
  return (
    <div className="bg-stone-900 border-b border-stone-800 px-4 pt-12 pb-3 flex items-center gap-3">
      <button onClick={onBack} className="w-10 h-10 rounded-full bg-stone-900 flex items-center justify-center active:scale-95">
        <ChevronLeft className="w-5 h-5" />
      </button>
      <h1 className="font-display text-2xl flex-1">{title}</h1>
      {right}
    </div>
  );
}

/* =========================================================================
   LIVE SCOREBOARD (public, read-only)
   Two entry points, both render <LiveScoreboard> internally:
     - <LiveScorePage gameId> — used by ?live=<gameId> deep-links.
         Subscribes to ONE game doc by id.
     - <PublicHomePage>       — used by the bare root URL (no params).
         Subscribes to ALL games, auto-picks active or most-recent finished,
         and renders a "Past games" list under the main scoreboard.
   Privacy: scoreboard shows first name + jersey number only — never last names,
   never pillar events, rosters, or weights.
   Auth: existing anonymous Firebase auth covers public viewers transparently.
   ========================================================================= */
/* ---- PublicVideoToggle: collapsed by default, expands to show 360° player ---- */
function PublicVideoToggle({ url, game, label }) {
  const [open, setOpen] = useState(false);
  // Hidden for now: we're not broadcasting/serving the 360° sphere video to
  // parents yet — they get the TV reel + highlights instead. Remove this early
  // return to re-enable the public "WATCH 360° VIDEO" button.
  return null;
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full bg-stone-900 border border-stone-800 rounded-xl px-4 py-3 flex items-center justify-between active:scale-[0.98] transition"
      >
        <span className="text-sm font-bold">{label}</span>
        <span className="text-xs text-stone-400 tracking-wider">TAP TO PLAY</span>
      </button>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-stone-400 tracking-wider">{label}</span>
        <button onClick={() => setOpen(false)} className="text-[10px] font-bold text-stone-500 hover:text-stone-300">HIDE</button>
      </div>
      <VideoPlayer360
        videoUrl={url}
        events={game.events || []}
        dotsMode="goals"
        lockDots
        gameInfo={{
          home: 'Stompers',
          away: game.opponent || 'OPP',
          homeScore: game.ourScore,
          awayScore: game.oppScore,
          period: game.period || 1,
          halfLengthMin: game.halfLengthMin || 25,
          homeColor: game.homeColor,
          awayColor: game.awayColor,
          status: game.status,
          gameId: game.id,
          // Live clock fields — mirror coach-app match clock so the scorebug
          // shows the same minute/half as the page header (vs. video time).
          clockRunning: game.clockRunning,
          startedAt: game.startedAt,
          segmentStartedAt: game.segmentStartedAt,
          elapsedAtPause: game.elapsedAtPause,
        }}
      />
    </div>
  );
}

/* ---- PublicAnalyticsCard: PUBLIC = videos only --------------------------
 * Parents/spectators see ONLY the broadcast videos (highlights + full game).
 *
 * IMPORTANT — privacy boundary:
 *   This component reads broadcast fields directly from the GAME DOC
 *   (videoHighlightsUrl, videoFullGameUrl, broadcastEvents, ...). It does
 *   NOT read the analytics/v1 subcollection. The analytics subcollection
 *   is locked to coaches by Firestore rules (see firestore.rules) and
 *   contains per-player stats, GK positioning, identity confidences, etc.
 *
 *   The on-screen overlay inside the videos also follows this rule:
 *   broadcastEvents only stores first name + jersey number (built by
 *   _build_broadcast_events_index in post_game/pipeline.py).
 */
function PublicAnalyticsCard({ game, roster: _roster }) {
  const [broadcastOpen, setBroadcastOpen] = useState(null); // 'tv_reel' | 'auto_highlights' | null

  // Usage analytics: log every reel open; endTs on close → watch time.
  const watchDocRef = useRef(null);
  useEffect(() => {
    if (broadcastOpen) {
      watchDocRef.current = trackUsage(
        broadcastOpen === 'tv_reel' ? 'watch_full_game' : 'watch_highlights',
        { gameId: game?.id || null },
        false,
      );
    }
    return () => { untrackUsage(watchDocRef.current); watchDocRef.current = null; };
  }, [broadcastOpen]);

  // Overlay index moved off the game doc (2026-06-13) → games/<id>/public/
  // broadcast, fetched only when a reel opens so the scoreboard list stays
  // lean. Fallback to the legacy on-doc field for games not yet re-run.
  const [bEvents, setBEvents] = useState(null);
  useEffect(() => {
    if (!broadcastOpen) return;
    if (Array.isArray(game?.broadcastEvents) && game.broadcastEvents.length) {
      setBEvents(game.broadcastEvents); return; // legacy doc, not yet migrated
    }
    if (!window.fbDb || !game?.id) return;
    window.fbDb.collection('teams').doc('main').collection('games').doc(game.id)
      .collection('public').doc('broadcast').get()
      .then(s => setBEvents((s.exists && s.data().events) || []))
      .catch(() => setBEvents([]));
  }, [broadcastOpen, game?.id]);

  const highlightsUrl = game?.videoHighlightsUrl;
  const fullGameUrl = game?.videoFullGameUrl;
  const highlightsDur = game?.videoHighlightsDurationS;
  const fullGameDur = game?.videoFullGameDurationS;
  if (!highlightsUrl && !fullGameUrl) return null;

  // The broadcast player expects an analytics-shaped "doc" for the overlay.
  // We synthesize a minimal one from the public game-doc fields.
  const broadcastDoc = {
    broadcast_events: bEvents || game?.broadcastEvents || [],
    // Scorebug is us/them-oriented (us always left), matching the live in-game
    // scorebug and the pipeline. Do NOT swap by isHome.
    home_name: game?.broadcastHomeName || 'Stompers',
    away_name: game?.broadcastAwayName || (game?.opponent || 'OPP'),
    home_color: game?.broadcastHomeColor || game?.homeColor,
    away_color: game?.broadcastAwayColor || game?.awayColor,
  };

  return (
    <div className="px-4 pt-4 max-w-2xl mx-auto">
      <div className="bg-stone-900 border border-stone-800 rounded-2xl p-4 space-y-2">
        <div className="flex items-center justify-between mb-1">
          <h3 className="font-display text-lg">🎬 MATCH VIDEO</h3>
          <span className="text-[10px] text-stone-500 tracking-wider">POST-GAME</span>
        </div>
        {highlightsUrl && (
          <button
            onClick={() => setBroadcastOpen('auto_highlights')}
            className="w-full flex items-center justify-between bg-lime-600 hover:bg-lime-500 text-stone-950 font-display rounded-lg px-4 py-3 active:scale-[0.98]"
          >
            <span>▶ WATCH HIGHLIGHTS</span>
            <span className="text-xs tabular-nums opacity-80">
              {highlightsDur
                ? `${Math.floor(highlightsDur / 60)}:${String(Math.floor(highlightsDur % 60)).padStart(2, '0')}`
                : ''}
            </span>
          </button>
        )}
        {fullGameUrl && (
          <button
            onClick={() => setBroadcastOpen('tv_reel')}
            className="w-full flex items-center justify-between bg-stone-800 hover:bg-stone-700 text-white font-display rounded-lg px-4 py-3 border border-stone-700 active:scale-[0.98]"
          >
            <span>▶ WATCH FULL GAME</span>
            <span className="text-xs tabular-nums text-stone-400">
              {fullGameDur
                ? `${Math.floor(fullGameDur / 60)}:${String(Math.floor(fullGameDur % 60)).padStart(2, '0')}`
                : ''}
            </span>
          </button>
        )}
      </div>
      {broadcastOpen && (
        <BroadcastVideoPlayer
          url={broadcastOpen === 'tv_reel' ? fullGameUrl : highlightsUrl}
          doc={broadcastDoc}
          label={broadcastOpen === 'tv_reel' ? `FULL GAME — ${game.opponent}` : `HIGHLIGHTS — ${game.opponent}`}
          timeKey={broadcastOpen === 'tv_reel' ? 'tvReelTimeS' : 'autoHighlightsTimeS'}
          onClose={() => setBroadcastOpen(null)}
        />
      )}
    </div>
  );
}

function LiveScorePage({ gameId }) {
  const [game, setGame] = useState(null);
  const [roster, setRoster] = useState([]);
  const [error, setError] = useState(null);

  // Usage analytics: one ping per game page per session.
  useEffect(() => { trackUsage('public:game', { gameId }); }, [gameId]);
  // Telemetry watchdog handshake (see shell).
  useEffect(() => { if ((game || error) && typeof window !== 'undefined') window.__appReady = true; }, [game, error]);
  // Cold-start resilience: re-arm the snapshot listeners when the first
  // result never lands (see PublicHomePage for the pattern rationale).
  const [retryNonce, setRetryNonce] = useState(0);
  useEffect(() => {
    if (game || error) return undefined;
    const t = setTimeout(() => {
      if (retryNonce < 3) setRetryNonce(n => n + 1);
      else setError('Connection is stuck — go back and tap the game again.');
    }, 12000);
    return () => clearTimeout(t);
  }, [game, error, retryNonce]);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbReady) {
      setError('This live link only works on the deployed app.');
      return;
    }
    let unsubGame = null, unsubRoster = null;
    window.fbReady.then(() => {
      const db = window.fbDb;
      if (!db) { setError('Could not connect to Firebase.'); return; }
      const teamRef = db.collection('teams').doc('main');
      unsubGame = teamRef.collection('games').doc(gameId).onSnapshot(
        (snap) => {
          if (!snap.exists) { setError('Game not found.'); return; }
          setGame({ ...snap.data(), id: snap.id });
        },
        (err) => setError(err.message || 'Could not load game.')
      );
      unsubRoster = teamRef.onSnapshot(
        (snap) => {
          if (snap.exists) {
            const data = snap.data();
            if (Array.isArray(data.roster)) setRoster(data.roster);
          }
        },
        () => {}
      );
    });
    return () => { if (unsubGame) unsubGame(); if (unsubRoster) unsubRoster(); };
    // retryNonce: cold-start watchdog re-arms these listeners when stuck.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameId, retryNonce]);

  if (error) return <PublicErrorScreen msg={error} />;
  if (!game) return <PublicLoadingScreen />;
  return (
    <div className="min-h-screen bg-stone-950 pb-12 relative">
      <style>{FONT_STYLES}</style>
      <div className="relative stripes-bg border-b-2 border-lime-500/70 shadow-[0_4px_24px_-8px_rgba(132,204,22,0.35)] overflow-hidden pt-[calc(env(safe-area-inset-top,0px)+3.25rem)]">
        <a
          href="./"
          onClick={(e) => { if (window.__navBack) { e.preventDefault(); window.__navBack(); } }}
          className="absolute top-[calc(env(safe-area-inset-top,0px)+1rem)] left-3 z-10 bg-white/15 hover:bg-white/25 text-white text-xs font-bold tracking-widest px-3 py-2 rounded-lg backdrop-blur-sm border border-white/20 flex items-center gap-1"
        >
          <ChevronLeft className="w-4 h-4" /> ALL MATCHES
        </a>
        <LiveScoreboard game={game} roster={roster} transparent />
      </div>
      {game.youtubeVideoId && (
        <div className="px-4 pt-4 max-w-2xl mx-auto">
          <div className="bg-red-950/40 border border-red-800 rounded-xl px-4 py-2 flex items-center gap-2 mb-3">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-sm font-bold text-red-300">{game.status === 'active' ? 'LIVE NOW' : 'REPLAY'}</span>
          </div>
          <YouTubeEmbed videoId={game.youtubeVideoId} live={game.status === 'active'} />
        </div>
      )}
      {!game.youtubeVideoId && game.liveInput?.hlsUrl && (
        <div className="px-4 pt-4 max-w-2xl mx-auto">
          <div className="bg-red-950/40 border border-red-800 rounded-xl px-4 py-2 flex items-center gap-2 mb-3">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-sm font-bold text-red-300">LIVE NOW</span>
          </div>
          <PublicVideoToggle
            url={game.liveInput.hlsUrl}
            game={game}
            label="🔴 WATCH LIVE"
          />
        </div>
      )}
      {!game.youtubeVideoId && !game.liveInput?.hlsUrl && game.videoUrl && (
        <div className="px-4 pt-4 max-w-2xl mx-auto">
          <PublicVideoToggle
            url={game.videoUrl}
            game={game}
            label="🎥 WATCH 360° VIDEO"
          />
        </div>
      )}
      {game.status === 'finished' && (
        <PublicAnalyticsCard game={game} roster={roster} />
      )}
    </div>
  );
}

/* ---- PublicHomePage: default URL for parents ---- */
// All public-page time logic is anchored in America/Toronto (the user calls
// this "EDT"). Browsers may be in a different tz on the road, so we never
// trust the local clock for date/hour decisions — we project Date.now() into
// America/Toronto and reason about wall-clock there.
const APP_TZ = 'America/Toronto';

function tzNowParts(d = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: APP_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(d).reduce((o, p) => { o[p.type] = p.value; return o; }, {});
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
  };
}

// Convert a Toronto wall-clock (YYYY-MM-DD, HH:MM) to a UTC ms epoch.
function tzTimestamp(dateStr, timeStr) {
  if (!dateStr) return 0;
  const [Y, M, D] = dateStr.split('-').map(Number);
  const [h, m] = (timeStr || '00:00').split(':').map(Number);
  const utcGuess = Date.UTC(Y, M - 1, D, h, m);
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: APP_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).formatToParts(new Date(utcGuess)).reduce((o, p) => { o[p.type] = p.value; return o; }, {});
  const asLocal = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour) % 24, Number(parts.minute), Number(parts.second),
  );
  const offset = asLocal - utcGuess;
  return utcGuess - offset;
}

// Pre-game / cancelled card that mirrors LiveScoreboard's look so the public
// home keeps a consistent "field" panel whether or not the game has started.
function ScheduledScoreboard({ item, transparent = false }) {
  const isCancelled = !!item.cancelled;
  const opp = splitTeamName(item.opponent || 'Opponent');
  // Both teams are stacked ("<city>" overline above "<team>"). Width budget
  // is driven by the longest team-name part and the longest city part.
  const longestName = Math.max(8 /* Stompers */, opp.name.length);
  const longestCity = Math.max(7 /* LaSalle */, opp.city.length);
  const nameSizeClass =
    longestName <= 7 ? 'text-4xl'
    : longestName <= 9 ? 'text-3xl'
    : longestName <= 12 ? 'text-2xl'
    : longestName <= 16 ? 'text-xl'
    : longestName <= 20 ? 'text-lg'
    : 'text-base';
  const overlineSizeClass =
    longestCity <= 9 ? 'text-sm'
    : longestCity <= 12 ? 'text-xs'
    : 'text-[10px]';
  return (
    <div className={`${transparent ? '' : 'stripes-bg '}text-white px-4 ${transparent ? 'pt-2' : 'pt-[calc(env(safe-area-inset-top,0px)+3.75rem)]'} pb-6`}>
      <div className="text-center text-xs uppercase tracking-widest text-white/60 mb-1">
        {item.tournament || 'Match'} · {formatDate(item.date)}
      </div>
      <div className="flex items-end justify-between gap-4 mt-5 px-2">
        <div className="flex-1 min-w-0 text-center">
          <div className={`font-display ${overlineSizeClass} text-lime-400/90 leading-none tracking-wide`}>LaSalle</div>
          <div className={`font-display ${nameSizeClass} leading-tight`}>Stompers</div>
        </div>
        <div className="flex-1 min-w-0 text-center">
          {opp.city && (
            <div className={`font-display ${overlineSizeClass} text-white/70 leading-none tracking-wide`}>{opp.city}</div>
          )}
          <div className={`font-display ${nameSizeClass} leading-tight`}>{opp.name}</div>
        </div>
      </div>
      <div className="font-display text-6xl tabular-nums text-center mt-3 leading-none text-white/30">
        <span>–</span><span className="mx-3 text-3xl align-middle">vs</span><span>–</span>
      </div>
      <div className="text-center mt-5 flex items-center justify-center gap-2 flex-wrap">
        {isCancelled ? (
          <span className="inline-block bg-red-500/20 text-red-200 border border-red-400/60 font-extrabold tracking-wider text-sm px-3 py-1 rounded-full">
            CANCELLED
          </span>
        ) : (
          <span className="inline-block bg-yellow-400/15 text-yellow-100 border border-yellow-300/50 font-extrabold tracking-wider text-sm px-3 py-1 rounded-full">
            {item.time ? `KICKS OFF AT ${formatTime12(item.time)}` : 'KICKOFF TBD'}
          </span>
        )}
        {item.field && (
          <span className="inline-block bg-blue-500/15 text-blue-200 border border-blue-500/40 font-extrabold tracking-wider text-xs px-2 py-1 rounded">
            📍 {item.field}
          </span>
        )}
      </div>
      {item.location && (
        <div className="text-center text-xs text-blue-300 mt-3">
          {item.location.startsWith('http') ? (
            <a href={item.location} target="_blank" rel="noopener noreferrer" className="underline inline-flex items-center gap-1">
              <MapPin className="w-3 h-3" /> View Map
            </a>
          ) : (
            <a href={`https://maps.google.com/?q=${encodeURIComponent(item.location)}`} target="_blank" rel="noopener noreferrer" className="underline inline-flex items-center gap-1">
              <MapPin className="w-3 h-3" /> {item.location}
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function PublicHomePage({ access }) {
  const [games, setGames] = useState([]);
  const [roster, setRoster] = useState([]);
  const [schedule, setSchedule] = useState([]);
  // Same TeamSnap mirror the dugout reads, so parents see practices and
  // tournaments rather than games only. Read-only on both sides — the Worker
  // cron owns this subcollection.
  const [teamsnapEvents, setTeamsnapEvents] = useState([]);
  const [teamsnapSyncedAt, setTeamsnapSyncedAt] = useState(null);
  // The coach's hidden entry keys, read-only in the parent view.
  const [hiddenEventKeys, setHiddenEventKeys] = useState(() => new Set());
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const isCoachUser = !!access && access.status === 'coach';
  // Tile hub: which full-screen panel is open ('heatmaps'|'stats'|'past'|'training').
  const [openPanel, setOpenPanel] = useState(null);
  // Families with several kids on the roster pick which one the kid tiles show.
  const kidIds = (access && access.playerIds) || [];
  const [kidId, setKidId] = useState(kidIds[0] || null);
  // Re-render once a minute so the featured card flips at 6 AM ET, kickoff,
  // and (final whistle + 1h) without needing a page reload or new snapshot.
  const [, setMinTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setMinTick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  // Usage analytics: one ping per session for the public home page.
  useEffect(() => { trackUsage('public:home'); }, []);
  // Telemetry watchdog handshake (see shell).
  useEffect(() => { if ((loaded || error) && typeof window !== 'undefined') window.__appReady = true; }, [loaded, error]);
  // Cold-start resilience: if the first snapshot never arrives, tear down and
  // re-subscribe (bumping retryNonce re-runs the listener effect) instead of
  // spinning forever; after 3 tries, say so out loud.
  const [retryNonce, setRetryNonce] = useState(0);
  useEffect(() => {
    if (loaded || error) return undefined;
    const t = setTimeout(() => {
      if (retryNonce < 3) setRetryNonce(n => n + 1);
      else setError('Connection is stuck — close and reopen the app, or check your signal.');
    }, 12000);
    return () => clearTimeout(t);
  }, [loaded, error, retryNonce]);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbReady) {
      setError('App not loaded.');
      return;
    }
    let unsubGames = null, unsubRoster = null;
    window.fbReady.then(() => {
      if (!window.fbDb) { setError('Could not connect to Firebase.'); return; }
      const teamRef = window.fbDb.collection('teams').doc('main');
      unsubGames = teamRef.collection('games').onSnapshot(
        (snap) => {
          const list = snap.docs.map((d) => ({ ...d.data(), id: d.id }));
          // Sort: newest date first, then newest endedAt (so the most recently
          // finished game of a tournament day stays featured).
          list.sort((a, b) => {
            const dc = new Date(b.date) - new Date(a.date);
            if (dc !== 0) return dc;
            return (b.endedAt || 0) - (a.endedAt || 0);
          });
          setGames(list);
          setLoaded(true);
        },
        (err) => { setError(err.message || 'Could not load games.'); setLoaded(true); }
      );
      unsubRoster = teamRef.onSnapshot(
        (snap) => {
          if (snap.exists) {
            const data = snap.data();
            if (Array.isArray(data.roster)) setRoster(data.roster);
            if (Array.isArray(data.schedule)) setSchedule(data.schedule);
          }
        },
        () => {}
      );
    });
    return () => { if (unsubGames) unsubGames(); if (unsubRoster) unsubRoster(); };
    // retryNonce: cold-start watchdog re-arms these listeners when stuck.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryNonce]);

  // TeamSnap mirror. Its own effect, mirroring App's, so the page still loads
  // when this subcollection is empty or unreadable — the calendar then just
  // shows fewer kinds instead of the whole parent view failing.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbReady) return undefined;
    let unsub = null;
    window.fbReady.then(() => {
      if (!window.fbDb) return;
      unsub = window.fbDb.collection('teams').doc('main').collection('teamsnapEvents')
        .onSnapshot(
          (snap) => {
            const list = [];
            let stamp = null;
            snap.forEach((d) => {
              // The cron's own clock, written as one more doc in this
              // collection. A sentinel, not an event — real ids look like
              // `9230745-366776389` — so it must not reach the merge. The
              // freshness stamp comes from here, never from an event's
              // `modified`, which is when the coach last edited in TeamSnap.
              if (d.id === 'zz-sync-meta') { stamp = d.data()?.syncedAt ?? null; return; }
              list.push({ uid: d.id, ...d.data() });
            });
            setTeamsnapEvents(list);
            setTeamsnapSyncedAt(stamp);
          },
          (err) => console.error('teamsnapEvents listen failed', err)
        );
    });
    return () => { if (unsub) unsub(); };
  }, []);

  // The coach's hide flags, read-only — parents never hide anything. Mirrored
  // here rather than passed down because PublicHomePage is a separate root
  // reached by a route branch, not a child of App. Without it a hidden event
  // would keep showing for families, which is the opposite of the intent.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbReady) return undefined;
    let unsub = null;
    window.fbReady.then(() => {
      if (!window.fbDb) return;
      unsub = window.fbDb.collection('teams').doc('main').collection('calendarHidden')
        .onSnapshot(
          (snap) => {
            const s = new Set();
            snap.forEach((d) => { const k = d.data()?.key; if (k) s.add(k); });
            setHiddenEventKeys(s);
          },
          (err) => console.error('calendarHidden listen failed', err)
        );
    });
    return () => { if (unsub) unsub(); };
  }, []);

  // Above the early returns on purpose: a hook after a conditional return
  // changes the hook count between renders and React throws #310.
  const calendarToday = useMemo(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }, []);
  const calendarModel = useMemo(
    () => buildCalendarModel({ teamsnapEvents, schedule, games, today: calendarToday, hidden: hiddenEventKeys }),
    [teamsnapEvents, schedule, games, calendarToday, hiddenEventKeys]
  );

  if (error) return <PublicErrorScreen msg={error} />;
  if (!loaded) return <PublicLoadingScreen />;

  const nowMs = Date.now();
  const todayStr = tzNowParts().date;
  const HOUR = 60 * 60 * 1000;

  const active = games.find((g) => g.status === 'active');
  const finished = games.filter((g) => g.status === 'finished');

  // Build today's slot list: today's finished/active games + today's schedule
  // items not already represented by a game doc (matched by opponent).
  const norm = (s) => (s || '').trim().toLowerCase();
  const todayFinished = finished.filter((g) => (g.date || '').slice(0, 10) === todayStr);
  const isCovered = (s) => {
    if (active && (active.date || '').slice(0, 10) === todayStr && norm(active.opponent) === norm(s.opponent)) return true;
    return todayFinished.some((g) => norm(g.opponent) === norm(s.opponent));
  };
  // Games only. The schedule now also holds coach-created practices, tryouts and
  // team events (they carry a `type`; absent means game, since every item
  // predates the field). Those have no opponent and no score, so featuring one
  // would put a scoreboard reading "Stompers vs Opponent" at the top of the
  // parent home page.
  const todaySched = (schedule || []).filter(
    (s) => (s.date || '').slice(0, 10) === todayStr
      && (s.type || 'game') === 'game'
      && !isCovered(s),
  );

  const slots = [];
  for (const g of todayFinished) {
    const kickoffMs = g.startedAt || tzTimestamp(todayStr, '00:00');
    slots.push({ kind: 'finished', game: g, kickoffMs, endMs: g.endedAt || kickoffMs });
  }
  for (const s of todaySched) {
    const kickoffMs = tzTimestamp(todayStr, s.time || '12:00');
    // Per coach: a cancellation within 3h of kickoff is treated like a played
    // game (so parents see CANCELLED on the home card). We don't record
    // `cancelledAt` yet, so we honor any `s.cancelled === true` for today.
    slots.push({
      kind: s.cancelled ? 'cancelled' : 'upcoming',
      item: s,
      kickoffMs,
      endMs: kickoffMs,
    });
  }
  slots.sort((a, b) => a.kickoffMs - b.kickoffMs);

  // Pick the featured slot.
  //   1. Active game today always wins.
  //   2. Otherwise, from 6 AM ET onward:
  //      - Latest slot whose kickoff has passed and is still "current"
  //        (within end+1h, OR is the last slot of the day → stay featured
  //        until midnight ET).
  //      - If nothing is current yet, show the next upcoming slot
  //        (pre-kickoff card, starting at 6 AM ET).
  let featuredSlot = null;
  if (active && (active.date || '').slice(0, 10) === todayStr) {
    featuredSlot = { kind: 'active', game: active };
  } else {
    const sixAm = tzTimestamp(todayStr, '06:00');
    if (nowMs >= sixAm && slots.length > 0) {
      for (let i = slots.length - 1; i >= 0; i--) {
        const s = slots[i];
        const isLast = i === slots.length - 1;
        if (s.kickoffMs <= nowMs) {
          const flipAt = s.endMs + HOUR;
          if (isLast || nowMs < flipAt) { featuredSlot = s; break; }
        }
      }
      if (!featuredSlot) featuredSlot = slots.find((s) => s.kickoffMs > nowMs) || null;
    }
  }

  const featuredGame =
    featuredSlot?.kind === 'active' || featuredSlot?.kind === 'finished' ? featuredSlot.game : null;
  const featuredItem =
    featuredSlot?.kind === 'upcoming' || featuredSlot?.kind === 'cancelled' ? featuredSlot.item : null;

  return (
    <div className="min-h-screen bg-stone-950 pb-12 relative">
      <style>{FONT_STYLES}</style>
      {featuredSlot ? (
        <>
          <div className="relative stripes-bg border-b-2 border-lime-500/70 shadow-[0_4px_24px_-8px_rgba(132,204,22,0.35)] overflow-hidden">
            <div className="text-white px-5 pt-16 pb-2">
              <div className="absolute top-[calc(env(safe-area-inset-top,0px)+0.75rem)] right-4 flex items-center gap-2">
                {window.fbUserInfo && (
                  <button
                    onClick={() => { if (window.fbAuth) window.fbAuth.signOut(); }}
                    aria-label="Sign out"
                    className="h-9 px-2 rounded-full bg-white/10 hover:bg-white/20 flex items-center gap-1.5 border border-white/15 active:scale-95"
                  >
                    {window.fbUserInfo.photo && <img src={window.fbUserInfo.photo} className="w-5 h-5 rounded-full" referrerPolicy="no-referrer" />}
                    <span className="text-[10px] text-white/70 font-bold">Sign Out</span>
                  </button>
                )}
                <a
                  href="./?coach"
                  className={`h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95 ${isCoachUser ? '' : 'hidden'}`}
                >
                  <span>🪑</span><span>DUGOUT</span>
                </a>
              </div>
              <div className="flex items-center gap-4 mt-12">
                <img
                  src="./stompers_logo.png"
                  alt=""
                  className="w-24 h-24 shrink-0 drop-shadow"
                  onError={(e) => { e.currentTarget.style.display = 'none'; }}
                />
                <div className="flex-1 min-w-0">
                  <h1 className="font-display text-4xl leading-none">LASALLE STOMPERS</h1>
                  <div className="font-display text-2xl text-lime-400 leading-tight">2016 BOYS SQUAD</div>
                </div>
              </div>
            </div>
            {featuredGame ? (
              <a href={`./?live=${featuredGame.id}`} onClick={(e) => { if (window.__navigate) { e.preventDefault(); window.__navigate({ kind: 'live', gameId: featuredGame.id }); } }} className="block">
                <LiveScoreboard game={featuredGame} roster={roster} transparent />
              </a>
            ) : (
              <ScheduledScoreboard item={featuredItem} transparent />
            )}
          </div>
          {featuredGame && featuredGame.videoUrl && !featuredGame.youtubeVideoId && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <PublicVideoToggle url={featuredGame.videoUrl} game={featuredGame} label="🎥 WATCH 360° VIDEO" />
            </div>
          )}
          {featuredGame && featuredGame.youtubeVideoId && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <YouTubeEmbed videoId={featuredGame.youtubeVideoId} live={featuredGame.status === 'active'} />
            </div>
          )}
          {featuredGame && !featuredGame.youtubeVideoId && featuredGame.liveInput?.hlsUrl && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <PublicVideoToggle url={featuredGame.liveInput.hlsUrl} game={featuredGame} label="🔴 WATCH LIVE" />
            </div>
          )}
          {featuredGame && featuredGame.status === 'finished' && (
            <PublicAnalyticsCard game={featuredGame} roster={roster} />
          )}
        </>
      ) : (
        <div className="relative stripes-bg text-white px-5 pt-16 pb-12 overflow-hidden border-b-2 border-lime-500/70 shadow-[0_4px_24px_-8px_rgba(132,204,22,0.35)]">
          <div className="absolute top-[calc(env(safe-area-inset-top,0px)+0.75rem)] right-4 flex items-center gap-2">
            {window.fbUserInfo && (
              <button
                onClick={() => { if (window.fbAuth) window.fbAuth.signOut(); }}
                aria-label="Sign out"
                className="h-9 px-2 rounded-full bg-white/10 hover:bg-white/20 flex items-center gap-1.5 border border-white/15 active:scale-95"
              >
                {window.fbUserInfo.photo && <img src={window.fbUserInfo.photo} className="w-5 h-5 rounded-full" referrerPolicy="no-referrer" />}
                <span className="text-[10px] text-white/70 font-bold">Sign Out</span>
              </button>
            )}
            <a
              href="./?coach"
              onClick={(e) => { if (window.__navigate) { e.preventDefault(); window.__navigate({ kind: 'coach' }); } }}
              className={`h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95 ${isCoachUser ? '' : 'hidden'}`}
            >
              <span>🪑</span><span>DUGOUT</span>
            </a>
          </div>
          <div className="flex items-center gap-4 mt-12">
            <img
              src="./stompers_logo.png"
              alt="LaSalle Stompers"
              className="w-24 h-24 shrink-0 drop-shadow-lg"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
            <div className="flex-1 min-w-0">
              <h1 className="font-display text-4xl leading-none">LASALLE STOMPERS</h1>
              <div className="font-display text-2xl text-lime-400 leading-tight">2016 BOYS SQUAD</div>
            </div>
          </div>
          <div className="mt-10 text-center">
            <div className="inline-block bg-white/10 rounded-2xl px-6 py-5">
              <div className="font-display text-2xl">No live match right now</div>
              <div className="text-white/60 text-sm mt-1">Check back on game day.</div>
            </div>
          </div>
        </div>
      )}
      {/* ---- Tile hub (coach's spec 2026-08-18): everything except the
           upcoming schedule lives in tiles. Kid tiles render only for
           accounts linked to a player; kid-less viewers get the two team
           tiles. ---- */}
      {(() => {
        const kid = kidId ? (roster || []).find((p) => p.id === kidId) : null;
        const kidFirst = kid ? (kid.name || '').trim().split(/\s+/)[0] : null;
        return (
          <div className="px-4 pt-6 max-w-2xl mx-auto space-y-3">
            {kidIds.length > 1 && (
              <div className="flex gap-2 flex-wrap">
                {kidIds.map((pid) => {
                  const p = (roster || []).find((x) => x.id === pid);
                  const first = p ? (p.name || '').trim().split(/\s+/)[0] : '?';
                  return (
                    <button
                      key={pid}
                      onClick={() => setKidId(pid)}
                      className={`px-3 py-1.5 rounded-full text-xs font-bold border ${pid === kidId ? 'bg-lime-500 text-stone-950 border-lime-400' : 'bg-stone-900 text-stone-300 border-stone-700'}`}
                    >
                      {first}
                    </button>
                  );
                })}
              </div>
            )}
            {kidId && (
              <div>
                <h3 className="font-display text-xl text-stone-200 mb-2">
                  {(kidFirst || 'MY PLAYER').toUpperCase()}{kid && kid.number ? ` · #${kid.number}` : ''}
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <TileButton onClick={() => setOpenPanel('heatmaps')} icon={<span className="text-2xl leading-none">🔥</span>} label="HEATMAPS" sub="Where he played, per game" />
                  <TileButton onClick={() => setOpenPanel('stats')} icon={<span className="text-2xl leading-none">📊</span>} label="MATCH STATS" sub="Goals & assists per game" />
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <TileButton onClick={() => setOpenPanel('past')} icon={<span className="text-2xl leading-none">🏟️</span>} label="PAST GAMES" sub="Scores, film & highlights" />
              <TileButton onClick={() => setOpenPanel('training')} icon={<span className="text-2xl leading-none">🎬</span>} label="TRAINING" sub="Soccer & GK drills" />
            </div>
          </div>
        );
      })()}
      {/* The same calendar the dugout shows, read-only. Replaces the flat
           UPCOMING GAMES rows: parents get practices, tournaments and past
           results on one surface, and the agenda strip keeps the "what's next"
           scan the rows used to provide. No `onBack` — this sits inline under
           the tiles, so CalendarView renders its embedded heading rather than
           a full-screen Header.

           The wrapper only centres and caps the width; CalendarView picks its
           own root shape from the absence of onBack. Its sections already carry
           their own px-4, so no padding is added here. */}
      <div className="max-w-2xl mx-auto">
        <CalendarView
          model={calendarModel}
          canEdit={false}
          today={calendarToday}
          syncedAt={teamsnapSyncedAt}
          onOpenGame={(gameId) => {
            // The route PAST GAMES already uses — the read-only game view a
            // parent is permitted.
            if (window.__navigate) window.__navigate({ kind: 'live', gameId });
            else window.location.href = `./?live=${gameId}`;
          }}
        />
      </div>
      {openPanel === 'past' && (
        <PastGamesPanel games={games} onBack={() => setOpenPanel(null)} />
      )}
      {openPanel === 'heatmaps' && kidId && (
        <ParentSeasonPanel mode="heatmaps" playerId={kidId} roster={roster} onBack={() => setOpenPanel(null)} />
      )}
      {openPanel === 'stats' && kidId && (
        <ParentSeasonPanel mode="stats" playerId={kidId} roster={roster} onBack={() => setOpenPanel(null)} />
      )}
      {openPanel === 'training' && <TrainingHub onBack={() => setOpenPanel(null)} />}

    </div>
  );
}

/* ---- Tile-hub panels (parent home, 2026-08-18) ---- */
// Full-screen overlay with a sticky header — the tile views live in these so
// the router/history machinery stays untouched (closing = plain state).
function PanelShell({ title, sub, onBack, children }) {
  useEffect(() => { if (typeof window !== 'undefined') window.scrollTo(0, 0); }, []);
  return (
    <div className="fixed inset-0 z-50 bg-stone-950 overflow-y-auto pb-12">
      <div className="sticky top-0 z-10 stripes-bg border-b border-stone-800 px-4 pt-[calc(env(safe-area-inset-top,0px)+0.75rem)] pb-3 flex items-center gap-3">
        <button onClick={onBack} className="bg-white/15 hover:bg-white/25 text-white text-xs font-bold tracking-widest px-3 py-2 rounded-lg flex items-center gap-1 shrink-0">
          <ChevronLeft className="w-4 h-4" /> BACK
        </button>
        <div className="min-w-0">
          <div className="font-display text-xl text-white leading-none truncate">{title}</div>
          {sub && <div className="text-[11px] text-white/60 mt-0.5 truncate">{sub}</div>}
        </div>
      </div>
      <div className="px-4 pt-4 max-w-2xl mx-auto">{children}</div>
    </div>
  );
}

function PastGamesPanel({ games, onBack }) {
  const past = (games || [])
    .filter((g) => g.status === 'finished')
    .sort((a, b) => (b.date || '').localeCompare(a.date || '')
      || (b.endedAt || b.startedAt || 0) - (a.endedAt || a.startedAt || 0));
  return (
    <PanelShell title="PAST GAMES" sub="Tap a game for the scoreboard, film and highlights" onBack={onBack}>
      {past.length === 0 ? (
        <div className="text-stone-500 text-sm text-center py-10">No finished games yet.</div>
      ) : (
        <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
          {past.map((g) => {
            const r = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
            const rColor = r === 'W' ? 'bg-lime-500 text-white' : r === 'L' ? 'bg-red-500 text-white' : 'bg-stone-700 text-stone-100';
            return (
              <a key={g.id} href={`./?live=${g.id}`} onClick={(e) => { if (window.__navigate) { e.preventDefault(); window.__navigate({ kind: 'live', gameId: g.id }); } }} className="flex items-center gap-3 p-3 active:bg-stone-950">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-display text-sm ${rColor}`}>{r}</div>
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">vs {g.opponent}</div>
                  <div className="text-xs text-stone-400 truncate">{g.tournament || gameTypeOf(g).toUpperCase()} · {formatDate(g.date)}</div>
                </div>
                <div className="font-display text-lg tabular-nums text-stone-100">{g.ourScore}–{g.oppScore}</div>
                <ChevronRight className="w-4 h-4 text-stone-400" />
              </a>
            );
          })}
        </div>
      )}
    </PanelShell>
  );
}

// One small read per open: the per-kid season doc published by the pipeline.
// Parents can only fetch playerIds on their own allowedUsers row (rules).
function useParentSeason(playerId) {
  const [state, setState] = useState({ loading: true, error: null, doc: null });
  useEffect(() => {
    if (!playerId || typeof window === 'undefined' || !window.fbDb) return undefined;
    let cancelled = false;
    setState({ loading: true, error: null, doc: null });
    window.fbDb.collection('teams').doc('main').collection('parentSeason').doc(playerId).get()
      .then((s) => { if (!cancelled) setState({ loading: false, error: null, doc: s.exists ? s.data() : null }); })
      .catch(() => { if (!cancelled) setState({ loading: false, error: 'Could not load season data — try again in a bit.', doc: null }); });
    return () => { cancelled = true; };
  }, [playerId]);
  return state;
}

// Both kid tiles share one panel: mode 'heatmaps' | 'stats'. Rows carry a
// full "–" story: attended=false → didn't play; attended but no heatmap →
// below the tracking-coverage bar (we don't show confidently-wrong maps).
function ParentSeasonPanel({ mode, playerId, roster, onBack }) {
  const { loading, error, doc } = useParentSeason(playerId);
  const kid = (roster || []).find((p) => p.id === playerId);
  const first = (doc && doc.playerFirstName) || (kid ? (kid.name || '').trim().split(/\s+/)[0] : 'Player');
  const num = (doc && doc.playerNumber) || (kid && kid.number) || '';
  const rows = ((doc && doc.games) || []).slice()
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  const [expandedId, setExpandedId] = useState(null);
  const title = `${String(first).toUpperCase()}${num ? ` #${num}` : ''} — ${mode === 'heatmaps' ? 'HEATMAPS' : 'MATCH STATS'}`;
  const sub = mode === 'heatmaps' ? 'From the coach’s tagged positions' : 'From the coach’s live match log';

  let body;
  if (loading) {
    body = <div className="text-stone-400 animate-pulse text-sm text-center py-10">Loading…</div>;
  } else if (error) {
    body = <div className="text-stone-400 text-sm text-center py-10">{error}</div>;
  } else if (rows.length === 0) {
    body = (
      <div className="text-stone-500 text-sm text-center py-10">
        No season data yet — this fills in after the next game analysis.
      </div>
    );
  } else if (mode === 'heatmaps') {
    const expanded = rows.find((g) => g.gameId === expandedId);
    body = (
      <div className="space-y-3">
        {expanded && expanded.heatmap && (
          <button onClick={() => setExpandedId(null)} className="block w-full bg-stone-900 border border-lime-600/50 rounded-2xl p-4 text-left">
            <div className="flex items-center justify-between mb-2">
              <div className="font-bold text-sm">vs {expanded.opponent} · {formatDate(expanded.date)}</div>
              <span className="text-[10px] text-stone-400 tracking-wider">TAP TO SHRINK</span>
            </div>
            <PlayerHeatmap grid={expanded.heatmap} rows={expanded.heatmapRows || 12} cols={expanded.heatmapCols || 8} />
          </button>
        )}
        <div className="grid grid-cols-2 gap-3">
          {rows.map((g) => (
            <button
              key={g.gameId}
              onClick={() => (g.heatmap ? setExpandedId(g.gameId === expandedId ? null : g.gameId) : null)}
              className={`bg-stone-900 border rounded-2xl p-3 text-left ${g.gameId === expandedId ? 'border-lime-600/50' : 'border-stone-800'}`}
            >
              <div className="text-xs font-bold truncate">vs {g.opponent}</div>
              <div className="text-[10px] text-stone-500 mb-2">{formatDate(g.date)}</div>
              {g.attended && g.heatmap ? (
                <PlayerHeatmap grid={g.heatmap} rows={g.heatmapRows || 12} cols={g.heatmapCols || 8} />
              ) : (
                <div className="h-24 flex items-center justify-center text-stone-600">
                  <div className="text-center">
                    <div className="font-display text-2xl leading-none">–</div>
                    <div className="text-[10px] mt-1">{g.attended ? 'Not tagged yet' : 'Didn’t play'}</div>
                  </div>
                </div>
              )}
            </button>
          ))}
        </div>
      </div>
    );
  } else {
    const attended = rows.filter((g) => g.attended);
    const sum = (k) => attended.reduce((s, g) => s + (Number(g[k]) || 0), 0);
    const showSaves = rows.some((g) => (g.saves || 0) > 0);
    // Minutes can be null (no sub-log data for that game) — distinct from 0.
    const minCell = (g) => (!g.attended || g.minutes == null ? '–' : Math.round(Number(g.minutes) || 0));
    body = (
      <div className="space-y-3">
        <div className="grid grid-cols-4 gap-2">
          {[['GAMES', attended.length], ['GOALS', sum('goals')], ['ASSISTS', sum('assists')], ['MINUTES', Math.round(sum('minutes'))]].map(([label, v]) => (
            <div key={label} className="bg-stone-900 border border-stone-800 rounded-xl p-2 text-center">
              <div className="font-display text-xl text-lime-400 tabular-nums leading-none">{v}</div>
              <div className="text-[9px] text-stone-500 tracking-wider mt-1">{label}</div>
            </div>
          ))}
        </div>
        <div className="bg-stone-900 border border-stone-800 rounded-2xl overflow-hidden">
          <div className={`grid ${showSaves ? 'grid-cols-[1fr_2rem_2rem_2rem_2.6rem]' : 'grid-cols-[1fr_2rem_2rem_2.6rem]'} gap-1 px-3 py-2 text-[10px] text-stone-500 tracking-wider border-b border-stone-800`}>
            <div>GAME</div><div className="text-center">G</div><div className="text-center">A</div>{showSaves && <div className="text-center">SV</div>}<div className="text-center">MIN</div>
          </div>
          {rows.map((g) => {
            const r = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
            const rColor = r === 'W' ? 'text-lime-400' : r === 'L' ? 'text-red-400' : 'text-stone-400';
            const cell = (v) => (g.attended ? (Number(v) || 0) : '–');
            return (
              <div key={g.gameId} className={`grid ${showSaves ? 'grid-cols-[1fr_2rem_2rem_2rem_2.6rem]' : 'grid-cols-[1fr_2rem_2rem_2.6rem]'} gap-1 px-3 py-2.5 border-b border-stone-800/60 last:border-0 items-center ${g.attended ? '' : 'opacity-50'}`}>
                <div className="min-w-0">
                  <div className="text-xs font-bold truncate">
                    <span className={`font-display mr-1 ${rColor}`}>{r}</span>
                    vs {g.opponent}
                  </div>
                  <div className="text-[10px] text-stone-500">{formatDate(g.date)} · {g.ourScore}–{g.oppScore}</div>
                </div>
                <div className="text-center text-sm tabular-nums">{cell(g.goals)}</div>
                <div className="text-center text-sm tabular-nums">{cell(g.assists)}</div>
                {showSaves && <div className="text-center text-sm tabular-nums">{cell(g.saves)}</div>}
                <div className="text-center text-sm tabular-nums">{minCell(g)}</div>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] text-stone-600 text-center">
          "–" means {first} didn’t play that game. Minutes come from the coach’s sub log.
        </p>
      </div>
    );
  }

  return <PanelShell title={title} sub={sub} onBack={onBack}>{body}</PanelShell>;
}

/* ---- shared public-mode helpers ---- */
function PublicErrorScreen({ msg }) {
  return (
    <div className="min-h-screen stripes-bg text-white flex items-center justify-center p-6">
      <style>{FONT_STYLES}</style>
      <div className="bg-white/10 rounded-2xl p-6 max-w-sm text-center">
        <div className="font-display text-3xl mb-2">⚠ {msg}</div>
        <div className="text-white/60 text-sm">Double-check the link with the coach.</div>
      </div>
    </div>
  );
}
function PublicLoadingScreen() {
  return (
    <div className="min-h-screen stripes-bg text-white flex items-center justify-center">
      <style>{FONT_STYLES}</style>
      <div className="font-display text-2xl text-white/60">Loading…</div>
    </div>
  );
}

/* ---- LiveScoreboard: pure render given game + roster ---- */
function LiveScoreboard({ game, roster, transparent = false }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!game || game.status !== 'active' || !game.clockRunning) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [game?.status, game?.clockRunning]);

  // Pair each GOAL with the immediately-following ASSIST (the coach app prompts
  // for ASSIST right after GOAL, so they're adjacent within ~60s).
  const feed = useMemo(() => {
    if (!game) return [];
    const events = [...(game.events || [])].filter(e => e.type !== 'POSITION').sort((a, b) => a.at - b.at);
    const out = [];
    let ourRun = 0, oppRun = 0;
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.type === 'GOAL') {
        ourRun++;
        let assist = null;
        for (let j = i + 1; j < events.length; j++) {
          const n = events[j];
          if (n.at - e.at > 60000) break;
          if (n.type === 'ASSIST' && n.playerId && n.playerId !== e.playerId) { assist = n; break; }
          if (n.type === 'GOAL' || n.type === 'OPP_GOAL') break;
        }
        out.push({ kind: 'us', at: e.at, elapsed: e.elapsed, period: e.period, scorerId: e.playerId, assistId: assist?.playerId || null, ourRun, oppRun });
      } else if (e.type === 'OPP_GOAL') {
        oppRun++;
        out.push({ kind: 'opp', at: e.at, elapsed: e.elapsed, period: e.period, ownGoalById: e.ownGoalById || null, ourRun, oppRun });
      }
    }
    return out;
  }, [game]);

  const elapsed = computeElapsed(game);
  const halfLenMin = Number(game.halfLengthMin) || 25;
  const totalMin = halfLenMin * 2;
  // Continuous match minute: in P2 add the first-half length so it reads 34'/60'
  // instead of restarting at 4'.
  const mins = Math.floor(elapsed / 60) + ((game.period || 1) === 2 ? halfLenMin : 0);
  const isActive = game.status === 'active';
  const isFinished = game.status === 'finished';
  let statusLabel;
  if (isFinished) statusLabel = 'FULL TIME';
  else if (isActive && !game.clockRunning) statusLabel = game.period >= 2 ? 'PAUSED' : 'HALF TIME';
  else if (isActive) statusLabel = `${mins}'`;
  else statusLabel = 'NOT STARTED';

  const opp = splitTeamName(game.opponent || 'Opponent');
  const leftScore = game.ourScore;
  const rightScore = game.oppScore;
  // Both teams are stacked ("<city>" overline above "<team>"). Width budget
  // is driven by the longest team-name part and the longest city part.
  const longestName = Math.max(8 /* Stompers */, opp.name.length);
  const longestCity = Math.max(7 /* LaSalle */, opp.city.length);
  const nameSizeClass =
    longestName <= 7 ? 'text-4xl'
    : longestName <= 9 ? 'text-3xl'
    : longestName <= 12 ? 'text-2xl'
    : longestName <= 16 ? 'text-xl'
    : longestName <= 20 ? 'text-lg'
    : 'text-base';
  const overlineSizeClass =
    longestCity <= 9 ? 'text-sm'
    : longestCity <= 12 ? 'text-xs'
    : 'text-[10px]';

  // Privacy: first name + jersey number only — never last names on public pages.
  // The roster is signed-in-only now (it holds every child's full name and
  // photo), so a signed-out visitor has an empty one and every scorer would
  // read "Unknown". Events carry only playerId — no name, no number — so there
  // is nothing to fall back to here; say "Player" rather than "Unknown", which
  // reads like a failure. Signed-in viewers still get "First #7" as before.
  const nameOf = (pid) => {
    const p = roster.find((r) => r.id === pid);
    if (!p) return 'Player';
    const first = (p.name || '').split(/\s+/)[0] || p.name || 'Player';
    return `${first} #${p.number || '?'}`;
  };

  const usGoals = feed.filter(r => r.kind === 'us');
  const oppGoals = feed.filter(r => r.kind === 'opp');

  return (
    <>
      <div className={`${transparent ? '' : 'stripes-bg '}text-white px-4 ${transparent ? 'pt-2' : 'pt-[calc(env(safe-area-inset-top,0px)+3.75rem)]'} pb-6`}>
        <div className="text-center text-xs uppercase tracking-widest text-white/60 mb-1">
          {game.tournament || 'Match'} · {formatDate(game.date)}
        </div>
        {/* Names on top, full half-width each. Score sits below for breathing room. */}
        <div className="flex items-end justify-between gap-4 mt-5 px-2">
          <div className="flex-1 min-w-0 text-center">
            <div className={`font-display ${overlineSizeClass} text-lime-400/90 leading-none tracking-wide`}>LaSalle</div>
            <div className={`font-display ${nameSizeClass} leading-tight`}>Stompers</div>
          </div>
          <div className="flex-1 min-w-0 text-center">
            {opp.city && (
              <div className={`font-display ${overlineSizeClass} text-white/70 leading-none tracking-wide`}>{opp.city}</div>
            )}
            <div className={`font-display ${nameSizeClass} leading-tight`}>{opp.name}</div>
          </div>
        </div>
        <div className="font-display text-7xl tabular-nums text-center mt-3 leading-none">
          {leftScore}<span className="text-white/40 mx-3">–</span>{rightScore}
        </div>
        {/* TV-style: goals listed under each team in two columns. Each column
            centers an inline-block group so the goal list sits under the team
            name, while rows inside the group are left-aligned so the ⚽ glyph
            stacks on a consistent edge instead of drifting with name width. */}
        {(usGoals.length > 0 || oppGoals.length > 0) && (
          <div className="flex items-start justify-between gap-4 mt-5 px-2">
            <div className="flex-1 min-w-0 text-center">
              <div className="inline-block text-left space-y-1.5">
                {usGoals.map((row, i) => {
                  const minute = eventDisplayMinute(row, halfLenMin);
                  return (
                    <div key={i} className="text-sm">
                      <div className="font-bold text-white truncate">
                        ⚽ {nameOf(row.scorerId)} <span className="text-white/60 tabular-nums font-normal">{minute}'</span>
                      </div>
                      {row.assistId && (
                        <div className="text-[11px] text-white/50 truncate pl-5">🅰️ {nameOf(row.assistId)}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="flex-1 min-w-0 text-center">
              <div className="inline-block text-left space-y-1.5">
                {oppGoals.map((row, i) => {
                  const minute = eventDisplayMinute(row, halfLenMin);
                  const ownGoalName = row.ownGoalById ? nameOf(row.ownGoalById) : null;
                  return (
                    <div key={i} className="text-sm">
                      <div className="font-bold text-white truncate">
                        ⚽ Goal <span className="text-white/60 tabular-nums font-normal">{minute}'</span>
                      </div>
                      {ownGoalName && (
                        <div className="text-[11px] text-amber-200/80 truncate pl-5">(OG {ownGoalName})</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
        <div className="text-center mt-5 flex items-center justify-center gap-2 flex-wrap">
          {isActive && (
            <span className="inline-block bg-white/10 border border-white/20 text-white/90 px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              {game.period >= 2 ? '2ND HALF' : '1ST HALF'}
            </span>
          )}
          {isActive && game.clockRunning && (
            <span className="inline-flex items-center gap-2 bg-red-500/25 border border-red-400/50 text-red-100 px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse"></span>
              LIVE · {mins}'/{totalMin}'
            </span>
          )}
          {isActive && !game.clockRunning && (
            <span className="inline-block bg-yellow-500/25 border border-yellow-400/50 text-yellow-100 px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              {statusLabel}
            </span>
          )}
          {isFinished && (
            <span className="inline-block bg-white/15 border border-white/20 text-white px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              FULL TIME
            </span>
          )}
        </div>
      </div>
    </>
  );
}
