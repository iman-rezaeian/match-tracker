import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  Plus, Users, Trash2, Edit3, ChevronLeft,
  PlayCircle, Undo2, X, ChevronRight,
  BarChart3, Flag, Zap, Calendar, MapPin
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
  // POSITION is a silent event written by the tactical board on drag-end.
  // Carries { playerId, x, y } where x,y ∈ [0,1] over a half-field portrait
  // (own goal bottom, halfway line top). Filtered out of RECENT and stats.
  POSITION:  { id: 'POSITION',  label: 'POSITION',  emoji: '📍', tone: 'neutral',    requiresPlayer: true,  silent: true },
};

// Events that get an optional zone tag (where on the field it happened).
// Skip continuous/ambient events (HOLDS_BALL) and events with implicit location (ASSIST, OPP_GOAL).
const EVENT_NEEDS_ZONE = new Set([
  'GOAL', 'SHOT_ON', 'SHOT_OFF', 'BALL_WIN', 'TURNOVER',
  'DUEL_WIN', 'DUEL_LOSE', 'KEY_PASS', 'GIVE_GO', 'GATES',
  'SAVE', 'BLOCK', 'CLEAR', 'KICK_OUT',
  'FOUL_BY', 'FOUL_ON', 'PEN_AWARDED', 'PEN_CONCEDED',
]);

// Events that get an optional pressure modifier (was the player under pressure when they did this?).
// Limited to decision-bearing events where pressure changes the meaning a lot.
const EVENT_NEEDS_PRESSURE = new Set([
  'KEY_PASS', 'GIVE_GO', 'GATES', 'BALL_WIN',
  'SHOT_ON', 'SHOT_OFF', 'DUEL_WIN', 'CLEAR', 'KICK_OUT',
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
// and only show the LINK flow. After deploying r2-upload-worker.js, paste the
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

// Viewer tracking — logs to Firestore when users watch video/live
function trackViewer(action, gameId) {
  if (typeof window === 'undefined' || !window.fbDb || !window.fbUserInfo) return null;
  const { email, name, photo } = window.fbUserInfo;
  const docRef = window.fbDb.collection('viewerLog').doc();
  docRef.set({
    email, name, photo, action, gameId,
    ts: window.firebase?.firestore?.FieldValue?.serverTimestamp?.() || new Date()
  }).catch(() => {});
  return docRef.id;
}
function untrackViewer(docId) {
  if (!docId || !window.fbDb) return;
  window.fbDb.collection('viewerLog').doc(docId).update({
    endTs: window.firebase?.firestore?.FieldValue?.serverTimestamp?.() || new Date()
  }).catch(() => {});
}

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
  const cleanSheets = (conceded === 0 && secondsAsGK >= 60 && game.status === 'finished') ? 1 : 0;
  return { oppGoalsConceded: conceded, concededPenalty, cleanSheets, secondsAsGK };
}

// ===== PERFORMANCE SCORE =====
// Weighted per-20-minute composite across 4 development pillars.
// Returns { overall, attacking, defending, decisions, involvement } rounded to 1 decimal.
// `position` (optional) — if 'GK', uses goalie-specific weights so keepers aren't
// unfairly penalized by low ATK/DEC opportunity, and saves count for more.
// `gkExtras` (optional, GK only) — { oppGoalsConceded, cleanSheets } aggregated
// across the games being scored. Adds clean-sheet bonus and conceded penalty.
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
};

function mergeWeights(w) {
  return {
    points:   { ...DEFAULT_WEIGHTS.points,   ...(w?.points   || {}) },
    gkPoints: { ...DEFAULT_WEIGHTS.gkPoints, ...(w?.gkPoints || {}) },
    pillars: {
      outfield: { ...DEFAULT_WEIGHTS.pillars.outfield, ...(w?.pillars?.outfield || {}) },
      gk:       { ...DEFAULT_WEIGHTS.pillars.gk,       ...(w?.pillars?.gk       || {}) },
    },
  };
}

function computePerformanceScore(playerId, events, minutesPlayed, gkFraction, gkExtras = {}, weights) {
  if (minutesPlayed <= 0) return { overall: 0, attacking: 0, defending: 0, decisions: 0, involvement: 0 };
  const W = mergeWeights(weights);
  // gkFraction ∈ [0,1] = share of minutes this player spent in goal. Point values
  // AND pillar weights are blended outfield↔GK by it, so a part-time keeper is
  // scored by their actual role mix (not 100% keeper for a brief stint).
  const f = Math.max(0, Math.min(1, Number(gkFraction) || 0));
  const perHalf = minutesPlayed / 20;
  const c = {};
  let partnerCount = 0; // give & go wall-pass credits earned by this player
  let ownGoals = 0;     // own goals attributed to this player via OPP_GOAL.ownGoalById
  for (const e of events) {
    // Only real play events feed the score. Skip anything that isn't a known,
    // non-silent EVENT_TYPE — this excludes bookkeeping events that aren't in
    // EVENT_TYPES (SUB, GK_CHANGE) and silent ones (POSITION tactical-board
    // drags), so none of them inflate the Involvement pillar.
    const def = EVENT_TYPES[e.type];
    if (!def || def.silent) continue;
    if (e.playerId === playerId) {
      c[e.type] = (c[e.type] || 0) + 1;
    }
    if (e.type === 'GIVE_GO' && e.partnerId === playerId) {
      partnerCount++;
    }
    if (e.type === 'OPP_GOAL' && e.ownGoalById === playerId) {
      ownGoals++;
    }
  }
  // Blended point value for a key: outfield (W.points) at f=0, GK (W.gkPoints) at f=1.
  const po = W.points, pg = W.gkPoints;
  const pt = (k) => { const a = po[k] || 0; return a + f * ((pg[k] || 0) - a); };
  const attacking = (
    (c.GOAL || 0)        * pt('GOAL_atk') +
    (c.ASSIST || 0)      * pt('ASSIST_atk') +
    (c.KEY_PASS || 0)    * pt('KEY_PASS_atk') +
    (c.SHOT_ON || 0)     * pt('SHOT_ON_atk') +
    (c.SHOT_OFF || 0)    * pt('SHOT_OFF_atk') +
    (c.FOUL_ON || 0)     * pt('FOUL_ON_atk') +
    (c.PEN_AWARDED || 0) * pt('PEN_AWARDED_atk')
  ) / perHalf;
  // Clean-sheet / conceded credit applies only to actual GK time (f > 0).
  const concededPenalty = f > 0 ? (gkExtras.concededPenalty || 0) : 0;
  const cleanSheets = f > 0 ? (gkExtras.cleanSheets || 0) : 0;
  const defending = (
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
  ) / perHalf;
  const decisions = (
    (c.GIVE_GO || 0)    * pt('GIVE_GO_dec') +
    partnerCount        * pt('GIVE_GO_PARTNER_dec') +
    (c.GATES || 0)      * pt('GATES_dec') +
    (c.KEY_PASS || 0)   * pt('KEY_PASS_dec') +
    (c.ASSIST || 0)     * pt('ASSIST_dec') +
    (c.HOLDS_BALL || 0) * pt('HOLDS_BALL_dec') +
    (c.TURNOVER || 0)   * pt('TURNOVER_dec')
  ) / perHalf;
  const totalEvents = Object.values(c).reduce((a, b) => a + b, 0) + partnerCount + ownGoals;
  const involvement = totalEvents / perHalf;
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

// Colored pill for the game type. Visibility escalates with stakes:
// Scrimmage = quiet violet (just labeled), Festival = medium-pop teal,
// anything else (e.g. "Canton Cup Tournament") = loudest amber.
function TournamentChip({ value }) {
  if (!value) return null;
  const t = String(value).toLowerCase();
  const cls = t === 'scrimmage' ? 'bg-violet-500/10 text-violet-400 border-violet-500/30'
            : t === 'festival'  ? 'bg-teal-500/20 text-teal-200 border-teal-400/50'
            : 'bg-amber-400/25 text-amber-100 border-amber-300/60';
  return (
    <span className={`inline-block ${cls} border font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded`}>
      {String(value).toUpperCase()}
    </span>
  );
}

const R2_WORKER_KEY = 'ManUtd2016'; // API key for R2 upload worker auth

export default function App() {
  // URL-based routing — computed once at mount; the URL doesn't change without
  // a full reload so this is safe to do before hooks.
  //   ?live=<gameId>  -> single-game public scoreboard (Share button URL)
  //   ?coach          -> coach app (password-gated)
  //   (default)       -> public home: current/latest scoreboard + past games
  const params = (typeof window !== 'undefined')
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams('');
  const liveGameId = params.get('live');
  const isCoach = params.has('coach');
  if (liveGameId) return <LiveScorePage gameId={liveGameId} />;
  if (!isCoach) return <PublicHomePage />;

  // ---- coach app below ----
  const [unlocked, setUnlocked] = useState(false);
  const [roster, setRoster] = useState([]);
  const [games, setGames] = useState([]);
  const [schedule, setSchedule] = useState([]);
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
  const [tick, setTick] = useState(0);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [pendingGameSetup, setPendingGameSetup] = useState(null);
  // When editing a scheduled game's pre-picked squad, we stash the schedule
  // item id (+ a snapshot of the current squad) so the SquadPickerView can
  // save back into the schedule and return to the schedule screen.
  const [editingScheduleSquad, setEditingScheduleSquad] = useState(null);
  // When returning from the squad picker, ScheduleView re-mounts; this hint
  // tells it to drop back into edit mode for the same item.
  const [resumeScheduleEditId, setResumeScheduleEditId] = useState(null);

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
    if (!email) { window.location.replace('./'); return; }
    window.fbDb.collection('allowedUsers').doc(email).get().then((doc) => {
      if (doc.exists && doc.data().role === 'coach') setUnlocked(true);
      else window.location.replace('./');
    }).catch(() => { window.location.replace('./'); });
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

  useEffect(() => {
    if (view !== 'activeGame') return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [view]);

  const persistRoster = async (next) => {
    setRoster(next);
    try { await storageSet(STORAGE_KEYS.ROSTER, JSON.stringify(next)); } catch (e) {}
  };

  const persistGames = async (next) => {
    setGames(next);
    try { await storageSet(STORAGE_KEYS.GAMES, JSON.stringify(next)); } catch (e) {}
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

  const startNewGame = (opponent, isHome, tournament, startingLineup, gkPlayerId, squad, halfLengthMin, homeColor, awayColor, liveInput, youtubeVideoId, kickoffPositions) => {
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
      tournament: tournament || 'Festival',
      isHome: !!isHome,
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
      ...(liveInput ? { liveInput } : {}),
      ...(youtubeVideoId ? { youtubeVideoId } : {}),
    };
    persistGames([game, ...games]);
    setActiveGameId(game.id);
    setView('activeGame');
  };

  const updateGame = (id, mutator) => {
    persistGames(games.map(g => g.id === id ? mutator(g) : g));
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
      ...extras,
    };
    const updated = {
      ...game,
      events: [...game.events, event],
      ourScore: game.ourScore + (ev.delta === 'us' ? 1 : 0),
      oppScore: game.oppScore + (ev.delta === 'opp' ? 1 : 0),
    };
    persistGames(games.map(g => g.id === gameId ? updated : g));

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

    if (eventType === 'GOAL' && playerId) {
      setPendingEvent({ type: 'ASSIST', excludePlayerId: playerId, skippable: true });
    } else {
      setPendingEvent(null);
    }
  };

  const undoLastEvent = (gameId) => {
    const game = games.find(g => g.id === gameId);
    if (!game || game.events.length === 0) return;
    // Skip silent POSITION events — undo should target the coach's last
    // visible action (GOAL, SUB, etc.), not their last tactical drag.
    let lastIdx = -1;
    for (let i = game.events.length - 1; i >= 0; i--) {
      if (game.events[i].type !== 'POSITION') { lastIdx = i; break; }
    }
    if (lastIdx === -1) return;
    const last = game.events[lastIdx];
    const ev = EVENT_TYPES[last.type];
    const updated = {
      ...game,
      events: game.events.filter((_, i) => i !== lastIdx),
      ourScore: game.ourScore - (ev?.delta === 'us' ? 1 : 0),
      oppScore: game.oppScore - (ev?.delta === 'opp' ? 1 : 0),
    };
    persistGames(games.map(g => g.id === gameId ? updated : g));
    showToast('Last event removed');
  };

  const deleteEvent = (gameId, eventId) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const ev = game.events.find(e => e.id === eventId);
    if (!ev) return;
    const evType = EVENT_TYPES[ev.type];
    const updated = {
      ...game,
      events: game.events.filter(e => e.id !== eventId),
      ourScore: game.ourScore - (evType?.delta === 'us' ? 1 : 0),
      oppScore: game.oppScore - (evType?.delta === 'opp' ? 1 : 0),
    };
    persistGames(games.map(g => g.id === gameId ? updated : g));
  };

  // Post-game tagging: patch zone/pressure/decision on an existing event.
  // Pass null/undefined for a field to remove that tag.
  const updateEvent = (gameId, eventId, patch) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
    const events = game.events.map(e => {
      if (e.id !== eventId) return e;
      const next = { ...e };
      for (const k of ['zone', 'pressure', 'decision']) {
        if (k in patch) {
          if (patch[k] == null) delete next[k];
          else next[k] = patch[k];
        }
      }
      return next;
    });
    persistGames(games.map(g => g.id === gameId ? { ...g, events } : g));
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
      await fetch(`${R2_UPLOAD_WORKER}/game/${gameId}/videos/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: R2_WORKER_KEY }),
      });
    } catch (e) { console.warn('R2 wipe failed (continuing):', e); }

    try {
      if (window.fbDb) {
        const gameRef = window.fbDb.collection('teams').doc('main')
          .collection('games').doc(gameId);
        for (const sub of ['analytics', 'clips']) {
          const qs = await gameRef.collection(sub).get();
          await Promise.all(qs.docs.map(d => d.ref.delete()));
        }
        await gameRef.delete();
      }
    } catch (e) {
      console.error('Firestore delete failed:', e);
      showToast('⚠️ Cloud delete failed — see console');
    }

    persistGames(games.filter(g => g.id !== gameId));
    setView('home');
    showToast('🗑 Game deleted');
  };

  const deleteGameVideos = async (gameId) => {
    // Coach "Delete videos only" — wipes R2 reels + clips and clears the
    // public-facing video URL fields on the game doc, but KEEPS the
    // analytics subcollection (per-player stats, GK positioning, etc.) so
    // you can re-render the videos later by re-running the pipeline.
    try {
      const r = await fetch(`${R2_UPLOAD_WORKER}/game/${gameId}/videos/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: R2_WORKER_KEY }),
      });
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
        elapsedAtPause: 0,
        segmentStartedAt: now,
        pausePeriods: pp,
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
      return {
        ...g,
        clockRunning: true,
        period: 1,
        segmentStartedAt: now,
        pausePeriods: pp,
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
      };
    });
    showToast('⏸ Clock paused');
  };

  const resumeClock = (gameId) => {
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
    const updated = { ...game, events: [...game.events, ...inheritedEvents] };
    persistGames(games.map(g => g.id === gameId ? updated : g));
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

  // Halftime bulk lineup change: coach picks the on-field set for the 2nd
  // half in one pass. Emits paired SUB events for each net swap (one OFF
  // player pairs with one ON player) so minutes-played stays accurate.
  // Extras get standalone SUB rows with the "missing" side null — these are
  // tolerated by playerSeconds / onFieldAt (they iterate independently).
  const bulkReplaceLineup = (gameId, nextOnFieldIds) => {
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
    const pairs = Math.max(offNow.length, onNow.length);
    const newEvents = [];
    for (let i = 0; i < pairs; i++) {
      newEvents.push({
        id: uid(),
        type: 'SUB',
        playerId: offNow[i] || null,
        subOnPlayerId: onNow[i] || null,
        period: game.period,
        elapsed,
        at: baseAt + i, // distinct timestamps so onFieldAt orders deterministically
        bulkHalftime: true,
      });
    }
    const updated = { ...game, events: [...game.events, ...newEvents] };
    persistGames(games.map(g => g.id === gameId ? updated : g));
    showToast(`🔄 Lineup updated · ${onNow.length} on / ${offNow.length} off`);
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
    persistGames(games.map(g => g.id === gameId
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
    persistGames(games.map(g => g.id === gameId
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

    let events = [...(game.events || [])];

    // If swapping to a new GK who's currently on the bench, auto-sub them on
    // for the old GK (who must come off to make room). This only fires for the
    // standalone "SWAP GK" flow — when this is called as the follow-up to a
    // SUB-triggered GK pick, the new GK is already on the field.
    if (newGKPlayerId && prevGKId && newGKPlayerId !== prevGKId
        && onField.has(prevGKId) && !onField.has(newGKPlayerId)) {
      events.push({
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
    events.push({
      id: uid(),
      type: 'GK_CHANGE',
      playerId: newGKPlayerId,
      prevGKId: prevGKId || null,
      period: game.period,
      elapsed,
      at: now + 1, // ordered after the SUB above
    });

    const change = { at: now + 1, gkPlayerId: newGKPlayerId || null };
    const updated = {
      ...game,
      events,
      gkChanges: [...(game.gkChanges || []), change],
    };
    persistGames(games.map(g => g.id === gameId ? updated : g));
    const p = roster.find(pl => pl.id === newGKPlayerId);
    showToast(`🧤 ${p?.name || 'No GK'} now in goal`);
    setPendingEvent(null);
  };

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

      {view === 'home' && (
        <HomeView
          roster={roster}
          games={games}
          schedule={schedule}
          activeGame={activeGame}
          onGoRoster={() => setView('roster')}
          onNewGame={() => setView('gameSetup')}
          onStartScheduled={(item) => {
            // Pre-fill from the scheduled item and always route through the
            // full Game Setup → Squad → Lineup flow so the coach can review
            // and tweak anything (half length, colors, squad) before kickoff.
            const prefill = {
              opponent: item.opponent,
              tournament: item.tournament,
              fromSchedule: true,
              isHome: typeof item.isHome === 'boolean' ? item.isHome : true,
              halfLengthMin: item.halfLengthMin,
              homeColor: item.homeColor,
              awayColor: item.awayColor,
              squad: Array.isArray(item.squadIds) && item.squadIds.length > 0 ? item.squadIds : undefined,
            };
            setPendingGameSetup(prefill);
            setView('gameSetup');
          }}
          onResumeGame={() => { setActiveGameId(activeGame.id); setView('activeGame'); }}
          onViewGame={(id) => { setViewingGameId(id); setView('gameDetail'); }}
          onViewStats={() => setView('stats')}
          onViewWeights={() => setView('weights')}
          onViewSchedule={() => setView('schedule')}
          onViewHelp={() => setView('help')}
          onViewViewers={() => setView('viewers')}
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
          onStart={(opponent, isHome, tournament, halfLengthMin, homeColor, awayColor) => {
            // Preserve any pre-picked squad / fromSchedule flag carried in from
            // a scheduled-game start, so it survives the GameSetup detour.
            setPendingGameSetup({ ...(pendingGameSetup || {}), opponent, isHome, tournament, halfLengthMin, homeColor, awayColor });
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
            startNewGame(pendingGameSetup.opponent, pendingGameSetup.isHome, pendingGameSetup.tournament, lineup, gkPlayerId, pendingGameSetup.squad, pendingGameSetup.halfLengthMin, pendingGameSetup.homeColor, pendingGameSetup.awayColor, liveInput, youtubeVideoId, kickoffPositions);
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
            // the next play.
            logEvent(activeGame.id, t, playerId);
          }}
          onCancelEvent={() => setPendingEvent(null)}
          onUndo={() => undoLastEvent(activeGame.id)}
          onPauseHalfTime={() => pauseHalfTime(activeGame.id)}
          onStartSecondHalf={() => startSecondHalf(activeGame.id)}
          onResumeFirstHalf={() => resumeFirstHalf(activeGame.id)}
          onPauseClock={() => pauseClock(activeGame.id)}
          onResumeClock={() => resumeClock(activeGame.id)}
          onEnd={() => askConfirm('End game and save final score?', () => endGame(activeGame.id))}
          onBack={() => setView('home')}
          onBulkReplaceLineup={(ids) => bulkReplaceLineup(activeGame.id, ids)}
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

      {view === 'schedule' && (
        <ScheduleView
          schedule={schedule}
          roster={roster}
          games={games}
          opponentSuggestions={opponentSuggestions}
          onRenameOpponent={async (oldName, newName) => {
            const o = (oldName || '').trim().toLowerCase();
            const n = (newName || '').trim();
            if (!o || !n) return 0;
            const matches = (s) => (s || '').trim().toLowerCase() === o;
            let count = 0;
            const nextGames = games.map((g) => {
              if (matches(g.opponent)) { count++; return { ...g, opponent: n }; }
              return g;
            });
            const nextSchedule = schedule.map((s) => {
              if (matches(s.opponent)) { count++; return { ...s, opponent: n }; }
              return s;
            });
            await Promise.all([persistGames(nextGames), persistSchedule(nextSchedule)]);
            return count;
          }}
          initialEditId={resumeScheduleEditId}
          onConsumedInitialEditId={() => setResumeScheduleEditId(null)}
          onSave={persistSchedule}
          onBack={() => setView('home')}
          onEditSquad={(item) => {
            setEditingScheduleSquad({ itemId: item.id, opponent: item.opponent, squadIds: item.squadIds || [] });
            setView('scheduleSquad');
          }}
          askConfirm={askConfirm}
          showToast={showToast}
        />
      )}

      {view === 'scheduleSquad' && editingScheduleSquad && (
        <SquadPickerView
          roster={roster}
          setup={{ opponent: editingScheduleSquad.opponent }}
          initialSquad={editingScheduleSquad.squadIds}
          title="EDIT MATCH-DAY SQUAD"
          subtitle={<>Pre-pick the squad now. On match day, tapping <span className="font-bold">START</span> will jump straight to the lineup.</>}
          nextLabel="SAVE SQUAD"
          onBack={() => {
            setResumeScheduleEditId(editingScheduleSquad.itemId);
            setEditingScheduleSquad(null);
            setView('schedule');
          }}
          onNext={(squadIds) => {
            const next = schedule.map(s => s.id === editingScheduleSquad.itemId ? { ...s, squadIds } : s);
            persistSchedule(next);
            showToast?.(`✅ Saved ${squadIds.length}-player squad`);
            setResumeScheduleEditId(editingScheduleSquad.itemId);
            setEditingScheduleSquad(null);
            setView('schedule');
          }}
        />
      )}

      {view === 'viewers' && (
        <ViewersPanel onBack={() => setView('home')} />
      )}

      {view === 'filmRoom' && (
        <FilmRoomView games={games} roster={roster} onBack={() => setView('home')} />
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
function HomeView({ roster, games, schedule, activeGame, onGoRoster, onNewGame, onStartScheduled, onResumeGame, onViewGame, onViewStats, onViewWeights, onViewSchedule, onViewHelp, onViewViewers, onViewFilmRoom, onViewTraining }) {
  const finishedGames = games.filter(g => g.status === 'finished');
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
            <div className="flex items-center gap-2 text-lime-400 text-xs font-bold uppercase tracking-widest mb-2">
              <Flag className="w-3.5 h-3.5" />
              Match Day Manager
            </div>
            <h1 className="font-display text-5xl leading-none">U10 BOYS</h1>
            <div className="font-display text-3xl text-lime-400 leading-tight">2016 SQUAD</div>
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
              <div className="text-xs">{activeGame.tournament || 'Festival'} · vs {activeGame.opponent} · {activeGame.ourScore}–{activeGame.oppScore}</div>
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

      <div className="px-4 pt-6">
        <button
          onClick={onNewGame}
          className="w-full bg-lime-500 hover:bg-lime-600 text-stone-100 font-display text-3xl py-6 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.99] transition flex items-center justify-center gap-3"
        >
          <Zap className="w-7 h-7" />
          START GAME
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 px-4 pt-3">
        <TileButton onClick={onGoRoster} icon={<Users className="w-6 h-6" />} label="ROSTER" sub={`${roster.length} players`} />
        <TileButton onClick={onViewStats} icon={<BarChart3 className="w-6 h-6" />} label="STATS" sub="Season totals" />
        <TileButton onClick={onViewSchedule} icon={<Calendar className="w-6 h-6" />} label="SCHEDULE" sub={`${schedule.filter(s => new Date(s.date + 'T' + (s.time || '00:00')) >= new Date()).length} upcoming`} />
        <TileButton onClick={onViewFilmRoom} icon={<span className="text-2xl leading-none">🎥</span>} label="FILM ROOM" sub={`${finishedGames.length} game${finishedGames.length === 1 ? '' : 's'} · analytics`} />
        <TileButton onClick={onViewWeights} icon={<span className="text-2xl leading-none">⚙</span>} label="SCORING" sub="Tune weights" />
        <TileButton onClick={onViewViewers} icon={<span className="text-2xl leading-none">👁</span>} label="VIEWERS" sub="Who's watching" />
        <TileButton onClick={onViewTraining} icon={<span className="text-2xl leading-none">🎓</span>} label="TRAINING" sub="Skill videos" />
      </div>

      {/* Upcoming games */}
      {(() => {
        const playedKey = (g) => `${(g.date || '').slice(0,10)}|${(g.opponent || '').trim().toLowerCase()}`;
        const playedKeys = new Set((games || []).map(playedKey));
        const upcoming = schedule
          .filter(s => new Date(s.date + 'T' + (s.time || '23:59')) >= new Date(new Date().toDateString()))
          .filter(s => !playedKeys.has(`${(s.date || '').slice(0,10)}|${(s.opponent || '').trim().toLowerCase()}`))
          .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        if (upcoming.length === 0) return null;
        return (
          <div className="px-4 pt-6">
            <h2 className="font-display text-2xl mb-3">UPCOMING GAMES</h2>
            <div className="space-y-2">
              {upcoming.slice(0, 5).map(item => (
                <div key={item.id} className="bg-stone-900 border border-stone-800 rounded-xl p-3 flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-blue-500/15 text-blue-300 flex flex-col items-center justify-center text-xs font-bold leading-tight">
                    <span>{new Date(item.date + 'T12:00').toLocaleDateString('en', { month: 'short' }).toUpperCase()}</span>
                    <span className="text-base">{new Date(item.date + 'T12:00').getDate()}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">vs {item.opponent}</div>
                    <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                      {item.cancelled && (
                        <span className="inline-block bg-red-500/15 text-red-300 border border-red-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          CANCELLED
                        </span>
                      )}
                      {item.tournament && <TournamentChip value={item.tournament} />}
                      {item.time && <span>{formatTime12(item.time)}</span>}
                      {item.field && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          📍 {item.field}
                        </span>
                      )}
                    </div>
                    {item.location && (
                      <a
                        href={item.location.startsWith('http') ? item.location : `https://maps.google.com/?q=${encodeURIComponent(item.location)}`}
                        target="_blank" rel="noopener noreferrer"
                        className="text-xs text-blue-400 underline flex items-center gap-1 mt-0.5"
                        onClick={e => e.stopPropagation()}
                      >
                        <MapPin className="w-3 h-3" /> {item.location.startsWith('http') ? 'View Map' : item.location}
                      </a>
                    )}
                  </div>
                  {!activeGame && !item.cancelled && (
                    <button
                      onClick={() => onStartScheduled(item)}
                      className="px-3 py-1.5 bg-lime-500 text-stone-100 font-display text-xs rounded-lg active:scale-95 transition"
                    >
                      START
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      <div className="px-4 pt-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-display text-2xl">PAST GAMES</h2>
          <div className="text-xs text-stone-400 font-semibold">{finishedGames.length} total</div>
        </div>
        {finishedGames.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-stone-400 text-sm">
            No games yet. Tap <span className="font-bold text-stone-100">START GAME</span> when you're at the field.
          </div>
        ) : (
          <div className="space-y-2">
            {finishedGames.slice(0, 10).map(g => (
              <button
                key={g.id}
                onClick={() => onViewGame(g.id)}
                className="w-full bg-stone-900 border border-stone-800 rounded-xl p-3 flex items-center gap-3 active:scale-[0.99] transition text-left"
              >
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center font-display text-base ${
                  g.ourScore > g.oppScore ? 'bg-lime-500/15 text-lime-300' :
                  g.ourScore < g.oppScore ? 'bg-red-500/15 text-red-300' :
                  'bg-stone-800 text-stone-300'
                }`}>
                  {g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">vs {g.opponent}</div>
                  <div className="text-xs text-stone-400 truncate">{g.tournament || 'Festival'} · {formatDate(g.date)}</div>
                </div>
                <div className="font-display text-xl tabular-nums">
                  {g.ourScore}–{g.oppScore}
                </div>
                <ChevronRight className="w-4 h-4 text-stone-400" />
              </button>
            ))}
          </div>
        )}
      </div>
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
  const [tournament, setTournament] = useState(initial?.tournament || 'Festival');
  const [isHome, setIsHome] = useState(typeof initial?.isHome === 'boolean' ? initial.isHome : true);
  const [halfLengthMin, setHalfLengthMin] = useState(initial?.halfLengthMin ?? 25);
  const [homeColor, setHomeColor] = useState(initial?.homeColor || '#0a0a0a');
  const [awayColor, setAwayColor] = useState(initial?.awayColor || '#dc2626');
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
        <Field label="TOURNAMENT">
          <input
            type="text"
            value={tournament}
            onChange={e => setTournament(e.target.value)}
            placeholder="Festival"
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

        <Field label="HALF LENGTH (MIN)">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setHalfLengthMin(m => Math.max(1, m - 1))}
              className="w-14 h-14 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-2xl font-bold active:scale-95 transition"
              aria-label="Decrease half length"
            >−</button>
            <div className="flex-1 py-3 rounded-xl bg-stone-900 border-2 border-stone-800 text-center">
              <div className="font-display text-3xl text-stone-100 tabular-nums leading-none">{halfLengthMin}</div>
              <div className="text-[10px] uppercase tracking-widest text-stone-500 mt-1">minutes</div>
            </div>
            <button
              type="button"
              onClick={() => setHalfLengthMin(m => Math.min(99, m + 1))}
              className="w-14 h-14 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-2xl font-bold active:scale-95 transition"
              aria-label="Increase half length"
            >+</button>
          </div>
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



        <button
          onClick={() => onStart(opponent.trim() || 'Opponent', isHome, tournament.trim() || 'Festival', halfLengthMin, homeColor, awayColor)}
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
  const SOFT_CAP = 12;
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
          Soft limit is <span className="font-bold">{SOFT_CAP}</span> (7v7 max squad) — you can exceed it if you need to.</>)}
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
    fetch(`${R2_UPLOAD_WORKER}/youtube-live`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY }),
    })
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
    fetch(`${R2_UPLOAD_WORKER}/live-input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY, name: 'stompers-team-live' }),
    })
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
      fetch(`${R2_UPLOAD_WORKER}/live-input/${pendingSetup.uid}/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: R2_WORKER_KEY }),
      }).catch(() => {});
    }
    setPendingSetup(null);
    setShowSetup(false);
  };

  const resetTeamKey = async () => {
    const ok = window.confirm('Reset team stream key?\n\nThis deletes the current Cloudflare Stream Live Input. You\'ll need to paste a NEW RTMPS URL + Stream Key into the Insta360 / OBS app. Only do this if the key was compromised or you want a fresh start.');
    if (!ok) return;
    if (teamLiveInput?.uid) {
      try {
        await fetch(`${R2_UPLOAD_WORKER}/live-input/${teamLiveInput.uid}/delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: R2_WORKER_KEY }),
        });
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
function ActiveGameView({ game, roster, pendingEvent, onSelectEvent, onSelectPlayer, onResolveOppGoal, onConfirmGK, onSwapGK, onMovePosition, onResetFormation, onCancelEvent, onUndo, onPauseHalfTime, onStartSecondHalf, onResumeFirstHalf, onPauseClock, onResumeClock, onEnd, onBack, onBulkReplaceLineup, tick }) {
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

  const inFirstHalf = game.period === 1 && game.clockRunning !== false;
  const inHalfTimeBreak = game.period === 1 && game.clockRunning === false;
  const inSecondHalf = game.period === 2;

  // Halftime bulk-lineup modal: coach taps players to flip on/off the field
  // in a single pass instead of doing one SUB at a time.
  const [halftimePicker, setHalftimePicker] = useState(false);

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
          <button onClick={onBack} className="text-white/70 active:scale-95">
            <ChevronLeft className="w-6 h-6" />
          </button>
          <div className="text-center flex-1">
            <div className="text-xs text-white font-bold tracking-wide truncate">
              {game.tournament || 'Festival'}
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

function ZonePicker({ event, onPick, onSkip, onCancel }) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <div className="text-xs text-stone-400 font-bold tracking-widest">WHERE?</div>
          <div className="font-display text-3xl flex items-center gap-2">
            <span>{event.emoji}</span>
            <span>{event.label}</span>
          </div>
        </div>
        <button onClick={onCancel} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="mb-2 text-[11px] text-stone-400 tracking-wider font-bold flex items-center justify-between">
        <span>⬆ OUR ATTACK</span>
        <span className="text-stone-500">(tap a third)</span>
      </div>

      <div className="grid grid-cols-3 grid-rows-3 gap-2 mb-3 aspect-[3/4]" style={{ direction: 'ltr' }}>
        {/* Render top row first = attacking third (row 2 in ZONES) so the field reads goal-up. */}
        {['A', 'M', 'D'].flatMap(band =>
          ['L', 'C', 'R'].map(side => {
            const id = `${band}-${side}`;
            const tone = band === 'A'
              ? 'bg-lime-900/40 border-lime-700 text-lime-200 hover:border-lime-500'
              : band === 'M'
              ? 'bg-stone-800 border-stone-700 text-stone-200 hover:border-stone-500'
              : 'bg-red-950/40 border-red-900 text-red-200 hover:border-red-700';
            return (
              <button
                key={id}
                onClick={() => onPick(id)}
                className={`rounded-xl border-2 ${tone} active:scale-[0.97] transition flex flex-col items-center justify-center font-display`}
              >
                <div className="text-2xl">{band === 'A' ? '🥅' : band === 'M' ? '•' : '🛡️'}</div>
                <div className="text-[11px] tracking-widest font-bold">{ZONE_LABEL[id]}</div>
              </button>
            );
          })
        )}
      </div>

      <button
        onClick={onSkip}
        className="w-full bg-stone-900 text-stone-300 border border-stone-700 font-display text-sm py-3 rounded-xl active:scale-[0.98] transition"
      >
        SKIP ZONE
      </button>
    </div>
  );
}

function PressurePicker({ event, onPick, onSkip, onCancel }) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <div className="text-xs text-stone-400 font-bold tracking-widest">PRESSURE?</div>
          <div className="font-display text-3xl flex items-center gap-2">
            <span>{event.emoji}</span>
            <span>{event.label}</span>
          </div>
        </div>
        <button onClick={onCancel} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
          <X className="w-5 h-5" />
        </button>
      </div>

      <button
        onClick={() => onPick('open')}
        className="mb-2 w-full bg-lime-900/40 text-lime-200 border-2 border-lime-700 font-display text-2xl py-6 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
      >
        <span className="text-3xl">🆓</span>
        <span>OPEN</span>
      </button>
      <button
        onClick={() => onPick('pressure')}
        className="mb-3 w-full bg-orange-900/40 text-orange-200 border-2 border-orange-700 font-display text-2xl py-6 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
      >
        <span className="text-3xl">⚡</span>
        <span>UNDER PRESSURE</span>
      </button>
      <button
        onClick={onSkip}
        className="w-full bg-stone-900 text-stone-300 border border-stone-700 font-display text-sm py-3 rounded-xl active:scale-[0.98] transition"
      >
        SKIP
      </button>
    </div>
  );
}

function DecisionPicker({ event, onPick, onSkip, onCancel }) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <div className="text-xs text-stone-400 font-bold tracking-widest">RIGHT CHOICE?</div>
          <div className="font-display text-3xl flex items-center gap-2">
            <span>{event.emoji}</span>
            <span>{event.label}</span>
          </div>
        </div>
        <button onClick={onCancel} className="w-11 h-11 rounded-full bg-stone-800 flex items-center justify-center active:scale-95">
          <X className="w-5 h-5" />
        </button>
      </div>

      <button
        onClick={() => onPick('good')}
        className="mb-2 w-full bg-lime-900/40 text-lime-200 border-2 border-lime-700 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
      >
        <span className="text-3xl">🎯</span>
        <span>GOOD CALL</span>
      </button>
      <button
        onClick={() => onPick('forced')}
        className="mb-2 w-full bg-amber-900/40 text-amber-200 border-2 border-amber-700 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
      >
        <span className="text-3xl">🤔</span>
        <span>FORCED / 50–50</span>
      </button>
      <button
        onClick={() => onPick('bad')}
        className="mb-3 w-full bg-red-900/40 text-red-200 border-2 border-red-700 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
      >
        <span className="text-3xl">❌</span>
        <span>POOR CHOICE</span>
      </button>
      <button
        onClick={onSkip}
        className="w-full bg-stone-900 text-stone-300 border border-stone-700 font-display text-sm py-3 rounded-xl active:scale-[0.98] transition"
      >
        SKIP
      </button>
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

  // Track viewer presence
  useEffect(() => {
    const isLive = gameInfo && gameInfo.status === 'active';
    const docId = trackViewer(isLive ? 'watch_live' : 'watch_replay', gameInfo?.gameId);
    return () => { untrackViewer(docId); };
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
        <div className="text-sm font-bold">{ev.label}</div>
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
            {!player && !ev.requiresPlayer && event.type !== 'OPP_GOAL' && <div className="text-xs text-stone-400">No player</div>}
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
  // Fixed U10 7v7 pitch dimensions — calibration is per-game now.
  const LENGTH_M = 50;
  const WIDTH_M = 35;
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);

  // Push a history entry so iOS swipe-back / Android back closes the modal
  // instead of leaving the coach app. Also lock body scroll so the page
  // underneath retains its scroll position when this modal closes.
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    window.history.pushState({ modal: 'calibrate' }, '');
    const onPop = () => onCancel();
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      window.scrollTo(0, scrollY);
      if (window.history.state && window.history.state.modal === 'calibrate') {
        window.history.back();
      }
    };
  }, [onCancel]);

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
          <p className="text-[10px] text-stone-500 text-center">Pitch: U10 7v7 (50 × 35 m). Calibration saved per game.</p>
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

function FilmRoomView({ games, roster, onBack }) {
  const [openGameId, setOpenGameId] = useState(null);
  const [showSeason, setShowSeason] = useState(false);
  const finished = useMemo(() => (
    (games || [])
      .filter(g => g.status === 'finished')
      .sort((a, b) => (b.date || '').localeCompare(a.date || '') || (b.endedAt || 0) - (a.endedAt || 0))
  ), [games]);
  const openGame = finished.find(g => g.id === openGameId) || null;

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

        {/* SEASON AGGREGATE — opens season-wide rollup */}
        <button
          onClick={() => setShowSeason(true)}
          disabled={finished.length === 0}
          className={`w-full bg-stone-900 border border-stone-800 rounded-2xl p-4 flex items-center gap-3 transition ${finished.length === 0 ? 'opacity-60 cursor-not-allowed' : 'hover:border-lime-500/40 active:scale-[0.99]'}`}
        >
          <div className="w-10 h-10 rounded-lg bg-lime-500/15 text-lime-300 flex items-center justify-center text-xl">📈</div>
          <div className="flex-1 text-left">
            <div className="font-display text-base">SEASON ANALYTICS</div>
            <div className="text-xs text-stone-400">Aggregate across past games {finished.length > 0 ? `· ${finished.length} game${finished.length === 1 ? '' : 's'}` : '· no data yet'}</div>
          </div>
          {finished.length > 0 && <span className="text-[10px] font-bold text-lime-400">OPEN →</span>}
        </button>

        {finished.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
            No finished games yet. Analytics show up here after you end a match.
          </div>
        ) : (
          <div className="space-y-2">
            {finished.map(g => {
              const result = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
              const resultClass = result === 'W' ? 'bg-lime-500/15 text-lime-300 border-lime-500/40'
                               : result === 'L' ? 'bg-red-500/15 text-red-300 border-red-500/40'
                                                : 'bg-stone-500/15 text-stone-300 border-stone-500/40';
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
                      {g.tournament && <TournamentChip value={g.tournament} />}
                      <span>{formatDate(g.date)}</span>
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
        )}
      </div>

      {openGame && (
        <AnalyticsPanel
          game={openGame}
          roster={roster}
          onClose={() => setOpenGameId(null)}
          onSeekVideo={() => setOpenGameId(null)}
        />
      )}

      {showSeason && (
        <SeasonAnalyticsView
          games={finished}
          roster={roster}
          onClose={() => setShowSeason(false)}
        />
      )}
    </div>
  );
}

/* ---------- SEASON ANALYTICS ----------
 * Aggregates per-game analytics/v1 docs into season-to-date and last-N rollups.
 * Best practice for U10 (15-25 game season): show both SEASON and LAST 5 in a
 * tab toggle so coaches compare all-time vs. recent form with one tap.
 * Last 3 is too noisy (one outlier swings 33%); last 10 overlaps season-to-date.
 */
const ROLLING_WINDOW = 5;

function SeasonAnalyticsView({ games, roster, onClose }) {
  const [docs, setDocs] = useState({}); // gameId -> analytics doc
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState('season'); // 'season' | 'rolling'
  const [sortKey, setSortKey] = useState('distance');
  const [expandedId, setExpandedId] = useState(null);

  // Push history so swipe-back closes modal
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    window.history.pushState({ modal: 'seasonAnalytics' }, '');
    const onPop = () => onClose();
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      window.scrollTo(0, scrollY);
      if (window.history.state && window.history.state.modal === 'seasonAnalytics') {
        window.history.back();
      }
    };
  }, [onClose]);

  // Fetch analytics/v1 for every finished game in parallel.
  useEffect(() => {
    if (!window.fbDb || !games || games.length === 0) { setLoading(false); return; }
    let cancelled = false;
    Promise.all(games.map(g => (
      window.fbDb.collection('teams').doc('main')
        .collection('games').doc(g.id)
        .collection('analytics').doc('v1').get()
        .then(snap => [g.id, snap.exists ? snap.data() : null])
        .catch(() => [g.id, null])
    ))).then(results => {
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

  // Per-player aggregate: avg minutes, total/avg distance, top speed (max),
  // avg sprints, avg thirds-pct. Also collects per-game series for sparklines.
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
          row = { pid, games: 0, minutes: 0, distance: 0, topSpeed: 0, sprints: 0,
                  attPct: 0, midPct: 0, defPct: 0,
                  distSeries: [], speedSeries: [], sprintSeries: [] };
          byPid.set(pid, row);
        }
        row.games += 1;
        row.minutes += s.minutes_played || 0;
        row.distance += s.distance_m || 0;
        row.topSpeed = Math.max(row.topSpeed, s.top_speed_ms || 0);
        row.sprints += s.sprint_count || 0;
        row.attPct += s.pct_attacking_third || 0;
        row.midPct += s.pct_middle_third || 0;
        row.defPct += s.pct_defensive_third || 0;
        row.distSeries.push(s.distance_m || 0);
        row.speedSeries.push((s.top_speed_ms || 0) * 3.6);
        row.sprintSeries.push(s.sprint_count || 0);
      });
    });
    return [...byPid.values()].map(r => ({
      ...r,
      avgMin: r.games ? r.minutes / r.games : 0,
      avgDist: r.games ? r.distance / r.games : 0,
      topSpeedKmh: r.topSpeed * 3.6,
      avgSprints: r.games ? r.sprints / r.games : 0,
      avgAttPct: r.games ? r.attPct / r.games : 0,
      avgMidPct: r.games ? r.midPct / r.games : 0,
      avgDefPct: r.games ? r.defPct / r.games : 0,
    }));
  }, [windowGames, docs]);

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

  const sortedPlayers = useMemo(() => {
    const key = sortKey;
    return [...playerAgg].sort((a, b) => {
      const av = key === 'name' ? '' : (a[key] || 0);
      const bv = key === 'name' ? '' : (b[key] || 0);
      if (key === 'name') {
        const ap = roster.find(r => r.id === a.pid)?.name || '';
        const bp = roster.find(r => r.id === b.pid)?.name || '';
        return ap.localeCompare(bp);
      }
      return bv - av;
    });
  }, [playerAgg, sortKey, roster]);

  const playerName = (pid) => {
    const p = roster.find(r => r.id === pid);
    if (!p) return pid || '—';
    return p.number != null ? `#${p.number} ${p.name}` : p.name;
  };

  return (
    <div className="fixed inset-0 bg-stone-950 z-50 overflow-y-auto">
      <div
        className="sticky top-0 stripes-bg text-white border-b border-stone-800 px-4 pb-3 flex items-center justify-between z-10"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <h2 className="font-display text-lg truncate pr-3">📈 SEASON ANALYTICS</h2>
        <button
          onClick={onClose}
          className="shrink-0 h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95"
        >
          CLOSE ✕
        </button>
      </div>

      {loading ? (
        <div className="p-10 text-center text-stone-400 animate-pulse">Loading season analytics…</div>
      ) : gamesWithAnalytics.length === 0 ? (
        <div className="m-4 p-4 bg-stone-900 border border-stone-800 rounded-xl text-sm text-stone-300">
          No analytics docs found yet for any finished games. Run <code className="text-lime-400">./run_analytics.sh &lt;gameId&gt;</code> on your Mac first.
        </div>
      ) : (
        <div className="p-4 space-y-5 max-w-3xl mx-auto">
          {/* Window toggle */}
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-1.5 flex gap-1">
            <button
              onClick={() => setMode('season')}
              className={`flex-1 py-2 rounded-xl font-display text-sm transition ${mode === 'season' ? 'bg-lime-500 text-stone-950' : 'text-stone-300 hover:bg-stone-800'}`}
            >
              SEASON · {gamesWithAnalytics.length}
            </button>
            <button
              onClick={() => setMode('rolling')}
              className={`flex-1 py-2 rounded-xl font-display text-sm transition ${mode === 'rolling' ? 'bg-lime-500 text-stone-950' : 'text-stone-300 hover:bg-stone-800'}`}
            >
              LAST {ROLLING_WINDOW}
            </button>
          </div>
          {fellBackToSeason && (
            <div className="text-xs text-amber-400 -mt-3 text-center">
              Need {ROLLING_WINDOW - gamesWithAnalytics.length} more game{(ROLLING_WINDOW - gamesWithAnalytics.length) === 1 ? '' : 's'} for rolling window — showing season instead.
            </div>
          )}

          {/* Team rollup */}
          <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
            <div className="text-xs text-stone-500 uppercase mb-3">Team — {mode === 'season' ? 'season' : `last ${windowGames.length}`}</div>
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

          {/* Per-player rollup */}
          <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs text-stone-500 uppercase">Players</div>
              <div className="flex gap-1 text-[10px]">
                {[
                  ['avgDist', 'DIST'],
                  ['topSpeedKmh', 'TOP'],
                  ['avgSprints', 'SPR'],
                  ['avgMin', 'MIN'],
                  ['name', 'A-Z'],
                ].map(([k, label]) => (
                  <button
                    key={k}
                    onClick={() => setSortKey(k)}
                    className={`px-1.5 py-0.5 rounded font-bold ${sortKey === k ? 'bg-lime-500 text-stone-950' : 'bg-stone-800 text-stone-400 hover:text-stone-200'}`}
                  >{label}</button>
                ))}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-stone-500 border-b border-stone-800">
                    <th className="text-left py-1 pr-2">Player</th>
                    <th className="text-right py-1 px-1">GP</th>
                    <th className="text-right py-1 px-1">Min</th>
                    <th className="text-right py-1 px-1">Dist/g</th>
                    <th className="text-right py-1 px-1">Top</th>
                    <th className="text-right py-1 px-1">Spr/g</th>
                    <th className="text-right py-1 pl-1">Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedPlayers.map(p => {
                    const expanded = expandedId === p.pid;
                    return (
                      <React.Fragment key={p.pid}>
                        <tr
                          onClick={() => setExpandedId(expanded ? null : p.pid)}
                          className="border-b border-stone-800/50 cursor-pointer hover:bg-stone-800/40"
                        >
                          <td className="py-1.5 pr-2 truncate max-w-[120px]">{playerName(p.pid)}</td>
                          <td className="text-right px-1 tabular-nums">{p.games}</td>
                          <td className="text-right px-1 tabular-nums">{p.avgMin.toFixed(0)}</td>
                          <td className="text-right px-1 tabular-nums">{p.avgDist.toFixed(0)}m</td>
                          <td className="text-right px-1 tabular-nums">{p.topSpeedKmh.toFixed(1)}</td>
                          <td className="text-right px-1 tabular-nums">{p.avgSprints.toFixed(1)}</td>
                          <td className="text-right pl-1"><Sparkline values={p.distSeries} color="#a3e635" /></td>
                        </tr>
                        {expanded && (
                          <tr className="bg-stone-800/30">
                            <td colSpan={7} className="px-2 py-3">
                              <div className="grid grid-cols-3 gap-3">
                                <SparkBlock label="Distance (m)" values={p.distSeries} color="#a3e635" fmt={v => v.toFixed(0)} />
                                <SparkBlock label="Top speed (km/h)" values={p.speedSeries} color="#60a5fa" fmt={v => v.toFixed(1)} />
                                <SparkBlock label="Sprints" values={p.sprintSeries} color="#fbbf24" fmt={v => v.toFixed(0)} />
                              </div>
                              <div className="mt-3 pt-3 border-t border-stone-700 grid grid-cols-3 gap-2 text-center text-[11px]">
                                <div><div className="text-stone-500 text-[9px] uppercase">Att third</div><div className="tabular-nums">{p.avgAttPct.toFixed(0)}%</div></div>
                                <div><div className="text-stone-500 text-[9px] uppercase">Mid third</div><div className="tabular-nums">{p.avgMidPct.toFixed(0)}%</div></div>
                                <div><div className="text-stone-500 text-[9px] uppercase">Def third</div><div className="tabular-nums">{p.avgDefPct.toFixed(0)}%</div></div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="text-[10px] text-stone-500 mt-2">Tap a row to expand sparkline trend across the {mode === 'season' ? 'season' : `last ${windowGames.length} games`}.</div>
          </section>
        </div>
      )}
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

function BroadcastVideoPlayer({ url, doc, label, onClose, timeKey }) {
  const videoRef = useRef(null);
  const [now, setNow] = useState(0);
  // Fit (letterbox, whole frame) vs Fill (crop to fill the screen). The reel is
  // 16:9 but phones in landscape are wider (~20:9), so Fit leaves side bars.
  const [fillMode, setFillMode] = useState(false);

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

  // Push history entry so swipe-back closes the modal.
  useEffect(() => {
    window.history.pushState({ modal: 'broadcast' }, '');
    const onPop = () => onClose();
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      if (window.history.state && window.history.state.modal === 'broadcast') {
        window.history.back();
      }
    };
  }, [onClose]);

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
  for (const e of allEvents) {
    if (e.t <= now + 0.01) {
      if (e.ourScoreAfter != null) ourScore = e.ourScoreAfter;
      if (e.oppScoreAfter != null) oppScore = e.oppScoreAfter;
      if (e.period) currentPeriod = e.period;
      if (e.elapsed != null) currentElapsed = e.elapsed;
    } else break;
  }
  // Row-2 label: half + minute, matching the live in-game scorebug
  // ("1ST · 27'"). Minute is derived from the latest passed event's clock.
  const halfLenMin = Number(doc?.halfLengthMin) || 25;
  const minuteNum = Math.max(1, Math.floor((currentElapsed || 0) / 60) + 1)
    + (currentPeriod === 2 ? halfLenMin : 0);
  const statusLabel = `${currentPeriod === 2 ? '2ND' : '1ST'} · ${minuteNum}'`;

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
        const entry = us.get(key) || { num: e.jerseyNumber, first: e.playerFirstName || 'Goal', minutes: [] };
        entry.minutes.push(minStr);
        us.set(key, entry);
      } else if (isOppGoal) {
        // Opponent: we don't know scorer name — just collect minutes under "OPP".
        const entry = them.get('opp') || { num: null, first: null, minutes: [] };
        entry.minutes.push(minStr);
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

      {/* Floating controls — top-right overlay (replaces the old solid band). */}
      <div
        className="absolute z-20 flex items-center gap-2"
        style={{ right: 'max(env(safe-area-inset-right, 0px), 12px)', top: 'max(env(safe-area-inset-top, 0px), 12px)' }}
      >
        <span className="hidden sm:block text-white/85 font-display text-xs truncate max-w-[34vw] pr-1" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.8)' }}>{label}</span>
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
function BroadcastGoalCard({ elapsed, holdEnd, homeName, awayName, homeColor, awayColor, ourScore, oppScore, scorers, scoringSide }) {
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
    return (
      <div key={`${side}-${label}`} className={`flex ${side === 'us' ? 'justify-start' : 'justify-end'} items-baseline gap-3 text-sm`}>
        {side === 'us' && <span className="font-display tracking-wide text-white">{label}</span>}
        <span className="tabular-nums text-stone-300">{s.minutes.join(", ")}</span>
        {side === 'them' && <span className="font-display tracking-wide text-white">{label}</span>}
      </div>
    );
  };

  return (
    <div
      className="absolute inset-0 pointer-events-none select-none flex items-center justify-center"
      style={{ opacity, transition: 'opacity 80ms linear' }}
    >
      <div
        className="rounded-xl border border-black/60 shadow-2xl overflow-hidden"
        style={{
          background: 'rgba(0,0,0,0.82)',
          backdropFilter: 'blur(8px)',
          width: 'min(92vw, 720px)',
          transform: `scale(${scale})`,
          transformOrigin: 'center',
          transition: 'transform 80ms linear',
        }}
      >
        {/* Top banner */}
        <div className="px-4 py-2 text-center text-[11px] tracking-[0.3em]" style={{ background: scoringSide === 'us' ? homeColor : awayColor, color: readableTextOn(scoringSide === 'us' ? homeColor : awayColor) }}>
          ⚽ GOAL
        </div>

        {/* Score line */}
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-4 text-white">
          <div className="flex items-center gap-2 min-w-0">
            <span className="inline-block w-2 h-8 rounded-sm shrink-0" style={{ background: homeColor }} />
            <span className="font-display text-lg sm:text-2xl tracking-wider leading-tight break-words">{homeName}</span>
          </div>
          <div className="font-display text-3xl sm:text-5xl tabular-nums text-center px-2">
            <span className={scoringSide === 'us' ? 'text-lime-300' : ''}>{ourScore}</span>
            <span className="mx-2 text-stone-500">—</span>
            <span className={scoringSide === 'them' ? 'text-red-300' : ''}>{oppScore}</span>
          </div>
          <div className="flex items-center gap-2 min-w-0 justify-end">
            <span className="font-display text-lg sm:text-2xl tracking-wider leading-tight break-words text-right">{awayName}</span>
            <span className="inline-block w-2 h-8 rounded-sm shrink-0" style={{ background: awayColor }} />
          </div>
        </div>

        {/* Scorers lists */}
        {(scorers.us.length > 0 || scorers.them.length > 0) && (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-5 pb-4 border-t border-white/10 pt-3">
            <div className="space-y-1">
              {scorers.us.map(s => renderLine(s, 'us'))}
            </div>
            <div className="space-y-1">
              {scorers.them.map(s => renderLine(s, 'them'))}
            </div>
          </div>
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
  const max = Math.max(1, ...grid);
  // Build display rows top→bottom = opponent-net → our-net (data row index high→low).
  const displayRows = [];
  for (let r = rows - 1; r >= 0; r--) {
    const cells = [];
    for (let c = 0; c < cols; c++) cells.push(grid[r * cols + c] || 0);
    displayRows.push(cells);
  }
  const heat = (v) => {
    if (v <= 0) return 'transparent';
    const a = Math.pow(v / max, 0.6); // gamma so low cells stay visible
    // green → yellow → red ramp by intensity
    if (a < 0.5) {
      const t = a / 0.5; // green→yellow
      return `rgba(${Math.round(120 + 135 * t)}, ${Math.round(200)}, 40, ${0.25 + 0.55 * a})`;
    }
    const t = (a - 0.5) / 0.5; // yellow→red
    return `rgba(255, ${Math.round(200 - 160 * t)}, 30, ${0.45 + 0.5 * a})`;
  };
  return (
    <div className="flex flex-col items-center gap-1 py-2">
      <div className="text-[9px] tracking-widest text-stone-500">OPPONENT NET ↑</div>
      <div
        className="relative rounded-md overflow-hidden border border-emerald-200/20"
        style={{ width: 132, aspectRatio: `${cols} / ${rows}`, background: 'linear-gradient(180deg,#14532d,#0b3d22)' }}
      >
        {/* pitch markings */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute left-0 right-0 top-1/2 h-px bg-white/25" />
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-7 h-7 rounded-full border border-white/25" />
          <div className="absolute left-[30%] right-[30%] top-0 h-[10%] border-x border-b border-white/20" />
          <div className="absolute left-[30%] right-[30%] bottom-0 h-[10%] border-x border-t border-white/20" />
        </div>
        {/* heat cells */}
        <div className="absolute inset-0 grid" style={{ gridTemplateColumns: `repeat(${cols},1fr)`, gridTemplateRows: `repeat(${rows},1fr)` }}>
          {displayRows.flatMap((cells, ri) => cells.map((v, ci) => (
            <div key={`${ri}-${ci}`} style={{ background: heat(v) }} />
          )))}
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
  const [showAll, setShowAll] = useState(false); // false = only unassigned to do
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
      if (savedSet.has(String(t.tracklet_id))) return false; // already decided + saved
      if (showAll) return true;
      return !t.player_id; // default view: only the still-unassigned ones
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

  const rosterById = Object.fromEntries(roster.map(p => [p.id, p]));
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
  const fmt = (s) => { const m = Math.floor((s || 0) / 60), ss = Math.round((s || 0) % 60); return `${m}:${String(ss).padStart(2, '0')}`; };

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
        <button
          onClick={() => setShowAll(v => !v)}
          className={`shrink-0 h-7 px-2.5 rounded-full text-[11px] border active:scale-95 ${showAll ? 'bg-white/15 text-white border-white/25' : 'bg-stone-800 text-stone-400 border-stone-700'}`}
        >
          {showAll ? 'All segments' : 'Unassigned only'}
        </button>
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
                  <span className="text-stone-200 text-sm font-semibold truncate">{pname(tl.player_id)}</span>
                  {confBadge(tl.confidence)}
                  {tl.status === 'coach' && <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-sky-500/20 text-sky-300">COACH</span>}
                </div>
                <div className="text-[11px] text-stone-500 mt-0.5">
                  {Math.round(tl.minutes || 0)} min · {fmt(tl.t_start_s)}–{fmt(tl.t_end_s)} <span className="text-stone-700">· #{tl.tracklet_id}</span>
                </div>
                <select
                  value={selFor(tl)}
                  onChange={(e) => setSel(tl, e.target.value)}
                  className="mt-2 w-full bg-stone-800 border border-stone-700 rounded-lg px-2 py-1.5 text-sm text-stone-100 focus:outline-none focus:border-lime-500"
                >
                  <option value="__auto__">Auto: {pname(tl.player_id)}</option>
                  <optgroup label="Our players (this game)">
                    {sortedRoster.map(p => (
                      <option key={p.id} value={p.id}>{p.number != null ? `#${p.number} ` : ''}{p.name}</option>
                    ))}
                  </optgroup>
                  <optgroup label="Not our player">
                    <option value="__opp__">⚪ Opponent</option>
                    <option value="__ref__">🟨 Referee</option>
                    <option value="__other__">🚫 Coach / spectator / other</option>
                  </optgroup>
                </select>
              </div>
            </div>
          );
        })}
        {tracklets.length === 0 && (
          <div className="text-center text-stone-400 text-sm py-12">
            <div className="text-3xl mb-2">✅</div>
            {showAll ? 'Nothing left to review.' : 'All players identified — nothing unassigned left.'}
            {!showAll && allTracklets.some(t => t.player_id) && (
              <div className="mt-3">
                <button onClick={() => setShowAll(true)} className="text-xs text-lime-400 underline">
                  Show all segments to double-check assignments
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

function AnalyticsPanel({ game, roster, onClose, onSeekVideo, onDeleteVideos, onUpdateGame }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [clips, setClips] = useState([]);
  const [broadcastOpen, setBroadcastOpen] = useState(null); // 'tv_reel' | 'auto_highlights' | null
  const [openHeatmap, setOpenHeatmap] = useState(null); // player_id whose heatmap is expanded
  const [showFix, setShowFix] = useState(false); // IdentityFixView open?

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

  // Push a history entry so swipe-back closes the panel instead of leaving the app.
  // Also lock body scroll so the page underneath keeps its scroll position
  // when the modal closes (otherwise iOS resets to top).
  // IMPORTANT: run ONCE (empty deps) via a ref — keying on [onClose] re-ran the
  // cleanup (history.back) on every re-render (e.g. after Save → setGames), which
  // cascaded through the history stack and kicked the coach out to the dugout.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    window.history.pushState({ modal: 'analytics' }, '');
    const onPop = () => onCloseRef.current();
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      window.scrollTo(0, scrollY);
      if (window.history.state && window.history.state.modal === 'analytics') {
        window.history.back();
      }
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
  const teamKm = (pstats.reduce((s, p) => s + (p.distance_m || 0), 0) / 1000);
  const teamTopKmh = Math.max(0, ...pstats.map(p => (p.top_speed_ms || 0) * 3.6));
  const teamSprints = pstats.reduce((s, p) => s + (p.sprint_count || 0), 0);
  // minutes-weighted team thirds
  const _wsum = pstats.reduce((s, p) => s + (p.minutes_played || 0), 0) || 1;
  const teamThirds = ['pct_defensive_third', 'pct_middle_third', 'pct_attacking_third'].map(k =>
    pstats.reduce((s, p) => s + (p[k] || 0) * (p.minutes_played || 0), 0) / _wsum);
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
                <div className="text-[10px] tracking-widest text-stone-400">{(game.tournament || 'MATCH').toUpperCase()}{game.date ? ` · ${game.date}` : ''}</div>
                <div className="text-white font-display text-lg leading-tight truncate">Stompers <span className="text-stone-500">vs</span> {game.opponent || 'OPP'}</div>
              </div>
              <div className="text-right shrink-0 pl-3">
                <div className="font-display text-3xl text-white leading-none">{game.ourScore}<span className="text-stone-600">–</span>{game.oppScore}</div>
                <div className={`text-[10px] tracking-widest font-bold mt-0.5 ${result === 'WIN' ? 'text-lime-400' : result === 'LOSS' ? 'text-red-400' : 'text-stone-400'}`}>{result}</div>
              </div>
            </div>
            <div className="grid grid-cols-4 gap-2 mb-3">
              {[[teamKm.toFixed(1), 'KM TOTAL'], [teamTopKmh.toFixed(1), 'TOP KM/H'], [teamSprints, 'SPRINTS'], [pstats.length, 'PLAYERS']].map(([v, l]) => (
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
                <div className="text-[10px] tracking-widest text-stone-400 mb-2">FORMATION</div>
                {(doc.formation_snapshots || []).length ? doc.formation_snapshots.map(f => (
                  <div key={f.period} className="flex items-center gap-2 text-white"><span className="text-[10px] text-stone-500 w-7">{f.period === 2 ? '2ND' : '1ST'}</span><span className="font-display text-base">{f.label}</span></div>
                )) : <div className="text-stone-500 text-xs">—</div>}
              </div>
              <div className="rounded-xl border border-stone-700/60 bg-stone-900/60 p-3">
                <div className="text-[10px] tracking-widest text-stone-400 mb-2">TIME BY THIRD</div>
                <div className="flex h-2 rounded-full overflow-hidden mb-1">
                  <div style={{ width: `${teamThirds[2]}%`, background: '#a3e635' }} title="Attacking" />
                  <div style={{ width: `${teamThirds[1]}%`, background: '#eab308' }} />
                  <div style={{ width: `${teamThirds[0]}%`, background: '#ef4444' }} />
                </div>
                <div className="flex justify-between text-[9px] text-stone-500"><span className="text-lime-300">Att {teamThirds[2].toFixed(0)}</span><span className="text-yellow-300">Mid {teamThirds[1].toFixed(0)}</span><span className="text-red-300">Def {teamThirds[0].toFixed(0)}</span></div>
              </div>
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
                const conf = playerConf(s.player_id);
                const confColor = conf == null ? 'text-stone-500' : conf >= 0.8 ? 'text-lime-400' : conf >= 0.5 ? 'text-amber-400' : 'text-red-400';
                // Under-tracked: played real minutes but the assigned tracks carry
                // almost no movement (the pipeline only captured a sliver of their
                // play). Their distance/speed/sprints aren't real — flag, don't lie.
                const distPerMin = (s.distance_m || 0) / Math.max(s.minutes_played || 1, 1);
                const lowTrack = (s.minutes_played || 0) >= 5 && distPerMin < 15;
                return (
                  <div key={s.player_id} className="rounded-2xl border border-stone-800 bg-stone-900 p-4">
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <PlayerAvatar player={p} sizeClass="w-11 h-11" rounded="rounded-xl" textSize="text-lg" />
                        <div className="min-w-0">
                          <div className="text-white font-display text-base leading-tight truncate">{p?.name || s.player_id}</div>
                          <div className="flex items-center gap-2">
                            {p?.number != null && <span className="text-[10px] text-stone-400 tabular-nums">#{p.number}</span>}
                            <span className="text-[10px] tracking-widest text-lime-400">{posLabel(s)}</span>
                          </div>
                        </div>
                      </div>
                      <div className="text-right shrink-0 pl-2">
                        <div className="text-[9px] text-stone-500">IDENTITY</div>
                        <div className={`text-xs font-bold ${confColor}`}>{conf == null ? '—' : `● ${(conf * 100).toFixed(0)}%`}</div>
                        {lowTrack && <div className="text-[9px] font-bold text-amber-400 mt-1">⚠ LOW TRACKING</div>}
                      </div>
                    </div>
                    <div className="grid grid-cols-4 gap-2 mb-1">
                      {[[`${(s.minutes_played || 0).toFixed(0)}'`, 'MIN', false], [(s.distance_m || 0).toFixed(0), 'DIST m', true], [((s.top_speed_ms || 0) * 3.6).toFixed(1), 'TOP km/h', true], [s.sprint_count || 0, 'SPRINTS', true]].map(([v, l, movement]) => (
                        <div key={l} className={`rounded-xl border border-stone-700/60 p-2 text-center ${lowTrack && movement ? 'opacity-40' : ''}`} style={{ background: 'linear-gradient(160deg,#202024,#161618)' }}>
                          <div className="text-white font-display text-base leading-none">{lowTrack && movement ? '—' : v}</div>
                          <div className="text-[9px] text-stone-400 mt-1">{l}</div>
                        </div>
                      ))}
                    </div>
                    {lowTrack && (
                      <div className="text-[9px] text-amber-400/80 mb-2 leading-snug">
                        Played {(s.minutes_played || 0).toFixed(0)}′ but the camera only captured a sliver of this player — movement stats are unreliable. Use FIX IDS to rescue their tracks.
                      </div>
                    )}
                    <div className="mb-2">
                      <div className="flex h-2 rounded-full overflow-hidden">
                        <div style={{ width: `${s.pct_defensive_third || 0}%`, background: '#ef4444' }} />
                        <div style={{ width: `${s.pct_middle_third || 0}%`, background: '#eab308' }} />
                        <div style={{ width: `${s.pct_attacking_third || 0}%`, background: '#a3e635' }} />
                      </div>
                      <div className="flex justify-between text-[9px] text-stone-500 mt-1">
                        <span>Def {(s.pct_defensive_third || 0).toFixed(0)}%</span>
                        <span>Mid {(s.pct_middle_third || 0).toFixed(0)}%</span>
                        <span>Att {(s.pct_attacking_third || 0).toFixed(0)}%</span>
                      </div>
                    </div>
                    <div className="rounded-xl border border-stone-700/60 bg-stone-950/40">
                      <PlayerHeatmap grid={s.heatmap_grid} rows={s.heatmap_grid_rows} cols={s.heatmap_grid_cols} />
                    </div>
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
          onClose={() => setBroadcastOpen(null)}
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

  const goLive = () => {
    if (!R2_UPLOAD_WORKER) { setLiveErr('Worker URL not configured'); return; }
    setLiveBusy(true);
    setLiveErr(null);
    fetch(`${R2_UPLOAD_WORKER}/live-input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY, name: `stompers-${game.id}` }),
    })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || 'live-input failed')))
      .then((info) => {
        onUpdateGame({ liveInput: { ...info, createdAt: Date.now() } });
        setShowLiveCreds(true);
        setShowLive(true);
      })
      .catch((err) => setLiveErr(String(err)))
      .finally(() => setLiveBusy(false));
  };

  const stopLive = () => {
    if (!game.liveInput?.uid) { onUpdateGame({ liveInput: null }); return; }
    setLiveBusy(true);
    fetch(`${R2_UPLOAD_WORKER}/live-input/${game.liveInput.uid}/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY }),
    })
      .catch(() => {})
      .finally(() => {
        onUpdateGame({ liveInput: null });
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
    fetch(`${R2_UPLOAD_WORKER}/upload-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY, filename, contentType }),
    })
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
        <div className="text-center text-xs text-white/70 mb-1">{game.tournament || 'Festival'} · {formatDate(game.date)}</div>
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
              {showLiveCreds && game.liveInput.streamKey && (
                <div className="mt-3 bg-stone-900 border border-stone-800 rounded-xl p-3 space-y-2 text-xs">
                  <p className="text-stone-400 font-bold">📡 Stream from X5 / OBS using these credentials:</p>
                  <div>
                    <div className="text-stone-500">RTMPS Server</div>
                    <code className="block bg-stone-800 p-2 rounded text-lime-400 break-all">{game.liveInput.rtmpsUrl}</code>
                  </div>
                  <div>
                    <div className="text-stone-500">Stream Key</div>
                    <code className="block bg-stone-800 p-2 rounded text-lime-400 break-all">{game.liveInput.streamKey}</code>
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
          <h3 className="font-display text-xl mb-2">PERFORMANCE SCORES</h3>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800">
            {Object.entries(tally)
              .map(([pid, stats]) => {
                const min = Math.round((stats.seconds || 0) / 60);
                const player = roster.find(p => p.id === pid);
                // Treat as GK for scoring if they served any GK time in this game.
                const wasGKThisGame = (game.gkPlayerId === pid) || (game.gkChanges || []).some(c => c.gkPlayerId === pid);
                const gkExtras = wasGKThisGame ? gkExtrasForGame(pid, game) : undefined;
                // Blend GK vs outfield scoring by the share of this game's minutes spent in goal.
                const gkFraction = (wasGKThisGame && stats.seconds > 0) ? (gkExtras.secondsAsGK || 0) / stats.seconds : 0;
                const score = computePerformanceScore(pid, events, min, gkFraction, gkExtras, weights);
                return { pid, stats, min, score, gkExtras };
              })
              .sort((a, b) => b.score.overall - a.score.overall)
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
                      <PillarMini label="ATK" value={score.attacking} />
                      <PillarMini label="DEF" value={score.defending} />
                      <PillarMini label="DEC" value={score.decisions} />
                      <PillarMini label="INV" value={score.involvement} />
                    </div>
                  </div>
                );
              })}
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
  const [tournament, setTournament] = useState(game.tournament || 'Festival');
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

      <Field label="TOURNAMENT / COMPETITION">
        <input
          type="text"
          value={tournament}
          onChange={(e) => setTournament(e.target.value)}
          placeholder="Festival"
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
            tournament: tournament.trim() || 'Festival',
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
  const finished = games.filter(g => g.status === 'finished');

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

  // Season performance score per player
  const seasonScores = useMemo(() => {
    const map = {};
    for (const p of roster) {
      const s = stats[p.id];
      if (!s) continue;
      const min = Math.round((s.totalSeconds || 0) / 60);
      const allEvents = [];
      for (const g of finished) {
        for (const e of g.events) {
          if (e.type === 'SUB') continue;
          if (e.playerId === p.id) allEvents.push(e);
          else if (e.type === 'GIVE_GO' && e.partnerId === p.id) allEvents.push(e);
        }
      }
      // GK = roster position OR any game where they served as GK.
      let wasGKAnyGame = p.position === 'GK';
      let gkExtras;
      gkExtras = { oppGoalsConceded: 0, concededPenalty: 0, cleanSheets: 0 };
      for (const g of finished) {
        const servedAsGK = (g.gkPlayerId === p.id) || (g.gkChanges || []).some(c => c.gkPlayerId === p.id);
        if (servedAsGK) wasGKAnyGame = true;
        if (servedAsGK) {
          const gx = gkExtrasForGame(p.id, g);
          gkExtras.oppGoalsConceded += gx.oppGoalsConceded;
          gkExtras.concededPenalty += gx.concededPenalty;
          gkExtras.cleanSheets += gx.cleanSheets;
        }
      }
      // Blend by the share of season minutes spent in goal (part-time keepers
      // are scored proportionally, not as a full-season keeper).
      const gkFraction = (s.totalSeconds > 0) ? (s.gkSeconds || 0) / s.totalSeconds : (p.position === 'GK' ? 1 : 0);
      map[p.id] = computePerformanceScore(p.id, allEvents, min, gkFraction, wasGKAnyGame ? gkExtras : undefined, weights);
    }
    return map;
  }, [roster, finished, stats]);

  const sorted = [...roster].sort((a, b) => (seasonScores[b.id]?.overall || 0) - (seasonScores[a.id]?.overall || 0));
  const detailPlayer = roster.find(p => p.id === detailPlayerId);

  return (
    <div className="pb-24">
      <Header title="SEASON STATS" onBack={onBack} />

      <div className="px-4 pt-5">
        <div className="text-xs text-stone-400 mb-1">Based on {finished.length} completed game{finished.length === 1 ? '' : 's'}.</div>
        <div className="text-xs text-stone-400 italic mb-2">Sorted by performance score. Tap a player for full breakdown.</div>
        <details className="bg-stone-900 border border-stone-800 rounded-xl mb-3 text-stone-300">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-bold text-stone-200">ⓘ How this score works</summary>
          <div className="px-3 pb-3 text-xs text-stone-400 space-y-1.5">
            <p>It's a <b className="text-stone-200">per-20-minute development rating</b>, not a goal tally — a blend of four pillars:</p>
            <p><b className="text-lime-400">ATK</b> goals/assists/shots · <b className="text-sky-400">DEF</b> saves/blocks/wins · <b className="text-amber-400">DEC</b> smart passes vs turnovers · <b className="text-stone-200">INV</b> total involvement.</p>
            <p>Because it's a <i>rate</i>, more minutes spread a player's actions thinner, and turnovers count against the Decisions pillar. So a high-volume scorer who also gives the ball away can rank below a tidy player in fewer minutes — by design. Tune the weights in <b className="text-stone-200">⚙ Scoring</b>.</p>
          </div>
        </details>

        {roster.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
            Add players to track stats.
          </div>
        ) : (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl overflow-hidden">
            <div className="grid grid-cols-[2.5rem_1fr_2rem_2.5rem_2rem_2rem_3rem] gap-1 px-3 py-2 bg-stone-900 text-[9px] font-bold tracking-wider text-stone-300">
              <div>#</div>
              <div>PLAYER</div>
              <div className="text-center">GP</div>
              <div className="text-center">MIN</div>
              <div className="text-center">G</div>
              <div className="text-center">A</div>
              <div className="text-center">SCORE</div>
            </div>
            <div className="divide-y divide-stone-800">
              {sorted.map(p => {
                const s = stats[p.id] || {};
                const min = Math.round((s.totalSeconds || 0) / 60);
                const sc = seasonScores[p.id] || {};
                return (
                  <button
                    key={p.id}
                    onClick={() => setDetailPlayerId(p.id)}
                    className="w-full grid grid-cols-[2.5rem_1fr_2rem_2.5rem_2rem_2rem_3rem] gap-1 px-3 py-3 items-center text-left active:bg-stone-950 transition"
                  >
                    <PlayerAvatar player={p} sizeClass="w-9 h-9" textSize="text-base" numberClasses="bg-stone-900 text-stone-100" />
                    <div className="min-w-0">
                      <div className="font-bold text-sm truncate">{p.name}</div>
                      {p.position && <div className="text-[10px] text-stone-400 font-bold tracking-wider">{p.position}</div>}
                    </div>
                    <div className="text-center font-display text-sm tabular-nums text-stone-200">{s.gamesPlayed || 0}</div>
                    <div className="text-center font-display text-sm tabular-nums text-sky-700">{min}</div>
                    <div className="text-center font-display text-sm tabular-nums text-lime-700">{s.GOAL || 0}</div>
                    <div className="text-center font-display text-sm tabular-nums text-stone-200">{s.ASSIST || 0}</div>
                    <div className={`text-center font-display text-base tabular-nums ${(sc.overall || 0) >= 6 ? 'text-lime-600' : (sc.overall || 0) >= 3 ? 'text-stone-100' : 'text-stone-400'}`}>{sc.overall || 0}</div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="mt-4 bg-stone-900 rounded-xl p-3 text-xs text-stone-300">
          <span className="font-bold">SCORE</span> = weighted per-20min rate (ATK 30% · DEF 25% · DEC 30% · INV 15%) · <span className="font-bold">GP</span> Games · <span className="font-bold">MIN</span> Minutes · <span className="font-bold">G</span> Goals · <span className="font-bold">A</span> Assists
        </div>
      </div>

      {detailPlayer && (
        <PlayerStatsDetail
          player={detailPlayer}
          stats={stats[detailPlayer.id] || {}}
          score={seasonScores[detailPlayer.id] || {}}
          onClose={() => setDetailPlayerId(null)}
        />
      )}
    </div>
  );
}

/* ---------- PLAYER STATS DETAIL ---------- */
function PlayerStatsDetail({ player, stats, score, onClose }) {
  const min = Math.round((stats.totalSeconds || 0) / 60);
  const gkMin = Math.round((stats.gkSeconds || 0) / 60);
  const isGK = gkMin > 0 || player.position === 'GK';
  const rows = [
    { label: 'Games played', value: stats.gamesPlayed || 0 },
    { label: 'Minutes played', value: min },
    ...(isGK ? [
      { label: 'Minutes as GK', value: gkMin, accent: 'text-sky-400' },
      { label: 'Games as GK', value: stats.gamesAsGK || 0, accent: 'text-sky-400' },
      { label: 'Clean sheets', value: stats.cleanSheets || 0, accent: 'text-lime-500' },
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
    };
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
      </div>

      <div className="px-4 pt-4">
        {tab === 'actions' && <PointsSection group="points" />}
        {tab === 'pillars' && <PillarsSection />}
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

function ScheduleView({ schedule, roster, games = [], opponentSuggestions = [], onRenameOpponent, initialEditId, onConsumedInitialEditId, onSave, onBack, onEditSquad, askConfirm, showToast }) {
  const [opponent, setOpponent] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [tournament, setTournament] = useState('');
  const [location, setLocation] = useState('');
  const [field, setField] = useState('');
  // Optional match-day pre-fill — saved on the schedule item, used when the
  // coach taps START on match day to skip setup screens.
  const [isHome, setIsHome] = useState(true);
  const [halfLengthMin, setHalfLengthMin] = useState(25);
  const [homeColor, setHomeColor] = useState('#0a0a0a');
  const [awayColor, setAwayColor] = useState('#dc2626');
  const [squadIds, setSquadIds] = useState([]);
  const [pasteText, setPasteText] = useState('');
  const [parsed, setParsed] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [showSetup, setShowSetup] = useState(false);
  const [showOpponentManager, setShowOpponentManager] = useState(false);
  const formRef = React.useRef(null);
  const isLightColor = (hex) => {
    try {
      const h = (hex || '').replace('#', '');
      const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      return (0.299 * r + 0.587 * g + 0.114 * b) > 160;
    } catch { return false; }
  };

  // Keep our local squad state in sync when the parent saves an updated
  // schedule (e.g., after returning from the squad picker for the same item).
  React.useEffect(() => {
    if (!editingId) return;
    const item = schedule.find(s => s.id === editingId);
    if (item && Array.isArray(item.squadIds)) setSquadIds(item.squadIds);
  }, [schedule, editingId]);

  // Resume edit mode when returning from the squad-picker detour.
  React.useEffect(() => {
    if (!initialEditId) return;
    const item = schedule.find(s => s.id === initialEditId);
    if (item) {
      setEditingId(item.id);
      setOpponent(item.opponent || '');
      setDate(item.date || '');
      setTime(item.time || '');
      setTournament(item.tournament || '');
      setLocation(item.location || '');
      setField(item.field || '');
      setIsHome(typeof item.isHome === 'boolean' ? item.isHome : true);
      setHalfLengthMin(typeof item.halfLengthMin === 'number' ? item.halfLengthMin : 25);
      setHomeColor(item.homeColor || '#0a0a0a');
      setAwayColor(item.awayColor || '#dc2626');
      setSquadIds(Array.isArray(item.squadIds) ? item.squadIds : []);
      setShowSetup(true);
    }
    onConsumedInitialEditId?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialEditId]);

  const parseECSL = (text) => {
    // ECSL table rows: # | Date | KO | Field | Home | Away
    // When copied they come as tab-separated or multi-line
    const lines = text.trim().split('\n').map(l => l.trim()).filter(Boolean);
    const results = [];
    const months = { jan:0, feb:1, mar:2, apr:3, may:4, jun:5, jul:6, aug:7, sep:8, oct:9, nov:10, dec:11 };
    const year = new Date().getFullYear();

    for (const line of lines) {
      // Split by tab first, fall back to 2+ spaces
      let parts = line.split('\t');
      if (parts.length < 5) parts = line.split(/\s{2,}/);
      if (parts.length < 5) continue;

      // Try to find date pattern (like "May 9" or "Jun 15")
      let dateStr = '', timeStr = '', field = '', home = '', away = '';
      let idx = 0;
      // Skip game number if first part is just a number
      if (/^\d+$/.test(parts[0].trim())) idx = 1;

      dateStr = parts[idx]?.trim() || '';
      timeStr = parts[idx + 1]?.trim() || '';
      field = parts[idx + 2]?.trim() || '';
      home = parts[idx + 3]?.trim() || '';
      away = parts[idx + 4]?.trim() || '';

      // Parse date
      const dateMatch = dateStr.match(/([a-z]+)\s*(\d+)/i);
      if (!dateMatch) continue;
      const mon = months[dateMatch[1].toLowerCase().slice(0, 3)];
      if (mon === undefined) continue;
      const day = parseInt(dateMatch[2]);
      const isoDate = `${year}-${String(mon + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;

      // Parse time (3:00pm -> 15:00)
      const timeMatch = timeStr.match(/(\d+):(\d+)\s*(am|pm)/i);
      let isoTime = '';
      if (timeMatch) {
        let h = parseInt(timeMatch[1]);
        const m = parseInt(timeMatch[2]);
        if (timeMatch[3].toLowerCase() === 'pm' && h < 12) h += 12;
        if (timeMatch[3].toLowerCase() === 'am' && h === 12) h = 0;
        isoTime = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      }

      // Determine opponent (we are "Lasalle" or "LasalleGn" or "LSSC")
      const isUs = (name) => /lasalle|lssc|stompers/i.test(name);
      let opp = '';
      if (isUs(home)) opp = away;
      else if (isUs(away)) opp = home;
      else continue; // neither team is us, skip

      // Clean up opponent name (remove B10 prefix if present)
      opp = opp.replace(/^B\d+/i, '').trim() || opp;

      results.push({ date: isoDate, time: isoTime, opponent: opp, field });
    }
    return results;
  };

  const handleParse = () => {
    const results = parseECSL(pasteText);
    setParsed(results);
  };

  const handleImport = () => {
    if (!parsed || parsed.length === 0) return;
    const newItems = parsed.map(p => ({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      opponent: p.opponent,
      date: p.date,
      time: p.time,
      tournament: '',
      location: '',
      field: p.field || '',
    }));
    onSave([...schedule, ...newItems]);
    showToast?.(`✅ Added ${newItems.length} game${newItems.length === 1 ? '' : 's'}`);
    setPasteText('');
    setParsed(null);
  };

  const handleAdd = () => {
    if (!opponent.trim() || !date) return;
    const setupFields = {
      isHome,
      halfLengthMin,
      homeColor,
      awayColor,
      squadIds: Array.isArray(squadIds) ? squadIds : [],
    };
    if (editingId) {
      onSave(schedule.map(s => s.id === editingId ? {
        ...s,
        opponent: opponent.trim(),
        date,
        time: time || '',
        tournament: tournament.trim(),
        location: location.trim(),
        field: field.trim(),
        ...setupFields,
      } : s));
      showToast?.(`✏️ Updated vs ${opponent.trim()}`);
      resetForm();
      return;
    }
    const item = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      opponent: opponent.trim(),
      date,
      time: time || '',
      tournament: tournament.trim(),
      location: location.trim(),
      field: field.trim(),
      ...setupFields,
    };
    onSave([...schedule, item]);
    const dateLabel = new Date(date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' });
    showToast?.(`✅ Added vs ${item.opponent} · ${dateLabel}`);
    resetForm();
  };

  const resetForm = () => {
    setOpponent(''); setDate(''); setTime(''); setTournament(''); setLocation(''); setField('');
    setIsHome(true); setHalfLengthMin(25); setHomeColor('#0a0a0a'); setAwayColor('#dc2626'); setSquadIds([]);
    setShowSetup(false);
    setEditingId(null);
  };

  const handleEdit = (item) => {
    setEditingId(item.id);
    setOpponent(item.opponent || '');
    setDate(item.date || '');
    setTime(item.time || '');
    setTournament(item.tournament || '');
    setLocation(item.location || '');
    setField(item.field || '');
    setIsHome(typeof item.isHome === 'boolean' ? item.isHome : true);
    setHalfLengthMin(typeof item.halfLengthMin === 'number' ? item.halfLengthMin : 25);
    setHomeColor(item.homeColor || '#0a0a0a');
    setAwayColor(item.awayColor || '#dc2626');
    setSquadIds(Array.isArray(item.squadIds) ? item.squadIds : []);
    setShowSetup(
      typeof item.isHome === 'boolean' ||
      typeof item.halfLengthMin === 'number' ||
      !!item.homeColor ||
      !!item.awayColor ||
      (Array.isArray(item.squadIds) && item.squadIds.length > 0)
    );
    if (formRef.current) formRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleToggleCancel = (item) => {
    const wasCancelled = !!item.cancelled;
    onSave(schedule.map(s => s.id === item.id ? { ...s, cancelled: !s.cancelled } : s));
    showToast?.(wasCancelled ? `↩ Restored vs ${item.opponent}` : `⛔ Cancelled vs ${item.opponent}`);
  };

  const handleDelete = (id) => {
    const item = schedule.find(s => s.id === id);
    const label = item ? `vs ${item.opponent}${item.date ? ' on ' + new Date(item.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' }) : ''}` : 'this game';
    askConfirm(`Delete ${label} from the schedule?`, () => {
      if (editingId === id) resetForm();
      onSave(schedule.filter(s => s.id !== id));
      showToast?.(`🗑 Deleted ${label}`);
    }, { danger: true, yesLabel: 'DELETE' });
  };

  const sorted = [...schedule].sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));

  return (
    <div className="min-h-screen bg-stone-900 pb-8">
      <Header
        title="SCHEDULE"
        onBack={onBack}
        right={
          opponentSuggestions.length > 0 && onRenameOpponent ? (
            <button
              onClick={() => setShowOpponentManager(true)}
              className="text-xs font-display text-stone-400 hover:text-stone-100 px-2 py-1 rounded-lg border border-stone-800"
            >
              🏷️ OPPONENTS
            </button>
          ) : null
        }
      />

      {/* Import from ECSL */}
      <div className="px-4 pt-4">
        <div className="bg-stone-900 border border-stone-800 rounded-2xl p-4 space-y-3">
          <div className="font-display text-lg">IMPORT FROM ECSL</div>
          <p className="text-xs text-stone-400">Go to ecslsoccer.ca → Schedule → select your team → copy the table rows → paste below.</p>
          <textarea
            placeholder="Paste schedule rows here..."
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            rows={3}
            className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500 resize-none"
          />
          {pasteText.trim() && !parsed && (
            <button
              onClick={handleParse}
              className="w-full bg-blue-600 text-white font-display text-base py-2.5 rounded-xl active:scale-[0.98] transition"
            >
              PARSE
            </button>
          )}
          {parsed && (
            <div className="space-y-2">
              {parsed.length === 0 ? (
                <div className="text-sm text-red-600">Could not parse any games. Make sure you copied the table rows.</div>
              ) : (
                <>
                  <div className="text-xs font-semibold text-stone-300">Found {parsed.length} game{parsed.length > 1 ? 's' : ''}:</div>
                  {parsed.map((p, i) => (
                    <div key={i} className="bg-stone-950 rounded-lg px-3 py-2 text-sm">
                      <span className="font-bold">vs {p.opponent}</span> · {new Date(p.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' })}
                      {p.time && ` · ${formatTime12(p.time)}`}
                      {p.location && ` · ${p.location}`}
                    </div>
                  ))}
                  <button
                    onClick={handleImport}
                    className="w-full bg-lime-500 text-stone-100 font-display text-base py-2.5 rounded-xl active:scale-[0.98] transition"
                  >
                    ADD {parsed.length} GAME{parsed.length > 1 ? 'S' : ''}
                  </button>
                  <button
                    onClick={() => { setParsed(null); setPasteText(''); }}
                    className="w-full text-stone-400 text-sm py-1"
                  >
                    Cancel
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Add form */}
      <div className="px-4 pt-4 space-y-3" ref={formRef}>
        <div className={`bg-stone-900 border rounded-2xl p-4 space-y-3 ${editingId ? 'border-amber-500/60 ring-1 ring-amber-500/30' : 'border-stone-800'}`}>
          <div className="font-display text-lg flex items-center gap-2">
            {editingId ? <><span>✏️</span><span>EDIT GAME</span></> : <span>ADD GAME</span>}
          </div>
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
          <input
            type="text"
            placeholder="Tournament / Festival"
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

          {/* ---- Optional match-day pre-fill (squad, home/away, half, colors) ---- */}
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
          {showSetup && (
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

              {/* Half length */}
              <div>
                <div className="text-[10px] font-bold text-stone-400 tracking-widest mb-1">HALF LENGTH (MIN)</div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setHalfLengthMin(m => Math.max(1, m - 1))}
                    className="w-11 h-11 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-xl font-bold active:scale-95 transition"
                  >−</button>
                  <div className="flex-1 py-2 rounded-xl bg-stone-900 border-2 border-stone-800 text-center">
                    <div className="font-display text-2xl text-stone-100 tabular-nums leading-none">{halfLengthMin}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setHalfLengthMin(m => Math.min(99, m + 1))}
                    className="w-11 h-11 rounded-xl bg-stone-900 border-2 border-stone-800 text-stone-300 text-xl font-bold active:scale-95 transition"
                  >+</button>
                </div>
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
                    if (!editingId) {
                      showToast?.('💡 Save the game first, then edit its squad.');
                      return;
                    }
                    // Persist current form state so the edit isn't lost while
                    // we navigate away to the squad picker.
                    onSave(schedule.map(s => s.id === editingId ? {
                      ...s,
                      opponent: opponent.trim() || s.opponent,
                      date: date || s.date,
                      time: time || '',
                      tournament: tournament.trim(),
                      location: location.trim(),
                      field: field.trim(),
                      isHome, halfLengthMin, homeColor, awayColor,
                      squadIds: Array.isArray(squadIds) ? squadIds : [],
                    } : s));
                    onEditSquad?.({ id: editingId, opponent: opponent.trim() || 'Opponent', squadIds });
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
                        : (editingId ? 'Tap to pick squad' : 'Save game first, then pick squad')}
                    </span>
                  </span>
                  <span className="text-xs text-stone-400">{editingId ? 'EDIT →' : ''}</span>
                </button>
              </div>
            </div>
          )}

          <div className="flex gap-2">
            {editingId && (
              <button
                onClick={resetForm}
                className="flex-1 bg-stone-900 text-stone-300 border border-stone-700 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
              >
                CANCEL
              </button>
            )}
            <button
              onClick={handleAdd}
              disabled={!opponent.trim() || !date}
              className={`flex-1 font-display text-lg py-3 rounded-xl disabled:opacity-40 active:scale-[0.98] transition ${editingId ? 'bg-amber-500 text-stone-950 border-2 border-amber-400' : 'bg-stone-900 text-lime-400'}`}
            >
              {editingId ? 'SAVE CHANGES' : 'ADD TO SCHEDULE'}
            </button>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="px-4 pt-6">
        <h2 className="font-display text-xl mb-3">ALL GAMES ({sorted.length})</h2>
        {sorted.length === 0 ? (
          <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-stone-400 text-sm">
            No scheduled games yet.
          </div>
        ) : (
          <div className="space-y-2">
            {sorted.map(item => {
              const isPast = new Date(item.date + 'T' + (item.time || '23:59')) < new Date(new Date().toDateString());
              const isCancelled = !!item.cancelled;
              const isEditing = editingId === item.id;
              return (
                <div
                  key={item.id}
                  className={`bg-stone-900 border rounded-xl p-3 flex items-center gap-3 ${
                    isEditing ? 'border-amber-500/60 ring-1 ring-amber-500/30'
                    : isCancelled ? 'border-red-900/60 opacity-70'
                    : isPast ? 'border-stone-800 opacity-50'
                    : 'border-stone-800'
                  }`}
                >
                  <div className="w-10 h-10 rounded-lg bg-blue-500/15 text-blue-300 flex flex-col items-center justify-center text-xs font-bold leading-tight">
                    <span>{new Date(item.date + 'T12:00').toLocaleDateString('en', { month: 'short' }).toUpperCase()}</span>
                    <span className="text-base">{new Date(item.date + 'T12:00').getDate()}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className={`font-bold text-sm truncate ${isCancelled ? 'line-through text-stone-400' : ''}`}>vs {item.opponent}</div>
                    <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                      {isCancelled && (
                        <span className="inline-block bg-red-500/15 text-red-300 border border-red-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          CANCELLED
                        </span>
                      )}
                      {item.tournament && <TournamentChip value={item.tournament} />}
                      {item.time && <span>{formatTime12(item.time)}</span>}
                      {item.field && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          📍 {item.field}
                        </span>
                      )}
                      {Array.isArray(item.squadIds) && item.squadIds.length > 0 && (
                        <span className="inline-flex items-center gap-1 bg-lime-500/15 text-lime-300 border border-lime-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          👥 {item.squadIds.length}
                        </span>
                      )}
                      {(() => {
                        const ready = Array.isArray(item.squadIds) && item.squadIds.length > 0
                          && typeof item.isHome === 'boolean'
                          && typeof item.halfLengthMin === 'number'
                          && !!item.homeColor && !!item.awayColor;
                        return ready ? (
                          <span className="inline-block bg-lime-500/20 text-lime-200 border border-lime-400/60 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                            READY
                          </span>
                        ) : null;
                      })()}
                    </div>
                    {item.location && (
                      <a
                        href={item.location.startsWith('http') ? item.location : `https://maps.google.com/?q=${encodeURIComponent(item.location)}`}
                        target="_blank" rel="noopener noreferrer"
                        className="text-xs text-blue-400 underline flex items-center gap-1 mt-0.5"
                        onClick={e => e.stopPropagation()}
                      >
                        <MapPin className="w-3 h-3" /> {item.location.startsWith('http') ? 'View Map' : item.location}
                      </a>
                    )}
                  </div>
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEdit(item)}
                      className="w-8 h-8 rounded-full bg-amber-500/15 text-amber-400 flex items-center justify-center active:scale-90 transition text-sm"
                      title="Edit"
                    >
                      ✏️
                    </button>
                    <button
                      onClick={() => handleToggleCancel(item)}
                      className={`w-8 h-8 rounded-full flex items-center justify-center active:scale-90 transition text-sm ${isCancelled ? 'bg-lime-500/15 text-lime-400' : 'bg-stone-800 text-stone-300'}`}
                      title={isCancelled ? 'Restore' : 'Mark cancelled'}
                    >
                      {isCancelled ? '↻' : '🚫'}
                    </button>
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="w-8 h-8 rounded-full bg-red-500/10 text-red-500 flex items-center justify-center active:scale-90 transition"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {showOpponentManager && (
        <OpponentManagerModal
          opponentSuggestions={opponentSuggestions}
          games={games}
          schedule={schedule}
          onClose={() => setShowOpponentManager(false)}
          onRename={onRenameOpponent}
          askConfirm={askConfirm}
          showToast={showToast}
        />
      )}
    </div>
  );
}

/* ---------- OPPONENT MANAGER MODAL ---------- */
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

/* ---------- VIEWERS PANEL ---------- */
function ViewersPanel({ onBack }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState(7); // days
  const [tab, setTab] = useState('overview'); // overview | people | activity

  useEffect(() => {
    if (!window.fbDb) { setLoading(false); return; }

    const since = new Date();
    since.setDate(since.getDate() - timeRange);

    const unsub = window.fbDb.collection('viewerLog')
      .where('ts', '>=', since)
      .orderBy('ts', 'desc')
      .limit(500)
      .onSnapshot((snap) => {
        const items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
        setLogs(items);
        setLoading(false);
      }, () => setLoading(false));

    return unsub;
  }, [timeRange]);

  // --- Derived analytics ---
  const STALE_MS = 2 * 60 * 60 * 1000; // 2 hours
  const currentlyWatching = logs.filter(l =>
    (l.action === 'watch_live' || l.action === 'watch_replay') && !l.endTs &&
    l.ts && (Date.now() - (l.ts.toDate ? l.ts.toDate().getTime() : new Date(l.ts).getTime())) < STALE_MS
  );
  const uniqueEmails = [...new Set(logs.map(l => l.email))];
  const loginEvents = logs.filter(l => l.action === 'login');
  const watchEvents = logs.filter(l => l.action === 'watch_live' || l.action === 'watch_replay');
  const liveWatchEvents = logs.filter(l => l.action === 'watch_live');
  const replayWatchEvents = logs.filter(l => l.action === 'watch_replay');

  // Per-person aggregation
  const peopleMap = {};
  logs.forEach(l => {
    if (!peopleMap[l.email]) {
      peopleMap[l.email] = { email: l.email, name: l.name, photo: l.photo, logins: 0, watches: 0, liveWatches: 0, lastSeen: null };
    }
    const p = peopleMap[l.email];
    if (l.name && !p.name) p.name = l.name;
    if (l.photo && !p.photo) p.photo = l.photo;
    if (l.action === 'login') p.logins++;
    if (l.action === 'watch_live' || l.action === 'watch_replay') p.watches++;
    if (l.action === 'watch_live') p.liveWatches++;
    const ts = l.ts?.toDate ? l.ts.toDate() : (l.ts ? new Date(l.ts) : null);
    if (ts && (!p.lastSeen || ts > p.lastSeen)) p.lastSeen = ts;
  });
  const people = Object.values(peopleMap).sort((a, b) => (b.lastSeen || 0) - (a.lastSeen || 0));

  // Daily activity for sparkline (last N days)
  const dailyCounts = {};
  for (let i = 0; i < timeRange; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    dailyCounts[d.toLocaleDateString('en-CA')] = { logins: 0, watches: 0 };
  }
  logs.forEach(l => {
    const ts = l.ts?.toDate ? l.ts.toDate() : (l.ts ? new Date(l.ts) : null);
    if (!ts) return;
    const key = ts.toLocaleDateString('en-CA');
    if (dailyCounts[key]) {
      if (l.action === 'login') dailyCounts[key].logins++;
      else dailyCounts[key].watches++;
    }
  });
  const dailyArr = Object.entries(dailyCounts).sort(([a], [b]) => a.localeCompare(b));

  // Peak day
  const peakDay = dailyArr.reduce((best, [day, c]) => {
    const total = c.logins + c.watches;
    return total > best.total ? { day, total } : best;
  }, { day: '', total: 0 });

  const fmtTs = (ts) => {
    if (!ts) return '';
    const d = ts.toDate ? ts.toDate() : new Date(ts);
    return d.toLocaleString('en', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  };
  const fmtDay = (iso) => {
    const d = new Date(iso + 'T12:00');
    return d.toLocaleDateString('en', { weekday: 'short', month: 'short', day: 'numeric' });
  };
  const relativeTime = (date) => {
    if (!date) return 'Never';
    const diff = Date.now() - date.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  };

  // Mini bar chart
  const Sparkline = ({ data }) => {
    const maxVal = Math.max(...data.map(([, c]) => c.logins + c.watches), 1);
    return (
      <div className="flex items-end gap-[2px] h-10">
        {data.map(([day, c]) => {
          const total = c.logins + c.watches;
          const pct = (total / maxVal) * 100;
          const isToday = day === new Date().toLocaleDateString('en-CA');
          return (
            <div key={day} className="flex-1 flex flex-col items-center gap-0.5" title={`${fmtDay(day)}: ${total} events`}>
              <div
                className={`w-full rounded-sm min-h-[2px] transition-all ${isToday ? 'bg-lime-400' : 'bg-stone-600'}`}
                style={{ height: `${Math.max(pct, 5)}%` }}
              />
            </div>
          );
        })}
      </div>
    );
  };

  const TabBtn = ({ id, label, count }) => (
    <button
      onClick={() => setTab(id)}
      className={`px-3 py-1.5 text-xs font-bold tracking-wider rounded-lg transition ${
        tab === id ? 'bg-stone-800 text-white' : 'text-stone-500 hover:text-stone-300'
      }`}
    >
      {label}{count != null && <span className="ml-1 text-stone-500">({count})</span>}
    </button>
  );

  return (
    <div className="min-h-screen bg-stone-950 pb-20">
      <Header title="AUDIENCE" onBack={onBack} />

      {loading ? (
        <div className="p-6 text-center text-stone-500 animate-pulse">Loading analytics…</div>
      ) : (
        <div className="px-4 pt-4 space-y-5">

          {/* Time range selector */}
          <div className="flex gap-1.5 justify-center">
            {[7, 14, 30].map(d => (
              <button
                key={d}
                onClick={() => setTimeRange(d)}
                className={`px-3 py-1 text-[10px] font-bold tracking-wider rounded-full border transition ${
                  timeRange === d
                    ? 'bg-lime-500/20 border-lime-500/50 text-lime-400'
                    : 'border-stone-700 text-stone-500 hover:text-stone-300'
                }`}
              >
                {d}D
              </button>
            ))}
          </div>

          {/* Currently watching - live indicator */}
          {currentlyWatching.length > 0 && (
            <div className="bg-gradient-to-r from-lime-950/60 to-stone-900 border border-lime-800/40 rounded-2xl p-4">
              <div className="flex items-center gap-2 mb-3">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-lime-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-lime-500"></span>
                </span>
                <span className="text-xs font-bold text-lime-400 tracking-wider">WATCHING NOW ({currentlyWatching.length})</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {currentlyWatching.map(l => (
                  <div key={l.id} className="flex items-center gap-1.5 bg-stone-900/80 rounded-full pl-1 pr-3 py-1">
                    {l.photo ? (
                      <img src={l.photo} className="w-6 h-6 rounded-full ring-2 ring-lime-500/40" referrerPolicy="no-referrer" />
                    ) : (
                      <div className="w-6 h-6 rounded-full bg-stone-700 flex items-center justify-center text-[10px] text-stone-400 ring-2 ring-lime-500/40">
                        {(l.name || l.email || '?')[0].toUpperCase()}
                      </div>
                    )}
                    <span className="text-xs text-white font-medium truncate max-w-[100px]">{l.name || l.email?.split('@')[0]}</span>
                    <span className="text-[9px] text-lime-400/80 font-bold">{l.action === 'watch_live' ? '🔴' : '▶'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-white">{uniqueEmails.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">UNIQUE VIEWERS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-white">{loginEvents.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">TOTAL LOGINS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-lime-400">{liveWatchEvents.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">LIVE VIEWS</div>
            </div>
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-3xl font-display text-sky-400">{replayWatchEvents.length}</div>
              <div className="text-[10px] text-stone-500 font-bold tracking-wider mt-1">REPLAY VIEWS</div>
            </div>
          </div>

          {/* Activity sparkline */}
          {dailyArr.length > 1 && (
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] text-stone-500 font-bold tracking-wider">DAILY ACTIVITY</span>
                {peakDay.total > 0 && (
                  <span className="text-[10px] text-stone-500">Peak: <span className="text-white font-bold">{peakDay.total}</span> on {fmtDay(peakDay.day)}</span>
                )}
              </div>
              <Sparkline data={dailyArr} />
              <div className="flex justify-between mt-1.5">
                <span className="text-[9px] text-stone-600">{fmtDay(dailyArr[0]?.[0])}</span>
                <span className="text-[9px] text-stone-600">Today</span>
              </div>
            </div>
          )}

          {/* Tabs */}
          <div className="flex gap-1 bg-stone-900/50 rounded-lg p-1">
            <TabBtn id="overview" label="OVERVIEW" />
            <TabBtn id="people" label="PEOPLE" count={people.length} />
            <TabBtn id="activity" label="LOG" count={logs.length} />
          </div>

          {/* Tab: Overview — engagement summary */}
          {tab === 'overview' && (
            <div className="space-y-4">
              {/* Engagement funnel */}
              <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
                <h3 className="text-[10px] text-stone-500 font-bold tracking-wider mb-3">ENGAGEMENT FUNNEL</h3>
                <div className="space-y-2">
                  {[
                    { label: 'Signed in', count: uniqueEmails.length, color: 'bg-stone-500' },
                    { label: 'Watched content', count: [...new Set(watchEvents.map(l => l.email))].length, color: 'bg-sky-500' },
                    { label: 'Watched live', count: [...new Set(liveWatchEvents.map(l => l.email))].length, color: 'bg-lime-500' },
                  ].map(({ label, count, color }) => {
                    const pct = uniqueEmails.length > 0 ? Math.round((count / uniqueEmails.length) * 100) : 0;
                    return (
                      <div key={label} className="flex items-center gap-3">
                        <span className="text-xs text-stone-400 w-24 shrink-0">{label}</span>
                        <div className="flex-1 h-4 bg-stone-800 rounded-full overflow-hidden">
                          <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${Math.max(pct, 3)}%` }} />
                        </div>
                        <span className="text-xs font-bold text-white w-8 text-right">{count}</span>
                        <span className="text-[10px] text-stone-500 w-8">{pct}%</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Top viewers */}
              {people.length > 0 && (
                <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
                  <h3 className="text-[10px] text-stone-500 font-bold tracking-wider mb-3">TOP VIEWERS</h3>
                  <div className="space-y-2">
                    {people.sort((a, b) => b.watches - a.watches).slice(0, 5).map((p, i) => (
                      <div key={p.email} className="flex items-center gap-2">
                        <span className="text-[10px] text-stone-600 w-4">{i + 1}.</span>
                        {p.photo ? (
                          <img src={p.photo} className="w-6 h-6 rounded-full" referrerPolicy="no-referrer" />
                        ) : (
                          <div className="w-6 h-6 rounded-full bg-stone-700 flex items-center justify-center text-[10px] text-stone-400">
                            {(p.name || p.email || '?')[0].toUpperCase()}
                          </div>
                        )}
                        <span className="text-sm text-white flex-1 truncate">{p.name || p.email?.split('@')[0]}</span>
                        <span className="text-xs text-stone-400">{p.watches} views</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tab: People — full user list */}
          {tab === 'people' && (
            <div className="space-y-2">
              {people.map(p => (
                <div key={p.email} className="flex items-center gap-3 bg-stone-900 border border-stone-800 rounded-xl px-4 py-3">
                  {p.photo ? (
                    <img src={p.photo} className="w-9 h-9 rounded-full" referrerPolicy="no-referrer" />
                  ) : (
                    <div className="w-9 h-9 rounded-full bg-stone-700 flex items-center justify-center text-sm text-stone-400 font-bold">
                      {(p.name || p.email || '?')[0].toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-white font-medium truncate">{p.name || p.email?.split('@')[0]}</div>
                    <div className="text-[10px] text-stone-500 truncate">{p.email}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="flex items-center gap-1.5 text-[10px]">
                      {p.liveWatches > 0 && <span className="text-lime-400">🔴 {p.liveWatches}</span>}
                      {p.watches > 0 && <span className="text-sky-400">▶ {p.watches}</span>}
                      <span className="text-stone-500">🔓 {p.logins}</span>
                    </div>
                    <div className="text-[9px] text-stone-600 mt-0.5">{relativeTime(p.lastSeen)}</div>
                  </div>
                </div>
              ))}
              {people.length === 0 && <p className="text-stone-500 text-sm text-center py-4">No viewers yet.</p>}
            </div>
          )}

          {/* Tab: Activity — raw log */}
          {tab === 'activity' && (
            <div className="space-y-1">
              {logs.slice(0, 50).map(l => (
                <div key={l.id} className="flex items-center gap-2 py-2 border-b border-stone-800/40">
                  {l.photo ? (
                    <img src={l.photo} className="w-6 h-6 rounded-full" referrerPolicy="no-referrer" />
                  ) : (
                    <div className="w-6 h-6 rounded-full bg-stone-800 flex items-center justify-center text-[10px] text-stone-500">
                      {(l.name || l.email || '?')[0].toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <span className="text-xs text-stone-200 truncate block">{l.name || l.email?.split('@')[0]}</span>
                  </div>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                    l.action === 'login' ? 'bg-stone-800 text-stone-400' :
                    l.action === 'watch_live' ? 'bg-lime-950 text-lime-400' :
                    'bg-sky-950 text-sky-400'
                  }`}>
                    {l.action === 'login' ? 'LOGIN' : l.action === 'watch_live' ? 'LIVE' : 'REPLAY'}
                  </span>
                  <span className="text-[10px] text-stone-600 w-20 text-right shrink-0">{fmtTs(l.ts)}</span>
                </div>
              ))}
              {logs.length === 0 && <p className="text-stone-500 text-sm text-center py-4">No activity yet.</p>}
              {logs.length > 50 && <p className="text-stone-600 text-xs text-center py-2">Showing 50 of {logs.length} events</p>}
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
          <Step n={1}>Tap <Pill tone="lime">START GAME</Pill> → opponent → squad → starting 7.</Step>
          <Step n={2}>During play, tap actions as they happen.</Step>
          <Step n={3}>End the game → scores save → review in <Pill>STATS</Pill> &amp; past games.</Step>
          <p className="text-xs text-stone-400">All data syncs to the cloud, so other coaches with the link see the same roster and live games.</p>
        </Section>

        <Section id="start" emoji="🚀" title="1 · Starting a match" summary="Opponent → squad → lineup → GK">
          <p>From Home tap <Pill tone="lime">START GAME</Pill> and walk through the wizard:</p>
          <Step n={1}><strong>Game setup</strong>: opponent name, tournament/festival label.</Step>
          <Step n={2}><strong>Squad picker</strong>: tick who's actually available today. For 7v7 leagues that cap rosters at 12, you'll see a soft warning over 12 — but no hard limit.</Step>
          <Step n={3}><strong>Starting lineup</strong>: pick the 7 (or however many) who start on the field. Everyone else begins on the bench.</Step>
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
          <p>Each pillar is normalized to a "per 20 minutes" rate so a substitute who plays 10 minutes is compared fairly against a starter who plays 40.</p>
          <p>Outfield players use a balanced blend; goalkeepers use a defence-heavy blend (DEF counts ~55%, ATK only ~10%).</p>
        </Section>

        <Section id="weights" emoji="⚙" title="7 · Tuning scoring weights" summary="Adjust how much each action is worth">
          <p>From Home tap <Pill>⚙ SCORING</Pill>. Two tabs:</p>
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>ACTIONS</strong> — points per action. The same values apply to outfield players and the keeper.</li>
            <li><strong>PILLARS</strong> — how much each pillar (ATK · DEF · DEC · INV) contributes to the overall score. Outfield and GK have separate mixes — the GK row is DEF-heavy because that's where keepers earn their rating. Each row should sum to 100% — the header turns red if it doesn't.</li>
          </ul>
          <p>Negative numbers (red boxes) penalize the score. Tap <Pill>RESET</Pill> in the top-right to restore defaults. All past games re-score live with the new weights — nothing is baked in.</p>
        </Section>

        <Section id="history" emoji="📜" title="8 · Past games & season stats" summary="Where to find recorded matches">
          <p><strong>PAST GAMES</strong> list on Home shows every finished match with the result. Tap any game to see the timeline of every event, per-player scores, and minutes.</p>
          <p>Tap <Pill>STATS</Pill> for season-aggregate per-player numbers — total minutes, goals, season performance score, etc.</p>
          <p>To delete a game tap into it and use the trash button (top-right) — that wipes the videos from R2, the analytics from Firestore, and removes the game from the public list. To delete <em>just</em> the videos (and keep the stats so you can re-run the pipeline later), open <strong>Analytics</strong> and tap "🗑 DELETE VIDEOS ONLY". To remove a single mis-logged event, tap the trash next to it in the event list.</p>
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

  const highlightsUrl = game?.videoHighlightsUrl;
  const fullGameUrl = game?.videoFullGameUrl;
  const highlightsDur = game?.videoHighlightsDurationS;
  const fullGameDur = game?.videoFullGameDurationS;
  if (!highlightsUrl && !fullGameUrl) return null;

  // The broadcast player expects an analytics-shaped "doc" for the overlay.
  // We synthesize a minimal one from the public game-doc fields.
  const broadcastDoc = {
    broadcast_events: game?.broadcastEvents || [],
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
  }, [gameId]);

  if (error) return <PublicErrorScreen msg={error} />;
  if (!game) return <PublicLoadingScreen />;
  return (
    <div className="min-h-screen bg-stone-950 pb-12 relative">
      <style>{FONT_STYLES}</style>
      <div className="relative stripes-bg border-b-2 border-lime-500/70 shadow-[0_4px_24px_-8px_rgba(132,204,22,0.35)] overflow-hidden pt-[calc(env(safe-area-inset-top,0px)+3.25rem)]">
        <a
          href="./"
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

function PublicHomePage() {
  const [games, setGames] = useState([]);
  const [roster, setRoster] = useState([]);
  const [schedule, setSchedule] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const [isCoachUser, setIsCoachUser] = useState(false);
  const [showTraining, setShowTraining] = useState(false);
  // Re-render once a minute so the featured card flips at 6 AM ET, kickoff,
  // and (final whistle + 1h) without needing a page reload or new snapshot.
  const [, setMinTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setMinTick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.fbDb || !window.fbUserInfo) return;
    const email = window.fbUserInfo.email?.toLowerCase();
    if (!email) return;
    window.fbDb.collection('allowedUsers').doc(email).get().then((doc) => {
      if (doc.exists && doc.data().role === 'coach') setIsCoachUser(true);
    }).catch(() => {});
  }, []);

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
  }, []);

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
  const todaySched = (schedule || []).filter(
    (s) => (s.date || '').slice(0, 10) === todayStr && !isCovered(s),
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

  const past = finished.filter((g) => !featuredGame || g.id !== featuredGame.id);

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
                  <h1 className="font-display text-5xl leading-none">U10 BOYS</h1>
                  <div className="font-display text-3xl text-lime-400 leading-tight">2016 SQUAD</div>
                </div>
              </div>
            </div>
            {featuredGame ? (
              <a href={`./?live=${featuredGame.id}`} className="block">
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
              <h1 className="font-display text-5xl leading-none">U10 BOYS</h1>
              <div className="font-display text-3xl text-lime-400 leading-tight">2016 SQUAD</div>
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
      {(() => {
        const playedKey = (g) => `${(g.date || '').slice(0,10)}|${(g.opponent || '').trim().toLowerCase()}`;
        const playedKeys = new Set((games || []).map(playedKey));
        const upcoming = schedule
          .filter(s => new Date(s.date + 'T' + (s.time || '23:59')) >= new Date(new Date().toDateString()))
          .filter(s => !featuredItem || s.id !== featuredItem.id)
          .filter(s => !playedKeys.has(`${(s.date || '').slice(0,10)}|${(s.opponent || '').trim().toLowerCase()}`))
          .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        if (upcoming.length === 0) return null;
        return (
          <div className="px-4 pt-6 max-w-md mx-auto">
            <h3 className="font-display text-xl text-stone-200 mb-2">UPCOMING GAMES</h3>
            <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
              {upcoming.map(item => (
                <div key={item.id} className="flex items-center gap-3 p-3">
                  <div className="w-10 h-10 rounded-lg bg-blue-500/15 text-blue-300 flex flex-col items-center justify-center text-xs font-bold leading-tight">
                    <span>{new Date(item.date + 'T12:00').toLocaleDateString('en', { month: 'short' }).toUpperCase()}</span>
                    <span className="text-base">{new Date(item.date + 'T12:00').getDate()}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">vs {item.opponent}</div>
                    <div className="text-xs text-stone-400 truncate flex items-center gap-1.5 flex-wrap mt-0.5">
                      {item.cancelled && (
                        <span className="inline-block bg-red-500/15 text-red-300 border border-red-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          CANCELLED
                        </span>
                      )}
                      {item.tournament && <TournamentChip value={item.tournament} />}
                      {item.time && <span>{formatTime12(item.time)}</span>}
                      {item.field && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-bold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          📍 {item.field}
                        </span>
                      )}
                    </div>
                    {item.location && (
                      <div className="text-xs text-blue-400 truncate mt-0.5">
                        {item.location.startsWith('http') ? (
                          <a href={item.location} target="_blank" rel="noopener noreferrer" className="underline flex items-center gap-1">
                            <MapPin className="w-3 h-3 inline" /> View Map
                          </a>
                        ) : (
                          <a href={`https://maps.google.com/?q=${encodeURIComponent(item.location)}`} target="_blank" rel="noopener noreferrer" className="underline flex items-center gap-1">
                            <MapPin className="w-3 h-3 inline" /> {item.location}
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}
      {past.length > 0 && (
        <div className="px-4 pt-6 max-w-md mx-auto">
          <h3 className="font-display text-xl text-stone-200 mb-2">PAST GAMES</h3>
          <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
            {past.map((g) => {
              const r = g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D';
              const rColor = r === 'W' ? 'bg-lime-500 text-white' : r === 'L' ? 'bg-red-500 text-white' : 'bg-stone-700 text-stone-100';
              return (
                <a key={g.id} href={`./?live=${g.id}`} className="flex items-center gap-3 p-3 active:bg-stone-950">
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-display text-sm ${rColor}`}>{r}</div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm truncate">vs {g.opponent}</div>
                    <div className="text-xs text-stone-400 truncate">{g.tournament || 'Festival'} · {formatDate(g.date)}</div>
                  </div>
                  <div className="font-display text-lg tabular-nums text-stone-100">{g.ourScore}–{g.oppScore}</div>
                  <ChevronRight className="w-4 h-4 text-stone-400" />
                </a>
              );
            })}
          </div>

        </div>
      )}

      {/* Single full-width Training Videos tile → opens the hub (playlists → shorts player) */}
      <div className="px-4 pt-6 max-w-2xl mx-auto">
        <button
          onClick={() => setShowTraining(true)}
          className="w-full rounded-2xl p-4 flex items-center justify-between active:scale-[0.98] transition"
          style={{ background: 'linear-gradient(135deg,#0e7490,#155e75)', border: '1px solid rgba(34,211,238,0.2)' }}
        >
          <span className="flex items-center gap-3 min-w-0">
            <span className="text-2xl">🎬</span>
            <span className="text-left min-w-0">
              <span className="block font-display text-base text-white">TRAINING VIDEOS</span>
              <span className="block text-[11px] text-cyan-100/70">Soccer &amp; Goalkeeper drills</span>
            </span>
          </span>
          <span className="text-white/70 shrink-0">›</span>
        </button>
      </div>
      {showTraining && <TrainingHub onBack={() => setShowTraining(false)} />}

    </div>
  );
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
  const nameOf = (pid) => {
    const p = roster.find((r) => r.id === pid);
    if (!p) return 'Unknown';
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
