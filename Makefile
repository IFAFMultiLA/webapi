COMPFILE := docker/compose.yml

dockerup:
	docker compose -f $(COMPFILE) up

dockerdown:
	docker compose -f $(COMPFILE) down

dockerbuild:
	docker compose -f $(COMPFILE) build

dockercreate:
	docker compose -f $(COMPFILE) create

dockerenter:
	docker compose -f $(COMPFILE) exec web /bin/bash

migrate:
	docker compose -f $(COMPFILE) exec web python manage.py migrate

