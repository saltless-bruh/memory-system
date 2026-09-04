# Task Completion Workflow — SNP Memory System

1. Confirm the requested scope, declared ownership, and acceptance gates.
2. Make the smallest complete code or documentation change while preserving
   unrelated work.
3. Run focused tests first, then the bounded offline regression suite with
   sockets disabled. Run live checks only when their disposable dependencies
   are explicitly available.
4. For wiki work, manually review the target `SCHEMA.md` and V3 heading frame;
   the current automated checker is not complete V3 certification.
5. Report measured pass, fail, unmet, and abandoned counts. Never claim a check
   that was not executed.
