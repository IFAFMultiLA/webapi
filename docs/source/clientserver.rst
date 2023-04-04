.. _clientserver:

Client-server communication
===========================

- client-server communication happens on the basis of a RESTful web API implemented in this repository
- the API is self-documented using an OpenAPI schema (TODO)


Client-server communication flowchart
-------------------------------------

Without login ("anonymous")
^^^^^^^^^^^^^^^^^^^^^^^^^^^

- doesn't require an account
- user authentication is based on a user token that is generated on first visit and then stored to cookies for re-use

.. image:: img/client-server-noauth.png


With login
^^^^^^^^^^

- requires that the user has registered an account with email and password

.. image:: img/client-server-login.png
