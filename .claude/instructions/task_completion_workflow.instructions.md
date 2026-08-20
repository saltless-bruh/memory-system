# Task Completion Workflow — SNP Memory System

1. **Verify Requirements**: Confirm task scope and run local linter.
2. **Execute Code / Note Updates**: Modify code or wiki notes.
3. **Run Tests**: Execute the bounded offline suite with sockets disabled and
   `python3 scripts/gen_index.py --check`; run marked integration tests only
   when their disposable services are in scope.
4. **Document & Summarize**: Provide clear, concise summaries of changes made.
