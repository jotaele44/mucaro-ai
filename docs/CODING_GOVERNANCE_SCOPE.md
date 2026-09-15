# Bounded coding governance scope

This increment is intentionally limited to source-level governance contracts that can be verified without pretending that untrusted repository commands have executed.

Included:
- approved patch transaction authority,
- read-only isolated check planning,
- review evidence binding,
- regression manifest,
- blocker and queue ledgers.

Excluded:
- arbitrary command execution,
- writable container source mounts,
- live test/build certification,
- branch/commit/PR mutation,
- whole-product release certification.

The bounded scope may be merged as `PROVISIONAL` source state even when hosted CI remains startup-blocked before jobs, provided the blocker remains explicit and no PASS promotion is claimed from that condition.
