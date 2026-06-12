---
name: test-coverage
description: Analyze a repo's test coverage, find untested code, write tests, and run them.
  Use when the user asks to analyze/improve test coverage, add missing tests, set up a test
  suite, or check what's untested. Detects language/framework automatically (Vitest for
  TypeScript/Next.js/React; pytest for Python). Works on the current repo.
---

You are running the /test-coverage skill. Your job: analyze the current repo, write missing
tests, fix bugs you uncover, run the suite, and report results. Work repo-by-repo if multiple
are present. Do not stop after analysis — write and run the tests.

## Workflow

### 1. Detect stack
- Check package.json (scripts, dependencies, devDependencies) for JS/TS projects
- Check for pyproject.toml, pytest.ini, setup.cfg, requirements*.txt for Python
- Check for existing test configs: vitest.config.*, jest.config.*, .coverage, pytest cache
- Note: TypeScript strictness, path aliases, Next.js App Router vs Pages Router

### 2. Inventory source vs test files
- Source files: all files under src/ (TS) or project root .py files (Python), excluding
  node_modules, .next, __pycache__, dist, build
- Test files: *.test.ts, *.spec.ts, __tests__/**/*.ts (TS) / test_*.py, *_test.py (Python)
- Report: total source files, total test files, which source files have NO tests

### 3. Prioritize gaps
Priority order for writing tests:
1. Pure/deterministic functions (no I/O, no randomness) — highest ROI, no mocking needed
2. Utility modules with filesystem/DB I/O — mock the I/O layer
3. API route handlers — mock DB/external calls
4. React/UI components — use @testing-library with jsdom
5. Integration/orchestration code — mock all sub-layers

### 4. Set up test infra (if missing)

#### TypeScript (Next.js / React):
Install: `vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @vitest/coverage-v8 vitest-mock-extended`
Create `vitest.config.ts` with react plugin, path alias matching tsconfig, environment per
project (node for API routes, jsdom for components).
Add scripts to package.json: `"test": "vitest run"`, `"test:watch": "vitest"`, `"test:coverage": "vitest run --coverage"`.

#### Python:
Add to pyproject.toml or pytest.ini: `[tool.pytest.ini_options]` with testpaths = ["tests"].
Install: `pytest pytest-cov pytest-mock`.
Create `tests/__init__.py` and `tests/conftest.py` with shared fixtures.

### 5. Write tests

- Start with pure functions — full parametrized coverage
- Mock external deps (Prisma via `vi.mock`, Gemini API via `pytest-mock`, filesystem via `vi.mock('fs')`)
- For Next.js App Router routes: import the handler directly, mock `prisma`, call with a
  mock `Request` object
- For React components: render with `@testing-library/react`, assert on text/roles/callbacks
- File naming convention: `src/__tests__/foo.test.ts` (TS) or `tests/unit/test_foo.py` (Python)

### 6. Fix bugs found
When a test reveals a real bug (crash, wrong return value, missing import, logic error):
- Fix the source file
- Document what was wrong in the test file comment
- Do NOT fix NotImplementedError stubs — those are unfinished features, leave as-is

### 7. Run and report
- Run the full test suite
- Report: total tests, passed, failed, skipped
- Show coverage summary per file (% covered)
- List remaining gaps with a priority rank for the next session

## Important notes
- Always commit test files AND any bug fixes together with a clear message
- Push to the current branch when done
- If a test would require massive refactoring to be possible (e.g. deeply tangled I/O),
  write a placeholder test with `it.todo` / `pytest.mark.skip` and a comment explaining why
- Do not generate tests that test third-party library behavior (Prisma internals, etc.) —
  only test YOUR code
