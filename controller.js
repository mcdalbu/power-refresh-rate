import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {buildConfiguration, PROFILE_KEYS, ScaleUnavailableError} from './policy.js';

const DISPLAY = 'org.gnome.Mutter.DisplayConfig';
const POWER = 'org.freedesktop.UPower.PowerProfiles';
const FLAGS = Gio.DBusProxyFlags.DO_NOT_AUTO_START |
    Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES;

function proxyFor(bus, name, path, cancellable) {
    return new Promise((resolve, reject) => {
        Gio.DBusProxy.new_for_bus(bus, FLAGS, null, name, path, name, cancellable,
            (_source, result) => {
                try {
                    resolve(Gio.DBusProxy.new_for_bus_finish(result));
                } catch (error) {
                    reject(error);
                }
            });
    });
}

function call(proxy, method, params, cancellable) {
    return new Promise((resolve, reject) => {
        proxy.call(method, params, Gio.DBusCallFlags.NONE, 5000, cancellable,
            (source, result) => {
                try {
                    resolve(source.call_finish(result));
                } catch (error) {
                    reject(error);
                }
            });
    });
}

function parameters(config, method) {
    const logicals = config.logicals.map(([x, y, scale, transform, primary, physical]) =>
        [x, y, scale, transform, primary, physical.map(([connector, mode, values]) => {
            const props = {};
            for (const [key, value] of Object.entries(values))
                props[key] = new GLib.Variant(key === 'underscanning' ? 'b' : 'u', value);
            return [connector, mode, props];
        })]);
    const props = {};
    for (const [key, value] of Object.entries(config.props))
        props[key] = new GLib.Variant(key === 'layout-mode' ? 'u' : 'a(ssss)', value);
    return new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [config.serial, method, logicals, props]);
}

export class RefreshController {
    constructor(settings, powerBus = Gio.BusType.SYSTEM) {
        this._settings = settings;
        this._powerBus = powerBus;
        this._connections = [];
        this._pending = 0;
        this._running = false;
        this._busy = false;
        this._again = false;
        this._failures = 0;
        this._lastError = null;
    }

    start() {
        this._running = true;
        this._cancellable = new Gio.Cancellable();
        this._status('Iniciando…');
        this._connect(this._settings, 'changed', (_settings, key) => {
            if (key.endsWith('-hz') || key === 'separate-refresh-by-power-source')
                this._queue();
        });
        this._initialize().catch(error => {
            if (this._running)
                this._reportError(error);
        });
    }

    async _initialize() {
        const [display, power, upower] = await Promise.all([
            proxyFor(Gio.BusType.SESSION, DISPLAY, '/org/gnome/Mutter/DisplayConfig', this._cancellable),
            proxyFor(this._powerBus, POWER, '/org/freedesktop/UPower/PowerProfiles', this._cancellable),
            proxyFor(this._powerBus, 'org.freedesktop.UPower', '/org/freedesktop/UPower', this._cancellable),
        ]);
        if (!this._running)
            return;
        this._display = display;
        this._power = power;
        this._upower = upower;
        this._connect(display, 'g-signal', (_p, _sender, signal) => {
            if (signal === 'MonitorsChanged')
                this._queue();
        });
        this._connect(display, 'g-properties-changed', (_p, changed) => {
            const keys = Object.keys(changed.deep_unpack());
            if (keys.includes('PowerSaveMode') || keys.includes('ApplyMonitorsConfigAllowed'))
                this._queue();
        });
        this._connect(power, 'g-properties-changed', (_p, changed, invalidated) => {
            if ('ActiveProfile' in changed.deep_unpack() || invalidated.includes('ActiveProfile'))
                this._queue();
        });
        this._connect(upower, 'g-properties-changed', (_p, changed, invalidated) => {
            if ('OnBattery' in changed.deep_unpack() || invalidated.includes('OnBattery'))
                this._queue();
        });
        for (const proxy of [display, power, upower])
            this._connect(proxy, 'notify::g-name-owner', () => this._queue());
        this._queue();
        console.info('[Power Refresh Rate] Extensão iniciada; controle por sinais D-Bus.');
    }

    _connect(object, signal, callback) {
        this._connections.push([object, object.connect(signal, callback)]);
    }

    _status(status, error = '') {
        for (const [key, value] of [['last-status', status], ['last-error', error]]) {
            if (this._settings.get_string(key) !== value)
                this._settings.set_string(key, value);
        }
    }

    _queue(retry = false) {
        if (!this._running)
            return;
        if (!retry)
            this._failures = 0;
        if (this._pending)
            GLib.source_remove(this._pending);
        this._pending = GLib.timeout_add(GLib.PRIORITY_DEFAULT, retry ? 1500 : 300, () => {
            this._pending = 0;
            if (this._busy) {
                this._again = true;
            } else {
                this._reconcile();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _profile() {
        return this._power.get_cached_property('ActiveProfile')?.deep_unpack();
    }

    _target(profile) {
        if (!this._settings.get_boolean('separate-refresh-by-power-source'))
            return this._settings.get_uint(PROFILE_KEYS[profile]);
        const battery = this._upower.get_cached_property('OnBattery');
        if (!this._upower.get_name_owner() || battery === null)
            throw new Error('Aguardando UPower para selecionar a frequência por alimentação.');
        const source = battery.deep_unpack() ? 'battery' : 'ac';
        return this._settings.get_uint(`${profile}-${source}-hz`);
    }

    async _reconcile() {
        this._busy = true;
        try {
            if (!this._display.get_name_owner() || !this._power.get_name_owner()) {
                this._status('Aguardando os serviços de tela e energia.');
                return;
            }
            const allowed = this._display.get_cached_property('ApplyMonitorsConfigAllowed');
            const screenPower = this._display.get_cached_property('PowerSaveMode')?.deep_unpack();
            if (allowed?.deep_unpack() === false || (screenPower !== undefined && screenPower > 0)) {
                this._status('Aguardando a tela ficar disponível.');
                return;
            }
            const profile = this._profile();
            const key = PROFILE_KEYS[profile];
            if (!key)
                throw new Error(`Perfil de energia indisponível ou desconhecido: ${profile}`);
            const target = this._target(profile);
            const state = await call(this._display, 'GetCurrentState', null, this._cancellable);
            if (!this._running)
                return;
            const config = buildConfiguration(state.recursiveUnpack(), target);
            if (config.changes.length) {
                await call(this._display, 'ApplyMonitorsConfig', parameters(config, 0), this._cancellable);
                if (!this._running || profile !== this._profile() || target !== this._target(profile)) {
                    this._again = this._running;
                    return;
                }
                await call(this._display, 'ApplyMonitorsConfig', parameters(config, 1), this._cancellable);
                if (!this._running)
                    return;
                console.info(`[Power Refresh Rate] ${profile}: ${config.changes.join('; ')}`);
            }
            this._status(config.activeRates.length ? `${profile}: ${target} Hz · escala preservada` :
                'Sem painel interno ativo.');
            this._failures = 0;
            this._lastError = null;
        } catch (error) {
            if (this._running) {
                this._reportError(error);
                // Unsupported scales and other invalid configurations will not
                // improve with retries. A new profile/monitor event rechecks them.
                const remote = error instanceof GLib.Error && Gio.DBusError.is_remote_error(error) ?
                    Gio.DBusError.get_remote_error(error) : '';
                if (!(error instanceof ScaleUnavailableError) &&
                    remote !== 'org.freedesktop.DBus.Error.InvalidArgs' && ++this._failures < 3)
                    this._queue(true);
            }
        } finally {
            this._busy = false;
            if (this._again && this._running) {
                this._again = false;
                this._queue();
            }
        }
    }

    _reportError(error) {
        const message = error.message ?? String(error);
        this._status('A troca automática precisa de atenção.', message);
        if (message !== this._lastError)
            console.warn(`[Power Refresh Rate] ${message}`);
        this._lastError = message;
    }

    stop() {
        this._running = false;
        this._cancellable?.cancel();
        if (this._pending)
            GLib.source_remove(this._pending);
        this._pending = 0;
        for (const [object, id] of this._connections)
            object.disconnect(id);
        this._connections = [];
        this._status('Desativada.');
        this._power = null;
        this._upower = null;
        this._display = null;
        this._again = false;
    }
}
