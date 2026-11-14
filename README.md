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

- Control panel: http://localhost:8080 on this computer (always admin).
- Command line: `ledpanels image|video|text|clock|fill|pixel|clear|test ...` (`ledpanels --help`).
- Python: see `examples/api_demo.py`. Only one program should send to the panels at a time.
- `ledpanels --simulate serve` runs everything without hardware.

## Guests (phones)

In the Guests tab, open guest access for a set time. Guests scan the QR code or type the address (same network
as this computer). They can draw, send text, play patterns and library items, each for at most `guest_max_seconds`.
They cannot upload, delete, change settings, or use the camera, screen or DMX. "Pause guest control" ignores
their input without closing the page. Closing guest access, or the timer ending, locks them out again.

Remote admin: set `admin_pin` in the config (or `LEDPANELS_PIN`), then log in from another device. Without a PIN,
only this computer can administer. There is no HTTPS, so use it only on a network you trust.

## sACN and Art-Net

In the DMX tab, set a virtual pixel grid that your lighting software maps onto (default 96x16, which scales 8x to
768x128), then press Start listening. sACN universes start at the "first universe" (default 1); Art-Net uses
the same numbers. Linear layout is 170 RGB pixels per universe, continuing into the next. The display blanks
after `timeout` seconds without data. Ports: UDP 5568 (sACN, multicast 239.255.x.x or unicast) and UDP 6454
(Art-Net); open them in the firewall if needed. sACN priority is honored between sources.

## HTTP API

Every request needs the header `X-Requested-With: ledpanels` for anything other than GET. Remote clients need a
guest window or an admin cookie from `POST /api/login {"pin": "..."}`.

| Method and path | Purpose | Role |
|---|---|---|
| GET `/api/whoami` | role (`admin`, `guest`, null), guest window state | all |
| GET `/api/status` | current job, fps, settings | guest, admin |
| GET `/api/media`, `/api/media/<name>/thumb`, `/api/patterns`, `/api/fonts`, `/api/preview.png` | read-only info | guest, admin |
| POST `/api/play` | start a job (see below) | guest (limited kinds), admin |
| POST `/api/frame` | raw RGB or RGBA bytes, one full frame (live drawing) | guest, admin |
| POST `/api/stop` | stop and blank | admin |
| PUT/DELETE `/api/media/<name>` | upload / delete a file | admin |
| POST `/api/brightness` `{percent}`, `/api/calibration` `{gamma, balance:[r,g,b]}` | output tuning | admin |
| POST `/api/schedule`, `/api/playlist`, `/api/autoplay`, `/api/dmx` | settings | admin |
| GET/POST `/api/guest`, GET `/api/guest/qr.svg` | guest window and QR | admin |

Jobs for `/api/play`: `{"kind": "image|video", "name": "file", "fit": "contain|cover|stretch", "loop": true}`,
`{"kind": "text", "text": "Hi", "color": [r,g,b], "speed": 100, "font": "name", "size": 60}`,
`{"kind": "clock"}`, `{"kind": "fill", "color": [r,g,b]}`, `{"kind": "pattern", "name": "rainbow"}`,
`{"kind": "playlist", "items": [...], "loop": true}`, `{"kind": "camera", "device": 0}`,
`{"kind": "screen", "monitor": 1}` (X11 only), `{"kind": "dmx"}`. Any job accepts `"duration"` in seconds.

## Notes

- If the cable is unplugged, output retries automatically and recovers when the link returns.
- Fonts: drop `.ttf` or `.otf` files into a `fonts/` folder next to `ledpanels.toml`; system fonts are listed too.
- `ledpanels benchmark` measures send speed; `ledpanels setup` finds the interface.
- Tests: `uv run pytest`.
