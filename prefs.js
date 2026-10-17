import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class PowerRefreshPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window.set_default_size(640, 650);
        const page = new Adw.PreferencesPage({title: 'Frequência da tela', icon_name: 'video-display-symbolic'});
        window.add(page);
        const profiles = new Adw.PreferencesGroup({
            title: 'Perfis de energia',
            description: 'Ajusta somente o painel interno. Mantém a escala, a resolução e a configuração de VRR.',
        });
        page.add(profiles);
        for (const [title, key] of [['Economia de energia', 'power-saver-hz'],
            ['Balanceado', 'balanced-hz'], ['Desempenho', 'performance-hz']]) {
            const values = [60, 120];
            const row = new Adw.ComboRow({title,
                model: Gtk.StringList.new(values.map(hz => `${hz} Hz`)),
                selected: Math.max(0, values.indexOf(settings.get_uint(key))),
            });
            row.connect('notify::selected', () => settings.set_uint(key, values[row.selected]));
            profiles.add(row);
        }
        const diagnostics = new Adw.PreferencesGroup({title: 'Estado'});
        page.add(diagnostics);
        const stateRow = new Adw.ActionRow({title: 'Controle automático'});
        const errorRow = new Adw.ActionRow({title: 'Diagnóstico'});
        diagnostics.add(stateRow);
        diagnostics.add(errorRow);
        const update = () => {
            stateRow.subtitle = settings.get_string('last-status');
            errorRow.subtitle = settings.get_string('last-error');
            errorRow.visible = Boolean(errorRow.subtitle);
        };
        update();
        const signals = ['last-status', 'last-error'].map(key =>
            settings.connect(`changed::${key}`, update));
        window.connect('close-request', () => {
            for (const signal of signals)
                settings.disconnect(signal);
            return false;
        });
    }
}
