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