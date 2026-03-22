Usage Guide
===========

Getting Started
---------------

Once Mycelian is installed and configured, you can start using its various features to enhance your streaming experience through both the desktop application interface and browser sources.

Desktop Application Interface
-----------------------------

Mycelian provides a comprehensive desktop application with a tabbed interface for managing all aspects of your streaming setup. The main interface includes several key tabs:

Activity Feed Tab
~~~~~~~~~~~~~~~~~

The Activity Feed tab provides real-time monitoring of all stream events and alerts:

**Current Alerts Tab:**
* View live alerts as they happen
* Real-time status updates with "NEW" badges for recent events
* Interactive controls to replay or skip alerts
* Filter alerts by type (follows, subs, bits, donations, etc.)
* Condensed view option to group alerts by user
* Pause/resume alerts functionality

**Previous Alerts Tab:**
* Browse historical alerts with pagination
* Search through past events
* Replay any previous alert
* Configurable page limits and display options

**Features:**
* Real-time WebSocket communication with templates
* Thread-safe alert processing
* Automatic timestamp formatting ("just now", "2m ago", etc.)
* Support for all alert types including PSN trophies and hype trains

Settings Tab
~~~~~~~~~~~~

Comprehensive configuration interface for all services and integrations:

**Service Integrations:**
* **Twitch:** OAuth connection, API credentials, channel configuration
* **Spotify:** OAuth setup, playback integration, now-playing display
* **YouTube:** Multi-channel monitoring, latest video tracking, chatbot integration
* **PlayStation Network:** PSN credentials, trophy notifications
* **Streamlabs:** Alternative donation platform support

**Database Configuration:**
* Firebase Realtime Database setup
* Local SQLite database options
* Database migration tools
* Connection testing and status monitoring

**Application Settings:**
* Stream overlay preferences
* Alert behavior configuration
* UI theme and display options
* File path management with built-in browser

**OAuth Management:**
* Secure authentication flows for supported services
* Token refresh handling
* Connection status monitoring
* Easy re-authorization process

Custom Sources Tab
~~~~~~~~~~~~~~~~~~

Template configuration management interface:

**Configuration Management:**
* Load and edit template configuration files
* Search functionality across all template properties
* Create, duplicate, and delete configurations

**Interactive Form Elements:**
* Text inputs, number fields, and sliders
* Color pickers with transparency support
* Dropdown selectors and toggles
* Grouped controls for better organization

**Template Properties:**
* Styling options (fonts, colors, positioning)
* Animation settings and timing controls
* Audio configuration and volume settings
* Asset management (images, sounds, animations)

Source Controls Tab
~~~~~~~~~~~~~~~~~~~

Interactive control panel for real-time template manipulation:

**Dynamic Controls:**
* Buttons for triggering actions (spin wheels, reset counters, etc.)
* Sliders for real-time value adjustments
* Toggles for enabling/disabling features
* Counter controls with increment/decrement/reset options

**Template Categories:**
* Actions: Trigger specific template behaviors
* Counters & Numbers: Manage numeric displays
* Text Input: Send text to templates in real-time
* Adjustments: Fine-tune settings on the fly
* Special Controls: Template-specific advanced features

**Features:**
* Works with both visible and hidden templates
* Real-time WebSocket communication
* Grouped controls by functionality
* Support for custom control types

Connectors Tab
~~~~~~~~~~~~~~

The Connectors tab provides a comprehensive automation system for creating sophisticated trigger-action workflows:

**Main Interface:**

* **Connector Management:** View all connectors with status indicators, trigger counts, and last triggered timestamps
* **Statistics Dashboard:** Real-time statistics showing total connectors, enabled connectors, triggers fired, and actions executed
* **Search and Filter:** Find connectors by name or filter by enabled/disabled status
* **Quick Actions:** Create, edit, test, and delete connectors with a single click

**Creating Connectors:**

1. **Click "New Connector"** to open the creation dialog
2. **Basic Information:**
   * Name: Descriptive name for your connector
   * Description: Optional explanation of what the connector does

3. **Trigger Configuration:**
   * Choose from 15+ trigger types
   * Add optional conditions for precise control
   * Configure trigger-specific parameters

4. **Action Configuration:**
   * Add multiple actions to execute when triggered
   * Configure action-specific settings
   * Use placeholders for dynamic data insertion

5. **Testing and Deployment:**
   * Test connectors with sample data
   * Review execution results
   * Enable connectors for live operation

**Visual Design Features:**

* **Color-coded Interface:** Blue for triggers, green for actions, purple for conditions
* **Real-time Preview:** See connector configuration as you build it
* **Validation:** Built-in validation prevents invalid configurations
* **Statistics Tracking:** Monitor connector performance and usage

Connector Automation System
----------------------------

The Connector system is a powerful no-code automation framework that allows you to create sophisticated trigger-action workflows for your stream. Think of connectors as "if this happens, then do that" rules that automate responses to stream events.

Overview
~~~~~~~~

Connectors consist of three main components:

1. **Triggers** - Events that start the automation (follows, subs, bits, donations, etc.)
2. **Conditions** - Optional filters to make triggers more specific
3. **Actions** - What happens when the trigger fires (template controls, chat messages, API calls, etc.)

Available Triggers
~~~~~~~~~~~~~~~~~~

**Twitch Event Triggers:**

* **Twitch Bits** - Responds to bit/cheer events
  - Available fields: ``amount``, ``username``, ``message``
  - Example conditions: Amount >= 100, username contains "VIP"

* **Twitch Subscription** - New subscriber events
  - Available fields: ``tier``, ``username``, ``message``, ``months``
  - Example conditions: Tier >= 2, message contains "love"

* **Twitch Resubscription** - Subscription renewal events
  - Available fields: ``tier``, ``username``, ``message``, ``months``, ``streak_months``
  - Example conditions: Months >= 12, streak_months >= 6

* **Twitch Gift Subscription** - Gift sub events
  - Available fields: ``tier``, ``username``, ``recipient``, ``gift_count``
  - Example conditions: Gift count >= 5, username not equal "anonymous"

* **Twitch Follow** - New follower events
  - Available fields: ``username``, ``followed_at``
  - Example conditions: Username starts with "bot" (to filter bot follows)

* **Twitch Raid** - Incoming raid events
  - Available fields: ``username``, ``viewer_count``
  - Example conditions: Viewer count >= 10

* **Channel Points** - Channel point redemption events
  - Available fields: ``username``, ``reward_title``, ``reward_cost``, ``user_input``
  - Example conditions: Reward title equals "Spin Wheel", cost >= 1000

* **Chat Message** - Any chat message
  - Available fields: ``username``, ``message``, ``badges``
  - Example conditions: Message contains "hello", username equals "moderator"

* **Chat Command** - Messages starting with ! (or custom prefix)
  - Available fields: ``username``, ``message``, ``command``, ``command_args``
  - Example conditions: Command equals "test", username contains "mod"

* **Hype Train Start/End** - Hype train events
  - Available fields: ``level``, ``total``, ``goal``, ``top_contributors``
  - Example conditions: Level >= 3, total >= 1000

**Donation Triggers:**

* **Donation** - StreamLabs donation events
  - Available fields: ``amount``, ``username``, ``message``, ``currency``
  - Example conditions: Amount >= 5.00, currency equals "USD"

**Time-based Triggers:**

* **Timer** - Recurring time-based events
  - Available fields: ``timer_id``, ``interval_seconds``
  - Configure interval for how often it triggers

* **Schedule** - Cron-like scheduled events (future feature)
  - Available fields: ``schedule_pattern``, ``timezone``

**Manual Triggers:**

* **Manual** - Manually triggered events
  - Available fields: Customizable event data
  - Used for testing or manual activation

* **Webhook** - External webhook events
  - Available fields: Webhook payload data
  - For integration with external services

* **Hotkey** - Keyboard shortcut triggers
  - Available fields: ``key_combination``, ``modifiers``, ``key_code``, ``is_global``
  - Supports key combinations like ``Ctrl+Shift+F``, ``Alt+Tab``, ``F5``
  - Can work globally or only when the application is focused

Condition System
~~~~~~~~~~~~~~~~

Conditions allow you to make triggers more specific by filtering based on event data:

**Comparison Operators:**

* **Equals** - Exact match
* **Greater than / Greater than or equal** - Numeric comparisons
* **Less than / Less than or equal** - Numeric comparisons
* **Contains** - Text contains substring
* **Starts with / Ends with** - Text prefix/suffix matching
* **Not equal / Not contains** - Inverse comparisons

**Field References:**

Use dot notation to access nested data:
* ``user.display_name`` - User's display name
* ``reward.title`` - Channel point reward title
* ``message.text`` - Chat message content

**Example Conditions:**

* Amount >= 100 (for high-value events)
* Username contains "VIP" (for special users)
* Message starts with "Hello" (for greeting detection)
* Tier equals 3 (for top-tier subscribers only)

Available Actions
~~~~~~~~~~~~~~~~~

**Template Control Actions:**

Control your browser source templates dynamically:

* **Counter Controls:**
  - Increment, decrement, or reset counters
  - Update counter values to specific numbers
  - Set custom counter messages

* **Roulette Controls:**
  - Spin wheels automatically
  - Add new options to wheels
  - Reset wheel configurations

* **Animation Triggers:**
  - Start template animations
  - Control visual effects
  - Trigger template state changes

* **Configuration:** Select template name and action, configure parameters like increment amount or wheel options

**Communication Actions:**

* **Send Chat Message:**
  - Send automated responses to Twitch chat
  - Use placeholders for dynamic content: ``Thanks {{username}} for the {{amount}} bits!``
  - Support for emotes and formatting

* **WebSocket Events:**
  - Send custom events to templates
  - Trigger custom template behaviors
  - Pass structured data to browser sources

**External Integration Actions:**

* **API Calls:**
  - Make HTTP requests to external services
  - Support for GET, POST, PUT, DELETE, PATCH methods
  - Custom headers and request bodies
  - Use placeholders in URLs and data

* **Trigger Alerts:**
  - Activate custom alerts through the alert system
  - Configure alert type, amount, and custom messages
  - Integration with existing alert templates

**System Actions:**

* **File Operations:**
  - Write data to files for logging
  - Create custom logs with event data
  - Append or overwrite file content
  - Use placeholders for dynamic file paths and content

* **Execute Commands:**
  - Run system commands or scripts
  - Control external applications
  - Set working directories and timeouts
  - Use placeholders for dynamic command parameters

* **Keyboard/Mouse Input:**
  - Simulate key presses and mouse clicks
  - Support for key combinations (Ctrl+C, Alt+Tab)
  - Configurable hold durations and repeat counts
  - Macro sequences for complex input patterns

* **Audio Control:**
  - Control system volume levels
  - Mute/unmute microphone
  - Control application-specific volume
  - Cross-platform support (Windows, macOS, Linux)

Placeholder System
~~~~~~~~~~~~~~~~~~

Actions support dynamic placeholders that are replaced with event data:

**Common Placeholders:**

* ``{{username}}`` - User who triggered the event
* ``{{amount}}`` - Numeric amount (bits, donation, viewer count)
* ``{{message}}`` - Message content (chat, donation message)
* ``{{timestamp}}`` - When the event occurred
* ``{{event_type}}`` - Type of event that triggered

**Conditional Placeholders:**

* ``{{tier}}`` - Subscription tier (sub events)
* ``{{months}}`` - Subscription months (sub events)
* ``{{command}}`` - Chat command (command events)
* ``{{reward_title}}`` - Channel point reward name
* ``{{currency}}`` - Donation currency

**YouTube Placeholders:**

* ``{{youtube.latest_video_title}}`` - Latest video title across all channels
* ``{{youtube.latest_video_url}}`` - Latest video URL across all channels
* ``{{youtube.latest_video_channel}}`` - Channel name of the global latest video
* ``{{ChannelName_latest_video_title}}`` - Latest video title from specific channel
* ``{{ChannelName_latest_video_url}}`` - Latest video URL from specific channel

**Usage Examples:**

* Chat message: ``Welcome {{username}} to the stream! Thanks for following!``
* File content: ``{{timestamp}}: {{username}} donated {{amount}} {{currency}}``
* API request: ``{"user": "{{username}}", "amount": {{amount}}}``
* YouTube announcement: ``Check out {{youtube.latest_video_title}} from {{youtube.latest_video_channel}}!``
* Channel-specific: ``New video from PewDiePie: {{pewdiepie_latest_video_title}}``

Connector Examples
~~~~~~~~~~~~~~~~~~

**High Bits Counter:**

* **Trigger:** Twitch Bits
* **Condition:** Amount >= 100
* **Action:** Template Control → Counter → Increment

**VIP Welcome Message:**

* **Trigger:** Twitch Follow  
* **Condition:** Username contains "VIP"
* **Action:** Send Chat Message → "Welcome VIP {{username}}! Thank you for the follow!"

**Donation Logger:**

* **Trigger:** Donation
* **Condition:** Amount >= 5.00
* **Action:** Write File → "donations.log" → "{{timestamp}}: {{username}} - ${{amount}} - {{message}}"

**Music Skip on Channel Points:**

* **Trigger:** Channel Points
* **Condition:** Reward title equals "Skip Song"
* **Action:** API Call → POST → "http://localhost:8080/skip" → {"user": "{{username}}"}

**Auto Thank You for Large Raids:**

* **Trigger:** Twitch Raid
* **Condition:** Viewer count >= 20
* **Action:** Send Chat Message → "HUGE thanks to {{username}} for the {{viewer_count}} viewer raid! Welcome everyone!"

**Automated Counter Reset:**

* **Trigger:** Timer (every 60 seconds)
* **Condition:** None
* **Action:** Template Control → Counter → Reset

**Built-in Connector Examples:**

Mycelian comes with several pre-built connector examples to help you get started:

* **High Bits Counter** - Automatically increments a counter when someone donates 100+ bits
* **VIP Welcome** - Sends a special welcome message to VIP followers
* **Donation Logger** - Logs all donations to a file with timestamps
* **Raid Celebrations** - Thanks raiders and updates celebration counters
* **Music Controls** - Skip songs via channel point redemptions

**Creating Connectors from Examples:**

1. In the Connectors tab, click the **"Load Examples"** button
2. Browse available example connectors
3. Click **"Create from Example"** to customize and deploy
4. Modify triggers, conditions, and actions as needed
5. Test and enable your customized connector

**Hotkey Connector Example:**

* **Trigger:** Hotkey (``Ctrl+Shift+C``)
* **Condition:** None
* **Action:** Template Control → Counter → Increment

This creates a global hotkey that increments your stream counter from anywhere on your system.

Testing and Debugging
~~~~~~~~~~~~~~~~~~~~~~

**Test Mode:**

Every connector includes a built-in test function:

1. Click "Test" on any connector
2. System generates sample data based on trigger type
3. Connector executes with test data
4. Review results to verify proper operation

**Test Data Examples:**

* Bits test: 100 bits from "TestUser"
* Follow test: New follow from "TestUser"  
* Donation test: $5.00 from "TestUser"
* Chat command test: "!test" from "TestUser"

**Debugging Features:**

* **Execution Logs:** View detailed logs of connector execution
* **Condition Evaluation:** See which conditions pass/fail
* **Action Results:** Monitor action success/failure status
* **Statistics Tracking:** View trigger counts and success rates

**Common Issues:**

* **Trigger not firing:** Check service connections and condition logic
* **Action failing:** Verify configuration parameters and placeholders
* **Template controls not working:** Ensure template supports the specified action
* **API calls failing:** Check endpoint URLs and authentication

Management Features
~~~~~~~~~~~~~~~~~~~

**Connector Status:**

* **Enabled/Disabled:** Toggle connectors on/off without deleting
* **Trigger Count:** Track how many times each connector has fired
* **Last Triggered:** See when connectors last executed
* **Success Rate:** Monitor action execution success

**Bulk Operations:**

* **Examples Creation:** Generate sample connectors to learn from
* **Export/Import:** Share connector configurations (future feature)
* **Batch Enable/Disable:** Control multiple connectors at once

**Performance Monitoring:**

* **Statistics Dashboard:** Real-time metrics on connector usage
* **Resource Usage:** Monitor system impact of automation
* **Error Tracking:** Identify and resolve connector issues

**Security Considerations:**

* **Validation:** Built-in validation prevents malicious input
* **Sandboxing:** Actions execute in controlled environments  
* **Logging:** Comprehensive logs for audit and debugging
* **Permission Checks:** Verify access to files and system resources

Browser Sources
---------------

Mycelian serves HTML template files through a Flask web server that can be added as browser sources in OBS Studio:

Template System
~~~~~~~~~~~~~~~

**Server Details:**
* **Default URL:** ``http://localhost:5000``
* **Template Structure:** Each ``.html`` file in ``templates/`` becomes a route
* **Dynamic Routes:** Templates are automatically registered when files are added
* **Asset Serving:** Static files served via ``/assets/`` and ``/static/`` routes

**Adding Browser Sources:**
1. Open OBS Studio
2. Add a new Browser Source
3. Set URL to ``http://localhost:5000/{template_name}``
4. Configure width/height as needed for your template
5. Template will auto-refresh when files change during development

**Available Templates:**

Alert System
~~~~~~~~~~~~

Real-time alert display with extensive customization options:

**Alert Types Supported:**
* Followers and new subscribers
* Subscription renewals (resubs) with month tracking
* Gift subscriptions with quantity display
* Bits/cheers with amount tracking
* Channel point redemptions
* Donations with amount display
* Raids with viewer count
* Hype train events with level progression
* PSN Trophy notifications with game artwork

**Customization Features:**
* Multiple GIF animations per alert type
* Custom audio files with volume control
* Randomization options for variety
* Tier-specific styling for subscriptions
* Fade in/out effects
* Audio-only alerts option
* Duration and positioning controls

**URL:** ``http://localhost:5000/bitboss`` (or your alert template name)

Activity Feed Display
~~~~~~~~~~~~~~~~~~~~~

Scrolling feed of recent stream activity:

**Features:**
* Real-time alert display as they occur
* Configurable display duration
* Alert type filtering and search
* Pagination for historical events
* Badge styling based on alert tier/type
* User message display for subs/donations/points

**URL:** ``http://localhost:5000/activity_feed``

Chat Integration
~~~~~~~~~~~~~~~~

Real-time chat display with Twitch integration:

**Features:**
* Live message display with user badges
* Emote rendering (Twitch, BTTV, FFZ)
* Message moderation and filtering
* Customizable styling and animations
* Chat commands support
* Message history and replay

**URL:** ``http://localhost:5000/chat``

Chatbot System
~~~~~~~~~~~~~~~

Mycelian includes a comprehensive chatbot automation system that goes beyond simple chat display to provide intelligent command processing, automated responses, and interactive features:

**Core Components:**

* **Chat Commands** - Custom commands that users can trigger with specific prefixes (e.g., ``!command``)
* **Automated Events** - Responses triggered by stream events (follows, subs, donations, etc.)
* **Greeting System** - Automatic welcome messages for new viewers with cooldown management
* **Quote Management** - Store and retrieve funny moments and quotes from chat
* **Statistics Tracking** - Monitor command usage and chat activity

**Chat Commands:**

Create custom commands that respond to user input:

* **Basic Commands** - Simple text responses
* **Counter Commands** - Commands that track usage counts
* **API Integration** - Commands that fetch data from external APIs
* **Variable Support** - Dynamic responses using user data and statistics

**Example Commands:**

.. code-block:: none

   !hello → "Welcome to the stream {username}!"
   !uptime → "Stream has been live for {uptime}"
   !followage → "{username} has been following for {followage}"
   !quote → Displays a random stored quote
   !addquote → Allows moderators to add new quotes
   !latest → "Check out {youtube.latest_video_title} from {youtube.latest_video_channel}!"
   !pewdiepie → "PewDiePie just uploaded: {pewdiepie_latest_video_title}"

**Automated Events:**

Respond automatically to stream events:

* **Welcome Messages** - Greet new followers automatically
* **Subscription Celebrations** - Custom messages for new subs and resubs
* **Donation Thanks** - Personalized thank you messages for donations
* **Raid Responses** - Automatic responses to incoming raids
* **Bits Reactions** - Special responses for bit donations
* **YouTube Announcements** - Share latest videos when events occur

**Greeting System:**

Intelligent greeting management:

* **Cooldown Tracking** - Prevent spam by tracking when users were last greeted
* **Custom Greetings** - Personalized messages for specific users
* **Default Greetings** - Fallback messages for regular viewers
* **Greeting History** - Database tracking of greeting interactions

**Quote System:**

Community quote management:

* **Quote Storage** - Save memorable chat moments
* **Quote Retrieval** - Random quote display or search by number
* **Moderation** - Control who can add quotes
* **Statistics** - Track quote usage and popularity

**Configuration:**

Access the chatbot system through the **Chatbot** tab in the desktop application:

1. **Commands Tab** - Create and manage custom commands
2. **Events Tab** - Configure automated responses to stream events
3. **Greetings Tab** - Set up welcome messages and cooldowns
4. **Quotes Tab** - Manage community quotes
5. **Settings Tab** - Configure chatbot behavior and permissions

**Advanced Features:**

* **Variable System** - Use dynamic placeholders in responses
* **Permission Levels** - Control who can trigger commands
* **Cooldown Management** - Prevent command spam
* **Usage Statistics** - Track command popularity and usage patterns
* **API Integration** - Connect to external services for dynamic data
* **Custom Prefixes** - Configure command prefixes beyond "!"

Now Playing Display
~~~~~~~~~~~~~~~~~~~

Spotify integration for music display:

**Features:**
* Current track information (title, artist, album)
* Album artwork display
* Playback progress and controls
* Track history tracking
* Customizable layout and styling

**URL:** ``http://localhost:5000/spotify`` (or your music template name)

Interactive Elements
~~~~~~~~~~~~~~~~~~~~

Templates with real-time control capabilities:

**Counter Templates:**
* Increment/decrement/reset functionality
* Custom messaging display
* Real-time updates via WebSocket
* **URL:** ``http://localhost:5000/counter``

**Roulette/Wheel Templates:**
* Spin wheel functionality
* Dynamic option management
* Result display and history
* **URL:** ``http://localhost:5000/roulette``

**Subscriber Goal Bars:**
* Progress tracking towards goals
* Visual progress indicators
* Milestone celebrations
* **URL:** ``http://localhost:5000/subbar``

**Custom Overlays:**
* Donation/bits progress bars
* Event-triggered animations
* Social media integration displays
* Game-specific overlays

Service Integrations
--------------------

Twitch
~~~~~~

**Features:**
* Real-time follower and subscription alerts
* Chat integration
* Channel point redemptions
* Raid and host notifications

**Setup:**
1. Add your Twitch application credentials
2. Authorize the application
3. Configure alert preferences

Spotify
~~~~~~~

**Features:**
* Current song display
* Playback controls
* Track history

**Setup:**
1. Create Spotify developer application
2. Configure redirect URI
3. Authorize access to your Spotify account

YouTube
~~~~~~~

**Features:**
* Multi-channel video monitoring and latest video tracking
* Real-time YouTube data integration with chatbot variables
* Automatic detection of newest videos across all configured channels
* Channel-specific and global latest video variables
* Playlist filtering and content moderation
* WebSocket integration for live template updates

**Setup:**
1. Obtain YouTube Data API v3 key from Google Cloud Console
2. Enable YouTube Data API v3 in your Google Cloud project
3. Configure API key in Mycelian Settings → YouTube Integration
4. Enter channel URLs separated by pipe symbols (|)
5. Test connection and verify channel data loading

**Supported Channel Formats:**
* ``https://youtube.com/@ChannelHandle``
* ``https://youtube.com/channel/UC_CHANNEL_ID``
* ``https://youtube.com/c/CustomChannelName``
* ``https://youtube.com/user/Username``

**Chatbot Integration:**
* Global variables: ``{youtube.latest_video_title}``, ``{youtube.latest_video_channel}``
* Channel-specific variables: ``{ChannelName_latest_video_title}``, ``{ChannelName_latest_video_url}``
* Automatic variable generation for all configured channels

PlayStation Network
~~~~~~~~~~~~~~~~~~~

**Features:**
* Trophy unlock notifications
* Game status updates
* Achievement displays
* Game cache system for performance optimization
* Game name mismatch detection and resolution
* Real-time PSN presence monitoring

**Setup:**
1. Configure PSN credentials
2. Set your PSN username
3. Enable trophy notifications

**Game Cache System:**

Mycelian includes an intelligent game cache system that optimizes PSN integration performance by storing game data locally. This system automatically builds a database of games you've played, including trophy information, artwork URLs, and platform details.

**Key Features:**
* Automatic game data caching when you play games
* Fast lookup of game information without API calls
* Game name mismatch detection and manual resolution
* Search and edit cached game data through the UI

**How It Works:**
1. When you start playing a game, Mycelian checks if it's in the cache
2. If cached (cache hit): Uses stored data for immediate response
3. If not cached (cache miss): Fetches data from PSN API and stores it
4. Mismatch detection: Alerts when presence and trophy APIs report different game names
5. Manual resolution: Edit game mappings through the PSN tab interface

**PSN Tab Interface:**

The PSN tab provides comprehensive management of your PlayStation Network integration with an intuitive desktop interface.

**Connection Configuration:**
* **NPSSO Code Input:** Secure field for PlayStation Network authentication token
* **PSN Username Field:** Specify which profile to track (leave empty for your own account)
* **Help Dialog:** Step-by-step guide for obtaining NPSSO tokens from PlayStation.com
* **Save/Discard:** Standard form controls with validation

**Connection Status Display:**
* **Status Indicator:** "Connected," "Not Connected," "Configured but Offline," or "Error"
* **Tracking Target:** Shows which PSN username is being monitored
* **Real-time Updates:** Status refreshes automatically every 5 seconds
* **Connection Details:** Account ID and online ID when available

**Mismatch Notification Banner:**
* **Visual Alert:** Orange-themed banner with warning icon
* **Mismatch Details:** Displays conflicting game names from different APIs
* **Action Prompt:** "Edit in Game Cache section below" link
* **Auto-hide:** Banner disappears when mismatch is resolved
* **Throttle Protection:** Prevents spam with 5-minute cooldown between notifications

**Game Cache Editor Section:**

**Game Search Interface:**
* **Autocomplete Dropdown:** Search all cached games with fuzzy matching
* **Platform Display:** Shows PS4, PS5, etc. alongside game names
* **No Cache State:** Helpful message when no games are cached yet

**Game Details Panel:**
* **Read-only Information:**
  * Trophy Name (from trophy API)
  * NP Communication ID (unique game identifier)
  * Platform (PS4, PS5, etc.)
  * Cover Art URL (with truncation for long URLs)
  * Last Updated timestamp
* **Editable Fields:**
  * Presence Name (from social/presence API)
  * NP Title ID (from presence data)
* **Save Controls:** Enabled only when changes are made
* **Validation:** Ensures required fields are populated

**Cache Management Features:**
* **Bulk Operations:** All games cached when new titles are discovered
* **Manual Editing:** Fix naming inconsistencies between APIs
* **Data Persistence:** Changes saved immediately to local database
* **Search Performance:** Instant filtering across hundreds of games
* **Cache Statistics:** Visual indicators of cache hit/miss ratios

**Status Monitoring:**
* **Background Thread:** Dedicated monitoring runs continuously
* **Connection Recovery:** Automatic reconnection on network/API issues
* **Performance Metrics:** Cache hit rates and API call counts
* **Error Handling:** Graceful degradation with user feedback
* **Real-time Broadcasting:** Live updates sent to browser sources

OBS Studio Integration
~~~~~~~~~~~~~~~~~~~~~~

**Features:**
* Scene switching
* Source visibility control
* Automated recording/streaming

**Setup:**
1. Enable OBS WebSocket server
2. Configure connection settings in Mycelian
3. Set up scene automation rules

PSN Game Cache System
---------------------

Mycelian's PSN Game Cache System provides intelligent caching and management of PlayStation Network game data to optimize performance and handle edge cases in game name reporting.

Overview
~~~~~~~~

The game cache system addresses several challenges with PSN API integration:

**Performance Optimization:**
* Eliminates repeated API calls for the same game data
* Provides instant access to game information during streams
* Reduces API rate limiting issues

**Data Accuracy:**
* Maintains a local database of game information
* Handles discrepancies between different PSN APIs
* Allows manual correction of game name mismatches

**User Experience:**
* Seamless integration with trophy notifications
* Visual mismatch detection and resolution
* Comprehensive game data management interface

Cache Architecture
~~~~~~~~~~~~~~~~~~

The cache system uses a hierarchical database structure:

**Storage Path:** ``PSNGameData/games/{np_communication_id}``

**Game Data Structure:**

.. code-block:: json

   {
     "np_communication_id": "NPWR12345_00",
     "np_title_id": "PPSA01234_00",
     "presence_name": "Game Name from Presence API",
     "trophy_name": "Game Name from Trophy API",
     "cover_art_url": "https://image.api.playstation.com/...",
     "platform": "PS5",
     "trophy_groups": [
       {
         "trophy_group_id": "default",
         "group_name": "Base Game",
         "is_base_game": true,
         "defined_trophies": {
           "bronze": 10,
           "silver": 5,
           "gold": 2,
           "platinum": 1
         }
       }
     ],
     "last_updated": "2024-01-01T12:00:00.000Z"
   }

Cache Operations
~~~~~~~~~~~~~~~~

**Cache Hit Logic:**
1. User starts playing a game
2. System checks cache by ``np_title_id`` (from presence data)
3. If found, uses cached data immediately (cache hit)
4. Sleep interval: 10 seconds between updates

**Cache Miss Logic:**
1. Game not found in cache
2. Fetches all user's games from PSN Trophy API
3. Matches current game by name comparison
4. Stores all games in cache for future use
5. Sleep interval: 15 seconds between updates

**Mismatch Detection:**
1. Compares presence game name vs trophy API game name
2. Creates mismatch record when names differ
3. Notifies user via UI banner and WebSocket event
4. Allows manual resolution through game cache editor

**Background Processing:**
* Runs in dedicated thread to avoid blocking UI
* Automatic reconnection on API failures
* Intelligent sleep timing based on cache status
* Real-time data broadcasting via WebSocket

Game Data Management
~~~~~~~~~~~~~~~~~~~~

**Automatic Caching:**
* Triggered when playing uncached games
* Fetches complete trophy group information
* Stores artwork URLs and platform details
* Updates timestamps for cache freshness

**Manual Editing:**
* Search through cached games by name
* Edit presence and trophy name mappings
* Update NP Title IDs for mismatch resolution
* Save changes directly to database

**Cache Maintenance:**
* Automatic timestamp updates on data refresh
* No expiration policy (games remain cached indefinitely)
* Manual cleanup through database management
* Backup integration with Mycelian's database system

Troubleshooting
~~~~~~~~~~~~~~~

**Common Issues:**

**Games Not Appearing in Cache:**
* Ensure PSN credentials are configured correctly
* Play the game while Mycelian is running
* Check logs for API authentication errors
* Verify the game has trophies (some games don't)

**Mismatch Notifications:**
* Different APIs may report slightly different game names
* Regional naming variations can cause mismatches
* Use the game cache editor to standardize names

**Performance Issues:**
* Large game libraries may take time to cache initially
* API rate limits may slow down bulk caching
* Check network connectivity and PSN service status

**Data Accuracy:**
* Manually verify game data through the cache editor
* Cross-reference with official PSN website
* Report persistent issues with specific game titles

Best Practices
~~~~~~~~~~~~~~

**Cache Management:**
* Regularly review cached games for accuracy
* Clean up unused game entries to reduce database size
* Backup database before major changes

**Mismatch Resolution:**
* Use consistent naming conventions
* Prefer official game titles over regional variants
* Test changes by triggering presence updates

**Performance Optimization:**
* Allow initial caching to complete before heavy streaming
* Monitor API rate limit usage in logs
* Use appropriate update intervals for your streaming style

Templates and Customization
---------------------------

HTML Templates
~~~~~~~~~~~~~~

Mycelian uses HTML templates for browser sources that can be customized:

* Edit templates in the ``templates/`` directory
* Use the template configuration system
* Preview changes in real-time

CSS Styling
~~~~~~~~~~~

Customize the appearance of your sources:

* Modify CSS in template files
* Use the built-in theme editor
* Create custom animations

Audio Configuration
~~~~~~~~~~~~~~~~~~~

Configure sound effects for alerts:

* Add custom sound files to ``assets/sounds/``
* Set volume levels and audio mixing
* Test audio through the interface

Advanced Features
-----------------

Database Management
~~~~~~~~~~~~~~~~~~~

* View alert history
* Export configuration settings
* Backup and restore data

Statistics Manager
~~~~~~~~~~~~~~~~~~

Mycelian includes a comprehensive statistics tracking system that monitors various aspects of your streaming activity:

**Tracked Statistics:**

* **Alert Statistics** - Bits, subs, follows, donations, raids, channel points
* **Hype Train Data** - Completion rates by level, total trains started
* **Connector Usage** - Trigger counts, action execution success rates
* **Chatbot Metrics** - Command usage, event triggers, quote interactions
* **Template Usage** - Custom statistics from individual templates

**Accessing Statistics:**

Statistics are available through the desktop application and can be viewed in various sections:

1. **Activity Feed** - Real-time statistics display
2. **Connectors Tab** - Connector-specific performance metrics
3. **Chatbot Tab** - Command and event usage statistics
4. **Settings Tab** - Overall system statistics

**Custom Template Statistics:**

Templates can submit their own statistics using the statistics API:

.. code-block:: javascript

   // Submit custom statistics from a template
   socket.emit('submit_template_stat', {
       template_name: 'roulette',
       stat_name: 'spins_today',
       stat_value: 42,
       increment: true
   });

**Data Export:**

Statistics can be exported for external analysis and reporting through the settings interface.

Web Server Architecture
~~~~~~~~~~~~~~~~~~~~~~~

Mycelian runs a Flask web server that serves HTML template files as browser sources:

* **Host:** ``http://localhost:5000`` (default)
* **Templates:** Served from the ``templates/`` directory
* **Static Assets:** Served from the ``assets/`` directory via ``/assets/`` and ``/static/`` routes
* **Template URLs:** Each HTML template is accessible at ``http://localhost:5000/{template_name}``

**Available Template Routes:**

The web server automatically creates routes for all ``.html`` files in the templates directory. Common templates include:

* ``http://localhost:5000/activity_feed`` - Activity feed display
* ``http://localhost:5000/chat`` - Chat integration
* ``http://localhost:5000/bitbar`` - Donation/bits bar
* ``http://localhost:5000/counter`` - Counter display
* ``http://localhost:5000/roulette`` - Roulette wheel
* ``http://localhost:5000/subbar`` - Subscriber bar
* ``http://localhost:5000/pausedalerts`` - Pause alerts control

WebSocket API Reference
-----------------------

Mycelian uses WebSocket communication via Socket.IO for real-time interactions between templates and the application. All WebSocket events are transmitted over the Flask-SocketIO connection.

Connection
~~~~~~~~~~

Templates connect to the WebSocket server at: ``http://localhost:5000``

**Connection Events:**

* **connect** - Automatically triggered when a client connects
* **disconnect** - Automatically triggered when a client disconnects

Alert System Events
~~~~~~~~~~~~~~~~~~~

**Client to Server:**

``alert_complete``
  Sent when an alert finishes playing.
  
  **Data:** None
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('alert_complete');

``pause_alerts``
  Toggle the pause state of alerts.
  
  **Data:** None
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('pause_alerts');

``get_pause_status``
  Request the current pause status.
  
  **Data:** None
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_pause_status');

``set_pause_status``
  Set the pause status directly.
  
  **Data:**
  
  * ``paused`` (boolean) - Whether alerts should be paused
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('set_pause_status', {paused: true});

**Server to Client:**

``next_alert``
  Triggers the next alert to be displayed.
  
  **Data:**
  
  * ``alert_type`` (string) - Type of alert (e.g., "follow", "sub", "bits")
  * ``username`` (string) - Username of the user
  * ``message`` (string) - Alert message
  * ``duration`` (number) - Duration in seconds
  * ``volume`` (number) - Volume level (0-100)
  * ``gif_dir`` (string) - Directory path for GIF assets
  * ``gif_name`` (string) - GIF filename
  * ``single_audio_dir`` (string) - Audio directory path
  * ``single_audio_name`` (string) - Audio filename
  * Additional fields based on alert type
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.on('next_alert', function(data) {
         console.log('New alert:', data.alert_type, data.username);
     });

``pause_status_update``
  Broadcasts pause status changes to all clients.
  
  **Data:**
  
  * ``paused`` (boolean) - Current pause state
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.on('pause_status_update', function(data) {
         console.log('Alerts paused:', data.paused);
     });

``alerts_paused``
  Specific event when alerts are paused.
  
  **Data:**
  
  * ``paused`` (boolean) - Always true
  
``alerts_resumed``
  Specific event when alerts are resumed.
  
  **Data:**
  
  * ``paused`` (boolean) - Always false

Data Management Events
~~~~~~~~~~~~~~~~~~~~~~

**Client to Server:**

``set_data``
  Set data in the database.
  
  **Data:**
  
  * ``path`` (string) - Database path
  * ``data`` (object) - Data to set
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('set_data', {
         path: 'alerts/follow',
         data: {enabled: true, message: 'Thanks for following!'}
     });

``update_data``
  Update existing data in the database.
  
  **Data:**
  
  * ``path`` (string) - Database path
  * ``data`` (object) - Data to update
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('update_data', {
         path: 'settings/volume',
         data: {master_volume: 75}
     });

``get_data``
  Retrieve data from the database.
  
  **Data:**
  
  * ``path`` (string) - Database path
  * ``request_etag`` (boolean, optional) - Request ETag with data
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_data', {path: 'alerts/follow'});

**Server to Client:**

``get_data``
  Response to get_data request.
  
  **Data:** Returns the requested data or error information.

Service Integration Events
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Twitch Events:**

``twitch_api_proxy``
  Proxy Twitch API requests.
  
  **Data:**
  
  * ``url`` (string) - Twitch API URL
  * ``method`` (string, optional) - HTTP method (default: "GET")
  * ``params`` (object, optional) - Query parameters
  * ``json_data`` (object, optional) - JSON body data
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('twitch_api_proxy', {
         url: 'https://api.twitch.tv/helix/users',
         method: 'GET',
         params: {login: 'username'}
     });

``get_twitch_data``
  Request current Twitch data.
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_twitch_data');
     
     socket.on('twitch_data_update', function(data) {
         console.log('Twitch data:', data);
     });

**Spotify Events:**

``get_spotify_data``
  Request current Spotify playback data.
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_spotify_data');
     
     socket.on('spotify_data_update', function(data) {
         console.log('Now playing:', data.track_name);
     });

``spotify_oauth_start``
  Start Spotify OAuth flow.
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('spotify_oauth_start');
     
     socket.on('spotify_oauth_url', function(data) {
         window.open(data.url, '_blank');
     });

``spotify_oauth_complete``
  Complete Spotify OAuth with authorization code.
  
  **Data:**
  
  * ``code`` (string) - Authorization code from Spotify
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('spotify_oauth_complete', {code: 'auth_code_here'});

**PlayStation Network Events:**

``get_psn_data``
  Request current PSN data (trophies, game status).

  **Example:**

  .. code-block:: javascript

     socket.emit('get_psn_data');

     socket.on('psn_data_update', function(data) {
         console.log('Current game:', data.current_game_name);
     });

``psn_game_mismatch``
  Emitted when a game name mismatch is detected between PSN presence and trophy APIs.

  **Data:**
  * ``presence_name`` (string) - Game name from presence/social API
  * ``np_title_id`` (string) - Game ID from presence data
  * ``platform`` (string) - PS4, PS5, etc.
  * ``detected_at`` (string) - ISO timestamp when mismatch was detected
  * ``notified`` (boolean) - Whether user has been notified

  **Example:**

  .. code-block:: javascript

     socket.on('psn_game_mismatch', function(mismatch) {
         console.log('Game mismatch detected:', mismatch.presence_name);
         // Show notification to user about the mismatch
         showMismatchNotification(mismatch);
     });

Chat Integration Events
~~~~~~~~~~~~~~~~~~~~~~~

``get-streamer-info``
  Request streamer information for chat templates.
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get-streamer-info');
     
     socket.on('streamer-info', function(data) {
         console.log('Streamer:', data.streamer_name);
     });

``twitch-api-request``
  Make Twitch API requests for chat features (emotes, badges).
  
  **Data:**
  
  * ``endpoint`` (string) - Twitch API endpoint URL
  * ``method`` (string, optional) - HTTP method (default: "GET")
  * ``requestId`` (string) - Unique request identifier
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('twitch-api-request', {
         endpoint: 'https://api.twitch.tv/helix/chat/emotes',
         requestId: 'emotes_request_1'
     });
     
     socket.on('twitch-api-response', function(data) {
         if (data.requestId === 'emotes_request_1') {
             console.log('Emotes:', data.data);
         }
     });

**Server to Client Chat Events:**

``new-message``
  New chat message received.

  **Data:**

  * ``username`` (string) - Message author
  * ``message`` (string) - Message content
  * ``badges`` (array) - User badges
  * ``emotes`` (object) - Emote data
  * ``timestamp`` (string) - Message timestamp

``clear-chat``
  Clear all chat messages.

``remove-messages``
  Remove specific messages by ID.

  **Data:** Array of message IDs to remove

**Chatbot Events:**

``chatbot_command_response``
  Response from a chatbot command execution.

  **Data:**

  * ``command`` (string) - The command that was executed
  * ``username`` (string) - User who triggered the command
  * ``response`` (string) - The chatbot's response
  * ``timestamp`` (number) - When the command was executed

  **Example:**

  .. code-block:: javascript

     socket.on('chatbot_command_response', function(data) {
         console.log(`${data.username} used !${data.command}: ${data.response}`);
     });

``chatbot_event_triggered``
  Notification when an automated chatbot event is triggered.

  **Data:**

  * ``event_type`` (string) - Type of event (follow, sub, donation, etc.)
  * ``username`` (string) - User who triggered the event
  * ``response`` (string) - The automated response
  * ``timestamp`` (number) - When the event was triggered

  **Example:**

  .. code-block:: javascript

     socket.on('chatbot_event_triggered', function(data) {
         console.log(`Auto-response to ${data.event_type} from ${data.username}`);
     });

**Statistics Events:**

``statistics_update``
  Real-time statistics update broadcast.

  **Data:**

  * ``category`` (string) - Statistics category (alerts, connectors, chatbot, etc.)
  * ``data`` (object) - Updated statistics data
  * ``timestamp`` (number) - When the statistics were updated

  **Example:**

  .. code-block:: javascript

     socket.on('statistics_update', function(data) {
         if (data.category === 'alerts') {
             console.log('New alert stats:', data.data);
         }
     });

``get_statistics``
  Request current statistics data.

  **Data:**

  * ``category`` (string, optional) - Specific category to request
  * ``request_id`` (string) - Unique request identifier

  **Example:**

  .. code-block:: javascript

     socket.emit('get_statistics', {
         category: 'alerts',
         request_id: 'stats_request_1'
     });

     socket.on('statistics_response', function(data) {
         if (data.request_id === 'stats_request_1') {
             console.log('Statistics:', data.stats);
         }
     });

Activity Feed Events
~~~~~~~~~~~~~~~~~~~~

``get_stored_alerts_paginated``
  Request paginated stored alerts for activity feed.
  
  **Data:**
  
  * ``page`` (number) - Page number
  * ``limit`` (number) - Alerts per page
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_stored_alerts_paginated', {page: 1, limit: 25});
     
     socket.on('stored_alerts_paginated', function(data) {
         console.log('Alerts:', data.alerts);
     });

``activity_feed_replay_alert``
  Replay an alert from the activity feed.
  
  **Data:** Alert object to replay
  
``activity_feed_skip_alert``
  Skip/dismiss an alert from the activity feed.
  
  **Data:** Alert object to skip

**Server to Client Activity Events:**

``activity_feed_alert``
  New alert for the activity feed.
  
  **Data:** Alert information formatted for activity feed display

Template Control Events
~~~~~~~~~~~~~~~~~~~~~~~

Dynamic template controls generate events in the format: ``{template_name}_{action}``

**Counter Template:**

* ``counter_counter_increment`` - Increment counter
* ``counter_counter_decrement`` - Decrement counter  
* ``counter_counter_reset`` - Reset counter
* ``counter_counter_update`` - Update counter value

**Roulette Template:**

* ``roulette_spin`` - Spin the wheel
* ``roulette_add_option`` - Add new option
* ``roulette_reset`` - Reset wheel

**Example:**

.. code-block:: javascript

   // Increment counter
   socket.emit('counter_counter_increment');
   
   // Add roulette option
   socket.emit('roulette_add_option', {text: 'New Option'});

Connector Automation Events
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The connector system uses WebSocket events for template control actions and system communication:

**Template Control Events from Connectors:**

Template control actions in connectors generate WebSocket events in the format: ``{template_name}_{control_action}``

* **Counter Controls:**
  - ``counter_counter_increment`` - Increment counter
  - ``counter_counter_decrement`` - Decrement counter
  - ``counter_counter_reset`` - Reset counter
  - ``counter_counter_update`` - Update to specific value

* **Roulette Controls:**
  - ``roulette_spin`` - Spin the wheel
  - ``roulette_add_option`` - Add new option to wheel
  - ``roulette_reset`` - Reset wheel configuration

* **Custom Template Controls:**
  - Templates can define custom connector actions in their ``template_configs/*.json`` files
  - Events are generated based on the ``connector_actions`` configuration
  - Control data includes trigger context and event information

**Example Template Control from Connector:**

.. code-block:: javascript

   // Listen for connector-triggered counter increment
   socket.on('counter_counter_increment', function(data) {
       // data includes:
       // - trigger_id: ID of the trigger that fired
       // - event_type: Type of event (e.g., "twitch_bits")
       // - timestamp: When the trigger fired
       // - event data placeholders replaced
       console.log('Counter incremented by connector:', data);
   });

**Connector System Events:**

``connector_event``
  Generic event emitted by connectors for custom WebSocket actions.
  
  **Data:**
  
  * ``event_name`` (string) - Custom event name
  * ``trigger_id`` (string) - ID of the trigger that fired
  * ``triggered_at`` (number) - Timestamp when triggered
  * ``event_data`` (object) - Original event data that triggered the connector
  * Custom data fields based on action configuration
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.on('connector_event', function(data) {
         if (data.event_name === 'high_bits_celebration') {
             console.log('High bits celebration triggered:', data);
         }
     });

**Manual Connector Triggers:**

Templates can trigger manual connector events:

``trigger_manual_connector``
  Trigger connectors with manual trigger type.
  
  **Data:**
  
  * ``event_data`` (object) - Custom event data to pass to connectors
  * ``connector_id`` (string, optional) - Specific connector to trigger
  
  **Example:**
  
  .. code-block:: javascript
  
     // Trigger manual connectors with custom data
     socket.emit('trigger_manual_connector', {
         event_data: {
             username: 'StreamerBot',
             action: 'test_automation',
             custom_value: 42
         }
     });

**Connector Integration for Templates:**

Templates can integrate with the connector system by:

1. **Defining Connector Actions** in their ``template_configs/*.json`` file:

.. code-block:: json

   {
     "connector_actions": {
       "spin_wheel": {
         "action_name": "Spin Wheel",
         "elements": [
           {
             "type": "select",
             "id": "spin_speed",
             "label": "Spin Speed",
             "options": ["slow", "normal", "fast"],
             "value": "normal"
           }
         ]
       }
     }
   }

2. **Listening for Connector Events** in their JavaScript:

.. code-block:: javascript

   // Listen for connector-triggered actions
   socket.on('roulette_spin_wheel', function(data) {
       // data.spin_speed contains the configured speed
       // data.trigger_id contains the trigger information
       // data.event_type contains the original event type
       startWheelSpin(data.spin_speed);
   });

3. **Providing Dynamic Data** back to connectors:

.. code-block:: javascript

   // Emit template state for connector conditions
   socket.emit('template_state_update', {
       template_name: 'roulette',
       state: {
           current_option: 'Option 1',
           spin_count: 42,
           is_spinning: false
       }
   });

System Events
~~~~~~~~~~~~~

``get_route_info``
  Request information about registered template routes.
  
``force_route_resync``
  Force resynchronization of template routes.
  
``get_audio_files``
  List audio files in a specified folder.
  
  **Data:**
  
  * ``folder`` (string) - Folder name under /assets/alerts/
  * ``request_id`` (string) - Unique request identifier
  
  **Example:**
  
  .. code-block:: javascript
  
     socket.emit('get_audio_files', {
         folder: 'follows',
         request_id: 'audio_request_1'
     });
     
     socket.on('audio_files_response', function(data) {
         console.log('Audio files:', data.files);
     });

Error Handling
~~~~~~~~~~~~~~

Most WebSocket events can return error responses. Always listen for error events:

.. code-block:: javascript

   socket.on('spotify_data_error', function(data) {
       console.error('Spotify error:', data.error);
   });
   
   socket.on('psn_data_error', function(data) {
       console.error('PSN error:', data.error);
   });

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Alerts not displaying:**
1. Check browser source URL
2. Verify alert configuration
3. Test with preview function

**Service connection errors:**
1. Verify API credentials
2. Check internet connection
3. Review logs for detailed error messages

**Performance issues:**
1. Close unnecessary browser sources
2. Reduce alert animation complexity
3. Check system resources

**WebSocket connection issues:**
1. Verify the web server is running on the correct port
2. Check browser console for connection errors
3. Ensure templates are connecting to the correct WebSocket URL
4. Check firewall settings for port 5000 (default)

**Connector system issues:**
1. **Connectors not triggering:** Verify service integrations are connected, check trigger conditions for accuracy, test with manual triggers
2. **Actions not executing:** Review action parameters for correctness, check logs for execution errors, verify template/API endpoints are accessible
3. **Template controls not responding:** Ensure templates support the connector action, verify WebSocket connections are active, check template configuration files
4. **Hotkey triggers not working:** Check if hotkeys are registered globally, verify key combinations don't conflict with other applications, test in different applications

**Chatbot system issues:**
1. **Commands not responding:** Verify chatbot is enabled, check command permissions, ensure proper command prefixes (!)
2. **Automated events not triggering:** Confirm service integrations are connected, review event conditions, check cooldown settings
3. **Greeting system not working:** Verify greeting cooldown settings, check custom greeting configurations, ensure user data is available
4. **Quote system issues:** Check quote permissions, verify quote storage is working, review quote search functionality

**Statistics system issues:**
1. **Statistics not updating:** Verify statistics manager is running, check database connections, review statistics tracking code
2. **Template statistics not showing:** Ensure templates are properly submitting statistics, verify WebSocket connections, check statistics API usage
4. **Performance impact:** Monitor system resources, disable unnecessary connectors, optimize condition logic for efficiency 