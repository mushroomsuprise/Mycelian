Custom Templates Guide
======================

Mycelian allows you to create custom HTML templates that integrate with your streaming services through WebSocket communication. This guide covers template creation, data integration, and advanced features.

Template System Overview
-------------------------

Template Architecture
~~~~~~~~~~~~~~~~~~~~~

* **HTML Templates:** Stored in ``templates/`` directory
* **Template Configs:** JSON configuration files in ``templates/template_configs/``
* **Assets:** Static files (images, sounds, fonts) in ``assets/`` directory
* **WebSocket Communication:** Real-time data via Socket.IO on ``http://localhost:5000``
* **Auto-Registration:** Templates automatically become available as routes

File Structure
~~~~~~~~~~~~~~

.. code-block::

    templates/
    ├── my_custom_template.html          # Your HTML template
    ├── template_configs/
    │   └── my_custom_template.json      # Configuration file
    └── assets/
        ├── my_custom_template/          # Template-specific assets
        │   ├── images/
        │   ├── sounds/
        │   └── styles/
        └── default_assets/              # Shared assets
            ├── fonts/
            ├── images/
            └── sounds/

Creating Your First Template
-----------------------------

Basic HTML Template
~~~~~~~~~~~~~~~~~~~

Create ``templates/my_alerts.html``:

.. code-block:: html

    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>My Custom Alerts</title>
        <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
        <style>
            body {
                margin: 0;
                padding: 0;
                background: transparent;
                font-family: 'Arial', sans-serif;
                overflow: hidden;
            }
            
            .alert-container {
                position: fixed;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                text-align: center;
                opacity: 0;
                transition: opacity 0.5s ease-in-out;
            }
            
            .alert-container.show {
                opacity: 1;
            }
            
            .alert-title {
                font-size: 36px;
                font-weight: bold;
                color: #ffffff;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                margin-bottom: 10px;
            }
            
            .alert-message {
                font-size: 24px;
                color: #cccccc;
                text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
            }
        </style>
    </head>
    <body>
        <div id="alertContainer" class="alert-container">
            <div id="alertTitle" class="alert-title"></div>
            <div id="alertMessage" class="alert-message"></div>
        </div>

        <script>
            // Connect to WebSocket
            const socket = io();
            
            // Handle new alerts
            socket.on('next_alert', function(data) {
                showAlert(data);
            });
            
            function showAlert(alertData) {
                const container = document.getElementById('alertContainer');
                const title = document.getElementById('alertTitle');
                const message = document.getElementById('alertMessage');
                
                // Set alert content
                title.textContent = alertData.alert_type.toUpperCase();
                message.textContent = alertData.username + ' - ' + alertData.message;
                
                // Show alert
                container.classList.add('show');
                
                // Hide after duration
                setTimeout(() => {
                    container.classList.remove('show');
                    
                    // Notify completion
                    socket.emit('alert_complete');
                }, alertData.duration * 1000 || 5000);
            }
        </script>
    </body>
    </html>

Creating JSON Configuration Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Template configuration files define the customizable settings for your templates. They are stored in ``templates/template_configs/`` and follow a specific structure.

**Basic Structure:**

.. code-block:: json

    {
        "template_name": "my_template",
        "elements": [
            {
                "type": "separator",
                "label": "Section Title"
            },
            {
                "type": "text",
                "id": "setting_id",
                "label": "Setting Display Name",
                "value": "default_value",
                "description": "Description of what this setting does"
            }
        ]
    }

**Available Element Types:**

**Text Input:**

.. code-block:: json

    {
        "type": "text",
        "id": "custom_text",
        "label": "Custom Text",
        "value": "Default text here",
        "description": "Enter custom text to display"
    }

**Number Input:**

.. code-block:: json

    {
        "type": "number",
        "id": "animation_duration",
        "label": "Animation Duration",
        "value": 5,
        "min": 1,
        "max": 30,
        "step": 0.5,
        "description": "Duration in seconds"
    }

**Slider Input:**

.. code-block:: json

    {
        "type": "slider",
        "id": "opacity_level",
        "label": "Opacity Level",
        "value": 80,
        "min": 0,
        "max": 100,
        "step": 5,
        "description": "Opacity percentage"
    }

**Checkbox (Boolean):**

.. code-block:: json

    {
        "type": "checkbox",
        "id": "enable_feature",
        "label": "Enable Feature",
        "value": true,
        "description": "Check to enable this feature"
    }

**Toggle (Alternative Boolean):**

.. code-block:: json

    {
        "type": "toggle",
        "id": "compact_mode",
        "label": "Compact Mode",
        "value": false,
        "description": "Use compact layout"
    }

**Color Selector:**

.. code-block:: json

    {
        "type": "select",
        "id": "background_color",
        "label": "Background Color",
        "value": "#7c3aed",
        "description": "Choose background color",
        "display": "color_grid",
        "transparent": true,
        "options": [
            "#e11d48",
            "#dc2626",
            "#ea580c",
            "#d97706",
            "#ca8a04",
            "#65a30d",
            "#16a34a",
            "#059669",
            "#0891b2",
            "#0284c7",
            "#2563eb",
            "#4f46e5",
            "#7c3aed",
            "#9333ea",
            "#c026d3",
            "#db2777",
            "#ffffff",
            "#f8fafc",
            "#f1f5f9",
            "#e2e8f0",
            "#cbd5e1",
            "#94a3b8",
            "#64748b",
            "#475569",
            "#334155",
            "#1e293b",
            "#0f172a",
            "#1a1a1a",
            "#000000",
            "transparent"
        ]
    }

**Dropdown Select:**

.. code-block:: json

    {
        "type": "select",
        "id": "font_weight",
        "label": "Font Weight",
        "value": "bold",
        "description": "Choose font weight",
        "options": [
            "normal",
            "bold",
            "bolder",
            "lighter",
            "100",
            "200",
            "300",
            "400",
            "500",
            "600",
            "700",
            "800",
            "900"
        ]
    }

**Section Separator:**

.. code-block:: json

    {
        "type": "separator",
        "label": "Visual Settings"
    }

**Complete Example Configuration:**

.. code-block:: json

    {
        "template_name": "my_alerts",
        "elements": [
            {
                "type": "separator",
                "label": "General Settings"
            },
            {
                "type": "checkbox",
                "id": "enabled",
                "label": "Enable Template",
                "value": true,
                "description": "Enable or disable this template entirely"
            },
            {
                "type": "text",
                "id": "alert_title",
                "label": "Alert Title",
                "value": "New Alert!",
                "description": "Text shown as the alert title"
            },
            {
                "type": "separator",
                "label": "Visual Settings"
            },
            {
                "type": "select",
                "id": "title_color",
                "label": "Title Color",
                "value": "#ffffff",
                "description": "Color of the alert title text",
                "display": "color_grid",
                "transparent": false,
                "options": [
                    "#ffffff",
                    "#000000",
                    "#e11d48",
                    "#dc2626",
                    "#ea580c",
                    "#d97706",
                    "#ca8a04",
                    "#65a30d",
                    "#16a34a",
                    "#059669",
                    "#0891b2",
                    "#0284c7",
                    "#2563eb",
                    "#4f46e5",
                    "#7c3aed",
                    "#9333ea",
                    "#c026d3",
                    "#db2777"
                ]
            },
            {
                "type": "number",
                "id": "font_size",
                "label": "Font Size",
                "value": 24,
                "min": 12,
                "max": 48,
                "step": 2,
                "description": "Font size in pixels"
            },
            {
                "type": "slider",
                "id": "opacity",
                "label": "Background Opacity",
                "value": 80,
                "min": 0,
                "max": 100,
                "step": 5,
                "description": "Background opacity percentage"
            },
            {
                "type": "separator",
                "label": "Animation Settings"
            },
            {
                "type": "number",
                "id": "alert_duration",
                "label": "Alert Duration",
                "value": 5.0,
                "min": 1.0,
                "max": 30.0,
                "step": 0.5,
                "description": "How long alerts are displayed in seconds"
            },
            {
                "type": "select",
                "id": "animation_easing",
                "label": "Animation Easing",
                "value": "ease-out",
                "description": "Easing function for animations",
                "options": [
                    "linear",
                    "ease",
                    "ease-in",
                    "ease-out",
                    "ease-in-out"
                ]
            },
            {
                "type": "toggle",
                "id": "enable_sound",
                "label": "Enable Sound Effects",
                "value": true,
                "description": "Play sound effects with alerts"
            }
        ]
    }

**Element Properties Reference:**

**Common Properties (all elements):**
- ``type``: Element type (required)
- ``id``: Unique identifier for accessing the value (required)
- ``label``: Display name in the UI (required)
- ``description``: Help text explaining the setting (optional)

**Value Properties:**
- ``value``: Default value (required for most types)

**Number/Slider Properties:**
- ``min``: Minimum allowed value
- ``max``: Maximum allowed value
- ``step``: Increment step size

**Select Properties:**
- ``options``: Array of available choices (required)
- ``display``: Display style (``"color_grid"`` for color pickers)
- ``transparent``: Whether to include transparent option for colors

**Best Practices:**

1. **Use descriptive IDs:** Make IDs clear and consistent (e.g., ``title_color``, ``animation_duration``)

2. **Group related settings:** Use separators to organize settings logically

3. **Provide helpful descriptions:** Explain what each setting does and its effects

4. **Set sensible defaults:** Choose default values that work well out of the box

5. **Use appropriate ranges:** Set reasonable min/max values for numbers and sliders

6. **Color grid organization:** Order colors logically (light to dark, or by hue)

7. **Consistent naming:** Use consistent naming patterns across your configuration

**Template Configuration File Example:**

Save as ``templates/template_configs/my_alerts.json``:

WebSocket Integration
---------------------

Connection Setup
~~~~~~~~~~~~~~~~

All templates must connect to the WebSocket server:

.. code-block:: javascript

    // Connect to Socket.IO server
    const socket = io();
    
    // Handle connection events
    socket.on('connect', function() {
        console.log('Connected to Mycelian');
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from Mycelian');
    });

Alert System Integration
~~~~~~~~~~~~~~~~~~~~~~~~

**Receiving Alerts:**

.. code-block:: javascript

    // Listen for new alerts
    socket.on('next_alert', function(alertData) {
        console.log('New alert:', alertData);
        
        // Alert data structure:
        // {
        //     alert_type: "follow|sub|bits|donation|raid|points",
        //     username: "viewer_name",
        //     message: "alert message",
        //     duration: 5,
        //     volume: 100,
        //     tier: 1,                    // For subs (1, 2, 3)
        //     amt_cheered: 100,           // For bits
        //     donation_amount: 10.00,     // For donations
        //     raider_count: 25,           // For raids
        //     point_cost: 500,            // For channel points
        //     gif_dir: "assets/alerts/",  // GIF directory
        //     gif_name: "follow.gif",     // GIF filename
        //     single_audio_dir: "assets/sounds/",
        //     single_audio_name: "alert.mp3",
        //     user_message: "Thanks for the follow!"  // User's message
        // }
        
        displayAlert(alertData);
    });
    
    // Notify when alert is complete
    function alertComplete() {
        socket.emit('alert_complete');
    }

**Pause/Resume Status:**

.. code-block:: javascript

    // Listen for pause status changes
    socket.on('pause_status_update', function(data) {
        if (data.paused) {
            console.log('Alerts are paused');
            // Hide alert interface or show paused state
        } else {
            console.log('Alerts are resumed');
            // Show alert interface
        }
    });
    
    // Request current pause status
    socket.emit('get_pause_status');

Service Data Integration
------------------------

Twitch Integration
~~~~~~~~~~~~~~~~~~

**Getting Streamer Information:**

.. code-block:: javascript

    // Request streamer info
    socket.emit('get-streamer-info');
    
    socket.on('streamer-info', function(data) {
        console.log('Streamer data:', data);
        
        // Data structure:
        // {
        //     user_id: "123456789",
        //     streamer_name: "MyStreamer",
        //     current_category: "Just Chatting"
        // }
    });

**Twitch API Requests:**

.. code-block:: javascript

    // Make Twitch API requests through proxy
    socket.emit('twitch-api-request', {
        endpoint: 'https://api.twitch.tv/helix/chat/emotes',
        method: 'GET',
        requestId: 'emotes_request_1'
    });
    
    socket.on('twitch-api-response', function(data) {
        if (data.requestId === 'emotes_request_1' && data.success) {
            console.log('Emotes data:', data.data);
        }
    });

**Chat Integration:**

.. code-block:: javascript

    // Listen for new chat messages
    socket.on('new-message', function(messageData) {
        console.log('New chat message:', messageData);
        
        // Message data structure:
        // {
        //     username: "viewer123",
        //     message: "Hello stream!",
        //     badges: ["subscriber", "vip"],
        //     emotes: { /* emote data */ },
        //     timestamp: "2024-01-01T12:00:00Z"
        // }
    });
    
    // Listen for chat clear events
    socket.on('clear-chat', function() {
        // Clear all displayed messages
    });
    
    socket.on('remove-messages', function(messageIds) {
        // Remove specific messages by ID
    });

Spotify Integration
~~~~~~~~~~~~~~~~~~~

**Getting Now Playing Data:**

.. code-block:: javascript

    // Request current Spotify data
    socket.emit('get_spotify_data');
    
    socket.on('spotify_data_update', function(spotifyData) {
        console.log('Now playing:', spotifyData);
        
        // Spotify data structure:
        // {
        //     track_name: "Song Title",
        //     artist_name: "Artist Name",
        //     album_name: "Album Name",
        //     album_art_url: "https://...",
        //     is_playing: true,
        //     progress_ms: 120000,
        //     duration_ms: 240000,
        //     external_url: "https://open.spotify.com/..."
        // }
        
        updateNowPlaying(spotifyData);
    });

PlayStation Network Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Getting PSN Data:**

.. code-block:: javascript

    // Request PSN data
    socket.emit('get_psn_data');
    
    socket.on('psn_data_update', function(psnData) {
        console.log('PSN data:', psnData);
        
        // PSN data structure:
        // {
        //     current_game_name: "Game Title",
        //     current_game_art_url: "https://...",
        //     current_game_trophies: [
        //         {
        //             trophy_name: "Achievement Name",
        //             trophy_type: "bronze|silver|gold|platinum",
        //             earned_date: "2024-01-01T12:00:00Z"
        //         }
        //     ],
        //     trophy_counts: {
        //         platinum: 5,
        //         gold: 25,
        //         silver: 100,
        //         bronze: 500
        //     }
        // }
    });

Activity Feed Integration
~~~~~~~~~~~~~~~~~~~~~~~~~

**Getting Historical Alerts:**

.. code-block:: javascript

    // Request paginated stored alerts
    socket.emit('get_stored_alerts_paginated', {
        page: 1,
        limit: 25
    });
    
    socket.on('stored_alerts_paginated', function(data) {
        console.log('Historical alerts:', data);
        
        // Response structure:
        // {
        //     alerts: [ /* array of alert objects */ ],
        //     page: 1,
        //     total_pages: 10,
        //     total_count: 250,
        //     has_prev: false,
        //     has_next: true
        // }
    });

**Activity Feed Events:**

.. code-block:: javascript

    // Listen for new activity feed alerts
    socket.on('activity_feed_alert', function(alertData) {
        // Display alert in activity feed format
        addToActivityFeed(alertData);
    });

Accessing Template Configuration
----------------------------------

Loading Configuration via API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Templates can dynamically load their JSON configuration files through the web API. This allows real-time updates without requiring template reloads:

.. code-block:: javascript

    // Load all template configurations
    async function loadTemplateConfig() {
        try {
            const response = await fetch('/api/all-template-configs');
            if (response.ok) {
                const allConfigs = await response.json();
                console.log('Loaded all template configs:', Object.keys(allConfigs));
                
                // Get your specific template config
                const myTemplateConfig = allConfigs.my_template_name;
                if (myTemplateConfig && myTemplateConfig.elements) {
                    console.log('Found template config:', myTemplateConfig);
                    
                    // Parse configuration elements into usable values
                    const config = {};
                    myTemplateConfig.elements.forEach(element => {
                        if (element.type !== 'section') {
                            config[element.id] = element.value;
                        }
                    });
                    
                    console.log('Parsed config values:', config);
                    applyTemplateConfig(config);
                } else {
                    console.warn('No template config found');
                }
            } else {
                console.error('Failed to load template configs:', response.status);
            }
        } catch (error) {
            console.error('Error loading template config:', error);
        }
    }

Applying Configuration Settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once loaded, configuration values can be applied to your template elements:

.. code-block:: javascript

    function applyTemplateConfig(config) {
        console.log('Applying template config:', config);
        
        // Apply styling configurations
        const container = document.querySelector('.my-container');
        
        // Position and size settings
        if (config.ContainerLeft !== undefined) {
            container.style.left = config.ContainerLeft + 'px';
        }
        if (config.ContainerTop !== undefined) {
            container.style.top = config.ContainerTop + 'px';
        }
        if (config.ContainerWidth !== undefined) {
            container.style.width = config.ContainerWidth + 'px';
        }
        if (config.ContainerHeight !== undefined) {
            container.style.height = config.ContainerHeight + 'px';
        }
        
        // Font and color settings
        const titleElement = document.getElementById('title');
        if (titleElement) {
            if (config.TitleFontFamily) {
                titleElement.style.fontFamily = config.TitleFontFamily;
            }
            if (config.TitleFontSize !== undefined) {
                titleElement.style.fontSize = config.TitleFontSize + 'px';
            }
            if (config.TitleColor) {
                titleElement.style.color = config.TitleColor;
            }
        }
        
        // Background settings with conditional application
        if (config.EnableBackground) {
            container.style.backgroundColor = config.BackgroundColor || 'rgba(0,0,0,0.5)';
            container.style.padding = (config.BackgroundPadding || 10) + 'px';
            container.style.borderRadius = (config.BackgroundBorderRadius || 5) + 'px';
            if (config.BackgroundOpacity !== undefined) {
                container.style.opacity = config.BackgroundOpacity;
            }
        } else {
            container.style.backgroundColor = 'transparent';
            container.style.padding = '0';
            container.style.borderRadius = '0';
        }
        
        // Apply behavioral settings to global variables
        if (config.AnimationDuration !== undefined) {
            window.animationDuration = config.AnimationDuration;
        }
        if (config.EnableSound !== undefined) {
            window.soundEnabled = config.EnableSound;
        }
        
        // Update audio source if configured
        if (config.AudioFile !== undefined) {
            const audio = document.getElementById('myAudio');
            if (audio && audio.firstElementChild) {
                audio.firstElementChild.src = config.AudioFile;
                audio.load(); // Reload audio with new source
            }
        }
    }

Real-time Configuration Updates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set up periodic reloading to pick up configuration changes:

.. code-block:: javascript

    // Load config on startup
    setTimeout(function() {
        loadTemplateConfig();
    }, 1000);
    
    // Periodically reload config (every 30 seconds)
    setInterval(loadTemplateConfig, 30000);

Configuration-Driven Initialization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use configuration values during template initialization:

.. code-block:: javascript

    // Global configuration object
    var templateConfig = {};
    var configLoaded = false;
    
    // Initialize template after config is loaded
    async function initializeTemplate() {
        // Load configuration first
        await loadTemplateConfig();
        configLoaded = true;
        
        // Set up template with config values
        setupEventListeners();
        initializeDisplay();
        startPeriodicUpdates();
    }
    
    function setupEventListeners() {
        const socket = io();
        
        socket.on('next_alert', function(alertData) {
            if (!configLoaded) {
                console.warn('Config not loaded yet, skipping alert');
                return;
            }
            
            // Use config values in alert processing
            const duration = templateConfig.AlertDuration || 5000;
            const volume = templateConfig.AudioVolume || 1.0;
            
            processAlert(alertData, duration, volume);
        });
    }

Configuration Element Types
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Handle different configuration element types properly:

.. code-block:: javascript

    function parseConfigElement(element) {
        switch (element.type) {
            case 'text':
                return element.value || '';
                
            case 'number':
                return parseFloat(element.value) || 0;
                
            case 'boolean':
                return Boolean(element.value);
                
            case 'color':
                return element.value || '#000000';
                
            case 'select':
                return element.value || element.options[0];
                
            case 'file':
                return element.value || '';
                
            case 'font':
                return element.value || 'Arial';
                
            case 'section':
                // Skip section headers
                return null;
                
            default:
                return element.value;
        }
    }
    
    // Enhanced config parsing
    function parseTemplateConfig(configData) {
        const config = {};
        
        if (configData && configData.elements) {
            configData.elements.forEach(element => {
                const value = parseConfigElement(element);
                if (value !== null) {
                    config[element.id] = value;
                }
            });
        }
        
        return config;
    }

Error Handling for Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Implement robust error handling for configuration loading:

.. code-block:: javascript

    async function loadTemplateConfigSafely() {
        try {
            const response = await fetch('/api/all-template-configs');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const allConfigs = await response.json();
            const myConfig = allConfigs.my_template_name;
            
            if (!myConfig) {
                console.warn('Template config not found, using defaults');
                return applyDefaultConfig();
            }
            
            const parsedConfig = parseTemplateConfig(myConfig);
            applyTemplateConfig(parsedConfig);
            
            return true;
            
        } catch (error) {
            console.error('Failed to load template config:', error);
            
            // Fallback to default configuration
            applyDefaultConfig();
            return false;
        }
    }
    
    function applyDefaultConfig() {
        const defaultConfig = {
            ContainerWidth: 400,
            ContainerHeight: 200,
            TitleColor: '#ffffff',
            TitleFontSize: 24,
            AnimationDuration: 5,
            EnableSound: true
        };
        
        applyTemplateConfig(defaultConfig);
        console.log('Applied default configuration');
    }

Template Controls Integration
-----------------------------

Dynamic Controls
~~~~~~~~~~~~~~~~

Add interactive controls to your templates by defining them in your configuration:

**Configuration (template_configs/interactive_counter.json):**

.. code-block:: json

    {
        "template_name": "interactive_counter",
        "dynamic_controls": {
            "elements": [
                {
                    "type": "counter_control",
                    "id": "main_counter",
                    "label": "Main Counter",
                    "action_increment": "counter_increment",
                    "action_decrement": "counter_decrement",
                    "action_reset": "counter_reset"
                },
                {
                    "type": "button",
                    "id": "special_action",
                    "label": "Special Action",
                    "button_text": "Trigger Effect",
                    "action": "special_effect"
                },
                {
                    "type": "text_input",
                    "id": "message_input",
                    "label": "Send Message",
                    "action": "send_message",
                    "placeholder": "Enter message..."
                }
            ]
        }
    }

**Template JavaScript:**

.. code-block:: javascript

    // Listen for control events
    socket.on('interactive_counter_counter_increment', function() {
        incrementCounter();
    });
    
    socket.on('interactive_counter_counter_decrement', function() {
        decrementCounter();
    });
    
    socket.on('interactive_counter_counter_reset', function() {
        resetCounter();
    });
    
    socket.on('interactive_counter_special_effect', function() {
        triggerSpecialEffect();
    });
    
    socket.on('interactive_counter_send_message', function(data) {
        displayMessage(data.text);
    });

Database Integration
~~~~~~~~~~~~~~~~~~~~

**Setting Data:**

.. code-block:: javascript

    // Save data to database
    socket.emit('set_data', {
        path: 'custom_templates/my_template/settings',
        data: {
            counter_value: 42,
            last_update: new Date().toISOString(),
            user_preferences: {
                theme: 'dark',
                animations: true
            }
        }
    });

**Getting Data:**

.. code-block:: javascript

    // Retrieve data from database
    socket.emit('get_data', {
        path: 'custom_templates/my_template/settings'
    });
    
    socket.on('get_data', function(response) {
        if (response.error) {
            console.error('Database error:', response.error);
        } else {
            console.log('Retrieved data:', response);
            // Use the data to initialize your template
        }
    });

**Updating Data:**

.. code-block:: javascript

    // Update existing data
    socket.emit('update_data', {
        path: 'custom_templates/my_template/settings',
        data: {
            counter_value: 43  // Only update specific fields
        }
    });

Connector Automation Integration
--------------------------------

Templates can be fully integrated with the Connector automation system to enable automated control through trigger-action workflows. This allows your templates to respond to stream events automatically.

Defining Connector Actions
~~~~~~~~~~~~~~~~~~~~~~~~~~

To make your template compatible with the Connector system, add a ``connector_actions`` section to your template configuration file. This defines what actions connectors can perform on your template.

**Configuration (template_configs/my_template.json):**

.. code-block:: json

    {
        "template_name": "my_template",
        "elements": [
            // Regular configuration elements...
        ],
        "connector_actions": {
            "increment_counter": {
                "action_name": "Increment Counter",
                "elements": [
                    {
                        "type": "number",
                        "id": "increment_amount",
                        "label": "Increment Amount",
                        "value": 1,
                        "min": 1,
                        "max": 100,
                        "description": "How much to increment by"
                    }
                ]
            },
            "set_text": {
                "action_name": "Set Text",
                "elements": [
                    {
                        "type": "text",
                        "id": "new_text",
                        "label": "Text Content",
                        "value": "Default text",
                        "placeholder": "Enter text here...",
                        "description": "Text to display in the template"
                    }
                ]
            },
            "trigger_animation": {
                "action_name": "Trigger Animation",
                "elements": [
                    {
                        "type": "select",
                        "id": "animation_type",
                        "label": "Animation Type",
                        "value": "fade",
                        "options": ["fade", "slide", "bounce", "spin"],
                        "description": "Type of animation to trigger"
                    },
                    {
                        "type": "number",
                        "id": "animation_duration",
                        "label": "Duration (seconds)",
                        "value": 2.0,
                        "min": 0.1,
                        "max": 10.0,
                        "step": 0.1,
                        "description": "How long the animation should last"
                    }
                ]
            }
        }
    }

**Action Element Types:**

The ``elements`` array in each connector action supports all the same element types as regular template configuration:

* ``text`` - Text input fields
* ``number`` - Numeric input with validation
* ``select`` - Dropdown selection with options
* ``checkbox`` - Boolean true/false values
* ``slider`` - Numeric slider with range
* ``separator`` - Section headers for organization

Listening for Connector Events
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In your template JavaScript, listen for connector-triggered events using the format: ``{template_name}_{action_id}``

.. code-block:: javascript

    const socket = io();
    
    // Listen for connector-triggered actions
    socket.on('my_template_increment_counter', function(data) {
        console.log('Connector triggered increment:', data);
        
        // data includes:
        // - increment_amount: Value from connector action configuration
        // - trigger_id: ID of the trigger that fired this action
        // - event_type: Type of original event (e.g., "twitch_bits")
        // - timestamp: When the trigger fired
        // - event data from the original trigger
        
        incrementCounter(data.increment_amount || 1);
    });
    
    socket.on('my_template_set_text', function(data) {
        console.log('Connector triggered text update:', data);
        
        // data.new_text contains the configured text (with placeholders replaced)
        updateDisplayText(data.new_text);
    });
    
    socket.on('my_template_trigger_animation', function(data) {
        console.log('Connector triggered animation:', data);
        
        // Use the configured animation parameters
        triggerAnimation(data.animation_type, data.animation_duration);
    });

Placeholder Support in Connector Actions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Connector actions support placeholder replacement, allowing dynamic content based on the triggering event:

**Example Configuration with Placeholders:**

.. code-block:: json

    {
        "connector_actions": {
            "show_donation_message": {
                "action_name": "Show Donation Message",
                "elements": [
                    {
                        "type": "text",
                        "id": "message_template",
                        "label": "Message Template",
                        "value": "Thanks {{username}} for the ${{amount}} donation!",
                        "description": "Use {{username}}, {{amount}}, {{message}} placeholders"
                    },
                    {
                        "type": "number",
                        "id": "display_duration",
                        "label": "Display Duration (seconds)",
                        "value": 5,
                        "min": 1,
                        "max": 30
                    }
                ]
            }
        }
    }

**Template JavaScript:**

.. code-block:: javascript

    socket.on('my_template_show_donation_message', function(data) {
        // data.message_template will have placeholders replaced
        // e.g., "Thanks StreamViewer for the $10.00 donation!"
        
        showMessage(data.message_template, data.display_duration * 1000);
    });
    
    function showMessage(text, duration) {
        const messageEl = document.getElementById('message');
        messageEl.textContent = text;
        messageEl.style.display = 'block';
        
        setTimeout(() => {
            messageEl.style.display = 'none';
        }, duration);
    }

**Available Placeholders:**

Common placeholders available in connector actions:

* ``{{username}}`` - User who triggered the event
* ``{{amount}}`` - Numeric amount (bits, donation, viewer count)
* ``{{message}}`` - Message content (chat, donation message)
* ``{{timestamp}}`` - When the event occurred
* ``{{event_type}}`` - Type of triggering event
* ``{{tier}}`` - Subscription tier (for sub events)
* ``{{months}}`` - Subscription months (for sub events)
* ``{{command}}`` - Chat command (for command events)
* ``{{reward_title}}`` - Channel point reward name

Advanced Connector Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Dynamic Control Data:**

Your template can provide dynamic data back to the connector system for use in conditions:

.. code-block:: javascript

    // Emit template state for connector conditions
    function updateConnectorState() {
        socket.emit('template_state_update', {
            template_name: 'my_template',
            state: {
                counter_value: currentCounter,
                last_action: lastActionType,
                is_active: isTemplateActive,
                current_mode: displayMode
            }
        });
    }
    
    // Call this whenever your template state changes
    incrementCounter = function(amount) {
        currentCounter += amount;
        updateDisplay();
        updateConnectorState(); // Notify connector system
    };

**Complex Action Handlers:**

Handle complex connector actions with multiple configuration parameters:

.. code-block:: javascript

    socket.on('my_template_complex_action', function(data) {
        console.log('Complex action triggered:', data);
        
        // Access all configured parameters
        const config = {
            primaryColor: data.primary_color || '#ffffff',
            secondaryColor: data.secondary_color || '#000000',
            animationSpeed: data.animation_speed || 1.0,
            enableSound: data.enable_sound || false,
            customText: data.custom_text || 'Default text',
            repeatCount: data.repeat_count || 1
        };
        
        // Execute complex template behavior
        executeComplexAnimation(config);
    });

Manual Connector Triggers
~~~~~~~~~~~~~~~~~~~~~~~~~~

Templates can trigger manual connector events for testing or special interactions:

.. code-block:: javascript

    // Trigger manual connectors from template
    function triggerManualConnector(customData) {
        socket.emit('trigger_manual_connector', {
            event_data: {
                username: 'TemplateUser',
                source: 'template_manual',
                action_type: 'button_click',
                template_name: 'my_template',
                ...customData
            }
        });
    }
    
    // Example: Button in template triggers manual connectors
    document.getElementById('testButton').addEventListener('click', function() {
        triggerManualConnector({
            button_id: 'test_button',
            click_count: Math.floor(Math.random() * 100)
        });
    });

Template Action Categories
~~~~~~~~~~~~~~~~~~~~~~~~~~

Organize your connector actions into logical categories:

.. code-block:: json

    {
        "connector_actions": {
            // Counter Actions
            "counter_increment": {
                "action_name": "Increment Counter",
                "category": "Counter",
                "elements": [...]
            },
            "counter_decrement": {
                "action_name": "Decrement Counter", 
                "category": "Counter",
                "elements": [...]
            },
            "counter_reset": {
                "action_name": "Reset Counter",
                "category": "Counter",
                "elements": []
            },
            
            // Visual Actions
            "change_theme": {
                "action_name": "Change Theme",
                "category": "Visual",
                "elements": [...]
            },
            "trigger_effect": {
                "action_name": "Trigger Effect",
                "category": "Visual", 
                "elements": [...]
            },
            
            // Text Actions
            "update_title": {
                "action_name": "Update Title",
                "category": "Text",
                "elements": [...]
            }
        }
    }

Best Practices for Connector Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Descriptive Action Names:** Use clear, descriptive names for connector actions that explain what they do.

2. **Logical Grouping:** Organize related actions and use categories to group them in the UI.

3. **Sensible Defaults:** Provide good default values for action parameters that work out of the box.

4. **Parameter Validation:** Set appropriate min/max values and validate input ranges.

5. **Comprehensive Placeholders:** Support relevant placeholders for the events that might trigger your actions.

6. **Error Handling:** Gracefully handle missing or invalid connector data.

7. **State Reporting:** Update connector state when your template changes to enable condition-based automation.

8. **Testing Support:** Provide ways to manually trigger actions for testing connector workflows.

Example: Complete Connector-Enabled Template
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Template Configuration (template_configs/smart_counter.json):**

.. code-block:: json

    {
        "template_name": "smart_counter",
        "elements": [
            {
                "type": "separator",
                "label": "Display Settings"
            },
            {
                "type": "select",
                "id": "counter_color",
                "label": "Counter Color",
                "value": "#ffffff",
                "display": "color_grid",
                "options": ["#ffffff", "#ff0000", "#00ff00", "#0000ff"]
            },
            {
                "type": "number",
                "id": "font_size",
                "label": "Font Size",
                "value": 48,
                "min": 12,
                "max": 128
            }
        ],
        "connector_actions": {
            "increment": {
                "action_name": "Increment Counter",
                "elements": [
                    {
                        "type": "number",
                        "id": "amount",
                        "label": "Increment Amount",
                        "value": 1,
                        "min": 1,
                        "max": 1000
                    }
                ]
            },
            "set_value": {
                "action_name": "Set Counter Value",
                "elements": [
                    {
                        "type": "number",
                        "id": "new_value",
                        "label": "New Value",
                        "value": 0,
                        "min": 0,
                        "max": 999999
                    }
                ]
            },
            "show_message": {
                "action_name": "Show Message",
                "elements": [
                    {
                        "type": "text",
                        "id": "message_text",
                        "label": "Message",
                        "value": "Thanks {{username}}!",
                        "placeholder": "Enter message with {{placeholders}}"
                    },
                    {
                        "type": "number",
                        "id": "duration",
                        "label": "Duration (seconds)",
                        "value": 3,
                        "min": 1,
                        "max": 30
                    }
                ]
            }
        }
    }

**Template HTML/JavaScript (templates/smart_counter.html):**

.. code-block:: html

    <!DOCTYPE html>
    <html>
    <head>
        <title>Smart Counter</title>
        <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
        <style>
            body { 
                margin: 0; 
                background: transparent; 
                font-family: Arial, sans-serif; 
            }
            .counter { 
                font-size: 48px; 
                font-weight: bold; 
                color: #ffffff; 
                text-align: center; 
                padding: 20px; 
            }
            .message { 
                font-size: 24px; 
                color: #ffff00; 
                text-align: center; 
                display: none; 
                margin-top: 10px; 
            }
        </style>
    </head>
    <body>
        <div id="counter" class="counter">0</div>
        <div id="message" class="message"></div>

        <script>
            const socket = io();
            let counterValue = 0;
            let config = {};

            // Load template configuration
            async function loadConfig() {
                try {
                    const response = await fetch('/api/all-template-configs');
                    const allConfigs = await response.json();
                    const myConfig = allConfigs.smart_counter;
                    
                    if (myConfig && myConfig.elements) {
                        myConfig.elements.forEach(element => {
                            if (element.id) {
                                config[element.id] = element.value;
                            }
                        });
                        applyConfig();
                    }
                } catch (error) {
                    console.error('Failed to load config:', error);
                }
            }

            function applyConfig() {
                const counter = document.getElementById('counter');
                if (config.counter_color) {
                    counter.style.color = config.counter_color;
                }
                if (config.font_size) {
                    counter.style.fontSize = config.font_size + 'px';
                }
            }

            // Connector action handlers
            socket.on('smart_counter_increment', function(data) {
                console.log('Increment triggered by connector:', data);
                counterValue += data.amount || 1;
                updateDisplay();
                updateConnectorState();
            });

            socket.on('smart_counter_set_value', function(data) {
                console.log('Set value triggered by connector:', data);
                counterValue = data.new_value || 0;
                updateDisplay();
                updateConnectorState();
            });

            socket.on('smart_counter_show_message', function(data) {
                console.log('Show message triggered by connector:', data);
                showMessage(data.message_text, data.duration * 1000);
            });

            function updateDisplay() {
                document.getElementById('counter').textContent = counterValue;
            }

            function showMessage(text, duration) {
                const messageEl = document.getElementById('message');
                messageEl.textContent = text;
                messageEl.style.display = 'block';
                
                setTimeout(() => {
                    messageEl.style.display = 'none';
                }, duration);
            }

            function updateConnectorState() {
                socket.emit('template_state_update', {
                    template_name: 'smart_counter',
                    state: {
                        counter_value: counterValue,
                        last_updated: new Date().toISOString()
                    }
                });
            }

            // Initialize
            loadConfig();
            setTimeout(() => loadConfig(), 1000); // Reload periodically
        </script>
    </body>
    </html>

Advanced Features
-----------------

Audio Integration
~~~~~~~~~~~~~~~~~

**Getting Available Audio Files:**

.. code-block:: javascript

    // Get audio files from a specific folder
    socket.emit('get_audio_files', {
        folder: 'alerts/follows',
        request_id: 'audio_request_1'
    });
    
    socket.on('audio_files_response', function(data) {
        if (data.request_id === 'audio_request_1' && data.success) {
            console.log('Available audio files:', data.files);
            // data.files contains array of filenames
        }
    });

**Playing Audio:**

.. code-block:: javascript

    function playAlertSound(audioPath, volume = 1.0) {
        const audio = new Audio(`/assets/${audioPath}`);
        audio.volume = volume;
        audio.play().catch(e => console.error('Audio play error:', e));
    }

Asset Management
~~~~~~~~~~~~~~~~

**Loading Images:**

.. code-block:: javascript

    function loadAlertGif(gifPath) {
        const img = document.createElement('img');
        img.src = `/assets/${gifPath}`;
        img.onload = function() {
            // GIF loaded successfully
            document.body.appendChild(img);
        };
        img.onerror = function() {
            console.error('Failed to load GIF:', gifPath);
        };
    }

**Asset URLs:**

* **Template-specific assets:** ``/assets/template_name/file.ext``
* **Shared assets:** ``/assets/default_assets/file.ext``
* **Fonts:** ``/assets/default_assets/fonts/font.ttf``
* **Sounds:** ``/assets/sounds/sound.mp3``

Template Examples
-----------------

Donation Goal Tracker
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: html

    <!DOCTYPE html>
    <html>
    <head>
        <title>Donation Goal</title>
        <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
        <style>
            .goal-container {
                width: 400px;
                background: rgba(0,0,0,0.8);
                border-radius: 10px;
                padding: 20px;
                color: white;
                font-family: Arial, sans-serif;
            }
            .progress-bar {
                width: 100%;
                height: 30px;
                background: #333;
                border-radius: 15px;
                overflow: hidden;
                margin: 10px 0;
            }
            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #4CAF50, #45a049);
                transition: width 1s ease;
                border-radius: 15px;
            }
        </style>
    </head>
    <body>
        <div class="goal-container">
            <h2 id="goalTitle">Donation Goal</h2>
            <div class="progress-bar">
                <div id="progressFill" class="progress-fill" style="width: 0%"></div>
            </div>
            <p id="goalText">$0 / $500</p>
        </div>

        <script>
            const socket = io();
            let currentAmount = 0;
            const goalAmount = 500;

            // Listen for donation alerts
            socket.on('next_alert', function(data) {
                if (data.alert_type === 'donation') {
                    currentAmount += parseFloat(data.donation_amount);
                    updateGoal();
                }
            });

            function updateGoal() {
                const percentage = Math.min((currentAmount / goalAmount) * 100, 100);
                document.getElementById('progressFill').style.width = percentage + '%';
                document.getElementById('goalText').textContent = 
                    `$${currentAmount.toFixed(2)} / $${goalAmount}`;
                
                if (percentage >= 100) {
                    document.getElementById('goalTitle').textContent = 'Goal Reached! 🎉';
                }
            }

            // Load saved progress
            socket.emit('get_data', {path: 'donation_goal/current_amount'});
            socket.on('get_data', function(response) {
                if (!response.error && response.data) {
                    currentAmount = response.data || 0;
                    updateGoal();
                }
            });
        </script>
    </body>
    </html>

Real-time Follower Count
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: html

    <!DOCTYPE html>
    <html>
    <head>
        <title>Follower Count</title>
        <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
        <style>
            .counter {
                font-size: 48px;
                font-weight: bold;
                color: #9146ff;
                text-align: center;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                font-family: 'Arial Black', sans-serif;
            }
        </style>
    </head>
    <body>
        <div class="counter" id="followerCount">0 Followers</div>

        <script>
            const socket = io();
            let followerCount = 0;

            // Update count when someone follows
            socket.on('next_alert', function(data) {
                if (data.alert_type === 'follow') {
                    followerCount++;
                    updateDisplay();
                    
                    // Animate the update
                    document.getElementById('followerCount').style.transform = 'scale(1.2)';
                    setTimeout(() => {
                        document.getElementById('followerCount').style.transform = 'scale(1)';
                    }, 300);
                }
            });

            function updateDisplay() {
                document.getElementById('followerCount').textContent = 
                    `${followerCount} Followers`;
            }

            // Get initial follower count via Twitch API
            socket.emit('twitch-api-request', {
                endpoint: 'https://api.twitch.tv/helix/channels/followers',
                requestId: 'follower_count'
            });

            socket.on('twitch-api-response', function(data) {
                if (data.requestId === 'follower_count' && data.success) {
                    followerCount = data.data.total || 0;
                    updateDisplay();
                }
            });
        </script>
    </body>
    </html>

Best Practices
---------------

Template Development
~~~~~~~~~~~~~~~~~~~~

1. **Always include Socket.IO:** ``<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>``
2. **Handle connection events:** Listen for connect/disconnect events
3. **Use transparent backgrounds:** For overlay compatibility
4. **Implement error handling:** Gracefully handle missing data
5. **Test with different data:** Ensure your template works with various alert types
6. **Optimize performance:** Avoid heavy animations that could impact stream performance

Configuration Management
~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Use descriptive labels:** Make configuration options clear for users
2. **Provide sensible defaults:** Templates should work out of the box
3. **Include descriptions:** Help users understand what each setting does
4. **Group related settings:** Use logical organization in your config
5. **Validate input ranges:** Set appropriate min/max values for numbers

Asset Organization
~~~~~~~~~~~~~~~~~~

1. **Create template-specific folders:** Keep assets organized by template
2. **Use appropriate file formats:** Optimize images and audio for web use
3. **Provide fallbacks:** Handle missing assets gracefully
4. **Consider file sizes:** Large assets can impact loading performance
5. **Use relative paths:** Always reference assets via ``/assets/`` URLs

Error Handling
~~~~~~~~~~~~~~

.. code-block:: javascript

    // Always handle WebSocket errors
    socket.on('connect_error', function(error) {
        console.error('Connection error:', error);
        // Show offline state or retry logic
    });
    
    // Handle missing data gracefully
    socket.on('next_alert', function(data) {
        try {
            if (!data || !data.alert_type) {
                console.warn('Invalid alert data received');
                return;
            }
            
            // Process alert...
        } catch (error) {
            console.error('Error processing alert:', error);
        }
    });

Debugging Templates
~~~~~~~~~~~~~~~~~~~

1. **Use browser console:** Check for JavaScript errors
2. **Test WebSocket connection:** Verify events are being received
3. **Validate JSON configs:** Ensure configuration files are valid
4. **Check asset paths:** Verify all assets load correctly
5. **Monitor network requests:** Use browser dev tools to debug loading issues

Template Testing
~~~~~~~~~~~~~~~~~

1. **Test with real data:** Use actual alerts to verify functionality
2. **Test edge cases:** Handle missing or unusual data
3. **Test different screen sizes:** Ensure compatibility with various resolutions
4. **Test performance:** Monitor CPU/memory usage during heavy activity
5. **Test in OBS:** Verify the template works correctly as a browser source 