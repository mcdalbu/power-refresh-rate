import Gio from 'gi://Gio';
import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import GLib from 'gi://GLib';

Gio.Resource.load('/usr/share/gnome-shell/org.gnome.Shell.Extensions.src.gresource')._register();
const {default: Preferences, eppDescription} = await import('../prefs.js');
for (const [value, name] of [[0, 'Maximum Performance'], [32, 'High Performance'],
    [64, 'Performance'], [96, 'Responsive'], [128, 'Balanced'], [160, 'Efficient'],
    [192, 'Power Saving'], [224, 'Maximum Savings'], [255, 'Full Economy'], [208, 'Custom value']]) {
    if (eppDescription(value) !== `${value} · ${name}`)
        throw new Error(`Incorrect EPP label for ${value}`);
}
const extensionDir = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent();
const metadata = JSON.parse(new TextDecoder().decode(extensionDir.get_child('metadata.json').load_contents(null)[1]));
metadata.path = extensionDir.get_path();
metadata.dir = extensionDir;
Adw.init();
let applied;
let applyCalls = 0;
class TestPreferences extends Preferences {
    _loadSystemConfiguration() {
        const load = name => JSON.parse(new TextDecoder().decode(extensionDir.get_child(`system/${name}`).load_contents(null)[1]));
        return {version: 1, epp: load('epp-defaults.json'), hardware: load('hardware-defaults.json')};
    }
    async _applySystem(draft) {
        applyCalls++;
        applied = JSON.parse(JSON.stringify(draft));
        return {changed: true};
    }
}
const preferences = new TestPreferences(metadata);
const window = new Adw.PreferencesWindow();
preferences.fillPreferencesWindow(window);
window.present();
window.set_visible_page_name('balanced');
function widgets(root) {
    const output = [root];
    for (let child = root.get_first_child(); child; child = child.get_next_sibling())
        output.push(...widgets(child));
    return output;
}
const all = widgets(window);
const save = all.find(w => w.label === 'Aplicar ajustes');
if (!save || !save.sensitive || !window._powerProfileDraft)
    throw new Error('System editor did not load');
const customEpp = all.find(w => w instanceof Adw.SpinRow && w.title === 'Custom EPP' && w.value === 208);
customEpp.value = 209;
if (!['power-saver', 'balanced', 'performance'].some(p => ['ac', 'battery'].some(s => window._powerProfileDraft.epp[p][s].includes(209))))
    throw new Error('Custom EPP was rounded or lost');
customEpp.value = 208;
const eppRow = all.find(w => w instanceof Adw.ComboRow && w.title.includes('UTIL_IDLE') && w.selected < 9);
const previousEpp = eppRow.selected;
eppRow.selected = 0;
eppRow.selected = 7;
if (eppRow.model.get_string(eppRow.selected) !== '224 · Maximum Savings')
    throw new Error('Named EPP selector did not show the value and label');
eppRow.selected = previousEpp;
save.emit('clicked');
if (applyCalls !== 0)
    throw new Error('Unchanged settings requested authentication');
// Changing a workload value must reach the single explicit save operation.
const parkingRows = all.filter(w => w instanceof Adw.SwitchRow && w.title === 'Desativar P-cores secundários');
if (parkingRows.length !== 2 || parkingRows.some(row => row.active))
    throw new Error('Parking must be opt-in and limited to the two Economy contexts');
parkingRows[1].active = true;
if (window._powerProfileDraft.hardware['power-saver'].battery.park_p_cores !== true ||
    window._powerProfileDraft.hardware['power-saver'].ac.park_p_cores === true)
    throw new Error('Parking switch did not preserve independent AC/battery settings');
window._powerProfileDraft.epp.balanced.battery[0] = 209;
GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
    save.emit('clicked');
    return GLib.SOURCE_REMOVE;
});
await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));
if (!save.sensitive)
    throw new Error('Apply did not finish or editors stayed disabled');
if (applied?.epp.balanced.battery[0] !== 209)
    throw new Error('Preferences did not apply the edited configuration');
if (applied.hardware['power-saver'].battery.park_p_cores !== true)
    throw new Error('Parking option was lost in the privileged-helper payload');
if (applyCalls !== 1)
    throw new Error('Apply called more than once');
preferences._applySystem = async () => { throw new Error('Authentication cancelled'); };
window._powerProfileDraft.epp.balanced.battery[0] = 210;
save.emit('clicked');
await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => { resolve(); return GLib.SOURCE_REMOVE; }));
if (!save.sensitive || applyCalls !== 1)
    throw new Error('Cancelled authentication did not recover safely');
print('System editor: explicit save, no-op, workload payload and cancelled-auth recovery verified');
window.set_visible_page_name('power-saver');
await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 200, () => { resolve(); return GLib.SOURCE_REMOVE; }));
const snapshot = new Gtk.Snapshot();
Gtk.WidgetPaintable.new(window).snapshot(snapshot, window.get_width(), window.get_height());
const node = snapshot.to_node();
if (node)
    window.get_renderer().render_texture(node, null).save_to_png('/tmp/power-profiles-preferences.png');
window.close();
