import streamDeck from "@elgato/streamdeck";
import { refreshStates, SwitchAccountAction } from "./actions/switch-account";

// Register the action before connecting so the SDK can dispatch events.
streamDeck.actions.registerAction(new SwitchAccountAction());

// Connect to the Stream Deck application.
streamDeck.connect();

/**
 * Background polling loop (SD-05 / D-32).
 *
 * Every 3000 ms: reconcile every visible button's state with active_username from
 * accounts.json so an external switch (CLI / desktop app) is reflected on the keys.
 *
 * refreshStates() is diff-based and feedback-aware: it only calls setState() when a
 * button's desired state actually changes, and skips buttons within their showOk/
 * showAlert window. This is critical — an unconditional setState() every tick cancels
 * the feedback overlay, which is why the warning triangle never used to appear.
 */
setInterval(() => void refreshStates(), 3000);
