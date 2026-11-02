# Gedächtnis für opencode — eine Erweiterung für OpenTerminalApps.
PAKETSTAND := $(shell date +%G-%V)
export PAKETSTAND

.PHONY: setup up update test logs

setup:
	@./setup.sh

up: setup
	docker compose up -d --build

# Neue Fassung holen und den Dienst neu bauen. Die Daten bleiben im Volume.
update:
	git pull --ff-only
	docker compose up -d --build

test:
	docker compose -f tests/compose.test.yml up --build --abort-on-container-exit --exit-code-from pruefer pruefer; \
	  r=$$?; docker compose -f tests/compose.test.yml down -v >/dev/null 2>&1; exit $$r

logs:
	docker compose logs -f --tail 100 app
