Custom Variables Dialog
=======================

The Custom Variables Dialog is Mycelian's advanced expression builder that allows you to create reusable, dynamic variables using complex mathematical calculations, comparisons, date manipulations, and chained expressions. This powerful feature transforms simple variables into sophisticated data processors that can adapt to your stream's real-time data.

Overview
--------

Custom variables extend Mycelian's variable system by allowing you to create named expressions that combine multiple data sources, perform calculations, and apply conditional logic. Unlike basic variables that just display data, custom variables can transform and analyze your stream statistics, user data, and external API responses.

**Key Benefits:**
  * **Data Transformation:** Convert raw data into meaningful metrics (e.g., user levels, engagement rates)
  * **Complex Calculations:** Perform mathematical operations across multiple variables
  * **Conditional Logic:** Create variables that change based on comparisons and conditions
  * **Reusable Components:** Build once, use everywhere in commands and events
  * **Real-time Processing:** Variables update automatically as stream data changes

**Use Cases:**
  * Calculate user levels based on bits/cheers
  * Create engagement scores combining multiple metrics
  * Generate dynamic pricing or reward systems
  * Build complex conditional responses
  * Transform API data into readable formats

Accessing the Dialog
--------------------

The Custom Variables Dialog is accessible from the Chatbot tab in Mycelian's main interface.

**How to Open:**
  1. Launch Mycelian desktop application
  2. Navigate to the **Chatbot** tab
  3. Click the **"Custom Variables"** button (purple/violet colored)
  4. The dialog will open as a large modal window


Dialog Components
-----------------

Left Column - Variable Creation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Variable Name Field:**
  * Enter a descriptive name for your custom variable
  * Names are automatically prefixed with "custom_" (e.g., "user_level" becomes "custom_user_level")
  * Use lowercase letters, numbers, and underscores only
  * Keep names descriptive and memorable

**Expression Field:**
  * The main input area for building your variable expression
  * Supports multi-line expressions for complex logic
  * Click variables from the right column to insert them
  * Click expression templates from the far right to insert function calls

**Examples Section:**
  * Expandable help area showing common expression patterns
  * Includes practical examples with explanations
  * Scrollable for easy reference while building expressions

**Save/Cancel Buttons:**
  * **Save:** Validates expression and creates/updates the variable
  * **Cancel:** Closes dialog without saving changes
  * Validation prevents saving expressions with non-existent variables

Middle Column - Available Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The variables panel displays all available data sources organized by category. Each category can be collapsed/expanded for easy navigation.

**Basic Variables (Always Available):**
  * ``{channel_name}`` - Your Twitch channel name
  * ``{stream_title}`` - Current stream title
  * ``{game_name}`` - Currently playing game
  * ``{viewer_count}`` - Current viewer count
  * ``{uptime}`` - Stream uptime in HH:MM:SS format
  * ``{follower_count}`` - Total channel followers
  * ``{subscriber_count}`` - Total subscribers

**Stats Variables (Mycelian Statistics):**
  * ``{stats.alerts.bit_alerts_played}`` - Total bit alerts shown
  * ``{stats.alerts.follow_alerts_played}`` - Total follow alerts shown
  * ``{stats.alerts.resubs_played}`` - Total resub alerts shown
  * ``{stats.alerts.subs_played}`` - Total new sub alerts shown
  * ``{stats.chatbot.commands_triggered}`` - Total commands used
  * ``{stats.chatbot.events_triggered}`` - Total events triggered
  * ``{stats.stream.total_view_time}`` - Total viewer minutes
  * ``{stats.stream.average_viewers}`` - Average concurrent viewers
  * ``{stats.stream.peak_viewers}`` - Peak concurrent viewers
  * ``{stats.stream.total_streams}`` - Total number of streams
  * ``{stats.stream.total_stream_time}`` - Total streaming time

**YouTube Variables (If Configured):**
  * ``{youtube.latest_video_title}`` - Latest video title across all channels
  * ``{youtube.latest_video_url}`` - Latest video URL
  * ``{youtube.latest_video_channel}`` - Channel that uploaded latest video
  * ``{ChannelName_latest_video_title}`` - Latest video from specific channel
  * ``{ChannelName_latest_video_url}`` - URL for specific channel's latest video

**Command/Event Variables (Context-Dependent):**
  * ``{username}`` - User who triggered the command/event
  * ``{amount}`` - Bits/donation amount
  * ``{message}`` - User's message text
  * ``{tier}`` - Subscription tier (1, 2, 3)
  * ``{tier_name}`` - Subscription tier name ("Tier 1", "Tier 2", etc.)
  * ``{months}`` - Total months subscribed
  * ``{recipient_name}`` - Gift sub recipient
  * ``{total_gifts}`` - Total gifts given by user
  * ``{viewer_count}`` - Raid viewer count
  * ``{level}`` - Hype train level
  * ``{progress}`` - Hype train progress
  * ``{goal}`` - Hype train goal

**Time Variables:**
  * ``{time}`` - Current time in default format
  * ``{time:EST:12:show}`` - EST time, 12-hour with AM/PM
  * ``{time:UTC:24}`` - UTC time, 24-hour format
  * ``{time:PST:12:hide}`` - PST time, 12-hour without AM/PM

**Custom Variables (Your Created Variables):**
  * ``{custom_user_level}`` - Variables you've already created
  * ``{custom_engagement_score}`` - Reusable in other expressions

Right Column - Expressions & Modifiers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The expressions panel provides categorized function templates that insert blank expressions for you to customize.

**Time Formatting Functions:**
  * ``format_time(timezone, format, ampm)`` - Advanced time formatting
  * ``date_to_age(date_string)`` - Convert ISO date to age
  * ``date_to_age(date_string).years`` - Extract just years
  * ``date_to_age(date_string).days`` - Extract remaining days

**Math Operations:**
  * ``math(value1, operator, value2)`` - Basic arithmetic
  * ``math(expression)`` - Complex mathematical expressions
  * ``math({stats.bits} / 10)`` - Division with variables
  * ``math({custom_base_level} + {stats.alerts.played})`` - Combined variables

**Comparison Functions:**
  * ``compare(value1, operator, value2)`` - Boolean comparisons
  * ``compare({stats.commands}, >, 100)`` - Greater than comparison
  * ``compare({viewer_count}, >=, 50)`` - Greater than or equal
  * ``compare({tier}, ==, 2)`` - Equality comparison

**Date Functions:**
  * ``date_to_age(date_string)`` - Full age calculation
  * ``date_to_age({data.created_at})`` - Account age from API data

Creating Custom Variables
-------------------------

Basic Variable Creation
~~~~~~~~~~~~~~~~~~~~~~~~

**Simple Math Example:**
Let's create a variable that calculates a user's level based on bits cheered.

1. **Open the Custom Variables Dialog**
2. **Enter Variable Name:** ``user_level``
3. **Build Expression:** Click ``math(``, then ``{stats.alerts.bit_alerts_played}``, then ``,/,``, then type ``10``, then ``)``
4. **Final Expression:** ``math({stats.alerts.bit_alerts_played}, /, 10)``
5. **Click Save**

The variable ``{custom_user_level}`` will now display the user's level (bits ÷ 10).

**Simple Comparison Example:**
Create a variable that shows if a stream is "popular".

1. **Variable Name:** ``popular_stream``
2. **Expression:** ``compare({viewer_count}, >, 100)``
3. **Result:** ``True`` when viewers > 100, ``False`` otherwise

Advanced Expressions
~~~~~~~~~~~~~~~~~~~~

**Complex Math with Multiple Operations:**
Calculate an engagement score combining multiple metrics.

* **Variable Name:** ``engagement_score``
* **Expression:** ``math(({stats.chatbot.commands_triggered} + {stats.alerts.bit_alerts_played} + {stats.alerts.follow_alerts_played}) / 10)``

**Result:** A single number representing overall engagement.

**Conditional Logic with Comparisons:**
Create a VIP status indicator.

* **Variable Name:** ``vip_status``
* **Expression:** ``compare({stats.alerts.bit_alerts_played}, >, 1000)``

**Result:** ``True`` for users who cheered over 1000 bits total.

**Date-Based Calculations:**
Calculate account age in years.

* **Variable Name:** ``account_age_years``
* **Expression:** ``date_to_age({data.created_at}).years``

**Result:** Shows how many years ago the account was created.

Chaining Expressions Together
-----------------------------

One of the most powerful features is combining custom variables in complex expressions.

**Multi-Step Level Calculation:**
Create variables that build upon each other.

1. **Base Level Variable:**

   * **Name:** ``base_level``
   * **Expression:** ``math({stats.alerts.bit_alerts_played}, /, 100)``

2. **Bonus Level Variable:**

   * **Name:** ``bonus_level``
   * **Expression:** ``math({stats.chatbot.commands_triggered}, /, 50)``

3. **Total Level Variable:**

   * **Name:** ``total_level``
   * **Expression:** ``math({custom_base_level}, +, {custom_bonus_level})``

**Result:** A comprehensive level system that combines different engagement types.

**Engagement Tiers:**
Create tiered status based on multiple criteria.

1. **Engagement Score:**

   * **Name:** ``engagement_score``
   * **Expression:** ``math({stats.alerts.bit_alerts_played} + {stats.chatbot.commands_triggered} * 2)``

2. **Tier Determination:**

   * **Name:** ``vip_tier``
   * **Expression:** ``compare({custom_engagement_score}, >, 500)``

   * **Name:** ``premium_tier``
   * **Expression:** ``compare({custom_engagement_score}, >, 1000)``

**Nested Comparisons:**
Create complex conditional logic.

* **Variable Name:** ``stream_status``
* **Expression:** ``compare(compare({viewer_count}, >, 50), and, compare({uptime}, >, 3600))``

**Result:** ``True`` only when both conditions are met (50+ viewers AND 1+ hour stream time).

Practical Examples
------------------

User Level System
~~~~~~~~~~~~~~~~~

**Scenario:** Create a leveling system where users gain levels based on their engagement.

**Variables to Create:**

1. **Bits Level:**

   * **Name:** ``bits_level``
   * **Expression:** ``math({stats.alerts.bit_alerts_played}, /, 50)``
   * **Usage:** "You are level {custom_bits_level} from bits!"

2. **Command Level:**

   * **Name:** ``command_level``
   * **Expression:** ``math({stats.chatbot.commands_triggered}, /, 25)``

3. **Total Level:**

   * **Name:** ``total_level``
   * **Expression:** ``math({custom_bits_level}, +, {custom_command_level})``

4. **Level Title:**

   * **Name:** ``level_title``
   * **Expression:** ``compare({custom_total_level}, >, 10) ? "Expert" : "Beginner"``

**Command Usage:**
    ``!mylevel → "You are a level {custom_total_level} {custom_level_title}!"``

Engagement Analytics
~~~~~~~~~~~~~~~~~~~~

**Scenario:** Track and display stream engagement metrics.

**Variables to Create:**

1. **Engagement Rate:**

   * **Name:** ``engagement_rate``
   * **Expression:** ``math({stats.chatbot.commands_triggered} / {stats.stream.total_view_time} * 100)``

2. **Bits per Viewer:**

   * **Name:** ``bits_per_viewer``
   * **Expression:** ``math({stats.alerts.bit_alerts_played} / {viewer_count})``

3. **Peak Performance:**

   * **Name:** ``peak_performance``
   * **Expression:** ``compare({viewer_count}, ==, {stats.stream.peak_viewers})``

**Command Usage:**
    ``!stats → "Engagement: {custom_engagement_rate}%, Bits/viewer: ${custom_bits_per_viewer}, At peak: {custom_peak_performance}"``

Dynamic Pricing System
~~~~~~~~~~~~~~~~~~~~~~

**Scenario:** Create donation goals that scale with viewer count.

**Variables to Create:**

1. **Base Goal:**

   * **Name:** ``base_goal``
   * **Expression:** ``math({viewer_count}, *, 0.5)``

2. **Premium Goal:**

   * **Name:** ``premium_goal``
   * **Expression:** ``math({custom_base_goal}, *, 2)``

3. **Goal Progress:**

   * **Name:** ``goal_progress``
   * **Expression:** ``math({stats.alerts.bit_alerts_played} / {custom_premium_goal} * 100)``

**Command Usage:**
    ``!goals → "Base goal: ${custom_base_goal}, Premium: ${custom_premium_goal}, Progress: {custom_goal_progress}%"``

Account Age Display
~~~~~~~~~~~~~~~~~~~

**Scenario:** Show user account age in a friendly format.

**Variables to Create:**

1. **Account Years:**

   * **Name:** ``account_years``
   * **Expression:** ``date_to_age({data.created_at}).years``

2. **Account Days:**

   * **Name:** ``account_days``
   * **Expression:** ``date_to_age({data.created_at}).days``

3. **Veteran Status:**

   * **Name:** ``veteran_user``
   * **Expression:** ``compare({custom_account_years}, >=, 1)``

**Command Usage:**
    ``!accountage → "Your account is {custom_account_years} years and {custom_account_days} days old! Veteran: {custom_veteran_user}"``

Stream Status Indicators
~~~~~~~~~~~~~~~~~~~~~~~~

**Scenario:** Create variables that indicate various stream states.

**Variables to Create:**

1. **Long Stream:**

   * **Name:** ``long_stream``
   * **Expression:** ``compare({uptime_seconds}, >, 7200)``  # 2+ hours

2. **Popular Stream:**

   * **Name:** ``popular_stream``
   * **Expression:** ``compare({viewer_count}, >, 100)``

3. **High Engagement:**

   * **Name:** ``high_engagement``
   * **Expression:** ``compare({stats.chatbot.commands_triggered}, >, 50)``

4. **Stream Health Score:**

   * **Name:** ``stream_health``
   * **Expression:** ``math(({viewer_count} + {stats.chatbot.commands_triggered} + {stats.alerts.bit_alerts_played}) / 10)``

**Command Usage:**
    ``!streamstatus → "Long stream: {custom_long_stream}, Popular: {custom_popular_stream}, High engagement: {custom_high_engagement}, Health: {custom_stream_health}/10"``

API Data Processing
~~~~~~~~~~~~~~~~~~~

**Scenario:** Process external API data into readable formats.

**Variables to Create:**

1. **Weather Temp (Celsius):**

   * **Name:** ``weather_temp_c``
   * **Expression:** ``{api_response.current.temp_c}``

2. **Weather Temp (Fahrenheit):**

   * **Name:** ``weather_temp_f``
   * **Expression:** ``math({custom_weather_temp_c} * 9 / 5 + 32)``

3. **Weather Status:**

   * **Name:** ``weather_status``
   * **Expression:** ``{api_response.current.condition.text}``

4. **Comfortable Weather:**

   * **Name:** ``comfortable_weather``
   * **Expression:** ``compare({custom_weather_temp_c}, >=, 15)``

**Command Usage:**
    ``!weather → "Current temp: {custom_weather_temp_c}°C ({custom_weather_temp_f}°F), {custom_weather_status}, Comfortable: {custom_comfortable_weather}"``

Expression Syntax Reference
---------------------------

Math Function Syntax
~~~~~~~~~~~~~~~~~~~~~

**Basic Arithmetic:**

    ``math(value1, operator, value2)`` ``math(expression)``

**Supported Operators:**
  * ``+`` - Addition
  * ``-`` - Subtraction
  * ``*`` - Multiplication
  * ``/`` - Division
  * ``%`` - Modulo (remainder)
  * ``**`` - Exponentiation

**Complex Expressions:**
    ``math(expression)``

**Examples:**
    ``math(10, +, 5)``                    # 15
    ``math({viewer_count}, *, 2)``        # Double viewer count
    ``math({stats.bits} / 100)``          # Bits as percentage
    ``math(({a} + {b}) / 2)``             # Average of two values

Comparison Function Syntax
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Basic Comparison:**
    ``compare(value1, operator, value2)``

**Supported Operators:**
  * ``==`` - Equal to
  * ``!=`` - Not equal to
  * ``>`` - Greater than
  * ``<`` - Less than
  * ``>=`` - Greater than or equal to
  * ``<=`` - Less than or equal to

**Examples:**

    ``compare(10, >, 5)``                           # True
    ``compare({viewer_count}, >=, 50)``             # True if 50+ viewers
    ``compare({tier}, ==, 2)``                      # True for Tier 2 subs
    ``compare({stats.commands}, !=, 0)``            # True if commands used

Date Function Syntax
~~~~~~~~~~~~~~~~~~~~

**Age Calculation:**
    ``date_to_age(date_string)``

**Returns object with:**
  * ``.years`` - Years component
  * ``.days`` - Remaining days
  * ``.total_days`` - Total days

**Examples:**
    ``date_to_age("2020-01-01T00:00:00Z").years``      # Years since 2020
    ``date_to_age({data.created_at}).days``            # Days since account creation
    ``date_to_age({data.created_at}).total_days``      # Total account age in days

Time Formatting Syntax
~~~~~~~~~~~~~~~~~~~~~~

**Advanced Time Formatting:**
    ``format_time(timezone, format, ampm)``

**Timezone Options:**
  * ``EST``, ``PST``, ``UTC``, ``GMT``, etc.

**Format Options:**
  * ``12`` - 12-hour format
  * ``24`` - 24-hour format

**AM/PM Options:**
  * ``show`` - Display AM/PM
  * ``hide`` - Hide AM/PM

**Examples:**
    ``format_time(EST, 12, show)``      # "02:30 PM EST"
    ``format_time(UTC, 24)``           # "19:30 UTC"
    ``format_time(PST, 12, hide)``     # "11:30 PST"

Variable Nesting and Chaining
------------------------------

Advanced Techniques
~~~~~~~~~~~~~~~~~~~

**Variable References:**
Custom variables can reference other custom variables, creating complex dependency chains.

**Example Chain:**
    ``custom_base_score = math({stats.bits} + {stats.commands})``
    ``custom_multiplier = compare({viewer_count}, >, 50) ? 2 : 1``
    ``custom_final_score = math({custom_base_score} * {custom_multiplier})``

**Circular Reference Prevention:**
The system automatically prevents circular references (A references B which references A).

**Context Awareness:**
Variables are evaluated with full context, so command-specific variables work when used in commands.

Best Practices
--------------

Variable Naming
~~~~~~~~~~~~~~~~

**Clear Naming Conventions:**
  * Use descriptive names: ``user_level`` not ``ul``
  * Include units in name: ``viewer_count_percentage`` not ``viewer_pct``
  * Use consistent prefixes: ``engagement_``, ``level_``, ``status_``
  * Avoid reserved words: Don't use ``math``, ``compare``, etc.

**Organizational Grouping:**
    ``# Good grouping:``
    ``custom_user_bits_level``
    ``custom_user_commands_level``
    ``custom_user_total_level``

    ``# Avoid mixing concepts:``
    ``custom_bits_and_weather``  # Too many purposes

Expression Complexity
~~~~~~~~~~~~~~~~~~~~~

**Start Simple:**
Begin with basic expressions and gradually add complexity.

**Test Incrementally:**
    ``# Step 1: Basic calculation``
    ``custom_bits_dollars = math({stats.alerts.bit_alerts_played} / 100)``

    ``# Step 2: Add comparison``
    ``custom_high_roller = compare({custom_bits_dollars}, >, 10)``

    ``# Step 3: Combine in final expression``
    ``custom_status = {custom_high_roller} ? "High Roller" : "Regular"``

**Performance Considerations:**
  * Avoid extremely complex nested expressions
  * Consider breaking very complex logic into multiple variables
  * Test expressions with realistic data values

Error Handling
~~~~~~~~~~~~~~

**Validation:**
The dialog automatically validates expressions before saving:
  * Checks for non-existent variables
  * Verifies syntax correctness
  * Prevents circular references

**Safe Evaluation:**
  * Math expressions are evaluated in restricted environments
  * Invalid operations return safe defaults (0 for math, False for comparisons)
  * Error messages guide you to correct issues

**Testing:**
Always test your variables with real data before using them in live commands.

Common Patterns
~~~~~~~~~~~~~~~

**Percentage Calculations:**
    ``custom_completion_pct = math({current_value} / {target_value} * 100)``

**Tier Systems:**
    ``custom_tier_1 = compare({score}, <, 100)``
    ``custom_tier_2 = compare({score}, >=, 100)``
    ``custom_tier_3 = compare({score}, >=, 500)``

**Time-Based Logic:**
    ``custom_prime_time = compare({time_hour}, >=, 19) and compare({time_hour}, <=, 22)``

**Conditional Messages:**
    ``custom_greeting = {custom_veteran_user} ? "Welcome back, veteran!" : "Welcome, new friend!"``

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Variable Not Found Error:**
  * Check spelling of variable names
  * Ensure variables exist and are enabled
  * Verify category selection in variables panel

**Expression Syntax Error:**
  * Check parentheses matching: ``math(``, ``)``
  * Verify comma placement in functions
  * Ensure operators are valid (``+``, ``-``, ``*``, ``/``, etc.)

**Unexpected Results:**
  * Test expressions with specific values
  * Check variable data types (numbers vs strings)
  * Verify context availability (command variables in commands)

**Performance Issues:**
  * Simplify complex expressions
  * Break large expressions into smaller variables
  * Avoid unnecessary calculations

Debug Techniques
~~~~~~~~~~~~~~~~

**Testing Expressions:**
  1. Create test command with your variable
  2. Trigger command with known data
  3. Verify output matches expectations
  4. Adjust expression as needed

**Variable Inspection:**
  * Use simple commands to display individual variables
  * Compare expected vs actual values
  * Test with edge cases (0, negative numbers, large values)

**Expression Debugging:**
    ``# Test individual components:``
    ``!test1 → "Bits: {stats.alerts.bit_alerts_played}"``
    ``!test2 → "Math result: math({stats.alerts.bit_alerts_played}, /, 10)"``

    ``# Then combine:``
    ``!test3 → "Level: {custom_user_level}"``

**Log Analysis:**
Check Mycelian logs for expression evaluation errors and warnings.

Advanced Features
-----------------

Expression Templates
~~~~~~~~~~~~~~~~~~~~

**Pre-built Templates:**
The expressions panel provides clickable templates that insert blank expressions:

    ``Click "math(a,+,b)" → Inserts: math(,+,)``
    ``Click "compare(a,>,b)" → Inserts: compare(,>,)``

**Customization:**
Fill in the placeholders with your variables and values.

Variable Categories
~~~~~~~~~~~~~~~~~~~

**Understanding Categories:**
Different variable categories are available based on context:

* **Basic:** Always available, core stream data
* **Stats:** Mycelian's collected statistics
* **YouTube:** Requires YouTube integration setup
* **Command/Event:** Only available in command/event context
* **Custom:** Variables you've created

**Category Restrictions:**
Some variables only work in specific contexts:
  * Command variables (``{username}``, ``{amount}``) only work in commands/events
  * Stats variables require Mycelian to have collected data
  * API variables require API integration configuration

API Integration
~~~~~~~~~~~~~~~

**External Data Processing:**
Custom variables can process API response data:

    ``# If API returns: {"current": {"temp_c": 20, "condition": {"text": "Sunny"}}}``
    ``custom_temperature = {api_response.current.temp_c}``
    ``custom_weather = {api_response.current.condition.text}``

**Complex API Processing:**
    ``custom_weather_score = compare({api_response.current.temp_c}, >, 20) ? "Perfect" : "Too Cold"``

Real-time Updates
~~~~~~~~~~~~~~~~~

**Dynamic Variables:**
Custom variables update automatically as stream data changes:

* **Live Statistics:** Variables recalculate when stats change
* **Viewer Count:** Updates as viewers join/leave
* **Time Variables:** Update every second
* **API Data:** Refreshes when API calls complete

**Performance Note:**
Complex expressions with many dependencies may have slight calculation delays.

Integration with Commands and Events
-------------------------------------

Using in Commands
~~~~~~~~~~~~~~~~~

**Basic Usage:**
    ``Command: !mylevel``
    ``Response: You are level {custom_user_level}!``

**Conditional Responses:**
    ``Command: !status``
    ``Response: {custom_vip_status} ? "You are a VIP member!" : "Become a VIP by cheering 1000+ bits!"``

**Dynamic Pricing:**
    ``Command: !donate``
    ``Response: Current goal: ${custom_dynamic_goal} bits``

Using in Events
~~~~~~~~~~~~~~~

**Personalized Events:**
    ``Follow Event: Welcome {username}! Your level is {custom_user_level}.``

**Conditional Event Logic:**
    ``Subscription Event: {custom_veteran_user} ? "Welcome back, {username}! Thanks for another {months} months!" : "Welcome {username} to the community!"``

**Dynamic Rewards:**
   ``Bits Event: Thanks for {amount} bits, {username}! Your new level: {custom_user_level}``

Variable Management
-------------------

Editing Variables
~~~~~~~~~~~~~~~~~

**Modifying Existing Variables:**
  1. Open Custom Variables Dialog
  2. The interface shows all existing variables
  3. Click "Edit" button next to any variable
  4. Modify name or expression as needed
  5. Save changes

**Testing Changes:**
Always test modified variables to ensure they work correctly with existing commands/events.

Deleting Variables
~~~~~~~~~~~~~~~~~~

**Safe Deletion:**
  * Check where variables are used before deleting
  * System warns about dependent variables
  * Consider replacement variables for continuity

**Cleanup:**
Regularly review and remove unused variables to keep your setup organized.

Import/Export
~~~~~~~~~~~~~

**Sharing Variables:**
Custom variables can be exported and imported between Mycelian installations.

**Backup Best Practice:**
Export your custom variables regularly as part of your Mycelian backup routine.

Future Enhancements
-------------------

Planned Features
~~~~~~~~~~~~~~~~

**Advanced Functions:**
  * String manipulation functions (uppercase, lowercase, substring)
  * Array/list operations for processing multiple values
  * Regular expression matching and extraction
  * Conditional expressions (if/then/else logic)

**Enhanced UI:**
  * Expression builder with drag-and-drop interface
  * Visual expression trees showing dependencies
  * Real-time expression validation and preview
  * Template library for common patterns

**Integration Improvements:**
  * Direct integration with OBS scenes and sources
  * Enhanced API processing with custom parsers
  * Webhook support for external triggers
  * Database integration for persistent data

Getting Help
------------

Documentation Resources
~~~~~~~~~~~~~~~~~~~~~~~

**Primary Documentation:**
  * This comprehensive Custom Variables guide
  * Main Mycelian documentation
  * Chatbot system documentation

**Community Resources:**
  * Mycelian community forums
  * Discord server for real-time help
  * GitHub issues for bug reports
  * Example configurations repository

**Debug Tools:**
  * Built-in expression testing in the dialog
  * Command testing functionality
  * Application logs for error diagnosis
  * Network debugging for API issues

**Support Guidelines:**
  * Include your Mycelian version when reporting issues
  * Provide example expressions that aren't working
  * Share relevant log entries
  * Describe expected vs actual behavior

Quick Reference
---------------

**Common Expressions:**

.. code-block:: text

    # User Level: Bits divided by 100
    math({stats.alerts.bit_alerts_played}, /, 100)

    # Engagement Score: Commands + Bits + Follows
    math({stats.chatbot.commands_triggered} + {stats.alerts.bit_alerts_played} + {stats.alerts.follow_alerts_played})

    # VIP Status: Over 500 total engagement
    compare({stats.chatbot.commands_triggered} + {stats.alerts.bit_alerts_played}, >, 500)

    # Account Age in Years
    date_to_age({data.created_at}).years

    # Stream Popularity: 50+ viewers
    compare({viewer_count}, >, 50)

**Variable Categories:**
  * ``{basic}`` - Core stream data
  * ``{stats.*}`` - Mycelian statistics
  * ``{youtube.*}`` - YouTube integration
  * ``{custom_*}`` - Your variables
  * ``{time:*}`` - Time formatting
  * ``{command_*}`` - Command context
  * ``{event_*}`` - Event context

**Function Reference:**
  * ``math(a,op,b)`` - Arithmetic operations
  * ``compare(a,op,b)`` - Boolean comparisons
  * ``date_to_age(date)`` - Age calculations
  * ``format_time(tz,fmt,ampm)`` - Time formatting

This comprehensive guide should provide everything you need to master the Custom Variables Dialog. Start with simple expressions and gradually build complexity as you become comfortable with the system.
