.. _components:

Software components
===================

Overview
--------

The following images show an overview of the MultiLA platform components:

.. image:: img/sw-architektur1.png

.. image:: img/sw-architektur2.png

.. image:: img/sw-architektur3.png

.. image:: img/sw-architektur4.png

.. image:: img/sw-architektur5.png

.. image:: img/sw-architektur6.png

Further information on components
---------------------------------

- the web API is central and provides a common platform for setting up client applications, configuring and sharing
  them, and tracking user data and feedback
- all data – user generated or operational – is stored in the database

  - only the web API service has direct access to the database – client applications cannot access the database directly

- for *learnr* based client applications, there is a package *adaptivelearnr* that provides all necessary (JavaScript)
  code to interact with the web API and to make client applications *configurable*

  - this allows to quickly create several client applications that share the same code for interfacing with the web API
    and that can be configured in some details (e.g. including/excluding certain sections, aesthetic changes, etc.)

- external services are optional so far
