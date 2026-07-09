"""
Tidbyt-Style Baseball Scoreboard plugin for ChuckBuilds/LEDMatrix.

Layout:
  - Left half: two team columns side by side (not stacked), each full
    panel height, so each team gets a much bigger logo. Logo on top,
    "ABBR SCORE" bold text below.
  - Right half (black background):
      - upper-left:  inning indicator (solid triangle + inning number)
      - upper-right: diamond of bases (lit when occupied, configurable colors)
      - lower-left:  ball-strike count
      - lower-right: outs indicator (configurable colors)

By default this cycles through every currently-live MLB game leaguewide
(not just your favorite teams) every `game_rotation_seconds`. Set
`show_favorite_teams_only: true` to restrict rotation to your favorite
teams' live games. If nothing is live, it falls back to showing your
favorite team's most recent/upcoming game.

Data comes from ESPN's public scoreboard API:
    https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard

NOTE ON display_manager: different LEDMatrix versions have exposed the
PIL image slightly differently over time. This plugin builds its own
RGB PIL.Image internally and then tries, in order:
    1. display_manager.image.paste(...) + display_manager.update_display()
    2. display_manager.set_image(...)
If neither exists, _push_image() raises a clear error telling you what
attribute to wire up.
"""

import logging
import os
import time
from io import BytesIO
from typing import Optional, Dict, Any, Tuple, List

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    from src.plugin_system.base_plugin import BasePlugin
except ImportError:
    # Fallback for local/dev-preview testing outside the full package tree.
    class BasePlugin:  # type: ignore
        def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
            self.plugin_id = plugin_id
            self.config = config
            self.display_manager = display_manager
            self.cache_manager = cache_manager
            self.plugin_manager = plugin_manager
            self.logger = logging.getLogger(plugin_id)


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

DEFAULT_AWAY_COLOR = (0, 142, 226)
DEFAULT_HOME_COLOR = (200, 16, 46)


class TidbytBaseballPlugin(BasePlugin):
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.logger = logging.getLogger(f"plugin.{plugin_id}")
        self._derive_settings()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "LEDMatrix-TidbytBaseball/1.0"})

        # Rotation state
        self.live_games: List[Dict[str, Any]] = []
        self.fallback_game: Optional[Dict[str, Any]] = None
        self.current_index: int = 0
        self.last_switch_time: float = time.time()
        self.last_fetch_time: float = 0.0

        self._logo_cache: Dict[str, Optional[Image.Image]] = {}

        self.font_team = self._load_font(8, bold=True)
        self.font_small = self._load_font(9)
        self.font_tiny = self._load_font(7)

    # ------------------------------------------------------------------
    # Config handling
    # ------------------------------------------------------------------
    def _derive_settings(self):
        cfg = self.config or {}
        self.favorite_teams = [t.upper() for t in cfg.get("favorite_teams", ["PHI"])]
        self.update_interval = cfg.get("update_interval_seconds", 300)
        self.live_update_interval = cfg.get("live_update_interval_seconds", 15)
        self.game_rotation_seconds = cfg.get("game_rotation_seconds", 8)
        self.show_favorite_teams_only = cfg.get("show_favorite_teams_only", False)
        self.display_duration = cfg.get("display_duration", 20)
        self.away_color_fallback = tuple(cfg.get("away_color", DEFAULT_AWAY_COLOR))
        self.home_color_fallback = tuple(cfg.get("home_color", DEFAULT_HOME_COLOR))
        self.use_team_colors = cfg.get("use_team_colors", True)
        self.show_logos = cfg.get("show_logos", True)
        self.logo_dir = cfg.get("logo_dir", "assets/sports/mlb_logos")
        self.base_fill_color = tuple(cfg.get("base_fill_color", [255, 255, 255]))
        self.base_empty_color = tuple(cfg.get("base_empty_color", [95, 95, 95]))
        self.out_fill_color = tuple(cfg.get("out_fill_color", [255, 140, 0]))
        self.out_empty_color = tuple(cfg.get("out_empty_color", [120, 120, 120]))
        self.test_mode = cfg.get("test_mode", False)

    def on_config_change(self, new_config):
        self.config = new_config
        self._derive_settings()
        self.last_fetch_time = 0

    def validate_config(self) -> bool:
        if not self.favorite_teams:
            self.logger.error("No favorite_teams configured")
            return False
        return True

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = [
            "assets/fonts/PressStart2P.ttf",
            "assets/fonts/4x6-font.ttf",
            "assets/fonts/PressStart2P-Regular.ttf",
        ]
        if bold:
            candidates += [
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
            ]
        else:
            candidates += [
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------
    def update(self):
        now = time.time()
        has_data = bool(self.live_games) or self.fallback_game is not None
        interval = self.live_update_interval if self.live_games else self.update_interval

        if has_data and (now - self.last_fetch_time < interval):
            return

        self.last_fetch_time = now

        if self.test_mode:
            game = self._fake_game()
            self._resolve_logos(game)
            self.live_games = [game]
            self.fallback_game = None
            if self.current_index >= len(self.live_games):
                self.current_index = 0
            return

        try:
            resp = self.session.get(ESPN_SCOREBOARD_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.logger.error(f"Failed to fetch MLB scoreboard: {e}", exc_info=True)
            return

        live_games, fallback_game = self._process_scoreboard(data)

        for g in live_games:
            self._resolve_logos(g)
        if fallback_game:
            self._resolve_logos(fallback_game)

        self.live_games = live_games
        self.fallback_game = fallback_game
        if self.current_index >= len(self.live_games):
            self.current_index = 0

    def _process_scoreboard(self, data: Dict[str, Any]):
        """Returns (live_games, fallback_game). live_games is every
        currently in-progress game leaguewide (or just favorite teams'
        if show_favorite_teams_only is set). fallback_game is your
        favorite team's most relevant scheduled/recent game, used only
        when nothing is live."""
        events = data.get("events", [])
        live_games = []

        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            state = comp.get("status", {}).get("type", {}).get("state")
            if state != "in":
                continue
            game = self._parse_game(event, comp)
            if self.show_favorite_teams_only:
                if game["away_abbr"] in self.favorite_teams or game["home_abbr"] in self.favorite_teams:
                    live_games.append(game)
            else:
                live_games.append(game)

        fallback_game = None
        if not live_games:
            fallback_game = self._find_favorite_game(data)

        return live_games, fallback_game

    def _find_favorite_game(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        events = data.get("events", [])
        candidates = []
        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            abbrevs = [c.get("team", {}).get("abbreviation", "").upper() for c in competitors]
            if any(fav in abbrevs for fav in self.favorite_teams):
                candidates.append((event, comp))

        if not candidates:
            return None

        event, comp = candidates[0]
        return self._parse_game(event, comp)

    def _parse_game(self, event: Dict[str, Any], comp: Dict[str, Any]) -> Dict[str, Any]:
        competitors = comp.get("competitors", [])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[0])
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[-1])

        situation = comp.get("situation", {}) or {}
        status = comp.get("status", {}) or {}
        status_type = status.get("type", {}) or {}

        def team_color(competitor):
            color = competitor.get("team", {}).get("color")
            if self.use_team_colors and color:
                try:
                    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
                except Exception:
                    pass
            return None

        def team_logo_url(competitor):
            team = competitor.get("team", {})
            if team.get("logo"):
                return team["logo"]
            logos = team.get("logos") or []
            if logos:
                return logos[0].get("href")
            return None

        return {
            "state": status_type.get("state", "pre"),
            "away_abbr": away.get("team", {}).get("abbreviation", "AWY")[:3].upper(),
            "home_abbr": home.get("team", {}).get("abbreviation", "HOM")[:3].upper(),
            "away_score": int(away.get("score", 0) or 0),
            "home_score": int(home.get("score", 0) or 0),
            "away_color": team_color(away) or self.away_color_fallback,
            "home_color": team_color(home) or self.home_color_fallback,
            "away_logo_url": team_logo_url(away),
            "home_logo_url": team_logo_url(home),
            "away_logo": None,
            "home_logo": None,
            "inning": status.get("period", 1),
            "inning_half": situation.get("isTopInning", True),
            "balls": situation.get("balls", 0),
            "strikes": situation.get("strikes", 0),
            "outs": situation.get("outs", 0),
            "on_first": bool(situation.get("onFirst")),
            "on_second": bool(situation.get("onSecond")),
            "on_third": bool(situation.get("onThird")),
        }

    def _fake_game(self) -> Dict[str, Any]:
        return {
            "state": "in",
            "away_abbr": "ATH",
            "home_abbr": "DET",
            "away_score": 3,
            "home_score": 2,
            "away_color": self.away_color_fallback,
            "home_color": self.home_color_fallback,
            "away_logo_url": None,
            "home_logo_url": None,
            "away_logo": None,
            "home_logo": None,
            "inning": 3,
            "inning_half": True,
            "balls": 2,
            "strikes": 1,
            "outs": 1,
            "on_first": True,
            "on_second": False,
            "on_third": True,
        }

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------
    def _maybe_rotate(self):
        if len(self.live_games) <= 1:
            return
        now = time.time()
        if now - self.last_switch_time >= self.game_rotation_seconds:
            self.current_index = (self.current_index + 1) % len(self.live_games)
            self.last_switch_time = now

    def _current_game(self) -> Optional[Dict[str, Any]]:
        if self.live_games:
            return self.live_games[self.current_index]
        return self.fallback_game

    # ------------------------------------------------------------------
    # Logos
    # ------------------------------------------------------------------
    def _resolve_logos(self, game: Dict[str, Any], size: Optional[int] = None):
        if not self.show_logos:
            return
        if size is None:
            _, height = self._get_dimensions()
            size = max(height - 12, 10)  # leave room for the text row below
        game["away_logo"] = self._get_team_logo(game["away_abbr"], game.get("away_logo_url"), size)
        game["home_logo"] = self._get_team_logo(game["home_abbr"], game.get("home_logo_url"), size)

    def _get_team_logo(self, abbr: str, url: Optional[str], size: int) -> Optional[Image.Image]:
        cache_key = f"{abbr}_{size}"
        if cache_key in self._logo_cache:
            return self._logo_cache[cache_key]

        logo = self._load_local_logo(abbr, size)

        if logo is None and url:
            try:
                resp = self.session.get(url, timeout=8)
                resp.raise_for_status()
                raw = Image.open(BytesIO(resp.content)).convert("RGBA")
                raw.thumbnail((size, size), Image.LANCZOS)
                logo = raw
            except Exception as e:
                self.logger.warning(f"Could not download logo for {abbr}: {e}")

        if logo is None:
            self.logger.info(f"No logo found for {abbr}; showing abbreviation only.")

        self._logo_cache[cache_key] = logo
        return logo

    def _load_local_logo(self, abbr: str, size: int) -> Optional[Image.Image]:
        candidates = [f"{abbr}.png", f"{abbr.lower()}.png", f"{abbr}.PNG"]
        for name in candidates:
            path = os.path.join(self.logo_dir, name)
            if os.path.isfile(path):
                try:
                    raw = Image.open(path).convert("RGBA")
                    raw.thumbnail((size, size), Image.LANCZOS)
                    return raw
                except Exception as e:
                    self.logger.warning(f"Found {path} but couldn't load it: {e}")
        return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def display(self, force_clear: bool = False):
        self._maybe_rotate()

        width, height = self._get_dimensions()
        image = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)

        game = self._current_game()
        if game is None:
            draw.text((4, height // 2 - 4), "No Game", font=self.font_small, fill=(180, 180, 180))
            self._push_image(image, force_clear)
            return

        left_w = width // 2
        col_w = left_w // 2

        # --- Left half: two team columns side by side, full height ---
        draw.rectangle([0, 0, col_w - 1, height - 1], fill=game["away_color"])
        draw.rectangle([col_w, 0, left_w - 1, height - 1], fill=game["home_color"])

        away_txt_color = self._text_color_for(game["away_color"])
        home_txt_color = self._text_color_for(game["home_color"])

        self._draw_team_column(image, draw, 0, 0, col_w, height,
                                game["away_abbr"], game["away_score"], game.get("away_logo"), away_txt_color)
        self._draw_team_column(image, draw, col_w, 0, left_w - col_w, height,
                                game["home_abbr"], game["home_score"], game.get("home_logo"), home_txt_color)

        # --- Right half (black): inning upper-left, diamond centered,
        #     count lower-left, outs lower-right ---
        right_x0 = left_w + 2
        right_w = width - right_x0 - 1

        self._draw_inning(draw, right_x0 + 1, 1, game)

        diamond_w = int(right_w * 0.5)
        diamond_h = int(height * 0.62)
        diamond_x = right_x0 + (right_w - diamond_w) // 2
        diamond_y = (height - diamond_h) // 2 - 2
        self._draw_diamond(draw, diamond_x, diamond_y, diamond_w, diamond_h, game, scale=0.78)

        lower_y = height - 8
        self._draw_count(draw, right_x0 + 1, lower_y, game)
        self._draw_outs(draw, right_x0, lower_y, right_w, game)

        self._push_image(image, force_clear)

    def _get_dimensions(self) -> Tuple[int, int]:
        dm = self.display_manager
        for attr_pair in (("width", "height"), ("matrix_width", "matrix_height")):
            w = getattr(dm, attr_pair[0], None)
            h = getattr(dm, attr_pair[1], None)
            if w and h:
                return int(w), int(h)
        matrix = getattr(dm, "matrix", None)
        if matrix is not None:
            w = getattr(matrix, "width", None)
            h = getattr(matrix, "height", None)
            if w and h:
                return int(w), int(h)
        return 128, 32

    def _push_image(self, image: Image.Image, force_clear: bool):
        dm = self.display_manager
        if hasattr(dm, "image") and hasattr(dm, "update_display"):
            dm.image.paste(image, (0, 0))
            dm.update_display()
            return
        if hasattr(dm, "set_image"):
            dm.set_image(image)
            return
        raise AttributeError(
            "display_manager has neither `.image`/`update_display()` nor "
            "`.set_image()`. Check your LEDMatrix DisplayManager API and "
            "adjust TidbytBaseballPlugin._push_image() to match."
        )

    @staticmethod
    def _text_color_for(bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
        luminance = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        return (0, 0, 0) if luminance > 150 else (255, 255, 255)

    def _draw_bold_text(self, draw, xy, text, font, fill):
        """Faux-bold: draws the text at a couple of 1px offsets to
        thicken the strokes. More reliable than depending on a bold TTF
        file actually being present/embedded at runtime."""
        x, y = xy
        for dx, dy in ((0, 0), (1, 0), (0, 1)):
            draw.text((x + dx, y + dy), text, font=font, fill=fill)

    def _draw_team_column(self, image, draw, x0, y0, w, h, abbr, score, logo, text_color):
        """Logo on top (as large as the column allows), bold
        'ABBR SCORE' text on a single line underneath."""
        text_line = f"{abbr} {score}"
        line_bbox = draw.textbbox((0, 0), text_line, font=self.font_team)
        line_h = line_bbox[3] - line_bbox[1]
        line_w = line_bbox[2] - line_bbox[0]

        logo_area_h = h - line_h - 4

        if logo is not None:
            logo_x = x0 + (w - logo.width) // 2
            logo_y = y0 + max((logo_area_h - logo.height) // 2, 0) + 1
            image.paste(logo, (logo_x, logo_y), logo)

        tx = x0 + max((w - line_w) // 2, 0)
        tx = min(tx, x0 + w - line_w) if line_w < w else x0
        ty = y0 + h - line_h - 2 - line_bbox[1]
        self._draw_bold_text(draw, (tx, ty), text_line, self.font_team, text_color)

    def _draw_diamond(self, draw, x, y, w, h, game, scale=0.78):
        """Draws 3 diamonds (2nd top-center, 3rd/1st below on either side)
        lit up in base_fill_color when occupied, outlined in
        base_empty_color when empty."""
        cx = x + w // 2
        size = int(min(w // 2, h) * scale)
        half = max(size // 2, 3)

        top_y = y + half + 1
        bottom_y = top_y + half + 2

        positions = {
            "second": (cx, top_y),
            "third": (cx - half - 3, bottom_y),
            "first": (cx + half + 3, bottom_y),
        }
        occupied = {
            "first": game["on_first"],
            "second": game["on_second"],
            "third": game["on_third"],
        }

        for base, (px, py) in positions.items():
            pts = [
                (px, py - half),
                (px + half, py),
                (px, py + half),
                (px - half, py),
            ]
            if occupied[base]:
                draw.polygon(pts, fill=self.base_fill_color)
            else:
                draw.polygon(pts, outline=self.base_empty_color)

    def _draw_inning(self, draw, x, y, game):
        """Solid triangle pointing up (top of inning) or down (bottom),
        drawn as a polygon rather than a unicode arrow glyph -- unicode
        arrows (▲▼) render as a blank/empty box (tofu) on many bitmap
        or embedded fonts, which is why this showed up broken before."""
        tri_size = 7
        if game["inning_half"]:
            pts = [(x, y + tri_size), (x + tri_size // 2, y), (x + tri_size, y + tri_size)]
        else:
            pts = [(x, y), (x + tri_size, y), (x + tri_size // 2, y + tri_size)]
        draw.polygon(pts, fill=(255, 255, 255))
        draw.text((x + tri_size + 2, y - 1), str(game["inning"]), font=self.font_small, fill=(255, 255, 255))

    def _draw_count(self, draw, x, y, game):
        count_text = f"{game['balls']}-{game['strikes']}"
        draw.text((x, y), count_text, font=self.font_tiny, fill=(255, 200, 0))

    def _draw_outs(self, draw, x, y, w, game):
        square = 3
        gap = 2
        edge_margin = 3
        base_x = x + w - edge_margin - (square + gap) * 3 + gap
        for i in range(3):
            sx = base_x + i * (square + gap)
            box = [sx, y + 1, sx + square, y + 1 + square]
            if i < game["outs"]:
                draw.rectangle(box, fill=self.out_fill_color)
            else:
                draw.rectangle(box, outline=self.out_empty_color)
