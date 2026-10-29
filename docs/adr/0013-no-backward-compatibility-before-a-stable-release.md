# No backward compatibility before a stable release

Until zloop declares a stable release, a change replaces a name, field, flag or saved-state format in one switch: no alias, fallback, dual field, reader of the old form or migration of saved state. A run in progress elsewhere that still holds the old form is told once, not designed around. We chose this over expand, migrate and contract, because the user put correctness and simplicity first while the framework is under development, and compatibility layers had added tickets, dual keys and aliases for state nobody needs to keep.
