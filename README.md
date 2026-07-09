# Tidbyt-Style Baseball Scoreboard (LEDMatrix plugin)

A custom MLB scoreboard for [ChuckBuilds/LEDMatrix](https://github.com/ChuckBuilds/LEDMatrix),
built for a 128x32 panel: two team columns on the left (logo, bold
abbreviation + score), and a diamond/inning/count/outs readout on the
right. By default it cycles through **every currently live MLB game**,
not just your favorite team's.

## Install

1. Push this folder to your own GitHub repo (repo root = this folder).
2. On your Pi, open the web UI: `http://<pi-ip>:5000` → **Plugin Manager**.
3. Use **Install from GitHub URL**, paste your repo URL.
4. It installs into `plugin-repos/tidbyt-baseball-scoreboard/` (folder
   name matches the `id` in `manifest.json`).
5. Restart the display service so the loader picks it up.
6. In the web UI config editor, add the block from `example_config.json`,
   setting `favorite_teams` to your team(s).

## Testing without hardware

Set `"test_mode": true` to render a single fake in-progress game (ATH @
DET, top 3rd, runners on 1st/3rd) instead of calling ESPN.

## What's new in this version

- **Bigger logos**: team columns are now side-by-side (not stacked), so
  each team gets the panel's full height for its logo instead of half.
- **Fixed inning indicator**: the up/down inning arrow is now drawn as a
  solid triangle, not a unicode ▲/▼ character. Unicode arrows render as
  a blank "tofu" box on a lot of bitmap/embedded fonts — that's what
  caused the empty square you saw.
- **Bolder team/score text**: drawn with a faux-bold technique (text
  rendered at a couple of 1px offsets to thicken the strokes) rather
  than relying on a bold TTF actually being available at runtime.
- **Live game rotation**: cycles through every live MLB game leaguewide
  every `game_rotation_seconds` (default 8s). Set
  `"show_favorite_teams_only": true` to restrict rotation to your
  favorite teams instead. If nothing is live anywhere, it falls back to
  showing your favorite team's next/most recent game.
- **Configurable indicator colors**: `base_fill_color` /
  `base_empty_color` for the diamond, `out_fill_color` /
  `out_empty_color` for the outs squares.

## Layout notes / where to tweak things

All rendering lives in `manager.py::display()`:

- **Team columns** (`_draw_team_column`): logo on top (sized to fill
  the column minus the text row), bold `"ABBR SCORE"` on one line
  underneath. Logos resolve in this order:
  1. Bundled local logo at `{logo_dir}/{ABBR}.png` (defaults to
     `assets/sports/mlb_logos/`, the same folder the core LEDMatrix
     managers use)
  2. ESPN download as a fallback
  3. No logo — just abbreviation + score

  Cached in memory per team abbreviation, so this only runs once per
  team regardless of source. Set `"show_logos": false` to skip it.
- **Right half quadrants**:
  - upper-left: inning indicator (`_draw_inning`) — solid triangle,
    point up for top of inning, point down for bottom
  - upper-right: diamond of bases (`_draw_diamond`) — colors
    configurable via `base_fill_color`/`base_empty_color`
  - lower-left: ball-strike count (`_draw_count`), orange text
  - lower-right: outs indicator (`_draw_outs`) — colors configurable
    via `out_fill_color`/`out_empty_color`
- **Rotation** (`_maybe_rotate`, `_current_game`): advances through
  `self.live_games` every `game_rotation_seconds`, called once per
  `display()` frame so switching isn't tied to the ESPN poll interval.

## Config options

See `config_schema.json` for the full list.

| Key | Default | Notes |
|---|---|---|
| `favorite_teams` | `["PHI"]` | Fallback game + rotation filter if restricted |
| `show_favorite_teams_only` | `false` | Restrict rotation to favorite teams' live games |
| `game_rotation_seconds` | `8` | How long each live game shows before switching |
| `update_interval_seconds` | `300` | Poll rate when nothing is live |
| `live_update_interval_seconds` | `15` | Poll rate while games are live |
| `use_team_colors` | `true` | Pull real team colors from ESPN |
| `show_logos` | `true` | Show team logos |
| `logo_dir` | `assets/sports/mlb_logos` | Local logo folder, checked before ESPN |
| `base_fill_color` / `base_empty_color` | white / grey | Diamond colors |
| `out_fill_color` / `out_empty_color` | orange / grey | Outs indicator colors |
| `test_mode` | `false` | Render a fake game for layout testing |

## Data source

ESPN's public scoreboard endpoint, no API key required:
```
https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard
```
