import { action, DidReceiveSettingsEvent, KeyDownEvent, SingletonAction, WillAppearEvent, WillDisappearEvent } from "@elgato/streamdeck";
import { apiUrl, DEFAULT_SERVER_URL, parseServerConfig } from "../lib/server-config";

/**
 * Settings for {@link TemplateAction}.
 */
type TemplateActionSettings = {
	serverUrl?: string;
	selectedTemplate?: string;
	selectedAction?: string;
	actionData?: Record<string, any>;
};

/**
 * An action class that executes configurable template actions in the Mycelian app via HTTP requests.
 */
@action({ UUID: "com.mushroomsuprise.mycelian.templateaction" })
export class TemplateAction extends SingletonAction<TemplateActionSettings> {
	private pollingTimer: NodeJS.Timeout | null = null;
	private readonly POLLING_INTERVAL = 5000; // Poll every 5 seconds for configuration updates

	/**
	 * Start polling the server for template actions updates
	 */
	private startPolling(serverConfig: { host: string; port: number }, action: any): void {
		// Clear any existing timer
		this.stopPolling();

		console.log(`Starting template actions polling for ${serverConfig.host}:${serverConfig.port}`);

		// Start polling
		this.pollingTimer = setInterval(async () => {
			await this.pollTemplateActions(serverConfig, action);
		}, this.POLLING_INTERVAL);
	}

	/**
	 * Stop polling the server
	 */
	private stopPolling(): void {
		if (this.pollingTimer) {
			clearInterval(this.pollingTimer);
			this.pollingTimer = null;
			console.log('Stopped template actions polling');
		}
	}

	/**
	 * Poll the server for template actions (for validation purposes)
	 */
	private async pollTemplateActions(serverConfig: { host: string; port: number }, action: any): Promise<void> {
		try {
			const response = await fetch(apiUrl(serverConfig, "/api/streamdeck/get_template_actions"));
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { actions: any[]; success: boolean; message: string };

			// Don't update button title during polling to avoid loops
			console.log('Template actions poll successful');

		} catch (error) {
			console.warn('Template actions polling error (will retry):', error);
		}
	}

	/**
	 * Get available template actions from the server
	 */
	private async getAvailableTemplateActions(serverConfig: { host: string; port: number }): Promise<any[]> {
		try {
			const response = await fetch(apiUrl(serverConfig, "/api/streamdeck/get_template_actions"));
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { actions: any[]; success: boolean; message: string };
			return result.actions || [];
		} catch (error) {
			console.error('Failed to get template actions:', error);
			return [];
		}
	}

	/**
	 * The {@link SingletonAction.onWillAppear} event is useful for setting the visual representation of an action when it becomes visible.
	 */
	override async onWillAppear(ev: WillAppearEvent<TemplateActionSettings>): Promise<void> {
		const { settings } = ev.payload;
		const serverUrl = settings.serverUrl || DEFAULT_SERVER_URL;
		const serverConfig = parseServerConfig(serverUrl);

		// Update button title initially
		await this.updateButtonTitle(ev.action, settings);

		// Start polling for validation
		this.startPolling(serverConfig, ev.action);
	}

	/**
	 * Called when the action is about to disappear from the Stream Deck.
	 * We stop polling to avoid unnecessary network requests.
	 */
	override async onWillDisappear(ev: WillDisappearEvent<TemplateActionSettings>): Promise<void> {
		this.stopPolling();
	}

	/**
	 * Listens for the {@link SingletonAction.onKeyDown} event which is emitted by Stream Deck when an action is pressed.
	 * When triggered, we send a request to execute the configured template action.
	 */
	override async onKeyDown(ev: KeyDownEvent<TemplateActionSettings>): Promise<void> {
		try {
			const { settings } = ev.payload;

			// Validate that we have the required configuration
			if (!settings.selectedTemplate || !settings.selectedAction) {
				console.error('Template action not configured: missing template or action selection');
				await ev.action.setTitle("TEMPLATE\nNOT SET");
				return;
			}

			const serverUrl = settings.serverUrl || DEFAULT_SERVER_URL;
			const serverConfig = parseServerConfig(serverUrl);
			console.log('Using server config:', serverConfig);

			// Prepare the template action request
			const requestData = {
				templateName: settings.selectedTemplate,
				actionName: settings.selectedAction,
				actionData: settings.actionData || {}
			};

			console.log(`Executing template action: ${settings.selectedTemplate}.${settings.selectedAction}`, requestData);

			// Send the request to execute the template action
			const requestUrl = apiUrl(serverConfig, "/api/streamdeck/template_action");
			console.log('Making HTTP request to:', requestUrl);

			const response = await fetch(requestUrl, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
				},
				body: JSON.stringify(requestData)
			});

			console.log('HTTP response status:', response.status);
			console.log('HTTP response ok:', response.ok);

			if (!response.ok) {
				const errorText = await response.text();
				console.error('HTTP response error text:', errorText);
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { success: boolean; message: string; templateName: string; actionName: string };
			console.log('Server response:', result);

			if (result.success) {
				// Show success feedback
				const title = `${settings.selectedTemplate}\n${settings.selectedAction}\n✓`;
				await ev.action.setTitle(title);

				// Reset to normal display after a short delay
				setTimeout(async () => {
					await this.updateButtonTitle(ev.action, settings);
				}, 1000);

				console.log(`Template action executed successfully: ${result.message}`);
			} else {
				throw new Error(result.message || 'Unknown error');
			}

		} catch (error) {
			console.error('Failed to execute template action:', error);
			await ev.action.setTitle("TEMPLATE\nERROR");
		}
	}

	/**
	 * Called when the action receives new settings.
	 * We update the button title and restart polling if needed.
	 */
	override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<TemplateActionSettings>): Promise<void> {
		const { settings } = ev.payload;
		console.log('Template Action received settings:', JSON.stringify(settings, null, 2));

		const serverUrl = settings.serverUrl || DEFAULT_SERVER_URL;
		const serverConfig = parseServerConfig(serverUrl);

		// Update button title with new settings
		await this.updateButtonTitle(ev.action, settings);

		// Restart polling with new server configuration if URL changed
		this.startPolling(serverConfig, ev.action);
	}

	/**
	 * Updates the button title to reflect the current configuration.
	 */
	private async updateButtonTitle(action: any, settings?: TemplateActionSettings): Promise<void> {
		try {
			// Use provided settings or get current settings
			const currentSettings = settings || await action.getSettings() as TemplateActionSettings;

			if (!currentSettings.selectedTemplate || !currentSettings.selectedAction) {
				await action.setTitle("TEMPLATE\nNOT SET");
				return;
			}

			// Create a shortened display name for the template and action
			const templateName = currentSettings.selectedTemplate.length > 8
				? currentSettings.selectedTemplate.substring(0, 8) + "..."
				: currentSettings.selectedTemplate;

			const actionName = currentSettings.selectedAction.length > 8
				? currentSettings.selectedAction.substring(0, 8) + "..."
				: currentSettings.selectedAction;

			const title = `${templateName}\n${actionName}`;
			await action.setTitle(title);

		} catch (error) {
			console.error('Failed to update template action button title:', error);
			await action.setTitle("TEMPLATE\nERROR");
		}
	}

	/**
	 * Helper method to get available templates from the server
	 */
	async getAvailableTemplates(serverConfig: { host: string; port: number }): Promise<string[]> {
		try {
			const actions = await this.getAvailableTemplateActions(serverConfig);
			const templates = [...new Set(actions.map(action => action.template_name))];
			return templates.sort();
		} catch (error) {
			console.error('Failed to get available templates:', error);
			return [];
		}
	}

	/**
	 * Helper method to get available actions for a specific template
	 */
	async getAvailableActions(serverConfig: { host: string; port: number }, templateName: string): Promise<any[]> {
		try {
			const actions = await this.getAvailableTemplateActions(serverConfig);
			return actions.filter(action => action.template_name === templateName);
		} catch (error) {
			console.error('Failed to get available actions for template:', error);
			return [];
		}
	}
}
