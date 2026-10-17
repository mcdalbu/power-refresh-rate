import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {RefreshController} from './controller.js';

export default class PowerRefreshRate extends Extension {
    enable() {
        this._controller = new RefreshController(this.getSettings());
        this._controller.start();
    }

    disable() {
        this._controller?.stop();
        this._controller = null;
    }
}
