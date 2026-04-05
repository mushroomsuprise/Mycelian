Mycelian Documentation
======================

Mycelian is a comprehensive streaming toolkit that provides custom alert systems, interactive browser sources, multi-platform integrations, and powerful automation workflows for Twitch streamers. Built with Python and Flask, it offers both a desktop application interface and a powerful web-based template system with an advanced connector automation framework.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   installation
   usage
   templates
   connectors
   chatbot
   custom_variables
   modules

Overview
--------

Mycelian provides streamers with a complete solution for managing alerts, creating custom browser sources, and integrating with popular streaming services. The system consists of a desktop application for configuration and management, plus a Flask web server that serves customizable HTML templates as browser sources.

**Core Components:**

* **Desktop Application** - Tabbed interface for managing alerts, settings, template configurations, connectors, and chatbot
* **Web Server** - Flask-based server serving HTML templates at ``http://localhost:5000``
* **Template System** - Customizable HTML/CSS/JavaScript templates with JSON configuration files
* **WebSocket Communication** - Real-time data exchange between templates and the application
* **Connector System** - Advanced automation framework for creating trigger-action workflows
* **Chatbot System** - Interactive chat commands, automatic responses, quotes, personalized greetings, and giveaways
* **Service Integrations** - OAuth-based connections to Twitch, Spotify, PlayStation Network, and more

**Platform Integrations:**

* **Twitch API** - Followers, subscribers, bits, channel points, chat integration
* **Spotify** - Now playing display, track history, album artwork
* **PlayStation Network** - Trophy notifications, game status, achievement displays
* **StreamLabs** - Donation and event processing
* **OBS Studio** - Scene switching and source control (via WebSocket)

Features
--------

**Alert System:**
* Real-time processing of follows, subs, bits, donations, raids, and channel points
* PSN trophy notifications with game artwork
* Customizable GIF animations and audio for each alert type
* Alert history with pagination and replay functionality
* Pause/resume controls with activity feed monitoring

**Connector Automation System:**
* Visual trigger-action workflow designer with no-code interface
* 15+ trigger types including Twitch events, donations, timers, and manual triggers
* 9+ action types including template controls, chat messages, API calls, file operations, and system commands
* Advanced conditional logic with field comparison operators
* Real-time testing and debugging capabilities
* Integration with existing template system for dynamic control
* Statistics and monitoring for automation performance

**Template System:**
* Custom HTML templates with WebSocket integration
* JSON configuration files for user-friendly settings
* Real-time template updates without page reloads
* Asset management for images, sounds, and fonts
* Interactive controls for counters, wheels, and dynamic content

**Desktop Interface:**
* **Activity Feed** - Real-time and historical alert monitoring
* **Settings** - Service integrations and OAuth management
* **Custom Sources** - Template configuration with visual form builders
* **Source Controls** - Interactive template control panels
* **Connectors** - Visual automation workflow designer and management interface

**Web Browser Sources:**
* Served at ``http://localhost:5000/{template_name}``
* Compatible with OBS Studio browser sources
* Real-time WebSocket communication
* Responsive design with customizable styling
* Audio/visual effects with volume controls

Quick Start
-----------

1. **Install and Launch Mycelian:**
   
   Follow the :doc:`installation` guide to set up Mycelian and launch the desktop application.

2. **Configure Service Integrations:**
   
   Use the **Settings** tab to connect your streaming services:
   
   * Set up Twitch OAuth for alert integration
   * Connect Spotify for now-playing displays
   * Configure PSN for trophy notifications
   * Add StreamLabs credentials

3. **Set Up Browser Sources in OBS:**
   
   Add browser sources pointing to ``http://localhost:5000/{template_name}``:
   
   * ``http://localhost:5000/activity_feed`` - Alert activity feed
   * ``http://localhost:5000/bitboss`` - Interactive bit boss game
   * ``http://localhost:5000/chat`` - Chat integration
   * ``http://localhost:5000/counter`` - Interactive counters

4. **Customize Templates:**
   
   Use the **Custom Sources** tab to configure template settings, or create your own following the :doc:`templates` guide.

5. **Set Up Automation Workflows:**
   
   Use the **Connectors** tab to create automated trigger-action workflows:
   
   * Create connectors that respond to stream events
   * Set up conditions for when automations should trigger
   * Configure actions like template controls, chat messages, or API calls
   * Test connectors with sample data before going live

6. **Monitor and Control:**
   
   Use the **Activity Feed** tab to monitor alerts and the **Source Controls** tab for real-time template interaction.

Template Development
--------------------

Mycelian's template system allows for extensive customization:

**Getting Started with Templates:**
* Read the comprehensive :doc:`templates` guide
* Explore existing templates in the ``templates/`` directory
* Create JSON configuration files for user-friendly settings
* Use WebSocket integration for real-time data

**Template Features:**
* HTML/CSS/JavaScript with Socket.IO integration
* JSON configuration system with various input types
* Asset management for images, sounds, and fonts
* Real-time updates via WebSocket communication
* Interactive controls and dynamic content

**Example Templates:**
* **BitBoss** - Interactive boss battle using bits and subs
* **Activity Feed** - Scrolling display of stream events
* **Chat Integration** - Real-time chat with emote support
* **Now Playing** - Spotify track display with artwork
* **Counters & Timers** - Interactive counting displays
* **Roulette Wheels** - Customizable spinning wheels

API Reference
-------------

.. toctree::
   :maxdepth: 4

   modules/modules

The API documentation provides detailed information about all modules, classes, and functions available in Mycelian.

WebSocket Events
----------------

Mycelian uses WebSocket communication for real-time interaction between templates and the application. See the :doc:`usage` guide for complete WebSocket event documentation including:

* Alert system events (``next_alert``, ``alert_complete``)
* Service integration events (Twitch, Spotify, PSN data)
* Template control events (counters, interactive elements)
* Database management events (``set_data``, ``get_data``)
* Configuration loading (``/api/all-template-configs``)

Documentation Sections
---------------------

* :doc:`installation` - Setup instructions and requirements
* :doc:`usage` - Desktop application interface and WebSocket API reference
* :doc:`templates` - Complete guide to creating custom templates and configurations
* :doc:`connectors` - Complete guide to the automation and connector system
* :doc:`chatbot` - Complete guide to the chat automation and command system
* :doc:`modules` - Python API documentation for developers

Common Use Cases
----------------

**Stream Overlay Setup:**
* Create custom alert displays with GIFs and sounds
* Add now-playing music displays with Spotify integration
* Set up interactive elements like counters and wheels
* Display chat with emote support

**Interactive Streaming:**
* BitBoss games where viewers battle with bits and subscriptions
* Roulette wheels for viewer decision making
* Goal tracking with progress bars
* Real-time viewer interaction controls

**Stream Automation:**
* Automated responses to follows, subs, bits, and donations
* Scheduled announcements and reminders
* Dynamic template controls triggered by viewer actions
* Cross-platform event automation and API integrations
* Conditional logic for sophisticated automation workflows

**Stream Management:**
* Historical alert tracking and replay
* Pause/resume alert functionality during stream breaks
* Multi-platform integration for comprehensive stream data
* Customizable configurations for different stream types

Getting Help
------------

**Troubleshooting:**
* Check the :doc:`usage` guide for common issues and solutions
* Verify browser source URLs and WebSocket connections
* Review template configuration files for syntax errors
* Test service integrations individually

**Template Development:**
* Follow the :doc:`templates` guide for comprehensive development information
* Use browser developer tools to debug WebSocket communication
* Test templates individually before integrating into OBS
* Start with existing templates and modify for your needs

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search` 