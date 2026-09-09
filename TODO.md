Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Twitch:
    [] - Fix new sub filter logic. it appears all new subs are still not being played

Chat Template:
    [] - Add ability to display new Twitch GIFs. Create enable/disable toggle and have it disabled by default (keep current behavior when off), and add setting to scale the size.
    [x] - Fix colorized usernames when a chatter does not have a specific global color picked out. It is currently making them all white.

Alerts: 
    [x] - Make stored alerts "repoll" the alert data so they get updated alert data when being replayed
    [x] - Fix test alerts not following the animation settings for the configured alert
    [x] - Fix new sub filter. This filter should build a database of users that have been seen in chat with any subscriber badge,or have sent a sub alert. The filter should monitor the "on_new_sub" endpoint from the Twitch EventSub loop, and if it fires, it should check if the user is in this database. If they are, it should silently skip alerts and adding to the activity feeds. If they are not, it should trigger a new sub alert (1 month sub).

General:
    [x] - Fix built-in activity feed still drawing a blank window. This seems to happen once a user is live and gets alerts past midnight, and persists between restarts until a long period of time happens. There has been many attempts to fix this with no success so far.
    [x] - Add in options to trim stored alert data automatically to the Settings>App Settings tab. There should be a toggle to turn on the auto-trim, the quantity of past alerts to keep, an timeframe to keep them, and an option to trim based on quantity or based on time or both.

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
