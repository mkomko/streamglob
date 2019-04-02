import logging
logger = logging.getLogger(__name__)

import abc

import urwid
from panwid import *
from orderedattrdict import AttrDict
from datetime import datetime, timedelta
import dateutil.parser
import pytz
import distutils.spawn
import dataclasses
from dataclasses import *
import typing
import itertools
import requests
import html2text
import webbrowser

from .. import player
from .. import config
from .. import model
from .. import utils

from .base import *
from .filters import *
from ..player import *
from .widgets import *


LINE_STYLES = {
    "standard": {"height": 4, "boxed": True},
    "compact": {"height": 3, "boxed": False},
}

class BAMLineScoreBox(urwid.WidgetWrap):

    def __init__(self, table, style=None):

        self.table = w = table
        self.style = style or "standard"
        if not self.style in ["standard", "boxed", "compact"]:
            raise Exception(f"line style {style} not invalid")

        if LINE_STYLES[self.style]["boxed"]:
            w = urwid.LineBox(
                self.table,
                tlcorner="", tline="", trcorner="",
                blcorner="├", brcorner="┤"
            )

        self.box = urwid.BoxAdapter(w, LINE_STYLES[self.style]["height"])
        super().__init__(self.box)

    @property
    def min_width(self):
        # FIXME: this should just be +2 for the LineBox, but something causes
        # the last column header to clip
        return self.table.min_width + 3
        # return self._width

    @property
    def height(self):
        return self._height

    def selectable(self):
        return True

    # def keypress(self, size, key):
    #     return super().keypress(size, key)



class BAMLineScoreDataTable(DataTable):

    sort_icons = False
    no_clip_header = True

    OVERTIME_LABEL = None

    @classmethod
    def for_game(cls, provider, game, hide_spoilers=False):

        # style = style or "standard"

        # if not style in ["standard", "boxed", "compact"]:
        #     raise Exception(f"line style {style} not invalid")

        line_score = game.get("linescore", None)
        away_team = game["teams"]["away"]["team"]["abbreviation"]
        home_team = game["teams"]["home"]["team"]["abbreviation"]

        primary_scoring_attr = cls.SCORING_ATTRS[0]
        # code = game["status"]["statusCode"]
        status = game["status"]["detailedState"]
        if hide_spoilers:
            status = "?"
        elif status == "Scheduled":
            start_time = dateutil.parser.parse(game["gameDate"]).astimezone(
                pytz.timezone(config.settings.profile.time_zone)
            ).strftime("%I:%M%p").lower()
            status = start_time[1:] if start_time.startswith("0") else start_time
        elif status == "In Progress" and line_score:
            status = cls.PLAYING_PERIOD_DESC(line_score)

        columns = [
            DataTableColumn("team", width=min(12, max(10, len(status))), min_width=10, label=status, align="right"),
            DataTableDivider("\N{BOX DRAWINGS DOUBLE VERTICAL}", in_header=False),
        ]

        if not line_score:
            line_score = {
                "innings": [],
                "teams": {
                    "away": {},
                    "home": {}
                }
            }

        if "teams" in line_score:
            tk = line_score["teams"]
        else:
            tk = line_score

        data = []
        for s, side in enumerate(["away", "home"]):

            i = -1
            line = AttrDict()
            team = away_team if s == 0 else home_team
            attr = provider.team_color_attr(team.lower(),
                                            provider.config.listings.line.colors)
            line.team = urwid.Padding(
                urwid.Text((attr, f"{team:>3s}"), align="right"),
                width=3,
                align="right"
            )

            if isinstance(line_score[cls.PLAYING_PERIOD_ATTR], list):

                for i, playing_period in enumerate(line_score[cls.PLAYING_PERIOD_ATTR]):
                    # continue
                    if hide_spoilers and i == cls.NUM_PLAYING_PERIODS:
                        break

                    if not s:
                        columns.append(
                            DataTableColumn(
                                str(i+1),
                                label=(
                                    str(i+1)
                                    if (
                                            cls.OVERTIME_LABEL is None
                                            or i < cls.NUM_PLAYING_PERIODS
                                    )
                                    else cls.OVERTIME_LABEL
                                ),
                                width=3,
                                align="right"
                            )
                        )

                    if hide_spoilers:
                        setattr(line, str(i+1), urwid.Text(("dim", "?")))
                    elif side in playing_period:
                        if isinstance(playing_period[side], dict) and primary_scoring_attr in playing_period[side]:
                            setattr(line, str(i+1), parse_int(playing_period[side][primary_scoring_attr]))
                        else:
                            setattr(line, str(i+1), "0")
                    else:
                        setattr(line, str(i+1), "X")

                for n in range(i+1, cls.NUM_PLAYING_PERIODS):
                    if not s:
                        columns.append(
                            DataTableColumn(str(n+1), label=str(n+1), width=3, align="right")
                        )
                    if hide_spoilers:
                        setattr(line, str(n+1), urwid.Text(("dim", "?")))
                    # else:
                    #     setattr(line, str(n+1), "x")
            if not s:
                columns.append(DataTableDivider("\N{BOX DRAWINGS LIGHT VERTICAL}", in_header=False))

            for stat in cls.SCORING_ATTRS:
                if not s:
                    columns.append(
                        DataTableColumn(stat, label=stat[0].upper(), width=3, align="right")
                    )
                if not stat in tk[side]:# and len(data) > 0:
                    # setattr(line, stat, "")
                    continue
                if not hide_spoilers:
                    setattr(line, stat, parse_int(tk[side][stat]))
                else:
                    setattr(line, stat, urwid.Text(("dim", "?")))

            data.append(line)

        if not hide_spoilers and len(data):

            for s, side in enumerate(["away", "home"]):
                for i, playing_period in enumerate(line_score[cls.PLAYING_PERIOD_ATTR]):
                    if str(i+1) in data[s] and data[s][str(i+1)] == 0:
                        data[s][str(i+1)] = urwid.Text(("dim", str(data[s][str(i+1)])))

            if None not in [ data[i].get(primary_scoring_attr) for i in range(2) ]:
                if data[0][primary_scoring_attr] > data[1][primary_scoring_attr]:
                    data[0][primary_scoring_attr] = urwid.Text(("bold", str(data[0][primary_scoring_attr])))
                elif data[1][primary_scoring_attr] > data[0][primary_scoring_attr]:
                    data[1][primary_scoring_attr] = urwid.Text(("bold", str(data[1][primary_scoring_attr])))

        return cls(columns, data)


class HighlightsDataTable(Observable, DataTable):

    with_scrollbar = True
    with_header = False
    ui_sort = False
    sort_icons = False

    empty_message = "(no highlights available)"
    detail_hanging_indent = "title"
    divider = "\N{BOX DRAWINGS LIGHT VERTICAL}"

    COLUMNS = [
        # DataTableColumn("duration", width=10),
        DataTableColumn(
            "type",
            pack=True,
            value = lambda t, r: f"{r.data.attrs.get('event_type') or ' '}"
        ),
        DataTableColumn(
            "title",
            pack=True,
            value = lambda t, r: ("title", f"{r.data.title} ({r.data.duration})")
        ),
        # DataTableColumn("description"),
        # DataTableColumn("url", hide=True),
    ]

    def detail_fn(self, data):
        return urwid.Columns([
            # (4, urwid.Text("")),
            ("weight", 1, urwid.Text(f"{(data.get('description'))}"))
        ])

    def keypress(self, size, key):
        if key == "enter":
            self.notify("play", self.selection.data)
        else:
            return super().keypress(size, key)


class ExpandableAnchorIcon(urwid.WidgetWrap):

    ICON_CLOSED = "\N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
    ICON_OPEN = "\N{BLACK DOWN-POINTING SMALL TRIANGLE}"

    def __init__(self, icon_closed=None, icon_open=None):

        self.icon_closed = icon_closed or self.ICON_CLOSED
        self.icon_open = icon_open or self.ICON_OPEN

        self.icon = urwid.SelectableIcon(self.icon_closed)
        # self.icon.selectable = lambda: True
        super().__init__(self.icon)

    def open(self):
        self.icon.set_text(self.icon_open)

    def close(self):
        self.icon.set_text(self.icon_closed)

    # def selectable(self):
    #     return True

class ExpandableAnchor(urwid.WidgetWrap):

    def __init__(self, anchor, contents):

        self.anchor = anchor
        self.contents = contents
        self._contents_showing = False

        self.icon = ExpandableAnchorIcon()
        self.columns = urwid.Columns([
            ("pack", self.icon),
            ("pack", urwid.Text( ("anchor", self.anchor) ))
        ], dividechars=1)

        self.pile = urwid.Pile([
            ("pack", self.columns)
        ])

        self.attr = urwid.AttrMap(
            self.pile,
            attr_map = {},
            focus_map = {
                None: "highlight",
                "anchor": "highlight"
            },
        )
        super().__init__(self.attr)

    def show_contents(self):

        if self._contents_showing: return

        self.pile.contents +=[
            (urwid.Divider("-"), self.pile.options("pack")),
            (self.contents, self.pile.options("pack"))
        ]
        self.pile.focus_position = 2
        self._contents_showing = True
        self.icon.open()

    def hide_contents(self):

        if not self._contents_showing: return
        self.pile.focus_position = 0
        del self.pile.contents[1:]
        self._contents_showing = False
        self.icon.close()

    def toggle_contents(self):
        if not self._contents_showing:
            self.show_contents()
        else:
            self.hide_contents()

    def selectable(self):
        return True

    def keypress(self, size, key):
        key = super().keypress(size, key)
        if key == "enter":
            self.toggle_contents()
        elif key == "right":
            self.show_contents()
        elif key in ["left", "esc", "q"] and self._contents_showing:
            self.hide_contents()
        else:
            return key#super().keypress(size, key)


class BAMArticleBody(BasePopUp):

    def __init__(self, body):
        super().__init__(
            urwid.LineBox(
                ScrollBar(Scrollable(urwid.Text(body)))
                # panwid.listbox.ScrollingListBox([urwid.Text(l) for l in body])
            )
        )


# Strip off leading zeroes if they represent hours.  Because MLB expresses
# duration as hh:mm:ss while NHL expresses it as mm:ss, it's a little clumsy.
DURATION_RE = re.compile("(?:(?:0+:)(?=\d+:\d+))?(.*)")

@dataclass
class BAMEditorial:

    HEADLINE_SEPARATOR = " \N{EM DASH} "
    editorial_type: str
    headline: str
    subhead: str = None
    blurb: str = None
    body: str = None
    url: str = None

    @property
    def full_headline(self):
        headline = self.headline
        if self.subhead:
            headline = f"{headline}{self.HEADLINE_SEPARATOR}{self.subhead}"
        return headline

def get_playback_url(playbacks):
    # FIXME: make configurable
    for name in [
            "hlsCloud",
            "HTTP_CLOUD_WIRED_60",
            "mp4Avc",
            "FLASH_1200K_640X360",
            "FLASH_300K_320X180"
    ]:
        try:
            return next(
                p["url"] for p in playbacks
                if p["name"] == name
            )
        except StopIteration:
            # give up and return the first one
            return next(
                p["url"] for p in playbacks
            )

class BAMDetailBox(Observable, urwid.WidgetWrap):

    def __init__(self, provider, listing):

        self.provider = provider
        self.listing = listing
        self.game = self.listing.game_data

        # FIXME: add option to reveal spoilers
        try:
            highlights = sorted([
                AttrDict(dict(
                    media_id = h.get("guid", h.get("id")),
                    title = h["title"],
                    description = h["description"],
                    duration = DURATION_RE.search(h["duration"]).groups()[0],
                    url = get_playback_url(h["playbacks"]),
                    attrs = self.get_highlight_attrs(h, self.listing),
                    _details = {"open": True, "disabled": True}
                )) for h in self.game["content"]["highlights"][self.HIGHLIGHT_ATTR]["items"]
            ], key = lambda h: (h.attrs.timestamp is None, h.attrs.timestamp, h.attrs.event_type == "Other"))
        except KeyError:
            highlights = []
        except StopIteration:
            raise Exception(self.game.get("gamePk"))

        self.preview = self.get_editorial("preview")
        self.recap = self.get_editorial("recap")

        if self.recap and not self.listing.hide_spoilers:
            self.editorial = self.recap
        elif self.preview:
            self.editorial = self.preview
        else:
            self.editorial = None

        if not self.listing.hide_spoilers:
            self.highlights = highlights
        else:
            self.highlights = []

        self.pile = urwid.Pile([])

        if not (self.editorial or len(self.highlights)):
            # super().__init__(urwid.Text(""))
            if (self.listing.hide_spoilers
                and self.editorial and self.editorial == self.recap):
                message = "[spoilers hidden]"
            else:
                message = "[no content]"
            self.pile.contents.append(
                (urwid.Text(message), self.pile.options("pack"))
            )
            super().__init__(self.pile)
            return

        if self.editorial:

            self.editorial_blurb_pile = urwid.Pile([
                    ("pack", urwid.Text(self.editorial.blurb)),
            ])

            self.editorial_blurb_attr = urwid.AttrMap(
                self.editorial_blurb_pile,
                attr_map = {
                    None: "table_row_body focused",
                    "bold": "bold focused",
                    "link": "link focused"
                }
            )

            if self.editorial.body:

                blurb_more_button = SquareButton(("bold", "more"))

                body_markup = utils.html_to_urwid_text_markup(
                    self.editorial.body,
                    excludes = [lambda item: item[0] == "link" and item[1][0].startswith("Video:")]

                )

                def open_body_popup(b):

                    self.provider.view.open_popup(
                        BAMArticleBody(body_markup),
                        width=("relative", 80),
                        height=("relative", 80),
                    )

                urwid.connect_signal(blurb_more_button, "click", open_body_popup)

            elif self.editorial.url:
                blurb_more_button = SquareButton(("bold", "open"))

                def open_body_url(b, u):
                    logger.info(u)
                    webbrowser.open(u)

                urwid.connect_signal(
                    blurb_more_button, "click", open_body_url,
                    f"{self.provider.URL_ROOT}/{self.editorial.url}"
                )
            else:
                blurb_more_button = None

            if blurb_more_button:

                blurb_more_attr = urwid.AttrMap(
                    blurb_more_button,
                    attr_map = {},
                    focus_map = {
                        "bold": "highlight"
                    }
                )

                self.editorial_blurb_pile.contents.append(
                    (urwid.Columns([
                        ("pack", blurb_more_attr),
                        ("weight", 1, urwid.Text(" "))
                    ]), self.pile.options("pack"))
                )
                self.editorial_blurb_pile.focus_position = 1

            self.editorial_anchor = ExpandableAnchor(
                f"{self.editorial.editorial_type.title()}: "
                f"{self.editorial.full_headline}",
                self.editorial_blurb_attr
            )
            self.pile.contents.append(
                (self.editorial_anchor, self.pile.options("pack"))
            )

        if len(self.highlights):

            self.table = self.HIGHLIGHT_TABLE_CLASS(
                data = self.highlights
            )

            self.table_attr = urwid.AttrMap(
                self.table,
                attr_map = {},
                focus_map = {
                    None: "highlight",
                    "table_row_body focused": "highlight",
                    "title focused": "highlight"
                },
            )

            def play(h): self.notify("play", h)

            self.table.connect("play", play)
            self.table_box = urwid.BoxAdapter(
                self.table_attr, min(2*len(self.highlights), 10) + 1
            )

            self.table_anchor = ExpandableAnchor(
                "Highlights",
                self.table_box
            )

            self.pile.contents.append(
                (self.table_anchor, self.pile.options("pack"))
            )

        self.pile.focus_position = 0
        super().__init__(self.pile)

    def close_all(self):
        for c in self.pile.contents:
            if isinstance(c[0], ExpandableAnchor):
                c[0].hide_contents()

    def get_editorial_item(self, editorial):
        raise NotImplementedError

    def get_editorial(self, editorial_type):
        try:
            item = self.get_editorial_item(
                self.game["content"]["editorial"][editorial_type]
            )
            return BAMEditorial(
                editorial_type,
                item.get("headline", None),
                item.get("subhead", None),
                item.get("blurb", item.get("seoDescription", None)),
                item.get("body", None),
                item.get("url", None)
            )
        except (AttributeError, KeyError, IndexError):
            return None

    @property
    def HIGHLIGHT_ATTR(self):
        raise NotImplementedError

    def get_highlight_attrs(self, highlight):
        raise NotImplementedError

    def selectable(self):
        return True

    def keypress(self, size, key):
        key = super().keypress(size, key)
        if key in ["left", "escape", "q"]:
            self.close_all()
            return key
        else:
            return key


def get_team_city_and_name(team):
    # They don't make this very easy...
    try:
        city = team["locationName"]
    except:
        return (None, team.get("teamName", "?"))
    name = team["teamName"]
    if team["teamName"] in team["name"]:
        city = team["name"].replace(name, "").strip()
    elif team["shortName"] in team["name"]:
        name = team["name"].replace(team["shortName"], "").strip()
        city = team["name"].replace(name, "").strip()
    return (city, name)

@dataclass
class BAMMediaListing(model.MediaListing):

    FEED_TYPE_ORDER = [
        "away",
        "in_market_away",
        "home",
        "in_market_home",
        "national",
        "multi-cam",
        "condensed",
        "recap",
        "..."
    ]

    game_id: int = None
    game_type: str = None
    away_team: int = None
    home_team: int = None
    away_city: str = None
    home_city: str = None
    away_abbrev: str = None
    home_abbrev: str = None
    away_record: tuple = None
    home_record: tuple = None
    start: datetime = None
    venue: str = None
    # attrs: str = None

    @property
    def style(self):
        return self.provider.config.listings.line.style or "standard"

    @classmethod
    def from_json(cls, provider, g):
        game_pk = g["gamePk"]
        game_type = g["gameType"]
        status = g["status"]["statusCode"]
        away_city_name = get_team_city_and_name(g["teams"]["away"]["team"])
        (away_city, away_team) = away_city_name
        # away_team = g["teams"]["away"]["team"]["teamName"]
        # away_city = g["teams"]["away"]["team"]["name"].replace(away_team, "").strip()
        home_city_name = get_team_city_and_name(g["teams"]["home"]["team"])
        # home_team = g["teams"]["home"]["team"]["teamName"]
        # home_city = g["teams"]["home"]["team"]["name"].replace(home_team, "").strip()
        (home_city, home_team) = home_city_name

        away_abbrev = g["teams"]["away"]["team"]["abbreviation"].lower()
        home_abbrev = g["teams"]["home"]["team"]["abbreviation"].lower()
        away_record = tuple(
            [g["teams"]["away"]["leagueRecord"][x]
             for x in ["wins", "losses"]]
        )
        home_record = tuple(
            [g["teams"]["home"]["leagueRecord"][x]
             for x in ["wins", "losses"]]
        )
        start_time = dateutil.parser.parse(g["gameDate"])
        venue = g["venue"]["name"]

        if config.settings.profile.time_zone:
            start_time = start_time.astimezone(
                pytz.timezone(config.settings.profile.time_zone)
            )

        return cls(
            provider_id = provider,
            game_id = game_pk,
            game_type = game_type,
            away_team = away_team,
            home_team = home_team,
            away_abbrev = away_abbrev,
            home_abbrev = home_abbrev,
            away_city = away_city,
            home_city = home_city,
            away_record = away_record,
            home_record = home_record,
            start = start_time,
            venue = venue
            # attrs = attrs,
        )


    def team_box(self, side):

        wins = getattr(self, f"{side}_record")[0]
        losses = getattr(self, f"{side}_record")[1]
        pct = wins/(wins+losses) if (wins+losses) else 0.0

        attrcfg = self.provider.config.listings.teams.colors
        if isinstance(attrcfg, list):
            if len(attrcfg) > 2:
                attr1, attr2, attr3 = attrcfg
            else:
                attr1, attr2 = attrcfg
                att3 = attr2
        else:
            attr1 = attr2 = attr3 = attrcfg

        attr1 = self.provider.team_color_attr(getattr(self, f"{side}_abbrev"),
                                              self.provider.config.listings.colors,
                                              style=attr1)

        attr2 = self.provider.team_color_attr(getattr(self, f"{side}_abbrev"),
                                              self.provider.config.listings.colors,
                                              style=attr2)

        attr3 = self.provider.team_color_attr(getattr(self, f"{side}_abbrev"),
                                              self.provider.config.listings.colors,
                                              style=attr3)

        record_text = (
            f"({wins}-{losses}, {('%.3f' %(pct)).lstrip('0')})"
            if not self.hide_spoilers
            else " "
        )

        pile = urwid.Pile([
            ( "pack", urwid.Padding(
                urwid.Text( ((attr3), getattr(self, f"{side}_team"))),
                width="pack", align="center"),
            ),
            ("pack", urwid.Padding(
                urwid.Text(
                    record_text),
                width="pack", align="center")
            )
        ])

        if getattr(self, f"{side}_city"):
            pile.contents.insert(
                0,
                (urwid.Padding(
                    urwid.Text(((attr2), getattr(self, f"{side}_city"))),
                    width="pack", align="center"
                ), pile.options("pack"))
            )
        else:
            pile.contents.insert(
                0,
                (urwid.Padding(
                    urwid.Text(" ")
                ), pile.options("pack"))
            )

        return urwid.BoxAdapter(urwid.Filler(urwid.AttrMap(
            pile,
            {None: attr1}
        ), valign="middle"), LINE_STYLES[self.style]["height"])


    @property
    def away_team_box(self):
        return self.team_box("away")

    @property
    def home_team_box(self):
        return self.team_box("home")

    @property
    def hide_spoilers(self):
        if not self.provider.filters.hide_spoilers.value:
            return False
        hide_spoiler_teams = self.provider.config.teams.get("hide_spoilers", [])
        if isinstance(hide_spoiler_teams, bool):
            return hide_spoiler_teams

        if hide_spoiler_teams == "favorite":
            hide_spoiler_teams = self.provider.config.teams.get("favorite", [])

        return len(set(
            [self.away_abbrev, self.home_abbrev]).intersection(
                set(hide_spoiler_teams)
            )) > 0

    @property
    def is_favorite(self):
        favorites = self.provider.config.teams.get("favorite", [])
        return len(
            set(favorites).intersection(
                set([self.away_abbrev, self.home_abbrev])
            )
        ) > 0

    @property
    def state(self):
        return self.game_data["status"]["detailedState"]

    @property
    def media_types(self):
        return set([
            m.media_type
            for m in self.media
        ])

    @property
    def has_video(self):
        return "video" in self.media_types

    @property
    def has_audio(self):
        return "audio" in self.media_types

    @property
    def attrs(self):
        return "".join([
            f"{'V' if self.has_video else ' '}",
            f"{'a' if self.has_audio else ' '}",
            f"{'!' if self.is_free else '$'}",
        ])

    @property
    def media_available(self):
        # return "".join(["!" if self.is_free else "$"] + [
        #     m.stream_indicator or "" for m in self.media
        # ])

        # return urwid.Columns([
        #     (2, m.stream_indicator or urwid.Text(" "))
        #     for m in self.media
        # ])
        items = [ (k, list(x[1] for x in g))
                  for k, g in itertools.groupby(
                          (m.stream_indicator
                           for m in self.media
                           if m.stream_indicator),
                          lambda v: v[0]
                  ) ]
        return urwid.Pile([
            ("pack", urwid.Text(" " ))
            ] + [
            ("pack",
             urwid.Columns( [
                 (2, urwid.Padding(urwid.Text(("bold", media_type)), right=1))
             ] + [ ("pack", urwid.Text("!" if self.is_free else"$"))] + [
                 ("pack", urwid.Text(f))
                 for f in feed_types
             ])
            )
            for media_type, feed_types in items
        ])


    @property
    def is_free(self):
        return any([
            m.free for m in self.media
        ])

    @property
    def _details(self):
        return {"open": True, "disabled": True}

    # FIXME
    # @property
    # def provider(self):
    #     return self.provider.NAME.lower()

    @property
    def start_date(self):
        return self.start.strftime("%Y-%m-%d")

    @property
    def start_time(self):
        return self.start.strftime("%I%M%p").lower()

    @property
    def start_date_time(self):
        return f"{self.start_date}_{self.start_time}"

    @property
    def ext(self):
        return "mp4"

    @property
    def title(self):
        return f"{self.away_abbrev}@{self.home_abbrev}"

    @property
    def game_data(self):
        return self.provider.game_data(self.game_id)

        # schedule = self.provider.schedule(game_id=self.game_id)
        # try:
        #     # Get last date for games that have been rescheduled to a later date
        #     game = schedule["dates"][-1]["games"][0]
        # except KeyError:
        #     logger.warn("no game data for %s" %(self.game_id))
        # # logger.info(f"game: {game}")
        # return game

    @property
    @memo(region="medium")
    def game_feed_data(self):
        return requests.get(
            self.provider.GAME_DATA_TEMPLATE.format(
                game_id=self.game_id
            )
        ).json()

    @property
    def plays(self):
        return (p for p in self.game_feed_data["liveData"]["plays"]["allPlays"])


    @property
    @memo(region="short")
    def media(self):

        def fix_feed_type(feed_type, epg_title, title, description, blurb):
            # logger.info(f"{feed_type}, {epg_title}, {title}, {description}")
            # MLB-specific -- mediaSubType is sometimes a team ID instead
            # of away/home
            if feed_type and feed_type.isdigit():
                if int(feed_type) == game["teams"]["away"]["team"]["id"]:
                    feed_type = "away"
                elif int(feed_type) == game["teams"]["home"]["team"]["id"]:
                    return "home"
            elif feed_type and feed_type.lower() == "composite":
                feed_type = "multi-cam"
            elif "Recap" in epg_title:
                feed_type = "recap"
            elif "Highlights" in epg_title:
                if ("CG" in title
                    or "Condensed" in description
                    or "Condensed" in blurb):
                    feed_type = "condensed"

            if feed_type is None:
                return title or "..."

            return feed_type.title()


        logger.debug(f"geting media for game {self.game_id}")

        game = self.game_data

        try:
            epgs = (game["content"]["media"]["epg"]
                    + game["content"]["media"].get("epgAlternate", []))
        except KeyError:
            return []
            # raise SGStreamNotFound("no matching media for game %d" %(self.game_id))

        # raise Exception(self.game_id, epgs)

        if not isinstance(epgs, list):
            epgs = [epgs]

        items = sorted(
            [ self.provider.new_media_source(
                # mediaId and guid fields are both used to identify streams
                # provider_id = self.provider_id,
                game_id = self.game_id,
                media_id = item.get(self.provider.MEDIA_ID_FIELD,
                                    item.get("guid", "")),
                title = item.get("title", ""),
                description = item.get("description", ""),
                state = (
                    "live" if (item.get("mediaState") == "MEDIA_ON")
                    else
                    "archive" if (item.get("mediaState") == "MEDIA_ARCHIVE")
                    else
                    "off" if (item.get("mediaState") == "MEDIA_OFF")
                    else
                    "done" if (item.get("mediaState") == "MEDIA_DONE")
                    else
                    "unknown"
                ),
                call_letters = item.get("callLetters", ""),
                # epg_title=epg["title"],
                language=item.get("language", "").lower(),
                media_type = "audio" if "audio" in epg["title"].lower() else "video",
                feed_type = fix_feed_type(
                    item.get("mediaFeedType", item.get("mediaFeedSubType")),
                    epg["title"],
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("blurb", ""),
                ),
                free = item.get("freeGame"),
                playbacks = item.get("playbacks", []),
                **self.extra_media_attributes(item)
            )
              for epg in epgs
              for item in epg["items"]],
            key = lambda i: (
                i.get("media_type", "") != "video",
                self.FEED_TYPE_ORDER.index(i.get("feed_type", "").lower())
                if i.get("feed_type", "").lower() in self.FEED_TYPE_ORDER
                else len(self.FEED_TYPE_ORDER),
                i.get("language", ""),
            )
        )

        items = [
            i for i in items
            if i.media_id
            and i.state not in ["off", "done" ]
        ]
        return items

    def extra_media_attributes(self, item):
        return {}


@dataclass
class BAMMediaSource(model.MediaSource):

    # provider: typing.Optional[BaseProvider] = None
    game_id: str = ""
    media_id: str = ""
    title: str = ""
    description: str = ""
    state: str = "unknown"
    call_letters: str = ""
    language: str = ""
    feed_type: str = ""
    free: bool = False
    playbacks: typing.List[dict] = field(default_factory=list)


    MEDIA_STATE_MAP = {
        "live": "!",
        "archive": ".",
        "done": "^",
        "off": "X",
    }

    @property
    def state_indicator(self):
        return self.MEDIA_STATE_MAP.get(self.state, "?")

    @property
    def stream_indicator(self):
        if not self.state in ["unknown", "live", "done", "archive"]:
            logger.warn(f"game {self.game_id} media {self.media_id} state: {self.state}")
        media_type = self.media_type[0].upper()
        feed_type = self.feed_type[0].lower()
        if self.state == "live":
            feed_type = feed_type.upper()
        return media_type + feed_type
        # return urwid.Pile([
        #     ("pack", urwid.Text(c[0])),
        #     ("pack", urwid.Text(c[1])),
        # ])

    @property
    def helper(self):
        return "streamlink"

    @property
    @memo(region="long")
    def locator(self):

        # # FIXME: borked
        # # Get any team-specific profile overrides, and apply settings for them
        # profiles = tuple([ list(d.values())[0]
        #              for d in config.settings.profile_map.get("team", {})
        #              if list(d.keys())[0] in [
        #                      self.listing.away_abbrev,
        #                      self.listing.home_abbrev
        #              ] ])

        # if len(profiles):
        #     # override proxies for team, if defined
        #     if len(config.settings.profiles[profiles].proxies):
        #         old_proxies = self.session.proxies
        #         self.session.proxies = config.settings.profiles[profiles].proxies
        #         # self.session.refresh_access_token(clear_token=True)
        #         self.session.proxies = old_proxies

        if len(self.playbacks):
            playback = next(p for p in self.playbacks
                            if p["name"] == "HTTP_CLOUD_WIRED_60")

            try:
                media_url = playback["url"]
            except:
                from pprint import pformat
                raise Exception(pformat(media))
        else:
            try:
                stream = self.provider.session.get_stream(self)
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d, %s" %(self.game_id))
        return media_url



class BAMProviderData(model.ProviderData):

    season_year = Required(int)
    start = Required(datetime)
    end = Required(datetime)

    composite_key(model.ProviderData.classtype, season_year)


def parse_int(n):
    try:
        return int(n)
    except ValueError:
        return n
    except TypeError:
        return ""


class MediaAttributes(AttrDict):

    def __repr__(self):
        state = "!" if self.state == "MEDIA_ON" else "."
        free = "_" if self.free else "$"
        return f"{state}{free}"

    def __len__(self):
        return len(str(self))

def format_start_time(d):
    s = datetime.strftime(d, "%I:%M%p").lower()[:-1]
    if s[0] == "0":
        s = s[1:]
    return s

class BasePopUp(urwid.WidgetWrap):

    signals = ["close_popup"]

    def selectable(self):
        return True

class BAMDateFilter(DateFilter):

    @property
    def widget_kwargs(self):
        return {"initial_date": self.provider.start_date}



class OffsetDropdown(urwid.WidgetWrap):


    def __init__(self, timestamps, live=False, default=None):

        if "S" in timestamps: del timestamps["S"]
        timestamp_map = AttrDict(

            ( "Start" if k == "SO" else k, v ) for k, v in timestamps.items()
        )
        if live:
            timestamp_map["Live"] = False

        self.dropdown = Dropdown(
            timestamp_map, label="Begin playback",
            default = timestamp_map.get(default, None)
        )
        super().__init__(self.dropdown)

    @property
    def selected_value(self):
        return self.dropdown.selected_value

    def cycle(self, *args, **kwargs):
        self.dropdown.cycle(*args, **kwargs)


class WatchDialog(BasePopUp):

    signals = ["play"]

    def __init__(self,
                 provider,
                 selection,
                 media_title=None,
                 default_resolution=None,
                 watch_live=None):

        self.provider = provider
        self.game_id = selection["game_id"]
        self.media_title = media_title
        self.default_resolution = default_resolution
        self.watch_live = watch_live

        away_attr = provider.team_color_attr(
            selection.away_abbrev.lower(),
            provider.config.listings.line.colors,
            "primary"
        )

        home_attr = provider.team_color_attr(
            selection.home_abbrev.lower(),
            provider.config.listings.line.colors,
            "primary"
        )

        self.title = urwid.Text([
            (away_attr ,f"{selection.away_city} {selection.away_team}"),
            ("bold", " @ "),
            (home_attr, f"{selection.home_city} {selection.home_team}")
        ])

        # media = list(self.provider.get_media(self.game_id, title=self.media_title))
        # media = list(self.provider.get_media(self.game_id))
        # raise Exception(selection)
        try:
            media = selection.media
        except SGStreamNotFound as e:
            logger.warn(e)

        if not len(media):
            raise SGStreamNotFound

        feed_map = [
            (
                (f"""{e.media_type.title()}: {e.get("feed_type", "").title()} """
                 f"""({e.get("call_letters", "")}{"/"+e.get("language") if e.get("language") else ""})"""),
                e["media_id"].lower()
            )
            for e in media
        ]

        # raise Exception(feed_map)

        try:
            home_feed = next(
                e for e in media
                if e["feed_type"].lower() == "home"
            )
        except StopIteration:
            home_feed = media[0]

        self.live_stream = (home_feed.get("state") == "live")
        self.feed_dropdown = Dropdown(
            feed_map,
            label="Feed",
            default=home_feed["media_id"],
            max_height=8
        )
        urwid.connect_signal(
            self.feed_dropdown,
            "change",
            lambda s, b, *args: self.update_offset_dropdown(*args)
        )

        self.resolution_dropdown = Dropdown(
            self.provider.RESOLUTIONS, default=self.default_resolution,
            label="Resolution"
        )

        self.offset_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.update_offset_dropdown(self.feed_dropdown.selected_value)

        def play(s):
            media_id = self.feed_dropdown.selected_value
            offset = (
                self.offset_dropdown.selected_value
            )
            self.provider.play(
                selection,
                # media = selected_media,
                media_id = media_id,
                offset=offset,
                resolution=self.resolution_dropdown.selected_value
            )
            self._emit("close_popup")

        def download(s):
            media_id = self.feed_dropdown.selected_value
            self.provider.download(
                selection,
                # media = selected_media,
                media_id = media_id,
                offset=self.offset_dropdown.selected_value,
                resolution=self.resolution_dropdown.selected_value
            )
            self._emit("close_popup")

        def cancel(s):
            self._emit("close_popup")

        self.play_button = SquareButton("Play")
        self.play_button._label.align = 'center'
        self.download_button = SquareButton("Download")
        self.download_button._label.align = 'center'
        self.cancel_button = SquareButton("Cancel")
        self.cancel_button._label.align = 'center'

        urwid.connect_signal(self.play_button, "click", play)
        urwid.connect_signal(self.download_button, "click", download)
        urwid.connect_signal(self.cancel_button, "click", cancel)

        pile = urwid.Pile([
            ("pack", urwid.Padding(self.title, width="pack", align="center")),
            ("pack", urwid.Padding(urwid.Text( ("bold", selection.venue) ),
                                   width="pack", align="center")),
            ("pack", urwid.Text(" ")),
            ("weight", 1, urwid.Pile([
                ("weight", 5, urwid.Filler(
                    urwid.Columns([
                        ("weight", 3, self.feed_dropdown),
                        ("weight", 1, self.resolution_dropdown),
                    ]), valign="top")),
                ("weight", 1, urwid.Filler(self.offset_dropdown_placeholder)),
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                        ("weight", 1,
                         urwid.Padding(
                             urwid.AttrMap(self.play_button, "dropdown_text", "dropdown_focused"),
                             width=16
                         )),
                        ("weight", 1, urwid.Text("")),
                        ("weight", 1,
                         urwid.Padding(
                             urwid.AttrMap(self.download_button, "dropdown_text", "dropdown_focused"),
                             width=16
                         )),
                        ("weight", 1, urwid.Text("")),
                        ("weight", 1,
                         urwid.Padding(
                             urwid.AttrMap(self.cancel_button, "dropdown_text", "dropdown_focused"),
                             width=16
                         )),
                ])))
            ]))
        ])
        super(WatchDialog, self).__init__(pile)
        pile.contents[3][0].focus_position = 2


    def update_offset_dropdown(self, media_id):
        # raise Exception(self.provider.media_timestamps(self.game_id, media_id))
        self.offset_dropdown = OffsetDropdown(
            self.provider.media_timestamps(self.game_id, media_id),
            live = self.live_stream,
            default = "Live" if self.watch_live and self.live_stream else "Start"
        )
        self.offset_dropdown_placeholder.original_widget = self.offset_dropdown


    def keypress(self, size, key):

        key = super(WatchDialog, self).keypress(size, key)
        if key == "meta enter":
            self.play_button.keypress(size, "enter")
        elif key in ["<", ">"]:
            self.resolution_dropdown.cycle(1 if key == "<" else -1)
        elif key in ["[", "]"]:
            self.feed_dropdown.cycle(-1 if key == "[" else 1)
        elif key in ["-", "="]:
            self.offset_dropdown.cycle(-1 if key == "-" else 1)
        if key == "q":
            self.cancel()
        else:
            # return super(WatchDialog, self).keypress(size, key)
            # return super(WatchDialog, self).keypress(size, key)
            return key


class LiveStreamFilter(ListingFilter):

    @property
    def values(self):
        return AttrDict([
            ("Live", "live"),
            ("From Start", "start"),
        ])


class BAMProviderDataTable(ProviderDataTable):

    index = "game_id"
    ui_sort = False
    sort_icons = False
    detail_selectable = True
    detail_hanging_indent = "away_team_box"
    # no_load_on_init = True

    @property
    def empty_message(self):
        return f"(no games on {self.provider.filters.date.value})"

    @property
    def detail_fn(self):
        return self.provider.get_details

    def keypress(self, size, key):
        key = super().keypress(size, key)
        if key in ["right", "space"]:
            if self.selection.details_disabled:
                self.selection.details_disabled = False
                self.selection.details_focused = True
        elif key in ["left", "esc", "q"]:
            if self.selection.details_focused and not self.selection.details_disabled:
                self.selection.details_disabled = True
            else:
                return key
        elif key == "t":
            self.provider.filters.date.value = self.provider.current_game_day()
        elif key == "meta enter":
            self.provider.play(self.selection.data)
        # elif key == ".":
        #     self.selection.toggle_details()
        elif key == "ctrl k":
            logger.info(self.selection.data.game_id)
            # self.pack_columns()
            # self.sort_by_column(self.initial_sort)
            self.refresh()
            # self.selection.details_disabled = not self.selection.details_disabled
            # logger.info(f"{self.selection.details_disabled}")
        else:
            return key



class BAMProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = BAMProviderDataTable

@with_view(BAMProviderView)
class BAMProviderMixin(BackgroundTasksMixin, abc.ABC):
    """
    Mixin class for use by BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """

    UPDATE_INTERVAL = 60

    TASKS = [
        ("update", UPDATE_INTERVAL, [], {})
    ]

    sport_id = 1 # FIXME

    FILTERS_BROWSE = AttrDict([
        ("date", BAMDateFilter)
    ])

    FILTERS_OPTIONS = AttrDict([
        ("resolution", ResolutionFilter),
        ("live_stream", LiveStreamFilter),
        ("hide_spoilers", BooleanFilter),
    ])

    ATTRIBUTES = AttrDict(
        start = {"width": 6, "format_fn": format_start_time},
        away_team_box = {"label": "away", "width": 16},
        home_team_box = {"label": "home", "width": 16},
        line = {"pack": True},
        media_available = {"label": "media", "width": 10},
        # game_id = {"width": 10},
    )

    HELPER = "streamlink"

    REQUIRED_CONFIG = {"credentials": ["username", "password"]}

    GAME_STATUS_ORDER = [
        "Pre-Game",
        "Final",
        "Postponed",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["date"].connect("changed", self.on_date_change)
        self.filters["hide_spoilers"].connect("changed", self.on_hide_spoilers_change)
        self.game_map = AttrDict()


    def init_config(self):
        # set alternate team color attributes
        for teamname, attr in self.config.attributes.teams.items():
            self.config.attributes.teams_primary[teamname] = {"fg": attr["bg"]}
            self.config.attributes.teams_alternate[teamname] = {"fg": attr["fg"]}
            self.config.attributes.teams_full[teamname] = attr
            self.config.attributes.teams_inverse[teamname] = {"fg": attr["bg"], "bg": attr["fg"]}
            # del self.config.attributes.teams[teamname]

    @property
    def session_params(self):
        return self.config.credentials

    @property
    def config_is_valid(self):
        return (
            super().config_is_valid
            and
            self.HELPER in list(player.PROGRAMS[Helper].keys())
        )

    async def update(self):
        self.update_games()

    def update_games(self):
        date = self.filters.date.value
        schedule = self.schedule(start=date, end=date)
        try:
            games = schedule["dates"][-1]["games"]
        except IndexError:
            games = []

        self.game_map.clear()
        for game in games:
            self.game_map[game["gamePk"]] = AttrDict(game)
        self.view.table.refresh()

    def on_date_change(self, date):
        self.update_games()

    def on_hide_spoilers_change(self, value):
        self.reset()

    def game_data(self, game_id):

        return self.game_map[game_id]
        # schedule = self.schedule(game_id=game_id)
        # try:
        #     # Get last date for games that have been rescheduled to a later date
        #     game = schedule["dates"][-1]["games"][0]
        # except KeyError:
        #     raise SGException("no game data")
        # return game


    @memo(region="short")
    def schedule(
            self,
            # sport_id=None,
            season=None, # season only works for NHL
            start=None,
            end=None,
            game_type=None,
            team_id=None,
            game_id=None,
            brief=False
    ):

        logger.debug(
            "getting schedule: %s, %s, %s, %s, %s, %s, %s" %(
                self.sport_id,
                season,
                start,
                end,
                game_type,
                team_id,
                game_id
            )
        )
        if brief:
            template = self.SCHEDULE_TEMPLATE_BRIEF
        else:
            template = self.SCHEDULE_TEMPLATE

        url = template.format(
            sport_id = self.sport_id,
            season = season if season else "",
            start = start.strftime("%Y-%m-%d") if start else "",
            end = end.strftime("%Y-%m-%d") if end else "",
            game_type = game_type if game_type else "",
            team_id = team_id if team_id else "",
            game_id = game_id if game_id else ""
        )
        logger.debug(url)

        # with self.cache_responses_short():
        return self.session.get(url).json()


    def listings(self, offset=None, limit=None, *args, **kwargs):

        return iter(
            sorted(
                (
                    self.LISTING_CLASS.from_json(self.IDENTIFIER, g)
                    for g in self.game_map.values()
                ),
                key = lambda l:
                (
                    not l.is_favorite,
                    self.GAME_STATUS_ORDER.index(l.state)
                    if l.state in self.GAME_STATUS_ORDER
                    else 0,
                    l.start_time
                )
            )
        )

    def get_url(self, game_id,
                media = None,
                offset=None,
                preferred_stream=None,
                call_letters=None,
                output=None,
                verbose=0):

        live = False
        team = None
        # sport_code = "mlb" # default sport is MLB

        # media_title = "MLBTV"
        # media_id = None
        allow_stdout=False

        # schedule = self.schedule(
        #     game_id = game_id
        # )

        # date = schedule["dates"][-1]
        # game = date["games"][-1]

        game = self.game_data(game_id)

        away_team_abbrev = game["teams"]["away"]["team"]["abbreviation"].lower()
        home_team_abbrev = game["teams"]["home"]["team"]["abbreviation"].lower()

        if not preferred_stream or call_letters:
            preferred_stream = (
                "away"
                if team == away_team_abbrev
                else "home"
            )


        if not media:
            try:
                media = next(self.get_media(
                    game_id,
                    # media_id = media_id,
                    # title=media_title,
                    preferred_stream=preferred_stream,
                    call_letters = call_letters
                ))
                # raise Exception(media)
            except StopIteration:
                raise SGStreamNotFound("no matching media for game %d" %(game_id))

        # Get any team-specific profile overrides, and apply settings for them
        profiles = tuple([ list(d.values())[0]
                     for d in config.settings.profile_map.get("team", {})
                     if list(d.keys())[0] in [
                             away_team_abbrev, home_team_abbrev
                     ] ])

        if len(profiles):
            # override proxies for team, if defined
            if len(config.settings.profiles[profiles].proxies):
                old_proxies = self.session.proxies
                self.session.proxies = config.settings.profiles[profiles].proxies
                # self.session.refresh_access_token(clear_token=True)
                self.session.proxies = old_proxies

        if "playbacks" in media:
            playback = next(p for p in media["playbacks"]
                            if p["name"] == "HTTP_CLOUD_WIRED_60")

            try:
                media_url = playback["url"]
            except:
                from pprint import pformat
                raise Exception(pformat(media))
        else:
            stream = self.session.get_stream(media)
            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d, %s" %(game_id))

        return media_url

    @abc.abstractmethod
    def teams(self, season=None):
        pass

    @property
    @abc.abstractmethod
    def start_date(self):
        pass

    def current_game_day(self):
        return (datetime.now() - timedelta(hours=8)).date()

    def parse_identifier(self, identifier):

        game_number = 1
        game_date = None
        team = None
        feed_type = "home"

        game_date = self.current_game_day().strftime("%Y/%m/%d")

        if isinstance(identifier, int):
            game_id = identifier
            schedule = self.schedule(
                game_id = game_id
            )
        else:
            try:
                (game_date, team, game_number) = identifier.split(".")
            except ValueError:
                try:
                    (game_date, team) = identifier.split(".")
                except ValueError:
                    if identifier.isalpha():
                        team = identifier
                    else:
                        game_date = identifier
            except AttributeError:
                pass

            game_date = dateutil.parser.parse(game_date).date()
            self.filters["date"].value = game_date

            if not team:
                raise SGIncompleteIdentifier


            if "-" in team:
                (sport_code, team) = team.split("-")

            game_number = int(game_number)
            teams =  self.teams(season=game_date.year)
            team_id = teams.get(team)

            if not team:
                msg = "'%s' not a valid team code, must be one of:\n%s" %(
                    identifier, " ".join(teams)
                )
                raise argparse.ArgumentTypeError(msg)

            schedule = self.schedule(
                start = game_date,
                end = game_date,
                # sport_id = sport["id"],
                team_id = team_id
            )

        try:
            date = schedule["dates"][-1]
            game = date["games"][game_number-1]

        except IndexError:
            raise SGException("No game %d found for %s on %s" %(
                game_number, team, game_date)
            )
        g = self.LISTING_CLASS.from_json(self.IDENTIFIER, game)

        if team.lower() == g.away_abbrev.lower():
            feed_type = "away"
        elif team.lower() == g.home_abbrev.lower():
            feed_type = "home"
        else:
            raise Exception(team, g.away_abbrev)

        return (g, dict(feed_type=feed_type))
        # return self.new_listing(
        #     game_id = game_id,
        #     away_team = away_team,
        #     home_team = home_team,
        #     away_abbrev = away_abbrev,
        #     home_abbrev = home_abbrev
        # )

    def on_select(self, widget, selection):
        self.open_watch_dialog(selection)

    # def on_activate(self):
    #     super().on_activate()
        # logger.info(f"activate: {self.filters.date.value}")
        # self.filters.date.changed()
        # self.refresh()
        # if not self.filters.date.value:
        #     raise Exception
        #     self.filters.date.value = datetime.now()

    def open_watch_dialog(self, selection):
        # media = list(self.get_media(selection["game_id"]))
        try:
            dialog = WatchDialog(
                self,
                selection,
                media_title = self.MEDIA_TITLE,
                default_resolution = self.filters.resolution.value,
                watch_live = self.filters.live_stream.value == "live"
            )
            self.view.open_popup(dialog, width=80, height=15)
        except SGStreamNotFound:
            logger.warn(f"no stream found for game {selection['game_id']}")


        # self.play(selection)

    def get_details(self, listing):

        game_id = listing.game_id
        game = self.game_data(game_id)

        def play_highlight(selection):
            logger.info(f"play_highlight: {selection}")
            task = model.PlayMediaTask(
                provider=self.NAME,
                title=selection.title,
                sources = [
                    model.MediaSource(
                        provider_id=self.IDENTIFIER,
                        url = selection.url,
                        media_type = "video"
                    )
                ]
            )
            logger.info(f"task: {task}")
            player_spec = {"media_types": {"video"}}
            helper_spec = None
            # asyncio.create_task(Player.play(task, player_spec, helper_spec))
            state.task_manager.play(task, player_spec, helper_spec)

        box = self.DETAIL_BOX_CLASS(self, listing)
        box.connect("play", play_highlight)
        return box

    def get_source(self, selection, media_id=None, feed_type=None, **kwargs):
        try:
            selected_media = next(
                m for m in selection.media
                if (not media_id or m.media_id == media_id)
                and (not feed_type or m.feed_type.lower() == feed_type.lower())
            )
        except StopIteration:
            selected_media = selection.media[0]

        return selected_media

    def play_args(self, selection, **kwargs):

        source, kwargs = super().play_args(selection, **kwargs)

        # kwargs["resolution"] = self.filters.resolution.value
        media_type = source.media_type
        # don't use video resolution for audio feeds
        if media_type == "audio":
            kwargs["resolution"] = "best"
        offset = kwargs.pop("offset", None)
        if offset:
            # raise Exception(selection)
            if (source.state == "live"): # live stream
                logger.debug("live stream")
                # calculate HLS offset, which is negative from end of stream
                # for live streams
                # start_time = dateutil.parser.parse(timestamps["S"])
                start_time = selection.start
                # start_time = selection.start
                offset_delta = (
                    datetime.now(pytz.utc)
                    - start_time.astimezone(pytz.utc)
                    - (timedelta(seconds=offset))
                )
                # offset_delta = (timedelta(seconds=-offset))
                # raise Exception(
                #     start_time,
                #     start_time.astimezone(pytz.utc),
                #     datetime.now(pytz.utc),
                #     datetime.now(pytz.utc) - start_time.astimezone(pytz.utc),
                #     (timedelta(seconds=-offset)),
                #     offset,
                #     offset_delta,
                #     offset_delta.seconds,
                # )
            else:
                # raise Exception
                logger.debug("recorded stream")
                offset_delta = timedelta(seconds=offset)

            # offset_seconds = offset_delta.seconds
            kwargs["offset"] = offset_delta.seconds

        kwargs["headers"] = self.session.headers
        kwargs["cookies"] = self.session.cookies
        return (source, kwargs)

    def team_color_attr(self, team, cfg, style=None):

        if cfg is False:
            return "bold"
        color_cfg = "teams_" + (style or cfg or "primary")
        if team.lower() in self.config.attributes[color_cfg]:
            key = team.lower()
        else:
            key = "none"
        attr = f"{self.IDENTIFIER.lower()}.{color_cfg}.{key}"
        return attr
