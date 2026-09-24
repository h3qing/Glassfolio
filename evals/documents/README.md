# Document evaluation set (synthetic)

Made-up statements and a fund fact sheet, rendered from the HTML sources here:

| File | Path it exercises |
| --- | --- |
| `statement.pdf` | PDF with a text layer (headless Chrome print) |
| `statement.png` | screenshot → Apple Vision OCR (Quick Look render) |
| `statement_scanned.pdf` | image-only PDF → page render → OCR (`sips`) |
| `factsheet.pdf` | fund holdings with weights |

Known answers are in `expected.json`; `glassfolio eval-model` runs them with the configured model.
