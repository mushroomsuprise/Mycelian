Modules Documentation
====================

This section contains the complete API documentation for all Mycelian modules, automatically generated from the source code.

Overview
--------

Mycelian is a comprehensive streaming toolkit with the following major components:

- **Alert System**: Custom alert processing for Twitch events (follows, subs, bits, etc.)
- **Chatbot System**: Advanced Twitch chatbot with commands, events, and automated responses
- **Connector Automation**: Trigger-action workflows for stream automation
- **Web Engine**: Flask-based server for browser sources and real-time data
- **Service Integrations**: Support for Twitch, Spotify, PlayStation Network, and StreamLabs
- **Desktop UI**: NiceGUI-based interface for configuration and management

Core Modules
------------

Main Application
~~~~~~~~~~~~~~~~

.. automodule:: main
   :members:
   :undoc-members:
   :show-inheritance:

Configuration Management
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.config_manager
   :members:
   :undoc-members:
   :show-inheritance:

Database Management
~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.database_manager
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.database_init
   :members:
   :undoc-members:
   :show-inheritance:

Statistics Manager
~~~~~~~~~~~~~~~~~~

.. automodule:: modules.statistics_manager
   :members:
   :undoc-members:
   :show-inheritance:

Service Integration Modules
---------------------------

Twitch Integration
~~~~~~~~~~~~~~~~~~

.. automodule:: modules.twitch
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.twitch_api_reference
   :members:
   :undoc-members:
   :show-inheritance:

Spotify Integration
~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.spotify
   :members:
   :undoc-members:
   :show-inheritance:

PlayStation Network
~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.psn_service
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.psnapi
   :members:
   :undoc-members:
   :show-inheritance:

StreamLabs
~~~~~~~~~~

.. automodule:: modules.streamlabs
   :members:
   :undoc-members:
   :show-inheritance:

OBS Studio Integration
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.obs
   :members:
   :undoc-members:
   :show-inheritance:

Chatbot System
--------------

The Chatbot system provides comprehensive Twitch chat bot functionality including commands, events, quotes, and automated responses.

Chatbot API Integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.chatbot
   :members:
   :undoc-members:
   :show-inheritance:

Chatbot Core
~~~~~~~~~~~~

.. automodule:: modules.chatbot_core
   :members:
   :undoc-members:
   :show-inheritance:

Chatbot Manager
~~~~~~~~~~~~~~~

.. automodule:: modules.chatbot_manager
   :members:
   :undoc-members:
   :show-inheritance:

Alert System
------------

Alert Processing
~~~~~~~~~~~~~~~~

.. automodule:: modules.alert_processor
   :members:
   :undoc-members:
   :show-inheritance:

Alert Utilities
~~~~~~~~~~~~~~~

.. automodule:: modules.alertutils
   :members:
   :undoc-members:
   :show-inheritance:

Alert Parsing
~~~~~~~~~~~~~

.. automodule:: modules.alerts_parser
   :members:
   :undoc-members:
   :show-inheritance:

User Interface
--------------

Main UI Window
~~~~~~~~~~~~~~

.. automodule:: modules.mainuiwindow
   :members:
   :undoc-members:
   :show-inheritance:

Web Engine
~~~~~~~~~~

.. automodule:: modules.web_engine
   :members:
   :undoc-members:
   :show-inheritance:

UI Windows
~~~~~~~~~~

.. automodule:: modules.uiwindows.settings
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.alertsettings
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.activity_feed
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.customsources
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.sourcecontrols
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.connectors
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.uiwindows.chatbot
   :members:
   :undoc-members:
   :show-inheritance:

Connector Automation System
----------------------------

The Connector system provides a powerful automation framework for creating trigger-action workflows that respond to stream events automatically.

Core Connector Framework
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.connector_core
   :members:
   :undoc-members:
   :show-inheritance:

Connector Manager
~~~~~~~~~~~~~~~~~

.. automodule:: modules.connector_manager
   :members:
   :undoc-members:
   :show-inheritance:

Trigger System
~~~~~~~~~~~~~~

.. automodule:: modules.connector_triggers
   :members:
   :undoc-members:
   :show-inheritance:

Action System
~~~~~~~~~~~~~

.. automodule:: modules.connector_actions
   :members:
   :undoc-members:
   :show-inheritance:

Service Integration
~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.connector_integration
   :members:
   :undoc-members:
   :show-inheritance:

Connector Examples
~~~~~~~~~~~~~~~~~~

.. automodule:: modules.connector_examples
   :members:
   :undoc-members:
   :show-inheritance:

Hotkey System
~~~~~~~~~~~~~

.. automodule:: modules.hotkey_listener
   :members:
   :undoc-members:
   :show-inheritance:

Utility Modules
---------------

Data Objects
~~~~~~~~~~~~

.. automodule:: modules.dataobjects
   :members:
   :undoc-members:
   :show-inheritance:

Path Utilities
~~~~~~~~~~~~~~

.. automodule:: modules.path_utils
   :members:
   :undoc-members:
   :show-inheritance:

Encryption Utilities
~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.encryption_utils
   :members:
   :undoc-members:
   :show-inheritance:

API Credentials Manager
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.api_credentials_manager
   :members:
   :undoc-members:
   :show-inheritance:

Template Configuration
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: modules.template_config_parser
   :members:
   :undoc-members:
   :show-inheritance:

Preview Mappings
~~~~~~~~~~~~~~~~

.. automodule:: modules.preview_mappings
   :members:
   :undoc-members:
   :show-inheritance:

Migration and Maintenance
------------------------

Database Migration
~~~~~~~~~~~~~~~~~~

.. automodule:: modules.alert_database_migration
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.alerts_migration
   :members:
   :undoc-members:
   :show-inheritance:

Updater
~~~~~~~

.. automodule:: modules.updater
   :members:
   :undoc-members:
   :show-inheritance:

Build System
~~~~~~~~~~~~

.. automodule:: build
   :members:
   :undoc-members:
   :show-inheritance: 