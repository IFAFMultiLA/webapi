# MultiLA Web API and administration backend

## Requirements

- Docker with Docker Compose v2 (recommended: run Docker in *rootless* mode)
- recommended: IDE with Docker Compose support (e.g. PyCharm Professional, VSCode)

## Software and frameworks used in this project

- Django as web framework with djangorestframework extension package 
- PostgreSQL database

## Docker services

The Docker Compose file under `docker/compose.yml` defines the following services:

- `web`: the Django web application developed under `src/` and build via `docker/Dockerfile`
  - exposes port 8000
- `db`: the PostgreSQL database
  - does not expose any ports
- `adminer`: simple database administration web interface for development purposes
  - exposes port 8080

## Local development setup

- create a project in your IDE, set up a connection to Docker and set up to use the Python interpreter inside the
  `multila-web` service 
  - for set up with PyCharm Professional,
   [see here](https://www.jetbrains.com/help/pycharm/using-docker-compose-as-a-remote-interpreter.html)
- start all services for a first time
  - **note:** the first start of the "web" service may fail, since the database is initialized in parallel and may not
    be ready yet when "web" is started – simply starting the services as second time should solve the problem
- when all services were started successfully, run `make migrate` to run the initial database migrations
- alternatively, to manually control the docker services outside your IDE, use the commands specified in the Makefile:
  - `make dockercreate` to create the containers
  - `make dockerup` to launch all services
- the web application is then available under `http://0.0.0.0:8000`
- a simple database administration web interface is then available under `http://0.0.0.0:8080`

## Deployment

TODO