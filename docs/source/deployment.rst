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
      # # optional: DB admin web interface accessible on local port 8081
      # adminer:
      #  image: adminer
      #  ports:
      #    - 127.0.0.1:8081:8080
      #  restart: always

      db:
        image: postgres
        volumes:
          - '../data/db:/var/lib/postgresql/data'
          - '../data/backups:/data_backup'
        environment:
          - 'POSTGRES_USER=admin'
          - 'POSTGRES_PASSWORD=<CHANGE_THIS>'
          - 'POSTGRES_DB=multila'
        restart: always

      web:
        build:
          context: ..
          dockerfile: ./docker/Dockerfile_prod
        command: python -m uvicorn --host 0.0.0.0 --port 8000 multila.asgi:application
        volumes:
          - '../src:/code'
          - '../data/export:/data_export'
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
        restart: always


2. Make sure the correct server and directory is entered in ``Makefile`` under ``SERVER`` and ``APPDIR``. Then run:

    - ``make collectstatic`` to copy all static files to the ``static_files`` directory
    - ``make sync`` to upload all files to the server

3. On the server, do the following:

    - run ``make copy_static`` to copy the static files to the directory ``/var/www/api_static_files/`` (you must have
      the permissions to do so)
    - run ``make build`` to build the web application
    - run ``make create`` to create the docker containers
    - run ``make up`` to launch the containers
    - run ``make migrate`` to initialize the DB
    - run ``make superuser`` to create a backend admin user – **use a secure password**
    - run ``make check`` to check the deployment
    - run ``make test`` to run the tests in the deployment environment
    - you may run ``make logs`` and/or ``curl http://0.0.0.0:8000/`` to check if the web server is running

4. On the server, create an HTTP proxy to forward HTTP requests to the server to the docker container running the web
   application. For example, a configuration for the Apache webserver that forwards all requests to
   ``https://<HOST>/api/`` would use the following::

    # setup static files (and prevent them to be passed through the proxy)
    ProxyPass /api_static_files !
    Alias /api_static_files /var/www/api_static_files

    # setup proxy for API
    ProxyPass /api/ http://0.0.0.0:8000/
    ProxyPassReverse /api/ http://0.0.0.0:8000/

All requests to ``https://<SERVER>/api/`` should then be forwarded to the web application.

Publishing updates
------------------

- locally, run ``make testsync`` and ``make sync`` to publish updated files to the server
- on the server, optional run ``make migrate`` to update the database and run ``make restart_web`` to restart the web
  application (there is a shortcut ``make server_restart_web`` that you can run *locally* in order to restart the web
  application on the server)
- if there are changes in the static files, you should run ``make collectstatic`` before ``make sync`` and then run
  ``make copy_static`` on the server
- if there are changes in the dependencies, you need to rebuild the container as decribed above under
  *Initial deployment*, point (3)

Optional DB administration interface
------------------------------------

If you have enabled the ``adminer`` service in the docker compose file above, a small DB administration web interface
is running on port 8081 on the server. For security reasons, it is only accessible from localhost, i.e. you need to set
up an SSH tunnel to make it available remotely from your machine. You can do so on your machine by running::

    ssh -N -L 8081:localhost:8081 <USER>@<SERVER>

, where ``<USER>@<SERVER>`` are the login name and the host name of the server, where docker containers are running.
A shortcut is available in the Makefile as ``adminer_tunnel``. You can then go to ``http://localhost:8081/`` in your
browser and login to the Postgres server (not MySQL!) using the ``POSTGRES_USER`` and ``POSTGRES_PASSWORD`` listed in
the environment variabless of the docker compose file.

DB backups
----------

You can use ``make dbbackup`` on the server to generate a PostgreSQL database dump with the current timestamp under
``data/backups/``. It's advisable to run this command regularly, e.g. via a cronjob, and then copy the database dumps
to a backup destination e.g. via ``make download_dbbackup``.
