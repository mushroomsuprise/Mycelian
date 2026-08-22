Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Alerts Template:
    [x] - Add toggle option to display usernames of gift sub recipients when the gift sub amount is only to a single person.

Twitch:
    [x] - Create system to filter out new subs from resubs so brand new subs appear, but alerts dont hit twice for resubs.

Chat Template:
    [] - Add ability to display new Twitch GIFs. Create enable/disable toggle and have it disabled by default (keep current behavior when off), and add setting to scale the size.

FF7 Template:
    [x] - Add row display to the Party segment. Put in the same row as the character name and justified to the right so it sits above the MP number. Font size should match the username size. We should just show a label "FR" for front row and "BR" for back row.
    [x] - Double check materia color mapping, a couple could be wrong (cover maybe?), or it could be slots are being read wrong. someone mentioned there was an error with the first slot a couple of times.
    [x] - Add option to display all characters in the party section (including characters not part of the main 3-slot party), make this a togglable option.
    [x] - Add character portraits, make this a toggleable option. portraits will be placed in the assets folder and named like "cloud.png". Must be sized to stay within the current party member row height. Note: Until you pass the "Kalm Flashback", Cait Sith and Vincent's character data is actually Young Cloud and Sephiroth.
    [x] - Recheck the recent items setup. it still appears to display the incorrect item occasionally (mapping issue?)
    [x] - Add a setup to the party member segment to display if someone has "sadness" or "fury". See pasted image for how these should be color for font. i want this to be put to the right of the name (inbetween the name and where the row indicator is at)
    [x] - Make sadness/fury display shrink to "S" and "F" if the space is too small for names to appear properly (if names are ellipsed)
    [x] - Update limit bar so it appears the blueish color when someone is in sadness. you will need to look up the color for this as i dont have an example
    [x] - When portraits and row indicators are enabled, shift portraits to show the row indicator instead of the "FR" and "BR" labels. If portraits are turned off, then use the "FR" and "BR" setup. Portraits should be shifted more left if the character is in front row and shifted more right for back row.

YouTube Integration:
    [] - Add secondary credentials system for YouTube to have a "chatbot", similar to the Twitch system

Kik Integration:
    [] - Add in Kik integration service
    [] - Add in chat messages so they appear in the main chat box. Make this a toggle option in the JSON file. Default to being off.
    [] - Add Kik to the alerts alerts, matching up the Kik equivalent to the current Twitch configurations
    [] - Add options to the chatbot and connectors that parity Twitch options to allow platform specific targetting for Chatbot and Connectors
    [] - Add as a send target to the chatbot
    [] - Add Kik specific badging to activity feeds
    [] - Add secondary credentials system for Kik to have a "chatbot", similar to the Twitch system
