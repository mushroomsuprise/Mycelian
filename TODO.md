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

FF7 HTML Template
    [x] - Create HTML template that will hook onto the FF7 exe process to get data from the game.
        [x] - The template must be able to reliably hook onto the game process, rehook if the game has been closed and reopened, and fetch game data every .5 seconds.
    [x] - Create configurable elements for the template that display the following:
        [x] - Enemies - HP, Name, Level, and a progress bar for their ATB gauge.
        [x] - Party Members - HP, Name, Level, Limit Bar, and a progress bar for their ATB gauge.
        [x] - Current play time
        [x] - Current gil
        [x] - Bosses defeated (toggleable as a list of boss names, most recently killed boss name, or a total count) <-- This element must be resettable to nothing by clicking a button, or upon app restart.
    [x] - Template should be stylized to match FF7, by using its font, and setting up progress bars to look the same.
    [x] - Fix issue with enemies segment blinking on when not in a battle
    [x] - Decrease time between data fetches to 0.25 seconds
    [x] - Find memory values for game menu colors and configure the template to use them so the template segment backgrounds match the game menu colors.
    [x] - When a party member is killed, their name, current hp, and max hp, should be red. If their hp or mp is below 25% of their max, their current HP and MP should be yellow.
    [x] - Make party member limit and atb bars only appear during battle. When outside of battle, it should instead cycle through the following: showing the party member's equipped gear, a materia display showing the color orbs exactly as it appears in the game (no names), and the limit/atb bars. The cycle time should be definable in the template's JSON file under the Party segment.
    [x] - The materia display should uses assets located in the assets/ff7 folder. These will be named like "materia_purple", "materia_green", "linked_slot", "single_slot", etc etc.
    [x] - Make the party member limit and atb bars cycle through the bars and status effects similar to how it works outside of battle, just different data.
    [x] - Make the enemy ATB bar cycle through the status effects similar to the party member limit and atb bars.
    [x] - Add toggles to the template's JSON config file to control what is shown in the rotating section outside of battle. if only one toggle is enabled, it should statically display the one option. if two are enabled, it should cycle through the two options. if three are enabled, it should cycle through the three options.
    [x] - Add party member levels just before their name in the template.
    [x] - Shrink MP bar in width by about 10%
    [x] - Add field names and current module name to the Records segment. Display this segment as 2 columns. Gil and time (and their values) in the first column, and field name and module name in the second column.
    [x] - Update Records segment to include toggles to toggle each item (gil has its own toggle, time has its own toggle, etc etc)
    [x] - Add commas to the Gil value so its formatted like 1,000,000
    [x] - Fix MP coloring so it is not yellow when HP is below 25% of max HP, but when its below 25% of max MP instead
    [x] - Finish setting up the boss tracker segment. It should either be a list of bosses, the most recently killed boss, or a total count of bosses killed.
    [x] - Slow down timer for the rotators
    [x] - Fix equipment not displaying the correct name of the equipment.

Game Hooks
    [x] - Create a system to write to game memory values for "Crowd Control" style of streams.
    [x] - Expose the game memory writes (Must be easy names like, Add X Gil, Kill X Enemy, etc etc) with appropriate arguments to the Connectors system as actions so they can be triggered by Connector events.
    [x] - Functions for connector actions should be defined in the specific game's hook file. Example: ff7_hook.py should have a function called "add_gil" that takes a positive only integer argument.
    [x] - Create a new directory in the modules folder called "game_hooks" for all of the new game hook files. (Just game hooks, not the core game hooks service)
    [x] - Add game hook service to the help system. List out currently available game hooks, and explain in plain text what information is available for each game hook (both reading and writing options). I want this to be human readable, not just a list of function names and arguments.
    [x] - Rewrite hook service to be game agnostic, so it can be used for any game that has a hook service. Game hooks should be able to be added and removed at runtime, and should be able to be configured to use different memory values for different games. Everything should be specified in the game hook file, the hook service is just a module that handles the reading and writing of game memory values.

FF7 Hook
    [x] - Find the memory values that handle inputs, such as movement confirm/cancel, menu button, etc etc, and expose them as functions for the Connectors system to use.
    [x] - Add functions to kill all enemies, add gil, remove gil, kill a specific party member, add hp to a specific party member, remove hp from a specific party member, etc etc.
    [x] - Move the ff7_reader.py file to the game_hooks directory. Then rename the ff7_reader.py file to ff7_hook.py
    [x] - Finish listing out all of the bosses so they can be tracked and displayed in the template. Full list can be found here: https://finalfantasy.neoseeker.com/wiki/Bosses_(FFVII)
    [x] - Add memory addresses for materia, and gear, so they can be displayed in the template. (Community FF7 memory tooling / reference notes.)
    [x] - Add memory addresses for statuses and conditions, so they can be displayed in the template. (Community FF7 memory tooling / reference notes.)
    [x] - Add ability to rename characters, args should be <current_character_name> and <new_character_name>
    [x] - Add ability to inflict status effect on character(s) (positive and negative, including fury and sadness), args should be: <character> and <status_effect>
    [x] - Add ability to change equipment for a character. equipment can be weapons, armor, and accessories. We must safeguard weapons/armor so that we don't force equip gear a character cannot use (do this by making a table map of gear for each character). args should be: <character> and <gear>
    [x] - Disable/enable menus (reference community FF7 tooling), args should be: <menu_name>
    [x] - Change game speed (permanently until next change, or time based; reference community FF7 tooling), args should be: <duration> and <speed>
    [x] - Update the kill character to use character names as an arg, instead of having to force a specific slot.
    [x] - Update the add and remove HP actions to use character names as an arg, instead of a specific slot.
    [x] - Remove the "set party slot hp" action.
    [x] - Change "damage enemy" action to take enemy names instead of slots.
    [x] - Include options for random characters/enemies and random values (damage, status effect, gear, etc) on every FF7 connector action.
    [x] - Ensure connector actions can take arguments from a chat message, or point alert user input message. Include an example in the text line where it says "Requires Game Hooks enabled and game...." so users know how to pull the data from the message
    [x] - Add/Remove gil does not work.
    [x] - Kill enemy does not find the enemy names (must add a varible setup to give the message with the matched text from the condition removed)
    [x] - Gamespeed command fails to work without FFNx installed; verify memory addresses and FFNx vs vanilla paths against community FF7 reference tooling.
    [x] - Disabling/enabling menus does not work; verify memory addresses against community FF7 reference tooling.
    [x] - Increase and decrease enemy level <enemy> and <level> as args
    [x] - Increase party member level <character> and <level> as args
    [x] - Increase enemy stat <enemy>, <stat>, and <amount> as args
    [x] - Increase party member stat <character>, <stat>, and <amount> as args
    [x] - Change menu colors, either a single corner (upper left, upper right, lower left, lower right), or all 4. Need to set this up so its easy to type in either a color name (blue, red, green), or RGB/Hex values, then adapt to the FF7 menu color system.
    [x] - Equip/unequip materia. <character>, <gear>, <slot>, <materia> as args. If a materia is present in the slot, it must unequip the existing material before equipping the new one. Must validate that the user has the materia to equip. This should remove the materia from the inventory list when equipping, and place it back when unequipping. Just like it would when if it were done in the game's menu.
    [x] - Add check if equipment is available to the equip gear hook. This should remove the gear from the inventory list when equipping, and place it back when unequipping. Just like it would when if it were done in the game's menu.
    [x] - Start a battle. take <battle_id> as an arg. if the game is in the menu or victory modules, it must queue the fight until outside of those modules.
    [x] - Add items, take <item_name> and <quantity> as args
    [x] - Add materia, must take <materia_name> and <materia_level> as args, adds to the inventory, does not equip
    [x] - Add gear, must take <gear_name> as args, adds to the inventory, does not equip
    [x] - Replace combobox options with text inputs so variables can be used in the args. (Only on the recent added hook options, starting from Increase and decrease enemy level on down)
    [x] - Starting battles does not work.
    [x] - changing menu colors does not work.
    [] - Add ability to change game between wait and active to game hook, add to connector with <mode> as the arg (text input for variables)
    [] - Add battle mode to the ff7.html template under the Records segment
    [] - Enemy max HP gets colored yellow when below 25% when only the current HP should be colored on the FF7 template
    [] - Average party level should only be calculated based off the current characters in the party (and count). It also resets to 0 while in battle when it shouldnt
    [] - Add "most recent item" to the Records segment. We should implement this by keeping an in memory "snapshot" of the inventory and gil count, including item counts. If either a new item appears, or a quantity increases, but the gil amount does not DECREASE (Note: gil can increase as you get gil and items from battles), it should display the name of that item, and the newly received quantity. 


    


General Items:
    [x] - Auto-update is not notifying the user of updates upon startup.
    [x] - Interval update timer is not notifying when an update is available after the app has been already opened and running.
    [x] - Fix per user stats tracking. Nothing displays in the stats panel when searching for a specific user.
    [x] - Fix highlights exporting. I'm getting a "no data found in the selected date range" message.
    [x] - Allow connectors to be moved into user nameable folders. (drag and drop the cards onto each other to create a folder, then drag other cards onto the folder to add them to it)
    [] - Update the create theme dialog to show a mock-ui similar to the mock ui in the theme tab. It should live update as the user changes the different color boxes. Do a general overhaul of the create theme dialog to make better use of space.