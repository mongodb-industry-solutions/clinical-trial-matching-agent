# Leafy Composition

## Intended Ownership

Leafy Hospital remains the host demo.

Clinical Trial Matching Agent becomes the reusable product for:

- trial ingestion
- evidence normalization
- patient-to-trial matching
- trial-to-patient matching
- reviewer workflows

## Composition Pattern

Leafy should embed this repo's outputs through stable APIs and compatibility React surfaces while continuing to own:

- demo navigation
- patient workflow shell
- private environment wiring

## Migration Direction

1. Trial features move here first.
2. Leafy consumes stable outputs.
3. Direct code divergence shrinks over time.
