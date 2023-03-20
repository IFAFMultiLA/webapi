# MultiLA Web API and administration backend

Markus Konrad <markus.konrad@htw-berlin.de>, March 2023

## Requirements

- Docker with Docker Compose v2 (recommended: run Docker in *rootless* mode)
- recommended: IDE with Docker Compose support (e.g. PyCharm Professional, VSCode)

## Software and frameworks used in this project

- Python 3.11
- Django 4.1 as web framework with djangorestframework extension package 
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

There are two ways to set up a local development environment: either by using a Python virtual environment *(venv)*
on the local machine to run the Python interpreter or to by using a Python interpreter inside a docker container.

## Option 1: Using a venv on the local machine

- create a Python 3.11 virtual environment and activate it
- install the required packages via pip: `pip install -r requirements.txt`
- create a project in your IDE, set up the Python interpreter as the one you just created in the virtual environment
- copy `docker/compose_dev_db_only.yml` to `docker/compose_dev.yml`
- create a launch configuration for Django in your IDE and make sure to set the following environment variables:

```
DJANGO_SETTINGS_MODULE=multila.settings
POSTGRES_DB=multila
POSTGRES_USER=admin
POSTGRES_PASSWORD=admin
```

- start the docker services for the first time via `make up` or via your IDE's docker interface
  - **note:** the first start of the "web" service may fail, since the database is initialized in parallel and may not
    be ready yet when "web" is started – simply starting the services as second time should solve the problem
- start the web application using the launch configuration in your IDE

## Option 2: Using a Python interpreter inside a docker container

- copy `docker/compose_dev_full.yml` to `docker/compose_dev.yml`
- create a project in your IDE, set up a connection to Docker and set up to use the Python interpreter inside the
  `multila-web` service 
  - for set up with PyCharm Professional,
   [see here](https://www.jetbrains.com/help/pycharm/using-docker-compose-as-a-remote-interpreter.html)
- start all services for a first time
  - **note:** the first start of the "web" service may fail, since the database is initialized in parallel and may not
    be ready yet when "web" is started – simply starting the services as second time should solve the problem
- alternatively, to manually control the docker services outside your IDE, use the commands specified in the Makefile:
  - `make create` to create the containers
  - `make up` to launch all services

### Common set up steps for both options

- when all services were started successfully, run `make migrate` to run the initial database migrations
- run `make superuser` to create a backend admin user
- the web application is then available under `http://localhost:8000`
- a simple database administration web interface is then available under `http://localhost:8080`

## Deployment

### Initial deployment

1. Create a Docker Compose configuration like the following as `docker/compose_prod.yml`: 

```yaml
version: '2'

services:
  db:
    image: postgres
    volumes:
      - '../data/db:/var/lib/postgresql/data'
    environment:
      - 'POSTGRES_USER=admin'
      - 'POSTGRES_PASSWORD=<CHANGE_THIS>'
      - 'POSTGRES_DB=multila'
  web:
    build:
      context: ..
      dockerfile: ./docker/Dockerfile_prod
    command: python -m uvicorn --host 0.0.0.0 --port 8000 multila.asgi:application
    volumes:
      - ../src:/code
    ports:
      - "8000:8000"
    environment:
      - 'POSTGRES_USER=admin'
      - 'POSTGRES_PASSWORD=<CHANGE_THIS>'
      - 'POSTGRES_DB=multila'
      - 'DJANGO_SETTINGS_MODULE=multila.settings_prod'
      - 'SECRET_KEY=<CHANGE_THIS>'
    depends_on:
      - db
```

2. Make sure the correct server and directory is entered in `Makefile` under `SERVER` and `APPDIR`. Then run
   `make sync` to upload all files to the server.
3. On the server, do the following:
   - run `make build` to build the web application
   - run `make create` to create the docker containers
   - run `make up` to launch the containers
   - run `make migrate` to initialize the DB
   - run `make superuser` to create a backend admin user
   - run `make check` to check the deployment
   - you may run `make logs` and/or `curl http://0.0.0.0:8000/` to check if the web server is running
4. On the server, create an HTTP proxy to forward HTTP requests to the server to the docker container running the web
   application. For example, a configuration for the Apache webserver would use the following: 

```
ProxyPass /api/ http://0.0.0.0:8000/
ProxyPassReverse /api/ http://0.0.0.0:8000/
```

All requests to `https://<SERVER>/api/` should then be forwarded to the web application.

### Publishing updates

- locally, run `make testsync` and `make sync` to publish updated files to the server
- on the server, optional run `make migrate` to update the database and run `make restart_web` to restart the web
  application
