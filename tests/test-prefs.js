import Gio from 'gi://Gio';
import Adw from 'gi://Adw';
import GLib from 'gi://GLib';

Gio.Resource.load('/usr/share/gnome-shell/org.gnome.Shell.Extensions.src.gresource')._register();
const {default: Preferences} = await import('../prefs.js');
const extensionDir = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent();
const metadata = JSON.parse(new TextDecoder().decode(extensionDir.get_child('metadata.json').load_contents(null)[1]));
metadata.path = extensionDir.get_path();
metadata.dir = extensionDir;
Adw.init();
const preferences = new Preferences(metadata);
const window = new Adw.PreferencesWindow();
preferences.fillPreferencesWindow(window);
window.present();
const loop = new GLib.MainLoop(null, false);
GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
    window.close();
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
loop.run();
