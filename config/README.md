# Runtime configuration

`demo-profile.json` is deliberately synthetic and is safe for public CI.

For a real deployment, set `ROYAL_RUNTIME_MODE=production` and provide the same JSON shape through protected `ROYAL_PROFILE_JSON` (or an explicit non-demo runtime path). Production mode never falls back to the committed demo profile. Do not commit a real profile file.

A real profile can define:

- sailing metadata;
- configured UTC target windows;
- traveler role mapping;
- watch products and thresholds;
- generic profile slots; FOUNDATION-001 permits exactly one enabled slot in production;
- environment-variable names that resolve protected credentials.

Keep credentials themselves outside the JSON when practical. Do not put a real runtime profile in repository variables or source control.
