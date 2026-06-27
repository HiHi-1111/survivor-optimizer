# Data sources

- `source_pack/` contains the master source PDFs and untouched raw guide files.
- `extracted/text/` contains extracted text.
- `extracted/ocr/` contains OCR evidence and source manifests.
- `extracted/processed_images/` is derived and ignored by Git.
- `legacy/` preserves older manual/example source inputs.

Do not edit raw source files in place. Put verified corrections in
`knowledge/source_pack/manual_corrections.json`.
