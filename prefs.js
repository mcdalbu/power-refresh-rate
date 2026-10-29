import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const PROFILES = [['power-saver', 'Economia'], ['balanced', 'Balanceado'], ['performance', 'Desempenho']];
const SOURCES = [['ac', 'Na tomada'], ['battery', 'Na bateria']];
const EPP_NAMES = ['Atividade leve · UTIL_IDLE', 'Carga sustentada · SUSTAIN',
    'Rajadas curtas · BURSTY', 'Atividade gráfica · GFX_BUSY'];
const HELPER = '/usr/local/libexec/power-profile-settings-helper';

function readJson(path) {
    return JSON.parse(new TextDecoder().decode(GLib.file_get_contents(path)[1]));
}

function applySystem(draft) {
    return new Promise((resolve, reject) => {
        let process;
        try {
            process = Gio.Subprocess.new(['/usr/bin/pkexec', '--disable-internal-agent', HELPER],
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
        } catch (error) {
            reject(error);
            return;
        }
        process.communicate_utf8_async(JSON.stringify(draft), null, (source, result) => {
            try {
                const [, output, stderr] = source.communicate_utf8_finish(result);
                if (!source.get_successful()) {
                    if ([126, 127].includes(source.get_exit_status()))
                        throw new Error('Autenticação cancelada ou não autorizada. Os ajustes não foram aplicados.');
                    throw new Error(stderr.trim() || 'O serviço não conseguiu aplicar os ajustes.');
                }
                resolve(JSON.parse(output));
            } catch (error) {
                reject(error);
            }
        });
    });
}

function spin(title, value, upper, subtitle = '', step = 1) {
    return new Adw.SpinRow({title, subtitle, numeric: true, digits: 0,
        adjustment: new Gtk.Adjustment({lower: 0, upper, step_increment: step,
            page_increment: step * 5, value})});
}

export default class PowerRefreshPreferences extends ExtensionPreferences {
    _loadSystemConfiguration() {
        if (!GLib.file_test(HELPER, GLib.FileTest.IS_EXECUTABLE))
            throw new Error('Instale o complemento de sistema para editar CPU, EPP e GPU.');
        return {version: 1, epp: readJson('/etc/power-profile-lpmd/config.json'),
            hardware: readJson('/etc/power-profile-cpu-cap/config.json')};
    }

    _applySystem(draft) {
        return applySystem(draft);
    }

    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window.set_default_size(820, 850);
        window.search_enabled = true;
        let draft;
        let backendError = '';
        try {
            draft = this._loadSystemConfiguration();
        } catch (error) {
            backendError = error.message;
        }
        window._powerProfileDraft = draft;
        const overview = new Adw.PreferencesPage({name: 'general', title: 'Geral', icon_name: 'preferences-system-symbolic'});
        window.add(overview);
        const info = new Adw.PreferencesGroup({title: 'Perfis de energia',
            description: 'EPP, limites de CPU e GPU são ajustes do sistema. A frequência da tela pertence à sua sessão GNOME. O turbo permanece habilitado.'});
        overview.add(info);
        const applyRow = new Adw.ActionRow({title: 'Aplicar ajustes de CPU, EPP e GPU',
            subtitle: backendError || 'Revise as páginas dos três perfis antes de aplicar. Requer autenticação de administrador.'});
        const save = new Gtk.Button({label: 'Aplicar ajustes', valign: Gtk.Align.CENTER,
            css_classes: ['suggested-action'], sensitive: Boolean(draft)});
        applyRow.add_suffix(save);
        info.add(applyRow);
        const status = new Adw.ActionRow({title: 'Resultado', subtitle: backendError || 'Valores atuais carregados; nenhuma alteração aplicada.'});
        info.add(status);
        const editors = [];
        let applying = false;
        let saved = JSON.stringify(draft);
        save.connect('clicked', async () => {
            if (applying || !draft)
                return;
            if (JSON.stringify(draft) === saved) {
                status.subtitle = 'Nenhuma alteração de sistema para aplicar.';
                return;
            }
            applying = true;
            save.sensitive = false;
            for (const group of editors)
                group.sensitive = false;
            status.subtitle = 'Aguardando autenticação e aplicação…';
            try {
                const result = await this._applySystem(draft);
                saved = JSON.stringify(draft);
                status.subtitle = result.changed ? 'Ajustes salvos. Os serviços estão aplicando a política do perfil atual.' :
                    'Os valores já estavam salvos. Nenhum serviço precisou ser reiniciado.';
            } catch (error) {
                status.subtitle = error.message;
            } finally {
                applying = false;
                save.sensitive = true;
                for (const group of editors)
                    group.sensitive = true;
            }
        });
        const diagnosticGroup = new Adw.PreferencesGroup({title: 'Tela · diagnóstico'});
        overview.add(diagnosticGroup);
        const screenStatus = new Adw.ActionRow({title: 'Controle automático'});
        const screenError = new Adw.ActionRow({title: 'Diagnóstico'});
        diagnosticGroup.add(screenStatus);
        diagnosticGroup.add(screenError);
        const updateStatus = () => {
            screenStatus.subtitle = settings.get_string('last-status');
            screenError.subtitle = settings.get_string('last-error');
            screenError.visible = Boolean(screenError.subtitle);
        };
        updateStatus();
        const signals = ['last-status', 'last-error'].map(key => settings.connect(`changed::${key}`, updateStatus));
        window.connect('close-request', () => {
            if (applying) {
                status.subtitle = 'Aguarde a conclusão da aplicação antes de fechar.';
                return true;
            }
            for (const id of signals)
                settings.disconnect(id);
            return false;
        });
        for (const [profile, title] of PROFILES) {
            const page = new Adw.PreferencesPage({name: profile, title, icon_name: 'power-profile-balanced-symbolic'});
            window.add(page);
            for (const [source, sourceTitle] of SOURCES) {
                const display = new Adw.PreferencesGroup({title: `${sourceTitle} · tela`,
                    description: 'A frequência é salva imediatamente. Preserva escala, resolução e VRR; o painel precisa oferecer o modo selecionado.'});
                page.add(display);
                const key = `${profile}-${source}-hz`;
                const current = settings.get_boolean('separate-refresh-by-power-source') ? settings.get_uint(key) : settings.get_uint(`${profile}-hz`);
                const rates = [60, 120];
                const refresh = new Adw.ComboRow({title: 'Taxa de atualização',
                    model: Gtk.StringList.new(rates.map(rate => `${rate} Hz`)), selected: Math.max(0, rates.indexOf(current))});
                refresh.connect('notify::selected', () => {
                    // Copy the old per-profile preferences on first use, keeping migration lossless.
                    if (!settings.get_boolean('separate-refresh-by-power-source')) {
                        for (const [p] of PROFILES)
                            for (const [s] of SOURCES)
                                settings.set_uint(`${p}-${s}-hz`, settings.get_uint(`${p}-hz`));
                    }
                    settings.set_uint(key, rates[refresh.selected]);
                    settings.set_boolean('separate-refresh-by-power-source', true);
                });
                display.add(refresh);
                const system = new Adw.PreferencesGroup({title: `${sourceTitle} · CPU e GPU`,
                    description: draft ? 'Edite aqui e use “Aplicar ajustes” na página Geral. EPP maior favorece eficiência; 0 MHz restaura o máximo do hardware.' : backendError,
                    sensitive: Boolean(draft)});
                page.add(system);
                if (!draft)
                    continue;
                editors.push(system);
                const hardware = draft.hardware[profile][source];
                for (const [keyName, label] of [['p_max_mhz', 'Limite dos P-cores (MHz)'], ['e_max_mhz', 'Limite dos E-cores (MHz)']]) {
                    const row = spin(label, hardware[keyName], 10000, '0 = máximo do hardware. O complemento valida os limites aceitos nesta CPU.', 100);
                    row.connect('notify::value', () => {hardware[keyName] = Math.round(row.value);});
                    system.add(row);
                }
                const gpu = new Adw.ComboRow({title: 'Política da GPU Intel Xe',
                    model: Gtk.StringList.new(['Padrão · base', 'Economia · power_saving']),
                    selected: hardware.gpu === 'power_saving' ? 1 : 0});
                gpu.connect('notify::selected', () => {hardware.gpu = gpu.selected ? 'power_saving' : 'base';});
                system.add(gpu);
                const eppRows = [];
                let dynamic;
                if (profile === 'performance') {
                    dynamic = new Adw.SwitchRow({title: 'EPP dinâmico com LPMD',
                        subtitle: 'Desativado mantém EPP 0. Ativado usa os quatro estados abaixo.',
                        active: draft.epp.performance_dynamic[source]});
                    system.add(dynamic);
                }
                const values = draft.epp[profile][source];
                for (let index = 0; index < 4; index++) {
                    const row = spin(EPP_NAMES[index], values[index], 255, 'EPP de 0 a 255; selecionado pelo estado de carga do LPMD.');
                    row.sensitive = !dynamic || dynamic.active;
                    row.connect('notify::value', () => {values[index] = Math.round(row.value);});
                    system.add(row);
                    eppRows.push(row);
                }
                let dynamicValues = values.some(value => value !== 0) ? [...values] : [192, 96, 64, 128];
                dynamic?.connect('notify::active', () => {
                    draft.epp.performance_dynamic[source] = dynamic.active;
                    if (dynamic.active) {
                        for (let i = 0; i < 4; i++) {
                            values[i] = dynamicValues[i];
                            eppRows[i].value = dynamicValues[i];
                        }
                    } else {
                        dynamicValues = [...values];
                        for (let i = 0; i < 4; i++) {
                            values[i] = 0;
                            eppRows[i].value = 0;
                        }
                    }
                    for (const row of eppRows)
                        row.sensitive = dynamic.active;
                });
            }
        }
    }
}
