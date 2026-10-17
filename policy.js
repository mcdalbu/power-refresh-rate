export const PROFILE_KEYS = {
    'power-saver': 'power-saver-hz',
    balanced: 'balanced-hz',
    performance: 'performance-hz',
};

export class ScaleUnavailableError extends Error {}

export function currentMode(monitor) {
    return monitor[1].find(mode => mode[6]['is-current']);
}

export function buildConfiguration(state, target) {
    const [serial, monitors, logicals, properties] = state;
    const bySpec = new Map(monitors.map(m => [JSON.stringify(m[0]), m]));
    const changes = [];
    const activeRates = [];
    const output = logicals.map(logical => {
        const [x, y, scale, transform, primary, specs] = logical;
        const physical = specs.map(spec => {
            const monitor = bySpec.get(JSON.stringify(spec));
            const mode = monitor && currentMode(monitor);
            if (!mode)
                throw new Error(`Não há modo ativo para ${spec[0]}.`);
            let selected = mode;
            if (monitor[2]['is-builtin']) {
                const vrr = mode[6]['refresh-rate-mode'] ?? 'fixed';
                const candidates = monitor[1].filter(m =>
                    m[1] === mode[1] && m[2] === mode[2] &&
                    Math.abs(m[3] - target) < 1 &&
                    (m[6]['refresh-rate-mode'] ?? 'fixed') === vrr);
                if (!candidates.length)
                    throw new Error(`${spec[0]} não oferece ${target} Hz com a resolução e o VRR atuais.`);
                selected = candidates.reduce((a, b) =>
                    Math.abs(a[3] - target) <= Math.abs(b[3] - target) ? a : b);
                activeRates.push(selected[3]);
                if (selected[0] !== mode[0])
                    changes.push(`${spec[0]}: ${mode[0]} → ${selected[0]}`);
            }
            if (!selected[5].some(s => Math.abs(s - scale) < 0.000001)) {
                const percent = Math.round(scale * 100);
                throw new ScaleUnavailableError(
                    `O Mutter não anuncia a escala de ${percent}% em ${spec[0]}. ` +
                    'O complemento de escala pode ter sido desativado após uma atualização. ' +
                    'Revalide o complemento e saia e entre na sessão. A escala foi preservada.');
            }
            const props = {};
            if ('is-underscanning' in monitor[2])
                props.underscanning = Boolean(monitor[2]['is-underscanning']);
            for (const key of ['color-mode', 'rgb-range']) {
                if (key in monitor[2])
                    props[key] = monitor[2][key];
            }
            return [spec[0], selected[0], props];
        });
        return [x, y, scale, transform, primary, physical];
    });
    const props = {};
    if (properties['supports-changing-layout-mode'])
        props['layout-mode'] = properties['layout-mode'];
    const leased = monitors.filter(m => m[2]['is-for-lease']).map(m => m[0]);
    if (leased.length)
        props['monitors-for-lease'] = leased;
    return {serial, logicals: output, props, changes, activeRates};
}
