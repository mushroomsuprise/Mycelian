Legacy Templates:
    [] - Recreate the "memecalc" and "ttimers" in Spore Studio as a trial. Change them to a default "bitcounter" and "subcounter"

Spore Studio:
    [] - Investigate issues with the templates suddenly not updating until the app is closed. Maybe a web_engine failure?
    [] - Check hot reload maybe causing issues with packaged Windows version?
    [] - Port conflict for web_engine, may be due to reload feature? End user had to reboot to get it working again.
    [] - Add in additional text options: text leading, kerning, bold, italic, underlined, strikethrough, etc etc
    [] - Dragging text element box did not update the size, could also effect other elements
    [] - Make sections in the Properties tab collapsable (compressed by default)
    [] - Align checkboxes with their text, instead of being above or below it
    [] - Utilize more tooltips for options

Chat template:
    [x] - Add option to change the chat message for an alert (bits, subs, resubs, etc) from a regular chat message, to a special event type message that will display the alert image/video and the alert message. This should have the option to do either message type or both. Will need to scale the image to the font size, and the font size should be based on the event text font size
    [x] - Fix reply message font size to be based on the event message font size
    [x] - Fix reply messages so that the reply is only a single text line that gets truncated with 3 dots
    [x] - Fix reply messages not stripping the leading username from the main message