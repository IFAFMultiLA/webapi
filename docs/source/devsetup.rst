.. _devsetup:

Local development setup
=======================

There are two ways to set up a local development environment: either by using a Python virtual environment *(venv)*
on the local machine to run the Python interpreter or to by using a Python interpreter inside a docker container.

Option 1: Using a venv on the local machine
-------------------------------------------

- create a Python 3.11 virtual environment and activate it
- install the required packages via pip: ``pip install -r requirements.txt``
- create a project in your IDE, set up the Python interpreter as the one you just created in the virtual environment
- copy ``docker/compose_dev_db_only.yml`` to ``docker/compose_dev.yml``
- start the docker services for the first time via ``make up`` or via your IDE's docker interface

  - **note:** the first start of the "web" service may fail, since the database is initialized in parallel and may not
    be ready yet when "web" is started – simply starting the services as second time should solve the problem

- optional: create a launch configuration for Django in your IDE or
- start the web application using the launch configuration in your IDE or use ``python src/manage.py runserver``

Option 2: Using a Python interpreter inside a docker container
--------------------------------------------------------------

- copy ``docker/compose_dev_full.yml`` to ``docker/compose_dev.yml``
- create a project in your IDE, set up a connection to Docker and set up to use the Python interpreter inside the
  ``multila-web`` service

  - for set up with PyCharm Professional, `see here <https://www.jetbrains.com/help/pycharm/using-docker-compose-as-a-remote-interpreter.html>`_

- start all services for a first time

  - **note:** the first start of the "web" service may fail, since the database is initialized in parallel and may not
    be ready yet when "web" is started – simply starting the services as second time should solve the problem

- alternatively, to manually control the docker services outside your IDE, use the commands specified in the Makefile:

  - ``make create`` to create the containers
  - ``make up`` to launch all services

Common set up steps for both options
------------------------------------

- when all services were started successfully, run ``make migrate`` to run the initial database migrations
- run ``make superuser`` to create a backend admin user
- the web application is then available under ``http://localhost:8000``
- a simple database administration web interface is then available under ``http://localhost:8080/admin``

Generating the documentation
----------------------------

- all documentation is written
  `reStructuredText <https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html>`_ using
  the Python documentation system `Sphinx <https://www.sphinx-doc.org/>`_
- the documentation source files are located under ``docs/source``
- different output formats can be produced using the Makefile in ``docs``, e.g. via ``make html``
- the generated documentation is then available under ``docs/build/<output_format>``
- a shortcut is available in the Makefile in the project root directory – you can run ``make docs`` from here
- note that generating a PDF of the documentation requires that the packages *texlive*, *texlive-latex-extra* and
  *latexmk* are installed
