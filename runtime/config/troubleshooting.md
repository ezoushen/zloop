# Reset, troubleshooting and migration

## Reset and troubleshooting

Command examples below use Claude's spelling; use the corresponding setup command
in Codex or Pi.

| Intent or problem | Action |
|---|---|
| Keep the executor, restore native model/thinking | `/zloop:setup reset <agent> --route` |
| Clear only one override | `/zloop:setup reset <agent> --route thinking` or `model` |
| Use the lower-layer configuration again | Ask the skill to remove the selected profile or shared agent entry |
| Mask a personal executor selection for this project | Set the project agent entry to null; existing native preferences then apply |
| Missing `jsonschema` | Use the prepared plugin Python environment, or run the [setup steps](executors.md#manual-setup-and-inspection) |
| Named agent, skill or provider unavailable | Exclude the failed capability and use a suitable allowed pool candidate; report exhaustion if none remain |
| Unsupported model, thinking or inheritance | Exclude that combination; recover within the pool without approximating settings or breaking user pins |
| Settings appear ignored | Inspect with --route; configured agents bypass legacy models.json and active workers retain their launch settings |
| Wrong profile selected | Inspect config_profile/profile_source; --config-profile takes precedence over ZLOOP_PROFILE and the configured router |

## Migration

The `executors` package registry and `routes` format fail with a migration
diagnostic; they are never silently treated as native defaults. On explicit request,
replace each route with an `agents.<agent>` instance. A Herdr route becomes a
`skill` instance selecting the installed Herdr skill and a concrete delegation
instruction. Remove the old package registration only after translating its uses.
For override mode, copy the desired effective destination preferences into the
instance, renaming `effort` to `thinking`; do not infer a model from the parent.
Preview/validate the replacement before writing it. Existing `models.json` remains
supported for unconfigured native workers; migrating it is optional and explicit.

Installation preserves existing user configuration and writes no model/thinking
presets; to pin a pair for an agent, save it through the setup command. Reload native
agent definitions when they change; existing workers retain their launch settings.

Back up the file. Move `agents.<agent>.default` to the appropriate
`profiles.<name>.<agent>.<worker-runtime>.candidate`, keeping pools in `agents`.
Use `router.type: skill` or `decision-model` for adaptive selection, or select that profile explicitly.
Keep per-model thinking baselines and bounds. `profiles.<name>.agents`
entries require an explicit migration: shared models/routes/pools belong at the
top level, and only candidate/thinking preferences belong in routing profiles.
Do not translate a host profile name into a worker runtime without checking its
actual bindings. For catalog migration, move the previous
`model` value to `models.<name>.runtimes.<target>.id`, thinking policy alongside it,
and launch routes to `runtimes.<target>.executors`. Confirm the destination from its
installed contract or existing known setup; never infer it from model names or prose
alone. Reconcile conflicting shared routes before saving. The old catalog layout
fails with a migration diagnostic; it is not silently treated as native defaults.
