.. _deployment:

Server deployment
=================

Prerequisites
-------------

- Docker with Docker Compose v2 (recommended: run Docker in *rootless* mode)
- an HTTP server such as Apache or nginx used as proxy
- a valid SSL certificate – **only run this service via HTTPS in production!**

Initial deployment
------------------

1. Create a Docker Compose configuration like the following as ``docker/compose_prod.yml``:

.. code-block:: yaml

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

2. Make sure the correct server and directory is entered in ``Makefile`` under ``SERVER`` and ``APPDIR``. Then run
   ``make sync`` to upload all files to the server.
3. On the server, do the following:

   - run ``make build`` to build the web application
   - run ``make create`` to create the docker containers
   - run ``make up`` to launch the containers
   - run ``make migrate`` to initialize the DB
   - run ``make superuser`` to create a backend admin user
   - run ``make check`` to check the deployment
   - you may run ``make logs`` and/or ``curl http://0.0.0.0:8000/`` to check if the web server is running

4. On the server, create an HTTP proxy to forward HTTP requests to the server to the docker container running the web
   application. For example, a configuration for the Apache webserver would use the following::

    ProxyPass /api/ http://0.0.0.0:8000/
    ProxyPassReverse /api/ http://0.0.0.0:8000/

All requests to ``https://<SERVER>/api/`` should then be forwarded to the web application.

Publishing updates
------------------

- locally, run ``make testsync`` and ``make sync`` to publish updated files to the server
- on the server, optional run ``make migrate`` to update the database and run ``make restart_web`` to restart the web
  application
