# PDF dependency security update, 2026-11-02

The local release audit at `b86335d77d07` refused promotion after finding seven
advisories against `pypdf 6.21.2`: CVE-2026-119203 through CVE-2026-119209.
Functional tests had passed; that did not satisfy the separate security gate.

The project now pins `pypdf 6.21.4` in its runtime requirements, project metadata,
macOS build inputs, and all seven governed packaging locks. The publisher's
[6.21.4 release](https://github.com/py-pdf/pypdf/releases/tag/6.21.4) and
[PyPI distribution](https://pypi.org/project/pypdf/6.21.4/) identify the patched
release. The frozen PyPI release metadata SHA-256 is
`2f98f17abf1e2ec13768d54b32893da5fd8802eba458cd782c8377f5ce2b8851`.

Both downloaded release files were checked against the publisher's digests:

| File | Bytes | SHA-256 |
|---|---:|---|
| `pypdf-6.21.4-py3-none-any.whl` | 410430 | `33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c` |
| `pypdf-6.21.4.tar.gz` | 7109255 | `c8817e3f888d47a087872ded69812c1ef30cacb871d6e053e0c00bbaf0bc2163` |

Lock changes are a targeted package/hash substitution. Comparing complete
package stanzas before and after shows only `pypdf` changed in each lock. The
global resolver horizon remains frozen; the existing package-specific security
exception moves only pypdf's version and cutoff to include the patch. The macOS
lock's generator comment records that narrow cutoff change.

An isolated Python environment installed the frozen wheel without dependencies
or index access. PDF parsing, PDF OCR, ingestion-safety, and one-click tests
passed 101 cases with six existing environment-dependent skips. Audits of the
backend requirements and both macOS runtime locks returned no known
vulnerabilities at the time of this update. Those advisory results are dated,
not a permanent claim of vulnerability freedom. Run the complete release gate
after integration and preserve its exact-source result.

For future updates, freeze publisher metadata and release bytes once, verify
their hashes, compare package-level lock differences, retain unrelated pins,
run representative parsing and adversarial checks with the actual patched
runtime, and rerun every affected advisory gate. A clean source test suite does
not adjudicate known vulnerable dependency versions.
