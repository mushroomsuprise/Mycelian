import { action, DidReceiveSettingsEvent, KeyDownEvent, SingletonAction, WillAppearEvent, WillDisappearEvent } from "@elgato/streamdeck";

/**
 * Settings for {@link ToggleAlerts}.
 */
type ToggleAlertsSettings = {
	serverUrl?: string;
};

/**
 * An action class that toggles alert playback in the Mycelian app via HTTP requests.
 */
@action({ UUID: "com.mushroomsuprise.mycelian.togglealerts" })
export class ToggleAlerts extends SingletonAction<ToggleAlertsSettings> {
	private readonly DEFAULT_SERVER_URL = "127.0.0.1:5000";
	private pollingTimer: NodeJS.Timeout | null = null;
	private readonly POLLING_INTERVAL = 2000; // Poll every 2 seconds
	private lastKnownStatus: 'ACTIVE' | 'PAUSED' | 'ERROR' | null = null;

	/**
	 * Parse server URL string into host and port components
	 */
	private parseServerConfig(serverUrl: string): { host: string; port: number } {
		// Handle formats like "127.0.0.1:5000" or just "127.0.0.1"
		const parts = serverUrl.split(':');
		if (parts.length === 2) {
			return {
				host: parts[0],
				port: parseInt(parts[1], 10)
			};
		} else if (parts.length === 1) {
			// Default port if not specified
			return {
				host: parts[0],
				port: 5000
			};
		} else {
			// Invalid format, return defaults
			console.warn(`Invalid server URL format: ${serverUrl}, using defaults`);
			return {
				host: "127.0.0.1",
				port: 5000
			};
		}
	}

	/**
	 * Start polling the server for status updates
	 */
	private startPolling(serverConfig: { host: string; port: number }, action: any): void {
		// Clear any existing timer
		this.stopPolling();

		console.log(`Starting status polling for ${serverConfig.host}:${serverConfig.port}`);

		// Start polling
		this.pollingTimer = setInterval(async () => {
			await this.pollStatus(serverConfig, action);
		}, this.POLLING_INTERVAL);
	}

	/**
	 * Stop polling the server
	 */
	private stopPolling(): void {
		if (this.pollingTimer) {
			clearInterval(this.pollingTimer);
			this.pollingTimer = null;
			console.log('Stopped status polling');
		}
	}

	/**
	 * Poll the server for current status and update if changed
	 */
	private async pollStatus(serverConfig: { host: string; port: number }, action: any): Promise<void> {
		try {
			// Create a timeout promise
			const timeoutPromise = new Promise((_, reject) => {
				setTimeout(() => reject(new Error('Request timeout')), 3000);
			});

			// Race between fetch and timeout
			const response = await Promise.race([
				fetch(`http://${serverConfig.host}:${serverConfig.port}/api/streamdeck/get_pause_status`),
				timeoutPromise
			]) as Response;

			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { paused: boolean; success: boolean; message: string };
			const currentStatus = result.paused ? 'PAUSED' : 'ACTIVE';

			// Only update if status has actually changed
			if (this.lastKnownStatus !== currentStatus) {
				console.log(`Status changed from ${this.lastKnownStatus} to ${currentStatus}`);
				this.lastKnownStatus = currentStatus;

				const title = `TGL ALERTS\n${currentStatus}`;
				await action.setTitle(title);
			}

		} catch (error) {
			// Only log polling errors if we're not already in error state to avoid spam
			if (this.lastKnownStatus !== 'ERROR') {
				console.warn('Polling error (will retry):', error);
			}

			// If we get multiple polling errors, show error state
			if (!this.lastKnownStatus || this.lastKnownStatus === 'ERROR') {
				this.lastKnownStatus = 'ERROR';
				const title = "TGL ALERTS\nERROR";
				try {
					await action.setTitle(title);
				} catch (e) {
					// Ignore errors when setting title during polling
				}
			}
		}
	}

	/**
	 * The {@link SingletonAction.onWillAppear} event is useful for setting the visual representation of an action when it becomes visible.
	 * We're fetching the current alert pause status and updating the button title accordingly.
	 */
	override async onWillAppear(ev: WillAppearEvent<ToggleAlertsSettings>): Promise<void> {
		const { settings } = ev.payload;
		const serverUrl = settings.serverUrl || this.DEFAULT_SERVER_URL;
		const serverConfig = this.parseServerConfig(serverUrl);

		// Update button title initially
		await this.updateButtonTitle(ev);

		// Start polling for continuous updates
		this.startPolling(serverConfig, ev.action);
	}

	/**
	 * Called when the action is about to disappear from the Stream Deck.
	 * We stop polling to avoid unnecessary network requests.
	 */
	override async onWillDisappear(ev: WillDisappearEvent<ToggleAlertsSettings>): Promise<void> {
		this.stopPolling();
	}

	/**
	 * Listens for the {@link SingletonAction.onKeyDown} event which is emitted by Stream Deck when an action is pressed.
	 * When triggered, we send a request to toggle the alert pause status in the Mycelian web server.
	 */
	override async onKeyDown(ev: KeyDownEvent<ToggleAlertsSettings>): Promise<void> {
		try {
			const { settings } = ev.payload;
			const serverUrl = settings.serverUrl || this.DEFAULT_SERVER_URL;
			const serverConfig = this.parseServerConfig(serverUrl);

			// Send toggle request to the Mycelian web server
			const response = await fetch(`http://${serverConfig.host}:${serverConfig.port}/api/streamdeck/toggle_alerts`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
				}
			});

			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = await response.json() as { paused: boolean; success: boolean; message: string };

			// Update our internal status tracking and button title
			this.lastKnownStatus = result.paused ? 'PAUSED' : 'ACTIVE';
			const title = `TGL ALERTS\n${this.lastKnownStatus}`;
			await ev.action.setTitle(title);

			// Restart polling after manual toggle to ensure we catch the change immediately
			// This also helps if the polling was paused due to errors
			this.startPolling(serverConfig, ev.action);

		} catch (error) {
			console.error('Failed to toggle alerts:', error);
			// Show error state
			await ev.action.setTitle("TGL ALERTS\nERROR");
		}
	}

	/**
	 * Called when the action receives new settings.
	 * We restart polling with the new server configuration.
	 */
	override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<ToggleAlertsSettings>): Promise<void> {
		const { settings } = ev.payload;
		const serverUrl = settings.serverUrl || this.DEFAULT_SERVER_URL;
		const serverConfig = this.parseServerConfig(serverUrl);

		// Restart polling with new server configuration
		this.startPolling(serverConfig, ev.action);

		// Update button title with current status
		await this.updateButtonTitle(ev);
	}

	/**
	 * Updates the button title to reflect the current alert pause status.
	 */
	private async updateButtonTitle(ev: KeyDownEvent<ToggleAlertsSettings> | WillAppearEvent<ToggleAlertsSettings> | DidReceiveSettingsEvent<ToggleAlertsSettings>, forcePaused?: boolean): Promise<void> {
		try {
			const { settings } = ev.payload;
			const serverUrl = settings.serverUrl || this.DEFAULT_SERVER_URL;
			const serverConfig = this.parseServerConfig(serverUrl);

			let status: 'ACTIVE' | 'PAUSED' | 'ERROR';

			if (forcePaused !== undefined) {
				status = forcePaused ? "PAUSED" : "ACTIVE";
			} else {
				// Fetch current status
				const response = await fetch(`http://${serverConfig.host}:${serverConfig.port}/api/streamdeck/get_pause_status`);
				if (!response.ok) {
					throw new Error(`HTTP ${response.status}: ${response.statusText}`);
				}
				const result = await response.json() as { paused: boolean; success: boolean; message: string };
				status = result.paused ? "PAUSED" : "ACTIVE";

				// Update our internal status tracking
				this.lastKnownStatus = status;
			}

			// Update button title with fixed "TOGGLE ALERTS" header and status below
			const title = `TGL ALERTS\n${status}`;
			await ev.action.setTitle(title);

		} catch (error) {
			console.error('Failed to get pause status:', error);
			const errorStatus = 'ERROR';
			this.lastKnownStatus = errorStatus;
			const title = `TGL ALERTS\n${errorStatus}`;
			await ev.action.setTitle(title);
		}
	}
}
