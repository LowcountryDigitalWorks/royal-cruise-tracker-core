# Runtime configuration

`demo-profile.json` is deliberately synthetic and is safe for public CI.

For a real deployment, provide the same JSON shape through the protected `ROYAL_PROFILE_JSON` runtime value. Do not commit a real profile file.

A real profile can define:

- sailing metadata;
- configured UTC target windows;
- traveler role mapping;
- watch products and thresholds;
- one or more generic profile slots;
- environment-variable names that resolve protected credentials.

Keep credentials themselves outside the JSON when practical. Do not put a real runtime profile in repository variables or source control.
