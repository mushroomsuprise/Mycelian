// Copyright (c) 2024-2026 Mycelian. SPDX-License-Identifier: MIT

export type ServerConfig = {
	host: string;
	port: number;
};

export const DEFAULT_SERVER_URL = "127.0.0.1:5000";

/**
 * Parse server URL string into host and port components.
 */
export function parseServerConfig(serverUrl: string): ServerConfig {
	const parts = serverUrl.split(":");
	if (parts.length === 2) {
		return {
			host: parts[0],
			port: parseInt(parts[1], 10),
		};
	}
	if (parts.length === 1) {
		return {
			host: parts[0],
			port: 5000,
		};
	}
	console.warn(`Invalid server URL format: ${serverUrl}, using defaults`);
	return {
		host: "127.0.0.1",
		port: 5000,
	};
}

export function apiUrl(serverConfig: ServerConfig, path: string): string {
	return `http://${serverConfig.host}:${serverConfig.port}${path}`;
}
