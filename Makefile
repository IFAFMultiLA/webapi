COMPFILE := docker/compose_dev.yml
COMP := compose -f $(COMPFILE)
EXEC := $(COMP) exec web
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
	docker $(EXEC) /bin/bash

superuser:
	docker $(EXEC) python manage.py createsuperuser

djangoshell:
	docker $(EXEC) python manage.py shell

migrate:
	docker $(EXEC) python manage.py migrate

dump:
	docker $(EXEC) python manage.py dumpdata -o /fixtures/dump-`date -Iseconds`.json.gz

sync:
	rsync $(RSYNC_COMMON) . $(SERVER_APP) && ssh $(SERVER) "mv $(APPDIR)/Makefile_server $(APPDIR)/Makefile"

testsync:
	rsync $(RSYNC_COMMON) -n . $(SERVER_APP)

