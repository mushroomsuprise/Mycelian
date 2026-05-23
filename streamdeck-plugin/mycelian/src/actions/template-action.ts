import { action, DidReceiveSettingsEvent, KeyDownEvent, SingletonAction, WillAppearEvent } from "@elgato/streamdeck";
import { apiUrl, DEFAULT_SERVER_URL, parseServerConfig } from "../lib/server-config";

const SUCCESS_TITLE_RESET_MS = 150;

/**
 * Settings for {@link TemplateAction}.
 */
type TemplateActionSettings = {
	serverUrl?: string;
	selectedTemplate?: string;
	selectedAction?: string;
	resolvedEvent?: string;
	actionData?: Record<string, any>;
};

/**
 * An action class that executes configurable template actions in the Mycelian app via HTTP requests.
 */
@action({ UUID: "com.mushroomsuprise.mycelian.templateaction" })
export class TemplateAction extends SingletonAction<TemplateActionSettings> {
	/**
	 * The {@link SingletonAction.onWillAppear} event is useful for setting the visual representation of an action when it becomes visible.
	 */
	override async onWillAppear(ev: WillAppearEvent<TemplateActionSettings>): Promise<void> {
		await this.updateButtonTitle(ev.action, ev.payload.settings);
	}

	/**
	 * Listens for the {@link SingletonAction.onKeyDown} event which is emitted by Stream Deck when an action is pressed.
	 * When triggered, we send a request to execute the configured template action.
	 */
	override async onKeyDown(ev: KeyDownEvent<TemplateActionSettings>): Promise<void> {
		try {
			const { settings } = ev.payload;

			if (!settings.selectedTemplate || !settings.selectedAction) {
				console.error('Template action not configured: missing template or action selection');
				await ev.action.setTitle("TEMPLATE\nNOT SET");
				return;
			}

			const serverConfig = parseServerConfig(settings.serverUrl || DEFAULT_SERVER_URL);

			const requestData: Record<string, unknown> = {
				templateName: settings.selectedTemplate,
				actionName: settings.selectedAction,
				actionData: settings.actionData || {},
			};
			const resolvedEvent = settings.resolvedEvent?.trim();
			if (resolvedEvent) {
				requestData.eventName = resolvedEvent;
			}

			const response = await fetch(apiUrl(serverConfig, "/api/streamdeck/template_action"), {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
				},
				body: JSON.stringify(requestData)
			});

			if (!response.ok) {
				const errorText = await response.text();
				console.error('HTTP response error text:', errorText);
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { success: boolean; message: string; templateName: string; actionName: string };

			if (result.success) {
				const title = `${settings.selectedTemplate}\n${settings.selectedAction}\n✓`;
				void ev.action.setTitle(title);
				setTimeout(() => {
					void this.updateButtonTitle(ev.action, settings);
				}, SUCCESS_TITLE_RESET_MS);
			} else {
				throw new Error(result.message || 'Unknown error');
			}

		} catch (error) {
			console.error('Failed to execute template action:', error);
			await ev.action.setTitle("TEMPLATE\nERROR");
		}
	}

	override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<TemplateActionSettings>): Promise<void> {
		await this.updateButtonTitle(ev.action, ev.payload.settings);
	}

	private async updateButtonTitle(action: any, settings?: TemplateActionSettings): Promise<void> {
		try {
			const currentSettings = settings || await action.getSettings() as TemplateActionSettings;

			if (!currentSettings.selectedTemplate || !currentSettings.selectedAction) {
				await action.setTitle("TEMPLATE\nNOT SET");
				return;
			}

			const templateName = currentSettings.selectedTemplate.length > 8
				? currentSettings.selectedTemplate.substring(0, 8) + "..."
				: currentSettings.selectedTemplate;

			const actionName = currentSettings.selectedAction.length > 8
				? currentSettings.selectedAction.substring(0, 8) + "..."
				: currentSettings.selectedAction;

			await action.setTitle(`${templateName}\n${actionName}`);

		} catch (error) {
			console.error('Failed to update template action button title:', error);
			await action.setTitle("TEMPLATE\nERROR");
		}
	}
}
