import St from 'gi://St';
import Shell from 'gi://Shell';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import GObject from 'gi://GObject';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const COGL_SNIPPET_HOOK_FRAGMENT = 2048;

const InvertEffect = GObject.registerClass(
class InvertEffect extends Shell.GLSLEffect {
    vfunc_build_pipeline() {
        this.add_glsl_snippet(COGL_SNIPPET_HOOK_FRAGMENT,
            '',
            'cogl_color_out.rgb = cogl_color_out.aaa - cogl_color_out.rgb;',
            false);
    }
});

const DBUS_IFACE = `
<node>
  <interface name="org.gnome.OCRScreenshot">
    <method name="Full">
      <arg type="s" direction="in" name="filename"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="Area">
      <arg type="s" direction="in" name="filename"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="InvertColors">
      <arg type="b" direction="out" name="inverted"/>
    </method>
    <method name="GetInvertState">
      <arg type="b" direction="out" name="inverted"/>
    </method>
    <method name="ListWindows">
      <arg type="s" direction="out" name="windows_json"/>
    </method>
    <method name="FocusWindow">
      <arg type="s" direction="in" name="query"/>
      <arg type="b" direction="out" name="success"/>
    </method>
  </interface>
</node>`;


export default class OCRScreenshotExtension extends Extension {
    enable() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBUS_IFACE, this);
        this._dbusImpl.export(Gio.DBus.session, '/org/gnome/OCRScreenshot');
        this._selecting = false;
        this._inverted = false;
        this._invertEffect = null;
    }

    disable() {
        this._cancelSelection();
        if (this._inverted)
            this._removeInvertEffect();
        this._dbusImpl.unexport();
    }

    InvertColors() {
        if (this._inverted) {
            this._removeInvertEffect();
        } else {
            this._applyInvertEffect();
        }
        this._inverted = !this._inverted;
        return this._inverted;
    }

    GetInvertState() {
        return this._inverted;
    }

    _applyInvertEffect() {
        this._invertEffect = new InvertEffect();
        Main.uiGroup.add_effect_with_name('invert-rgb', this._invertEffect);
    }

    _removeInvertEffect() {
        Main.uiGroup.remove_effect_by_name('invert-rgb');
        this._invertEffect = null;
    }

    ListWindows() {
        const windows = [];
        for (const actor of global.get_window_actors()) {
            const win = actor.get_meta_window();
            if (win.get_window_type() !== Meta.WindowType.NORMAL)
                continue;
            const rect = win.get_frame_rect();
            windows.push({
                title: win.get_title() || '',
                wm_class: win.get_wm_class() || '',
                app_id: win.get_sandboxed_app_id() || win.get_gtk_application_id() || '',
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                focused: win.has_focus(),
            });
        }
        return JSON.stringify(windows);
    }

    FocusWindow(query) {
        const queryLower = query.toLowerCase();
        for (const actor of global.get_window_actors()) {
            const win = actor.get_meta_window();
            const title = (win.get_title() || '').toLowerCase();
            const wmClass = (win.get_wm_class() || '').toLowerCase();
            if (title.includes(queryLower) || wmClass.includes(queryLower)) {
                win.activate(global.get_current_time());
                return true;
            }
        }
        return false;
    }

    async Full(filename) {
        try {
            const file = Gio.File.new_for_path(filename);
            const stream = file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            const screenshot = new Shell.Screenshot();
            await screenshot.screenshot(true, stream);
            stream.close(null);
            return true;
        } catch (e) {
            log(`OCR Screenshot Full error: ${e}`);
            return false;
        }
    }

    AreaAsync(params, invocation) {
        const [filename] = params;
        if (this._selecting) {
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        this._showRegionPicker(filename, success => {
            invocation.return_value(new GLib.Variant('(b)', [success]));
        });
    }

    _showRegionPicker(filename, callback) {
        this._selecting = true;
        this._filename = filename;
        this._callback = callback;
        this._startX = 0;
        this._startY = 0;
        this._dragging = false;

        this._overlay = new St.Widget({
            reactive: true,
            style: 'background-color: rgba(0, 0, 0, 0.3);',
        });
        this._overlay.set_position(0, 0);
        this._overlay.set_size(global.stage.width, global.stage.height);

        this._selRect = new St.Widget({
            style: 'border: 2px solid rgba(255, 255, 255, 0.9); background-color: rgba(255, 255, 255, 0.1);',
            visible: false,
        });
        this._overlay.add_child(this._selRect);

        this._pressId = this._overlay.connect('button-press-event', (actor, event) => {
            if (event.get_button() !== Clutter.BUTTON_PRIMARY) {
                this._finish(false);
                return Clutter.EVENT_STOP;
            }
            const [x, y] = event.get_coords();
            this._startX = x;
            this._startY = y;
            this._dragging = true;
            this._selRect.visible = true;
            this._selRect.set_position(x, y);
            this._selRect.set_size(0, 0);
            return Clutter.EVENT_STOP;
        });

        this._motionId = this._overlay.connect('motion-event', (actor, event) => {
            if (!this._dragging) return Clutter.EVENT_PROPAGATE;
            const [x, y] = event.get_coords();
            const rx = Math.min(this._startX, x);
            const ry = Math.min(this._startY, y);
            const rw = Math.abs(x - this._startX);
            const rh = Math.abs(y - this._startY);
            this._selRect.set_position(rx, ry);
            this._selRect.set_size(rw, rh);
            return Clutter.EVENT_STOP;
        });

        this._releaseId = this._overlay.connect('button-release-event', (actor, event) => {
            if (!this._dragging) return Clutter.EVENT_PROPAGATE;
            const [x, y] = event.get_coords();
            const rx = Math.min(this._startX, x);
            const ry = Math.min(this._startY, y);
            const rw = Math.abs(x - this._startX);
            const rh = Math.abs(y - this._startY);
            this._dragging = false;

            if (rw < 10 || rh < 10) {
                this._finish(false);
                return Clutter.EVENT_STOP;
            }

            this._cleanup();
            this._takeAreaScreenshot(rx, ry, rw, rh);
            return Clutter.EVENT_STOP;
        });

        this._keyId = this._overlay.connect('key-press-event', (actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this._finish(false);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        Main.uiGroup.add_child(this._overlay);
        this._grab = Main.pushModal(this._overlay, {actionMode: Shell.ActionMode.ALL});
        if (!this._grab) {
            log('OCR Screenshot: failed to push modal');
            this._cleanup();
            callback(false);
            return;
        }

        global.display.set_cursor(Meta.Cursor.CROSSHAIR);
    }

    async _takeAreaScreenshot(x, y, w, h) {
        try {
            const file = Gio.File.new_for_path(this._filename);
            const stream = file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            const screenshot = new Shell.Screenshot();
            await screenshot.screenshot_area(x, y, w, h, stream);
            stream.close(null);
            this._selecting = false;
            this._callback(true);
        } catch (e) {
            log(`OCR Screenshot area error: ${e}`);
            this._selecting = false;
            this._callback(false);
        }
    }

    _finish(success) {
        this._cleanup();
        this._selecting = false;
        if (this._callback) {
            this._callback(success);
            this._callback = null;
        }
    }

    _cleanup() {
        global.display.set_cursor(Meta.Cursor.DEFAULT);
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        if (this._overlay) {
            Main.uiGroup.remove_child(this._overlay);
            this._overlay.destroy();
            this._overlay = null;
            this._selRect = null;
        }
    }

    _cancelSelection() {
        if (this._selecting)
            this._finish(false);
    }
}
