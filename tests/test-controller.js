import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {RefreshController} from '../controller.js';
import {buildConfiguration, ScaleUnavailableError} from '../policy.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

const native = ['eDP-1', 'SDC', 'panel', 'native'];
const external = ['DP-1', 'DEL', 'external', 'external'];
const lease = ['DP-2', 'DEL', 'leased', 'leased'];
let mode = 120;
let profile = 'balanced';
let onBattery = false;
let hasScale = true;
let serial = 1;
const applied = [];
const verified = [];
const V = (signature, value) => new GLib.Variant(signature, value);
const packedProps = props => Object.fromEntries(Object.entries(props).map(([key, value]) =>
    [key, V(typeof value === 'boolean' ? 'b' : typeof value === 'string' ? 's' : 'u', value)]));

function state() {
    return [serial, [
        [native, [120, 60].map(hz => [`2880x1800@${hz}+vrr`, 2880, 1800, hz, 1.8,
            hasScale ? [1, 1.8, 2] : [1, 2],
            packedProps({'is-current': hz === mode, 'refresh-rate-mode': 'variable'})]),
        packedProps({'is-builtin': true, 'color-mode': 0, 'rgb-range': 1})],
        [external, [['1920x1080@60', 1920, 1080, 60, 1, [1, 2],
            packedProps({'is-current': true})]], packedProps({'is-builtin': false})],
        [lease, [], packedProps({'is-for-lease': true})],
    ], [[0, 0, 1.8, 0, true, [native], {}], [1600, 0, 1, 0, false, [external], {}]],
    packedProps({'supports-changing-layout-mode': true, 'layout-mode': 1})];
}

const stateVariant = () => V('(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})', state());
const plain = stateVariant().recursiveUnpack();
const config = buildConfiguration(plain, 60);
assert(config.changes.length === 1, 'Only built-in refresh should change');
assert(config.logicals[0][2] === 1.8, '180% must be retained');
assert(config.logicals[1][0] === 1600 && config.logicals[1][2] === 1, 'External layout must be retained');
assert(config.logicals[1][5][0][1] === '1920x1080@60', 'External refresh must be retained');
assert(config.logicals[0][5][0][2]['rgb-range'] === 1, 'RGB range must be retained');
assert(config.props['monitors-for-lease'][0][0] === 'DP-2', 'Lease must be retained');
hasScale = false;
let scaleError;
try { buildConfiguration(stateVariant().recursiveUnpack(), 60); } catch (error) { scaleError = error; }
assert(scaleError instanceof ScaleUnavailableError, 'Unsupported scale must stop before Apply');
hasScale = true;
print('Policy: VRR, scale, external layout, leases and unsupported-scale guard: OK');

const displayXML = `<node><interface name="org.gnome.Mutter.DisplayConfig">
<method name="GetCurrentState"><arg direction="out" type="u"/><arg direction="out" type="a((ssss)a(siiddada{sv})a{sv})"/><arg direction="out" type="a(iiduba(ssss)a{sv})"/><arg direction="out" type="a{sv}"/></method>
<method name="ApplyMonitorsConfig"><arg direction="in" type="u"/><arg direction="in" type="u"/><arg direction="in" type="a(iiduba(ssa{sv}))"/><arg direction="in" type="a{sv}"/></method>
<property name="PowerSaveMode" type="i" access="read"/><property name="ApplyMonitorsConfigAllowed" type="b" access="read"/>
<signal name="MonitorsChanged"/>
</interface></node>`;
const powerXML = `<node><interface name="org.freedesktop.UPower.PowerProfiles"><property name="ActiveProfile" type="s" access="read"/></interface></node>`;

const display = Gio.DBusExportedObject.wrapJSObject(displayXML, {
    GetCurrentState() { return state(); },
    get PowerSaveMode() { return 0; },
    get ApplyMonitorsConfigAllowed() { return true; },
    ApplyMonitorsConfig(requestSerial, method, logicals, props) {
        assert(requestSerial === serial, 'Serial must be current');
        assert(logicals[0][2] === 1.8, 'Controller changed scale');
        assert(logicals[1][5][0][1] === '1920x1080@60', 'Controller changed external monitor');
        const layout = props['layout-mode'].deep_unpack();
        assert(layout === 1, 'Layout mode must be retained');
        if (method === 0) {
            verified.push(logicals);
            return;
        }
        assert(method === 1, 'Apply must be temporary');
        mode = Number(logicals[0][5][0][1].includes('@60+') ? 60 : 120);
        applied.push(mode);
        serial++;
        display.emit_signal('MonitorsChanged', null);
    },
});
const power = Gio.DBusExportedObject.wrapJSObject(powerXML, {
    get ActiveProfile() { return profile; },
});
const upower = Gio.DBusExportedObject.wrapJSObject('<node><interface name="org.freedesktop.UPower"><property name="OnBattery" type="b" access="read"/></interface></node>', {
    get OnBattery() { return onBattery; },
});
const connection = Gio.DBus.session;
upower.export(connection, '/org/freedesktop/UPower');
display.export(connection, '/org/gnome/Mutter/DisplayConfig');
power.export(connection, '/org/freedesktop/UPower/PowerProfiles');
for (const name of ['org.gnome.Mutter.DisplayConfig', 'org.freedesktop.UPower.PowerProfiles', 'org.freedesktop.UPower']) {
    connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName', V('(su)', [name, 0]),
        null, Gio.DBusCallFlags.NONE, 2000, null);
}
const dir = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent().get_child(
    'schemas').get_path();
const source = Gio.SettingsSchemaSource.new_from_directory(dir, Gio.SettingsSchemaSource.get_default(), false);
const settings = new Gio.Settings({settings_schema: source.lookup('org.gnome.shell.extensions.power-refresh-rate', false)});
const delay = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));
async function until(predicate, message) {
    const deadline = Date.now() + 6000;
    while (!predicate()) {
        if (Date.now() > deadline)
            throw new Error(`${message}; ${settings.get_string('last-error')}`);
        await delay(25);
    }
}
function setProfile(value) {
    profile = value;
    power.emit_property_changed('ActiveProfile', V('s', profile));
    power.flush();
}

let controller;
try {
    controller = new RefreshController(settings, Gio.BusType.SESSION);
    controller.start();
    await until(() => settings.get_string('last-status').includes('balanced: 120'), 'Initial status');
    setProfile('power-saver');
    await until(() => mode === 60 && settings.get_string('last-status').includes('power-saver: 60'), 'Power saver');
    setProfile('balanced');
    await until(() => mode === 120 && settings.get_string('last-status').includes('balanced: 120'), 'Balanced');
    setProfile('power-saver');
    await until(() => mode === 60, 'Power saver before performance');
    setProfile('performance');
    await until(() => mode === 120 && settings.get_string('last-status').includes('performance: 120'), 'Performance');
    assert(verified.length === applied.length, 'Every change must be verified first');
    print('D-Bus controller: power-saver=60, balanced/performance=120; all changes verified: OK');

    hasScale = false;
    display.emit_signal('MonitorsChanged', null);
    await until(() => settings.get_string('last-error').includes('180%'), 'Missing scale diagnostic');
    const count = applied.length;
    await delay(1800);
    assert(applied.length === count, 'Unsupported scale should not be applied/retried');
    hasScale = true;
    display.emit_signal('MonitorsChanged', null);
    await until(() => settings.get_string('last-error') === '', 'Recovery after scale restored');
    print('Unsupported scale: clear diagnostic, no unsafe applies, automatic recovery: OK');

    settings.set_uint('performance-hz', 60);
    await until(() => mode === 60, 'Settings change');
    settings.set_uint('performance-hz', 120);
    await until(() => mode === 120, 'Settings restore');
    settings.set_uint('performance-ac-hz', 120);
    settings.set_uint('performance-battery-hz', 60);
    settings.set_boolean('separate-refresh-by-power-source', true);
    onBattery = true;
    upower.emit_property_changed('OnBattery', V('b', true));
    upower.flush();
    await until(() => mode === 60, 'Battery refresh without changing profile');
    onBattery = false;
    upower.emit_property_changed('OnBattery', V('b', false));
    upower.flush();
    await until(() => mode === 120, 'AC refresh without changing profile');
    print('Separate AC/battery refresh and source-change events: OK');
    const beforeDisable = applied.length;
    setProfile('power-saver');
    controller.stop();
    await delay(700);
    assert(applied.length === beforeDisable, 'Disable must cancel pending work and disconnect signals');
    assert(settings.get_string('last-status') === 'Desativada.', 'Disable status');
    controller = null;
    print('Preferences updates and disable cleanup: OK');
} finally {
    controller?.stop();
    display.unexport();
    power.unexport();
    upower.unexport();
}
