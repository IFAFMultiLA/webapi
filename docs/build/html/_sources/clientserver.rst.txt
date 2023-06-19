.. _clientserver:

Client-server communication
===========================

- client-server communication happens on the basis of a RESTful web API implemented in this repository
- the implementation is done in ``api/views.py``
- the API exposes an OpenAPI schema under the URL ``http[s]://<HOST>/openapi`` when ``settings.DEBUG`` is ``True``


Client-server communication flowchart
-------------------------------------

- an application session may either require a login or not – this can be configured in the administration backend for
  each application session as "authentication mode"
- all API endpoints except for ``session/`` and ``session_login/`` require an HTTP authorization token, a.k.a
  "user token", even when no login is required
- this makes sure that each request to the API is linked to a user – either to a registered user (when a login is
  required) or to an anonymous user that is only identified with a unique code (when no login is required)


Without login ("anonymous")
^^^^^^^^^^^^^^^^^^^^^^^^^^^

- doesn't require an account
- user authentication is based on a user token that is generated on first visit and then stored to cookies for re-use

.. image:: img/client-server-noauth.png


With login
^^^^^^^^^^

- requires that the user has registered an account with email and password

.. image:: img/client-server-login.png


Application configuration
-------------------------

- to each application, a configuration can be passed when starting the application session
- the API sends the configuration as JSON object after the initial session request (anonymous session) or after log in
  (session that requires login) – this is displayed as ``app_config`` key in the above figures
- on the client side, the *adaptivelearnr* R package handles reading the configuration and setting up the application
  accordingly
- the application configuration JSON object has the following format and options::

    {
      "exclude": [<HTML element IDs to exclude>],
      "js": [<additional JavaScript files to load>],
      "css": [<additional CSS files to load>],
      "tracking": {
        "mouse": <bool>,  # enable/disable mouse tracking w/ mus.js
        "inputs": <bool>  # enable/disable tracking of input changes
      }
    }
