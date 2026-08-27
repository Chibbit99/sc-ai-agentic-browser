.PHONY: help venv build install uninstall run-dev clean

help:
	@echo "SC.AI build targets:"
	@echo "  make venv      Create the development virtualenv"
	@echo "  make build     Build the PyInstaller binaries into build/dist/"
	@echo "  make install   Install the binaries + KDE menu entry (user-local)"
	@echo "  make uninstall Remove the installed binaries + menu entry (keeps your data)"
	@echo "  make run-dev   Run the launcher from source"
	@echo "  make clean     Remove build artifacts"

venv:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

build:
	./build.sh

install:
	./install.sh

uninstall:
	./uninstall.sh

run-dev:
	.venv/bin/python launcher/launcher.py

clean:
	rm -rf build/work build/dist