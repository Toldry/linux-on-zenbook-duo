// Swap the windows of the two built-in panels: what F8 does under Windows
// (reported by the owner, 2026-10-31). Under Wayland only the compositor can
// move another program's window, so this lives inside GNOME Shell and offers
// one D-Bus method on the Shell's own connection; `duo swap-screens` calls it.
//
//   gdbus call --session --dest org.gnome.Shell \
//     --object-path /io/github/zenduo/SwapScreens \
//     --method io.github.zenduo.SwapScreens.Swap

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const OBJECT_PATH = '/io/github/zenduo/SwapScreens';
const INTERFACE = `
<node>
  <interface name="io.github.zenduo.SwapScreens">
    <method name="Swap">
      <arg type="u" direction="out" name="moved"/>
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

export default class SwapScreensExtension extends Extension {
    enable() {
        this._service = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._service.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._service.unexport();
        this._service = null;
    }

    Swap() {
        return swapWindows();
    }
}
