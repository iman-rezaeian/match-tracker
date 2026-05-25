import React, { useState, useEffect, useMemo } from 'react';
import {
  Plus, Users, Trash2, Edit3, ChevronLeft,
  PlayCircle, Undo2, X, ChevronRight,
  BarChart3, Flag, Zap
} from 'lucide-react';

const STORAGE_KEYS = { ROSTER: 'roster', GAMES: 'games' };

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
  GATES:     { id: 'GATES',     label: 'GATE PASS', emoji: '🚪', tone: 'blue',       requiresPlayer: true },
  TURNOVER:  { id: 'TURNOVER',  label: 'TURNOVER',  emoji: '💨', tone: 'soft-red',   requiresPlayer: true },
  HOLDS_BALL:{ id: 'HOLDS_BALL',label: 'HOLDS BALL',emoji: '⏳', tone: 'yellow',     requiresPlayer: true },
  OPP_GOAL:  { id: 'OPP_GOAL',  label: 'OPP GOAL',  emoji: '🚨', tone: 'big-red',    requiresPlayer: false, delta: 'opp' },
};

const TONE_CLASSES = {
  'big-green':  'bg-lime-500 hover:bg-lime-600 text-white shadow-lg shadow-lime-500/30 border-lime-600',
  'big-red':    'bg-red-500 hover:bg-red-600 text-white shadow-lg shadow-red-500/30 border-red-600',
  'soft-green': 'bg-lime-50 hover:bg-lime-100 text-lime-900 border-lime-200',
  'soft-red':   'bg-red-50 hover:bg-red-100 text-red-900 border-red-200',
  'blue':       'bg-sky-50 hover:bg-sky-100 text-sky-900 border-sky-200',
  'yellow':     'bg-yellow-50 hover:bg-yellow-100 text-yellow-900 border-yellow-300',
  'purple':     'bg-violet-100 hover:bg-violet-200 text-violet-900 border-violet-300',
  'neutral':    'bg-stone-50 hover:bg-stone-100 text-stone-900 border-stone-200',
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
  @import url('https://fonts.googleapis.com/css2?family=Anton&family=Outfit:wght@400;500;600;700;800;900&display=swap');
  .font-display { font-family: 'Anton', system-ui, sans-serif; letter-spacing: 0.03em; }
  .font-sans-pro { font-family: 'Outfit', system-ui, sans-serif; }
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
function computePerformanceScore(playerId, events, minutesPlayed, position, gkExtras = {}) {
  if (minutesPlayed <= 0) return { overall: 0, attacking: 0, defending: 0, decisions: 0, involvement: 0 };
  const isGK = position === 'GK';
  const perHalf = minutesPlayed / 20;
  const c = {};
  for (const e of events) {
    if (e.playerId === playerId && e.type !== 'SUB') {
      c[e.type] = (c[e.type] || 0) + 1;
    }
  }
  const attacking = (
    (c.GOAL || 0) * 10 +
    (c.ASSIST || 0) * 8 +
    (c.KEY_PASS || 0) * (isGK ? 10 : 5) +
    (c.SHOT_ON || 0) * 3 +
    (c.SHOT_OFF || 0) * 1
  ) / perHalf;
  const oppConceded = isGK ? (gkExtras.oppGoalsConceded || 0) : 0;
  const concededPenalty = isGK ? (gkExtras.concededPenalty || 0) : 0;
  const cleanSheets = isGK ? (gkExtras.cleanSheets || 0) : 0;
  const defending = (
    (c.SAVE || 0) * (isGK ? 10 : 7) +
    (c.BLOCK || 0) * 5 +
    (c.BALL_WIN || 0) * 5 +
    (c.DUEL_WIN || 0) * 4 +
    (c.DUEL_LOSE || 0) * -1 +
    -concededPenalty +
    cleanSheets * 8
  ) / perHalf;
  const decisions = (
    (c.GIVE_GO || 0) * 6 +
    (c.GATES || 0) * 4 +
    (c.KEY_PASS || 0) * (isGK ? 6 : 3) +
    (c.ASSIST || 0) * 3 +
    (c.HOLDS_BALL || 0) * -4 +
    (c.TURNOVER || 0) * -4
  ) / perHalf;
  const totalEvents = Object.values(c).reduce((a, b) => a + b, 0);
  const involvement = totalEvents / perHalf;
  // Outfield: 30/25/30/15. GK: 10/55/25/10 — DEF dominates, ATK de-weighted.
  const overall = isGK
    ? 0.10 * attacking + 0.55 * defending + 0.25 * decisions + 0.10 * involvement
    : 0.30 * attacking + 0.25 * defending + 0.30 * decisions + 0.15 * involvement;
  const r = (n) => Math.round(n * 10) / 10;
  return { overall: r(overall), attacking: r(attacking), defending: r(defending), decisions: r(decisions), involvement: r(involvement) };
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

const TEAM_CODE = '2016';

export default function App() {
  const [unlocked, setUnlocked] = useState(() => {
    try { return localStorage.getItem('stompers_unlocked') === 'true'; } catch(e) { return false; }
  });
  const [roster, setRoster] = useState([]);
  const [games, setGames] = useState([]);
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
      setLoaded(true);
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

  const startNewGame = (opponent, isHome, tournament, startingLineup, gkPlayerId, squad) => {
    const now = Date.now();
    const squadIds = (squad && squad.length > 0) ? squad : (startingLineup || []);
    const game = {
      id: uid(),
      opponent: opponent || 'Opponent',
      tournament: tournament || 'Festival',
      isHome: !!isHome,
      date: new Date().toISOString(),
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
    showToast(`${ev.emoji} ${ev.label}${playerLabel ? ` · ${playerLabel}` : ''}`);

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

  const logSubEvent = (gameId, offPlayerId, onPlayerId) => {
    const game = games.find(g => g.id === gameId);
    if (!game) return;
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
    const change = { at: atTs || Date.now(), gkPlayerId: newGKPlayerId || null };
    const updated = { ...game, gkChanges: [...(game.gkChanges || []), change] };
    persistGames(games.map(g => g.id === gameId ? updated : g));
    const p = roster.find(pl => pl.id === newGKPlayerId);
    showToast(`🧤 ${p?.name || 'No GK'} now in goal`);
    setPendingEvent(null);
  };

  if (!unlocked) {
    return <LockScreen onUnlock={() => {
      try { localStorage.setItem('stompers_unlocked', 'true'); } catch(e) {}
      setUnlocked(true);
    }} />;
  }

  if (!loaded) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <style>{FONT_STYLES}</style>
        <div className="font-sans-pro text-stone-500">Loading…</div>
      </div>
    );
  }

  const activeGame = games.find(g => g.id === activeGameId) || games.find(g => g.status === 'active');
  const viewingGame = games.find(g => g.id === viewingGameId);

  return (
    <div className="min-h-screen bg-stone-50 font-sans-pro text-stone-900">
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
          activeGame={activeGame}
          onGoRoster={() => setView('roster')}
          onNewGame={() => setView('gameSetup')}
          onResumeGame={() => { setActiveGameId(activeGame.id); setView('activeGame'); }}
          onViewGame={(id) => { setViewingGameId(id); setView('gameDetail'); }}
          onViewStats={() => setView('stats')}
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
          onStart={(opponent, isHome, tournament) => {
            setPendingGameSetup({ opponent, isHome, tournament });
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
          onBack={() => { setView('squad'); }}
          onStart={(lineup, gkPlayerId) => {
            startNewGame(pendingGameSetup.opponent, pendingGameSetup.isHome, pendingGameSetup.tournament, lineup, gkPlayerId, pendingGameSetup.squad);
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
              setPendingEvent({ type: 'SUB', step: 'ON', offPlayerId: playerId });
              return;
            }
            if (pendingEvent?.type === 'SUB' && pendingEvent.step === 'ON') {
              logSubEvent(activeGame.id, pendingEvent.offPlayerId, playerId);
              return;
            }
            const t = typeof pendingEvent === 'string' ? pendingEvent : pendingEvent?.type;
            logEvent(activeGame.id, t, playerId);
          }}
          onCancelEvent={() => setPendingEvent(null)}
          onUndo={() => undoLastEvent(activeGame.id)}
          onPauseHalfTime={() => pauseHalfTime(activeGame.id)}
          onStartSecondHalf={() => startSecondHalf(activeGame.id)}
          onResumeFirstHalf={() => resumeFirstHalf(activeGame.id)}
          onEnd={() => askConfirm('End game and save final score?', () => endGame(activeGame.id))}
          onBack={() => setView('home')}
          tick={tick}
        />
      )}

      {view === 'gameDetail' && viewingGame && (
        <GameDetail
          game={viewingGame}
          roster={roster}
          onBack={() => setView('home')}
          onDelete={() => askConfirm('Delete this game permanently?', () => deleteGame(viewingGame.id), { danger: true, yesLabel: 'DELETE' })}
          onDeleteEvent={(eid) => deleteEvent(viewingGame.id, eid)}
        />
      )}

      {view === 'stats' && (
        <StatsView roster={roster} games={games} onBack={() => setView('home')} />
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
        className="bg-white rounded-t-3xl sm:rounded-2xl w-full sm:max-w-sm p-5 pb-8 sm:pb-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-stone-900 text-center font-semibold text-base mb-5 pt-2">{message}</div>
        <div className="grid grid-cols-2 gap-2.5">
          <button
            onClick={onCancel}
            className="py-4 rounded-xl bg-stone-100 text-stone-700 font-display text-lg active:scale-[0.98] transition"
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
function HomeView({ roster, games, activeGame, onGoRoster, onNewGame, onResumeGame, onViewGame, onViewStats }) {
  const finishedGames = games.filter(g => g.status === 'finished');
  const wins = finishedGames.filter(g => g.ourScore > g.oppScore).length;
  const losses = finishedGames.filter(g => g.ourScore < g.oppScore).length;
  const draws = finishedGames.filter(g => g.ourScore === g.oppScore).length;

  return (
    <div className="pb-24">
      <div className="stripes-bg text-white px-5 pt-12 pb-8">
        <div className="flex items-start gap-3">
          <img
            src="./stompers_logo.png"
            alt=""
            className="w-16 h-16 shrink-0 drop-shadow"
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

      <div className="px-4 pt-6">
        <button
          onClick={onNewGame}
          className="w-full bg-lime-500 hover:bg-lime-600 text-stone-900 font-display text-3xl py-6 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.99] transition flex items-center justify-center gap-3"
        >
          <Zap className="w-7 h-7" />
          START GAME
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 px-4 pt-3">
        <TileButton onClick={onGoRoster} icon={<Users className="w-6 h-6" />} label="ROSTER" sub={`${roster.length} players`} />
        <TileButton onClick={onViewStats} icon={<BarChart3 className="w-6 h-6" />} label="STATS" sub="Season totals" />
      </div>

      <div className="px-4 pt-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-display text-2xl">PAST GAMES</h2>
          <div className="text-xs text-stone-500 font-semibold">{finishedGames.length} total</div>
        </div>
        {finishedGames.length === 0 ? (
          <div className="bg-white border border-stone-200 rounded-2xl p-6 text-center text-stone-500 text-sm">
            No games yet. Tap <span className="font-bold text-stone-900">START GAME</span> when you're at the field.
          </div>
        ) : (
          <div className="space-y-2">
            {finishedGames.slice(0, 10).map(g => (
              <button
                key={g.id}
                onClick={() => onViewGame(g.id)}
                className="w-full bg-white border border-stone-200 rounded-xl p-3 flex items-center gap-3 active:scale-[0.99] transition text-left"
              >
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center font-display text-base ${
                  g.ourScore > g.oppScore ? 'bg-lime-100 text-lime-800' :
                  g.ourScore < g.oppScore ? 'bg-red-100 text-red-800' :
                  'bg-stone-100 text-stone-700'
                }`}>
                  {g.ourScore > g.oppScore ? 'W' : g.ourScore < g.oppScore ? 'L' : 'D'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">vs {g.opponent}</div>
                  <div className="text-xs text-stone-500 truncate">{g.tournament || 'Festival'} · {formatDate(g.date)}</div>
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
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div>
      <div className={`font-display text-4xl ${accent}`}>{value}</div>
      <div className="text-[10px] text-white/60 font-bold tracking-widest">{label}</div>
    </div>
  );
}

function TileButton({ onClick, icon, label, sub }) {
  return (
    <button
      onClick={onClick}
      className="bg-white border border-stone-200 rounded-2xl p-4 text-left active:scale-[0.98] transition shadow-sm"
    >
      <div className="w-10 h-10 rounded-xl bg-stone-900 text-lime-400 flex items-center justify-center mb-2">
        {icon}
      </div>
      <div className="font-display text-lg leading-none">{label}</div>
      <div className="text-xs text-stone-500 mt-1">{sub}</div>
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
        <button onClick={onAdd} className="bg-lime-500 text-stone-900 w-10 h-10 rounded-full flex items-center justify-center font-bold shadow active:scale-95 transition">
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
          <div className="text-[11px] text-stone-500 mt-1.5 text-center px-2">
            Pick multiple files — names like <span className="font-mono">#10.PNG</span> match by jersey number.
          </div>
        </div>
      )}

      <div className="px-4 pt-4">
        {sorted.length === 0 ? (
          <div className="bg-white border border-stone-200 rounded-2xl p-8 text-center">
            <Users className="w-10 h-10 text-stone-300 mx-auto mb-3" />
            <div className="font-display text-xl mb-1">NO PLAYERS YET</div>
            <div className="text-sm text-stone-500 mb-4">Add your squad to start logging games.</div>
            <button onClick={onAdd} className="bg-stone-900 text-white px-5 py-3 rounded-full font-bold text-sm">
              + Add first player
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {sorted.map(p => (
              <div key={p.id} className="bg-white border border-stone-200 rounded-xl p-3 flex items-center gap-3">
                <PlayerAvatar player={p} />
                <div className="flex-1 min-w-0">
                  <div className="font-bold truncate">{p.name}</div>
                  {p.position && <div className="text-xs text-stone-500 uppercase tracking-wide">{p.position}</div>}
                </div>
                <button onClick={() => onEdit(p)} className="w-9 h-9 rounded-full bg-stone-100 flex items-center justify-center active:scale-95">
                  <Edit3 className="w-4 h-4 text-stone-700" />
                </button>
                <button
                  onClick={() => onDelete(p)}
                  className="w-9 h-9 rounded-full bg-red-50 flex items-center justify-center active:scale-95"
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
            <div className="w-24 h-24 rounded-2xl overflow-hidden bg-stone-100 border-2 border-stone-200 flex items-center justify-center shrink-0">
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
                  className="bg-stone-100 text-stone-600 font-bold text-xs px-4 py-2 rounded-xl active:scale-[0.98] transition"
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
            className="w-full bg-white border-2 border-stone-200 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
        </Field>

        <Field label="JERSEY NUMBER">
          <input
            type="number"
            inputMode="numeric"
            value={number}
            onChange={e => setNumber(e.target.value)}
            placeholder="0"
            className="w-full bg-white border-2 border-stone-200 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-2xl font-display tabular-nums"
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
                    : 'bg-white text-stone-700 border-stone-200'
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
          className="w-full bg-lime-500 disabled:bg-stone-300 disabled:text-stone-500 text-stone-900 font-display text-2xl py-4 rounded-2xl shadow-lg shadow-lime-500/20 border-2 border-lime-600 disabled:border-stone-300 active:scale-[0.99] transition mt-4"
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
      <label className="block text-xs font-bold tracking-widest text-stone-500 mb-2">{label}</label>
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
      <div className={`${sizeClass} ${rounded} overflow-hidden bg-stone-100 shrink-0`}>
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
  const [isHome, setIsHome] = useState(true);

  if (rosterCount === 0) {
    return (
      <div>
        <Header title="NEW GAME" onBack={onCancel} />
        <div className="p-6 text-center">
          <div className="bg-white border border-stone-200 rounded-2xl p-8">
            <Users className="w-10 h-10 text-stone-300 mx-auto mb-3" />
            <div className="font-display text-xl mb-2">ADD PLAYERS FIRST</div>
            <div className="text-sm text-stone-500 mb-4">You need a roster before starting a game.</div>
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
            className="w-full bg-white border-2 border-stone-200 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
        </Field>

        <Field label="OPPONENT">
          <input
            type="text"
            value={opponent}
            onChange={e => setOpponent(e.target.value)}
            placeholder="e.g., Lions FC"
            className="w-full bg-white border-2 border-stone-200 focus:border-stone-900 outline-none rounded-xl px-4 py-3 text-lg font-semibold"
          />
        </Field>

        <Field label="LOCATION">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setIsHome(true)}
              className={`py-4 rounded-xl font-display text-2xl border-2 transition ${
                isHome ? 'bg-stone-900 text-lime-400 border-stone-900' : 'bg-white text-stone-700 border-stone-200'
              }`}
            >
              HOME
            </button>
            <button
              type="button"
              onClick={() => setIsHome(false)}
              className={`py-4 rounded-xl font-display text-2xl border-2 transition ${
                !isHome ? 'bg-stone-900 text-lime-400 border-stone-900' : 'bg-white text-stone-700 border-stone-200'
              }`}
            >
              AWAY
            </button>
          </div>
        </Field>

        <button
          onClick={() => onStart(opponent.trim() || 'Opponent', isHome, tournament.trim() || 'Festival')}
          className="w-full bg-lime-500 text-stone-900 font-display text-3xl py-5 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.99] transition mt-4 flex items-center justify-center gap-3"
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
        <div className="text-xs text-stone-500 mb-1">vs {setup.opponent} · {setup.isHome ? 'Home' : 'Away'}</div>
        <div className="text-sm text-stone-700 mb-3">
          Tap players who are <span className="font-bold">available for this match</span>. Unchecked players are OUT.
          Soft limit is <span className="font-bold">{SOFT_CAP}</span> (7v7 max squad) — you can exceed it if you need to.
        </div>

        <div className="flex gap-2 mb-4">
          <button onClick={selectAll} className="flex-1 py-2 bg-stone-100 rounded-lg text-xs font-bold tracking-wider text-stone-700 active:scale-95">ALL IN</button>
          <button onClick={clearAll} className="flex-1 py-2 bg-stone-100 rounded-lg text-xs font-bold tracking-wider text-stone-700 active:scale-95">ALL OUT</button>
        </div>

        <div className="space-y-1.5">
          {sorted.map(p => {
            const on = selected.has(p.id);
            return (
              <button
                key={p.id}
                onClick={() => toggle(p.id)}
                className={`w-full flex items-center gap-3 p-2.5 rounded-xl border-2 text-left active:scale-[0.98] transition ${
                  on ? 'bg-lime-50 border-lime-400' : 'bg-white border-stone-200 opacity-60'
                }`}
              >
                <PlayerAvatar
                  player={p}
                  sizeClass="w-11 h-11"
                  textSize="text-xl"
                  numberClasses={on ? 'bg-stone-900 text-lime-400' : 'bg-stone-200 text-stone-500'}
                />
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">{p.name}</div>
                  <div className={`text-[10px] font-bold tracking-wider ${on ? 'text-lime-700' : 'text-stone-400'}`}>
                    {on ? 'AVAILABLE' : 'OUT'}{p.position ? ` · ${p.position}` : ''}
                  </div>
                </div>
                <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 ${
                  on ? 'bg-lime-500 border-lime-600' : 'bg-white border-stone-300'
                }`}>
                  {on && <span className="text-white text-sm font-bold">✓</span>}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-stone-200 p-4 shadow-xl">
        <div className={`text-center text-sm mb-2 ${overCap ? 'text-amber-700' : 'text-stone-600'}`}>
          <span className={`font-bold ${overCap ? 'text-amber-700' : 'text-lime-700'}`}>{selected.size}</span> in squad
          {overCap && <span className="ml-1 text-xs">⚠ over {SOFT_CAP}</span>}
        </div>
        <button
          onClick={() => canProceed && onNext(Array.from(selected))}
          disabled={!canProceed}
          className={`w-full font-display text-2xl py-4 rounded-2xl shadow-lg border-2 active:scale-[0.99] transition ${
            canProceed
              ? 'bg-stone-900 text-lime-400 border-stone-900'
              : 'bg-stone-200 text-stone-400 border-stone-300 cursor-not-allowed'
          }`}
        >
          NEXT: STARTING LINEUP →
        </button>
      </div>
    </div>
  );
}

/* ---------- STARTING LINEUP ---------- */
function StartingLineupView({ roster, squad, setup, onBack, onStart }) {
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
        <div className="text-xs text-stone-500 mb-1">vs {setup.opponent} · {setup.isHome ? 'Home' : 'Away'}</div>
        <div className="text-sm text-stone-700 mb-3">Tap a player to put them on the field. Tap the <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-bold text-[10px]">🧤 GK</span> button on the right to assign the goalie.</div>

        <div className="flex gap-2 mb-4">
          <button onClick={selectAll} className="flex-1 py-2 bg-stone-100 rounded-lg text-xs font-bold tracking-wider text-stone-700 active:scale-95">ALL ON</button>
          <button onClick={clearAll} className="flex-1 py-2 bg-stone-100 rounded-lg text-xs font-bold tracking-wider text-stone-700 active:scale-95">ALL BENCH</button>
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
                    ? (isGK ? 'bg-amber-50 border-amber-400' : 'bg-lime-50 border-lime-400')
                    : (isDefaultGK ? 'bg-amber-50/50 border-amber-200 opacity-60' : 'bg-white border-stone-200 opacity-60')
                }`}
              >
                {isGK && (
                  <div className="absolute -top-2 -left-2 bg-amber-400 text-stone-900 text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full border border-amber-600 shadow-sm flex items-center gap-0.5 z-10">
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
                      ? (isGK ? 'bg-amber-500 text-stone-900' : 'bg-stone-900 text-lime-400')
                      : 'bg-stone-200 text-stone-500'}
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
                      : 'bg-white border-stone-300'
                  }`}>
                    {on && <span className="text-white text-sm font-bold">✓</span>}
                  </div>
                </button>
                <button
                  onClick={(e) => pickGK(p.id, e)}
                  className={`shrink-0 w-12 flex flex-col items-center justify-center text-[10px] font-extrabold tracking-wider border-l-2 active:scale-[0.95] transition ${
                    isGK
                      ? 'bg-amber-400 text-stone-900 border-amber-500'
                      : 'bg-white text-stone-400 border-stone-200 hover:text-amber-600'
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

      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-stone-200 p-4 shadow-xl">
        <div className="text-center text-sm text-stone-600 mb-1">
          <span className="font-bold text-lime-700">{selected.size}</span> on field · <span className="font-bold text-stone-500">{sorted.length - selected.size}</span> on bench
        </div>
        <div className="text-center text-xs mb-2">
          {gkPlayer ? (
            <span className="text-amber-700 font-bold">🧤 GK: {gkPlayer.name} #{gkPlayer.number}</span>
          ) : (
            <span className="text-red-600 font-bold">⚠ No goalie selected — tap the GK button on a player.</span>
          )}
        </div>
        <button
          onClick={() => onStart(Array.from(selected), gkId)}
          disabled={!gkId}
          className={`w-full font-display text-2xl py-4 rounded-2xl shadow-lg border-2 active:scale-[0.99] transition ${
            gkId
              ? 'bg-lime-500 text-stone-900 shadow-lime-500/30 border-lime-600'
              : 'bg-stone-200 text-stone-400 border-stone-300 cursor-not-allowed'
          }`}
        >
          ▶ START GAME
        </button>
      </div>
    </div>
  );
}

/* ---------- ACTIVE GAME ---------- */
function ActiveGameView({ game, roster, pendingEvent, onSelectEvent, onSelectPlayer, onResolveOppGoal, onConfirmGK, onSwapGK, onCancelEvent, onUndo, onPauseHalfTime, onStartSecondHalf, onResumeFirstHalf, onEnd, onBack, tick }) {
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

  const [quickMode, setQuickMode] = useState(() => {
    try { return localStorage.getItem('stompers_quick_mode') === 'true'; } catch(e) { return false; }
  });
  const toggleQuickMode = () => {
    setQuickMode(prev => {
      const next = !prev;
      try { localStorage.setItem('stompers_quick_mode', next ? 'true' : 'false'); } catch(e) {}
      return next;
    });
  };

  const inFirstHalf = game.period === 1 && game.clockRunning !== false;
  const inHalfTimeBreak = game.period === 1 && game.clockRunning === false;
  const inSecondHalf = game.period === 2;

  const statusLabel = inHalfTimeBreak ? 'HALF TIME' : inSecondHalf ? '2ND HALF' : '1ST HALF';
  const statusColor = inHalfTimeBreak ? 'bg-amber-400 text-stone-900' : 'bg-stone-900 text-lime-400';

  return (
    <div className="min-h-screen flex flex-col">
      <div className="stripes-bg text-white px-4 pt-12 pb-4">
        <div className="flex items-center justify-between mb-3">
          <button onClick={onBack} className="text-white/70 active:scale-95">
            <ChevronLeft className="w-6 h-6" />
          </button>
          <div className="text-center">
            <div className="text-xs text-white font-bold tracking-widest truncate max-w-[180px]">
              {game.tournament || 'Festival'}
            </div>
            <div className="text-[10px] text-white/50">
              {formatDate(game.date)}
            </div>
          </div>
          <div className="bg-white/10 text-white/90 px-3 py-1.5 rounded-full text-[10px] font-bold tracking-widest">
            {game.isHome ? 'HOME' : 'AWAY'}
          </div>
        </div>

        <div className="grid grid-cols-3 items-center gap-3">
          <div className="text-center">
            <div className="text-[10px] font-bold tracking-widest text-lime-400">US</div>
            <div className="font-display text-7xl leading-none tabular-nums">{game.ourScore}</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] font-bold tracking-widest text-white/50">
              {game.clockRunning === false ? 'PAUSED' : 'CLOCK'}
            </div>
            <div className={`font-display text-3xl tabular-nums ${game.clockRunning === false ? 'text-white/50' : 'text-white/90'}`}>
              {formatClock(elapsed)}
            </div>
            <div className="text-[10px] text-white/50 mt-0.5 truncate">vs {game.opponent}</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] font-bold tracking-widest text-red-400">OPP</div>
            <div className="font-display text-7xl leading-none tabular-nums">{game.oppScore}</div>
          </div>
        </div>
      </div>

      {!pendingEvent && (
        <div className="px-4 pt-3 flex items-center gap-2">
          <div className={`${statusColor} flex-1 rounded-full py-2.5 text-center font-display text-base tracking-widest shadow`}>
            {statusLabel}
          </div>
          {(() => {
            const gk = roster.find(p => p.id === gameGKId);
            return (
              <button
                onClick={onSwapGK}
                className={`shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 active:scale-95 transition flex items-center gap-1 ${
                  gk
                    ? 'bg-amber-400 text-stone-900 border-amber-500 shadow'
                    : 'bg-red-100 text-red-700 border-red-300 animate-pulse'
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
              className="shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 bg-white text-stone-600 border-stone-200 active:scale-95 transition flex items-center gap-1"
              title="Live minutes played"
            >
              <span>⏱</span>
              <span>MINS</span>
            </button>
          )}
          {!inHalfTimeBreak && (
            <button
              onClick={toggleQuickMode}
              className={`shrink-0 rounded-full px-3 py-2.5 font-display text-xs tracking-widest border-2 transition active:scale-95 ${
                quickMode
                  ? 'bg-amber-400 text-stone-900 border-amber-500 shadow'
                  : 'bg-white text-stone-600 border-stone-200'
              }`}
              title="Toggle Quick-Tap Mode"
            >
              ⚡ {quickMode ? 'QUICK' : 'FULL'}
            </button>
          )}
        </div>
      )}

      <div className="flex-1 flex flex-col px-4 pt-4">
        {pendingEvent?.type === 'MINS_VIEW' ? (() => {
          const onField = onFieldAt(game);
          const rows = [...playersSorted].sort((a, b) => (secondsByPlayer[b.id] || 0) - (secondsByPlayer[a.id] || 0));
          const maxSec = Math.max(1, ...rows.map(p => secondsByPlayer[p.id] || 0));
          return (
            <div className="flex flex-col h-full min-h-0">
              <div className="flex items-center justify-between mb-3 shrink-0">
                <div>
                  <div className="text-xs text-stone-500 font-bold tracking-widest">LIVE</div>
                  <div className="font-display text-3xl flex items-center gap-2"><span>⏱</span><span>MINUTES</span></div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-200 flex items-center justify-center active:scale-95">
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
                      isGK ? 'bg-amber-50 border-amber-300' : isOn ? 'bg-lime-50 border-lime-300' : 'bg-white border-stone-200'
                    }`}>
                      <div className="absolute inset-y-0 left-0 rounded-xl opacity-30" style={{ width: `${pct}%`, background: isGK ? '#fbbf24' : isOn ? '#a3e635' : '#e7e5e4' }} />
                      <div className="relative z-10 flex items-center gap-3 w-full">
                        <PlayerAvatar player={p} sizeClass="w-10 h-10" textSize="text-lg" numberClasses={isGK ? 'bg-amber-500 text-stone-900' : isOn ? 'bg-stone-900 text-lime-400' : 'bg-stone-200 text-stone-500'} />
                        <div className="flex-1 min-w-0">
                          <div className="font-bold text-sm truncate">{p.name}</div>
                          <div className="text-[10px] font-bold tracking-wider text-stone-500">
                            {isGK ? '🧤 IN GOAL' : isOn ? 'ON FIELD' : 'BENCH'}{p.position ? ` · ${p.position}` : ''}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="font-display text-2xl tabular-nums leading-none">{min}</div>
                          <div className="text-[9px] font-bold tracking-wider text-stone-500">MIN</div>
                        </div>
                      </div>
                    </div>
                  );
                })}
                {rows.length === 0 && (
                  <div className="bg-white border border-stone-200 rounded-xl p-6 text-center text-stone-500 text-sm">
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
                  <div className="text-xs text-stone-500 font-bold tracking-widest">NEW GOALIE</div>
                  <div className="font-display text-3xl flex items-center gap-2">
                    <span>🧤</span>
                    <span>WHO'S IN GOAL?</span>
                  </div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-200 flex items-center justify-center active:scale-95">
                  <X className="w-5 h-5" />
                </button>
              </div>
              {currentGKPlayer && (
                <div className="mb-3 bg-stone-100 border border-stone-200 rounded-xl px-3 py-2 text-xs text-stone-600">
                  Current GK: <span className="font-bold text-stone-800">{currentGKPlayer.name} #{currentGKPlayer.number}</span>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2.5 pb-6 overflow-y-auto">
                {candidates.length === 0 && (
                  <div className="col-span-2 bg-white border border-stone-200 rounded-xl p-6 text-center text-stone-500 text-sm">
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
                        isDefault ? 'bg-amber-100 border-amber-500' : 'bg-white border-stone-200 hover:border-amber-400'
                      }`}
                    >
                      {isCurrent && (
                        <div className="absolute -top-2 -right-2 bg-stone-700 text-white text-[9px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full shadow-sm">CURRENT</div>
                      )}
                      <PlayerAvatar player={p} numberClasses="bg-amber-500 text-stone-900" />
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
                className="mt-1 w-full bg-white text-stone-600 border border-stone-300 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
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
                  <div className="text-xs text-stone-500 font-bold tracking-widest">OPP GOAL — GK FAULT?</div>
                  <div className="font-display text-3xl flex items-center gap-2">
                    <span>🚨</span>
                    <span>WHY?</span>
                  </div>
                </div>
                <button onClick={onCancelEvent} className="w-11 h-11 rounded-full bg-stone-200 flex items-center justify-center active:scale-95">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {gkOnField.length > 0 && (
                <div className="mb-3 bg-amber-50 border border-amber-300 rounded-xl px-3 py-2 flex items-center gap-2">
                  <span className="text-lg">🧤</span>
                  <div className="text-xs text-amber-800">
                    <span className="font-bold tracking-wider">GK ON FIELD:</span>{' '}
                    {gkOnField.map(p => `${p.name} #${p.number}`).join(', ')}
                  </div>
                </div>
              )}
              {gkOnField.length === 0 && (
                <div className="mb-3 bg-stone-100 border border-stone-200 rounded-xl px-3 py-2 text-xs text-stone-600">
                  No goalie set for this match. Pick a tag anyway — it'll be saved for record.
                </div>
              )}

              <button
                onClick={() => onResolveOppGoal('gk')}
                className="mb-2 w-full bg-red-100 text-red-900 border-2 border-red-400 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
              >
                <span className="text-3xl">🧤</span>
                <span>GK FAULT</span>
              </button>
              <button
                onClick={() => onResolveOppGoal('unstoppable')}
                className="mb-2 w-full bg-stone-100 text-stone-800 border-2 border-stone-400 font-display text-2xl py-5 rounded-2xl active:scale-[0.98] transition flex items-center justify-center gap-3"
              >
                <span className="text-3xl">😮</span>
                <span>UNSTOPPABLE</span>
              </button>
              <button
                onClick={() => onResolveOppGoal(null)}
                className="w-full bg-white text-stone-600 border border-stone-300 font-display text-base py-3 rounded-xl active:scale-[0.98] transition"
              >
                NEUTRAL / UNSURE
              </button>
            </div>
          );
        })() : pendingEvent ? (() => {
          const isSub = pendingEvent.type === 'SUB';
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
                : (!isSub && pickerPlayers.length === 0 ? 'No players on the field.' : null)}
            />
          );
        })() : inHalfTimeBreak ? (
          <div className="flex flex-col items-center text-center pt-6">
            <div className="text-6xl mb-3">⏸️</div>
            <div className="font-display text-4xl mb-2">HALF TIME</div>
            <div className="text-stone-500 text-sm mb-8 max-w-xs">
              Clock is paused. Tap below when the 2nd half kicks off.
            </div>
            <button
              onClick={onStartSecondHalf}
              className="w-full max-w-sm bg-lime-500 text-stone-900 font-display text-2xl py-5 rounded-2xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.98] transition flex items-center justify-center gap-2"
            >
              <span>▶</span>
              <span>START 2ND HALF</span>
            </button>
            <button
              onClick={onResumeFirstHalf}
              className="mt-3 text-stone-500 font-bold text-sm active:scale-95"
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
            {quickMode ? (
              /* QUICK-TAP MODE: 4 huge buttons for high-tempo stretches */
              <div className="grid grid-cols-2 gap-3">
                {[
                  { id: 'GOAL',     emoji: '⚽', label: 'GOAL',     tone: 'big-green' },
                  { id: 'BALL_WIN', emoji: '🔥', label: 'BALL WIN', tone: 'soft-green' },
                  { id: 'TURNOVER', emoji: '💨', label: 'TURNOVER', tone: 'soft-red' },
                  { id: 'OPP_GOAL', emoji: '🚨', label: 'OPP GOAL', tone: 'big-red' },
                ].map(b => (
                  <button
                    key={b.id}
                    onClick={() => onSelectEvent(b.id)}
                    className={`${TONE_CLASSES[b.tone]} border-2 rounded-2xl py-10 flex flex-col items-center justify-center gap-2 active:scale-[0.97] transition`}
                  >
                    <div className="text-5xl">{b.emoji}</div>
                    <div className="font-sans-pro font-extrabold tracking-tight text-xl leading-none text-center">{b.label}</div>
                  </button>
                ))}
              </div>
            ) : (
              <>
              {/* 4x3 grid: 12 primary events. ASSIST is removed — it's captured
                  automatically from the GOAL flow, so a standalone button would
                  let an assist be logged without an actual goal. */}
              <div className="grid grid-cols-4 gap-2">
                {['GOAL', 'KEY_PASS', 'BALL_WIN', 'HOLDS_BALL', 'SHOT_ON', 'SHOT_OFF', 'SAVE', 'BLOCK', 'DUEL_WIN', 'DUEL_LOSE', 'GIVE_GO', 'GATES'].map(id => {
                  const ev = EVENT_TYPES[id];
                  const big = ev.tone === 'big-green';
                  return (
                    <button
                      key={ev.id}
                      onClick={() => onSelectEvent(ev.id)}
                      className={`${TONE_CLASSES[ev.tone]} border-2 rounded-2xl ${big ? 'py-5' : 'py-4'} flex flex-col items-center justify-center gap-1 active:scale-[0.97] transition`}
                    >
                      <div className={`${big ? 'text-3xl' : 'text-2xl'}`}>{ev.emoji}</div>
                      <div className={`font-sans-pro font-extrabold tracking-tight ${big ? 'text-base' : 'text-xs'} leading-none text-center`}>{ev.label}</div>
                    </button>
                  );
                })}
              </div>

              <div className="mt-2">
                <button
                  onClick={() => onSelectEvent('TURNOVER')}
                  className={`${TONE_CLASSES['soft-red']} border-2 rounded-2xl py-3.5 w-full flex items-center justify-center gap-2 active:scale-[0.97] transition`}
                >
                  <span className="text-xl">💨</span>
                  <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">TURNOVER</span>
                </button>
              </div>

              <button
                onClick={() => onSelectEvent('OPP_GOAL')}
                className={`mt-2 w-full ${TONE_CLASSES['big-red']} border-2 rounded-2xl py-3.5 flex items-center justify-center gap-3 active:scale-[0.97] transition`}
              >
                <span className="text-2xl">🚨</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-lg leading-none">OPP GOAL</span>
              </button>
              </>
            )}

            <div className="grid grid-cols-2 gap-2 mt-2.5">
              <button
                onClick={() => onSelectEvent('SUB')}
                className={`${TONE_CLASSES['purple']} border-2 rounded-2xl py-3.5 flex items-center justify-center gap-2 active:scale-[0.97] transition`}
              >
                <span className="text-2xl">🔄</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SUBSTITUTION</span>
              </button>
              <button
                onClick={onSwapGK}
                className="bg-amber-100 text-amber-900 border-2 border-amber-400 rounded-2xl py-3.5 flex items-center justify-center gap-2 active:scale-[0.97] transition"
              >
                <span className="text-2xl">🧤</span>
                <span className="font-sans-pro font-extrabold tracking-tight text-base leading-none">SWAP GK</span>
              </button>
            </div>

            {inFirstHalf ? (
              <button
                onClick={onPauseHalfTime}
                className="mt-2.5 w-full bg-amber-500 text-stone-900 font-display text-xl py-4 rounded-2xl active:scale-[0.99] transition border-2 border-amber-600 shadow"
              >
                ⏸ HALF TIME
              </button>
            ) : (
              <button
                onClick={onEnd}
                className="mt-2.5 w-full bg-stone-900 text-white font-display text-xl py-4 rounded-2xl active:scale-[0.99] transition"
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
                    className="text-xs font-bold text-stone-500 flex items-center gap-1 active:scale-95 bg-stone-100 px-3 py-1.5 rounded-full"
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
          <div className="text-xs text-stone-500 font-bold tracking-widest">WHO?</div>
          <div className="font-display text-3xl flex items-center gap-2">
            <span>{event.emoji}</span>
            <span>{event.label}</span>
          </div>
        </div>
        <button onClick={onCancel} className="w-11 h-11 rounded-full bg-stone-200 flex items-center justify-center active:scale-95">
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
          className="mb-3 w-full bg-stone-50 text-stone-600 font-display text-sm py-2.5 rounded-xl border border-stone-200 active:scale-[0.98] transition"
        >
          NO {event.label}
        </button>
      )}

      <div className="grid grid-cols-2 gap-2.5 pb-6 overflow-y-auto">
        {players.length === 0 && emptyMessage && (
          <div className="col-span-2 bg-white border border-stone-200 rounded-xl p-6 text-center text-stone-500 text-sm">
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
                  ? 'bg-amber-50 border-amber-400 hover:border-amber-500'
                  : 'bg-white border-stone-200 hover:border-stone-900'
              }`}
            >
              {isGK && (
                <div className="absolute -top-2 -right-2 bg-amber-400 text-stone-900 text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded-full border border-amber-600 shadow-sm flex items-center gap-0.5">
                  <span>🧤</span><span>GK</span>
                </div>
              )}
              <PlayerAvatar
                player={p}
                numberClasses={isGK ? 'bg-amber-500 text-stone-900' : 'bg-stone-900 text-lime-400'}
              />
              <div className="min-w-0 flex-1">
                <div className="font-bold text-sm truncate">{p.name}</div>
                {secondsByPlayer ? (
                  <div className={`text-[10px] font-bold tracking-wider ${isGK ? 'text-amber-700' : 'text-stone-500'}`}>
                    {Math.round((secondsByPlayer[p.id] || 0) / 60)} min{p.position ? ` · ${p.position}` : ''}
                  </div>
                ) : (p.position && (
                  <div className={`text-[10px] font-bold tracking-wider ${isGK ? 'text-amber-700' : 'text-stone-500'}`}>
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

function EventRow({ event, roster, onDelete }) {
  const isSub = event.type === 'SUB';
  const ev = isSub
    ? { emoji: '🔄', label: 'SUB', requiresPlayer: true }
    : (EVENT_TYPES[event.type] || { emoji: '•', label: event.type, requiresPlayer: false });
  const player = roster.find(p => p.id === event.playerId);
  const subOnPlayer = isSub ? roster.find(p => p.id === event.subOnPlayerId) : null;
  return (
    <div className="bg-white border border-stone-200 rounded-lg px-3 py-2 flex items-center gap-3">
      <div className="text-xl">{ev.emoji}</div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-bold">{ev.label}</div>
        {isSub ? (
          <div className="text-xs text-stone-600 truncate">
            {subOnPlayer ? `${subOnPlayer.name} #${subOnPlayer.number}` : '?'} <span className="text-lime-600 font-bold">IN</span>
            {' · '}
            {player ? `${player.name} #${player.number}` : '?'} <span className="text-stone-500 font-bold">OUT</span>
          </div>
        ) : (
          <>
            {player && <div className="text-xs text-stone-600 truncate">{player.name} · #{player.number}</div>}
            {!player && ev.requiresPlayer && <div className="text-xs text-stone-500 italic">Unknown player</div>}
            {!player && !ev.requiresPlayer && event.type !== 'OPP_GOAL' && <div className="text-xs text-stone-500">No player</div>}
            {event.type === 'OPP_GOAL' && (
              <div className="mt-0.5">
                {event.gkFault === 'gk' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-red-100 text-red-700 border border-red-300">🧤 GK FAULT</span>
                )}
                {event.gkFault === 'unstoppable' && (
                  <span className="inline-block text-[10px] font-extrabold tracking-wider px-1.5 py-0.5 rounded bg-stone-100 text-stone-700 border border-stone-300">😮 UNSTOPPABLE</span>
                )}
                {!event.gkFault && (
                  <span className="text-[10px] text-stone-400 italic">unmarked</span>
                )}
              </div>
            )}
          </>
        )}
      </div>
      <div className="text-xs text-stone-400 tabular-nums shrink-0">
        {formatClock(event.elapsed)} · P{event.period}
      </div>
      {onDelete && (
        <button onClick={() => onDelete(event.id)} className="w-7 h-7 rounded-full bg-red-50 flex items-center justify-center active:scale-95 shrink-0">
          <Trash2 className="w-3.5 h-3.5 text-red-600" />
        </button>
      )}
    </div>
  );
}

/* ---------- PILLAR MINI BAR ---------- */
function PillarMini({ label, value }) {
  const color = value >= 6 ? 'bg-lime-500' : value >= 3 ? 'bg-sky-400' : value >= 0 ? 'bg-stone-300' : 'bg-red-400';
  const width = Math.min(100, Math.max(5, (Math.abs(value) / 15) * 100));
  return (
    <div>
      <div className="text-[9px] font-bold tracking-wider text-stone-500 mb-0.5">{label}</div>
      <div className="h-2 bg-stone-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${width}%` }}></div>
      </div>
      <div className="text-[10px] font-display tabular-nums text-stone-600 mt-0.5">{value}</div>
    </div>
  );
}

/* ---------- GAME DETAIL ---------- */
function GameDetail({ game, roster, onBack, onDelete, onDeleteEvent }) {
  const events = [...game.events].sort((a, b) => a.at - b.at);
  const result = game.ourScore > game.oppScore ? 'WIN' : game.ourScore < game.oppScore ? 'LOSS' : 'DRAW';
  const resultColor = result === 'WIN' ? 'text-lime-400' : result === 'LOSS' ? 'text-red-400' : 'text-white/70';

  const tally = useMemo(() => {
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0 });
    const map = {};
    for (const e of events) {
      if (!e.playerId || e.type === 'SUB') continue;
      map[e.playerId] = map[e.playerId] || init();
      if (map[e.playerId][e.type] !== undefined) map[e.playerId][e.type]++;
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
          <button
            onClick={onDelete}
            className="w-9 h-9 rounded-full bg-white/10 flex items-center justify-center active:scale-95"
          >
            <Trash2 className="w-4 h-4 text-white/70" />
          </button>
        </div>
        <div className="text-center text-xs text-white/70 mb-1">{game.tournament || 'Festival'} · {formatDate(game.date)} · {game.isHome ? 'Home' : 'Away'}</div>
        <div className="text-center font-display text-2xl">vs {game.opponent}</div>
        <div className="text-center font-display text-6xl tabular-nums mt-2">
          {game.ourScore} <span className="text-white/40">–</span> {game.oppScore}
        </div>
      </div>

      {/* Performance Scores */}
      {Object.keys(tally).length > 0 && (
        <div className="px-4 pt-5">
          <h3 className="font-display text-xl mb-2">PERFORMANCE SCORES</h3>
          <div className="bg-white border border-stone-200 rounded-2xl divide-y divide-stone-100">
            {Object.entries(tally)
              .map(([pid, stats]) => {
                const min = Math.round((stats.seconds || 0) / 60);
                const player = roster.find(p => p.id === pid);
                // Treat as GK for scoring if they served any GK time in this game.
                const wasGKThisGame = (game.gkPlayerId === pid) || (game.gkChanges || []).some(c => c.gkPlayerId === pid);
                const pos = wasGKThisGame ? 'GK' : player?.position;
                const gkExtras = wasGKThisGame ? gkExtrasForGame(pid, game) : undefined;
                const score = computePerformanceScore(pid, events, min, pos, gkExtras);
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
                        <div className="text-xs text-stone-500">{parts.join(' · ') || '—'}{min > 0 ? ` · ${min}min` : ''}</div>
                      </div>
                      <div className={`font-display text-2xl tabular-nums ${score.overall >= 8 ? 'text-lime-600' : score.overall >= 4 ? 'text-stone-900' : score.overall >= 0 ? 'text-stone-500' : 'text-red-600'}`}>
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

      <div className="px-4 pt-5">
        <h3 className="font-display text-xl mb-2">TIMELINE</h3>
        {events.length === 0 ? (
          <div className="bg-white border border-stone-200 rounded-2xl p-6 text-center text-sm text-stone-500">
            No events recorded.
          </div>
        ) : (
          <div className="space-y-1.5">
            {events.map(e => <EventRow key={e.id} event={e} roster={roster} onDelete={onDeleteEvent} />)}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- STATS ---------- */
function StatsView({ roster, games, onBack }) {
  const [detailPlayerId, setDetailPlayerId] = useState(null);
  const finished = games.filter(g => g.status === 'finished');

  const stats = useMemo(() => {
    const init = () => ({ GOAL: 0, ASSIST: 0, KEY_PASS: 0, SHOT_ON: 0, SHOT_OFF: 0, SAVE: 0, BLOCK: 0, BALL_WIN: 0, DUEL_WIN: 0, DUEL_LOSE: 0, GIVE_GO: 0, GATES: 0, TURNOVER: 0, HOLDS_BALL: 0, gamesPlayed: 0, totalSeconds: 0 });
    const map = {};
    for (const p of roster) map[p.id] = init();
    for (const g of finished) {
      const seen = new Set();
      for (const e of g.events) {
        if (!e.playerId || !map[e.playerId]) continue;
        if (e.type === 'SUB') continue;
        if (map[e.playerId][e.type] !== undefined) map[e.playerId][e.type]++;
        seen.add(e.playerId);
      }
      for (const p of roster) {
        const sec = playerSeconds(p.id, g);
        if (sec > 0) {
          if (map[p.id]) map[p.id].totalSeconds += sec;
          seen.add(p.id);
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
          if (e.playerId === p.id && e.type !== 'SUB') allEvents.push(e);
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
      map[p.id] = computePerformanceScore(p.id, allEvents, min, seasonPos, wasGKAnyGame ? gkExtras : undefined);
    }
    return map;
  }, [roster, finished, stats]);

  const sorted = [...roster].sort((a, b) => (seasonScores[b.id]?.overall || 0) - (seasonScores[a.id]?.overall || 0));
  const detailPlayer = roster.find(p => p.id === detailPlayerId);

  return (
    <div className="pb-24">
      <Header title="SEASON STATS" onBack={onBack} />

      <div className="px-4 pt-5">
        <div className="text-xs text-stone-500 mb-1">Based on {finished.length} completed game{finished.length === 1 ? '' : 's'}.</div>
        <div className="text-xs text-stone-400 italic mb-3">Sorted by performance score. Tap a player for full breakdown.</div>

        {roster.length === 0 ? (
          <div className="bg-white border border-stone-200 rounded-2xl p-6 text-center text-sm text-stone-500">
            Add players to track stats.
          </div>
        ) : (
          <div className="bg-white border border-stone-200 rounded-2xl overflow-hidden">
            <div className="grid grid-cols-[2.5rem_1fr_2rem_2.5rem_2rem_2rem_3rem] gap-1 px-3 py-2 bg-stone-100 text-[9px] font-bold tracking-wider text-stone-600">
              <div>#</div>
              <div>PLAYER</div>
              <div className="text-center">GP</div>
              <div className="text-center">MIN</div>
              <div className="text-center">G</div>
              <div className="text-center">A</div>
              <div className="text-center">SCORE</div>
            </div>
            <div className="divide-y divide-stone-100">
              {sorted.map(p => {
                const s = stats[p.id] || {};
                const min = Math.round((s.totalSeconds || 0) / 60);
                const sc = seasonScores[p.id] || {};
                return (
                  <button
                    key={p.id}
                    onClick={() => setDetailPlayerId(p.id)}
                    className="w-full grid grid-cols-[2.5rem_1fr_2rem_2.5rem_2rem_2rem_3rem] gap-1 px-3 py-3 items-center text-left active:bg-stone-50 transition"
                  >
                    <PlayerAvatar player={p} sizeClass="w-9 h-9" textSize="text-base" numberClasses="bg-stone-100 text-stone-900" />
                    <div className="min-w-0">
                      <div className="font-bold text-sm truncate">{p.name}</div>
                      {p.position && <div className="text-[10px] text-stone-500 font-bold tracking-wider">{p.position}</div>}
                    </div>
                    <div className="text-center font-display text-sm tabular-nums text-stone-700">{s.gamesPlayed || 0}</div>
                    <div className="text-center font-display text-sm tabular-nums text-sky-700">{min}</div>
                    <div className="text-center font-display text-sm tabular-nums text-lime-700">{s.GOAL || 0}</div>
                    <div className="text-center font-display text-sm tabular-nums text-stone-700">{s.ASSIST || 0}</div>
                    <div className={`text-center font-display text-base tabular-nums ${(sc.overall || 0) >= 6 ? 'text-lime-600' : (sc.overall || 0) >= 3 ? 'text-stone-900' : 'text-stone-500'}`}>{sc.overall || 0}</div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="mt-4 bg-stone-100 rounded-xl p-3 text-xs text-stone-600">
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
  const rows = [
    { label: 'Games played', value: stats.gamesPlayed || 0 },
    { label: 'Minutes played', value: min },
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
        className="bg-white rounded-t-3xl sm:rounded-2xl w-full sm:max-w-md max-h-[85vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-white border-b border-stone-200 px-5 py-4 flex items-center gap-3 z-10">
          <div className="w-12 h-12 rounded-xl bg-stone-900 text-lime-400 flex items-center justify-center font-display text-2xl tabular-nums">
            {player.number || '—'}
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-display text-xl truncate">{player.name}</div>
            {player.position && <div className="text-xs text-stone-500 font-bold tracking-wider">{player.position}</div>}
          </div>
          <button onClick={onClose} className="w-10 h-10 rounded-full bg-stone-100 flex items-center justify-center active:scale-95">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-5">
          <div className="bg-stone-50 rounded-xl p-4 mb-4">
            <div className="flex items-center justify-between mb-3">
              <div className="font-display text-lg">PERFORMANCE SCORE</div>
              <div className={`font-display text-3xl tabular-nums ${(score.overall || 0) >= 6 ? 'text-lime-600' : (score.overall || 0) >= 3 ? 'text-stone-900' : 'text-stone-500'}`}>{score.overall || 0}</div>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <PillarMini label="ATK" value={score.attacking || 0} />
              <PillarMini label="DEF" value={score.defending || 0} />
              <PillarMini label="DEC" value={score.decisions || 0} />
              <PillarMini label="INV" value={score.involvement || 0} />
            </div>
          </div>

          <div className="divide-y divide-stone-100">
            {rows.map(r => (
              <div key={r.label} className="flex items-center justify-between py-3">
                <div className="text-sm text-stone-700">{r.label}</div>
                <div className={`font-display text-2xl tabular-nums ${r.accent || 'text-stone-900'}`}>{r.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- LOCK SCREEN ---------- */
function LockScreen({ onUnlock }) {
  const [code, setCode] = useState('');
  const [error, setError] = useState(false);

  const handleSubmit = () => {
    if (code.trim() === TEAM_CODE) {
      onUnlock();
    } else {
      setError(true);
      setCode('');
      setTimeout(() => setError(false), 1500);
    }
  };

  return (
    <div className="min-h-screen stripes-bg flex flex-col items-center justify-center px-6 text-center">
      <style>{FONT_STYLES}</style>
      <img
        src="./stompers_logo.png"
        alt="LaSalle Stompers"
        className="w-28 h-28 mb-4 drop-shadow-lg"
        onError={(e) => { e.currentTarget.style.display = 'none'; }}
      />
      <div className="font-display text-5xl text-white leading-none">LASALLE</div>
      <div className="font-display text-3xl text-lime-400 leading-tight mb-2">STOMPERS</div>
      <div className="text-white/50 text-xs font-bold tracking-widest mb-8">U10 · 2016 SQUAD</div>

      <div className="w-full max-w-xs">
        <label className="block text-white/70 text-xs font-bold tracking-widest mb-2">ENTER TEAM CODE</label>
        <input
          type="number"
          inputMode="numeric"
          value={code}
          onChange={e => setCode(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          placeholder="••••"
          className={`w-full text-center text-3xl font-display py-4 rounded-xl border-2 outline-none transition ${
            error
              ? 'bg-red-100 border-red-400 text-red-700 animate-[shake_0.3s_ease-in-out]'
              : 'bg-white border-stone-200 text-stone-900'
          }`}
          autoFocus
        />
        {error && <div className="text-red-400 text-sm font-bold mt-2">Wrong code. Try again.</div>}
        <button
          onClick={handleSubmit}
          className="mt-4 w-full bg-lime-500 text-stone-900 font-display text-2xl py-4 rounded-xl shadow-lg shadow-lime-500/30 border-2 border-lime-600 active:scale-[0.98] transition"
        >
          ENTER
        </button>
      </div>
    </div>
  );
}

/* ---------- HEADER ---------- */
function Header({ title, onBack, right }) {
  return (
    <div className="bg-white border-b border-stone-200 px-4 pt-12 pb-3 flex items-center gap-3">
      <button onClick={onBack} className="w-10 h-10 rounded-full bg-stone-100 flex items-center justify-center active:scale-95">
        <ChevronLeft className="w-5 h-5" />
      </button>
      <h1 className="font-display text-2xl flex-1">{title}</h1>
      {right}
    </div>
  );
}
