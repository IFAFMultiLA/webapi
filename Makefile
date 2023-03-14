COMPFILE := docker/compose.yml

dockerup:
	docker compose -f $(COMPFILE) up

dockerdown:
	docker compose -f $(COMPFILE) down

dockerbuild:
	docker compose -f $(COMPFILE) build web

dockerenter:
	docker compose -f $(COMPFILE) exec web /bin/bash

