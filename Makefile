COMPFILE := docker/compose.yml
COMP := compose -f $(COMPFILE)
SERVER := htwserver-webapi
APPDIR := ~/api
SERVER_APP := $(SERVER):$(APPDIR)
RSYNC_COMMON := -rcv --exclude-from=.rsyncexclude

up:
	docker $(COMP) up

down:
	docker $(COMP) down

build:
	docker $(COMP) build

create:
	docker $(COMP) create

enter:
	docker $(COMP) exec web /bin/bash

migrate:
	docker $(COMP) exec web python manage.py migrate

sync:
	rsync $(RSYNC_COMMON) . $(SERVER_APP) && ssh $(SERVER) "mv $(APPDIR)/Makefile_server $(APPDIR)/Makefile"

testsync:
	rsync $(RSYNC_COMMON) -n . $(SERVER_APP)

