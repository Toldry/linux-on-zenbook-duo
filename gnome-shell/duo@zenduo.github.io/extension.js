// What linux-on-zenbook-duo needs from inside GNOME Shell, offered as D-Bus
// methods on the Shell's own connection; lib/shell_ext.py calls them.
//
// SwapScreens: swap the windows of the two built-in panels, what F8 does
// under Windows (reported by the owner, 2026-10-31). Under Wayland only the
// compositor can move another program's window.
// ShowDisplaySwitcher: GNOME's own Super+P popup. Super+P is bound to
// `duo super-p` instead, so F7 (which types Super+P) can open a terminal;
// a Super+P typed by hand comes back here (lib/hotkeys.py).
// ShowOsd: the popup GNOME shows for volume and brightness, for the keys
// MyASUS answers with one (Fn+Esc, Fn+F). org.gnome.Shell.ShowOSD would do,
// but GNOME has refused it to other programs since 41.
//
//   gdbus call --session --dest org.gnome.Shell \
//     --object-path /io/github/zenduo/Shell \
//     --method io.github.zenduo.Shell.ShowOsd input-keyboard-symbolic 'Fn lock on'

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OBJECT_PATH = '/io/github/zenduo/Shell';
const INTERFACE = `
<node>
  <interface name="io.github.zenduo.Shell">
    <method name="SwapScreens">
      <arg type="u" direction="out" name="moved"/>
    </method>
    <method name="ShowDisplaySwitcher"/>
    <method name="ShowOsd">
      <arg type="s" direction="in" name="icon"/>
      <arg type="s" direction="in" name="label"/>
    </method>
  </interface>
</node>`;

// eDP-1 is the top panel and eDP-2 the bottom one (docs/HARDWARE.md).
const PANELS = ['eDP-1', 'eDP-2'];

const MOVABLE = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.UTILITY,
]);

// [top, bottom] monitor indices, or null when the two panels are not both on.
// MetaMonitorManager.get_monitor_for_connector is Mutter's own lookup; that
// it answers -1 for a panel that is off is VERIFY-ON-HW.
function panelMonitors() {
    const manager = global.backend.get_monitor_manager();
    const found = PANELS.map(connector => manager.get_monitor_for_connector(connector));
    if (found.every(index => index >= 0))
        return found;
    return null;
}

function swapWindows() {
    const monitors = panelMonitors();
    if (monitors === null)
        return 0;
    const [top, bottom] = monitors;
    // Decide every move before making one, so a window moved down is not
    // then seen on the bottom panel and moved back up.
    const moves = [];
    for (const actor of global.get_window_actors()) {
        const window = actor.get_meta_window();
        if (!window || !MOVABLE.has(window.get_window_type()) || window.is_skip_taskbar())
            continue;
        const monitor = window.get_monitor();
        if (monitor === top)
            moves.push([window, bottom]);
        else if (monitor === bottom)
            moves.push([window, top]);
    }
    for (const [window, target] of moves)
        window.move_to_monitor(target);
    return moves.length;
}

function showOsd(iconName, label) {
    const icon = Gio.ThemedIcon.new(iconName);
    const osd = Main.osdWindowManager;
    // showAll is the every-monitor call from GNOME 46 on; show(-1, ...)
    // meant the same before it. Both are read from gnome-shell's
    // js/ui/osdWindow.js; which one 46 has is VERIFY-ON-HW.
    if (typeof osd.showAll === 'function')
        osd.showAll(icon, label, null, null);
    else
        osd.show(-1, icon, label, null, null);
}

// What Mutter's switch-monitor binding runs, read from gnome-shell's
// js/ui/windowManager.js (_startSwitcher) and js/ui/switchMonitor.js on
// 2026-10-31. The Super mask makes it behave as from the key: it stays up
// while Super is held and applies the choice when Super is let go (at once,
// if Super is already up). Imported when called, so a Shell without the
// module still loads the rest of the extension.
async function showDisplaySwitcher() {
    const SwitchMonitor = await import('resource:///org/gnome/shell/ui/switchMonitor.js');
    const popup = new SwitchMonitor.SwitchMonitorPopup();
    if (!popup.show(false, 'switch-monitor', Clutter.ModifierType.MOD4_MASK))
        popup.destroy();
}

export default class DuoExtension extends Extension {
    enable() {
        this._service = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._service.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._service.unexport();
        this._service = null;
    }

    SwapScreens() {
        return swapWindows();
    }

    ShowDisplaySwitcher() {
        showDisplaySwitcher().catch(error => logError(error, 'zenduo: display switcher'));
    }

    ShowOsd(icon, label) {
        showOsd(icon, label);
    }
}
