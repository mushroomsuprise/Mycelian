Update stat tracking for timestamped tracking
    [x] - Add per user timestamped bit/sub/giftsub/donation/point alert tracking **Note: Store this in a separate local database handled solely by the statistics manager, not the database manager**
    [x] - Keep current stat tracking as "lifetime totals" **Note: Move this to the separate local database**
    [x] - Add an exporting feature to the statistics tab to export an image within a date selectable range as a stat highlights thing **Note: make this flashy, bright, and vibrant, in a well formatted layout**
Default resub alert
    [x] - Create a new "Default" alert that will play if a resub alert does not meet match any currently created sub alerts. **Note: this should be for resub type alerts only**
    [x] - Default alert should be configurable just like every other alert
Create theme system for user customizable themes
    [x] - Store themes in json files
    [x] - Theme creator in the Settings tab
    [x] - Themes apply without app restarts
Connectors
    [x] - Check channel points not triggering connectors
Source Settings UI
    [x] - Make each category collapsable
    [x] - Roulette option categories show the name in the category title. Ex: "Option 1 (Rain World)"
Title Template
    [x] - Fix automatic category switching (refresh does not always work, and the eventsub callback seems to not work at all)
TTS
    [x] - Add text to speech functionality (must play through alerts.html template)
    [x] - Create settings to toggle TTS on and off
    [x] - Add in a setting to beep out bad words
Giveaway System
    [x] - Create a template to work in conjunction with the chatbot module
    [x] - Add a settings to the Chatbot settings to configure the giveaway system
        Options to configure:
        [x] - No duplicate entries
        [x] - Remove winners from pool
        [x] - Number of winners to pick
        [x] - Keyword to enter the giveaway
        [x] - Disable certain users (Mods, VIPs, specific usernames)
    [x] - Send an announcement message to the chat when a winner is picked
    [x] - Add statistics tracking for giveaway entries and winners
Database selection issues
    [x] - Fix issue with database (and all related settings) not being saved when changing database type
    [x] - Fix issue with Firebase url not being recognized
    [x] - Implement database migration between different database types

[] - Fix auto-update not notifying user of update upon startup.

FF7 HTML Template
    [] - Create HTML template that will hook onto the FF7 exe process to get data from the game.
        [] - The template must be able to reliably hook onto the game process, rehook if the game has been closed and reopened, and fetch game data every .5 seconds.
    [] - Create configurable elements for the template that display the following:
        [] - Enemies - HP, Name, Level, and a progress bar for their ATB gauge.
        [] - Party Members - HP, Name, Level, Limit Bar, and a progress bar for their ATB gauge.
        [] - Current play time
        [] - Current gil
        [] - Bosses defeated (toggleable as a list of boss names, most recently killed boss name, or a total count) <-- This element must be resettable to nothing by clicking a button, or upon app restart.
    [] - Template should be stylized to match FF7, by using its font, and setting up progress bars to look the same.
