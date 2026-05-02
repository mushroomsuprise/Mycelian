Game Hooks
    [] - Rewrite game hook so it works as a generic manager. any hooks are defined within the specific game's hook file.
    [] - Game hook manager should be platform agnostic, and select the appropriate path to modify memory based on the detected OS. specific game hook files should define the platforms it will work on within them. The settings UI should look to this when setting up the platform icons and the toggle.
    [] - The game hook manager should check the process list for any games that fall under enabled game hooks. If a game is found, it should spawn a new thread dedicated to that specific game's hook.
    [] - Relocate ff7 hook components into the ff7_hook.py file. The main game hook file should not contain game specific items.

FF7 Hook
    [] - Rewrite to match newly reworked game hook manager.

Notification Upgrades:
    [] - Create a "notification engine" in a new python file, convert all notification displays to run through this instead
    [] - Add a "notification center" to all tabs of the app (locate button in the top right corner), list of notifcations should be scrollable with the notifications appearing as small cards with "X" buttons to clear, and have clickable actions based on the notification.
    [] - Add notification history to the notification center, stores notifications until cleared by user
    [] - When notifications are clicked on, it should either copy the message, move to the corresponding area in the UI, or do nothing. This is dependant on the type of notification, multiple actions of this might be applicable.
    [] - Move notifications from the bottom center to the top right corner
    [] - Ensure all notifications only display once, some still infinitely trigger until the app is closed
    [] - Add game hook notifications if streaming in a category that contains a hook (frame it like: "Playing {game} on PC? Try the game hook out!")
    [] - Stylize notification colors to match the theme, but still have differentiated colors for errors, successes, and general info. Note: we will need to add color fields to the themes for this, and ensure the mock-ui in settings, and the create theme dialog contains these

Template Previewer:
    [] - Create a template preview inside the Source Settings menu (on the right hand side), that will display a live preview of the template, and show any changes (both saved and pending).
    [] - Templates that auto-hide (spotify, PSN trophies to name a few) should always display in the preview. Other templates that appear and disappear (things like alerts, chat) should mock the behavior of the template while its in use (IE play alerts, add chat messages, etc)

OBS Websocket:
    [] - Add in OBS websocket support. 
    [] - Create settings tab for OBS to input the websocket connection information (Must persist across restarts, so store in database)
    [] - Add in Connector actions for OBS controls. For example: change scene, enable/disable source, source transformations, mute/unmute audio devices, start/stop recording, start/stop streaming. Each of these will need to have appropriate args to them and should be comboboxes that autopopulate from OBS data when available (IE: scene names, source names, streaming/recording status)
    [] - Add OBS items as Connector Triggers. These should be things like: changing scene, streaming/recording status, audio device mute/unmute, etc. These triggers should have appropriate conditionals (IE: scene name, source name, etc etc)

General Items:
    [] - Update the create theme dialog to show a mock-ui similar to the mock ui in the theme tab. It should live update as the user changes the different color boxes. Do a general overhaul of the create theme dialog to make better use of space.
    [] - Debug recently added Stream Streaks, they do not appear in chat events, or activity feed, so likely an issue getting the data from Twitch
    [] - Expose Connectors system to the Streamdeck plugin. Make it so created Connectors can be assigned to Streamdeck buttons, and trigger actions when the button is pressed. We should create a trigger called "Streamdeck" that can be set inside Connectors. This trigger should add the Connector to the Streamdeck plugin dynamically. The Streamdeck plugin will need a new option called "Connector" that will have a dropdown list of all Connectors using the "Streamdeck" trigger. The streamdeck button should have a label that displays the Connector name. We will need to recompile the Streamdeck plugin to add this functionality, and place the compiled plugin in the same directory as the Mycelian desktop application for it to be installable via the app.
    [] - Ensure statistics database "lazy loads"

Priority Fixes:
    [] - Setup "generic image/video" template, so the file can just be renamed and all websocket events will point to the file name.
    [] - Inspect Spotify OAuth flow. Tokens are not being refreshed upon app startup properly
