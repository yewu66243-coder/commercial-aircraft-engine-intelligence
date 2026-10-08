# Reference metadata repair

References are resolved by file identity, enriched from selected local PDFs, then
looked up online only if bibliographic fields remain missing. The original source
files and the full-text vector index are not modified.

Local extraction reads publication imprints, consecutive printed page numbers,
labelled DOI values, translator credits and patent front-page fields. Publication
years are not taken from article titles or narrative dates. Existing OCR text is
reused for image-only PDFs; report formatting does not launch full-document OCR.
Unsupported layouts leave fields missing rather than infer bibliographic values.

SelectedLocalPaper carries journal, year, volume, issue, pages, DOI, applicant,
publication number and publication date. Field origins are retained in each
citation_map record under metadata_provenance.

Online repair tries title/author, title/journal and journal-directory queries,
continuing after rejected or incomplete results. Each citation's lookup_attempts
records query text, candidate titles/URLs, rejection reasons, missing fields and
detail-page errors. Conflicting journal/year/DOI/patent fields are rejected.
Online title matching is independent of snippet length. Recognized metadata hosts
are queried for citation_* HTML metadata, with a second title identity check.
Responses are bounded to 2 MB and requests have timeouts; redirects are not followed.
Nonmatching pages and absent structured metadata leave missing fields unchanged.
Title suffixes containing known author names are stripped before querying.

REFERENCE_LOOKUP_ENABLED defaults to true in the service. The existing
REFERENCE_LOOKUP_MAX_REFERENCES budget applies only to online attempts.
Successful cache entries expire after REFERENCE_LOOKUP_CACHE_TTL_SECONDS
(default 30 days); no-result entries expire after 5 minutes. Legacy cache entries
are retried. Search exceptions are recorded without caching the failure.

report_quality.reference_metadata_issues contains per-reference missing fields.
Local journal articles retain [J] when a DOI is available; explicit online_first
records use [J/OL]. DOI links remain in both forms. Incomplete references set report quality to needs_review without adding repair
instructions to the report body. The citation map retains network/local extraction
errors and field provenance; runtime logs report the number of incomplete entries.
The frontend names each incomplete reference and its missing fields, and identifies
the download as a review version while keeping export available.

Verification: run tests/test_reference_metadata.py with the project's portable
Python and repository root on sys.path. Live search availability depends on the
host network; deterministic tests cover matching, structured-page extraction,
failure handling, cache expiry, field propagation and bibliography formatting.
