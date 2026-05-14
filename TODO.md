Game Hooks
    [] - Rewrite game hook so it works as a generic manager. any hooks are defined within the specific game's hook file.
    [] - Game hook manager should be platform agnostic, and select the appropriate path to modify memory based on the detected OS. specific game hook files should define the platforms it will work on within them. The settings UI should look to this when setting up the platform icons and the toggle.
    [] - The game hook manager should check the process list for any games that fall under enabled game hooks. If a game is found, it should spawn a new thread dedicated to that specific game's hook.
    [] - Relocate ff7 hook components into the ff7_hook.py file. The main game hook file should not contain game specific items.

FF7 Hook
    [] - Rewrite to match newly reworked game hook manager.

Connectors:
    [] - Disable output of connectors when a "game hook" is the action, and that particular hook is either not enabled, or not connected to the game

Spore Studio:
    [] - Add ability to define streamdeck options in templates (these get defined in the main JSON config file for the template)
    [] - Add option to set the definable streamdeck options as bindings to elements

Template Previewer:
    [] - Add settings menu with persistant storage of options to enable/disable sounds, etc
    [] - Change mock data to be clickable buttons above the preview frame like the Spore Studio module uses. Mock data will no longer go to specialized websockets in the templates, but will use the actual websocket that live data uses
    [] - When data is changed, it currently refreshes the entire previewer, i would prefer it does not refresh the entire thing, just modify what changed.

Chat Improvements:
    [] - Add more entry animations, including complex ones
    [] - Change font boxes so they "autocomplete" from available fonts in the /assets/default_assets/fonts folder
    [] - Fix issue with emote container size not matching the scaled emote size
    [] - Emote padding setting not functional
    [] - Gigantified emotes not scaling to the correct size based on the JSON settings file
    [] - Create a .spore.json sidecar file for the Spore Studio module

Alerts Improvements:
    [] - Add emote parsing to alert messages (see chat template for the emote parser). Note: Alert messages have different payloads for emotes than chat messages for Twitch emotes specifically, 3rd party emotes can be parsed the same way.
    [] - Create a .spore.json sidecar file for the Spore Studio module

Other Standard Templates:
    [] - Create a .spore.json sidecar file for the Spore Studio module for bitbar.html, bitboss.html, counter.html, ff7.html, giveaway.html, pausedalerts.html, roulette.html, subbar.html, and title.html

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
    [] - Add the game category to Raid activity feed items. It should say something like: {user} has raided with {count} viewers, they were last playing {category}.
    [] - .wav audio files are not working (at least in the alerts template)
    [] - Stream streaks are not appearing in the activity feed, it seems like the data is not coming in at all
    [] - Stream streaks missing from the HTML template (OBS dock) but are in the python file

Priority Fixes:
    [] - Setup "generic image/video" template, so the file can just be renamed and all websocket events will point to the file name.
