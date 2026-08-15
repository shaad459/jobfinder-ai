"""JobScout AI - Streamlit UI (ROADMAP_1.md Phase 5).

Same underlying pipeline as test_matcher.py and chat_assistant.py - resume parsing, profile
extraction, company-scoped multi-source search, the three-stage matching cascade, ATS resume
generation/tailoring, PDF export - just driven by widgets instead of a CLI prompt loop or a
free-text chat loop. Nothing in the pipeline itself (database.py, matcher.py, job_aggregator.py,
resume_builder.py, resume_tailor.py, pdf_export.py) was changed to build this; this file only
calls those the same way the other two entry points already do.

Run with: streamlit run streamlit_app.py

Two things are structurally different here versus the CLI entry points, both because a Streamlit
script re-runs top-to-bottom on every widget interaction rather than looping once:

1. All state that needs to survive a rerun (the extracted profile, the last search's matches, any
   generated tailored/report PDFs) lives in st.session_state, not local variables.
2. "Open this job" can't call webbrowser.open() the way chat_assistant.py does - a server can't
   launch a browser on your screen. It's a plain link instead (opens in a new tab), with a
   separate "mark as opened" action, since there's no way to detect a link click server-side.
   This is exactly the deployment consideration chat_assistant.py's own docstring flagged in
   advance.

The print()-based diagnostics inside fetch_company_jobs()/score_all_jobs() (prefilter counts,
prescreen results, freshness/location notes, warnings) go to the terminal running `streamlit run`,
not the browser - since those are genuinely useful (this whole project's debugging leaned on them
heavily), stdout is captured during each search and shown in an expander in the UI too, rather
than being lost or requiring those functions to be rewritten to return diagnostics instead of
printing them.
"""

import contextlib
import html
import io
import re
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from resume_builder import build_ats_resume_pdf
from resume_tailor import tailor_profile_for_job, tailored_resume_filename
from job_aggregator import fetch_company_jobs
from matcher import score_all_jobs
from pdf_export import export_matches_to_pdf
from database import init_db
from connectors.workday_connector import parse_workday_url
from repository import (
    get_or_create_profile, get_profile_by_hash, save_job, save_match,
    get_scored_job_urls, get_matches, get_gemini_call_counts_today, delete_stale_jobs,
    mark_job_opened, get_all_companies, add_company, remove_company,
)

DIMENSIONS = ("role", "location", "skills", "certification", "experience", "domain")
TIER_ORDER = {"Strong": 0, "Good": 1, "Weak": 2}
TIER_BADGE_CLASS = {"Strong": "jsa-badge-strong", "Good": "jsa-badge-good", "Weak": "jsa-badge-weak"}
LEVEL_CHIP_CLASS = {"match": "jsa-level-match", "partial": "jsa-level-partial", "none": "jsa-level-none"}


def _esc(text) -> str:
    """Escapes text pulled from job postings / profile data before dropping it into raw HTML
    (badges, chips, titles below) - this is a local single-user app so there's no real security
    stake, but a stray '<' or '&' in a scraped job title could otherwise break the markup.
    """
    return html.escape(str(text)) if text is not None else ""


# Custom CSS: Streamlit's default look is functional but plain. This injects real card styling,
# a branded dark sidebar, color-coded tier/level badges, and tighter typography, without touching
# any of the underlying widgets or logic below - if a selector ever stops matching a future
# Streamlit version, the affected element just falls back to Streamlit's default look rather than
# breaking anything.
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
/* --- Palette: black / grey / blue, per direct instruction. Every background below is a
   neutral black-grey (no navy/indigo tint), every accent is blue, all text is white/near-white. --- */
.stApp { background: #0a0a0d !important; }

/* Streamlit's own top toolbar (hamburger menu, "Deploy" button) and the file-uploader widget
   are separate native components that DON'T inherit from .stApp's background - they render
   their own surface color. These were the two most likely reasons the page still looked
   "white" after the first pass, since neither was touched by the earlier CSS at all. */
[data-testid="stHeader"] { background: #0a0a0d !important; }
[data-testid="stToolbar"] { background: transparent !important; }
[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploader"] section {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.14) !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #ffffff !important; }
[data-testid="stFileUploaderDropzoneInstructions"] svg { fill: #ffffff !important; }

/* Real dark theme, pinned - not just "whatever Streamlit's picker happens to compute." Streamlit's
   own Light/Dark/System theme picker recomputes a separate text-color variable for native,
   unstyled elements (section headers, widget labels, st.caption text, radio/checkbox labels) -
   if someone clicks "Light" in that picker (or their OS is in light mode and they're on
   "System"), that variable would turn dark, which is invisible against the dark background
   we're forcing above. The .jsa-* classes below already set their own color directly (badges,
   job titles, scores) so they were never affected - this specifically patches the plain,
   un-styled text that has nothing else setting its color.
   :where(...) scopes this to the main content area (not the sidebar, which already sets its
   own light-on-dark text color above) WITHOUT adding any CSS specificity, so it can never
   accidentally out-rank the more specific .jsa-* rules further down for their own elements. */
:where([data-testid="stMain"]) :where([data-testid="stMarkdownContainer"]) *,
:where([data-testid="stMain"]) label,
:where([data-testid="stMain"]) h1,
:where([data-testid="stMain"]) h2,
:where([data-testid="stMain"]) h3,
:where([data-testid="stMain"]) p,
:where([data-testid="stMain"]) span {
    color: #ffffff !important;
}

.jsa-header { display: flex; align-items: center; gap: 12px; margin-bottom: 0; }
.jsa-header .jsa-logo { font-size: 2rem; line-height: 1; }
.jsa-header h1 {
    font-size: 2rem !important; font-weight: 800 !important;
    letter-spacing: -0.03em; margin: 0 !important; color: #ffffff;
}
.jsa-subtitle { color: #a3a3a8; font-size: 0.95rem; margin: 2px 0 6px 0; }

section[data-testid="stSidebar"] { background: linear-gradient(180deg, #19191c 0%, #0a0a0d 100%); }
section[data-testid="stSidebar"] * { color: #ffffff !important; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12); }
.jsa-sidebar-brand { font-size: 1.1rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 2px; }
.jsa-sidebar-tag { color: #a3a3a8 !important; font-size: 0.78rem; margin-bottom: 16px; }
.jsa-model-card {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; padding: 10px 14px; margin-bottom: 10px;
}
.jsa-model-name { font-weight: 600; font-size: 0.85rem; color: #ffffff !important; margin-bottom: 4px; }
.jsa-model-stat { font-size: 0.8rem; color: #a3a3a8 !important; }

h2, h3 { font-weight: 700 !important; letter-spacing: -0.01em; }

div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 14px !important; border: 1px solid rgba(255,255,255,0.12) !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4) !important; background: #1a1a1e !important;
}

/* Native widgets (text inputs, selects, expanders, checkboxes/radios) already follow the dark
   palette set in .streamlit/config.toml's [theme] block - this aligns their container chrome
   (the boxes around inputs, the expander panel, dropdown popovers) with the same grey surface
   used above, and explicitly recolors the checkbox/radio controls, which don't reliably pick
   up theme colors on their own in every browser. */
div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea,
[data-testid="stExpander"] details,
div[data-baseweb="select"] > div, div[data-baseweb="popover"] {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.12) !important; color: #ffffff !important;
}
ul[role="listbox"] { background: #1a1a1e !important; }
ul[role="listbox"] li { color: #ffffff !important; }
[data-testid="stCheckbox"] svg, [data-testid="stRadio"] svg { fill: #3b82f6 !important; }

.stButton button, .stDownloadButton button { border-radius: 8px !important; font-weight: 600 !important; }
button[kind="primary"] { background: #2563eb !important; border-color: #2563eb !important; color: #ffffff !important; }
button[kind="primary"]:hover { background: #1d4ed8 !important; border-color: #1d4ed8 !important; }
button[kind="secondary"] {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.16) !important; color: #ffffff !important;
}

.jsa-badge {
    display: inline-block; padding: 3px 11px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.03em;
    text-transform: uppercase; vertical-align: middle;
}
.jsa-badge-strong { background: #0f2e1e; color: #4ade80; }
.jsa-badge-good   { background: #332705; color: #fbbf24; }
.jsa-badge-weak   { background: #262626; color: #a3a3a8; }
.jsa-score { font-weight: 700; color: #ffffff; margin-left: 6px; font-size: 0.95rem; }
.jsa-coverage {
    font-weight: 600; color: #60a5fa; margin-left: 10px; font-size: 0.8rem;
    background: #0f2942; padding: 2px 8px; border-radius: 999px;
}
.jsa-job-title { font-size: 1.05rem; font-weight: 700; color: #ffffff; margin: 8px 0 2px 0; }

.jsa-level-chip {
    display: inline-block; padding: 1px 8px; border-radius: 6px;
    font-size: 0.72rem; font-weight: 700; text-transform: capitalize; margin-right: 6px;
}
.jsa-level-match   { background: #0f2e1e; color: #4ade80; }
.jsa-level-partial { background: #332705; color: #fbbf24; }
.jsa-level-none    { background: #3d1418; color: #f87171; }
</style>
"""

# gemini_utils.call_gemini() prints this exact line ("Rate limited by Gemini - waiting {N}s
# before retry {a}/{b}...") on every retry - _run_search_captured() below catches it in stdout
# along with everything else. This regex pulls the wait time back out of that captured text so
# the UI can show a real, run-specific rate-limit summary instead of just a raw log dump.
_RATE_LIMIT_WAIT_PATTERN = re.compile(r"waiting (\d+)s before retry")

# A small self-contained canvas game (no external assets, no network calls) shown in the sidebar
# while a search is running - fetch_company_jobs()/score_all_jobs() run synchronously and can
# take a while (especially the "all companies" sweep with Gemini rate-limit backoffs), and
# Streamlit blocks all UI updates until that finishes. This iframe is plain client-side
# JavaScript though, so once the browser has loaded it, it keeps running/responding to keypresses
# on its own regardless of what the Python side is doing.
#
# An original 2D bowling game - a top-down lane rendered with a simple vanishing-point
# perspective (the lane narrows toward the back), shaded pins and ball, and the classic
# two-press aim-then-power control scheme. All original code/art (canvas shapes and gradients
# only, no external assets), so there's no licensing question the way there would be with an
# actual Mario/Sonic/etc. title.
MINIGAME_HTML = """
<div style="font-family:sans-serif;text-align:center;">
  <canvas id="jsaGame" width="260" height="380" tabindex="0"
    style="background:#0b0d1a;border-radius:10px;outline:none;cursor:pointer;"></canvas>
  <div style="color:#9297b8;font-size:11px;margin-top:6px;">
    Click the lane, then Space to lock aim &middot; Space again to lock power
  </div>
</div>
<script>
(function() {
  const canvas = document.getElementById('jsaGame');
  const ctx = canvas.getContext('2d');
  canvas.addEventListener('click', () => canvas.focus());
  canvas.focus();

  // --- Lane geometry: a simple forced-perspective trapezoid. Pin/ball positions are tracked
  // in "world" units (using the lane's width at the player's end as the reference scale) and
  // converted to screen pixels via scaleAt(y), which shrinks toward the back of the lane. ---
  const topY = 26, bottomY = 344;
  const topHalfW = 38, bottomHalfW = 92;
  const centerX = canvas.width / 2;

  function scaleAt(y) {
    const t = Math.max(0, Math.min(1, (y - topY) / (bottomY - topY)));
    return (topHalfW + t * (bottomHalfW - topHalfW)) / bottomHalfW;
  }
  function screenX(y, worldX) { return centerX + worldX * scaleAt(y); }

  const PIN_ROWS = [
    { y: 62,  xs: [-27, -9, 9, 27] },
    { y: 96,  xs: [-18, 0, 18] },
    { y: 130, xs: [-9, 9] },
    { y: 164, xs: [0] },
  ];

  let pins, ball, state, frame, aimAngle, power, totalScore, rolls, message, messageTimer, pinsThisRoll;

  function spawnPins() {
    pins = [];
    PIN_ROWS.forEach(row => row.xs.forEach(wx => pins.push({ wx, y: row.y, standing: true })));
  }
  function resetRoll() {
    ball = { wx: 0, y: bottomY - 20, vy: 0, vx: 0 };
    pinsThisRoll = 0;
    state = 'aim';
  }
  spawnPins();
  resetRoll();
  totalScore = 0;
  rolls = 0;
  frame = 0;
  message = '';
  messageTimer = 0;
  const MAX_AIM = 0.55; // radians, ~31 degrees either side

  function handlePress() {
    if (state === 'aim') {
      state = 'power';
    } else if (state === 'power') {
      const speed = 3.2 + (power / 100) * 4.3;
      ball.vy = -speed;
      ball.vx = Math.sin(aimAngle) * (2.0 + (power / 100) * 1.4);
      state = 'roll';
    } else if (state === 'result') {
      spawnPins();
      resetRoll();
    }
  }

  canvas.addEventListener('keydown', (e) => {
    if (e.code === 'Space' || e.code === 'ArrowUp') { e.preventDefault(); handlePress(); }
  });
  canvas.addEventListener('mousedown', () => { canvas.focus(); handlePress(); });

  function endRoll(reason) {
    totalScore += pinsThisRoll;
    rolls++;
    if (pinsThisRoll >= 10) message = 'STRIKE!  +10';
    else if (reason === 'gutter') message = 'Gutter ball';
    else message = '+' + pinsThisRoll + ' pin' + (pinsThisRoll === 1 ? '' : 's');
    messageTimer = 70;
    state = 'result';
  }

  function update() {
    frame++;
    if (state === 'aim') {
      aimAngle = Math.sin(frame * 0.045) * MAX_AIM;
    } else if (state === 'power') {
      power = Math.abs(Math.sin(frame * 0.032)) * 100;
    } else if (state === 'roll') {
      ball.y += ball.vy;
      ball.wx += ball.vx;

      pins.forEach(p => {
        if (p.standing && Math.abs(ball.y - p.y) < 10 && Math.abs(ball.wx - p.wx) < 11) {
          p.standing = false;
          pinsThisRoll++;
        }
      });

      if (Math.abs(ball.wx) > bottomHalfW - 4) {
        endRoll('gutter');
      } else if (ball.y <= topY || pins.every(p => !p.standing)) {
        endRoll('reached_end');
      }
    } else if (state === 'result') {
      messageTimer--;
      if (messageTimer <= 0) { spawnPins(); resetRoll(); }
    }
  }

  function drawLane() {
    ctx.fillStyle = '#0b0d1a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Gutters (a wider, darker trapezoid drawn first, lane trapezoid on top of it)
    ctx.fillStyle = '#1a1440';
    ctx.beginPath();
    ctx.moveTo(screenX(topY, -topHalfW - 10), topY);
    ctx.lineTo(screenX(topY, topHalfW + 10), topY);
    ctx.lineTo(screenX(bottomY, bottomHalfW + 14), bottomY);
    ctx.lineTo(screenX(bottomY, -bottomHalfW - 14), bottomY);
    ctx.closePath();
    ctx.fill();

    const laneGrad = ctx.createLinearGradient(0, topY, 0, bottomY);
    laneGrad.addColorStop(0, '#caa06a');
    laneGrad.addColorStop(1, '#e8c48c');
    ctx.fillStyle = laneGrad;
    ctx.beginPath();
    ctx.moveTo(screenX(topY, -topHalfW), topY);
    ctx.lineTo(screenX(topY, topHalfW), topY);
    ctx.lineTo(screenX(bottomY, bottomHalfW), bottomY);
    ctx.lineTo(screenX(bottomY, -bottomHalfW), bottomY);
    ctx.closePath();
    ctx.fill();

    // Faint board lines for texture + a sense of depth
    ctx.strokeStyle = 'rgba(120, 85, 40, 0.25)';
    ctx.lineWidth = 1;
    [-60, -30, 0, 30, 60].forEach(wx => {
      ctx.beginPath();
      ctx.moveTo(screenX(topY, wx), topY);
      ctx.lineTo(screenX(bottomY, wx), bottomY);
      ctx.stroke();
    });

    // Foul line
    ctx.strokeStyle = 'rgba(180, 30, 30, 0.8)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(screenX(bottomY - 14, -bottomHalfW), bottomY - 14);
    ctx.lineTo(screenX(bottomY - 14, bottomHalfW), bottomY - 14);
    ctx.stroke();
  }

  function drawPin(p) {
    const s = scaleAt(p.y);
    const x = screenX(p.y, p.wx);
    const h = 14 * s, w = 6 * s;
    ctx.fillStyle = 'rgba(0,0,0,0.25)';
    ctx.beginPath();
    ctx.ellipse(x, p.y + h * 0.55, w * 0.9, w * 0.4, 0, 0, Math.PI * 2);
    ctx.fill();

    const grad = ctx.createLinearGradient(x - w, p.y - h, x + w, p.y + h);
    grad.addColorStop(0, '#f5f5f7');
    grad.addColorStop(0.5, '#ffffff');
    grad.addColorStop(1, '#d8d8de');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.ellipse(x, p.y, w, h, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#c23b3b';
    ctx.fillRect(x - w, p.y - h * 0.25, w * 2, h * 0.3);
  }

  function drawBall() {
    const s = scaleAt(ball.y);
    const x = screenX(ball.y, ball.wx);
    const r = 9 * s;
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.beginPath();
    ctx.ellipse(x, ball.y + r * 0.7, r * 0.9, r * 0.35, 0, 0, Math.PI * 2);
    ctx.fill();

    const grad = ctx.createRadialGradient(x - r * 0.35, ball.y - r * 0.35, r * 0.15, x, ball.y, r);
    grad.addColorStop(0, '#7b6bd8');
    grad.addColorStop(0.6, '#4b3aa8');
    grad.addColorStop(1, '#2b1f70');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(x, ball.y, r, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = 'rgba(20,15,50,0.8)';
    [[-0.2, -0.3], [0.2, -0.3], [0, 0.05]].forEach(([dx, dy]) => {
      ctx.beginPath();
      ctx.ellipse(x + dx * r, ball.y + dy * r, r * 0.14, r * 0.14, 0, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawAimAndPower() {
    if (state === 'aim') {
      const len = 46;
      const x1 = screenX(ball.y, 0), y1 = ball.y - 6;
      const x2 = x1 + Math.sin(aimAngle) * len, y2 = y1 - Math.cos(aimAngle) * len;
      ctx.strokeStyle = '#4ade80';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    if (state === 'power') {
      const barX = 30, barY = bottomY + 20, barW = canvas.width - 60, barH = 10;
      ctx.strokeStyle = '#e7e8f5';
      ctx.strokeRect(barX, barY, barW, barH);
      ctx.fillStyle = power > 80 ? '#f87171' : power > 40 ? '#fbbf24' : '#4ade80';
      ctx.fillRect(barX, barY, barW * (power / 100), barH);
    }
  }

  function draw() {
    drawLane();
    pins.forEach(p => { if (p.standing) drawPin(p); });
    if (state === 'roll' || state === 'result') drawBall();
    drawAimAndPower();

    ctx.fillStyle = '#e7e8f5';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('Score ' + totalScore, 8, 16);
    ctx.fillText('Rolls ' + rolls, 8, 30);

    if (state === 'aim') {
      ctx.fillStyle = '#9297b8';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Space to lock aim', canvas.width / 2, canvas.height - 4);
    } else if (state === 'power') {
      ctx.fillStyle = '#9297b8';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Space to roll!', canvas.width / 2, canvas.height - 4);
    } else if (state === 'result' && message) {
      ctx.fillStyle = 'rgba(0,0,0,0.35)';
      ctx.fillRect(0, canvas.height / 2 - 26, canvas.width, 40);
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 16px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(message, canvas.width / 2, canvas.height / 2);
    }
    ctx.textAlign = 'left';
  }

  function loop() {
    update();
    draw();
    requestAnimationFrame(loop);
  }
  loop();
})();
</script>
"""

# An original vertical-scrolling racing game, in the same spirit as the arcade-era "your red car
# dodges oncoming traffic on a scrolling road" genre (Konami's Road Fighter being the best-known
# example) - built from scratch here rather than using that actual game, since Road Fighter is
# Konami's copyrighted IP and isn't open source. All shapes/colors are drawn with canvas
# primitives only, no borrowed assets or code.
ROAD_RACER_HTML = """
<div style="font-family:sans-serif;text-align:center;">
  <canvas id="jsaRacer" width="260" height="380" tabindex="0"
    style="background:#0b0d1a;border-radius:10px;outline:none;cursor:pointer;"></canvas>
  <div style="color:#9297b8;font-size:11px;margin-top:6px;">
    Click the road, then &larr;/&rarr; to steer &middot; Space to restart after a crash
  </div>
</div>
<script>
(function() {
  const canvas = document.getElementById('jsaRacer');
  const ctx = canvas.getContext('2d');
  canvas.addEventListener('click', () => canvas.focus());
  canvas.focus();

  const roadLeft = 46, roadRight = 214;
  const carW = 22, carH = 34;
  const playerY = 320;

  let player, traffic, keys, dashOffset, grassOffset, speed, score, best, state, frame, spawnTimer;

  function reset() {
    player = { x: (roadLeft + roadRight) / 2 - carW / 2, vx: 0 };
    traffic = [];
    dashOffset = 0;
    grassOffset = 0;
    speed = 3.2;
    score = 0;
    frame = 0;
    spawnTimer = 60;
    state = 'playing';
  }
  keys = {};
  reset();
  best = 0;

  canvas.addEventListener('keydown', (e) => {
    if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') { e.preventDefault(); keys[e.code] = true; }
    if (e.code === 'Space' && state === 'crashed') { e.preventDefault(); reset(); }
  });
  canvas.addEventListener('keyup', (e) => {
    if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') { keys[e.code] = false; }
  });

  function spawnTrafficCar() {
    const w = carW, laneX = roadLeft + 4 + Math.random() * (roadRight - roadLeft - w - 8);
    const palette = ['#3b82f6', '#eab308', '#22c55e', '#a855f7'];
    traffic.push({
      x: laneX, y: -carH, w, h: carH,
      color: palette[Math.floor(Math.random() * palette.length)],
    });
  }

  function rectsOverlap(a, b) {
    return a.x < b.x + b.w && a.x + carW > b.x && playerY < b.y + b.h && playerY + carH > b.y;
  }

  function update() {
    if (state !== 'playing') return;
    frame++;

    if (keys['ArrowLeft']) player.vx -= 0.9;
    if (keys['ArrowRight']) player.vx += 0.9;
    if (!keys['ArrowLeft'] && !keys['ArrowRight']) player.vx *= 0.8;
    player.vx = Math.max(-5, Math.min(5, player.vx));
    player.x += player.vx;
    player.x = Math.max(roadLeft + 2, Math.min(roadRight - carW - 2, player.x));

    speed = 3.2 + Math.min(4.5, score / 400);
    dashOffset = (dashOffset + speed) % 40;
    grassOffset = (grassOffset + speed) % 30;

    spawnTimer--;
    if (spawnTimer <= 0) {
      spawnTrafficCar();
      spawnTimer = Math.max(22, 55 - Math.floor(score / 60));
    }

    traffic.forEach(c => c.y += speed);
    traffic = traffic.filter(c => c.y < canvas.height + 40);

    for (const c of traffic) {
      if (rectsOverlap({ x: player.x, y: playerY }, c)) {
        state = 'crashed';
        best = Math.max(best, Math.floor(score));
        break;
      }
    }

    score += speed * 0.12;
  }

  function drawCar(x, y, w, h, color, windshieldUp) {
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.fillRect(x - 1, y + h - 4, w + 2, 5);
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    const wy = windshieldUp ? y + 4 : y + h - 12;
    ctx.fillRect(x + 3, wy, w - 6, 8);
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillRect(x + 1, y + 2, 3, 6);
    ctx.fillRect(x + w - 4, y + 2, 3, 6);
    ctx.fillRect(x + 1, y + h - 8, 3, 6);
    ctx.fillRect(x + w - 4, y + h - 8, 3, 6);
  }

  function draw() {
    // Grass shoulders, with a simple scrolling stripe pattern for motion
    const grassGrad = ctx.createLinearGradient(0, 0, 0, canvas.height);
    grassGrad.addColorStop(0, '#1f5c2e');
    grassGrad.addColorStop(1, '#173f20');
    ctx.fillStyle = grassGrad;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    for (let y = -30 + grassOffset; y < canvas.height; y += 30) {
      ctx.fillRect(0, y, roadLeft, 14);
      ctx.fillRect(roadRight, y, canvas.width - roadRight, 14);
    }

    // Road surface + shoulder edge
    ctx.fillStyle = '#3a3a45';
    ctx.fillRect(roadLeft, 0, roadRight - roadLeft, canvas.height);
    ctx.fillStyle = '#e7e8f5';
    ctx.fillRect(roadLeft - 3, 0, 3, canvas.height);
    ctx.fillRect(roadRight, 0, 3, canvas.height);

    // Scrolling dashed lane dividers
    ctx.strokeStyle = 'rgba(231,232,245,0.7)';
    ctx.lineWidth = 3;
    ctx.setLineDash([16, 14]);
    ctx.lineDashOffset = -dashOffset;
    [roadLeft + (roadRight - roadLeft) / 3, roadLeft + (roadRight - roadLeft) * 2 / 3].forEach(x => {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    });
    ctx.setLineDash([]);

    traffic.forEach(c => drawCar(c.x, c.y, c.w, c.h, c.color, false));
    drawCar(player.x, playerY, carW, carH, '#e13a3a', true);

    ctx.fillStyle = '#e7e8f5';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('Score ' + Math.floor(score), 8, 16);
    ctx.fillText('Best ' + best, 8, 30);

    if (state === 'crashed') {
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(0, canvas.height / 2 - 30, canvas.width, 48);
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 15px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Crashed! Space to retry', canvas.width / 2, canvas.height / 2 - 4);
      ctx.font = '11px sans-serif';
      ctx.fillStyle = '#e7e8f5';
      ctx.fillText('Score: ' + Math.floor(score), canvas.width / 2, canvas.height / 2 + 16);
    }
    ctx.textAlign = 'left';
  }

  function loop() {
    update();
    draw();
    requestAnimationFrame(loop);
  }
  loop();
})();
</script>
"""

# Modeled after the mechanics of Chromium's actual open-source T-Rex runner (jump over cacti,
# duck under birds) rather than adapting Chromium's own game.js verbatim - that file lives inside
# a much larger module system that isn't a clean drop-in for a standalone iframe, so a fresh,
# compact reimplementation of the same mechanics is simpler here.
DINO_HTML = """
<div style="font-family:sans-serif;text-align:center;">
  <canvas id="jsaDino" width="260" height="200" tabindex="0"
    style="background:#0f1225;border-radius:10px;outline:none;cursor:pointer;"></canvas>
  <div style="color:#9297b8;font-size:11px;margin-top:6px;">
    Click, then Space/&uarr; to jump &middot; hold &darr; to duck
  </div>
</div>
<script>
(function() {
  const canvas = document.getElementById('jsaDino');
  const ctx = canvas.getContext('2d');
  canvas.addEventListener('click', () => canvas.focus());
  canvas.focus();

  const ground = 150;
  let dino, obstacles, frame, score, best, gameOver, speed, ducking;

  function reset() {
    dino = { x: 24, y: ground - 26, vy: 0, w: 20, h: 26, jumping: false };
    obstacles = [];
    frame = 0;
    score = 0;
    speed = 4;
    gameOver = false;
    ducking = false;
  }
  reset();
  best = 0;

  function jump() {
    if (gameOver) { reset(); return; }
    if (!dino.jumping && !ducking) { dino.vy = -12.5; dino.jumping = true; }
  }

  canvas.addEventListener('keydown', (e) => {
    if (e.code === 'Space' || e.code === 'ArrowUp') { e.preventDefault(); jump(); }
    if (e.code === 'ArrowDown') { e.preventDefault(); if (!dino.jumping) ducking = true; }
  });
  canvas.addEventListener('keyup', (e) => {
    if (e.code === 'ArrowDown') ducking = false;
  });

  function spawn() {
    const isBird = Math.random() < 0.3 && frame > 200;
    if (isBird) {
      obstacles.push({ type: 'bird', x: canvas.width, w: 22, h: 14, y: ground - 46 });
    } else {
      const h = 20 + Math.random() * 16;
      obstacles.push({ type: 'cactus', x: canvas.width, w: 12, h, y: ground - h });
    }
  }

  function loop() {
    frame++;
    ctx.fillStyle = '#0f1225';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#3730a3';
    ctx.beginPath();
    ctx.moveTo(0, ground + 1);
    ctx.lineTo(canvas.width, ground + 1);
    ctx.stroke();

    const dinoH = ducking ? 16 : dino.h;

    if (!gameOver) {
      dino.vy += 0.8;
      dino.y += dino.vy;
      const restY = ducking ? ground - 16 : ground - dino.h;
      if (dino.y >= restY) { dino.y = restY; dino.vy = 0; dino.jumping = false; }

      if (frame % Math.max(45, 80 - Math.floor(speed * 3)) === 0) spawn();
      obstacles.forEach(o => o.x -= speed);
      obstacles = obstacles.filter(o => o.x + o.w > 0);
      if (frame % 300 === 0) speed += 0.4;
      score = Math.floor(frame / 5);
      best = Math.max(best, score);

      obstacles.forEach(o => {
        if (dino.x < o.x + o.w && dino.x + dino.w > o.x &&
            dino.y < o.y + o.h && dino.y + dinoH > o.y) {
          gameOver = true;
        }
      });
    }

    ctx.fillStyle = '#818cf8';
    ctx.fillRect(dino.x, dino.y, dino.w, dinoH);
    ctx.fillRect(dino.x + dino.w - 8, dino.y - 6, 10, 10);
    if (!ducking) {
      const legOffset = Math.floor(frame / 6) % 2 === 0 ? 0 : 4;
      ctx.fillRect(dino.x + 2, dino.y + dinoH, 4, 6 - legOffset);
      ctx.fillRect(dino.x + dino.w - 8, dino.y + dinoH, 4, legOffset + 2);
    }

    obstacles.forEach(o => {
      if (o.type === 'cactus') {
        ctx.fillStyle = '#4ade80';
        ctx.fillRect(o.x, o.y, o.w, o.h);
        ctx.fillRect(o.x - 4, o.y + 6, 4, 8);
        ctx.fillRect(o.x + o.w, o.y + 10, 4, 8);
      } else {
        ctx.fillStyle = '#f472b6';
        const flapUp = Math.floor(frame / 8) % 2 === 0;
        ctx.fillRect(o.x, o.y + (flapUp ? 0 : 4), o.w, 6);
      }
    });

    ctx.fillStyle = '#e7e8f5';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('Score ' + score, 8, 16);
    ctx.fillText('Best ' + best, 8, 30);

    if (gameOver) {
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 13px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Game over - Space to retry', canvas.width / 2, canvas.height / 2);
      ctx.textAlign = 'left';
    }
    requestAnimationFrame(loop);
  }
  loop();
})();
</script>
"""

# An original reimplementation of 2048's mechanics (Gabriele Cirulli's original is MIT-licensed,
# but its actual code is built around external CSS/DOM tiles and touch handling rather than a
# single embeddable canvas) - same slide-and-merge rules, standalone canvas rendering so it drops
# into the sidebar iframe the same way the other games do.
G2048_HTML = """
<div style="font-family:sans-serif;text-align:center;">
  <canvas id="jsa2048" width="260" height="300" tabindex="0"
    style="background:#0b0d1a;border-radius:10px;outline:none;cursor:pointer;"></canvas>
  <div style="color:#9297b8;font-size:11px;margin-top:6px;">
    Click, then arrow keys to slide &amp; merge tiles
  </div>
</div>
<script>
(function() {
  const canvas = document.getElementById('jsa2048');
  const ctx = canvas.getContext('2d');
  canvas.addEventListener('click', () => canvas.focus());
  canvas.focus();

  const gridLeft = 20, gridTop = 55, gridSize = 220, gap = 8;
  const cell = (gridSize - gap * 5) / 4;
  const TILE_COLORS = {
    0: '#3a3750', 2: '#eee4da', 4: '#ede0c8', 8: '#f2b179', 16: '#f59563',
    32: '#f67c5f', 64: '#f65e3b', 128: '#edcf72', 256: '#edcc61', 512: '#edc850',
    1024: '#edc53f', 2048: '#edc22e',
  };
  function tileColor(v) { return TILE_COLORS[v] || '#3c3a32'; }
  function textColor(v) { return v > 0 && v <= 4 ? '#3c352c' : '#f9f6f2'; }

  let grid, score, best, over;

  function emptyCells() {
    const cells = [];
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) if (grid[r][c] === 0) cells.push([r, c]);
    return cells;
  }
  function spawnTile() {
    const cells = emptyCells();
    if (!cells.length) return;
    const [r, c] = cells[Math.floor(Math.random() * cells.length)];
    grid[r][c] = Math.random() < 0.9 ? 2 : 4;
  }
  function reset() {
    grid = [[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]];
    score = 0;
    over = false;
    spawnTile();
    spawnTile();
  }
  reset();
  best = 0;

  function processLine(line) {
    let nums = line.filter(v => v !== 0);
    let gained = 0;
    for (let i = 0; i < nums.length - 1; i++) {
      if (nums[i] === nums[i + 1]) {
        nums[i] *= 2;
        gained += nums[i];
        nums.splice(i + 1, 1);
      }
    }
    while (nums.length < 4) nums.push(0);
    return { line: nums, gained };
  }

  function hasMoves() {
    if (emptyCells().length) return true;
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        const v = grid[r][c];
        if (c < 3 && grid[r][c + 1] === v) return true;
        if (r < 3 && grid[r + 1][c] === v) return true;
      }
    }
    return false;
  }

  function move(direction) {
    const vertical = direction === 'up' || direction === 'down';
    const reversed = direction === 'right' || direction === 'down';
    let moved = false, gained = 0;
    const newGrid = grid.map(row => row.slice());

    for (let i = 0; i < 4; i++) {
      const original = vertical ? [0, 1, 2, 3].map(r => grid[r][i]) : grid[i].slice();
      let line = original.slice();
      if (reversed) line.reverse();
      const result = processLine(line);
      let finalLine = result.line.slice();
      if (reversed) finalLine.reverse();
      gained += result.gained;
      if (finalLine.some((v, idx) => v !== original[idx])) moved = true;
      if (vertical) { for (let r = 0; r < 4; r++) newGrid[r][i] = finalLine[r]; }
      else { newGrid[i] = finalLine; }
    }

    if (moved) {
      grid = newGrid;
      score += gained;
      best = Math.max(best, score);
      spawnTile();
      if (!hasMoves()) over = true;
    }
  }

  canvas.addEventListener('keydown', (e) => {
    const map = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
    if (!map[e.code]) return;
    e.preventDefault();
    if (over) { reset(); return; }
    move(map[e.code]);
  });

  function fillRoundRect(x, y, w, h, r) {
    if (ctx.roundRect) {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      ctx.fill();
    } else {
      ctx.fillRect(x, y, w, h);
    }
  }

  function drawTile(r, c, v) {
    const x = gridLeft + c * (cell + gap), y = gridTop + r * (cell + gap);
    ctx.fillStyle = tileColor(v);
    fillRoundRect(x, y, cell, cell, 6);
    if (v) {
      ctx.fillStyle = textColor(v);
      ctx.font = 'bold ' + (v < 100 ? 20 : v < 1000 ? 17 : 14) + 'px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(v), x + cell / 2, y + cell / 2 + 1);
      ctx.textBaseline = 'alphabetic';
    }
  }

  function draw() {
    ctx.fillStyle = '#0b0d1a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.fillStyle = '#e7e8f5';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('Score ' + score, 8, 16);
    ctx.fillText('Best ' + best, 8, 30);

    ctx.fillStyle = '#2a2740';
    fillRoundRect(gridLeft - gap, gridTop - gap, gridSize, gridSize, 8);

    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) drawTile(r, c, grid[r][c]);

    if (over) {
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 14px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No more moves', canvas.width / 2, canvas.height / 2 - 8);
      ctx.font = '11px sans-serif';
      ctx.fillText('press any arrow to restart', canvas.width / 2, canvas.height / 2 + 12);
    }
    ctx.textAlign = 'left';
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>
"""

MINIGAMES = {
    "Bowling": {"html": MINIGAME_HTML, "height": 430},
    "Road Racer": {"html": ROAD_RACER_HTML, "height": 430},
    "2048": {"html": G2048_HTML, "height": 350},
    "Chrome Dino Run": {"html": DINO_HTML, "height": 250},
}


def _render_game_panel():
    """Renders the game picker in the sidebar, plus the iframe itself when a game is open.
    Lives in the sidebar (not the search form) and, once unlocked (see game_panel_unlocked
    below), stays visible for the rest of the session - including after "Close game" is
    clicked. Closing only hides the running iframe (game_open), not the picker dropdown
    itself; previously both were gated by the same flag, so closing the game made the whole
    panel - dropdown included - disappear with no way to reopen a game afterward. Called from
    two places: inline, right before a search's blocking call (so it's visible during THAT
    run's wait), and from the persistent top-of-script sidebar block on every subsequent rerun
    (so it doesn't vanish the next time something else on the page triggers a rerun, e.g. the
    PDF export button).
    """
    st.markdown("**While you wait:**")
    game_choice = st.selectbox("Pick a game", list(MINIGAMES.keys()), key="game_choice",
                                label_visibility="collapsed")
    if st.session_state.game_open:
        game = MINIGAMES[game_choice]
        components.html(game["html"], height=game["height"], scrolling=False)
        if st.button("Close game", key="close_game_btn"):
            st.session_state.game_open = False
            st.rerun()
    else:
        if st.button("Open game", key="open_game_btn"):
            st.session_state.game_open = True
            st.rerun()


# --- Search helpers (same calls chat_assistant.py's _do_search makes, minus the input()
# fallback and print()s - a Streamlit widget always supplies company/location/etc. directly, and
# stdout is captured by the caller instead of printed inline). ---------------------------------

def _run_search(profile, profile_id, company, title_override, location, relocation_ok,
                 include_aggregators=False):
    query = title_override or (profile.get("job_titles") or ["product owner"])[0]
    jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok,
                               include_aggregators_for_workday=include_aggregators)

    already_scored = get_scored_job_urls(profile_id)
    new_jobs = [j for j in jobs if j["url"] not in already_scored]

    def save_batch(batch_scored):
        for job in batch_scored:
            save_job(job)
            save_match(profile_id, job)

    if new_jobs:
        score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch,
                        title_override=title_override)

    job_urls_this_search = {j["url"] for j in jobs}
    all_matches = get_matches(profile_id)
    return [m for m in all_matches if m["url"] in job_urls_this_search]


def _run_search_all_companies(profile, profile_id, title_override, location, relocation_ok):
    all_matches = []
    for company in get_all_companies():
        all_matches.extend(_run_search(profile, profile_id, company, title_override, location,
                                        relocation_ok, include_aggregators=True))
    return all_matches


def _run_search_captured(*args, **kwargs):
    """Runs a search while capturing everything it prints, so the same diagnostics the CLI
    entry points show inline (prefilter/prescreen counts, freshness/location notes, warnings)
    are visible in the browser too, not just in the terminal running the Streamlit server.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        all_companies = kwargs.pop("all_companies", False)
        if all_companies:
            result = _run_search_all_companies(*args, **kwargs)
        else:
            result = _run_search(*args, **kwargs)
    return result, buf.getvalue()


# --- Job result card ------------------------------------------------------------------------
#
# @st.fragment makes this its own independent rerun unit: clicking "Mark as opened" or "Tailor
# resume" on ONE card only reruns that card (and shows Streamlit's brief running/dim indicator
# on just that card), instead of the whole page - which is what was happening before and made
# every other card in the list look greyed out/disabled while a single job was being tailored.
# st.rerun(scope="fragment") (rather than a plain st.rerun()) is what keeps the rerun scoped to
# just this fragment instead of escalating back up to a full-app rerun.

def _ats_coverage_pct(job: dict) -> int | None:
    """BUILD_PLAN.md item 7b's "ATS keyword-coverage score" - deliberately free: match_points
    and match_gaps are already computed during precise scoring (matcher.py stage 2), so this is
    just a ratio over data that already exists, not a new Gemini call. Returns None for a job
    that never reached precise scoring (a prescreen reject or prefilter placeholder has no
    points/gaps to compute a ratio from) rather than a misleading 0%.
    """
    points = job.get("match_points") or []
    gaps = job.get("match_gaps") or []
    total = len(points) + len(gaps)
    if total == 0:
        return None
    return round(100 * len(points) / total)


@st.fragment
def _render_job_card(job, profile, profile_id, resume_filename):
    tier = job.get("match_tier") or "Weak"
    badge_class = TIER_BADGE_CLASS.get(tier, "jsa-badge-weak")
    url = job.get("url")
    coverage = _ats_coverage_pct(job)

    with st.container(border=True):
        st.markdown(
            f'<span class="jsa-badge {badge_class}">{_esc(tier)}</span>'
            f'<span class="jsa-score">{_esc(job.get("match_score"))}</span>'
            + (f'<span class="jsa-coverage">ATS coverage: {coverage}%</span>'
               if coverage is not None else ''),
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="jsa-job-title">{_esc(job.get("title"))}</div>',
                    unsafe_allow_html=True)
        posted = job.get("posted_date") or "date unknown"
        opened_note = " · already opened" if job.get("opened_at") else ""
        st.caption(f"{job.get('company')} · {job.get('location') or 'location not listed'} "
                   f"· {job.get('source')} · posted {posted}{opened_note}")

        breakdown = job.get("dimension_breakdown") or {}
        if breakdown:
            with st.expander("Fit breakdown"):
                for dimension in DIMENSIONS:
                    dim = breakdown.get(dimension) or {}
                    level = dim.get("level")
                    if not level:
                        continue
                    note = dim.get("note")
                    chip_class = LEVEL_CHIP_CLASS.get(level, "jsa-level-partial")
                    st.markdown(
                        f'<span class="jsa-level-chip {chip_class}">{_esc(level)}</span>'
                        f'<strong>{_esc(dimension.capitalize())}</strong>'
                        + (f" — {_esc(note)}" if note else ""),
                        unsafe_allow_html=True,
                    )

        action_cols = st.columns(3)
        with action_cols[0]:
            if url:
                # A plain link, not webbrowser.open() - this is a server-rendered page, it can't
                # launch a browser on your screen the way the CLI assistant does. Opens in a new
                # tab so the results list stays put.
                st.markdown(f"[View posting ↗]({url})")
        with action_cols[1]:
            if url and st.button("Mark as opened", key=f"open_{url}"):
                mark_job_opened(profile_id, url)
                st.rerun(scope="fragment")
        with action_cols[2]:
            # Available for Strong AND Good matches now - a Good-tier match with a high score
            # (e.g. 89) is still a real, worthwhile role; it was previously gated to Strong only.
            if tier in ("Strong", "Good") and url:
                if url in st.session_state.tailored_paths:
                    with open(st.session_state.tailored_paths[url], "rb") as f:
                        st.download_button(
                            "⬇ Tailored resume", f,
                            file_name=Path(st.session_state.tailored_paths[url]).name,
                            mime="application/pdf", key=f"dl_{url}")
                elif st.button("Tailor resume for this job", key=f"tailor_{url}"):
                    with st.spinner("Tailoring (one Gemini call)..."):
                        tailored_profile = tailor_profile_for_job(profile, job)
                        tailored_path = tailored_resume_filename(resume_filename, job["company"])
                        build_ats_resume_pdf(tailored_profile, tailored_path)
                    st.session_state.tailored_paths[url] = tailored_path
                    st.rerun(scope="fragment")


# --- Page setup ----------------------------------------------------------------------------

st.set_page_config(page_title="JobScout AI", page_icon="🧭", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_db()

if "profile" not in st.session_state:
    st.session_state.profile = None
    st.session_state.profile_id = None
    st.session_state.resume_filename = None
    st.session_state.last_matches = []
    st.session_state.tailored_paths = {}
    st.session_state.match_report_path = None

if "game_open" not in st.session_state:
    # Whether the game iframe itself is showing. Set True the moment a search starts; "Close
    # game" sets it back to False - but that only hides the iframe now, not the picker dropdown
    # below (game_panel_unlocked), so there's still a way to reopen a game afterward.
    st.session_state.game_open = False

if "game_panel_unlocked" not in st.session_state:
    # Set True the moment a search first starts, and never reset for the rest of the session -
    # this is what keeps the game picker (dropdown) visible in the sidebar even after the user
    # clicks "Close game". game_open (above) only controls whether the iframe itself is showing.
    st.session_state.game_panel_unlocked = False

if "startup_cleanup_done" not in st.session_state:
    deleted = delete_stale_jobs(max_age_days=7)
    st.session_state.startup_cleanup_done = True
    if deleted:
        st.session_state.startup_cleanup_note = f"Cleaned up {deleted} job(s) older than 7 days."

st.markdown(
    '<div class="jsa-header"><span class="jsa-logo">🧭</span><h1>JobScout AI</h1></div>'
    '<div class="jsa-subtitle">Upload your resume, search real job postings, '
    'and see exactly how you match.</div>',
    unsafe_allow_html=True,
)

if st.session_state.get("startup_cleanup_note"):
    st.caption(st.session_state.startup_cleanup_note)

with st.sidebar:
    st.markdown('<div class="jsa-sidebar-brand">🧭 JobScout AI</div>', unsafe_allow_html=True)
    st.markdown('<div class="jsa-sidebar-tag">Local AI job-matching assistant</div>',
                unsafe_allow_html=True)
    st.markdown("**Gemini usage today**")
    counts = get_gemini_call_counts_today()
    if not counts:
        st.caption("No calls logged today.")
    for model, status_counts in counts.items():
        stats_html = "".join(
            f'<div class="jsa-model-stat">{_esc(status)}: <strong>{c}</strong></div>'
            for status, c in status_counts.items()
        )
        st.markdown(
            f'<div class="jsa-model-card">'
            f'<div class="jsa-model-name">{_esc(model)}</div>{stats_html}</div>',
            unsafe_allow_html=True,
        )

# Persistent across reruns once unlocked (see _render_game_panel's docstring) - this is what
# keeps the game picker visible after a search finishes, after "Close game" is clicked, and on
# any later rerun that isn't the search itself (e.g. clicking "Export to PDF"). The inline render
# at the search-button call site below covers the one run where the panel needs to appear but
# hasn't been unlocked yet.
if st.session_state.game_panel_unlocked:
    with st.sidebar:
        st.divider()
        _render_game_panel()

# --- Resume upload + profile extraction -----------------------------------------------------

uploaded_file = st.file_uploader("Upload your resume", type=["pdf", "docx"])

if uploaded_file is not None:
    # extract_resume_text() dispatches by file extension and expects a real path on disk - same
    # function every other entry point uses, so the uploaded bytes are written to a temp file
    # rather than teaching that function to also accept a file-like object.
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    resume_text = extract_resume_text(tmp_path)
    cached = get_profile_by_hash(resume_text)
    if cached:
        profile_id = cached.pop("id")
        profile = cached
    else:
        with st.spinner("Extracting your profile with Gemini (one-time per resume)..."):
            profile = extract_structured_profile(resume_text)
            profile_id = get_or_create_profile(resume_text, profile)
        st.success(f"Profile extracted (id {profile_id}).")

    st.session_state.profile = profile
    st.session_state.profile_id = profile_id
    st.session_state.resume_filename = uploaded_file.name

# --- Profile summary + ATS resume download --------------------------------------------------

if st.session_state.profile:
    profile = st.session_state.profile
    profile_id = st.session_state.profile_id

    with st.expander(f"Extracted profile: {profile.get('full_name') or 'your resume'}", expanded=False):
        st.write(profile.get("summary") or "")
        st.write(f"**Current location:** {profile.get('current_location') or 'not stated'}")
        st.write(f"**Years of experience:** {profile.get('total_years_experience')}")
        st.write(f"**Titles held:** {', '.join(profile.get('job_titles') or []) or 'none extracted'}")

        skills = profile.get("skills") or {}
        if skills.get("hands_on"):
            st.write(f"**Hands-on skills:** {', '.join(skills['hands_on'])}")
        if skills.get("trained"):
            st.write(f"**Familiar with:** {', '.join(skills['trained'])}")

        certs = profile.get("certifications") or []
        if certs:
            cert_labels = [c.get("abbreviation") or c.get("name") for c in certs]
            st.write(f"**Certifications:** {', '.join(cert_labels)}")

    ats_resume_path = str(Path(st.session_state.resume_filename).stem) + " ATS.pdf"
    build_ats_resume_pdf(profile, ats_resume_path)
    with open(ats_resume_path, "rb") as f:
        st.download_button("⬇ Download ATS-optimized resume", f, file_name=ats_resume_path,
                            mime="application/pdf")

    st.divider()

    with st.container(border=True):
        st.subheader("Search for jobs")

        default_title = (profile.get("job_titles") or ["product owner"])[0]

        col1, col2 = st.columns(2)
        with col1:
            search_all = st.checkbox("Search all 5 configured companies (Workday + JSearch/Adzuna)")
            company_input = st.text_input("Company", disabled=search_all,
                                           placeholder="e.g. Citi, Google, Barclays")
            title_input = st.text_input("Specific role to search (optional)",
                                         placeholder=f'defaults to "{default_title}"')
        with col2:
            location_input = st.text_input("Location", value=profile.get("current_location") or "")
            relocation_ok = st.checkbox("I'm open to relocating / any location")

        search_clicked = st.button("Search", type="primary")

        if search_clicked:
            if not search_all and not company_input.strip():
                st.warning("Enter a company, or check 'search all configured companies'.")
            else:
                # If the game panel isn't already unlocked (from an earlier search), render it
                # now, before the blocking call below, so it's visible in the sidebar for THIS
                # run's wait. If it's already unlocked, the persistent sidebar block above
                # already rendered it earlier in this same script pass - rendering it again here
                # would duplicate the widget. The game itself (game_open) re-opens on every new
                # search even if it was previously closed; the picker (game_panel_unlocked)
                # stays visible for the rest of the session regardless of open/closed state.
                was_unlocked = st.session_state.game_panel_unlocked
                st.session_state.game_panel_unlocked = True
                st.session_state.game_open = True
                if not was_unlocked:
                    with st.sidebar:
                        st.divider()
                        _render_game_panel()

                with st.spinner(
                    "Searching and scoring - this can take a while for a fresh search... "
                    "meanwhile have a cup of coffee or pick a game from the side menu while "
                    "the results are displayed."
                ):
                    search_kwargs = dict(
                        title_override=title_input.strip() or None,
                        location=location_input.strip(),
                        relocation_ok=relocation_ok,
                    )
                    if not search_all:
                        search_kwargs["company"] = company_input.strip()
                    matches, log = _run_search_captured(
                        profile, profile_id, all_companies=search_all, **search_kwargs)

                st.session_state.last_matches = matches
                st.session_state.tailored_paths = {}
                st.session_state.match_report_path = None

                rate_limit_waits = [int(m) for m in _RATE_LIMIT_WAIT_PATTERN.findall(log)]
                if rate_limit_waits:
                    st.warning(
                        f"Hit Gemini's rate limit {len(rate_limit_waits)} time(s) during this "
                        f"search and automatically retried, waiting up to "
                        f"{max(rate_limit_waits)}s each time (total ~{sum(rate_limit_waits)}s "
                        f"spent waiting). See the search log below for exactly which batch. "
                        f"If this happens often, try searching fewer companies at once."
                    )
                if log.strip():
                    with st.expander("Search log", expanded=False):
                        st.code(log, language=None)

    with st.expander("Manage companies"):
        st.caption('Companies searched by "search all configured companies." Paste a Workday '
                   "careers URL to add another one - no need to know Workday's internal naming.")
        companies = get_all_companies()
        for name in sorted(companies):
            row_cols = st.columns([4, 1])
            with row_cols[0]:
                st.write(name)
            with row_cols[1]:
                if st.button("Remove", key=f"remove_company_{name}"):
                    remove_company(name)
                    st.rerun()

        st.divider()
        with st.form("add_company_form", clear_on_submit=True):
            new_company_name = st.text_input("Display name", placeholder="e.g. Tesla")
            new_company_url = st.text_input(
                "Workday careers URL",
                placeholder="https://tesla.wd1.myworkdayjobs.com/TeslaCareers")
            add_company_submitted = st.form_submit_button("Add company")

        if add_company_submitted:
            if not new_company_name.strip():
                st.error("Enter a display name for the company.")
            elif not new_company_url.strip():
                st.error("Paste the company's Workday careers URL.")
            else:
                try:
                    parsed = parse_workday_url(new_company_url)
                    add_company(new_company_name.strip(), parsed["company"],
                                parsed["datacenter"], parsed["site"])
                    st.success(f'Added "{new_company_name.strip()}" - it will show up in '
                               f'"search all configured companies" immediately.')
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    # --- Results ---------------------------------------------------------------------------

    if st.session_state.last_matches:
        st.divider()
        # match_score == 0 is not a genuine Gemini verdict - it's the placeholder matcher.py
        # writes for jobs excluded by the free title/experience pre-filter or the prescreen
        # pass (see SCREENED_OUT_PLACEHOLDER / _prefiltered_placeholder in matcher.py). Those
        # rows are kept in the database on purpose (nothing silently disappears, and it avoids
        # re-checking the same job on a future run) but they're not a match worth showing.
        real_matches = [j for j in st.session_state.last_matches if (j.get("match_score") or 0) > 0]
        excluded_count = len(st.session_state.last_matches) - len(real_matches)
        sorted_matches = sorted(
            real_matches,
            key=lambda j: (TIER_ORDER.get(j.get("match_tier"), 3), -(j.get("match_score") or 0)),
        )
        header_cols = st.columns([3, 1])
        with header_cols[0]:
            st.subheader(f"Results ({len(sorted_matches)})")
            if excluded_count:
                st.caption(f"{excluded_count} job(s) excluded by the pre-filter/prescreen before "
                           f"detailed scoring - not shown. See the search log above for why.")
        with header_cols[1]:
            # Moved up here (rather than below the results list) so it's reachable without
            # scrolling through the whole list first.
            if st.session_state.match_report_path:
                with open(st.session_state.match_report_path, "rb") as f:
                    st.download_button("⬇ Match report PDF", f, file_name="match_report.pdf",
                                        mime="application/pdf")
            elif st.button("Export to PDF"):
                output_path = export_matches_to_pdf(st.session_state.last_matches, "match_report.pdf")
                if output_path:
                    st.session_state.match_report_path = output_path
                    st.rerun()
                else:
                    st.info("No Strong or Good matches yet to export.")

        # A fixed-height container scrolls internally instead of letting the whole page grow
        # with the result count - the search panel and header above stay put on screen
        # regardless of how many jobs came back.
        with st.container(height=600, border=True):
            for job in sorted_matches:
                _render_job_card(job, profile, profile_id, st.session_state.resume_filename)
else:
    st.info("Upload a resume above to get started.")
