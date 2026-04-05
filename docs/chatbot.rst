Chatbot System
==============

The Mycelian Chatbot System is a comprehensive automation framework that enables you to create interactive chat commands, automatic event responses, quotes management, personalized user greetings, and keyword-based giveaways. Unlike the Connectors system which focuses on external integrations, the Chatbot system specializes in Twitch chat interactions and community engagement.

Overview
--------

The chatbot system consists of multiple interconnected components that work together to provide rich chat automation capabilities:

**Core Components:**
  * ``chatbot.py`` - Twitch API integration and connection management
  * ``chatbot_core.py`` - Core classes and logic for commands, events, and greetings
  * ``chatbot_manager.py`` - Management and processing of all chatbot items
  * ``giveaway_manager.py`` - Giveaway pool, configuration, draws, and statistics hooks
  * ``modules/uiwindows/chatbot.py`` - Desktop interface for configuration
  * ``api_credentials_manager.py`` - Separate credential management for chatbot

**Key Features:**
  * Separate Twitch account support for the chatbot
  * Commands with cooldowns, usage limits, and mod-only restrictions
  * Automatic event responses to stream activity
  * Quote management system with search and random selection
  * Personalized greetings with user tracking and cooldowns
  * Giveaways with keyword entry, configurable filters, draws, and Helix announcements
  * Dynamic variables for personalized responses
  * API integration for external data sources
  * Comprehensive statistics and monitoring

System Architecture
-------------------

The chatbot system is designed with a modular architecture:

.. code-block:: text

    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
    │   Twitch Chat   │───▶│  Chatbot Core   │───▶│   Response      │
    │   Messages      │    │                 │    │   Generation    │
    │                 │    │ • Commands      │    │                 │
    │ • User Messages │    │ • Events        │    │ • Variables     │
    │ • Commands      │    │ • Quotes        │    │ • API Calls     │
    │ • Events        │    │ • Greetings     │    │ • Templates     │
    └─────────────────┘    └─────────────────┘    └─────────────────┘

**Connection Options:**
  * **Dedicated Chatbot Account:** Separate Twitch credentials for the chatbot
  * **Fallback Mode:** Uses main Twitch API when no dedicated credentials are configured
  * **Health Monitoring:** Automatic connection monitoring and token refresh

Setting Up Separate Twitch Credentials
---------------------------------------

The chatbot system supports running from a separate Twitch account, which provides several advantages:

**Benefits:**
  * Different username for the chatbot (e.g., "StreamBot" vs "StreamerName")
  * Independent authentication and permissions
  * Better organization and management
  * Ability to have different moderator permissions

**Setup Process:**

1. **Create a Dedicated Twitch Account**

   Create a new Twitch account specifically for your chatbot. You can use:
   * A separate email address
   * Username like "YourStreamName_Bot" or "YourStreamNameBot"
   * Profile picture and bio indicating it's a bot account

2. **Register a Twitch Application**

   * Go to `https://dev.twitch.tv/console/apps` **Note: You must be logged in as your chatbot account.**
   * Click "Create Application"
   * Fill in the details:
     * **Name:** Your bot's name (e.g., "YourStreamName Chatbot")
     * **OAuth Redirect URLs:** `http://localhost:17563` & `https://twitchapps.com/tokengen/`
     * **Category:** Choose "Chat Bot" or "Other"
     * **Client Type:** Select "Confidential"
   * Note down the **Client ID** and **Client Secret**

3. **Configure Credentials in Mycelian**

   * Launch Mycelian and go to the **Settings** tab, then the **Twitch** sub-tab
   * Find the "Chatbot API Credentials" section
   * Enter your **Client ID** and **Client Secret**
   * Save the settings

4. **Connection**

   * Click the "Connect" button for the chatbot account
   * When the browser appears, authorize the connection using the dedicated chatbot account.
   * Grant the requested permissions (Chat Edit, Channel Moderate)
   * The chatbot will now operate from the separate account

**Configuration Details:**

.. code-block:: text

    Main Account: YourStreamName (used for alerts, integrations)
    Chatbot Account: YourStreamName_Bot (used for automated chat responses)

**Permissions Required:**
  * ``CHAT_EDIT`` - Send messages in chat
  * ``CHANNEL_MODERATE`` - Delete messages, timeout users (optional but recommended)

**Fallback Behavior:**
  If no separate credentials are configured, the chatbot will use your main Twitch account for all operations.

Commands
--------

Commands are the core of the chatbot system - user-triggered responses that start with exclamation marks (!).

Command Types
~~~~~~~~~~~~~

**Basic Commands**
  Simple text responses that can be triggered by anyone in chat.

  *Example:* ``!hello`` → "Welcome to the stream! 🎉"

**Counter Commands**
  Commands that track how many times they've been used, with persistent counting.

  *Example:* ``!deathcount`` → "This is death #42 today. RIP! 💀"

**Reset Commands**
  Commands that can reset other counter commands (typically mod-only).

  *Example:* ``!resetdeaths`` → "Resetting death counter for @moderator"

Command Configuration
~~~~~~~~~~~~~~~~~~~~~

**Basic Settings:**
  * **Command Name:** The trigger word (without !)
  * **Aliases:** Alternative command names
  * **Response Text:** What the bot responds with
  * **Description:** Optional description for management

**Advanced Settings:**
  * **Mod Only:** Restrict to moderators only
  * **Cooldown:** Minimum seconds between uses
  * **Usage Limit:** Maximum total uses (0 = unlimited)
  * **Enabled:** Toggle command on/off

**Counter-Specific Settings:**
  * **Persistent Counter:** Save count between app restarts
  * **Reset Command:** Optional command name that can reset this counter

**Example Configurations:**

.. code-block:: json

    {
        "name": "Hello Command",
        "command_name": "hello",
        "aliases": ["hi", "hey"],
        "response_text": "Hello {username}! Welcome to the stream! 🎉",
        "mod_only": false,
        "cooldown": 5,
        "usage_limit": 0
    }

Command Variables
~~~~~~~~~~~~~~~~~

Commands support dynamic variables for personalized responses:

**Built-in Variables:**
  * ``{username}`` - Who triggered the command
  * ``{timestamp}`` - Current timestamp
  * ``{datetime}`` - Full date and time
  * ``{date}`` - Current date
  * ``{time}`` - Current time

**Counter Variables:**
  * ``{count}`` - Current counter value
  * ``{last_used}`` - When command was last used
  * ``{usage_left}`` - Remaining uses (if limited)

**YouTube Variables:**

*Global YouTube Variables:*
  * ``{youtube.latest_video_title}`` - Latest video title across all channels
  * ``{youtube.latest_video_url}`` - Latest video URL across all channels
  * ``{youtube.latest_video_id}`` - Latest video ID across all channels
  * ``{youtube.latest_video_channel}`` - Channel name of the global latest video
  * ``{youtube.connection_status}`` - YouTube connection status
  * ``{youtube.channel_count}`` - Number of configured channels

*Channel-Specific YouTube Variables:*
  * ``{ChannelName_latest_video_title}`` - Latest video title from specific channel
  * ``{ChannelName_latest_video_url}`` - Latest video URL from specific channel
  * ``{ChannelName_latest_video_id}`` - Latest video ID from specific channel
  * ``{ChannelName_channel_title}`` - Channel title for specific channel
  * ``{ChannelName_channel_url}`` - Channel URL for specific channel
  * ``{ChannelName_last_updated}`` - Last update time for specific channel

**Examples:**

.. code-block:: text

    !hello → "Hello @StreamerFan! Welcome to the stream! 🎉"
    !uptime → "Stream started at {datetime}"
    !deathcount → "Death #{count} - Last death: {last_used}"
    !latest → "Check out {youtube.latest_video_title} from {youtube.latest_video_channel}!"
    !pewdiepie → "PewDiePie just uploaded: {pewdiepie_latest_video_title}"

Events
------

Events are automatic responses to stream activity - no user input required.

Available Events
~~~~~~~~~~~~~~~~

**Follow Events**
  Triggered when someone follows your channel.

  *Response Example:* "Thanks for following, {username}! Welcome to the community! 🎉"

**Subscription Events**
  Triggered when someone subscribes.

  *Fields:* ``tier``, ``username``, ``message``, ``months``

  *Response Example:* "Welcome {username} as a {tier_name} subscriber! Thank you! ⭐"

**Resubscription Events**
  Triggered when someone resubscribes.

  *Fields:* ``tier``, ``username``, ``message``, ``months``, ``streak``

  *Response Example:* "Welcome back {username}! Thanks for {months} months of support! 👑"

**Gift Subscription Events**
  Triggered when someone gifts a subscription.

  *Fields:* ``tier``, ``username``, ``recipient_name``, ``total_gifts``

  *Response Example:* "{recipient_name} just got a gift from {username}! Thank you! 🎁"

**Bits Events**
  Triggered when someone cheers bits.

  *Fields:* ``amount``, ``username``, ``message``

  *Response Example:* "Wow {username}! Thanks for the {amount} bits! 💎"

**Raid Events**
  Triggered when another streamer raids your channel.

  *Fields:* ``username``, ``viewer_count``

  *Response Example:* "Welcome raiders from {username}! Thanks for bringing {viewer_count} viewers! 🚀"

**Donation Events**
  Triggered when someone donates via StreamLabs.

  *Fields:* ``amount``, ``username``, ``message``, ``currency``

  *Response Example:* "Thank you {username} for the ${amount} donation! 🙏"

**Hype Train Events**
  Triggered during hype train progress.

  *Fields:* ``level``, ``progress``, ``goal``

  *Response Example:* "Hype train level {level}: {progress}/{goal}! Keep it up! 🚂"

Event Variables
~~~~~~~~~~~~~~~~

Events have access to contextual information from the triggering event:

**Common Variables:**
  * ``{username}`` - The user who triggered the event
  * ``{amount}`` - Bits/donation amount
  * ``{message}`` - User's message (if applicable)
  * ``{tier}`` - Subscription tier (1, 2, 3)
  * ``{tier_name}`` - Subscription tier name ("Tier 1", "Tier 2", etc.)
  * ``{months}`` - Total months subscribed
  * ``{streak}`` - Current resub streak
  * ``{viewer_count}`` - Number of raiders
  * ``{currency}`` - Donation currency
  * ``{level}`` - Hype train level

**YouTube Variables:**
  * ``{youtube.latest_video_title}`` - Latest video title across all channels
  * ``{youtube.latest_video_url}`` - Latest video URL across all channels
  * ``{youtube.latest_video_channel}`` - Channel name of the global latest video

**Example Event Responses:**

.. code-block:: json

    {
        "event_type": "subscription",
        "response_text": "Welcome {username} as a {tier_name} subscriber! Thank you for the support! ⭐"
    }

Quotes System
-------------

The quotes system allows you to store, manage, and share memorable moments from your stream.

Quote Management
~~~~~~~~~~~~~~~~

**Adding Quotes:**
  * Users can add quotes via chat command: ``!addquote [text]``
  * Quotes are automatically numbered sequentially
  * Each quote stores the original text and who added it
  * Optional author attribution

**Quote Retrieval:**
  * ``!quote`` - Random quote
  * ``!quote 5`` - Specific quote by number
  * ``!quote search term`` - Search quotes containing the term

**Quote Examples:**

.. code-block:: text

    Quote #42: "When life gives you lemons, make orange juice and leave the world wondering how you did it."
    Added by: StreamerFan on 2024-01-15

    !quote → Returns random quote
    !quote 10 → Returns quote #10
    !quote funny → Returns quotes containing "funny"

Quote Configuration
~~~~~~~~~~~~~~~~~~~

**System Settings:**
  * **Quotes Enabled:** Toggle the entire quotes system on/off
  * **Auto-Numbering:** Automatically assign quote numbers
  * **User Permissions:** Control who can add quotes
  * **Search Functionality:** Enable/disable quote searching

**Management Features:**
  * Edit existing quotes
  * Delete inappropriate quotes
  * View quote statistics
  * Export/import quote collections

Greetings System
----------------

The greetings system provides personalized welcome messages for returning viewers.

Greeting Types
~~~~~~~~~~~~~~

**Default Greetings**
  Generic welcome messages for users without custom greetings.

  *Example:* "@{username} welcome back to the stream! 🎉"

**Custom Greetings**
  Personalized messages for specific users.

  *Example:* "@StreamerFan welcome back, champion! Ready for another gaming session? 🎮"

**Greeting Features:**
  * **User Tracking:** Remembers when users were last greeted
  * **24-Hour Cooldown:** Prevents spam by greeting users once per day
  * **Username Updates:** Automatically updates usernames when they change
  * **ID-Based Tracking:** Uses Twitch user IDs for reliable tracking

Greeting Configuration
~~~~~~~~~~~~~~~~~~~~~~

**Default Greeting Settings:**
  * **Default Greeting Text:** The fallback message for users without custom greetings
  * **Greeting Reset Interval:** How often to reset greeting flags (default: 24 hours)
  * **Default Greeting Enabled:** Toggle default greetings on/off

**Custom Greeting Management:**
  * **User ID:** Twitch user ID for reliable tracking
  * **Username:** Current display name (auto-updates)
  * **Custom Text:** Personalized greeting message
  * **Enabled:** Toggle individual greetings on/off

**Example Configurations:**

.. code-block:: json

    {
        "user_id": "123456789",
        "username": "StreamerFan",
        "greeting_text": "Welcome back {username}! Ready for another stream? 🎮",
        "enabled": true
    }

Greeting Variables
~~~~~~~~~~~~~~~~~~

Greetings support the same variables as commands:

* ``{username}`` - The user's display name
* ``{user_id}`` - The user's Twitch ID
* ``{timestamp}`` - Current timestamp

API Integration
---------------

The chatbot system supports external API integration for dynamic content.

API Call Configuration
~~~~~~~~~~~~~~~~~~~~~~

**API Settings:**
  * **API Enabled:** Toggle API integration on/off
  * **Endpoint:** The API URL to call
  * **Method:** HTTP method (GET, POST, PUT, DELETE)
  * **Headers:** Custom HTTP headers
  * **Parameters:** URL parameters or request body
  * **Response Format:** How to process the API response

**Variable Processing:**
  * **API Response Variables:** Extract data from API responses
  * **Expression Processing:** Transform API data using expressions
  * **Error Handling:** Graceful fallbacks when API calls fail

**Example API Integration:**

.. code-block:: json

    {
        "api_enabled": true,
        "api_endpoint": "https://api.weatherapi.com/v1/current.json",
        "api_method": "GET",
        "api_parameters": {
            "key": "your_api_key",
            "q": "London"
        },
        "api_variable_processing": [
            "weather_temp = {current.temp_c}",
            "weather_condition = {current.condition.text}"
        ]
    }

**Response Variables:**
  * ``{api_status}`` - HTTP status code
  * ``{api_success}`` - Whether the call succeeded
  * ``{api_error}`` - Error message if call failed
  * ``{weather_temp}`` - Custom processed variable
  * ``{weather_condition}`` - Custom processed variable

YouTube Integration
-------------------

The chatbot system supports comprehensive YouTube integration for displaying the latest videos from multiple YouTube channels.

Configuration
~~~~~~~~~~~~~

**Setting Up YouTube Channels:**

1. **Obtain YouTube Data API v3 Key:**
   * Go to `https://console.developers.google.com/`
   * Create a new project or select existing one
   * Enable the YouTube Data API v3
   * Create credentials (API Key)
   * Note down the API key

2. **Configure Channels in Mycelian:**
   * Open Mycelian desktop application
   * Go to Settings → YouTube Integration tab
   * Enter your YouTube Data API v3 key
   * Enter channel URLs separated by pipe symbols: ``https://youtube.com/@Channel1|https://youtube.com/@Channel2``
   * Save settings and test connection

**Supported Channel URL Formats:**
  * ``https://youtube.com/@ChannelHandle``
  * ``https://youtube.com/channel/UC_CHANNEL_ID``
  * ``https://youtube.com/c/CustomChannelName``
  * ``https://youtube.com/user/Username``

YouTube Variables
~~~~~~~~~~~~~~~~~

The system provides two types of YouTube variables for use in commands and events:

**Global Variables:**
These variables always show the most recent video across ALL configured channels.

* ``{youtube.latest_video_title}`` - Title of the most recent video
* ``{youtube.latest_video_url}`` - Direct link to the most recent video
* ``{youtube.latest_video_id}`` - YouTube video ID
* ``{youtube.latest_video_channel}`` - Name of the channel that uploaded the latest video
* ``{youtube.connection_status}`` - Connection status ("Connected", "API Key Required", etc.)
* ``{youtube.channel_count}`` - Number of configured channels

**Channel-Specific Variables:**
These variables show information from specific channels. The channel name is automatically derived from the channel title and cleaned for use in variables.

* ``{ChannelName_latest_video_title}`` - Latest video title from specific channel
* ``{ChannelName_latest_video_url}`` - Latest video URL from specific channel
* ``{ChannelName_latest_video_id}`` - Latest video ID from specific channel
* ``{ChannelName_channel_title}`` - Full channel title
* ``{ChannelName_channel_url}`` - Channel URL
* ``{ChannelName_last_updated}`` - When this channel was last updated

**Variable Name Cleaning:**
Channel names are automatically cleaned for variable use:
  * ``PewDiePie`` → ``{pewdiepie_latest_video_title}``
  * ``Linus Tech Tips`` → ``{linustechtips_latest_video_title}``
  * ``MrBeast`` → ``{mrbeast_latest_video_title}``

YouTube Examples
~~~~~~~~~~~~~~~~

**Command Examples:**

.. code-block:: text

    !latest → "Check out '{youtube.latest_video_title}' from {youtube.latest_video_channel}! {youtube.latest_video_url}"
    !pewdiepie → "PewDiePie just uploaded: {pewdiepie_latest_video_title} - {pewdiepie_latest_video_url}"
    !linus → "New Linus Tech Tips video: {linustechtips_latest_video_title}"
    !videohelp → "We have {youtube.channel_count} channels configured. Latest video: {youtube.latest_video_title}"

**Event Examples:**

.. code-block:: text

    Follow Event: "Welcome {username}! Check out our latest video: {youtube.latest_video_title}"
    Subscription Event: "Thanks {username}! Here's our latest: {youtube.latest_video_url}"

**Advanced Usage:**

.. code-block:: json

    {
        "command_name": "multichannel",
        "response_text": "We monitor {youtube.channel_count} channels! Latest from {youtube.latest_video_channel}: {youtube.latest_video_title}",
        "mod_only": false
    }

YouTube Features
~~~~~~~~~~~~~~~~

**Multi-Channel Support:**
  * Monitor unlimited number of YouTube channels
  * Automatic detection of latest video across all channels
  * Individual channel data tracking
  * Playlist filtering support

**Real-time Updates:**
  * Automatic background monitoring (every 5 minutes)
  * WebSocket integration for live template updates
  * Connection status monitoring
  * Error handling and recovery

**Integration Benefits:**
  * Dynamic content for chat commands
  * Automated social media integration
  * Real-time video announcements
  * Cross-platform content sharing

**Troubleshooting:**

* **"Channel not found" error:** Check that the channel name spelling matches exactly
* **Empty video data:** Verify YouTube API key is valid and channels are public
* **Connection issues:** Test API key and channel URLs in Settings
* **Variable not working:** Ensure channel has uploaded videos and is accessible

Statistics and Monitoring
-------------------------

The chatbot system provides comprehensive statistics and monitoring.

Usage Statistics
~~~~~~~~~~~~~~~~

**Command Statistics:**
  * Commands Created: Total number of commands
  * Commands Triggered: Total command executions
  * Most Popular Commands: Usage frequency
  * Recent Activity: Last command usage

**Event Statistics:**
  * Events Created: Total number of events
  * Events Triggered: Total automatic responses
  * Response Times: How quickly events trigger
  * Success Rates: Percentage of successful responses

**Quote Statistics:**
  * Quotes Created: Total quotes stored
  * Quotes Retrieved: Total quote requests
  * Popular Quotes: Most frequently requested
  * Quote Authors: Who adds the most quotes

**Greeting Statistics:**
  * Custom Greetings: Number of personalized greetings
  * Greeting Triggers: How often greetings are sent
  * User Tracking: Number of unique users greeted
  * Greeting Reset Frequency: How often flags are reset

Real-time Monitoring
~~~~~~~~~~~~~~~~~~~~

**Connection Status:**
  * Chatbot Connection: Online/offline status
  * Token Status: Authentication token validity
  * API Health: External API connectivity
  * Error Tracking: Failed operations and reasons

**Performance Metrics:**
  * Response Time: How quickly the bot responds
  * Message Processing: Rate of message handling
  * Memory Usage: System resource consumption
  * Error Rates: Percentage of failed operations

Management Interface
--------------------

The chatbot system is managed through the Mycelian desktop application.

Interface Overview
~~~~~~~~~~~~~~~~~~

**Main Tabs:**
  * **Commands:** Create and manage chat commands
  * **Events:** Configure automatic event responses
  * **Quotes:** Manage the quote collection
  * **Greetings:** Set up personalized welcomes

**Key Features:**
  * **Search Functionality:** Find items by name, content, or type
  * **Bulk Operations:** Enable/disable multiple items
  * **Import/Export:** Backup and restore configurations
  * **Testing Tools:** Test commands and events before enabling
  * **Statistics Dashboard:** Monitor usage and performance

Configuration Management
~~~~~~~~~~~~~~~~~~~~~~~~~

**Backup and Restore:**
  * Export all chatbot configurations
  * Import configurations from backups
  * Selective import/export by category
  * Version tracking for configurations

**Settings Management:**
  * Global chatbot settings
  * Permission configurations
  * API credential management
  * Performance tuning options

Advanced Features
-----------------

Variable Expressions
~~~~~~~~~~~~~~~~~~~~

The chatbot supports advanced variable processing with expressions. For comprehensive information about creating and using custom variables, see the :doc:`custom_variables` documentation:

**Built-in Functions:**
  * ``math(expression)`` - Mathematical calculations
  * ``date_to_age(date)`` - Convert date to age
  * ``compare(value, operator, target)`` - Conditional comparisons
  * ``format_number(number)`` - Format numbers with commas
  * ``uppercase(text)`` - Convert to uppercase
  * ``lowercase(text)`` - Convert to lowercase

**Examples:**

.. code-block:: text

    {math({stats.alerts.bit_alerts_played} / 10)} - Calculate level
    {date_to_age({data.created_at}).years} - Account age in years
    {compare({stats.chatbot.commands_triggered}, >, 100)} - High usage check

Custom Variables
~~~~~~~~~~~~~~~~

Create reusable custom variables for complex expressions:

**Variable Creation:**
  * Name: ``custom_user_level``
  * Expression: ``math({stats.alerts.bit_alerts_played} / 10)``
  * Usage: ``{custom_user_level}``

**Variable Management:**
  * Edit existing variables
  * Delete unused variables
  * Organize by category
  * Share variable collections

Moderation Features
~~~~~~~~~~~~~~~~~~~

**Permission Controls:**
  * Mod-only commands and features
  * User permission levels
  * Rate limiting and spam protection
  * Content filtering options

**Safety Features:**
  * Command usage limits
  * Cooldown enforcement
  * Automatic timeout handling
  * Audit logging for moderation actions

Integration with Mycelian
-------------------------

Service Integration
~~~~~~~~~~~~~~~~~~~

The chatbot system integrates seamlessly with other Mycelian components:

**Twitch Integration:**
  * Automatic event detection
  * Chat message processing
  * User authentication and permissions
  * Real-time event streaming

**Alert System Integration:**
  * Trigger chatbot responses from alerts
  * Share statistics and data
  * Coordinate automated responses
  * Synchronize user data

**Template System Integration:**
  * Dynamic template updates via chat commands
  * Real-time template control
  * Variable sharing between systems
  * Coordinated interactive elements

**Statistics Integration:**
  * Unified statistics dashboard
  * Cross-system analytics
  * Performance monitoring
  * Usage tracking and reporting

Best Practices
--------------

Command Design
~~~~~~~~~~~~~~

**Effective Commands:**
  * Use clear, memorable command names
  * Provide helpful responses with personality
  * Include cooldowns to prevent spam
  * Test commands before enabling live
  * Use variables for dynamic, personalized responses

**Command Categories:**
  * **Informational:** ``!uptime``, ``!social``, ``!schedule``
  * **Interactive:** ``!lurk``, ``!shoutout``, ``!hype``
  * **Fun:** ``!joke``, ``!quote``, ``!dance``
  * **Utility:** ``!help``, ``!commands``, ``!discord``

Event Configuration
~~~~~~~~~~~~~~~~~~~

**Event Best Practices:**
  * Don't overwhelm chat with too many automatic responses
  * Use varied, engaging responses
  * Include user names for personalization
  * Test events during low-traffic periods
  * Monitor for spam and adjust accordingly

**Response Timing:**
  * Space out automatic responses
  * Use different delays for different event types
  * Consider chat activity levels
  * Respect user experience

Performance Optimization
~~~~~~~~~~~~~~~~~~~~~~~~

**System Performance:**
  * Use appropriate cooldowns to prevent overload
  * Monitor memory usage with large quote collections
  * Regularly clean up unused commands and events
  * Optimize API calls and external integrations

**Chat Performance:**
  * Balance automated responses with natural conversation
  * Use varied response templates to avoid repetition
  * Monitor chat engagement metrics
  * Adjust based on community feedback

Security and Safety
~~~~~~~~~~~~~~~~~~~

**Security Measures:**
  * Use separate chatbot account when possible
  * Regularly rotate API credentials
  * Monitor for unauthorized access attempts
  * Keep backup copies of configurations

**Content Guidelines:**
  * Review all commands and responses for appropriateness
  * Implement content filters for user-generated content
  * Have moderation procedures for inappropriate quotes
  * Monitor for harassment or spam

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Chatbot Not Responding:**
  * Check if chatbot is connected in Settings
  * Verify Twitch API credentials are valid
  * Confirm the chatbot account has chat permissions
  * Check for network connectivity issues

**Commands Not Working:**
  * Ensure commands are enabled
  * Check for typos in command names
  * Verify cooldowns aren't preventing responses
  * Test commands individually

**Events Not Triggering:**
  * Confirm event types are enabled
  * Check if event conditions are met
  * Verify Twitch integration is working
  * Review event configuration settings

**API Integration Issues:**
  * Check API endpoint URLs
  * Verify API keys and authentication
  * Review API response formats
  * Monitor for rate limiting

**Variable Issues:**
  * Check variable syntax (use curly braces)
  * Verify variable names are correct
  * Test variables individually
  * Review error logs for variable processing

Debug and Monitoring
~~~~~~~~~~~~~~~~~~~~

**Debug Tools:**
  * Test individual commands and events
  * Monitor chat logs for error messages
  * Use the built-in testing functionality
  * Check Mycelian application logs

**Performance Monitoring:**
  * Track response times and success rates
  * Monitor memory and CPU usage
  * Review statistics dashboards
  * Set up alerts for performance issues

**Log Analysis:**
  * Review error logs for patterns
  * Monitor successful operation logs
  * Track user interaction patterns
  * Identify peak usage periods

API Reference
-------------

For developers extending the chatbot system:

**Core Classes:**
  * ``ChatCommand`` - Represents a chat command with all its properties
  * ``ChatEvent`` - Represents an automatic event response
  * ``Quote`` - Represents a stored quote
  * ``Greeting`` - Represents a user greeting
  * ``ChatbotManager`` - Main management class for all chatbot operations

**Key Methods:**
  * ``ChatbotManager.process_chat_message()`` - Process incoming chat messages
  * ``ChatbotManager.process_event()`` - Handle automatic events
  * ``ChatCommand.use_command()`` - Execute a command
  * ``ChatEvent.trigger_event()`` - Trigger an event response

**Factory Functions:**
  * ``create_command()`` - Create a new command instance
  * ``create_event()`` - Create a new event instance
  * ``create_greeting()`` - Create a new greeting instance

**Data Structures:**
  * ``CommandType`` - Enumeration of command types
  * ``EventType`` - Enumeration of event types
  * ``ComparisonOperator`` - Operators for conditional logic

**Extension Points:**
  * Custom command types
  * New event types
  * Additional variable functions
  * Custom API integrations

Giveaways
---------

The **Giveaways** sub-tab under **Chatbot** runs keyword-based contests tied to live chat.

Architecture
~~~~~~~~~~~~

* Incoming Twitch chat is processed for ``!`` commands first. If no command matches, the message may be recorded as a giveaway entry when a giveaway is **active**.
* **Active giveaway** means: a non-empty **entry keyword** is configured **and** **Start giveaway** has been clicked (accepting entries is on).
* Entry text must **exactly** match the keyword (trimmed, case-insensitive).
* **Draw winners** selects up to **Number of winners per draw** from the current ticket pool, formats the winning message, and sends a **chat announcement** via the chatbot API (``moderator:manage:announcements`` on the broadcaster channel). A dedicated bot account is recommended so announcements appear as the bot; fallback mode may use the main account path.
* **Draw** does **not** clear the pool or stop accepting; run **Clear giveaway** to empty entries and clear the keyword.

Configuration options
~~~~~~~~~~~~~~~~~~~~~

* **No duplicate entries** — at most one ticket per Twitch user ID in the pool.
* **Unique winners per draw** — for multi-winner draws, the same user cannot take more than one slot in a single draw.
* **Exclude moderators / VIPs** — uses Twitch badge metadata on the message.
* **Blocked usernames** — list of logins that cannot enter.
* **Winning announcement message** — supports ``{winners}`` and ``{winner}``; both expand to the same comma-separated list of all winners for that draw.

Statistics
~~~~~~~~~~

The statistics subsystem records total giveaway entries, completed draws, average entries per completed draw, and per-display-name win counts (see the Giveaways and Statistics tabs).

Future Enhancements
-------------------

Planned features for the chatbot system:

* **Advanced AI Integration:** GPT-powered dynamic responses
* **Multi-Platform Support:** Discord, YouTube, and other platforms
* **Voice Integration:** Text-to-speech for voice responses
* **Advanced Moderation:** Automatic spam detection and filtering
* **Custom Scripting:** JavaScript/Python scripting for complex logic
* **Analytics Dashboard:** Detailed usage analytics and insights
* **Command Marketplace:** Share and download community commands
* **Multi-Language Support:** Localized responses and commands
* **Advanced Permissions:** Role-based access control
* **Stream Integration:** OBS scene switching via chat commands
* **Social Media Integration:** Automatic social media posting

Getting Help
------------

**Documentation Resources:**
  * Review this comprehensive guide
  * Check the Mycelian main documentation
  * Explore example configurations
  * Review the API reference

**Community Support:**
  * Join the Mycelian community
  * Share configurations and commands
  * Report bugs and request features
  * Participate in beta testing

**Debugging Tools:**
  * Use the built-in testing functionality
  * Monitor application logs
  * Check Twitch API status
  * Review network connectivity

**Performance Tips:**
  * Start with basic commands and expand gradually
  * Test everything in a development environment first
  * Monitor performance metrics regularly
  * Keep backups of working configurations
