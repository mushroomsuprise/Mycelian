import streamDeck, { LogLevel } from "@elgato/streamdeck";

import { ConnectorAction } from "./actions/connector-action";
import { TemplateAction } from "./actions/template-action";
import { ToggleAlerts } from "./actions/toggle-alerts";

// We can enable "trace" logging so that all messages between the Stream Deck, and the plugin are recorded. When storing sensitive information
streamDeck.logger.setLevel(LogLevel.TRACE);

// Register the toggle alerts action.
streamDeck.actions.registerAction(new ToggleAlerts());

// Register the template action.
streamDeck.actions.registerAction(new TemplateAction());

// Register the connector action.
streamDeck.actions.registerAction(new ConnectorAction());

// Finally, connect to the Stream Deck.
streamDeck.connect();
