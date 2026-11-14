# LED panels controller

Drives Colorlight 5A-75B receiver cards over raw Ethernet from a Linux computer. Images, GIFs, video, text,
live drawing, camera, direct pixels, and sACN / Art-Net input, with a web control panel that phones can use.

## Setup on a new computer

```
git clone <this repo> && cd LED_panels
scripts/setup.sh
.venv/bin/ledpanels serve
```

The script installs dependencies, grants raw-socket permission to the venv's Python (`setcap`, asks first), and
finds the network interface the panels are on. Then check `ledpanels.toml`: the canvas size (`width`, `height`),
`color_order`, and `interface`. `ledpanels discover` lists the receiver cards found on the wire.

## Using it

- Control panel: http://localhost:8080 on this computer (always admin). `ledpanels app` does the same and opens
  the browser.
- Command line: `ledpanels image|video|text|clock|fill|pixel|clear|test ...` (`ledpanels --help`). Also
  `discover`, `benchmark`, `dmx-test` (sends a test rainbow over sACN or Art-Net), `ddp-test` and `setup`.
- Python: see `examples/api_demo.py`. Only one program should send to the panels at a time.
- `ledpanels --simulate serve` runs everything without hardware.
- Optional: `ledpanels install-service` (start the server at login) and `ledpanels install-launcher`
  (application-menu entry). Nothing starts automatically unless you run these.

## How it works

The panels are driven by Colorlight 5A-75B receiver cards. The computer sends them raw Ethernet frames (no IP),
so it must be on the same link and needs `CAP_NET_RAW` (the setup script grants it to the venv's Python).

- The display is one canvas (`width` x `height` in `ledpanels.toml`, 768x128 here) that spans all the cards.
  Everything (images, games, DMX) draws into that canvas, and the sender splits it into one packet per row
  segment, up to 497 pixels each.
- Packet types (`src/ledpanels/protocol.py`): `0x55` pixel rows, `0x0A` brightness, `0x01` sync (latches the
  frame), `0x07` discover and `0x08` reply. The destination MAC is `11:22:33:44:55:66`.
- `color_order` (BGR here), `flip_x` and `flip_y` are applied when each frame is sent, along with the gamma and
  white-balance curve. Brightness is sent to the cards as a packet.
- Output runs at roughly 250 frames per second on a gigabit link; games and video are paced to their own rate.

Code layout:

| Path | Role |
|---|---|
| `sender.py`, `protocol.py`, `sim.py` | raw-socket display, packet builders, hardware-free stand-in |
| `engine.py` | runs one job at a time (image, video, text, game, DMX...), playlist, schedule, saved state |
| `web.py`, `access.py`, `static/index.html` | HTTP server, admin and guest roles with rate limits, the single-page UI |
| `dmx.py` | sACN and Art-Net input |
| `ddp.py` | DDP input for external apps |
| `games/` | the games, plus `pad.py` (player slots and controller state) and `base.py` (shared helpers) |
| `media.py`, `fonts.py`, `patterns.py`, `animations.py` | content helpers |
| `tests/` | pytest suite, which uses the simulated display |

## Configuration

`ledpanels.toml` keys (command-line options override them):

| Key | Meaning |
|---|---|
| `interface` | network interface the panels are on (`ledpanels setup` finds it) |
| `width`, `height` | canvas size, must match the receiver cards |
| `brightness` | percent, 0 to 100 |
| `firmware` | receiver firmware major version, 0 detects it |
| `color_order`, `flip_x`, `flip_y` | wiring corrections |
| `sync_gap` | seconds between the last pixel row and the sync (latch) packet, default 0.001; a Pi 3B+ needs 0.006 (see Troubleshooting) |
| `[[display]]` | optional canvas-to-card mapping per physical display (see Display geometry); none = whole canvas at card (0, 0) |
| `host`, `port` | web server bind address and port (0.0.0.0 lets phones connect) |
| `media_dir`, `state_file` | where uploads and saved settings live |
| `admin_pin` | enables remote admin login (or set `LEDPANELS_PIN`) |
| `guest_max_seconds` | longest a guest's item runs before the panels blank |
| `ddp_port` | UDP port for DDP frame input from external apps, default 0 (off); 4048 is the standard port |

### Display geometry

By default the canvas maps 1:1 onto the receiver cards' coordinate space from (0, 0). If your displays sit
elsewhere in card space, or you want strips that are side by side on the cards stacked on the canvas, add one
`[[display]]` entry per physical display. Each maps a canvas region `canvas = [x, y, w, h]` to the card position
`card = [x, y]` of its top-left pixel, with optional `flip_x`, `flip_y` and `reverse_panels` (panel order
reversed within each row, with `panel_width`, default 128). Canvas pixels outside every region are not sent.
Example: two 768x32 strips that the cards address as one 1536-pixel row (card x 0-767 and 768-1535, rows 160-191),
stacked into a 768x64 canvas:

```toml
width = 768
height = 64

[[display]]
canvas = [0, 0, 768, 32]    # top strip
card = [0, 160]

[[display]]
canvas = [0, 32, 768, 32]   # bottom strip
card = [768, 160]
```

Rows of different displays that land on the same card row are merged into one run from the leftmost x (gaps are
sent black), since chained cards only showed the second display when the rows were sent separately. The mapping
is precomputed at startup. `ledpanels test border` and `test tiles` mark each display separately, so you can
check order and orientation; the global `flip_x`/`flip_y` still apply to the whole canvas first.

Saved settings (calibration, schedule, playlist, DMX, autoplay) are in `state.json`, which is not committed.

## Guests (phones)

In the Guests tab, open guest access for a set time. Guests scan the QR code or type the address (same network
as this computer). They can draw, send text, play patterns and library items, each for at most `guest_max_seconds`.
They cannot upload, delete, change settings, or use the camera or DMX. "Pause guest control" ignores
their input without closing the page. Closing guest access, or the timer ending, locks them out again.

Remote admin: set `admin_pin` in the config (or `LEDPANELS_PIN`), then log in from another device. Without a PIN,
only this computer can administer. There is no HTTPS, so use it only on a network you trust.

## Games

The Games tab (also on the guest page) lists the games. Pressing Play starts one and turns the phone into a
controller. Most games use a D-pad with A and B; others replace it with something better suited (answer buttons,
a drawing canvas, a tap button, a touch pad). More phones can press "Join this game". Each phone keeps one
player slot, and a game with no input for 2 minutes ends and blanks the panels. Guests can play too.

- **Panel Run**: an original one-screen platformer (96x16 tiles of 8 pixels, the whole level always visible).
  Up to 4 players race to the flag. Run, jump, stomp the walkers, hit coin boxes. Needs the 768x128 display.
- **Pong**: 2 players; the computer plays any empty side.
- **Panel Flap**: up to 4 birds share one course of pipes. Tap A to flap. When a bird reaches the right edge play
  pauses and the whole screen scrolls over, so the course crosses every panel and keeps getting harder. The last
  bird flying wins.
- **Panel Chomp**: a random maze across the whole display (a new one each round), up to 4 players, one life each.
  Eat the dots, avoid the wisps, grab a corner power pellet to chase them for a few seconds. If the maze is
  cleared the game ends on a podium (1st, 2nd, 3rd by score, survivors ahead of anyone who was caught); if every
  player is caught, the last one standing wins.

Multiplayer games wait in a lobby until the **host presses START** on their phone. The host is the first player
to join (if they leave, the next in line). Anyone joining mid-round plays the next one.

- **Panel Bomber**: Bomberman-style arena across the whole display, up to 4 players. Drop bombs (A), blow up
  crates for power-ups (bigger blasts, more bombs, speed), last player standing wins. After 75 seconds the arena
  closes in from the edges so rounds always end.
- **Panel Sweep**: Minesweeper for up to 6 players on one shared board (48x7 squares). A reveals (or clears around
  a satisfied number), B flags. Revealing scores points, each mine costs one of two lives and stuns you for 3
  seconds, correct flags earn +10 and wrong ones cost 10 at the end. Clear the board for a podium.
- **Panel Trivia**: up to 8 players answer on their phones (four colored buttons). Faster correct answers score
  more (500 to 1000). The host picks the deck (mixed, nerd, engineering, movies and TV, sci-fi, fantasy) and the
  number of questions with the arrows, then presses START. Ends on a podium.
- **Panel Hop**: Frogger across the whole display, up to 8 players. Cross a chain of biomes (meadow, river, forest,
  swamp, desert, snow, volcano, then a huge multilane city highway) to the finish pond. Difficulty escalates toward
  the end, there are checkpoints and 3 lives, and cars flash a warning before entering the screen. The test suite
  includes a bot that proves every stretch is crossable (`LED_SLOW_TESTS=1` runs extra seeds).
- **Panel Sketch**: Pictionary for up to 8 players. The server picks the drawer, who draws a secret word on their
  phone while everyone else types guesses. Every player draws once per cycle (host picks 1 to 3 cycles with the
  arrows). Faster correct guesses score more, the drawer earns a bonus, and letters are revealed as time runs out.
  The right end of the display holds the timer, hint and leaderboard. Guessers never receive the word.
- **Panel Tug of War**: 2 to 8 players are split into two teams (random, balanced) and tap a big button on their
  phones to drag a flag along a rope. A team's pull is its taps divided by its size, so uneven teams are fair.
  Drag the flag to the far line to win a round at once, otherwise the side holding the flag when the timer ends
  wins (a dead heat goes to overtime). The host picks best of 1, 3 or 5 (LEFT/RIGHT) and 10 to 30 seconds a round
  (UP/DOWN). Taps are sent as messages and capped at 12 per second per player.
- **Panel Hockey**: air hockey across the whole display for up to 4 players. Players 1 and 3 defend the left goal,
  2 and 4 the right, and the computer plays any side with nobody on it. Each phone shows a touch pad: slide your
  finger to move your mallet inside your half and hit the puck into the other goal. The host picks first to 3, 5,
  7 or 10 goals with the arrows. Players can join or leave mid-match.

Trivia questions come from Open Trivia DB (https://opentdb.com, CC BY-SA 4.0), downloaded once into
`src/ledpanels/data/trivia.json` so the game works offline. Refresh or enlarge it with
`python scripts/fetch_trivia.py`; `--retag` re-sorts the saved questions into decks without downloading.

To add a game, create a class in `src/ledpanels/games/` with `id`, `title`, `description`, `max_players`,
`controls`, `update(dt, pads)` and `render(canvas)`, then list it in `REGISTRY` in `games/__init__.py`.
`pads` has one entry per player slot: `None` if nobody joined, else the held buttons as a bitmask
(`LEFT 1, RIGHT 2, UP 4, DOWN 8, A 16, B 32, START 64, X 128, Y 256`; see `games/pad.py`). Rendering is plain numpy
at the display's native resolution (RGB), and `games/base.py` has the shared host-START `Lobby`, tile movement,
podium and text helpers.

Optional hooks:

- `info()` returns public state (lobby, scores, timers) that is merged into `/api/status` and `/api/game/state`.
- `private_info(slot)` returns what only that player may see (the word to draw, their own team).
- `on_message(slot, data)` receives free-form messages from `POST /api/game/message` (taps, drawing strokes,
  guesses, touch positions). Validate everything: any phone can send anything.
- `layout` picks the phone controller: `dpad` (default), `quiz` (four answer buttons), `sketch` (draw and guess),
  `tug` (tap button) or `air` (touch pad). A new layout needs a pane in `static/index.html`; the page polls
  `/api/game/state` to draw it.

Games that need messages should handle them in `on_message` and keep per-player rate limits (see `tug.py` and
`sketch.py`). Tests follow `tests/test_games_multiplayer.py` (`begin`, `pads_for`, `DT`).

## sACN and Art-Net

In the DMX tab, set a virtual pixel grid that your lighting software maps onto (default 96x16, which scales 8x to
768x128), then press Start listening. sACN universes start at the "first universe" (default 1); Art-Net uses
the same numbers. Linear layout is 170 RGB pixels per universe, continuing into the next. The display blanks
after `timeout` seconds without data. Ports: UDP 5568 (sACN, multicast 239.255.x.x or unicast) and UDP 6454
(Art-Net); open them in the firewall if needed. sACN priority is honored between sources.

## External apps (DDP)

Other programs (games, the theremin at https://github.com/juan-ms2lab/theremin, xLights, WLED tools) can push frames
with DDP (Distributed Display Protocol) over UDP. It is off by default; enable it with `ddp_port = 4048` in
`ledpanels.toml` and restart `serve`. It listens on the same address as the web server (`host`), so with
`host = "127.0.0.1"` only programs on this computer can send. DDP has no authentication: anything that can
reach the port can take over the panels, regardless of guest settings.

- Packets: 10-byte header (flags `0x40`, `| 0x01` PUSH on the last packet of a frame; sequence; data type, read
  as RGB; destination id `1`; data offset in bytes and data length, both big-endian), then up to 1440 bytes of
  RGB data, row-major over the whole canvas (`offset = (y * width + x) * 3`). The sender must use the canvas
  size from `ledpanels.toml`; the driver does no scaling.
- On PUSH the assembled frame is shown at once (the same live path as drawing, so it replaces whatever is
  playing). After 2 seconds without data the panels blank. `/api/status` reports `ddp` (port, live, packet and
  frame counts, last source).
- `ledpanels ddp-test [--target IP]` sends a test rainbow to a running server.

## HTTP API

Every request needs the header `X-Requested-With: ledpanels` for anything other than GET. Remote clients need a
guest window or an admin cookie from `POST /api/login {"pin": "..."}`.

| Method and path | Purpose | Role |
|---|---|---|
| GET `/api/whoami` | role (`admin`, `guest`, null), guest window state | all |
| POST `/api/login` `{pin}`, `/api/logout` | admin session cookie | all |
| GET `/api/status` | current job, fps, settings | guest, admin |
| GET `/api/media`, `/api/media/<name>/thumb`, `/api/patterns`, `/api/fonts`, `/api/preview.png` | read-only info | guest, admin |
| POST `/api/play` | start a job (see below) | guest (limited kinds), admin |
| POST `/api/frame` | raw RGB or RGBA bytes, one full frame (live drawing) | guest, admin |
| GET `/api/games`; POST `/api/game/join`, `/api/game/input` `{token, keys}`, `/api/game/leave` | game catalog and controller | guest, admin |
| POST `/api/game/message` `{token, data}`, `/api/game/state` `{token}` | game-specific messages (drawing, guesses) and per-player state | guest, admin |
| POST `/api/stop` | stop and blank | admin |
| PUT/DELETE `/api/media/<name>` | upload / delete a file | admin |
| POST `/api/brightness` `{percent}`, `/api/calibration` `{gamma, balance:[r,g,b]}` | output tuning | admin |
| POST `/api/schedule`, `/api/playlist`, `/api/autoplay`, `/api/dmx` | settings | admin |
| GET/POST `/api/guest`, GET `/api/guest/qr.svg` | guest window and QR | admin |

Jobs for `/api/play`: `{"kind": "image|video", "name": "file", "fit": "contain|cover|stretch", "loop": true}`,
`{"kind": "text", "text": "Hi", "color": [r,g,b], "speed": 100, "font": "name", "size": 60}`,
`{"kind": "clock"}`, `{"kind": "fill", "color": [r,g,b]}`, `{"kind": "pattern", "name": "rainbow"}`,
`{"kind": "playlist", "items": [...], "loop": true}`, `{"kind": "camera", "device": 0}`,
`{"kind": "dmx"}`. Any job accepts `"duration"` in seconds.

## Troubleshooting

- "Operation not permitted" when sending: the venv's Python lacks `CAP_NET_RAW`. Re-run `scripts/setup.sh`, or
  `sudo setcap cap_net_raw+ep $(readlink -f .venv/bin/python)`. Recreating the venv removes the permission.
- Nothing lights up: check `interface` in `ledpanels.toml`, the cable, and that `ledpanels discover` lists the cards.
- Wrong colors or mirrored image: adjust `color_order`, `flip_x`, `flip_y`; `ledpanels test` shows an orientation pattern.
- Phones cannot connect: they must be on the same network, `host` must be `0.0.0.0`, guest access must be open
  in the Guests tab, and port 8080 (plus UDP 5568 and 6454 for DMX) must be allowed in the firewall.
- Another program is sending to the panels: only one sender may run at a time.
- Rows of the second (chained) card tear or flicker: raise `sync_gap`. `send()` only queues the packets, so on a
  slow NIC the sync can overtake the pixel rows still on the wire. A Raspberry Pi 3B+ (USB NIC, `lan78xx`) takes
  about 5 ms to put a 1536x32 frame on the wire and needs `sync_gap = 0.006`; 0.001 tore the second card's rows.

## Notes

- Games are verified by the test suite, including a bot that proves Panel Hop is completable.
  `LED_SLOW_TESTS=1 uv run pytest` runs extra Hop seeds.
- If the cable is unplugged, output retries automatically and recovers when the link returns.
- Fonts: drop `.ttf` or `.otf` files into a `fonts/` folder next to `ledpanels.toml`; system fonts are listed too.
- `ledpanels benchmark` measures send speed; `ledpanels setup` finds the interface.
- Tests: `uv run pytest`.

## License

GNU General Public License v3.0, see `LICENSE`. The bundled trivia questions are from Open Trivia DB under
CC BY-SA 4.0 (see the Games section).
