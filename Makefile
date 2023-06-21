COMPFILE := docker/compose_dev.yml
COMP := compose -f $(COMPFILE)
EXEC := $(COMP) exec web
EXECDB := $(COMP) exec db
SERVER := htwserver-webapi
APPDIR := ~/api
SERVER_APP := $(SERVER):$(APPDIR)
RSYNC_COMMON := -rcv --exclude-from=.rsyncexclude
NOW := date -Is | sed "s/://g" | cut -d+ -f 1

.PHONY: docs

up:
	docker $(COMP) up

down:
	docker $(COMP) down

build:
	docker $(COMP) build

create:
	docker $(COMP) create

enter:
	docker $(EXEC) /bin/bash || echo "web container is not running"

superuser:
	docker $(EXEC) python manage.py createsuperuser || python src/manage.py createsuperuser

djangoshell:
	docker $(EXEC) python manage.py shell || python src/manage.py shell

migrations:
	docker $(EXEC) python manage.py makemigrations || python src/manage.py makemigrations

migrate:
	docker $(EXEC) python manage.py migrate || python src/manage.py migrate

dump:
	docker $(EXEC) python manage.py dumpdata -o /fixtures/dump-`$(NOW)`.json.gz || python src/manage.py dumpdata -o /fixtures/dump-`$(NOW)`.json.gz

dbbackup:
	docker $(EXECDB) /bin/bash -c 'pg_dump -U admin -F c multila > /data_backup/local_dev_multila-`$(NOW)`.pgdump'

collectstatic:
	docker $(EXEC) python manage.py collectstatic || python src/manage.py collectstatic

test:
	docker $(EXEC) python manage.py test api || python src/manage.py test api

docs:
	cd docs && make clean && make html && make latexpdf
	pandoc -o data/codebook.pdf docs/source/codebook.rst

sync:
	rsync $(RSYNC_COMMON) . $(SERVER_APP) && ssh $(SERVER) "mv $(APPDIR)/Makefile_server $(APPDIR)/Makefile"

testsync:
	rsync $(RSYNC_COMMON) -n . $(SERVER_APP)

download_dbbackup:
	rsync -rcv $(SERVER_APP)/data/backups/ ./data/backups/

adminer_tunnel:
	ssh -N -L 8081:localhost:8081 $(SERVER)

server_restart_web:
	ssh $(SERVER) 'cd $(APPDIR) && make restart_web'
