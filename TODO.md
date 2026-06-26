Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Spore Studio:
    [x] - Add in additional text options: text leading, kerning, bold, italic, underlined, strikethrough, etc etc
    [x] - Dragging text element box did not update the size, could also effect other elements
    [x] - Make sections in the Properties tab collapsable (compressed by default)
    [x] - Align checkboxes with their text in the Properties tab, instead of being above or below it
    [x] - Utilize more tooltips for options so title text can be simpler
    [x] - Make right panel resizable
    [x] - Make it easier to see what tab youre in for the right panel. Add better separation between the tabs. Make the tabs a "carousel" if they overflow the panel width
    [x] - Remove the bottom black bar, remove the "Loaded" text and move "Unsaved Changes" text to be to the left of the Preview button. See first image for how it currently looks.
    [x] - Update the Progress Bar element's icon. it currently appears as a small square
    [x] - Remove the extra "?" button to open the help next to the reload editor button. there is a global button in the top right of the app that handles this already
    [x] - Add some new "blocks" for users, include all adjustment options, features, ways to display live data, etc etc. The end users will likely be creating elaborate displays of wildly varying content to display in OBS, so we should ensure we expose as many options as possible. Right now we have basic items such as text, videos, images, and progress bars. We should add in more options to help end users achieve unique displays.
    [x] - Reorganize block list into groups, then have the groups listed in alphabetical order, and the items inside each group in alphabetical order.
    [x] - Inspect the undo/redo logic. ive ran into several situations where the redo will not work
    [x] - Remove the very top "Spore Studio", and the description text underneath. Move the Reload Editor and Open Externally buttons to be to the left of the "Preview" button instead.


Chat template:
    [x] - Add option to change the chat message for an alert (bits, subs, resubs, etc) from a regular chat message, to a special event type message that will display the alert image/video and the alert message. This should have the option to do either message type or both. Will need to scale the image to the font size, and the font size should be based on the event text font size
    [x] - Fix reply message font size to be based on the event message font size
    [x] - Fix reply messages so that the reply is only a single text line that gets truncated with 3 dots
    [x] - Fix reply messages not stripping the leading username from the main message
    [x] - Add padding options to the alert event display for the images and fonts
    [x] - Update reply font size to be driven off the chat message. It should use a user definable scaler value to make it smaller (default 0.75x).
    [] - Fix missing "simple" event messages for non media rich events. it should always default back to the simple message if the media alert errors. this should happen even if the toggle is disabled for simple event messages.
    [] - Fix media-rich alert always saying people resibscribed for 1 month. it should display the total number of months they have been subscribed for
    [] - Fix message formatting so it says "gifted a tier X sub!" when only a single gifted sub is sent.
    [] - Fix media-rich events displaying alerts that arent configured (follow alert for sure)
    [] - Fix username color matching to check as case insensitive

UI Improvements:
    [x] - Move the two Twitch toggles from App Settings to the Twitch tab. Put inside an "Options" card that will be below the connection cards
    [x] - Change the "Save As" button on the Theme tab, the "View on GitHub", and the "View Changelog" buttons to match the theme, instead of being the teal color.
    [x] - On the OBS Source Controls dock, toast notifications are fired every time something is clicked. This results in excessive notifications. We should only send notifications upon failures.
    [] - Remove the background from the preview area so it does not display as white