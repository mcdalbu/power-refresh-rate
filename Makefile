UUID := power-refresh-rate@mcda.local

.PHONY: build test test-shell install

build:
	mkdir -p dist
	gnome-extensions pack --force --out-dir=dist --extra-source=controller.js --extra-source=policy.js .

test:
	glib-compile-schemas --strict schemas
	GSETTINGS_BACKEND=memory dbus-run-session --config-file=tests/private-bus.conf -- gjs -m tests/test-controller.js

test-shell:
	glib-compile-schemas --strict schemas
	dbus-run-session --config-file=tests/private-bus.conf -- /usr/bin/python3 tests/isolated-shell.py

install: build
	gnome-extensions install --force dist/$(UUID).shell-extension.zip
