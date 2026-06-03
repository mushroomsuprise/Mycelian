Installation
============

System Requirements
-------------------

* Python 3.10 or higher
* Windows, macOS, or Linux
* At least 2GB of available RAM
* Internet connection for streaming service integrations
* OBS Studio (for browser source integration)

Installing from Source
----------------------

1. Clone the repository::

    git clone https://github.com/yourusername/mycelian.git
    cd mycelian

2. Install using UV (recommended)::

    uv pip install -e .

   Or using pip::

    pip install -e .

3. Install development dependencies (optional)::

    uv pip install -e ".[docs]"

First Launch
------------

Desktop Application Interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Launch Mycelian::

    python main.py

2. The desktop application will open with a tabbed interface including:
   * **Activity Feed:** Real-time alert monitoring and history
   * **Settings:** Service configuration and database setup
   * **Custom Sources:** Template configuration management
   * **Source Controls:** Interactive template controls
   * **Connectors:** Automation workflow designer and management

3. The Flask web server will automatically start on ``http://localhost:5000`` to serve browser sources

Initial Configuration
---------------------

Database Setup
~~~~~~~~~~~~~~

On first launch, configure your database in the **Settings** tab:

**Option 1: Firebase Realtime Database (Recommended for cloud sync)**

1. Create a Firebase project at https://console.firebase.google.com/
2. Enable Realtime Database
3. Download your service account key file
4. In Settings → Database Configuration:
   * Select "Firebase" as database type
   * Browse and select your Firebase key file
   * Enter your database URL
   * Test the connection

**Option 2: Local SQLite Database**

1. In Settings → Database Configuration:
   * Select "SQLite" as database type
   * Choose or create a local database file
   * Test the connection

The application will create necessary database structures automatically.

Service Integration Setup
-------------------------

**🔑 IMPORTANT - Personal Credentials Required:**

Each service integration requires you to create your own developer applications and obtain your own unique credentials. Mycelian does not provide shared API keys or credentials. You must:

* Create developer accounts with each service you want to use
* Register your own applications on their developer platforms  
* Generate your own Client IDs and Client Secrets
* Use your own account credentials for OAuth authorization

This ensures your data privacy and complies with each service's terms of use.

Twitch Integration
~~~~~~~~~~~~~~~~~~

**Prerequisites:**
* Active Twitch account
* Twitch Developer account

**⚠️ Important: You MUST create your own Twitch application to get unique credentials**

**Setup Process:**

1. **Create Your Own Twitch Application:**
   * Visit https://dev.twitch.tv/console/apps
   * Log in with your Twitch account
   * Click "Register Your Application"
   * Fill in application details:
     - Name: "Mycelian" (or your preferred name)
     - OAuth Redirect URI: ``http://localhost:3000``
     - Category: "Application Integration"
   * Click "Create"

2. **Get Your Unique Credentials:**
   * Click "Manage" on your newly created application
   * Copy your **Client ID** (visible immediately)
   * Click "New Secret" to generate your **Client Secret**
   * Copy your **Client Secret** (keep this secure!)

3. **Configure Mycelian:**
   * In Mycelian Settings → Service Integrations → Twitch:
   * Enter YOUR unique Client ID and Client Secret
   * Click "Connect to Twitch"
   * Complete OAuth authorization in your browser
   * Verify connection status shows "Connected"

**Features Enabled:**
* Real-time follower and subscription alerts
* Chat integration with emotes and badges
* Channel point redemption notifications
* Raid and host alerts
* Bits/cheer tracking

Spotify Integration
~~~~~~~~~~~~~~~~~~~

**Prerequisites:**
* Spotify Premium account (for full playback control)
* Spotify Developer account

**⚠️ Important: You MUST create your own Spotify application to get unique credentials**

**Setup Process:**

1. **Create Your Own Spotify Application:**
   * Visit https://developer.spotify.com/dashboard
   * Log in with your Spotify account
   * Click "Create app"
   * Fill in application details:
     - App name: "Mycelian" (or your preferred name)
     - App description: "Stream overlay integration"
     - Website: Leave blank or add your stream URL
     - Redirect URI: ``http://localhost:8888/callback``
     - Which API/SDKs are you planning to use: "Web API"
   * Accept terms and click "Save"

2. **Get Your Unique Credentials:**
   * Click on your newly created application
   * Go to "Settings"
   * Copy your **Client ID** (visible immediately)
   * Click "View client secret" to reveal your **Client Secret**
   * Copy your **Client Secret** (keep this secure!)

3. **Configure Mycelian:**
   * In Mycelian Settings → Service Integrations → Spotify:
   * Enter YOUR unique Client ID and Client Secret
   * Click "Connect to Spotify"
   * Authorize the application in your browser
   * Verify connection status shows "Connected"

**Features Enabled:**
* Now playing track display
* Album artwork integration
* Playback history tracking
* Track information for overlays

YouTube Integration
~~~~~~~~~~~~~~~~~~~

**Prerequisites:**
* Google account (Gmail)
* YouTube channel(s) you want to monitor
* Basic understanding of API concepts

**⚠️ Important: You MUST create your own Google Cloud project and API key**

**Setup Process:**

1. **Create a Google Cloud Project:**

   * Visit https://console.cloud.google.com/
   * Sign in with your Google account (Gmail)
   * Click "Create Project" (or select an existing project if you have one)
   * Enter a project name: "Mycelian YouTube Integration" (or your preferred name)
   * Choose your organization if applicable
   * Click "Create"

2. **Enable the YouTube Data API v3:**

   * In your Google Cloud Console, go to "APIs & Services" → "Library"
   * Search for "YouTube Data API v3"
   * Click on "YouTube Data API v3" from the results
   * Click "Enable" to activate the API for your project

3. **Create API Credentials:**

   * Go to "APIs & Services" → "Credentials"
   * Click "Create Credentials" → "API key"
   * Your API key will be generated and displayed
   * **IMPORTANT:** Copy and save your API key immediately (you won't be able to see it again)
   * **SECURITY NOTE:** Keep your API key secure and never share it publicly

4. **Configure API Key Restrictions (Recommended):**

   * Click on your newly created API key
   * Under "API restrictions":
     - Select "Restrict key"
     - Check "YouTube Data API v3"
   * Under "Application restrictions":
     - Select "HTTP referrers (web sites)"
     - Add referrer: ``localhost:*`` (for local development)
     - You can add additional restrictions based on your needs
   * Click "Save" to apply the restrictions

5. **Configure Mycelian:**

   * Launch Mycelian and go to Settings → YouTube Integration tab
   * Enter your API key in the "API Key" field
   * Enter channel URLs separated by pipe symbols (|):
     ``https://youtube.com/@Channel1|https://youtube.com/@Channel2``
   * Supported URL formats:
     - ``https://youtube.com/@ChannelHandle``
     - ``https://youtube.com/channel/UC_CHANNEL_ID``
     - ``https://youtube.com/c/CustomChannelName``
     - ``https://youtube.com/user/Username``
   * Click "Save Settings"
   * Test the connection to verify everything works

**API Quota and Costs:**

* **Free Tier:** YouTube Data API v3 provides 10,000 units per day for free
* **Cost:** $0.50 per 1,000 units beyond the free tier
* **Usage:** Each video/channel lookup uses approximately 1-3 units
* **Monitoring:** With 5-minute refresh intervals, typical usage is well within free limits

**Security Best Practices:**

* **API Key Restrictions:** Always restrict your API key to specific APIs and referrers
* **Regular Rotation:** Rotate your API keys periodically for security
* **Environment Variables:** Consider using environment variables for API keys in production
* **Access Control:** Only share API keys with trusted team members

**Troubleshooting API Key Issues:**

* **"API key not valid" error:**
  - Verify you copied the API key correctly
  - Check that the YouTube Data API v3 is enabled in your project
  - Ensure API key restrictions aren't blocking your requests

* **"Daily quota exceeded" error:**
  - Wait for the quota to reset (24 hours)
  - Consider increasing your refresh intervals
  - Upgrade to a paid plan if needed

* **"Access denied" error:**
  - Check API key restrictions are configured correctly
  - Verify the HTTP referrer matches your setup
  - Ensure you're using the correct API key for the correct project

**Features Enabled:**
* Multi-channel video monitoring (unlimited channels)
* Real-time latest video detection across all channels
* Channel-specific and global latest video variables
* Automatic video filtering based on keywords
* WebSocket integration for live template updates
* Chatbot integration with dynamic YouTube variables

PlayStation Network Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Prerequisites:**
* Active PlayStation Network account
* PSN username you want to monitor for trophies

**⚠️ Important: You must use your own PSN account credentials**

**Setup Process:**

1. **Configure Your PSN Account:**
   * In Settings → Service Integrations → PlayStation Network:
   * Enter YOUR PSN username (the account you want to monitor)
   * Configure trophy notification preferences
   * Test the connection
   * Verify your profile is accessible for trophy tracking

**Features Enabled:**
* Trophy unlock notifications
* Game status updates
* Achievement progress tracking
* Game artwork display

Streamlabs Integration
~~~~~~~~~~~~~~~~~~~~~~~

**⚠️ Important: You must have your own accounts with these services**

**Streamlabs:**

**Prerequisites:**
* Active Streamlabs account linked to your streaming platform
* Streamlabs dashboard access

**Setup Process:**

1. **Ensure Streamlabs Account Setup:**
   * Visit https://streamlabs.com/
   * Sign in with your streaming platform account (Twitch/YouTube)
   * Complete your Streamlabs dashboard setup

2. **Connect to Mycelian:**
   * In Mycelian Settings → Service Integrations → Streamlabs:
   * Click "Connect to Streamlabs"
   * Complete OAuth authorization using YOUR Streamlabs account
   * Configure event preferences
   * Verify connection status shows "Connected"

OBS Studio Integration
----------------------

Browser Source Setup
~~~~~~~~~~~~~~~~~~~~

1. **Enable Templates:** Ensure the Flask server is running (automatic with Mycelian)

2. **Add Browser Sources in OBS:**
   * Create new Browser Source
   * Set URL to ``http://localhost:5000/{template_name}``
   * Common templates:
     - Alerts: ``http://localhost:5000/bitboss``
     - Activity Feed: ``http://localhost:5000/activity_feed``
     - Chat: ``http://localhost:5000/chat``
     - Now Playing: ``http://localhost:5000/spotify``

3. **Configure Source Properties:**
   * Set width/height appropriate for your template

Template Configuration
----------------------

Custom Sources Tab
~~~~~~~~~~~~~~~~~~

Use the **Custom Sources** tab to configure template appearance:

1. **Select Template:** Choose from available configurations
2. **Edit Properties:** Modify colors, fonts, animations, and layout
3. **Save Changes:** Apply configuration to template files
4. **Preview:** View changes in real-time

Source Controls Tab
~~~~~~~~~~~~~~~~~~~

Use the **Source Controls** tab for real-time interaction:

1. **Interactive Elements:** Buttons, sliders, toggles for live control
2. **Dynamic Updates:** Changes reflect immediately in browser sources
3. **Template Actions:** Trigger animations, reset counters, spin wheels

Connector Automation Setup
---------------------------

Connectors Tab
~~~~~~~~~~~~~~

The **Connectors** tab provides a powerful automation system for creating trigger-action workflows:

**Getting Started with Connectors:**

1. **Access the Connectors Tab:** Open the Connectors tab in the desktop application
2. **Create Your First Connector:** Click "New Connector" to open the creation dialog
3. **Configure Basic Information:**
   * Enter a descriptive name for your connector
   * Add an optional description explaining what it does

4. **Set Up Triggers:**
   * Choose from 15+ trigger types (Twitch events, donations, timers, manual triggers)
   * Add optional conditions to make triggers more specific
   * Configure trigger-specific parameters

5. **Configure Actions:**
   * Add one or more actions to execute when triggered
   * Choose from 9+ action types including:
     - Template controls (counters, roulette wheels, etc.)
     - Chat messages with dynamic placeholders
     - API calls to external services
     - File operations and logging
     - System commands and keyboard/mouse input
     - Audio control (volume, microphone)

6. **Test Your Connector:**
   * Use the built-in test functionality to verify your connector works
   * Review the test results and debug any issues
   * Enable the connector when you're satisfied it works correctly

**Example Connector Workflows:**

* **High Bits Counter:** When someone cheers 100+ bits → increment a counter template
* **VIP Follow Response:** When a user with "VIP" in their name follows → send a special chat message
* **Donation Logger:** When someone donates $5+ → write donation details to a log file
* **Music Control:** When channel points are redeemed for "Next Song" → make API call to skip track
* **Alert Trigger:** When specific chat command is used → trigger a custom alert animation

**Connector Management:**

* **Statistics:** View how often connectors trigger and execute actions
* **Enable/Disable:** Toggle connectors on/off without deleting them
* **Edit:** Modify existing connectors with the same interface used for creation
* **Delete:** Remove connectors you no longer need
* **Examples:** Use the "Examples" button to create pre-configured sample connectors

Verification
------------

Testing Your Setup
~~~~~~~~~~~~~~~~~~

1. **Database Connection:** Check Settings → Database for green status indicator
2. **Service Connections:** Verify all integrations show "Connected" status
3. **Browser Sources:** Add a test browser source in OBS to confirm template loading
4. **Alert Testing:** Use test buttons in Settings to verify alert functionality
5. **WebSocket Communication:** Check Activity Feed for real-time event updates
6. **Connector Testing:** Create a simple test connector and verify it triggers correctly

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Port Already in Use (5000):**
* Close other applications using port 5000
* Or modify the port in application settings and update OBS browser source URLs

**Database Connection Failed:**
* For Firebase: Verify key file path and database URL
* For SQLite: Check file permissions and path accessibility
* Test connection using built-in connection tester

**Service Authentication Errors:**
* Verify API credentials are correct and active
* Check OAuth redirect URIs match exactly
* Re-authorize connections if tokens expire
* Review service-specific permissions and scopes

**Browser Sources Not Loading:**
* Confirm Flask server is running (check Activity Feed tab)
* Verify template files exist in ``templates/`` directory
* Check browser console for JavaScript errors
* Ensure OBS browser source URL format is correct

**WebSocket Connection Issues:**
* Verify web server is running on correct port
* Check firewall settings for port 5000
* Review browser console for connection errors
* Confirm templates are properly connecting to Socket.IO

**Template Controls Not Working:**
* Check Source Controls tab for registered dynamic controls
* Verify template configuration includes ``dynamic_controls`` section
* Ensure WebSocket events are properly defined
* Test with Source Controls tab before using in OBS

**Performance Issues:**
* Close unnecessary browser sources in OBS
* Reduce animation complexity in template configurations
* Monitor system resources and available RAM
* Consider using audio-only alerts for resource-intensive setups

**Connector Issues:**
* **Connectors Not Triggering:** Verify service integrations are connected and active, check trigger conditions for typos, test with manual trigger events
* **Actions Not Executing:** Review action configuration parameters, check logs for execution errors, verify template names and action IDs are correct
* **Template Control Actions Failing:** Ensure template supports the specified action, verify WebSocket connection is active, check template configuration includes required elements
* **Test Mode Not Working:** Confirm connector is properly saved, check that all required fields are filled, review test data matches trigger expectations

Migration from Other Platforms
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Alert Import:**
* Use Settings → Alert Migration to import from Streamlabs
* Backup your current alert configurations before migration
* Test imported alerts thoroughly before going live

**Database Migration:**
* Use Settings → Database Migration for switching between Firebase and SQLite
* Create backups before starting migration process
* Monitor migration progress and logs for any issues

Getting Help
~~~~~~~~~~~~

**Diagnostic Information:**
* Check ``logs/`` directory for detailed error messages
* Use Settings → Debug Information for system status
* Monitor Activity Feed for real-time event processing

**Support Resources:**
* Verify all dependencies are properly installed with ``uv pip list``
* Ensure your system meets minimum requirements
* Check template configurations for syntax errors
* Review WebSocket event logs for communication issues

**Log Locations:**
* Application logs: ``logs/mycelian.log`` (capped at 50MB; oldest entries are removed when the limit is reached)
* If an older build left ``mycelian.log`` full of blank lines or stray characters on Windows, delete or rename that file once after updating; the app recreates it on the next launch.

**Support note:** Corrupted logs from pre-fix Windows builds are safe to delete; no settings are stored in ``mycelian.log``.