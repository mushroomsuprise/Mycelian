// Copyright (c) 2024-2026 Mycelian. SPDX-License-Identifier: MIT

import {
	action,
	DidReceiveSettingsEvent,
	KeyDownEvent,
	SingletonAction,
	WillAppearEvent,
	WillDisappearEvent,
} from "@elgato/streamdeck";
import {
	apiUrl,
	DEFAULT_SERVER_URL,
	parseServerConfig,
} from "../lib/server-config";

/** How often a key re-reads its goal, so a change made elsewhere (the app, chat) shows up. */
const REFRESH_MS = 15000;
const DONE_FLASH_MS = 900;

type GameGoalSettings = {
	serverUrl?: string;
	gameKey?: string;
	gameTitle?: string;
	goalId?: string;
	goalName?: string;
	operation?: "add" | "subtract";
	amount?: string | number;
};

type GoalState = {
	id: string;
	name: string;
	current: number;
	target: number;
	progress: number;
	completed: boolean;
};

/**
 * Counts a manual Games Tracker goal up or down. The key shows the goal's name and count.
 */
@action({ UUID: "com.mushroomsuprise.mycelian.gamegoal" })
export class GameGoalAction extends SingletonAction<GameGoalSettings> {
	private timers = new Map<string, NodeJS.Timeout>();

	override async onWillAppear(ev: WillAppearEvent<GameGoalSettings>): Promise<void> {
		await this.refresh(ev.action, ev.payload.settings);
		this.startRefreshing(ev.action, ev.payload.settings);
	}

	override onWillDisappear(ev: WillDisappearEvent<GameGoalSettings>): void {
		this.stopRefreshing(ev.action.id);
	}

	override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<GameGoalSettings>): Promise<void> {
		await this.refresh(ev.action, ev.payload.settings);
		this.startRefreshing(ev.action, ev.payload.settings);
	}

	override async onKeyDown(ev: KeyDownEvent<GameGoalSettings>): Promise<void> {
		const settings = ev.payload.settings;
		if (!settings.goalId) {
			await ev.action.setTitle("GOAL\nNOT SET");
			await ev.action.showAlert();
			return;
		}
		const server = parseServerConfig(settings.serverUrl || DEFAULT_SERVER_URL);
		try {
			const response = await fetch(apiUrl(server, "/api/streamdeck/adjust_goal"), {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					goalId: settings.goalId,
					operation: settings.operation === "subtract" ? "subtract" : "add",
					amount: amountOf(settings),
				}),
			});
			const result = (await response.json()) as { success: boolean; message?: string; goal?: GoalState };
			if (!response.ok || !result.success || !result.goal) {
				throw new Error(result.message || `HTTP ${response.status}`);
			}
			await ev.action.setTitle(goalTitle(result.goal, settings));
			if (result.goal.completed) {
				await ev.action.showOk();
				setTimeout(() => void ev.action.setTitle(goalTitle(result.goal as GoalState, settings)), DONE_FLASH_MS);
			}
		} catch (error) {
			console.error("Failed to change the goal:", error);
			await ev.action.setTitle("GOAL\nERROR");
			await ev.action.showAlert();
		}
	}

	private startRefreshing(action: any, settings: GameGoalSettings): void {
		this.stopRefreshing(action.id);
		if (!settings.goalId) {
			return;
		}
		this.timers.set(action.id, setInterval(() => void this.refresh(action), REFRESH_MS));
	}

	private stopRefreshing(id: string): void {
		const timer = this.timers.get(id);
		if (timer) {
			clearInterval(timer);
			this.timers.delete(id);
		}
	}

	/** Read the goal from Mycelian and show it; falls back to the saved name when Mycelian can't be reached. */
	private async refresh(action: any, settings?: GameGoalSettings): Promise<void> {
		const current = settings || ((await action.getSettings()) as GameGoalSettings);
		if (!current.goalId) {
			await action.setTitle("GOAL\nNOT SET");
			return;
		}
		const server = parseServerConfig(current.serverUrl || DEFAULT_SERVER_URL);
		try {
			const response = await fetch(
				apiUrl(server, `/api/streamdeck/get_game_goals?goalId=${encodeURIComponent(current.goalId)}`),
			);
			const result = (await response.json()) as { success: boolean; games?: { goals: GoalState[] }[] };
			const goal = result.success ? result.games?.[0]?.goals?.[0] : undefined;
			if (!goal) {
				await action.setTitle(`${shorten(current.goalName || "Goal")}\nGONE`);
				return;
			}
			await action.setTitle(goalTitle(goal, current));
		} catch {
			await action.setTitle(`${shorten(current.goalName || "Goal")}\n${sign(current)}`);
		}
	}
}

function amountOf(settings: GameGoalSettings): number {
	const n = Math.floor(Number(settings.amount ?? 1));
	return Number.isFinite(n) && n >= 1 ? n : 1;
}

function sign(settings: GameGoalSettings): string {
	return `${settings.operation === "subtract" ? "−" : "+"}${amountOf(settings)}`;
}

function shorten(text: string, max = 10): string {
	return text.length > max ? `${text.substring(0, max - 1)}…` : text;
}

/** "Endings / 2/5 / +1", or "✓ 5/5" when the goal is done. */
function goalTitle(goal: GoalState, settings: GameGoalSettings): string {
	const count = `${goal.completed ? "✓ " : ""}${goal.current}/${goal.target}`;
	return `${shorten(goal.name)}\n${count}\n${sign(settings)}`;
}
