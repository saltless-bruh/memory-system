# Git Workflow — SNP Memory System

## Agent-initiated changes

1. Never push or merge directly to `main` or `master`; use a feature branch and
   human-reviewed pull request (rules R-6.4, R-7.3).
2. The agent surface enforces this rule by absent capability: no exposed tool
   performs a git push.
3. Preserve unrelated work and stage only intended files.
4. Run bounded offline tests with sockets disabled. Run integration tests only
   against the documented disposable project when live services are in scope.
5. Treat `index.md` and `log.md` as authored control documents. Update them only
   according to the target vault's schema; do not replace them with generated
   blank descriptions or machine operational messages.
6. Report checks actually run, and distinguish content findings from
   infrastructure failures and unrun live gates.

## Human editing in Obsidian

Humans edit the vault directly in Obsidian and push to `main` or `master`.
The next agent answer reflects the edit with no operator action required
(acceptance workflow W-2).
