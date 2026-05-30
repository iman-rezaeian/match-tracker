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
  DUEL_WIN:  { id: 'DUEL_WIN',  label: '1V1 WIN',   emoji: '💪', tone: 'soft-green', requiresPlayer: true },
  DUEL_LOSE: { id: 'DUEL_LOSE', label: '1V1 LOSE',  emoji: '👎', tone: 'soft-red',   requiresPlayer: true },
  GIVE_GO:   { id: 'GIVE_GO',   label: 'GIVE-GO',   emoji: '🔁', tone: 'soft-green', requiresPlayer: true },
  GATES:     { id: 'GATES',     label: 'GATE PASS', emoji: '🚪', tone: 'soft-green', requiresPlayer: true },
  TURNOVER:  { id: 'TURNOVER',  label: 'TURNOVER',  emoji: '💨', tone: 'soft-red',   requiresPlayer: true },
  HOLDS_BALL:{ id: 'HOLDS_BALL',label: 'HOLDS BALL',emoji: '⏳', tone: 'yellow',     requiresPlayer: true },
  OPP_GOAL:  { id: 'OPP_GOAL',  label: 'OPP GOAL',  emoji: '⚽', tone: 'big-red',    requiresPlayer: false, delta: 'opp' },
};

// Events that get an optional zone tag (where on the field it happened).
// Skip continuous/ambient events (HOLDS_BALL) and events with implicit location (ASSIST, OPP_GOAL).
const EVENT_NEEDS_ZONE = new Set([
  'GOAL', 'SHOT_ON', 'SHOT_OFF', 'BALL_WIN', 'TURNOVER',
  'DUEL_WIN', 'DUEL_LOSE', 'KEY_PASS', 'GIVE_GO', 'GATES',
  'SAVE', 'BLOCK',
]);

// Events that get an optional pressure modifier (was the player under pressure when they did this?).
// Limited to decision-bearing events where pressure changes the meaning a lot.
const EVENT_NEEDS_PRESSURE = new Set([
  'KEY_PASS', 'GIVE_GO', 'GATES', 'BALL_WIN',
  'SHOT_ON', 'SHOT_OFF', 'DUEL_WIN',
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

// Live-streaming provider toggle.
// 'youtube'    — free: coach pastes YouTube video ID, app embeds the iframe
// 'cloudflare' — paid ($5/mo): one-tap GO LIVE via Cloudflare Stream Live Input
// Switch to 'cloudflare' when you subscribe to Cloudflare Stream Starter Bundle.
const LIVE_MODE = 'cloudflare';

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
    GOAL_atk: 10, ASSIST_atk: 8, KEY_PASS_atk: 5, SHOT_ON_atk: 3, SHOT_OFF_atk: 1,
    SAVE_def: 7, BLOCK_def: 5, BALL_WIN_def: 5, DUEL_WIN_def: 4, DUEL_LOSE_def: -1,
    GIVE_GO_dec: 6, GIVE_GO_PARTNER_dec: 3, GATES_dec: 4, KEY_PASS_dec: 3, ASSIST_dec: 3,
    HOLDS_BALL_dec: -4, TURNOVER_dec: -4, CLEAN_SHEET_def: 8,
  },
  gkPoints: {
    GOAL_atk: 10, ASSIST_atk: 8, KEY_PASS_atk: 10, SHOT_ON_atk: 3, SHOT_OFF_atk: 1,
    SAVE_def: 10, BLOCK_def: 5, BALL_WIN_def: 5, DUEL_WIN_def: 4, DUEL_LOSE_def: -1,
    GIVE_GO_dec: 6, GIVE_GO_PARTNER_dec: 3, GATES_dec: 4, KEY_PASS_dec: 6, ASSIST_dec: 3,
    HOLDS_BALL_dec: -4, TURNOVER_dec: -4, CLEAN_SHEET_def: 8,
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

function computePerformanceScore(playerId, events, minutesPlayed, position, gkExtras = {}, weights) {
  if (minutesPlayed <= 0) return { overall: 0, attacking: 0, defending: 0, decisions: 0, involvement: 0 };
  const W = mergeWeights(weights);
  const isGK = position === 'GK';
  const perHalf = minutesPlayed / 20;
  const c = {};
  let partnerCount = 0; // give & go wall-pass credits earned by this player
  for (const e of events) {
    if (e.type === 'SUB') continue;
    if (e.playerId === playerId) {
      c[e.type] = (c[e.type] || 0) + 1;
    }
    if (e.type === 'GIVE_GO' && e.partnerId === playerId) {
      partnerCount++;
    }
  }
  const pts = W.points;
  const attacking = (
    (c.GOAL || 0)     * pts.GOAL_atk +
    (c.ASSIST || 0)   * pts.ASSIST_atk +
    (c.KEY_PASS || 0) * pts.KEY_PASS_atk +
    (c.SHOT_ON || 0)  * pts.SHOT_ON_atk +
    (c.SHOT_OFF || 0) * pts.SHOT_OFF_atk
  ) / perHalf;
  const concededPenalty = isGK ? (gkExtras.concededPenalty || 0) : 0;
  const cleanSheets = isGK ? (gkExtras.cleanSheets || 0) : 0;
  const defending = (
    (c.SAVE || 0)      * pts.SAVE_def +
    (c.BLOCK || 0)     * pts.BLOCK_def +
    (c.BALL_WIN || 0)  * pts.BALL_WIN_def +
    (c.DUEL_WIN || 0)  * pts.DUEL_WIN_def +
    (c.DUEL_LOSE || 0) * pts.DUEL_LOSE_def +
    (isGK ? (-concededPenalty + cleanSheets * pts.CLEAN_SHEET_def) : 0)
  ) / perHalf;
  const decisions = (
    (c.GIVE_GO || 0)    * pts.GIVE_GO_dec +
    partnerCount        * pts.GIVE_GO_PARTNER_dec +
    (c.GATES || 0)      * pts.GATES_dec +
    (c.KEY_PASS || 0)   * pts.KEY_PASS_dec +
    (c.ASSIST || 0)     * pts.ASSIST_dec +
    (c.HOLDS_BALL || 0) * pts.HOLDS_BALL_dec +
    (c.TURNOVER || 0)   * pts.TURNOVER_dec
  ) / perHalf;
  const totalEvents = Object.values(c).reduce((a, b) => a + b, 0) + partnerCount;
  const involvement = totalEvents / perHalf;
  const pil = isGK ? W.pillars.gk : W.pillars.outfield;
  const overall = (pil.atk * attacking + pil.def * defending + pil.dec * decisions + pil.inv * involvement) / 100;
  const r = (n) => Math.round(n * 10) / 10;
  return { overall: r(overall), attacking: r(attacking), defending: r(defending), decisions: r(decisions), involvement: r(involvement) };
}

function formatDate(iso) {
  const d = typeof iso === 'string' ? iso.slice(0, 10) : '';
  return new Date(d + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function formatTime12(time24) {
  const [h, m] = time24.split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${suffix}`;
}

const R2_WORKER_KEY = 'ManUtd2016'; // API key for R2 upload worker auth

export default function App() {
  // URL-based routing — computed once at mount; the URL doesn't change without
  // a full reload so this is safe to do before hooks.
  //   ?live=<gameId>  -> single-game public scoreboard (Share button URL)
  //   ?coach          -> coach app (password-gated)
  //   (default)       -> public home: current/latest scoreboard + past matches
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

  const askConfirm = (message, onYes, opts = {}) => {
    setConfirmDialog({ message, onYes, danger: !!opts.danger, yesLabel: opts.yesLabel || 'YES' });
  };

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

  const startNewGame = (opponent, isHome, tournament, startingLineup, gkPlayerId, squad, halfLengthMin, homeColor, awayColor, liveInput, youtubeVideoId) => {
    const now = Date.now();
    const squadIds = (squad && squad.length > 0) ? squad : (startingLineup || []);
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
      events: [],
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
    const last = game.events[game.events.length - 1];
    const ev = EVENT_TYPES[last.type];
    const updated = {
      ...game,
      events: game.events.slice(0, -1),
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

  const deleteGame = (gameId) => {
    persistGames(games.filter(g => g.id !== gameId));
    setView('home');
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
    const updated = { ...game, events: [...game.events, event] };
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
            setPendingGameSetup({ opponent: item.opponent, isHome: true, tournament: item.tournament });
            setView('squad');
          }}
          onResumeGame={() => { setActiveGameId(activeGame.id); setView('activeGame'); }}
          onViewGame={(id) => { setViewingGameId(id); setView('gameDetail'); }}
          onViewStats={() => setView('stats')}
          onViewWeights={() => setView('weights')}
          onViewSchedule={() => setView('schedule')}
          onViewHelp={() => setView('help')}
          onViewViewers={() => setView('viewers')}
          onViewFilmRoom={() => setView('filmRoom')}
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
          onCancel={() => setView('home')}
          onStart={(opponent, isHome, tournament, halfLengthMin, homeColor, awayColor) => {
            setPendingGameSetup({ opponent, isHome, tournament, halfLengthMin, homeColor, awayColor });
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
          squad={pendingGameSetup.squad}
          setup={pendingGameSetup}
          teamLiveInput={teamLiveInput}
          onSaveTeamLiveInput={persistTeamLiveInput}
          onBack={() => { setView('squad'); }}
          onStart={(lineup, gkPlayerId, liveInput, youtubeVideoId) => {
            startNewGame(pendingGameSetup.opponent, pendingGameSetup.isHome, pendingGameSetup.tournament, lineup, gkPlayerId, pendingGameSetup.squad, pendingGameSetup.halfLengthMin, pendingGameSetup.homeColor, pendingGameSetup.awayColor, liveInput, youtubeVideoId);
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
            // fault: 'gk' | 'unstoppable' | null (neutral / unsure)
            logEvent(activeGame.id, 'OPP_GOAL', null, { gkFault: fault });
          }}
          onConfirmGK={(playerId) => setGameGK(activeGame.id, playerId)}
          onSwapGK={() => setPendingEvent({ type: 'NEW_GK', defaultGK: currentGKAt(activeGame) })}
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
          tick={tick}
        />
      )}

      {view === 'gameDetail' && viewingGame && (
        <GameDetail
          game={viewingGame}
          roster={roster}
          weights={weights}
          onBack={() => setView('home')}
          onDelete={() => {
            // In production, require the coach password before deleting a game.
            // Beta/dev hosts skip this so we can create and trash dummy games freely.
            const host = (typeof window !== 'undefined' && window.location.hostname) || '';
            const isProd = !/beta|localhost|127\.0\.0\.1|deploy-preview/i.test(host);
            if (isProd) {
              if (!window.confirm('Are you sure? This is a production game.')) return;
            }
            askConfirm('Delete this game permanently?', () => deleteGame(viewingGame.id), { danger: true, yesLabel: 'DELETE' });
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
          onSave={persistSchedule}
          onBack={() => setView('home')}
          askConfirm={askConfirm}
        />
      )}

      {view === 'viewers' && (
        <ViewersPanel onBack={() => setView('home')} />
      )}

      {view === 'filmRoom' && (
        <FilmRoomView games={games} roster={roster} onBack={() => setView('home')} />
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
              danger ? 'bg-red-500 text-white' : 'bg-stone-900 text-white'
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
function HomeView({ roster, games, schedule, activeGame, onGoRoster, onNewGame, onStartScheduled, onResumeGame, onViewGame, onViewStats, onViewWeights, onViewSchedule, onViewHelp, onViewViewers, onViewFilmRoom }) {
  const finishedGames = games.filter(g => g.status === 'finished');
  const wins = finishedGames.filter(g => g.ourScore > g.oppScore).length;
  const losses = finishedGames.filter(g => g.ourScore < g.oppScore).length;
  const draws = finishedGames.filter(g => g.ourScore === g.oppScore).length;
  const [showLiveTest, setShowLiveTest] = useState(false);
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
      </div>

      {/* Test live stream — dry-run helper, no game created */}
      <div className="px-4 pt-3">
        <button
          onClick={() => setShowLiveTest(true)}
          className="w-full py-2.5 rounded-xl bg-stone-900 border border-stone-800 text-stone-300 text-sm font-bold hover:bg-stone-800 active:scale-[0.99] flex items-center justify-center gap-2"
        >
          <span>🧪</span><span>TEST LIVE STREAM</span>
        </button>
      </div>

      {showLiveTest && <LiveStreamTester onClose={() => setShowLiveTest(false)} />}

      {/* Upcoming games */}
      {(() => {
        const upcoming = schedule
          .filter(s => new Date(s.date + 'T' + (s.time || '23:59')) >= new Date(new Date().toDateString()))
          .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        if (upcoming.length === 0) return null;
        return (
          <div className="px-4 pt-6">
            <h2 className="font-display text-2xl mb-3">UPCOMING</h2>
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
                      {item.tournament && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          {item.tournament.toUpperCase()}
                        </span>
                      )}
                      {item.time && <span>{formatTime12(item.time)}</span>}
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
          Adds a Stompers icon to your home screen. Opens fullscreen — no browser bar — and works offline.
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
function GameSetup({ rosterCount, onCancel, onStart, onGoRoster }) {
  const [opponent, setOpponent] = useState('');
  const [tournament, setTournament] = useState('Festival');
  const [halfLengthMin, setHalfLengthMin] = useState(25);
  const [homeColor, setHomeColor] = useState('#0a0a0a');
  const [awayColor, setAwayColor] = useState('#dc2626');
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
            className="w-full bg-stone-900 border-2 border-stone-800 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
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

        <Field label="STOMPERS JERSEY">
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
          onClick={() => onStart(opponent.trim() || 'Opponent', true, tournament.trim() || 'Festival', halfLengthMin, homeColor, awayColor)}
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
function SquadPickerView({ roster, setup, initialSquad, onBack, onNext }) {
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
      <Header title="MATCH-DAY SQUAD" onBack={onBack} />

      <div className="px-4 pt-4">
        <div className="text-xs text-stone-400 mb-1">vs {setup.opponent}</div>
        <div className="text-sm text-stone-200 mb-3">
          Tap players who are <span className="font-bold">available for this match</span>. Unchecked players are OUT.
          Soft limit is <span className="font-bold">{SOFT_CAP}</span> (7v7 max squad) — you can exceed it if you need to.
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
          NEXT: STARTING LINEUP →
        </button>
      </div>
    </div>
  );
}

/* ---------- STARTING LINEUP ---------- */
function StartingLineupView({ roster, squad, setup, teamLiveInput, onSaveTeamLiveInput, onBack, onStart }) {
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
    <div className="pb-40">
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

      <div className="fixed bottom-0 left-0 right-0 bg-stone-900 border-t border-stone-800 p-4 shadow-xl">
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

        <button
          onClick={() => {
            const livePayload = LIVE_MODE === 'cloudflare' && attached ? teamLiveInput : null;
            const ytId = LIVE_MODE === 'youtube' ? ytVideoId : null;
            onStart(Array.from(selected), gkId, livePayload, ytId);
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

/* ---------- ACTIVE GAME ---------- */
function ActiveGameView({ game, roster, pendingEvent, onSelectEvent, onSelectPlayer, onResolveOppGoal, onConfirmGK, onSwapGK, onCancelEvent, onUndo, onPauseHalfTime, onStartSecondHalf, onResumeFirstHalf, onPauseClock, onResumeClock, onEnd, onBack, tick }) {
  const elapsed = computeElapsed(game);
  const recent = [...game.events].reverse().slice(0, 6);
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

  const statusLabel = inHalfTimeBreak ? 'HALF TIME' : inSecondHalf ? '2ND HALF' : '1ST HALF';
  const statusColor = inHalfTimeBreak ? 'bg-amber-400 text-stone-100' : 'bg-stone-900 text-lime-400';

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
            <div className="text-[10px] font-bold tracking-widest text-lime-400">STOMPERS</div>
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
            </div>
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
            <div className="text-stone-400 text-sm mb-8 max-w-xs">
              Clock is paused. Tap below when the 2nd half kicks off.
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

            <div className="mt-8 w-full">
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
                {['GOAL', 'SHOT_ON', 'SHOT_OFF', 'KEY_PASS', 'GIVE_GO', 'GATES', 'BALL_WIN', 'DUEL_WIN', 'SAVE', 'BLOCK'].map(id => {
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
                {['TURNOVER', 'HOLDS_BALL', 'DUEL_LOSE'].map(id => {
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

/* ---------- LIVE STREAM TESTER ----------
 * Dry-run helper for coaches: detects the active YouTube live stream on
 * @Stompers2016 via the worker and embeds it inline — no game doc, no
 * Firestore writes. Lets you verify Insta360 → YouTube → detection works
 * before kickoff.
 */
function LiveStreamTester({ onClose }) {
  const [videoId, setVideoId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [ranOnce, setRanOnce] = useState(false);

  const detect = () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    fetch(`${R2_UPLOAD_WORKER}/youtube-live`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: R2_WORKER_KEY }),
    })
      .then(r => r.json().then(j => r.ok ? j : Promise.reject(j.error || 'detection failed')))
      .then((data) => {
        if (data.live && data.videoId) {
          setVideoId(data.videoId);
        } else {
          setVideoId(null);
          setErr('No live stream detected. Start streaming from Insta360 first, then tap DETECT again.');
        }
      })
      .catch((e) => { setVideoId(null); setErr(String(e)); })
      .finally(() => { setBusy(false); setRanOnce(true); });
  };

  useEffect(() => { detect(); /* auto-detect on open */ }, []);

  return (
    <div className="fixed inset-0 bg-black/80 z-50 flex items-end sm:items-center justify-center p-3" onClick={onClose}>
      <div
        className="w-full max-w-lg bg-stone-900 border border-stone-800 rounded-2xl p-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        style={{ paddingTop: 'max(env(safe-area-inset-top, 0px), 16px)' }}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-display text-lg">🧪 TEST LIVE STREAM</h3>
          <button onClick={onClose} className="w-8 h-8 rounded-full bg-stone-800 hover:bg-stone-700 flex items-center justify-center active:scale-95"><X className="w-4 h-4" /></button>
        </div>
        <div className="text-xs text-stone-400 mb-3">
          Dry-run check for the @Stompers2016 YouTube live stream. No game is created. Use this to confirm Insta360 → YouTube → detection works before kickoff.
        </div>

        {videoId ? (
          <>
            <div className="mb-2 text-[10px] uppercase tracking-wider text-lime-400">● LIVE · videoId {videoId}</div>
            <YouTubeEmbed videoId={videoId} live={true} />
          </>
        ) : (
          <div className="bg-stone-800/60 rounded-xl p-4 text-sm text-stone-300">
            {busy ? 'Detecting…' : (err || 'Ready to detect.')}
          </div>
        )}

        <div className="mt-3 flex gap-2">
          <button
            onClick={detect}
            disabled={busy}
            className="flex-1 py-2 rounded-xl bg-lime-500 text-stone-950 font-display text-sm active:scale-95 disabled:opacity-50"
          >
            {busy ? 'DETECTING…' : (ranOnce ? '↻ DETECT AGAIN' : 'DETECT')}
          </button>
          {videoId && (
            <a
              href={`https://www.youtube.com/watch?v=${videoId}`}
              target="_blank"
              rel="noreferrer"
              className="py-2 px-3 rounded-xl bg-stone-800 text-stone-200 text-sm flex items-center active:scale-95"
            >Open on YouTube ↗</a>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- YOUTUBE EMBED ---------- */
function YouTubeEmbed({ videoId, live = false }) {
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
  const params = [
    'autoplay=1', 'mute=1', 'rel=0', 'modestbranding=1', 'playsinline=1',
    'controls=0', 'disablekb=1', 'iv_load_policy=3', 'fs=0',
    'showinfo=0', 'cc_load_policy=0',
    live ? 'live=1' : '',
    origin ? `origin=${encodeURIComponent(origin)}` : '',
  ].filter(Boolean).join('&');
  const src = `https://www.youtube-nocookie.com/embed/${id}?${params}`;

  return (
    <div className="relative w-full aspect-video rounded-xl overflow-hidden bg-black">
      <iframe
        src={src}
        className="absolute inset-0 w-full h-full"
        frameBorder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
        referrerPolicy="strict-origin-when-cross-origin"
        title="Match stream"
      />
      {/* Click-blocking overlay — prevents accidental taps on YT pause/share/title */}
      <div className="absolute inset-0" aria-hidden="true" />
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

function VideoPlayer360({ videoUrl, seekTo, onClose, events = [], gameInfo, dotsMode: initialDotsMode = 'all', lockDots = false }) {
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
  const [tvMode] = useState(true);
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
      // 3) CSS-rotation fallback for iOS Safari PWA (ignores both APIs above).
      //    Always apply on touch devices — the wrapperStyle below compensates
      //    for the current page angle so the result is always landscape-primary.
      const isTouch = (typeof window !== 'undefined') && (('ontouchstart' in window) || (navigator.maxTouchPoints > 0));
      setFullscreenRotated(isTouch);
      rotatedRef.current = isTouch;
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

  // TV mode is always on for soccer viewing — clamp vertical, narrow FOV.
  // Set once on mount so the initial scene state matches the controls.
  useEffect(() => {
    const st = stateRef.current;
    st.tvMode = true;
    st.targetFov = 40;
    st.targetLat = Math.max(-45, Math.min(10, st.targetLat));
  }, []);

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
          const sensitivity = 0.1 * (st.fov / 75);
          const dLon = -dx * sensitivity;
          const dLat = dy * sensitivity;
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
    ? Math.floor(liveElapsedSec / 60)
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
          <svg viewBox="0 0 24 24" className="w-6 h-6">
            <path d="M11 5V2L6 6l5 4V7a6 6 0 1 1-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <text x="9" y="18" fontSize="7" fontWeight="700" fill="currentColor">10</text>
          </svg>
        </button>
        )}
        {/* Forward 10s — hidden for live */}
        {!isLive && (
        <button onClick={() => { if (videoRef.current) videoRef.current.currentTime = Math.min(duration, videoRef.current.currentTime + 10); }}
          className="w-10 h-10 rounded-full flex items-center justify-center text-white active:scale-95" aria-label="Forward 10 seconds">
          <svg viewBox="0 0 24 24" className="w-6 h-6">
            <path d="M13 5V2l5 4-5 4V7a6 6 0 1 0 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <text x="6" y="18" fontSize="7" fontWeight="700" fill="currentColor">10</text>
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
            className="w-full h-1 accent-lime-400 block"
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
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9"/>
            <polygon points="12,6 14.5,12 12,18 9.5,12" fill="currentColor" stroke="none"/>
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
                      {g.tournament && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          {g.tournament.toUpperCase()}
                        </span>
                      )}
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

/* ---------- POST-GAME ANALYTICS PANEL ----------
 * Reads `teams/main/games/<gameId>/analytics/v1` written by the Python
 * pipeline (post_game/pipeline.py). Shows per-player physical stats, team
 * formation, GK positioning, and links to highlight clips.
 * Coach-only — purely a read view here.
 */
function AnalyticsPanel({ game, roster, onClose, onSeekVideo }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [clips, setClips] = useState([]);

  // Push a history entry so swipe-back closes the panel instead of leaving the app.
  // Also lock body scroll so the page underneath keeps its scroll position
  // when the modal closes (otherwise iOS resets to top).
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    window.history.pushState({ modal: 'analytics' }, '');
    const onPop = () => onClose();
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
  }, [onClose]);

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

  return (
    <div className="fixed inset-0 bg-stone-950 z-50 overflow-y-auto">
      <div
        className="sticky top-0 stripes-bg text-white border-b border-stone-800 px-4 pb-3 flex items-center justify-between z-10"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.75rem)' }}
      >
        <h2 className="font-display text-lg truncate pr-3">📊 ANALYTICS — {game.opponent}</h2>
        <button
          onClick={onClose}
          className="shrink-0 h-9 px-3 rounded-full bg-white/15 hover:bg-white/25 text-white font-display text-xs flex items-center gap-1 border border-white/20 active:scale-95"
        >
          CLOSE ✕
        </button>
      </div>

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
          {/* Summary */}
          <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
            <div className="text-xs text-stone-500 uppercase mb-2">Summary</div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>Players analyzed: <strong>{(doc.player_stats || []).length}</strong></div>
              <div>Clips: <strong>{doc.clip_count ?? clips.length}</strong></div>
              <div>Field: <strong>{doc.field_name || '—'}</strong></div>
              <div>Generated: <strong>{doc.generated_at_ms ? new Date(doc.generated_at_ms).toLocaleString() : '—'}</strong></div>
            </div>
          </section>

          {/* Player physical stats */}
          <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
            <div className="text-xs text-stone-500 uppercase mb-2">Per-Player Physical Stats</div>
            {(doc.player_stats || []).length === 0 ? (
              <div className="text-sm text-stone-400">No player stats.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-stone-500 border-b border-stone-800">
                      <th className="text-left py-1 pr-2">Player</th>
                      <th className="text-right py-1 px-2">Min</th>
                      <th className="text-right py-1 px-2">Dist (m)</th>
                      <th className="text-right py-1 px-2">Top (km/h)</th>
                      <th className="text-right py-1 px-2">Sprints</th>
                      <th className="text-right py-1 px-2">Att%</th>
                      <th className="text-right py-1 px-2">Mid%</th>
                      <th className="text-right py-1 px-2">Def%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...(doc.player_stats || [])]
                      .sort((a, b) => (b.distance_m || 0) - (a.distance_m || 0))
                      .map(s => (
                        <tr key={s.player_id} className="border-b border-stone-800/50">
                          <td className="py-1 pr-2 truncate max-w-[140px]">{playerName(s.player_id)}</td>
                          <td className="text-right px-2">{(s.minutes_played || 0).toFixed(0)}</td>
                          <td className="text-right px-2">{(s.distance_m || 0).toFixed(0)}</td>
                          <td className="text-right px-2">{((s.top_speed_ms || 0) * 3.6).toFixed(1)}</td>
                          <td className="text-right px-2">{s.sprint_count || 0}</td>
                          <td className="text-right px-2">{(s.pct_attacking_third || 0).toFixed(0)}</td>
                          <td className="text-right px-2">{(s.pct_middle_third || 0).toFixed(0)}</td>
                          <td className="text-right px-2">{(s.pct_defensive_third || 0).toFixed(0)}</td>
                        </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Identity confidence */}
          {(doc.identity_assignments || []).some(a => a.status === 'review') && (
            <section className="bg-amber-950/40 border border-amber-800 rounded-2xl p-4">
              <div className="text-xs text-amber-400 uppercase mb-2">Identity Review Needed</div>
              <div className="text-xs text-amber-200 mb-2">
                {(doc.identity_assignments || []).filter(a => a.status === 'review').length} track(s) below auto-assign threshold.
              </div>
              <ul className="text-xs text-amber-100 space-y-1">
                {(doc.identity_assignments || [])
                  .filter(a => a.status === 'review')
                  .slice(0, 10)
                  .map(a => (
                    <li key={a.track_id}>
                      Track #{a.track_id} → {playerName(a.player_id)} (conf {(a.confidence * 100).toFixed(0)}%)
                    </li>
                  ))}
              </ul>
            </section>
          )}

          {/* Formation */}
          {(doc.formation_snapshots || []).length > 0 && (
            <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
              <div className="text-xs text-stone-500 uppercase mb-2">Formation by Period</div>
              {doc.formation_snapshots.map(f => (
                <div key={f.period} className="text-sm py-1">
                  Period {f.period}: <strong>{f.label}</strong>
                </div>
              ))}
            </section>
          )}

          {/* GK positioning */}
          {(doc.gk_positions || []).length > 0 && (
            <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
              <div className="text-xs text-stone-500 uppercase mb-2">Goalkeeper Positioning</div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-stone-500 border-b border-stone-800">
                    <th className="text-left py-1 pr-2">Event</th>
                    <th className="text-left py-1 px-2">GK</th>
                    <th className="text-right py-1 px-2">Dist line (m)</th>
                    <th className="text-right py-1 px-2">Lateral (m)</th>
                    <th className="text-right py-1 px-2">On angle?</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.gk_positions.map(g => (
                    <tr key={g.event_id} className="border-b border-stone-800/50">
                      <td className="py-1 pr-2">{g.event_type} P{g.period} {Math.floor(g.elapsed / 60)}'</td>
                      <td className="px-2 truncate max-w-[120px]">{playerName(g.gk_player_id)}</td>
                      <td className="text-right px-2">{(g.distance_from_goal_line_m || 0).toFixed(1)}</td>
                      <td className="text-right px-2">{(g.lateral_offset_from_goal_center_m || 0).toFixed(1)}</td>
                      <td className="text-right px-2">{g.on_correct_angle == null ? '—' : g.on_correct_angle ? '✓' : '✗'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {/* Clips */}
          {clips.length > 0 && (
            <section className="bg-stone-900 border border-stone-800 rounded-2xl p-4">
              <div className="text-xs text-stone-500 uppercase mb-2">Highlight Clips</div>
              <div className="grid grid-cols-1 gap-2">
                {clips.map(c => (
                  <a
                    key={c.eventId}
                    href={c.r2Url || '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between bg-stone-800/50 rounded-lg px-3 py-2 text-sm hover:bg-stone-800"
                  >
                    <span>
                      <strong>{c.eventType}</strong> · P{c.period} {Math.floor((c.elapsed || 0) / 60)}' · {playerName(c.playerId)}
                    </span>
                    <span className="text-xs text-lime-400">{c.r2Url ? 'WATCH ▶' : 'pending'}</span>
                  </a>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- GAME DETAIL ---------- */
function GameDetail({ game, roster, weights, onBack, onDelete, onDeleteEvent, onUpdateEvent, onUpdateGame }) {
  const events = [...game.events].sort((a, b) => a.at - b.at);
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
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GIVE_GO_WALL: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0 });
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
      {!game.youtubeVideoId && (game.liveInput || game.status === 'active') && (
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
            <span className="text-xs text-stone-400">DISTANCE · HEATMAPS · FORMATION · GK</span>
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
        />
      )}

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
                const pos = wasGKThisGame ? 'GK' : player?.position;
                const gkExtras = wasGKThisGame ? gkExtrasForGame(pid, game) : undefined;
                const score = computePerformanceScore(pid, events, min, pos, gkExtras, weights);
                return { pid, stats, min, score, pos, gkExtras };
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
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GIVE_GO_WALL: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0, gamesPlayed: 0, totalSeconds: 0, gkSeconds: 0, cleanSheets: 0, oppGoalsConceded: 0, gamesAsGK: 0 });
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
      const seasonPos = wasGKAnyGame ? 'GK' : p.position;
      map[p.id] = computePerformanceScore(p.id, allEvents, min, seasonPos, wasGKAnyGame ? gkExtras : undefined, weights);
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
        <div className="text-xs text-stone-400 italic mb-3">Sorted by performance score. Tap a player for full breakdown.</div>

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

function ScheduleView({ schedule, onSave, onBack, askConfirm }) {
  const [opponent, setOpponent] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [tournament, setTournament] = useState('');
  const [location, setLocation] = useState('');
  const [pasteText, setPasteText] = useState('');
  const [parsed, setParsed] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const formRef = React.useRef(null);

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

      results.push({ date: isoDate, time: isoTime, opponent: opp, location: field });
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
      location: p.location,
    }));
    onSave([...schedule, ...newItems]);
    setPasteText('');
    setParsed(null);
  };

  const handleAdd = () => {
    if (!opponent.trim() || !date) return;
    if (editingId) {
      onSave(schedule.map(s => s.id === editingId ? {
        ...s,
        opponent: opponent.trim(),
        date,
        time: time || '',
        tournament: tournament.trim(),
        location: location.trim(),
      } : s));
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
    };
    onSave([...schedule, item]);
    resetForm();
  };

  const resetForm = () => {
    setOpponent(''); setDate(''); setTime(''); setTournament(''); setLocation('');
    setEditingId(null);
  };

  const handleEdit = (item) => {
    setEditingId(item.id);
    setOpponent(item.opponent || '');
    setDate(item.date || '');
    setTime(item.time || '');
    setTournament(item.tournament || '');
    setLocation(item.location || '');
    if (formRef.current) formRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleToggleCancel = (item) => {
    onSave(schedule.map(s => s.id === item.id ? { ...s, cancelled: !s.cancelled } : s));
  };

  const handleDelete = (id) => {
    const item = schedule.find(s => s.id === id);
    const label = item ? `vs ${item.opponent}${item.date ? ' on ' + new Date(item.date + 'T12:00').toLocaleDateString('en', { month: 'short', day: 'numeric' }) : ''}` : 'this game';
    askConfirm(`Delete ${label} from the schedule?`, () => {
      if (editingId === id) resetForm();
      onSave(schedule.filter(s => s.id !== id));
    }, { danger: true, yesLabel: 'DELETE' });
  };

  const sorted = [...schedule].sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));

  return (
    <div className="min-h-screen bg-stone-900 pb-8">
      <Header title="SCHEDULE" onBack={onBack} />

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
            className="w-full border border-stone-700 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-lime-500"
          />
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
                      {item.tournament && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          {item.tournament.toUpperCase()}
                        </span>
                      )}
                      {item.time && <span>{formatTime12(item.time)}</span>}
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
          <p>To delete a game tap into it and use the trash button (top-right). To remove a single mis-logged event, tap the trash next to it in the event list.</p>
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
              <div className="text-xs text-stone-300">The bare URL (e.g. <code>stompers2016.com</code>) shows the live or most recent game scoreboard plus a list of all past matches. First name + jersey number only — no full names.</div>
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
         and renders a "Past matches" list under the main scoreboard.
   Privacy: scoreboard shows first name + jersey number only — never last names,
   never pillar events, rosters, or weights.
   Auth: existing anonymous Firebase auth covers public viewers transparently.
   ========================================================================= */
/* ---- PublicVideoToggle: collapsed by default, expands to show 360° player ---- */
function PublicVideoToggle({ url, game, label }) {
  const [open, setOpen] = useState(false);
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

/* ---- PublicAnalyticsCard: anonymized post-game stats for finished matches ----
 * Reads teams/main/games/<id>/analytics/v1 + clips/*. Names shown as
 * "First #Number" only (no last names) to match site privacy rules.
 */
function PublicAnalyticsCard({ game, roster }) {
  const [analytics, setAnalytics] = useState(null);
  const [clips, setClips] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!window.fbDb || !game?.id) { setLoaded(true); return; }
    let cancelled = false;
    const gref = window.fbDb.collection('teams').doc('main').collection('games').doc(game.id);
    Promise.all([
      gref.collection('analytics').doc('v1').get(),
      gref.collection('clips').get(),
    ])
      .then(([aSnap, cQs]) => {
        if (cancelled) return;
        setAnalytics(aSnap.exists ? aSnap.data() : null);
        setClips(cQs.docs.map(d => d.data()));
        setLoaded(true);
      })
      .catch(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [game?.id]);

  if (!loaded) return null;
  if (!analytics && clips.length === 0) return null;

  const shortName = (pid) => {
    const p = roster.find(r => r.id === pid);
    if (!p) return '—';
    const first = (p.name || '').split(/\s+/)[0] || p.name || 'Player';
    return `${first} #${p.number || '?'}`;
  };

  const stats = analytics?.player_stats || [];
  const topDistance = [...stats].sort((a, b) => (b.distance_m || 0) - (a.distance_m || 0)).slice(0, 5);
  const topSpeed = [...stats].sort((a, b) => (b.top_speed_ms || 0) - (a.top_speed_ms || 0)).slice(0, 5);

  return (
    <div className="px-4 pt-4 max-w-2xl mx-auto">
      <div className="bg-stone-900 border border-stone-800 rounded-2xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-display text-lg">📊 MATCH ANALYTICS</h3>
          <span className="text-[10px] text-stone-500 tracking-wider">POST-GAME</span>
        </div>

        {topDistance.length > 0 && (
          <div>
            <div className="text-[10px] text-stone-500 uppercase tracking-wider mb-1">Most Distance Covered</div>
            <ol className="space-y-1">
              {topDistance.map((s, i) => (
                <li key={s.player_id} className="flex items-center justify-between text-sm">
                  <span className="truncate">{i + 1}. {shortName(s.player_id)}</span>
                  <span className="font-bold tabular-nums text-lime-400">{(s.distance_m || 0).toFixed(0)} m</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {topSpeed.length > 0 && (
          <div>
            <div className="text-[10px] text-stone-500 uppercase tracking-wider mb-1">Fastest Sprinters</div>
            <ol className="space-y-1">
              {topSpeed.map((s, i) => (
                <li key={s.player_id} className="flex items-center justify-between text-sm">
                  <span className="truncate">{i + 1}. {shortName(s.player_id)}</span>
                  <span className="font-bold tabular-nums text-lime-400">{((s.top_speed_ms || 0) * 3.6).toFixed(1)} km/h</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {(analytics?.formation_snapshots || []).length > 0 && (
          <div>
            <div className="text-[10px] text-stone-500 uppercase tracking-wider mb-1">Formation</div>
            <div className="flex gap-3 text-sm">
              {analytics.formation_snapshots.map(f => (
                <div key={f.period} className="bg-stone-800/50 rounded-lg px-3 py-1">
                  P{f.period}: <strong>{f.label}</strong>
                </div>
              ))}
            </div>
          </div>
        )}

        {clips.length > 0 && (
          <div>
            <div className="text-[10px] text-stone-500 uppercase tracking-wider mb-1">Highlight Clips</div>
            <div className="grid grid-cols-1 gap-2">
              {clips
                .filter(c => c.r2Url)
                .sort((a, b) => (a.period - b.period) || (a.elapsed - b.elapsed))
                .map(c => (
                  <a
                    key={c.eventId}
                    href={c.r2Url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between bg-stone-800/50 rounded-lg px-3 py-2 text-sm hover:bg-stone-800"
                  >
                    <span className="truncate">
                      <strong>{c.eventType}</strong> · P{c.period} {Math.floor((c.elapsed || 0) / 60)}' · {shortName(c.playerId)}
                    </span>
                    <span className="text-xs text-lime-400 shrink-0 ml-2">WATCH ▶</span>
                  </a>
                ))}
            </div>
          </div>
        )}
      </div>
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
    window.fbReady.then((ok) => {
      if (!ok) { setError('Could not connect to Firebase.'); return; }
      const db = window.fbDb;
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
      <a
        href="./"
        className="absolute top-[calc(env(safe-area-inset-top,0px)+1rem)] left-3 z-10 bg-white/15 hover:bg-white/25 text-white text-xs font-bold tracking-widest px-3 py-2 rounded-lg backdrop-blur-sm border border-white/20 flex items-center gap-1"
      >
        <ChevronLeft className="w-4 h-4" /> ALL MATCHES
      </a>
      <LiveScoreboard game={game} roster={roster} />
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
function PublicHomePage() {
  const [games, setGames] = useState([]);
  const [roster, setRoster] = useState([]);
  const [schedule, setSchedule] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const [isCoachUser, setIsCoachUser] = useState(false);

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
    window.fbReady.then((ok) => {
      if (!ok) { setError('Could not connect to Firebase.'); return; }
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

  const active = games.find((g) => g.status === 'active');
  const finished = games.filter((g) => g.status === 'finished');
  // Feature a finished game only if it was played today (local tz); otherwise demote to past.
  const todayStr = new Date().toLocaleDateString('en-CA');
  const featuredFinished = finished[0] && (finished[0].date || '').slice(0, 10) >= todayStr ? finished[0] : null;
  const featured = active || featuredFinished || null;
  const past = active ? finished : (featuredFinished ? finished.slice(1) : finished);

  return (
    <div className="min-h-screen bg-stone-950 pb-12 relative">
      <style>{FONT_STYLES}</style>
      {featured ? (
        <>
          <div className="stripes-bg text-white px-5 pt-16 pb-6 relative">
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
          <a href={`./?live=${featured.id}`} className="block">
            <LiveScoreboard game={featured} roster={roster} />
          </a>
          {featured.videoUrl && !featured.youtubeVideoId && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <PublicVideoToggle url={featured.videoUrl} game={featured} label="🎥 WATCH 360° VIDEO" />
            </div>
          )}
          {featured.youtubeVideoId && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <YouTubeEmbed videoId={featured.youtubeVideoId} live={featured.status === 'active'} />
            </div>
          )}
          {!featured.youtubeVideoId && featured.liveInput?.hlsUrl && (
            <div className="px-4 pt-4 max-w-2xl mx-auto">
              <PublicVideoToggle url={featured.liveInput.hlsUrl} game={featured} label="🔴 WATCH LIVE" />
            </div>
          )}
          {featured.status === 'finished' && (
            <PublicAnalyticsCard game={featured} roster={roster} />
          )}
        </>
      ) : (
        <div className="stripes-bg text-white px-5 pt-16 pb-12 relative">
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
        const upcoming = schedule
          .filter(s => new Date(s.date + 'T' + (s.time || '23:59')) >= new Date(new Date().toDateString()))
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
                      {item.tournament && (
                        <span className="inline-block bg-blue-500/15 text-blue-300 border border-blue-500/40 font-extrabold tracking-wider text-[10px] px-1.5 py-0.5 rounded">
                          {item.tournament.toUpperCase()}
                        </span>
                      )}
                      {item.time && <span>{formatTime12(item.time)}</span>}
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
          <h3 className="font-display text-xl text-stone-200 mb-2">PAST MATCHES</h3>
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
function LiveScoreboard({ game, roster }) {
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
    const events = [...(game.events || [])].sort((a, b) => a.at - b.at);
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
        out.push({ kind: 'opp', at: e.at, elapsed: e.elapsed, period: e.period, ourRun, oppRun });
      }
    }
    return out;
  }, [game]);

  const elapsed = computeElapsed(game);
  const mins = Math.floor(elapsed / 60);
  const isActive = game.status === 'active';
  const isFinished = game.status === 'finished';
  let statusLabel;
  if (isFinished) statusLabel = 'FULL TIME';
  else if (isActive && !game.clockRunning) statusLabel = game.period >= 2 ? 'PAUSED' : 'HALF TIME';
  else if (isActive) statusLabel = `${mins}'`;
  else statusLabel = 'NOT STARTED';

  const HOME_NAME = 'Stompers';
  const leftName = HOME_NAME;
  const rightName = game.opponent || 'Opponent';
  const leftScore = game.ourScore;
  const rightScore = game.oppScore;
  // Pick a font size that keeps both names on one line based on the longer name.
  // Names now get the full half-width each (score sits below), so we can go bigger.
  const longestName = Math.max(leftName.length, rightName.length);
  const nameSizeClass =
    longestName <= 7 ? 'text-4xl'
    : longestName <= 9 ? 'text-3xl'
    : longestName <= 12 ? 'text-2xl'
    : longestName <= 16 ? 'text-xl'
    : longestName <= 20 ? 'text-lg'
    : 'text-base';

  // Privacy: first name + jersey number only — never last names on public pages.
  const nameOf = (pid) => {
    const p = roster.find((r) => r.id === pid);
    if (!p) return 'Unknown';
    const first = (p.name || '').split(/\s+/)[0] || p.name || 'Player';
    return `${first} #${p.number || '?'}`;
  };

  return (
    <>
      <div className="stripes-bg text-white px-4 pt-[calc(env(safe-area-inset-top,0px)+3.75rem)] pb-6">
        <div className="text-center text-xs uppercase tracking-widest text-white/60 mb-1">
          {game.tournament || 'Match'} · {formatDate(game.date)}
        </div>
        {/* Names on top, full half-width each. Score sits below for breathing room. */}
        <div className="flex items-start justify-between gap-4 mt-5 px-2">
          <div className="flex-1 min-w-0 text-center">
            <div className={`font-display ${nameSizeClass} leading-tight`}>{leftName}</div>
          </div>
          <div className="flex-1 min-w-0 text-center">
            <div className={`font-display ${nameSizeClass} leading-tight`}>{rightName}</div>
          </div>
        </div>
        <div className="font-display text-7xl tabular-nums text-center mt-3 leading-none">
          {leftScore}<span className="text-white/40 mx-3">–</span>{rightScore}
        </div>
        <div className="text-center mt-4 flex items-center justify-center gap-2 flex-wrap">
          {isActive && (
            <span className="inline-block bg-white/10 border border-white/20 text-white/90 px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              {game.period >= 2 ? '2ND HALF' : '1ST HALF'}
            </span>
          )}
          {isActive && game.clockRunning && (
            <span className="inline-flex items-center gap-2 bg-red-500/25 border border-red-400/50 text-red-100 px-3 py-1.5 rounded-full text-sm font-bold tracking-wider">
              <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse"></span>
              LIVE · {mins}'
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
      <div className="px-4 pt-5 max-w-md mx-auto">
        {feed.length === 0 ? (
          isActive ? (
            <>
              <h3 className="font-display text-xl text-stone-200 mb-2">GOALS</h3>
              <div className="bg-stone-900 border border-stone-800 rounded-2xl p-6 text-center text-sm text-stone-400">
                No goals yet.
              </div>
            </>
          ) : null
        ) : (
          <>
            <h3 className="font-display text-xl text-stone-200 mb-2">GOALS</h3>
            <div className="bg-stone-900 border border-stone-800 rounded-2xl divide-y divide-stone-800 overflow-hidden">
            {feed.map((row, idx) => {
              const minute = Math.max(1, Math.round((row.elapsed || 0) / 60));
              const isHt = idx > 0 && feed[idx - 1].period === 1 && row.period === 2;
              return (
                <React.Fragment key={idx}>
                  {isHt && (
                    <div className="bg-stone-900 px-3 py-1.5 text-center text-xs font-bold text-stone-400 uppercase tracking-widest">
                      — Half Time —
                    </div>
                  )}
                  {row.kind === 'us' ? (
                    <div className="p-3 flex items-start gap-3">
                      <div className="w-10 text-right font-display text-lg text-stone-400 tabular-nums">{minute}'</div>
                      <div className="text-2xl">⚽</div>
                      <div className="flex-1 min-w-0">
                        <div className="font-bold text-stone-100 truncate">{nameOf(row.scorerId)}</div>
                        {row.assistId && (
                          <div className="text-xs text-stone-400 truncate">🅰️ {nameOf(row.assistId)}</div>
                        )}
                      </div>
                      <div className="font-display text-sm text-lime-600 tabular-nums">{row.ourRun}–{row.oppRun}</div>
                    </div>
                  ) : (
                    <div className="p-3 flex items-start gap-3 bg-red-950/30">
                      <div className="w-10 text-right font-display text-lg text-stone-400 tabular-nums">{minute}'</div>
                      <div className="text-2xl">⚽</div>
                      <div className="flex-1 min-w-0">
                        <div className="font-bold text-stone-100 truncate">{game.opponent || 'Opponent'}</div>
                      </div>
                      <div className="font-display text-sm text-red-400 tabular-nums">{row.ourRun}–{row.oppRun}</div>
                    </div>
                  )}
                </React.Fragment>
              );
            })}
            </div>
          </>
        )}
      </div>
    </>
  );
}
