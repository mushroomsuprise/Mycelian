Connectors System
=================

The Mycelian Connectors System is a powerful automation framework that enables you to create sophisticated trigger-action workflows without writing any code. It provides a visual interface for building automation that responds to stream events, manages templates, controls system functions, and integrates with external services.

Overview
--------

The connector system consists of four main components:

**Connectors**
  The primary workflow container that links triggers to actions. Each connector processes events and executes actions when trigger conditions are met.

**Triggers**
  Event listeners that detect when something happens in your stream. Triggers can respond to Twitch events, donations, timers, hotkeys, webhooks, and more.

**Actions**
  The automated responses that execute when triggers fire. Actions can control templates, send chat messages, make API calls, execute system commands, and more.

**Conditions**
  Optional filters that add logic to triggers. Use conditions to create sophisticated rules like "only trigger when bit amount is greater than 100".

System Architecture
-------------------

The connector system is built with a modular architecture:

.. code-block:: text

    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
    │   Event Source  │───▶│   Connector     │───▶│   Action        │
    │                 │    │                 │    │   Execution     │
    │ • Twitch API    │    │ • Trigger       │    │                 │
    │ • Donations     │    │ • Conditions    │    │ • Template Ctrl │
    │ • Timers        │    │ • Actions       │    │ • Chat Messages │
    │ • Hotkeys       │    │                 │    │ • API Calls     │
    │ • Webhooks      │    └─────────────────┘    └─────────────────┘
    └─────────────────┘

**Core Files:**
  * ``connector_core.py`` - Core data structures and base classes
  * ``connector_manager.py`` - Main manager for processing and persistence
  * ``connector_actions.py`` - Action implementations
  * ``connector_triggers.py`` - Trigger implementations
  * ``connector_integration.py`` - Integration with Mycelian services
  * ``modules/uiwindows/connectors.py`` - Desktop interface

Getting Started
---------------

1. **Access the Connectors Interface**

   Launch Mycelian and navigate to the **Connectors** tab in the desktop application.

2. **Create Your First Connector**

   Click **"Create New Connector"** and give it a descriptive name like "Bits Celebration".

3. **Set Up a Trigger**

   Choose a trigger type (e.g., "Twitch Bits") and configure its settings.

4. **Add Conditions (Optional)**

   Add conditions to make the trigger more specific, such as "amount > 100".

5. **Configure Actions**

   Add one or more actions that will execute when the trigger fires.

6. **Test the Connector**

   Use the test functionality to verify your connector works as expected.

7. **Enable and Monitor**

   Enable the connector and monitor its activity in the connector list.

Triggers
--------

Triggers are the "when" part of your automation. Mycelian supports 15+ trigger types organized into categories:

Twitch Event Triggers
~~~~~~~~~~~~~~~~~~~~~

These triggers respond to events from your Twitch channel:

**Twitch Bits**
  Fires when someone cheers bits in your channel.

  *Fields:* ``amount``, ``username``, ``message``, ``timestamp``

  *Example Conditions:*
  * ``amount >= 100`` - Only trigger for 100+ bits
  * ``username == "specialuser"`` - Only trigger for specific user

**Twitch Subscription**
  Fires when someone subscribes to your channel.

  *Fields:* ``tier``, ``username``, ``message``, ``months``, ``timestamp``

**Twitch Resubscription**
  Fires when someone resubscribes to your channel.

  *Fields:* ``tier``, ``username``, ``message``, ``months``, ``streak_months``, ``timestamp``

**Twitch Gift Subscription**
  Fires when someone gifts a subscription to another viewer.

  *Fields:* ``tier``, ``username``, ``recipient_username``, ``months``, ``timestamp``

**Twitch Follow**
  Fires when someone follows your channel.

  *Fields:* ``username``, ``timestamp``

**Twitch Raid**
  Fires when another streamer raids your channel.

  *Fields:* ``username``, ``viewer_count``, ``timestamp``

**Twitch Channel Points**
  Fires when someone redeems a channel point reward.

  *Fields:* ``username``, ``reward_name``, ``reward_cost``, ``timestamp``

**Twitch Chat Message**
  Fires when someone sends a chat message.

  *Fields:* ``username``, ``message``, ``is_command``, ``command``, ``timestamp``

**Twitch Hype Train Start**
  Fires when a hype train begins in your channel.

  *Fields:* ``level``, ``progress``, ``goal``, ``timestamp``

**Twitch Hype Train End**
  Fires when a hype train ends in your channel.

  *Fields:* ``level``, ``total_progress``, ``timestamp``

Donation Triggers
~~~~~~~~~~~~~~~~~

**Donation**
  Fires when someone donates via StreamLabs.

  *Fields:* ``amount``, ``username``, ``message``, ``currency``, ``source``, ``timestamp``

Timer and Schedule Triggers
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Timer**
  Fires at regular intervals.

  *Configuration:*
  * ``interval_seconds`` - How often to trigger (minimum 60 seconds)

**Schedule**
  Fires at specific times (cron-like scheduling).

  *Configuration:*
  * ``schedule_pattern`` - Cron pattern (e.g., "0 */15 * * *" for every 15 minutes)
  * ``timezone`` - Timezone for scheduling

Manual Triggers
~~~~~~~~~~~~~~~

**Hotkey**
  Fires when a specific keyboard shortcut is pressed.

  *Configuration:*
  * ``key_combination`` - Key combination (e.g., "ctrl+shift+f", "f5")
  * ``modifiers`` - Modifier keys (ctrl, alt, shift, cmd)
  * ``is_global`` - Whether it works globally or only when app is focused

**Webhook**
  Fires when an HTTP webhook is received.

  *Configuration:*
  * ``webhook_url`` - Optional custom webhook endpoint
  * ``secret_key`` - Optional secret for webhook validation

Conditions
----------

Conditions add logic to triggers by comparing event data fields using various operators:

Comparison Operators
~~~~~~~~~~~~~~~~~~~~

* **Equal** (``==``) - Exact match
* **Not Equal** (``!=``) - Anything except the specified value
* **Greater Than** (``>``) - Numeric comparison
* **Greater Than or Equal** (``>=``) - Numeric comparison
* **Less Than** (``<``) - Numeric comparison
* **Less Than or Equal** (``<=``) - Numeric comparison
* **Contains** - Text contains substring (case-sensitive)
* **Not Contains** - Text does not contain substring
* **Starts With** - Text begins with specified value
* **Ends With** - Text ends with specified value
* **Regex Match** - Regular expression pattern matching

Field Paths
~~~~~~~~~~~

Conditions can reference nested data using dot notation:

* ``amount`` - Top-level field
* ``user.name`` - Nested field (if user is an object)
* ``metadata.custom_field`` - Custom metadata field

Example Conditions:

.. code-block:: json

    [
        {"field": "amount", "operator": "greater_than", "value": 100},
        {"field": "username", "operator": "not_equal", "value": "bot_user"},
        {"field": "message", "operator": "contains", "value": "special"}
    ]

Actions
-------

Actions are the "what" part of your automation - what happens when a trigger fires. Mycelian supports 12+ action types:

Template Control Actions
~~~~~~~~~~~~~~~~~~~~~~~~

**Template Control**
  Control interactive template elements via WebSocket.

  *Parameters:*
  * ``template_name`` - Target template (e.g., "counter", "roulette")
  * ``control_action`` - Action to perform (e.g., "increment", "spin", "reset")
  * ``control_data`` - Additional data for the action

  *Example:* Increment a counter when someone subscribes

**WebSocket Emit**
  Send custom WebSocket events to templates.

  *Parameters:*
  * ``event_name`` - Custom event name
  * ``event_data`` - Data to send with the event

Alert System Actions
~~~~~~~~~~~~~~~~~~~~

**Trigger Alert**
  Manually trigger alerts through the alert system.

  *Parameters:*
  * ``alert_type`` - Type of alert (e.g., "follow", "sub", "bits")
  * ``alert_id`` - Optional custom alert ID
  * ``alert_data`` - Alert data with placeholders

  *Example:* Trigger a custom alert with user-specific data

Chat Actions
~~~~~~~~~~~~

**Send Chat Message**
  Send a message to Twitch chat via the integrated chatbot system.

  *Parameters:*
  * ``message`` - Message text (supports placeholders)

  *Note:* Uses the chatbot system for message delivery with automatic fallback to main Twitch API if needed

Greeting Actions
~~~~~~~~~~~~~~~~

**Add Greeting**
  Add a new user greeting to the chatbot system.

  *Parameters:*
  * ``user_id`` - User's Twitch ID
  * ``username`` - User's display name
  * ``greeting_text`` - Custom greeting message
  * ``enabled`` - Whether greeting is active

**Update Greeting**
  Modify an existing user greeting.

  *Parameters:*
  * ``greeting_id`` - ID of greeting to update
  * ``greeting_text`` - New greeting text
  * ``enabled`` - Enable/disable greeting

**Send Greeting**
  Send a greeting to a user (even if already sent).

  *Parameters:*
  * ``user_id`` - User's Twitch ID
  * ``username`` - User's display name
  * ``force_send`` - Override normal greeting logic

API and System Actions
~~~~~~~~~~~~~~~~~~~~~~

**API Call**
  Make HTTP requests to external services.

  *Parameters:*
  * ``url`` - Target URL (supports placeholders)
  * ``method`` - HTTP method (GET, POST, PUT, DELETE, PATCH)
  * ``headers`` - Request headers
  * ``body`` - Request body (JSON)

  *Example:* Post stream events to a Discord webhook

**Write File**
  Write data to a file on disk.

  *Parameters:*
  * ``file_path`` - Target file path
  * ``content`` - Content to write
  * ``append`` - Whether to append or overwrite

**Execute Command**
  Run system commands.

  *Parameters:*
  * ``command`` - Command to execute
  * ``working_directory`` - Optional working directory
  * ``timeout_seconds`` - Command timeout (default 30s)

Input Control Actions
~~~~~~~~~~~~~~~~~~~~~

**Key Press**
  Simulate keyboard and mouse input.

  *Parameters:*
  * ``input_type`` - "key", "mouse", or "macro"
  * ``action_mode`` - "press", "hold", "release", "repeat"
  * ``key_sequence`` - Key combination (e.g., "ctrl+c", "left_click")
  * ``repeat_count`` - Number of repetitions
  * ``hold_duration`` - Duration for hold actions
  * ``macro_sequence`` - JSON string of macro steps

**Audio Control**
  Control system audio (volume, microphone, applications).

  *Parameters:*
  * ``control_type`` - "device" or "application"
  * ``action_mode`` - "set", "set_random", "increase", "decrease", "mute", "unmute", "toggle_mute"
  * ``volume_level`` - Target volume level (0-100)
  * ``volume_step`` - Increment/decrement amount
  * ``target_application`` - Application name for app-specific control
  * ``target_device`` - Audio device identifier
  * ``duration`` - Temporary change duration in seconds

Placeholders and Dynamic Data
------------------------------

All actions support placeholders for dynamic content using ``{{field_path}}`` syntax:

**Available Placeholders:**
  * ``{{amount}}`` - Bit amount, donation amount, etc.
  * ``{{username}}`` - User's display name
  * ``{{message}}`` - Chat message or donation message
  * ``{{tier}}`` - Subscription tier
  * ``{{months}}`` - Subscription months
  * ``{{timestamp}}`` - Event timestamp
  * ``{{random(min,max)}}`` - Random number between min and max

**Nested Access:**
  * ``{{user.display_name}}`` - Access nested object properties
  * ``{{metadata.custom_field}}`` - Access custom metadata

**Example Usage:**

.. code-block:: json

    {
        "message": "Thanks {{username}} for the {{amount}} bits! 🎉",
        "url": "https://api.example.com/alert?user={{username}}&amount={{amount}}",
        "content": "Stream event: {{username}} sent {{amount}} bits at {{timestamp}}"
    }

Creating Connectors
-------------------

Step-by-Step Guide
~~~~~~~~~~~~~~~~~~

1. **Open Connectors Interface**

   Navigate to the Connectors tab in the Mycelian desktop application.

2. **Create New Connector**

   Click "Create New Connector" and enter:
   * **Name:** Descriptive name (e.g., "Big Bits Celebration")
   * **Description:** Optional details about what this connector does

3. **Configure Trigger**

   * Select trigger type from dropdown
   * Configure trigger-specific settings
   * Add cooldown period if needed (seconds between activations)

4. **Add Conditions (Optional)**

   * Click "Add Condition" for each condition
   * Select field, operator, and value
   * Use "AND" logic - all conditions must be true

5. **Add Actions**

   * Click "Add Action" for each action
   * Select action type and configure parameters
   * Use placeholders for dynamic content
   * Actions execute in order

6. **Test Connector**

   * Click "Test Connector"
   * Review test results and execution details
   * Modify configuration as needed

7. **Enable Connector**

   * Toggle "Enabled" switch
   * Monitor activity in connector list

Advanced Features
-----------------

Cooldown Periods
~~~~~~~~~~~~~~~~

Prevent trigger spam by setting cooldown periods:

* **Trigger Level:** Applies to individual triggers
* **Global Level:** Can be implemented via conditions
* **Minimum:** 0 seconds (no cooldown)
* **Recommended:** 5-30 seconds depending on trigger frequency

Statistics and Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~~

Track connector performance:

* **Events Processed:** Total events received
* **Triggers Fired:** How many times conditions were met
* **Actions Executed:** Successful action executions
* **Error Count:** Failed executions
* **Uptime:** How long the connector has been active

Connector States
~~~~~~~~~~~~~~~~

* **Enabled:** Active and processing events
* **Disabled:** Inactive but preserved
* **Testing:** Temporary state for testing functionality

Error Handling
~~~~~~~~~~~~~~

* **Individual Action Failures:** Don't stop other actions
* **Logging:** All errors logged with context
* **Graceful Degradation:** System continues despite individual failures
* **Recovery:** Automatic retry for transient failures

Integration with Mycelian
-------------------------

Service Integration
~~~~~~~~~~~~~~~~~~~

The connector system automatically integrates with:

* **Twitch API:** All Twitch events fed into the connector system
* **Alert System:** Can trigger alerts and respond to alert events
* **Template System:** Real-time template control via WebSocket
* **Chat System:** Send messages and respond to chat events
* **Database:** Persistent storage of connector configurations

Event Flow
~~~~~~~~~~

1. **Event Occurs:** Twitch event, donation, timer, etc.
2. **Event Processing:** ConnectorIntegration receives event
3. **Event Formatting:** Converted to standardized EventData format
4. **Connector Processing:** All enabled connectors evaluate triggers
5. **Condition Evaluation:** Triggers check conditions against event data
6. **Action Execution:** Matching connectors execute their actions
7. **Result Logging:** Success/failure logged and tracked

Database Persistence
~~~~~~~~~~~~~~~~~~~~

Connectors are automatically saved to the Mycelian database:

* **Storage:** SQLite database (``mycelian.db``)
* **Key:** ``connectors`` in database
* **Format:** JSON serialization of connector objects
* **Backup:** Automatic backup on configuration changes

Example Connectors
------------------

Bits Celebration
~~~~~~~~~~~~~~~~~

**Trigger:** Twitch Bits (amount >= 100)

**Conditions:**
* ``amount >= 100``

**Actions:**
1. Template Control: ``counter`` → ``increment`` (increment by bit amount)
2. Send Chat: ``"Thanks {{username}} for the {{amount}} bits! 🎉"``
3. Audio Control: Set volume to random level for 10 seconds

Donation Thank You
~~~~~~~~~~~~~~~~~~

**Trigger:** Donation (any amount)

**Conditions:** None

**Actions:**
1. Trigger Alert: Custom donation alert with user data
2. Send Chat: ``"Thank you {{username}} for the ${{amount}} donation! {{message}}"``
3. WebSocket Emit: Send donation data to overlay templates

Raid Response
~~~~~~~~~~~~~

**Trigger:** Twitch Raid

**Conditions:**
* ``viewer_count >= 5``

**Actions:**
1. Send Chat: ``"Welcome raiders from @{{username}}! Thanks for the {{viewer_count}} viewers! 🚀"``
2. Template Control: ``raid_banner`` → ``show`` with raid data
3. Audio Control: Play raid sound effect

Scheduled Announcements
~~~~~~~~~~~~~~~~~~~~~~~

**Trigger:** Timer (every 30 minutes)

**Conditions:**
* ``random(1,10) == 1`` (10% chance to trigger)

**Actions:**
1. Send Chat: ``"Don't forget to follow and drop those bits! 💎"``
2. WebSocket Emit: Show reminder banner on stream

Hotkey Controls
~~~~~~~~~~~~~~~

**Trigger:** Hotkey (Ctrl+Shift+F)

**Conditions:** None

**Actions:**
1. Template Control: ``roulette`` → ``spin``
2. Audio Control: Play spin sound effect

Best Practices
--------------

Connector Design
~~~~~~~~~~~~~~~~

* **Single Responsibility:** Each connector should do one specific thing
* **Descriptive Names:** Use clear, descriptive connector names
* **Logical Grouping:** Group related connectors by function
* **Testing First:** Always test connectors before enabling live

Performance Considerations
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Condition Efficiency:** Use indexed fields when possible
* **Action Ordering:** Place critical actions first
* **Resource Limits:** Be mindful of system resources for frequent actions
* **Cooldown Usage:** Use appropriate cooldowns to prevent spam

Error Handling
~~~~~~~~~~~~~~

* **Fallback Actions:** Design connectors to work even if some actions fail
* **Monitoring:** Regularly check connector statistics
* **Logging:** Review logs for failed executions
* **Updates:** Test connectors after Mycelian updates

Maintenance
~~~~~~~~~~~

* **Regular Review:** Periodically review and update connectors
* **Documentation:** Document complex connector logic
* **Backup:** Keep backups of important connector configurations
* **Version Control:** Track connector changes over time

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Connector Not Triggering:**
* Check if connector is enabled
* Verify trigger conditions match event data
* Review cooldown settings
* Check for conflicting conditions

**Actions Not Executing:**
* Verify action parameters are correct
* Check for placeholder syntax errors
* Review error logs for specific failures
* Test actions individually

**Performance Issues:**
* Reduce frequency of high-cost actions
* Increase cooldown periods
* Optimize condition complexity
* Monitor system resource usage

**Integration Problems:**
* Verify service connections in Settings
* Check API credentials and permissions
* Review WebSocket connection status
* Test integrations individually

Debug Tools
~~~~~~~~~~~

* **Test Functionality:** Use built-in connector testing
* **Event Logging:** Review detailed event logs
* **Statistics:** Monitor connector performance metrics
* **Manual Testing:** Test connectors with sample data

API Reference
-------------

For developers extending the connector system:

**Core Classes:**
* ``BaseTrigger`` - Abstract base class for triggers
* ``BaseAction`` - Abstract base class for actions
* ``Connector`` - Main connector container
* ``ConnectorManager`` - System management and processing
* ``TriggerCondition`` - Condition evaluation logic

**Key Methods:**
* ``ConnectorManager.process_event()`` - Process events through connectors
* ``Connector.process_event()`` - Execute connector logic
* ``BaseTrigger.should_trigger()`` - Determine if trigger should fire
* ``BaseAction.execute()`` - Execute action logic

**Factory Functions:**
* ``create_trigger()`` - Instantiate trigger by type
* ``create_action()`` - Instantiate action by type

**Data Structures:**
* ``EventData`` - Standardized event format
* ``ComparisonOperator`` - Available condition operators
* ``TriggerType`` / ``ActionType`` - Available types

Extending the System
~~~~~~~~~~~~~~~~~~~~

To add custom triggers or actions:

1. Create a class inheriting from ``BaseTrigger`` or ``BaseAction``
2. Implement required abstract methods
3. Add the new type to ``TriggerType`` or ``ActionType`` enum
4. Update factory functions to instantiate your class
5. Add UI configuration in ``connectors.py``

Future Enhancements
-------------------

Planned features for the connector system:

* **Visual Workflow Designer:** Drag-and-drop connector creation
* **Connector Templates:** Pre-built connector configurations
* **Advanced Conditions:** Complex boolean logic (AND/OR/NOT)
* **Action Chains:** Conditional action execution based on previous results
* **External API Integration:** Expanded webhook and API capabilities
* **Performance Analytics:** Detailed performance monitoring
* **Import/Export:** Share connector configurations
* **Version History:** Track connector configuration changes
* **Collaboration:** Multi-user connector management

Support and Resources
---------------------

**Getting Help:**
* Review this documentation for detailed usage instructions
* Check the Mycelian logs for error messages and debugging information
* Test connectors individually to isolate issues
* Use the built-in test functionality to verify configurations

**Community Resources:**
* Template sharing and connector examples
* User-created automation workflows
* Integration guides for popular streaming tools
* Troubleshooting guides for common issues

**Development:**
* API documentation for custom trigger/action development
* Integration guides for external services
* Performance optimization tips
* Security best practices for API integrations
