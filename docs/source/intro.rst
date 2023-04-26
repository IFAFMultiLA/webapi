.. _intro:

MultiLA Web API and administration backend
==========================================

Markus Konrad <markus.konrad@htw-berlin.de>, April 2023

Technical requirements
----------------------

- Docker with Docker Compose v2 (recommended: run Docker in *rootless* mode)

  - all you need is to `install the Docker Engine <https://docs.docker.com/engine/install/>`_ for your operating system
    (Docker Desktop is  optional)
  - it is recommended to `set up Docker in rootless mode <https://docs.docker.com/engine/security/rootless/>`_ if your
    operating system supports it

- Python 3.11 if not running the web application in a Docker container
  (see *Option 1: Using a venv on the local machine*)

Software and frameworks used in this project
--------------------------------------------

- Python 3.11
- `Django 4.2 <https://www.djangoproject.com/>`_ as web framework with
  `Django REST framework extension package <https://www.django-rest-framework.org/>`_
- PostgreSQL database


Relevant documentation parts in used frameworks
-----------------------------------------------

Django:

- Models and databases (`tutorial <https://docs.djangoproject.com/en/4.2/intro/tutorial02/>`_ /
  `topic guide <https://docs.djangoproject.com/en/4.2/topics/db/>`_)
- Views (`tutorial <https://docs.djangoproject.com/en/4.2/intro/tutorial03/>`_ /
  `topic guide <https://docs.djangoproject.com/en/4.2/topics/http/views/>`_)
- Automated admin interface (`tutorial <https://docs.djangoproject.com/en/4.2/intro/tutorial02/>`_ /
  `documentation <https://docs.djangoproject.com/en/4.2/#the-admin>`_)
- Testing (`topic guide <https://docs.djangoproject.com/en/4.2/topics/testing/>`_)

Django REST framework:

- `Serialization <https://www.django-rest-framework.org/tutorial/1-serialization/>`_
- `Requests and Responses <https://www.django-rest-framework.org/tutorial/2-requests-and-responses/>`_
- `Testing <https://www.django-rest-framework.org/api-guide/testing/>`_
