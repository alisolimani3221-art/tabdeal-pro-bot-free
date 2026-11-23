# Paper runner recovery safety

The foreground paper simulator is read-only and has no exchange-order capability.
Its lifecycle is independent from the prepared-intent coordinator.

## Recovery rules

- The app-wide runtime lock must be held.
- The persisted coordinator snapshot must be readable and structurally valid.
- Any pending prepared intent blocks recovery.
- A coordinator emergency-stop flag is preserved and is never cleared by the paper-runner UI.
- Only a persisted paper-engine `EMERGENCY_STOP` with no open paper position can be re-armed after explicit operator confirmation.
- Maximum-loss halts, malformed state, active workers, and unknown halt reasons remain fail-closed.
- If post-rearm readiness fails, the paper-engine emergency stop is restored before the UI reports failure.

No real account, API key, live position, or exchange order is accessed by this recovery path.
