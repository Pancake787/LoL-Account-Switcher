import streamDeck, { action, KeyDownEvent, SingletonAction, WillAppearEvent } from "@elgato/streamdeck";
import { execFile } from "child_process";
import { readFileSync } from "fs";
import { join } from "path";

/**
 * Settings persisted in the Stream Deck Property Inspector.
 * username: Riot display name typed manually (D-33: no dropdown)
 * exePath:  absolute path to lolswitcher.exe (or wrapper on py main.py)
 */
export type SwitchSettings = {
	username: string;
	exePath: string;
};

/**
 * Path to accounts.json — user-scoped via process.env.APPDATA (Pitfall 5).
 * NEVER use __dirname here: the plugin lives under %APPDATA%\Elgato\StreamDeck\Plugins\...
 */
export const ACCOUNTS_JSON = join(process.env.APPDATA ?? "", "LoLSwitcher", "accounts.json");

/**
 * Reads the currently active username from accounts.json.
 * Returns null when the file is missing or malformed — must NOT crash (D-35).
 */
export function readActiveUsername(): string | null {
	try {
		const data = JSON.parse(readFileSync(ACCOUNTS_JSON, "utf-8")) as Record<string, unknown>;
		const active = data.active_username;
		return typeof active === "string" ? active : null;
	} catch {
		return null;
	}
}

// ---------------------------------------------------------------------------
// Shared visual-state management (used by onWillAppear, onKeyDown and the poll)
// ---------------------------------------------------------------------------

/** How long a showOk/showAlert overlay stays visible before a setState may run again. */
const FEEDBACK_MS = 1500;

/** Last state we set per action id — diff-based: avoids redundant setState() that
 *  would otherwise cancel an in-flight showOk/showAlert overlay every poll tick. */
const lastState = new Map<string, 0 | 1>();

/** Per-action timestamp (ms) until which state writes are suppressed, so a freshly
 *  shown showOk/showAlert overlay is not clobbered by the polling loop. */
const suppressUntil = new Map<string, number>();

/** Suppress state writes for an action for FEEDBACK_MS so its overlay stays visible. */
function markFeedback(actionId: string): void {
	suppressUntil.set(actionId, Date.now() + FEEDBACK_MS);
}

/**
 * Reconcile every visible key's state with active_username from accounts.json.
 *
 * Diff-based (only writes on change) so it never spams setState() — a setState()
 * cancels a showOk/showAlert overlay, so an unconditional "setState every 3s" loop
 * silently ate the feedback icons. Actions inside their feedback window are skipped.
 */
export async function refreshStates(): Promise<void> {
	const active = readActiveUsername();
	const now = Date.now();
	for (const a of streamDeck.actions) {
		if (!a.isKey()) continue;
		if ((suppressUntil.get(a.id) ?? 0) > now) continue; // keep an active overlay visible
		const s = await a.getSettings<SwitchSettings>();
		const desired: 0 | 1 = s?.username === active ? 1 : 0;
		if (lastState.get(a.id) !== desired) {
			lastState.set(a.id, desired);
			void a.setState(desired);
		}
	}
}

/**
 * SwitchAccountAction — one instance per configured Stream Deck button.
 *
 * onWillAppear: Sets initial state (active/inactive) when button becomes visible.
 * onKeyDown:    Calls lolswitcher.exe switch <username> via execFile (no shell —
 *               T-03-08 Command-Injection guard). Shows showOk on exit 0,
 *               showAlert on any error including match-block (D-31).
 *
 * @action decorator registers the action UUID from manifest.json.
 */
@action({ UUID: "com.lolswitcher.plugin.switch-account" })
export class SwitchAccountAction extends SingletonAction<SwitchSettings> {
	/**
	 * Called each time a button with this action appears on the Stream Deck canvas.
	 * Sets the visual state immediately from accounts.json (Pitfall 7: only visible
	 * instances are addressable — this ensures correct state on first appear).
	 */
	override onWillAppear(ev: WillAppearEvent<SwitchSettings>): void {
		if (!ev.action.isKey()) return;
		const desired: 0 | 1 = ev.payload.settings.username === readActiveUsername() ? 1 : 0;
		lastState.set(ev.action.id, desired);
		void ev.action.setState(desired);
	}

	/**
	 * Called when the user presses the button.
	 *
	 * Validates settings, spawns the exe via execFile (no shell — T-03-08), shows
	 * feedback (showOk / showAlert), and updates the active marker AFTER the overlay
	 * clears so the feedback icon stays visible (markFeedback + delayed refreshStates).
	 */
	override async onKeyDown(ev: KeyDownEvent<SwitchSettings>): Promise<void> {
		const { username, exePath } = ev.payload.settings;

		// V5 Input Validation: reject empty / whitespace-only values before execFile (T-03-08)
		if (!username?.trim() || !exePath?.trim()) {
			markFeedback(ev.action.id);
			await ev.action.showAlert();
			return;
		}

		const succeeded = await new Promise<boolean>((resolve) => {
			execFile(
				exePath,
				["switch", username],     // literal array — no shell expansion possible (T-03-08)
				{ timeout: 15000 },       // T-03-10: 10s kill-timeout + buffer; ENOENT -> error
				(error: Error | null) => resolve(error === null),
			);
		});

		if (!succeeded) {
			// Non-zero exit (match-block / error / no-snapshot), ENOENT, timeout — showAlert (D-31).
			// Nothing changed on disk, so we only show the alert; suppression + diff-based
			// polling keep the warning triangle visible (no setState clobbers it).
			markFeedback(ev.action.id);
			await ev.action.showAlert();
			return;
		}

		// Exit 0 — successful switch. Suppress state writes first so the checkmark is not
		// cancelled by a poll tick, then refresh the active marker once the overlay clears.
		markFeedback(ev.action.id);
		await ev.action.showOk();
		setTimeout(() => void refreshStates(), FEEDBACK_MS + 250);
	}
}
