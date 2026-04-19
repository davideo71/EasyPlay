# EasyPlay Design Review — v60 → easyplay_design_1

A deep read of `easyplay60.py` (2,882 lines) and a screenshot of the live UI,
from the perspective of the three things that matter: **stroke-recovery
accessibility**, **clarity of the moment-to-moment flow**, and **code
maintainability**. Organized from highest-impact to lowest.

---

## Part 1 — User interface / visual design

### 1.1 The home carousel has no visual hierarchy

Current: 11 slots of cover art, the center one slightly larger with a soft
glow, title text at the bottom.

Problems:
- Center vs. outer is a **size difference of 1.6×** — too subtle. For a user
  re-learning how to focus visual attention, the target should read from across
  the room at a glance.
- Glow is gentle; doesn't announce "this is where you are"
- Title text lives at the bottom, far from the focus element — the eye has to
  jump vertically

**Suggestion D1 (high impact):** Make the center slot more than 2× the
outer slots, add a subtle colored frame around it, and **move the title
directly under the selected cover** (not at the bottom of the screen).
Caption the outer slots very faintly (or not at all). The user's gaze
should have exactly one answer to "what am I looking at?".

---

### 1.2 The series picker is visually confusing

From the screenshot: Amsterdam Empire is open → the poster dominates the top,
the episode thumbnails are 16:9 landscape below, and faint "EMPIRE" text from
the backdrop bleeds through. Three different visual elements compete.

Problems:
- The episode row's own cover is redundant (we already saw the series cover
  getting here)
- Episode 1 label on black text over a dark thumbnail is hard to read
- No clear "you are inside Amsterdam Empire" breadcrumb

**Suggestion D2 (high impact):** Simplify the picker:
- Use the series poster as a **blurred full-screen background** (no sharp
  redundant copy layered on top)
- Episode row fills the middle third of the screen cleanly, no backdrop noise
- Episode labels have a dark semi-transparent bar underneath for legibility
- A small title crumb at top-left: "← Amsterdam Empire · Season 1"
- The title of the *episode* you'd play (not the series) under the selected
  episode thumbnail

---

### 1.3 Color palette is pragmatic but joyless

Current usage:
- Background: `(10, 10, 15)` near-black
- Title accent: `(102, 126, 234)` cool blue (the purple from the login gradient)
- Progress bar: white fill on grey 40% bg
- Glow: pure alpha white

Problems:
- Accent color appears in one place (app title on wait screen). Everything else
  is greyscale
- No distinct "selected" / "watched" / "in progress" color language

**Suggestion D3 (medium impact):** Define a 6-color palette and use it
consistently:
- `bg` — near-black (keep)
- `bg-raised` — `(20, 20, 28)` for cards/overlays
- `accent` — violet gradient base `(118, 75, 162)` → highlight `(102, 126, 234)`
- `text-primary` — `(245, 245, 250)`
- `text-dim` — `(140, 140, 155)` (for non-selected captions)
- `success` — `(80, 180, 120)` (completed / watched)
- `progress` — `(220, 220, 230)` (in-progress bar)

---

### 1.4 Typography is all one font at one weight

`pygame.font.SysFont("Helvetica", 36, bold=True)` for the main title, 18 for
smaller, 48/28 for wait screens. Everything lives on the same type scale and
weight.

**Suggestion D4 (medium impact):** Introduce a type scale:
- `display` — 72pt bold (selected-item title)
- `heading` — 36pt bold (menu headers)
- `body` — 22pt regular (list items, captions)
- `meta` — 16pt regular (dim context: episode count, file size, duration)

And render the display title with a subtle **text shadow** instead of no
outline — reads better on any background.

---

### 1.5 Missing persistent navigation hints

Nothing on screen tells you what the buttons do. A first-time user wouldn't
know LEFT/RIGHT scrolls, UP plays, DOWN quits.

**Suggestion D5 (high impact for accessibility):** Footer bar (~40px high,
near-transparent) always visible showing 3–5 button hints for the current
screen:

    ◀ ▶ browse    ▲ play    ▼ back    ⏻ power

Icons large enough to read easily. Hide during video playback.

---

### 1.6 The "watched / in progress / unseen" state isn't visible in the carousel

Progress tracking exists (`progress_ratio`) and draws a bar during playback,
but the home carousel shows nothing about it. User has no way to answer "which
ones have I seen?" without opening each.

**Suggestion D6 (high impact for usability):** In the carousel:
- **Unseen** items: cover as-is
- **In progress** (0 < ratio < 95%): horizontal progress bar across the bottom
  of the cover + small play-arrow badge at top-right
- **Completed** (ratio ≥ 95%): small green check badge at top-right, slightly
  dimmed cover

---

### 1.7 The wait screen is a good starting point — keep it, just polish

The "USB drive not connected" screen is calm and clear. Minor polish:
- Replace the text dot-animation with a subtle pulsing USB icon SVG
- Add "Plug in the media drive to continue" as the primary instruction
- Make the "press Q or ESC to quit" hint *much* smaller at the very bottom

---

## Part 2 — Interaction / UX / accessibility

### 2.1 Setup menu is hidden behind "hold DOWN"

Discovery problem. If a caregiver or someone new sits at the Pi, they have no
idea this menu exists.

**Suggestion I1:** Add a small "⚙" icon in the top-right corner of the
carousel, and make it an actual navigable slot (focused after the last
media item when you scroll right past the end).

---

### 2.2 Animation duration is too fast for some users

`ANIM_MS = 420` for scroll transitions. For someone with reduced reaction time,
fast transitions are disorienting. For autoscroll it's `ANIM_MS_AUTOSCROLL = 300`,
faster still.

**Suggestion I2:** Add an "animation speed" option to setup with 3 presets:
- Calm: 700ms / 500ms
- Normal: 420ms / 300ms (current)
- Fast: 250ms / 180ms

Default to Calm.

---

### 2.3 Accidental double-press handling is fragile

`DEBOUNCE_MS = 180` is a single number for everything. A stroke-recovery user
might double-tap unintentionally on a different cadence than a debounce
window assumes.

**Suggestion I3:** Track time-since-last-press per button separately, and let
the user configure the debounce window in setup (100–500ms, default 200ms).

---

### 2.4 No "continue watching" shortcut

Currently, to resume a half-watched thing you have to scroll to it and select
it. If it were the last thing you watched, we could offer a 1-button resume.

**Suggestion I4:** If the most recently watched title has progress_ratio
between 1% and 95%, show a translucent **"Resume: [title] — 1h 12m left"**
prompt in the top center of the carousel for the first 5 seconds. UP key
resumes immediately.

---

### 2.5 Audio track / subtitle selection is currently invisible

`play_video_embedded` exists (VLC embedded path) but I see no user-facing
audio-track or subtitle picker. For multi-language films this matters.

**Suggestion I5:** While in playback, a long press of UP opens a small
right-edge panel: **Audio** (list of tracks), **Subtitles** (list), **Speed**
(0.75x / 1x / 1.25x). LEFT/RIGHT navigates, UP picks, DOWN closes.

---

### 2.6 Global volume is invisible

No on-screen volume control. If the TV is off-remote, the user can't adjust.

**Suggestion I6:** Add volume as a setup option, and show a brief volume bar
overlay (2 seconds) when changed during playback via DOWN held.

---

### 2.7 Error recovery is silent

When BLE disconnects, the log notes it but the UI shows nothing. A user
wondering why the remote stopped working has no feedback.

**Suggestion I7:** Small icon bottom-right showing BLE status:
- Solid = connected
- Pulsing = reconnecting
- Grey with warning = not connected (after 10s of failed reconnects)

---

## Part 3 — Code structure / maintainability

### 3.1 Monolithic file

2,882 lines in one `.py`. `main()` alone spans ~830 lines and contains:
event handling, rendering, BT menu, keymap menu, setup menu, picker, standby,
CEC, autoscroll, progress tracking, all state transitions.

**Suggestion C1 (high impact):** Split into modules, e.g.:

    easyplay/
      __init__.py         # version, constants
      config.py           # _load_config, _save_config, get_* helpers
      media.py            # scan_media_library, MediaItem, clean_media_name
      thumbnails.py       # thumb cache
      progress.py         # watched/in-progress tracking
      ble.py              # BLE listener
      cec.py              # TV control
      ui/
        layout.py         # compute_layout, WheelLayout, ease
        carousel.py       # home screen render
        picker.py         # episode picker render
        overlays.py       # seek overlay, pause icon, volume bar
        setup.py          # hidden menus
        theme.py          # Palette, Type, spacing constants
      player.py           # VLC embedded + external
      app.py              # main() event loop, dispatch
    easyplay_design_1.py  # thin entry point: `from easyplay.app import main; main()`

Each file 100–300 lines. Individual concerns become testable.

---

### 3.2 `main()` has no clear screen-state machine

The event loop handles every mode inline with `if state.show_setup: ... elif
state.show_bt_menu: ... elif state.in_picker: ... else: ...` branches. Adding
a new screen requires touching the master branch ladder.

**Suggestion C2:** Introduce a `Screen` base class:

    class Screen:
        def on_event(self, event, app): pass
        def on_tick(self, app): pass
        def render(self, surface, app): pass

Home, Picker, Setup, Bluetooth, Keymap, Playback, Wait become subclasses.
The app keeps a stack (`self.screens: list[Screen]`). Push/pop on navigation.
`main()` becomes ~60 lines.

---

### 3.3 Global state scattered across AppState + module globals

`AppState` dataclass holds most of the running state, but `_progress_cache`,
`_ble_key_queue`, `_ble_thread`, `_cec_busy`, `_frame_buf`, etc. are
module-level. Testing or replacing any of them is hard.

**Suggestion C3:** Move all runtime state into `AppState` (or `App` context
object). Module-level globals become configuration constants only.

---

### 3.4 Naming is inconsistent

`_pick_cover`, `_is_hidden`, `_natural_sort_key` vs. `clean_media_name`,
`scan_media_library`, `get_media_folder`. Private helpers are marked with `_`
sometimes, sometimes not.

**Suggestion C4:** Pick one convention: public functions `no_underscore`,
internal helpers `_leading_underscore`. Apply across all modules.

---

### 3.5 Magic numbers everywhere

`GAP = 12`, `N_SLOTS = 11`, `CENTER_SLOT = 5`, `ANIM_MS = 420`,
`DEBOUNCE_MS = 180`, `GLOW_PAD = 14`, `PROGRESS_BAR_H = 60`, …

**Suggestion C5:** Group into named settings dataclasses:

    @dataclass(frozen=True)
    class Timing:
        anim_ms: int = 420
        anim_autoscroll_ms: int = 300
        debounce_ms: int = 180
        seek_interval_s: float = 0.25

    @dataclass(frozen=True)
    class Spacing:
        gap: int = 12
        progress_bar_h: int = 60
        glow_pad: int = 14

---

### 3.6 Filename cleaner is now enormous and deserves its own file with tests

`_CLEAN_PATTERNS` is ~80 regex lines. No unit tests. Every time a new scene
tag is added we rely on eyeballing the result.

**Suggestion C6:** Move to `media/cleaner.py`, add a `test_cleaner.py` with
the 70+ real folder names we've processed as cases. Fail the build on a
regression.

---

## Part 4 — Performance

### 4.1 Startup time dominated by thumbnail generation

`generate_all_thumbnails` runs on first launch; for 70+ videos it can take
30s+. During that time the user sees nothing useful.

**Suggestion P1:** Show the carousel **immediately** with placeholder
gradient-colored tiles, then swap in thumbnails as they become ready
(background worker + dirty-flag). Users can already scroll while generation
is happening.

---

### 4.2 Cover loading is not lazy

`load_cover_thumbs(items, cache, screen)` loads every cover before first
render. For a library of 200+ items this adds proportional startup cost.

**Suggestion P2:** Load only the covers visible in the current window of 11
slots + 5 on each side for prefetch. Evict beyond ±20. Constant memory
regardless of library size.

---

### 4.3 `pygame.event.get()` inside a per-tick busy loop

Main loop already does `state.render_dirty = True` on any input, which is
good. But the loop itself still wakes on a timer. On idle, we can drop to
`pygame.event.wait(timeout=100)` — OS-blocked, ~0% CPU until event or timeout.

**Suggestion P3:** On the home screen when not animating, use
`pygame.event.wait(timeout=16ms)`. Already partial — extend the pattern.

---

## Part 5 — New features worth considering

### 5.1 Library sort / filter

Currently alphabetical. Adding "Recently added", "Unseen", "Continue
watching" filters would make the library usable as it grows.

### 5.2 Search

For libraries of 100+ titles. On-screen keyboard (D-pad navigable) or voice
search via a long-press of UP on the setup screen.

### 5.3 Multi-user profiles

Two viewers with different "watched" states. Each has a simple name + color.
Overkill for single-user households but trivial for families.

### 5.4 Background auto-fetch covers

Detect newly added folders without covers; quietly run `fetch_covers.py`
against them in the background. User never needs to remember to run the tool.

### 5.5 Sleep timer during playback

Useful at night: "stop in 30 minutes" option in the long-press UP menu.

---

## Priorities for `easyplay_design_1.py`

If we implement everything above in one commit, we'll ship a regression. My
recommended priority order, with rough effort estimates:

### Phase A — high-impact, contained (1–2 hours work)

- D1: bigger center slot, title under cover
- D3: define palette, apply to all UI surfaces
- D5: persistent navigation hint footer
- D6: watched / in-progress / unseen badges in carousel
- I1: surface the setup menu as a visible slot
- I7: BLE status indicator
- P1: immediate render with placeholder tiles

### Phase B — code structure (2–3 hours, higher regression risk)

- C1: split into modules
- C2: Screen stack state machine
- C5: grouped settings dataclasses
- C6: filename cleaner + tests

### Phase C — nice-to-have (half a day each)

- D2: picker redesign
- I2: animation speed preset
- I4: resume-last prompt
- I5: audio/subtitle/speed panel during playback
- P2: lazy cover loading
- 5.4: background auto-cover-fetch

---

## Recommendation for this round

**`easyplay_design_1.py`** = current file + everything in Phase A.

No module split yet (that's `easyplay_design_2` or a separate refactor
branch). Keep the single-file layout so it's drop-in for the systemd
service and desktop icon. Bump version, preserve `easyplay60.py`
untouched so we can revert instantly.

That means `easyplay_design_1.py` delivers:

- Palette constants at top of file
- Bigger, more emphasized center slot
- Title under center cover, smaller title at bottom
- Watched/in-progress badges on covers
- Navigation hint footer (hidden during playback)
- Setup "⚙" as the rightmost carousel slot
- BLE status dot in the bottom-right corner
- Immediate carousel render (placeholder → real thumbs)

Tell me which items from Phase A to keep or drop, and anything from Phase B/C
you want to pull forward. Then I'll write `easyplay_design_1.py`.
