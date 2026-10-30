#!/usr/bin/env python3
"""duo gui: the Duo's features, knobs, checks and logs in a GNOME window.

    duo gui              open the window (or raise it when it is open)
    duo gui --toggle     open it, or close it when it is the focused window:
                         what F12 runs (watch-fn, the MyASUS key)
    duo gui --page NAME  open on overview, features, settings, logs or doctor

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

# One instance per session: a second `duo gui` hands its request to the first
# over D-Bus and exits, which is what lets F12 close the window it opened. The
# desktop file is named after it so GNOME puts the window under that launcher.
APP_ID = "io.github.zenduo.Duo"
TITLE = "Zenbook Duo"
REPO_URL = "https://github.com/Toldry/linux-on-zenbook-duo"

LOG_LINES = 400                 # journal lines the Logs page keeps
THEME_SECONDS = 2.0             # F7 flips the style behind the window's back

SCREEN_BUTTONS = (("Top", "top"), ("Bottom", "bottom"), ("Both", "both"),
                  ("Toggle bottom", "toggle"))
LAYOUT_BUTTONS = (("Laptop", "laptop"), ("External", "external"), ("Extend", "extend"),
                  ("Mirror", "mirror"))
BRIGHTNESS_BUTTONS = (("−", "down"), ("+", "up"), ("Match", "match"))

PAGES = ("overview", "features", "settings", "logs", "doctor")

# The Settings page, in the order it is shown: (title, description).
KNOB_GROUPS = (
    ("Screens", ""),
    ("Keyboard shortcuts", ""),
    ("Brightness and keyboard light", ""),
    ("Battery", "Stops charging at this level, which makes the battery last longer. "
                "20 to 100; empty charges to 100 %."),
)

# What each knob is called on the Settings page: (group, title, subtitle).
# duo_model's descriptions are written for `duo config`; these are written for
# someone who has not read the code. The key itself is the row's tooltip.
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
    "F7_TERMINAL": ("Keyboard shortcuts", "F7 opens a terminal",
                    "Instead of GNOME's display switcher. F7 types Super+P, so that shortcut "
                    "does the same; Ctrl+Alt+T keeps working too."),
    "F8_DARK_MODE": ("Keyboard shortcuts", "F8 switches dark mode",
                     "Light and dark style, as Settings → Appearance switches it."),
    "F12_GUI": ("Keyboard shortcuts", "F12 opens this window",
                "Press F12 again while the window is in front to close it."),
    "BACKLIGHT_SOURCE": ("Brightness and keyboard light", "Brightness is copied from",
                         "The screen the other one follows; intel_backlight is the top screen."),
    "BACKLIGHT_TARGET": ("Brightness and keyboard light", "Brightness is copied to",
                         "Automatic finds the bottom screen."),
    "KB_BACKLIGHT_RESTORE": ("Brightness and keyboard light", "Restore the keyboard light",
                             "The keyboard forgets its light level when it is docked, undocked "
                             "or the laptop wakes up; this sets it back."),
    "BATTERY_LIMIT": ("Battery", "Charge limit in percent", ""),
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
    return "entry"


def knob_choices(knob, backlight_names):
    """The values a choice row offers, "" meaning "empty" (auto-detect)."""
    values = list(knob.values) if knob.kind == "enum" else list(backlight_names)
    if knob.empty_ok:
        values.insert(0, "")
    return values


def choice_label(value):
    return value if value else "(automatic)"


def knob_text(knob):
    """(group, title, subtitle); a knob nobody wrote words for yet still shows."""
    return KNOB_TEXT.get(knob.key, (KNOB_GROUPS[-1][0], knob.key, knob.desc))


def knob_section(knob):
    return knob_text(knob)[0]


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
        elif a == "--page" and args and args[0] in PAGES:
            page = args.pop(0)
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

    class Window(Adw.ApplicationWindow):
        def __init__(self, app, model):
            super().__init__(application=app, title=TITLE)
            self.model = model
            self.set_default_size(760, 820)
            self.seen = -1
            self.syncing = False       # rows being set from the model, not by a click
            self.busy = set()          # features with an action in flight
            self.theme = ""
            self.errors_only = False

            self.toasts = Adw.ToastOverlay()
            self.stack = Adw.ViewStack()
            self.toasts.set_child(self.stack)
            header = Adw.HeaderBar()
            header.set_title_widget(Adw.ViewSwitcher(stack=self.stack,
                                                     policy=Adw.ViewSwitcherPolicy.WIDE))
            about = Gtk.Button(icon_name="help-about-symbolic",
                               tooltip_text="About, and the project on GitHub")
            about.connect("clicked", lambda *_: self.about())
            header.pack_end(about)
            view = Adw.ToolbarView()
            view.add_top_bar(header)
            view.set_content(self.toasts)
            self.set_content(view)

            self.stack.add_titled_with_icon(self.overview_page(), "overview", "Overview",
                                            "computer-symbolic")
            self.stack.add_titled_with_icon(self.features_page(), "features", "Features",
                                            "application-x-addon-symbolic")
            self.stack.add_titled_with_icon(self.settings_page(), "settings", "Settings",
                                            "emblem-system-symbolic")
            self.stack.add_titled_with_icon(self.logs_page(), "logs", "Logs",
                                            "text-x-generic-symbolic")
            self.stack.add_titled_with_icon(self.doctor_page(), "doctor", "Doctor",
                                            "dialog-information-symbolic")
            self.stack.connect("notify::visible-child", lambda *_: self.sync(force=True))

            keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                           action=Gtk.CallbackAction.new(self.on_escape)))
            self.add_controller(keys)

            GLib.timeout_add(400, self.tick)
            threading.Thread(target=self.theme_loop, name="duo-gui-theme", daemon=True).start()

        def on_escape(self, *_):
            self.close()
            return True

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
            toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=4 if result.ok else 8)
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

        def about(self):
            dialog = Adw.AboutDialog(
                application_name=TITLE, application_icon="preferences-desktop-display",
                version=self.model.version_text, website=REPO_URL,
                issue_url=REPO_URL + "/issues", license_type=Gtk.License.MIT_X11,
                comments="Dual screens, keyboard, audio and power for the ASUS Zenbook Duo "
                         "(2024) on Linux. The same things are in a terminal: duo")
            dialog.add_link("Source code on GitHub", REPO_URL)
            dialog.present(self)

        def details(self, result):
            dialog = Adw.AlertDialog(heading=result.title, body=result.output)
            dialog.add_response("close", "Close")
            dialog.present(self)

        def button(self, label, fn, *args, tooltip=""):
            b = Gtk.Button(label=label, tooltip_text=tooltip or None)
            b.connect("clicked", lambda *_: self.act(fn, *args))
            return b

        def icon_button(self, icon, tooltip, callback):
            b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: callback())
            return b

        # ── Overview ──────────────────────────────────────────────────────────

        def overview_page(self):
            page = Adw.PreferencesPage()
            machine = Adw.PreferencesGroup(title="This machine")
            self.glance_rows = {}
            for key, title, _value in glance_rows(dm.Glance()):
                row = Adw.ActionRow(title=title, subtitle="…")
                row.add_css_class("property")
                machine.add(row)
                self.glance_rows[key] = row
            self.override_row = Adw.ActionRow(title="Manual override", visible=False)
            self.override_row.add_css_class("property")
            machine.add(self.override_row)
            page.add(machine)

            m = self.model
            screens = Adw.PreferencesGroup(
                title="Screens",
                description="A hand-picked panel set holds until the keyboard docks or undocks.")
            row = Adw.ActionRow(title="Panels")
            row.add_suffix(linked(*(self.button(label, m.panels, verb)
                                    for label, verb in SCREEN_BUTTONS)))
            screens.add(row)
            row = Adw.ActionRow(title="Layout", subtitle="remembered per set of monitors, like Win+P")
            row.add_suffix(linked(*(self.button(label, m.layout, verb)
                                    for label, verb in LAYOUT_BUTTONS)))
            screens.add(row)
            row = Adw.ActionRow(title="Brightness", subtitle="both panels together")
            row.add_suffix(linked(*(self.button(label, m.brightness, verb)
                                    for label, verb in BRIGHTNESS_BUTTONS)))
            screens.add(row)
            page.add(screens)

            look = Adw.PreferencesGroup(title="Look and keyboard")
            self.dark_row = Adw.SwitchRow(title="Dark style", subtitle="what F8 toggles")
            self.dark_row.connect("notify::active", self.on_dark)
            look.add(self.dark_row)
            row = Adw.ActionRow(title="Keyboard backlight")
            self.kb_buttons = []
            first = None
            for level in range(4):
                b = Gtk.ToggleButton(label=str(level))
                if first is None:
                    first = b
                else:
                    b.set_group(first)
                b.connect("toggled", self.on_kb_level, level)
                self.kb_buttons.append(b)
            row.add_suffix(linked(*self.kb_buttons))
            look.add(row)
            page.add(look)

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
            footer = Adw.PreferencesGroup()
            footer.add(Gtk.LinkButton(uri=REPO_URL, label="linux-on-zenbook-duo on GitHub",
                                      halign=Gtk.Align.CENTER))
            page.add(footer)
            return page

        def on_dark(self, row, _pspec):
            if self.syncing:
                return
            style = "dark" if row.get_active() else "light"
            self.theme = style
            self.act(self.model.theme, style)

        def on_kb_level(self, button, level):
            if self.syncing or not button.get_active():
                return
            self.act(self.model.kb_backlight, level)

        def on_clear(self):
            hidden = self.model.clear_journal()
            self.toast(dm.Result(True, "clear", f"{hidden} lines hidden; Logs → Show all brings them back"))

        def sync_overview(self):
            g = self.model.glance
            for key, _title, value in glance_rows(g):
                row = self.glance_rows.get(key)
                if row is not None and row.get_subtitle() != value:
                    row.set_subtitle(GLib.markup_escape_text(value))
            self.override_row.set_visible(bool(g.override))
            if g.override:
                self.override_row.set_subtitle("a hand-picked panel set holds until the next dock or undock")
            if 0 <= g.kb_level < len(self.kb_buttons):
                self.kb_buttons[g.kb_level].set_active(True)
            if self.theme:
                self.dark_row.set_active(self.theme == "dark")
            self.problems.set_text(problems_text(self.model.recent_problems()))

        def theme_loop(self):
            while True:
                r = self.model.theme()
                if r.ok and r.summary in ("dark", "light") and r.summary != self.theme:
                    self.theme = r.summary
                    GLib.idle_add(self.sync, True)
                time.sleep(THEME_SECONDS)

        # ── Features ──────────────────────────────────────────────────────────

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

        # ── Settings ──────────────────────────────────────────────────────────

        def settings_page(self):
            page = Adw.PreferencesPage()
            self.hm_banner = Adw.PreferencesGroup(
                description="This config is written by home-manager: change the zenduo.* "
                            "options there. The values below are read-only.",
                visible=False)
            page.add(self.hm_banner)
            self.knob_rows = {}
            self.knob_shown = {}       # the value each entry row last showed
            names = self.backlight_names = [n for n, _p in dm.backlights()]
            groups = {}
            for title, desc in KNOB_GROUPS:
                groups[title] = Adw.PreferencesGroup(title=title, description=desc)
                page.add(groups[title])
            for knob in dm.KNOBS:
                kind = knob_kind(knob, names)
                _group, title, subtitle = knob_text(knob)
                if kind == "switch":
                    row = Adw.SwitchRow(title=title, subtitle=subtitle)
                    row.connect("notify::active", self.on_switch_knob, knob.key)
                elif kind == "choice":
                    choices = knob_choices(knob, names)
                    row = Adw.ComboRow(title=title, subtitle=subtitle,
                                       model=Gtk.StringList.new([choice_label(c) for c in choices]))
                    row.choices = choices
                    row.connect("notify::selected", self.on_choice_knob, knob.key)
                else:
                    row = Adw.EntryRow(title=title, show_apply_button=True)
                    row.connect("apply", self.on_entry_knob, knob.key)
                if kind != "entry":
                    row.set_subtitle_lines(0)      # wrap, never ellipsize
                row.set_tooltip_text(f"{knob.key} in zenduo.conf")
                groups[knob_section(knob)].add(row)
                self.knob_rows[knob.key] = row
            return page

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

        def sync_settings(self):
            config, hm = self.model.config, self.model.config_hm
            self.hm_banner.set_visible(hm)
            for knob in dm.KNOBS:
                row = self.knob_rows[knob.key]
                row.set_sensitive(not hm and bool(config))
                value = config.get(knob.key, "")
                kind = knob_kind(knob, self.backlight_names)
                if kind == "switch":
                    row.set_active(value == "1")
                elif kind == "choice":
                    if value in row.choices:
                        row.set_selected(row.choices.index(value))
                elif row.get_text() == self.knob_shown.get(knob.key, "") \
                        and not row.has_focus():
                    # Only an untouched entry follows the file: a half-typed
                    # value must survive the next refresh.
                    row.set_text(value)
                    self.knob_shown[knob.key] = value

        # ── Logs ──────────────────────────────────────────────────────────────

        def logs_page(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            only = Gtk.ToggleButton(label="Errors only")
            only.connect("toggled", self.on_errors_only)
            show_all = Gtk.Button(label="Show all")
            show_all.connect("clicked", lambda *_: (self.model.show_all(), self.sync(True)))
            title = Gtk.Label(label="zenduo journal (duo log)", xalign=0, hexpand=True)
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

        # ── Doctor ────────────────────────────────────────────────────────────

        def doctor_page(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            self.doctor_status = Gtk.Label(label="duo doctor: a read-only probe of the hardware",
                                           xalign=0, hexpand=True)
            self.doctor_status.add_css_class("dim-label")
            self.doctor_spinner = Gtk.Spinner()
            self.doctor_button = Gtk.Button(label="Run doctor")
            self.doctor_button.add_css_class("suggested-action")
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

        # ── refreshing ────────────────────────────────────────────────────────

        def tick(self):
            if self.model.version != self.seen:
                self.sync()
            return True

        def sync(self, force=False):
            self.seen = self.model.version
            self.syncing = True
            try:
                page = self.stack.get_visible_child_name()
                self.sync_overview()
                self.sync_features()
                self.sync_settings()
                if page == "logs":
                    self.sync_logs()
                if page == "doctor":
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
                if self.page:
                    self.window.stack.set_visible_child_name(self.page)
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
