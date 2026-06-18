// Copyright (c) 2024-2026 Mycelian. SPDX-License-Identifier: MIT

import {
	action,
	DidReceiveSettingsEvent,
	KeyDownEvent,
	SingletonAction,
	WillAppearEvent,
} from "@elgato/streamdeck";
import {
	apiUrl,
	DEFAULT_SERVER_URL,
	parseServerConfig,
} from "../lib/server-config";

const SUCCESS_TITLE_RESET_MS = 150;

type ConnectorActionSettings = {
	serverUrl?: string;
	connectorId?: string;
	connectorName?: string;
};

@action({ UUID: "com.mushroomsuprise.mycelian.connector" })
export class ConnectorAction extends SingletonAction<ConnectorActionSettings> {
	override async onWillAppear(
		ev: WillAppearEvent<ConnectorActionSettings>,
	): Promise<void> {
		await this.updateButtonTitle(ev.action, ev.payload.settings);
	}

	override async onKeyDown(
		ev: KeyDownEvent<ConnectorActionSettings>,
	): Promise<void> {
		const { settings } = ev.payload;

		if (!settings.connectorId) {
			await ev.action.setTitle("NOT SET");
			return;
		}

		const serverConfig = parseServerConfig(
			settings.serverUrl || DEFAULT_SERVER_URL,
		);

		try {
			const response = await fetch(
				apiUrl(serverConfig, "/api/streamdeck/trigger_connector"),
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ connectorId: settings.connectorId }),
				},
			);

			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			const result = (await response.json()) as {
				success: boolean;
				message?: string;
			};
			if (!result.success) {
				throw new Error(result.message || "Unknown error");
			}

			const name = settings.connectorName || "Connector";
			const shortName =
				name.length > 14 ? `${name.substring(0, 12)}...` : name;
			void ev.action.setTitle(`${shortName}\n✓`);

			setTimeout(() => {
				void this.updateButtonTitle(ev.action, settings);
			}, SUCCESS_TITLE_RESET_MS);
		} catch (error) {
			console.error("Failed to trigger connector:", error);
			await ev.action.setTitle("ERROR");
		}
	}

	override async onDidReceiveSettings(
		ev: DidReceiveSettingsEvent<ConnectorActionSettings>,
	): Promise<void> {
		await this.updateButtonTitle(ev.action, ev.payload.settings);
	}

	private async updateButtonTitle(
		action: any,
		settings?: ConnectorActionSettings,
	): Promise<void> {
		try {
			const current =
				settings || ((await action.getSettings()) as ConnectorActionSettings);

			if (!current.connectorId) {
				await action.setTitle("NOT SET");
				return;
			}

			const name = current.connectorName || "Connector";
			const lines =
				name.length > 14
					? [name.substring(0, 12) + "...", name.substring(12, 24)]
					: [name];
			await action.setTitle(lines.join("\n"));
		} catch (error) {
			console.error("Failed to update connector button title:", error);
			await action.setTitle("ERROR");
		}
	}
}
