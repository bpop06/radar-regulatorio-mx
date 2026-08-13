# Programación local retirada

Los LaunchAgents y cron locales están retirados. Los instaladores bajo
`scripts/install_macos_*.sh` fallan de forma explícita para impedir que se
reactive por accidente una segunda vía de publicación.

La operación vigente usa una sola automatización de proyecto de Codex, un
worktree limpio y publicación por pull request. Véase
[OPERATIONS.md](OPERATIONS.md).
