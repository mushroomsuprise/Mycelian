Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Source Controls:
    [] - Update Source Controls dock and in-app UI to allow items to be compressed better. IE: multiple template controls can be shown in a single row.
    [] - Update button names for all templates to not just be "Action". This will involve update the companion JSON files.

Source Settings:
    [] - Update color picker to be a proper color picker, not just a palette of fixed options.

General:
    [] - Add connection checking logic to all services (Twitch, OBS (already exists), PSN, Spotify, etc etc) that will monitor the connection stat and if the service is disconnected, it should automatically try to reconnect (IE: if the user loses internet). This should have some logic to test the validity of the user's internet connection first before attempting to connect to any disconnected services, as its pointless without it. Note: The database is unique because the user can be on a local database, which should never disconnect.  
