#!/usr/bin/env python3
"""duo gui: the Duo's features, knobs, checks and logs in a GNOME window.

    duo gui              open the window (or raise it when it is open)
    duo gui --toggle     open it, or close it when it is the focused window:
                         what F12 runs (watch-fn, the MyASUS key)
    duo gui --page NAME  open on home, device, screens, diagnosis, update or
                         settings; PAGE/TAB picks a tab, e.g. device/input

The window is a second face of the terminal screen (`duo`), not a second
program: it shows what lib/duo_model.py reads and changes things only through
the same Model actions, which are duo-cli and `systemctl --user` commands. If
the window can do something the command line cannot, that is a bug.

GTK 4 and libadwaita through PyGObject, run by $DUO_PYGI like the display code
(on Ubuntu: python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, which system/10-packages.sh
installs). The functions above make_app() hold every decision about what to
show and have no GTK in them, so tests/test_duo_gui.py runs without a display.
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import duo_model as dm  # noqa: E402  (same directory)
import battery  # noqa: E402

# One instance per session: a second `duo gui` hands its request to the first
# over D-Bus and exits, which is what lets F12 close the window it opened. The
# desktop file is named after it so GNOME puts the window under that launcher.
APP_ID = "io.github.zenduo.Duo"
TITLE = "Zenbook Duo"
REPO_URL = "https://github.com/Toldry/linux-on-zenbook-duo"
ABOUT_LINKS = (
    ("Source code on GitHub", REPO_URL, REPO_URL),
    ("Report a problem", "attach the output of duo report", REPO_URL + "/issues"),
    ("What works and what does not", "docs/FEATURES.md", REPO_URL + "/blob/main/docs/FEATURES.md"),
)

LOG_LINES = 400                 # journal lines the Logs page keeps
THEME_SECONDS = 2.0             # F8 flips the style behind the window's back

SCREEN_BUTTONS = (("Top", "top"), ("Bottom", "bottom"), ("Both", "both"),
                  ("Toggle bottom", "toggle"))
LAYOUT_BUTTONS = (("Laptop", "laptop"), ("External", "external"), ("Extend", "extend"),
                  ("Mirror", "mirror"))
BRIGHTNESS_BUTTONS = (("−", "down"), ("+", "up"), ("Match", "match"))

# The window is laid out like MyASUS (screenshots from the owner, 2026-10-30):
# a sidebar of pages, tabs inside a page, and each setting a card with its
# icon, a sentence on what it does, and its controls. Where MyASUS has
# something this project does not do, the card or the sidebar entry is still
# there, greyed, with the reason, so nobody wonders whether it was forgotten.

# The sidebar, top to bottom: (page, title, icon, why it is greyed or "").
# None is MyASUS's separator line.
SIDEBAR = (
    ("home", "Home", "go-home-symbolic", ""),
    ("device", "Device Settings", "preferences-system-symbolic", ""),
    ("screens", "Screens", "video-display-symbolic", ""),
    ("diagnosis", "System Diagnosis", "system-run-symbolic", ""),
    ("update", "System Update", "software-update-available-symbolic", ""),
    None,
    ("asus-devices", "My ASUS Devices", "network-workgroup-symbolic",
     "ASUS monitors, routers and mice: no Linux support"),
    ("messages", "Message Center", "mail-unread-symbolic", "ASUS's news feed: not on Linux"),
    None,
    ("recommended", "Recommended", "view-app-grid-symbolic", "ASUS app offers: not on Linux"),
    ("glidex", "ASUS GlideX", "phone-symbolic", "screen sharing with a phone: no Linux app"),
    None,
    ("support", "Customer Support", "help-browser-symbolic", "ASUS's support app: asus.com/support"),
    ("account", "User Center", "avatar-default-symbolic", "an ASUS account: not needed here"),
    ("settings", "Settings", "emblem-system-symbolic", ""),
)
PAGES = tuple(entry[0] for entry in SIDEBAR if entry and not entry[3])

# Tabs inside a page, as MyASUS has them: page -> ((tab, title), ...).
TABS = {
    "device": (("power", "Power & Performance"), ("audio", "Audio & Visual"),
               ("input", "Input Device Settings")),
    "diagnosis": (("tool", "Diagnostic Tool"), ("history", "Diagnostic History")),
    "settings": (("services", "Background services"), ("extras", "Extras"), ("about", "About")),
}

# `duo gui --page` names from before the MyASUS layout, still accepted.
PAGE_ALIASES = {
    "overview": ("home", ""), "features": ("settings", "services"),
    "extras": ("settings", "extras"), "logs": ("diagnosis", "history"),
    "doctor": ("diagnosis", "tool"), "about": ("settings", "about"),
}


class Card:
    """One MyASUS setting on a Device Settings tab.

    how says what the card drives: "fan-mode", "fn-key", "kb-level",
    "touchpad", "knob:KEY" (a zenduo.conf knob) or "feature:NAME" (a feature
    switch). None means not done here, and why says so; the card is shown
    greyed instead of left out.
    """

    def __init__(self, tab, title, desc, icon, how=None, why=""):
        self.tab, self.title, self.desc, self.icon = tab, title, desc, icon
        self.how, self.why = how, why

    @property
    def available(self):
        return self.how is not None


# MyASUS's Device Settings, in its order (screenshots 2026-10-30, settings
# seen in the Windows capture 2026-10-31, docs/HARDWARE.md).
CARDS = (
    Card("power", "Fan Profile",
         "How hard the fans work. Fn+F steps through the same three modes.",
         "weather-windy-symbolic", "fan-mode"),
    Card("power", "Battery Care Mode",
         "Stops charging at this level, which makes the battery last longer. MyASUS's "
         "Battery Care is 80 %. 20 to 100; empty charges to 100 %.",
         "battery-good-symbolic", "knob:BATTERY_LIMIT"),
    Card("power", "Instant Full-Charge Mode",
         "Charges to 100 % once, then Battery Care takes over again.",
         "battery-full-charging-symbolic",
         why="Not done yet: set the limit to 100 for now and back afterwards."),
    Card("power", "Sleep Assistant",
         "Switches to hibernation when standby drains the battery too fast.",
         "weather-clear-night-symbolic",
         why="Needs hibernation, which this setup does not use."),
    Card("power", "Memory Allocated to GPU",
         "How much memory the integrated graphics keep for themselves.",
         "applications-graphics-symbolic",
         why="A BIOS setting. On this laptop it does not save even from MyASUS "
             "(measured 2026-10-31)."),
    Card("audio", "Speaker voicing",
         "The harman/kardon tuning of the built-in speakers, as an EasyEffects chain.",
         "audio-speakers-symbolic", "feature:speaker-dsp"),
    Card("audio", "AI Noise-Canceling microphone",
         "Removes background noise from the microphone.",
         "audio-input-microphone-symbolic",
         why="Planned: ASUS's filter is Windows-only; a Linux one is to come."),
    Card("audio", "Splendid",
         "Colour modes for the screens: Normal, Vivid, Eye Care, Manual.",
         "preferences-color-symbolic",
         why="Not attempted yet. GNOME Settings → Displays has Night Light."),
    Card("audio", "OLED Care",
         "Pixel refresh and pixel shift, which keep the OLED panels from burning in.",
         "video-display-symbolic",
         why="Not attempted yet. A short screen blank (GNOME Settings → Power) helps."),
    Card("input", "Function key",
         "What F1–F12 do on their own. Fn+Esc switches between the two.",
         "input-keyboard-symbolic", "fn-key"),
    Card("input", "Keyboard backlight",
         "The light level, as F4 cycles it.",
         "keyboard-brightness-symbolic", "kb-level"),
    Card("input", "Smart Switch",
         "On Bluetooth, the keyboard light turns itself off to save the keyboard's battery.",
         "bluetooth-symbolic", "knob:KB_BACKLIGHT_IDLE_OFF"),
    Card("input", "Restore the keyboard light",
         "The keyboard forgets its light level when it is docked, undocked or the laptop "
         "wakes up; this sets it back.",
         "view-refresh-symbolic", "knob:KB_BACKLIGHT_RESTORE"),
    Card("input", "Automatic keyboard backlight",
         "Auto-brighten and auto-dim with the room's light, from the ambient light sensor.",
         "display-brightness-symbolic",
         why="Not done yet."),
    Card("input", "Keyboard backlight sleep mode",
         "Turns the light off after an idle time on battery or on AC, also while docked.",
         "preferences-desktop-screensaver-symbolic",
         why="Not done yet; Smart Switch covers the keyboard on Bluetooth."),
    Card("input", "Touchpad",
         "Turn the touchpad on or off. GNOME has this switch already.",
         "input-touchpad-symbolic", "touchpad"),
    Card("input", "MyASUS key (F12)",
         "Opens this window, and closes it again when it is in front.",
         "input-keyboard-symbolic", "knob:F12_GUI"),
)

# MyASUS's "Quick setting" card, above the tabs.
QUICK_SETTING = ("Quick setting", "Save several settings as a profile and switch with one click.",
                 "Not done yet.")

# The Screens page: what ScreenXpert does under Windows.
SCREENS_TODO = (
    ("App launcher and moving windows between screens",
     "ScreenXpert's control panel, task groups and the window-throw gestures.",
     "view-dual-symbolic", "Planned (PLAN phase U). F8 already swaps the windows."),
    ("Auto-rotate",
     "Tent, book and portrait: the screens turn with the laptop.",
     "object-rotate-right-symbolic", "Experimental: the rotation is logged, not acted on yet."),
)

SYSTEM_UPDATE = (
    "BIOS and firmware updates",
    "MyASUS updates the BIOS and the keyboard's firmware.",
    "This project never writes firmware from Linux. Use MyASUS on Windows, "
    "or EZ Flash in the BIOS setup.")

FAN_MODES = (("standard", "Standard", "balanced", "Balanced noise and speed."),
             ("whisper", "Whisper", "quiet", "Quiet fans, lower speed."),
             ("performance", "Performance", "performance", "Full speed, louder fans."))
FN_KEYS = ((False, "Locked Fn key", "F1–F12 do the printed hotkey: volume, brightness and so on."),
           (True, "Normal Fn key", "F1–F12 are the F-keys; Fn+F-key gives the hotkey."))

# Everything on Device Settings and Screens copies what the laptop does under
# Windows. What does not lives on Settings → Extras, so it is never mistaken
# for parity.
EXTRAS = ("Not what Windows does",
          "Keys given a job of their own. Switched off, they do what they do under "
          "Windows: F7 brings up GNOME's display switcher, as Win+P does, and F8 swaps "
          "the windows between the two screens.")

# The Screens page's knob groups, in the order shown: (title, description).
KNOB_GROUPS = (
    ("Screens", ""),
    ("Brightness", "The brightness keys change the top screen, and the bottom one follows."),
)

# What each knob is called where it is shown: (group, title, subtitle).
# duo_model's descriptions are written for `duo config`; these are written for
# someone who has not read the code. The key itself is the row's tooltip.
# A knob a Card drives takes the card's title and words instead.
KNOB_TEXT = {
    "APPLY_METHOD": ("Screens", "How layouts are applied",
                     "Temporary changes the screens without asking. Persistent makes GNOME ask "
                     "“Keep display settings?” after every dock, undock and wake."),
    "DOCK_POLICY": ("Screens", "Bottom screen off under the keyboard",
                    "Turns the bottom screen off while the keyboard lies on it, and back on "
                    "when you lift it."),
    "REMEMBER_LAYOUT": ("Screens", "Remember the layout for each set of monitors",
                        "Like Windows: positions, scaling and the main screen come back when "
                        "the same monitors are connected again."),
    "LOGIN_SCREEN_LAYOUT": ("Screens", "Same layout on the login screen",
                            "The password prompt appears on the screen you were using."),
    "TOUCH_MAPPING": ("Screens", "Touch and pen stay on the screen you touch",
                      "Without this, GNOME sends touches on both screens to the top one."),
    "F7_TERMINAL": (EXTRAS[0], "F7 opens a terminal",
                    "Instead of GNOME's display switcher. F7 types Super+P, so that shortcut "
                    "does the same; Ctrl+Alt+T keeps working too."),
    "F8_DARK_MODE": (EXTRAS[0], "F8 switches dark mode",
                     "Light and dark style, as Settings → Appearance switches it. Off, F8 "
                     "swaps the windows between the screens, as under Windows (from the next "
                     "login after installing)."),
    "F12_GUI": ("Device", "F12 opens this window", ""),
    "BACKLIGHT_SOURCE": ("Brightness", "Brightness is copied from",
                         "The screen the other one follows; intel_backlight is the top screen."),
    "BACKLIGHT_TARGET": ("Brightness", "Brightness is copied to",
                         "Automatic finds the bottom screen."),
    "KB_BACKLIGHT_RESTORE": ("Device", "After dock, undock and wake", ""),
    "KB_BACKLIGHT_IDLE_OFF": ("Device", "Light off after", ""),
    "BATTERY_LIMIT": ("Device", "Charge limit in percent", ""),
}

# ── what the window shows (no GTK below this line until make_app) ─────────────

def panel_text(status, enabled):
    if status == "absent":
        return "not found"
    if status != "connected":
        return status
    return "on" if enabled == "enabled" else "off"


def glance_rows(g):
    """[(key, title, value)] for the Overview's "This machine" group."""
    panels = {c: panel_text(st, en) for c, st, en in g.panels}
    lights = ", ".join(f"{name} {'?' if pct is None else f'{pct} %'}"
                       for name, pct in g.backlights) or "none"
    rows = [
        ("model", "Model", g.model or "unknown"),
        ("keyboard", "Keyboard", g.keyboard_text),
        ("top", "Top panel (eDP-1)", panels.get("eDP-1", "?")),
        ("bottom", "Bottom panel (eDP-2)", panels.get("eDP-2", "?")),
        ("backlight", "Backlight", lights),
        ("laptop-battery", "Laptop battery", battery.laptop_text(g.laptop_battery)),
        ("kb-battery", "Keyboard battery", g.kb_battery or "unknown"),
        ("battery", "Battery charge limit",
         f"{g.battery_now} %" if g.battery_now else "not supported by this kernel"),
    ]
    if g.override:
        rows.append(("override", "Manual override",
                     "a hand-picked panel set holds until the next dock or undock"))
    return rows


def feature_state(unit):
    """(text, level) of a feature row, level one of ok | warn | err | off."""
    if unit is None:
        return "not reported", "off"
    text = unit.state
    if unit.state == "failed" and unit.exec_status:
        text += f" (exit {unit.exec_status})"
    if unit.restarts and unit.state == "running":
        text += f", {unit.restarts} restarts"
    if unit.hm:
        text += " [hm]"
    return text, unit.level


def knob_kind(knob, backlight_names=()):
    """switch | choice | entry: the row a knob gets on the Settings page. A
    backlight is picked from a list, unless the kernel lists none to pick."""
    if knob.kind == "bool":
        return "switch"
    if knob.kind == "enum" or (knob.kind == "backlight" and backlight_names):
        return "choice"
    if knob.kind == "int" and knob.values:
        return "choice"    # a few sensible values beat typing a number
    return "entry"


def knob_choices(knob, backlight_names):
    """The values a choice row offers, "" meaning "empty" (auto-detect)."""
    values = list(knob.values) if knob.values else list(backlight_names)
    if knob.empty_ok:
        values.insert(0, "")
    return values


def choice_label(value, knob=None):
    if knob is not None and knob.key == "KB_BACKLIGHT_IDLE_OFF" and value.isdigit():
        return seconds_label(int(value))
    return value if value else "(automatic)"


def seconds_label(seconds):
    if seconds == 0:
        return "Never"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"After {minutes} minute" + ("" if minutes == 1 else "s")
    return f"After {seconds} seconds"


def knob_text(knob):
    """(group, title, subtitle); a knob nobody wrote words for yet still shows."""
    return KNOB_TEXT.get(knob.key, (KNOB_GROUPS[0][0], knob.key, knob.desc))


def knob_section(knob):
    return knob_text(knob)[0]


def card_for(knob):
    """The Card that drives this knob, or None."""
    return next((c for c in CARDS if c.how == f"knob:{knob.key}"), None)


def knob_page(knob):
    """Where a knob is shown: device (on its card), extras (what Windows does
    not do) or screens."""
    if card_for(knob) is not None:
        return "device"
    return "extras" if knob_section(knob) == EXTRAS[0] else "screens"


def cards(tab):
    return [c for c in CARDS if c.tab == tab]


def page_target(name):
    """(page, tab) for a --page NAME: a page, page/tab, or an old page name."""
    if name in PAGE_ALIASES:
        return PAGE_ALIASES[name]
    page, _, tab = name.partition("/")
    if page not in PAGES or (tab and tab not in dict(TABS.get(page, ()))):
        raise ValueError(name)
    return page, tab


def fan_mode_of(profile):
    """The MyASUS mode key for a platform_profile value, or ""."""
    return next((key for key, _t, value, _d in FAN_MODES if value == profile), "")


def home_tiles(g):
    """[(key, title, value, icon)]: the cards at the top of Home, as MyASUS
    shows battery, CPU and memory there."""
    mode = next((title for _k, title, value, _d in FAN_MODES if value == g.fan_mode),
                g.fan_mode or "unknown")
    return [
        ("laptop-battery", "Battery", battery.laptop_text(g.laptop_battery),
         "battery-good-symbolic"),
        ("keyboard", "Keyboard", {"docked": "docked", "undocked": "off the laptop",
                                  "dead": "USB link dead: re-seat it"}.get(g.keyboard, g.keyboard),
         "input-keyboard-symbolic"),
        ("kb-battery", "Keyboard battery", g.kb_battery or "unknown", "battery-symbolic"),
        ("fan", "Fan Profile", mode, "weather-windy-symbolic"),
    ]


def show_result(result):
    """Toast a finished action only when it failed: success shows on the page
    itself, and a toast saying "ok" after every click was noise."""
    return not result.ok


def journal_text(entries, errors_only=False, now=None, limit=LOG_LINES, error=""):
    if not entries:
        return f"Cannot read the journal: {error}" if error else "Nothing logged yet."
    lines = []
    for e in entries:
        if errors_only and e.level not in ("err", "warn"):
            continue
        lines.append(f"{e.when(now)}  {e.source:<16} {e.msg}")
    return "\n".join(lines[-limit:])


def problems_text(entries, now=None):
    if not entries:
        return "Nothing in the journal since the last clear."
    return "\n".join(f"{e.when(now)}  {e.source}: {e.msg}" for e in entries)


def parse_args(argv):
    """(toggle, page) from the command line; raises ValueError on anything else."""
    toggle, page = False, ""
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--toggle":
            toggle = True
        elif a == "--page" and args:
            page = args.pop(0)
            page_target(page)          # raises ValueError for a name it does not know
        else:
            raise ValueError(a)
    return toggle, page


def toggle_closes(visible, active):
    """F12 on the focused window closes it; anywhere else it brings it up."""
    return visible and active


def after_knob(key, value, result):
    """The follow-up a successful knob change needs: ("restart", feature) when a
    running daemon reads it only at start, else None."""
    if not result.ok:
        return None
    knob = dm.KNOB_BY_KEY.get(key)
    if knob is not None and knob.daemon:
        return ("restart", knob.daemon)
    return None


# ── the window ───────────────────────────────────────────────────────────────

def make_app(Adw, Gio, GLib, Gtk, Pango):
    """The application class, built once the GTK modules are imported."""

    def linked(*widgets):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        box.add_css_class("linked")
        for w in widgets:
            box.append(w)
        return box

    def monospace_view():
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, child=view)
        return view, scroll

    LEVEL_CSS = {"ok": "success", "warn": "warning", "err": "error", "off": "dim-label"}

    # MyASUS's cards: a darker panel with a blue rule on the left of each
    # setting, a big icon, and the controls on the right.
    CSS = b"""
    .duo-card { border-left: 3px solid @accent_bg_color; }
    .duo-card.duo-off { border-left-color: alpha(@window_fg_color, 0.2); }
    .duo-tile { padding: 14px 16px; min-width: 190px; }
    .duo-tile .duo-tile-value { font-size: 1.35em; font-weight: 600; }
    .duo-hero { font-size: 1.9em; font-weight: 700; }
    .duo-sidebar-why { font-size: 0.8em; }
    row.duo-current { background: @accent_bg_color; color: @accent_fg_color; }
    """

    def esc(text):
        return GLib.markup_escape_text(text)

    def icon(name, size=32):
        return Gtk.Image(icon_name=name, pixel_size=size, valign=Gtk.Align.CENTER)

    def unavailable_row(title, desc, why, icon_name):
        """A greyed card: what MyASUS does, and why it is not done here."""
        row = Adw.ActionRow(title=esc(title), subtitle_lines=0,
                            subtitle=f"{esc(desc)}\n<i>{esc(why)}</i>" if desc else f"<i>{esc(why)}</i>")
        row.add_prefix(icon(icon_name))
        row.add_suffix(Gtk.Label(label="Not available", valign=Gtk.Align.CENTER,
                                 css_classes=["caption", "dim-label"]))
        row.set_sensitive(False)
        return row

    def card_group(title="", desc="", available=True):
        group = Adw.PreferencesGroup(title=esc(title), description=esc(desc))
        group.add_css_class("duo-card")
        if not available:
            group.add_css_class("duo-off")
        return group

    def tabbed(stack_pages):
        """MyASUS's tab strip over a page: [(tab, title, widget)]. Text only,
        as MyASUS has them, so a Gtk.StackSwitcher rather than libadwaita's
        icon-and-label one."""
        stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        for tab, title, widget in stack_pages:
            stack.add_titled(widget, tab, title)
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER, margin_top=6,
                                     margin_bottom=6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(switcher)
        box.append(stack)
        return box, stack

    class Window(Adw.ApplicationWindow):
        def __init__(self, app, model):
            super().__init__(application=app, title=TITLE)
            self.model = model
            self.set_default_size(1120, 840)
            self.set_size_request(360, 480)
            self.seen = -1
            self.syncing = False       # rows being set from the model, not by a click
            self.busy = set()          # features with an action in flight
            self.theme = ""
            self.errors_only = False
            self.knob_rows = {}
            self.knob_shown = {}       # the value each entry row last showed
            self.backlight_names = [n for n, _p in dm.backlights()]

            provider = Gtk.CssProvider()
            provider.load_from_data(CSS, len(CSS))
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
            self.tabs = {}
            self.titles = {}
            builders = {"home": self.home_page, "device": self.device_page,
                        "screens": self.screens_page, "diagnosis": self.diagnosis_page,
                        "update": self.update_page, "settings": self.settings_page}
            for entry in SIDEBAR:
                if entry and not entry[3]:
                    self.stack.add_named(builders[entry[0]](), entry[0])
                    self.titles[entry[0]] = entry[1]

            self.toasts = Adw.ToastOverlay(child=self.stack)
            self.hm_banner = Adw.Banner(
                title="This config is written by home-manager: change the zenduo.* options "
                      "there. The settings here are read-only.")
            self.page_title = Adw.WindowTitle(title=TITLE)
            content_header = Adw.HeaderBar(title_widget=self.page_title)
            content = Adw.ToolbarView()
            content.add_top_bar(content_header)
            content.add_top_bar(self.hm_banner)
            content.set_content(self.toasts)

            sidebar = Adw.ToolbarView()
            sidebar_header = Adw.HeaderBar(
                title_widget=Adw.WindowTitle(title=TITLE, subtitle="for Linux"))
            sidebar.add_top_bar(sidebar_header)
            sidebar.set_content(Gtk.ScrolledWindow(child=self.sidebar_list(),
                                                   hscrollbar_policy=Gtk.PolicyType.NEVER))

            split = Adw.NavigationSplitView(
                sidebar=Adw.NavigationPage(child=sidebar, title=TITLE),
                content=Adw.NavigationPage(child=content, title=TITLE),
                min_sidebar_width=230, max_sidebar_width=260)
            self.split = split
            self.set_content(split)
            breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
            breakpoint_.add_setter(split, "collapsed", True)
            self.add_breakpoint(breakpoint_)

            self.stack.connect("notify::visible-child", lambda *_: self.on_page())
            keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                           action=Gtk.CallbackAction.new(self.on_escape)))
            self.add_controller(keys)

            GLib.timeout_add(400, self.tick)
            threading.Thread(target=self.theme_loop, name="duo-gui-theme", daemon=True).start()

        def on_escape(self, *_):
            self.close()
            return True

        # ── navigation ────────────────────────────────────────────────────────

        def sidebar_list(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.sidebar_rows = {}
            listbox = None
            for entry in (None,) + SIDEBAR + (None,):
                if entry is None:
                    if listbox is not None:
                        box.append(listbox)
                        box.append(Gtk.Separator(margin_start=12, margin_end=12))
                    # No GTK selection: it follows keyboard focus, which lands on
                    # Home when the window opens whatever page it opens on. The
                    # current page is marked like MyASUS's, solid blue.
                    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                    listbox.add_css_class("navigation-sidebar")
                    listbox.connect("row-activated", self.on_sidebar)
                    continue
                name, title, icon_name, why = entry
                line = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
                line.append(icon(icon_name, 16))
                words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                words.append(Gtk.Label(label=title, xalign=0))
                if why:
                    note = Gtk.Label(label=why, xalign=0, wrap=True, max_width_chars=24)
                    note.add_css_class("duo-sidebar-why")
                    note.add_css_class("dim-label")
                    words.append(note)
                line.append(words)
                row = Gtk.ListBoxRow(child=line, tooltip_text=why or None)
                row.page = name
                row.set_sensitive(not why)
                listbox.append(row)
                self.sidebar_rows[name] = row
            box.remove(box.get_last_child())   # no separator after the last group
            return box

        def on_sidebar(self, _listbox, row):
            self.show_page(row.page)
            self.split.set_show_content(True)

        def show_page(self, page, tab=""):
            self.stack.set_visible_child_name(page)
            if tab and page in self.tabs:
                self.tabs[page].set_visible_child_name(tab)

        def on_page(self):
            page = self.stack.get_visible_child_name()
            self.page_title.set_title(self.titles.get(page, TITLE))
            for name, row in self.sidebar_rows.items():
                if name == page:
                    row.add_css_class("duo-current")
                else:
                    row.remove_css_class("duo-current")
            self.sync(force=True)

        def current(self):
            page = self.stack.get_visible_child_name()
            tab = self.tabs[page].get_visible_child_name() if page in self.tabs else ""
            return page, tab

        # ── doing things ──────────────────────────────────────────────────────

        def act(self, fn, *args, feature=None, then=None):
            """Run a Model action off the GTK thread and toast what it printed."""
            if feature:
                self.busy.add(feature)

            def work():
                try:
                    result = fn(*args)
                except Exception as e:  # an action must never take the window down
                    result = dm.Result(False, getattr(fn, "__name__", "action"), repr(e))
                GLib.idle_add(self.done, result, feature, then)
            threading.Thread(target=work, name="duo-gui-action", daemon=True).start()

        def done(self, result, feature, then):
            self.busy.discard(feature)
            if show_result(result):
                self.toast(result)
            if then is not None:
                then(result)
            self.sync(force=True)
            return False

        def toast(self, result, button=None):
            text = result.summary if result.ok else f"{result.title}: {result.summary}"
            toast = Adw.Toast(title=esc(text), timeout=4 if result.ok else 8)
            if button is not None:
                label, callback = button
                toast.set_button_label(label)
                toast.connect("button-clicked", lambda *_: callback())
            elif not result.ok and len(result.lines) > 1:
                toast.set_button_label("Details")
                toast.connect("button-clicked", lambda *_: self.details(result))
            self.toasts.add_toast(toast)

        def copy(self, view, what):
            buf = view.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
            if not text:
                self.toast(dm.Result(False, "Copy", "nothing to copy yet"))
                return
            self.get_clipboard().set(text + "\n")
            self.toast(dm.Result(True, "Copy", f"Copied {len(text.splitlines())} {what}"))

        def open_url(self, url):
            Gtk.UriLauncher(uri=url).launch(self, None, None, None)

        def launch(self, argv, what):
            """Open another program (GNOME Settings, Software): not a duo action,
            so it does not go through the Model."""
            try:
                Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
            except GLib.Error as e:
                self.toast(dm.Result(False, what, e.message))

        def details(self, result):
            dialog = Adw.AlertDialog(heading=result.title, body=result.output)
            dialog.add_response("close", "Close")
            dialog.present(self)

        def button(self, label, fn, *args, tooltip=""):
            b = Gtk.Button(label=label, tooltip_text=tooltip or None)
            b.connect("clicked", lambda *_: self.act(fn, *args))
            return b

        def icon_button(self, icon_name, tooltip, callback):
            b = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: callback())
            return b

        def radio_rows(self, group, options, on_pick):
            """MyASUS's radio list inside a card: [(value, title, subtitle)];
            returns {value: CheckButton}."""
            buttons, first = {}, None
            for value, title, subtitle in options:
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                if first is None:
                    first = check
                else:
                    check.set_group(first)
                check.connect("toggled", lambda b, v=value: b.get_active() and on_pick(v))
                row = Adw.ActionRow(title=esc(title), subtitle=esc(subtitle),
                                    activatable_widget=check)
                row.add_prefix(check)
                group.add(row)
                buttons[value] = check
            return buttons

        # ── Home ──────────────────────────────────────────────────────────────

        def home_page(self):
            page = Adw.PreferencesPage()
            hero = Adw.PreferencesGroup()
            title = Gtk.Label(label="ASUS Zenbook Duo", xalign=0)
            title.add_css_class("duo-hero")
            self.hero_model = Gtk.Label(xalign=0, css_classes=["dim-label"])
            hero.add(title)
            hero.add(self.hero_model)
            page.add(hero)

            tiles = Adw.PreferencesGroup()
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12,
                               row_spacing=12, homogeneous=True, min_children_per_line=2,
                               max_children_per_line=4)
            self.tiles = {}
            for key, title_text, _value, icon_name in home_tiles(dm.Glance()):
                tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                tile.add_css_class("card")
                tile.add_css_class("duo-tile")
                tile.append(Gtk.Image(icon_name=icon_name, pixel_size=28, halign=Gtk.Align.START))
                tile.append(Gtk.Label(label=title_text, xalign=0, css_classes=["dim-label"]))
                value = Gtk.Label(label="…", xalign=0, wrap=True, max_width_chars=22)
                value.add_css_class("duo-tile-value")
                tile.append(value)
                flow.append(tile)
                self.tiles[key] = value
            tiles.add(flow)
            page.add(tiles)

            machine = Adw.PreferencesGroup(title="This machine")
            self.glance_rows = {}
            for key, row_title, _value in glance_rows(dm.Glance()):
                row = Adw.ActionRow(title=row_title, subtitle="…")
                row.add_css_class("property")
                machine.add(row)
                self.glance_rows[key] = row
            self.override_row = Adw.ActionRow(title="Manual override", visible=False)
            self.override_row.add_css_class("property")
            machine.add(self.override_row)
            page.add(machine)

            checks = card_group("System Diagnosis",
                                "Checks the hardware and this setup, the way MyASUS's "
                                "one-click diagnosis does. It changes nothing.")
            row = Adw.ActionRow(title="One-click diagnosis", subtitle="duo doctor")
            row.add_prefix(icon("system-run-symbolic"))
            run = Gtk.Button(label="Checkup", valign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
            run.connect("clicked", lambda *_: (self.show_page("diagnosis", "tool"),
                                               self.model.run_doctor_async()))
            row.add_suffix(run)
            checks.add(row)
            page.add(checks)

            problems = Adw.PreferencesGroup(title="Recent problems")
            clear = self.icon_button("edit-clear-all-symbolic", "Hide these (the journal is kept)",
                                     self.on_clear)
            problems.set_header_suffix(clear)
            self.problems = Gtk.Label(xalign=0, wrap=True, selectable=True,
                                      wrap_mode=Pango.WrapMode.WORD_CHAR)
            self.problems.add_css_class("monospace")
            self.problems.add_css_class("caption")
            problems.add(self.problems)
            page.add(problems)
            return page

        def on_clear(self):
            hidden = self.model.clear_journal()
            self.toast(dm.Result(True, "clear", f"{hidden} lines hidden; Diagnostic History → "
                                                "Show all brings them back"))

        def sync_home(self):
            g = self.model.glance
            self.hero_model.set_text(g.model or "")
            for key, _title, value, _icon in home_tiles(g):
                if self.tiles[key].get_text() != value:
                    self.tiles[key].set_text(value)
            for key, _title, value in glance_rows(g):
                row = self.glance_rows.get(key)
                if row is not None and row.get_subtitle() != value:
                    row.set_subtitle(esc(value))
            self.override_row.set_visible(bool(g.override))
            if g.override:
                self.override_row.set_subtitle("a hand-picked panel set holds until the next dock or undock")
            self.problems.set_text(problems_text(self.model.recent_problems()))

        # ── Device Settings ───────────────────────────────────────────────────

        def device_page(self):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            quick = Adw.PreferencesPage(vexpand=False)
            title, desc, why = QUICK_SETTING
            group = Adw.PreferencesGroup()
            group.add(unavailable_row(title, desc, why, "view-list-symbolic"))
            quick.add(group)
            quick.set_size_request(-1, 110)
            outer.append(quick)
            box, stack = tabbed([(tab, tab_title, self.cards_page(tab))
                                 for tab, tab_title in TABS["device"]])
            self.tabs["device"] = stack
            outer.append(box)
            return outer

        def cards_page(self, tab):
            page = Adw.PreferencesPage()
            for card in cards(tab):
                page.add(self.card(card))
            return page

        def card(self, card):
            if not card.available:
                group = card_group(available=False)
                group.add(unavailable_row(card.title, card.desc, card.why, card.icon))
                return group
            group = card_group(card.title, card.desc)
            how = card.how
            if how == "fan-mode":
                self.fan_buttons = self.radio_rows(
                    group, [(key, title, desc) for key, title, _v, desc in FAN_MODES],
                    lambda key: None if self.syncing else self.act(self.model.fan_profile, key))
            elif how == "fn-key":
                self.fn_buttons = self.radio_rows(
                    group, FN_KEYS,
                    lambda on: None if self.syncing else self.act(self.model.fn_lock, on))
            elif how == "kb-level":
                row = Adw.ActionRow(title="Level")
                row.add_prefix(icon(card.icon))
                self.kb_buttons, first = [], None
                for level in range(4):
                    b = Gtk.ToggleButton(label=("Off", "Low", "Medium", "High")[level])
                    if first is None:
                        first = b
                    else:
                        b.set_group(first)
                    b.connect("toggled", self.on_kb_level, level)
                    self.kb_buttons.append(b)
                row.add_suffix(linked(*self.kb_buttons))
                group.add(row)
            elif how == "touchpad":
                row = Adw.ActionRow(title=esc("Mouse & Touchpad settings"), activatable=True)
                row.add_prefix(icon(card.icon))
                row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
                row.connect("activated", lambda *_: self.launch(
                    ["gnome-control-center", "mouse"], "GNOME Settings"))
                group.add(row)
            elif how.startswith("knob:"):
                knob = dm.KNOB_BY_KEY[how[len("knob:"):]]
                row = self.knob_row(knob)
                row.add_prefix(icon(card.icon))
                group.add(row)
            elif how.startswith("feature:"):
                name = how[len("feature:"):]
                row = Adw.SwitchRow(title="On")
                row.add_prefix(icon(card.icon))
                row.connect("notify::active", self.on_card_feature, name)
                self.card_features = getattr(self, "card_features", {})
                self.card_features[name] = row
                group.add(row)
            return group

        def on_card_feature(self, row, _pspec, name):
            if self.syncing:
                return
            fn = self.model.enable if row.get_active() else self.model.disable
            self.act(fn, name, feature=name)

        def on_kb_level(self, button, level):
            if self.syncing or not button.get_active():
                return
            self.act(self.model.kb_backlight, level)

        def sync_device(self):
            g = self.model.glance
            mode = fan_mode_of(g.fan_mode)
            for key, b in self.fan_buttons.items():
                b.set_sensitive(bool(g.fan_mode))
                if key == mode:
                    b.set_active(True)
            self.fn_buttons[g.fn_lock].set_active(True)
            if 0 <= g.kb_level < len(self.kb_buttons):
                self.kb_buttons[g.kb_level].set_active(True)
            for name, row in getattr(self, "card_features", {}).items():
                if name not in self.busy and name == "speaker-dsp":
                    row.set_active(g.dsp_installed)

        # ── Screens ───────────────────────────────────────────────────────────

        def screens_page(self):
            page = Adw.PreferencesPage()
            m = self.model
            now = card_group("Screens", "A hand-picked panel set holds until the keyboard "
                                        "docks or undocks.")
            row = Adw.ActionRow(title="Panels")
            row.add_prefix(icon("video-display-symbolic"))
            row.add_suffix(linked(*(self.button(label, m.panels, verb)
                                    for label, verb in SCREEN_BUTTONS)))
            now.add(row)
            row = Adw.ActionRow(title="Layout", subtitle="remembered per set of monitors, like Win+P")
            row.add_prefix(icon("view-dual-symbolic"))
            row.add_suffix(linked(*(self.button(label, m.layout, verb)
                                    for label, verb in LAYOUT_BUTTONS)))
            now.add(row)
            row = Adw.ActionRow(title="Brightness", subtitle="both panels together")
            row.add_prefix(icon("display-brightness-symbolic"))
            row.add_suffix(linked(*(self.button(label, m.brightness, verb)
                                    for label, verb in BRIGHTNESS_BUTTONS)))
            now.add(row)
            page.add(now)
            groups = {}
            for title, desc in KNOB_GROUPS:
                groups[title] = card_group(title + (" settings" if title == "Screens" else ""), desc)
                page.add(groups[title])
            for knob in dm.KNOBS:
                if knob_page(knob) == "screens":
                    groups[knob_section(knob)].add(self.knob_row(knob))
            for title, desc, icon_name, why in SCREENS_TODO:
                group = card_group(available=False)
                group.add(unavailable_row(title, desc, why, icon_name))
                page.add(group)
            return page

        # ── knobs (Device Settings, Screens, Extras) ──────────────────────────

        def knob_row(self, knob):
            names = self.backlight_names
            kind = knob_kind(knob, names)
            _group, title, subtitle = knob_text(knob)
            if kind == "switch":
                row = Adw.SwitchRow(title=title, subtitle=subtitle)
                row.connect("notify::active", self.on_switch_knob, knob.key)
            elif kind == "choice":
                choices = knob_choices(knob, names)
                row = Adw.ComboRow(title=title, subtitle=subtitle,
                                   model=Gtk.StringList.new([choice_label(c, knob) for c in choices]))
                row.choices = choices
                row.connect("notify::selected", self.on_choice_knob, knob.key)
            else:
                row = Adw.EntryRow(title=title, show_apply_button=True)
                row.connect("apply", self.on_entry_knob, knob.key)
            if kind != "entry":
                row.set_subtitle_lines(0)      # wrap, never ellipsize
            row.set_tooltip_text(f"{knob.key} in zenduo.conf")
            self.knob_rows[knob.key] = row
            return row

        def set_knob(self, key, value):
            def then(result):
                follow = after_knob(key, value, result)
                if follow and self.model.unit(follow[1]) and self.model.unit(follow[1]).state == "running":
                    daemon = follow[1]
                    self.toast(dm.Result(True, "", "Takes effect when the background "
                                                   "service restarts"),
                               button=("Restart", lambda: self.act(self.model.restart, daemon,
                                                                    feature=daemon)))
            self.act(self.model.config_set, key, value, then=then)

        def on_switch_knob(self, row, _pspec, key):
            if not self.syncing:
                self.set_knob(key, "1" if row.get_active() else "0")

        def on_choice_knob(self, row, _pspec, key):
            if self.syncing:
                return
            i = row.get_selected()
            if 0 <= i < len(row.choices):
                self.set_knob(key, row.choices[i])

        def on_entry_knob(self, row, key):
            value = row.get_text().strip()
            self.knob_shown[key] = value
            self.set_knob(key, value)

        def sync_knobs(self):
            config, hm = self.model.config, self.model.config_hm
            self.hm_banner.set_revealed(hm)
            for knob in dm.KNOBS:
                row = self.knob_rows[knob.key]
                row.set_sensitive(not hm and bool(config))
                value = config.get(knob.key, "")
                kind = knob_kind(knob, self.backlight_names)
                if kind == "switch":
                    row.set_active(value == "1")
                elif kind == "choice":
                    if value and value not in row.choices and knob.kind == "int":
                        # Set by hand (`duo config set`) to something the list
                        # does not offer: show it rather than a wrong choice.
                        row.choices.append(value)
                        row.get_model().append(choice_label(value, knob))
                    if value in row.choices:
                        row.set_selected(row.choices.index(value))
                elif row.get_text() == self.knob_shown.get(knob.key, "") \
                        and not row.has_focus():
                    # Only an untouched entry follows the file: a half-typed
                    # value must survive the next refresh.
                    row.set_text(value)
                    self.knob_shown[knob.key] = value

        # ── System Diagnosis ──────────────────────────────────────────────────

        def diagnosis_page(self):
            box, stack = tabbed([("tool", "Diagnostic Tool", self.doctor_view_box()),
                                 ("history", "Diagnostic History", self.logs_view_box())])
            stack.connect("notify::visible-child", lambda *_: self.sync(force=True))
            self.tabs["diagnosis"] = stack
            return box

        def logs_view_box(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            only = Gtk.ToggleButton(label="Errors only")
            only.connect("toggled", self.on_errors_only)
            show_all = Gtk.Button(label="Show all")
            show_all.connect("clicked", lambda *_: (self.model.show_all(), self.sync(True)))
            title = Gtk.Label(label="What the background services logged (duo log)",
                              xalign=0, hexpand=True)
            title.add_css_class("dim-label")
            copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy these lines")
            copy.connect("clicked", lambda *_: self.copy(self.log_view, "log lines"))
            for w in (title, only, show_all, copy):
                bar.append(w)
            box.append(bar)
            self.log_view, self.log_scroll = monospace_view()
            box.append(self.log_scroll)
            return box

        def on_errors_only(self, button):
            self.errors_only = button.get_active()
            self.sync(force=True)

        def sync_logs(self):
            adj = self.log_scroll.get_vadjustment()
            at_end = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 4
            text = journal_text(self.model.journal(), self.errors_only,
                                error=self.model.journal_error)
            buf = self.log_view.get_buffer()
            if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) != text:
                buf.set_text(text)
                if at_end:
                    GLib.idle_add(lambda: adj.set_value(adj.get_upper()) and False)

        def doctor_view_box(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            self.doctor_status = Gtk.Label(
                label="One-click diagnosis: a read-only check of the hardware and this setup",
                xalign=0, hexpand=True)
            self.doctor_status.add_css_class("dim-label")
            self.doctor_spinner = Gtk.Spinner()
            self.doctor_button = Gtk.Button(label="Checkup")
            self.doctor_button.add_css_class("suggested-action")
            self.doctor_button.add_css_class("pill")
            self.doctor_button.connect("clicked", lambda *_: self.model.run_doctor_async())
            copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy the report")
            copy.connect("clicked", lambda *_: self.copy(self.doctor_view, "doctor lines"))
            for w in (self.doctor_status, self.doctor_spinner, copy, self.doctor_button):
                bar.append(w)
            box.append(bar)
            self.doctor_view, _scroll = monospace_view()
            buf = self.doctor_view.get_buffer()
            for name, colour in (("ok", "#26a269"), ("warn", "#c88800"), ("fail", "#e01b24"),
                                 ("must", "#e01b24"), ("section", None), ("summary", None)):
                tag = buf.create_tag(name)
                if colour:
                    tag.set_property("foreground", colour)
                if name in ("section", "summary", "must"):
                    tag.set_property("weight", 700)
            box.append(_scroll)
            self.doctor_shown = None
            return box

        def sync_doctor(self):
            m = self.model
            self.doctor_spinner.set_spinning(m.doctor_running)
            self.doctor_button.set_sensitive(not m.doctor_running)
            if m.doctor_running:
                self.doctor_status.set_text("running duo doctor…")
            elif m.doctor_ok is not None:
                when = time.strftime("%H:%M:%S", time.localtime(m.doctor_at))
                self.doctor_status.set_text(f"{'no problems' if m.doctor_ok else 'problems found'}"
                                            f" · ran at {when}")
            if m.doctor_lines is self.doctor_shown:
                return
            self.doctor_shown = m.doctor_lines
            buf = self.doctor_view.get_buffer()
            buf.set_text("")
            marks = {"ok": "✓ ", "warn": "! ", "fail": "✗ ", "must": "✗ ", "info": "· "}
            for line in m.doctor_lines:
                text = marks.get(line.kind, "") + line.text + "\n"
                if line.kind == "section":
                    text = "\n" + line.text + "\n"
                tags = [line.kind] if line.kind in ("ok", "warn", "fail", "must", "section",
                                                    "summary") else []
                buf.insert_with_tags_by_name(buf.get_end_iter(), text, *tags)

        # ── System Update ─────────────────────────────────────────────────────

        def update_page(self):
            page = Adw.PreferencesPage()
            software = card_group("Software updates",
                                  "Ubuntu's updates, including the kernel and the drivers "
                                  "this laptop needs.")
            row = Adw.ActionRow(title="Check for updates", subtitle="opens GNOME Software",
                                activatable=True)
            row.add_prefix(icon("software-update-available-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            row.connect("activated", lambda *_: self.launch(
                ["gnome-software", "--mode=updates"], "GNOME Software"))
            software.add(row)
            page.add(software)
            title, desc, why = SYSTEM_UPDATE
            group = card_group(available=False)
            group.add(unavailable_row(title, desc, why, "system-software-install-symbolic"))
            page.add(group)
            return page

        # ── Settings: background services, extras, about ──────────────────────

        def settings_page(self):
            box, stack = tabbed([("services", "Background services", self.features_page()),
                                 ("extras", "Extras", self.extras_page()),
                                 ("about", "About", self.about_page())])
            self.tabs["settings"] = stack
            return box

        def features_page(self):
            page = Adw.PreferencesPage()
            self.feature_rows = {}
            daemons = Adw.PreferencesGroup(
                title="Background services",
                description="systemd user units; the switch is duo enable / duo disable")
            speakers = Adw.PreferencesGroup(title="Speakers")
            system = Adw.PreferencesGroup(
                title="Installed by the system installer",
                description="Changed with sudo ./install.sh --system and the flag shown")
            for f in dm.FEATURES:
                row = Adw.ActionRow(title=f.name, subtitle=f.desc, subtitle_lines=2)
                state = Gtk.Label(valign=Gtk.Align.CENTER)
                state.add_css_class("caption")
                row.add_suffix(state)
                switch = None
                if f.scope == "system":
                    row.set_subtitle(f"{f.desc}\n--{f.flag} / --no-{f.flag}")
                    system.add(row)
                else:
                    if f.scope == "user":
                        row.add_suffix(self.icon_button(
                            "view-refresh-symbolic", "Restart (or run it now)",
                            lambda name=f.name: self.act(self.model.restart, name, feature=name)))
                    switch = Gtk.Switch(valign=Gtk.Align.CENTER)
                    switch.connect("notify::active", self.on_feature_switch, f.name)
                    row.add_suffix(switch)
                    (speakers if f.scope == "dsp" else daemons).add(row)
                self.feature_rows[f.name] = (row, state, switch)
            page.add(daemons)
            page.add(speakers)
            page.add(system)
            return page

        def on_feature_switch(self, switch, _pspec, name):
            if self.syncing:
                return
            fn = self.model.enable if switch.get_active() else self.model.disable
            self.act(fn, name, feature=name)

        def sync_features(self):
            g = self.model.glance
            for f in dm.FEATURES:
                _row, label, switch = self.feature_rows[f.name]
                if f.scope == "dsp":
                    text, level, on = ("installed", "ok", True) if g.dsp_installed \
                        else ("not installed", "off", False)
                else:
                    unit = self.model.unit(f.name)
                    text, level = feature_state(unit)
                    on = bool(unit and unit.enabled)
                    if f.scope == "system" and unit is None:
                        text, level = "", "off"
                label.set_text(text)
                for css in LEVEL_CSS.values():
                    label.remove_css_class(css)
                label.add_css_class(LEVEL_CSS.get(level, "dim-label"))
                if switch is not None and f.name not in self.busy:
                    switch.set_active(on)

        def extras_page(self):
            page = Adw.PreferencesPage()
            title, desc = EXTRAS
            group = Adw.PreferencesGroup(title=title, description=desc)
            for knob in dm.KNOBS:
                if knob_page(knob) == "extras":
                    group.add(self.knob_row(knob))
            page.add(group)
            look = Adw.PreferencesGroup(title="Style")
            self.dark_row = Adw.SwitchRow(title="Dark style", subtitle="what F8 toggles")
            self.dark_row.connect("notify::active", self.on_dark)
            look.add(self.dark_row)
            page.add(look)
            return page

        def on_dark(self, row, _pspec):
            if self.syncing:
                return
            style = "dark" if row.get_active() else "light"
            self.theme = style
            self.act(self.model.theme, style)

        def theme_loop(self):
            while True:
                r = self.model.theme()
                if r.ok and r.summary in ("dark", "light") and r.summary != self.theme:
                    self.theme = r.summary
                    GLib.idle_add(self.sync, True)
                time.sleep(THEME_SECONDS)

        def about_page(self):
            page = Adw.PreferencesPage()
            info = Adw.PreferencesGroup(
                title="linux-on-zenbook-duo",
                description="Dual screens, keyboard, audio and power for the ASUS Zenbook Duo "
                            "(2024) on Linux.")
            for title, value in (("Version", self.model.version_text),
                                 ("License", "MIT"),
                                 ("In a terminal", "duo, or duo --help for every command")):
                row = Adw.ActionRow(title=title, subtitle=value)
                row.add_css_class("property")
                info.add(row)
            page.add(info)
            links = Adw.PreferencesGroup(title="Links")
            for title, subtitle, url in ABOUT_LINKS:
                row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
                row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
                row.connect("activated", lambda _row, u=url: self.open_url(u))
                links.add(row)
            page.add(links)
            return page

        # ── refreshing ────────────────────────────────────────────────────────

        def tick(self):
            if self.model.version != self.seen:
                self.sync()
            return True

        def sync(self, force=False):
            self.seen = self.model.version
            self.syncing = True
            try:
                page, tab = self.current()
                self.sync_home()
                self.sync_device()
                self.sync_features()
                self.sync_knobs()
                if self.theme:
                    self.dark_row.set_active(self.theme == "dark")
                if page == "diagnosis" and tab == "history":
                    self.sync_logs()
                if page == "diagnosis" and tab == "tool":
                    self.sync_doctor()
            finally:
                self.syncing = False
            return False

    flags = getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", Gio.ApplicationFlags.FLAGS_NONE)

    class App(Adw.Application):
        def __init__(self, page=""):
            super().__init__(application_id=APP_ID, flags=flags)
            self.window = None
            self.model = None
            self.page = page
            toggle = Gio.SimpleAction.new("toggle", None)
            toggle.connect("activate", self.on_toggle)
            self.add_action(toggle)
            quit_ = Gio.SimpleAction.new("quit", None)
            quit_.connect("activate", lambda *_: self.window and self.window.close())
            self.add_action(quit_)
            self.set_accels_for_action("app.quit", ["<Control>q", "<Control>w"])

        def do_activate(self):
            self.ensure_window().present()

        def on_toggle(self, *_):
            w = self.window
            if w is not None and toggle_closes(w.get_visible(), w.is_active()):
                w.close()
            else:
                self.ensure_window().present()

        def ensure_window(self):
            if self.window is None:
                self.model = dm.Model()
                self.window = Window(self, self.model)
                self.window.connect("close-request", self.on_close)
                self.window.show_page(*page_target(self.page or "home"))
                self.window.on_page()
                # The first refresh shells out to systemctl and sudo-free probes;
                # the window draws first and fills in a moment later.
                threading.Thread(target=self.model.start, name="duo-gui-start",
                                 daemon=True).start()
            return self.window

        def on_close(self, *_):
            if self.model is not None:
                self.model.stop()
            self.window = None
            return False

    return App


def missing_gtk(error):
    return (f"duo gui: needs GTK 4 and libadwaita (1.5 or newer) for python: {error}\n"
            "  Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            "  (or re-run sudo ./install.sh --system, which installs them)\n"
            "  The same things are in the terminal screen: duo")


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.split("\n\n")[1])
        return 0
    try:
        toggle, page = parse_args(argv)
    except ValueError:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 64
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib, Gtk, Pango
    except (ImportError, ValueError) as e:
        print(missing_gtk(e), file=sys.stderr)
        return 1
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        print(missing_gtk(f"libadwaita is {Adw.get_major_version()}.{Adw.get_minor_version()}"),
              file=sys.stderr)
        return 1
    app = make_app(Adw, Gio, GLib, Gtk, Pango)(page)
    if toggle:
        app.register(None)
        if app.get_is_remote():
            # The window lives in another process: send it the toggle and
            # make sure the message is on the bus before this one exits.
            app.activate_action("toggle", None)
            app.get_dbus_connection().flush_sync(None)
            return 0
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
