# Coding governance acceptance gate

This branch may be accepted only as `PROVISIONAL` source state unless all execution blockers are independently closed.

Acceptance requires:
- mergeability against current canonical main,
- no unexplained overlap with newer source,
- preserved request/approve/run mutation authority,
- closed source-level regression accounting,
- explicit retention of execution and CI blockers,
- no denominator PASS promotion from source presence alone.

A zero-job Actions startup failure does not falsify the source implementation and does not certify it.
