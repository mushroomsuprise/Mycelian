Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Twitch:
    [x] - Fix new sub filter logic. it appears all new subs are still not being played

Chat Template:
    [x] - Add ability to display new Twitch GIFs. Create enable/disable toggle and have it disabled by default (keep current behavior when off), and add setting to scale the size.
    [x] - Fix colorized usernames when a chatter does not have a specific global color picked out. It is currently making them all white.
    [x] - Fix stream streak event messages not being displayed
    [x] - Fix channel point icon so it displays the custom icon made by the user, and not the generic Twitch icon (unless no custom icon is set). Need to check the docs for where to get it.

Alerts: 
    [x] - Make stored alerts "repoll" the alert data so they get updated alert data when being replayed
    [x] - Fix test alerts not following the animation settings for the configured alert
    [x] - Fix new sub filter. This filter should build a database of users that have been seen in chat with any subscriber badge,or have sent a sub alert. The filter should monitor the "on_new_sub" endpoint from the Twitch EventSub loop, and if it fires, it should check if the user is in this database. If they are, it should silently skip alerts and adding to the activity feeds. If they are not, it should trigger a new sub alert (1 month sub).
    [x] - Add toggle setting to the alerts template that will let users switch the alert gif to be the "gigantified emote" when it comes from that reward (default off). Also add a setting toggle to display the gigantifed emote larger (default 3x larger) in the alert message chat (default off). We will need to set up a way that tells the template that it is a gigantified emote and not just a normal bit donation.

Web Engine:
    [x] - Add a local-only filter for access to the served routes. Since we listen on all connections now, we should make sure any external addresses are blocked, and only local connections can be made.

OBS Integration:
    [x] - Add a button to the cards on the Settings>About page's template routes, to add them as browser sources to OBS. Clicking this button should either throw an error if OBS websocket is not configured and/or connected, or open a small dialog to allow the user to select what Scene to add the source to, its resolution values, and checkboxes for the 4 OBS options (Control audio via OBS, Use custom frame rate, Shutdown source when not visible, Refresh browser source when scene becomes active). Custom CSS should be empty for Mycelian sources. "Add to OBS" button should be placed to the right of the copy URL button, and should just show the OBS icon instead of text

Containers:
    [x] - Add a "container" system that will allow users to merge multiple templates into a single web route. This will need to be setup in Spore Studio. Note: this should not physically combine them into a single HTML file, but instead just load each HTML file into a single route. 
    [x] - Templates should be placed in a grid layout. This should be a grid system for positioning only, not necessarily fixed perfect rows/columns due to the desired dynamic size adjustments. For example: template 2 is below template 1, template 3 is to the right of template 1, but template 3 can size change into the same "row" template 2 is in, without influencing the other template locations.
    [x] - Container system should allow "dynamic" adjustment of the nested templates. Example: if 2 templates are stacked one on top of the other and the container source has a vertical resolution of 1080, then if one template continually changes size it should shrink the other's vertical height to compensate and so on.
    [x] -  "dynamic" setup should be user selectable on which template takes size priority, as well as minimum sizes as percentages and pixels.
    [x] - The container route should be user definable for the path, and should appear in the Settings>About page the same way as the other routes do. This should have its own badge on those cards to denote that it is a container, and show the number of templates that are combined together.
    [x] - Any templates should be able to be containerized, not just spore studio templates.
    [x] - Add options to mute audio from specific templates when containerized.


General:
    [x] - Fix built-in activity feed still drawing a blank window. This seems to happen once a user is live and gets alerts past midnight, and persists between restarts until a long period of time happens. There has been many attempts to fix this with no success so far.
    [x] - Add in options to trim stored alert data automatically to the Settings>App Settings tab. There should be a toggle to turn on the auto-trim, the quantity of past alerts to keep, an timeframe to keep them, and an option to trim based on quantity or based on time or both.
    [x] - Add persistant storage of condensed feed list filters

YouTube Integration:
    [] - Add secondary credentials system for YouTube to have a "chatbot", similar to the Twitch system

Donations:
    [] - Add in ability to connect major cash services (like PayPal and Venmo) so the user can generate a "donations" link that Mycelian can get data from to use for alerts

Kik Integration:
    [] - Add in Kik integration service
    [] - Add in chat messages so they appear in the main chat box. Make this a toggle option in the JSON file. Default to being off.
    [] - Add Kik to the alerts alerts, matching up the Kik equivalent to the current Twitch configurations
    [] - Add options to the chatbot and connectors that parity Twitch options to allow platform specific targetting for Chatbot and Connectors
    [] - Add as a send target to the chatbot
    [] - Add Kik specific badging to activity feeds
    [] - Add secondary credentials system for Kik to have a "chatbot", similar to the Twitch system
