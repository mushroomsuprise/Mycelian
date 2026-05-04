Game Hooks
    [] - Rewrite game hook so it works as a generic manager. any hooks are defined within the specific game's hook file.
    [] - Game hook manager should be platform agnostic, and select the appropriate path to modify memory based on the detected OS. specific game hook files should define the platforms it will work on within them. The settings UI should look to this when setting up the platform icons and the toggle.
    [] - The game hook manager should check the process list for any games that fall under enabled game hooks. If a game is found, it should spawn a new thread dedicated to that specific game's hook.
    [] - Relocate ff7 hook components into the ff7_hook.py file. The main game hook file should not contain game specific items.

FF7 Hook
    [] - Rewrite to match newly reworked game hook manager.

Template Previewer:
    [] - Create a template preview inside the Source Settings menu (on the right hand side), that will display a live preview of the template, and show any changes (both saved and pending).
    [] - Templates that auto-hide (spotify, PSN trophies to name a few) should always display in the preview. Other templates that appear and disappear (things like alerts, chat) should mock the behavior of the template while its in use (IE play alerts, add chat messages, etc)

Template Creator:
    [] - Add a new "main" tab after Settings called "Spore Studio".
    [] - Create a GUI based editor for creating and/or editting HTML templates and their JSON files specifically for Mycelian. This editor MUST be easy to use, as it will be the main source of customization for standard users. I still want a comprehensive list of options for the user to pick from.
    [] - Add a "Create" button that will generate a starting HTML template and JSON file in the correct location. This should give options for the user to select what alert system to listen to (queue vs instant), whether it should copy from an existing template, and a name. This is to set up basic items like the websockets, files, etc etc. This will also need to create the template's asset folder in the /assets directory.
    [] - The GUI editor should contain options to: add images, text, videos, audio, animations, effects, auto-hiding, etc etc.
    [] - The editor should work off a "drag and drop" style system for placing elements. When an element is placed, it should ask for a "category name" that will correspond to the properties segment for that element in its companion JSON file.
    [] - There should be a "properties" window when clicking on an element that will allow the user to edit options for it (font size, location, display type, animation, segment name, etc etc). The properties of these items should be populated into the JSON file with the set values automatically, and sorted within the segment name. The user should be able to add/remove properties to an element as desired (filtered to required properties: IE: font size for text).
    [] - Populate any items in the template's asset folder to be placed in. Repopulate this list if changes were detected within the asset folder.
    [] - Include an "undo" system to revert the most recent change. Include an undo history. Ideally store all changes to undo, but limit to a set value for the history if performance will be an issue.
    [] - Incorporate the same preview system that the Source Settings menu will have (detailed above)


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
    [] - Fix connector folder closing when toggle a connector on/off and when moving a connector out of the folder
    [] - Add a badge icon with a number to the notification history button

Priority Fixes:
    [] - Setup "generic image/video" template, so the file can just be renamed and all websocket events will point to the file name.
    [] - Inspect Spotify OAuth flow. Tokens are not being refreshed upon app startup properly, and are not being refreshed when they expire
