# Artifact plan and verification

Reference: 测试方案_参考原件.docx, copied without modification from the supplied desktop document.
SHA256: ae11c759dcd7e682ba2e2fb1b4811a2ae5341954294f29c37c19d041dea0f462
Type: formal Chinese test procedure; audience: project tester and demonstration presenter.

## Design authority and inventory
One A4 portrait section, 11906 x 16838 twips. Margins top/bottom 1440,
left/right 1800 twips. Header distance 851, footer distance 992 twips.
Single column, no tables, images, headers/footers, or native numbering parts.
Font 仿宋 16 pt, fixed line spacing 28 pt; body first line 32 pt / 2 characters.
Main headings bold; narrative instructions use numbered paragraphs.
All package parts preserved byte for byte except word/document.xml and word/styles.xml.
Section properties preserved. No document relationships or media changed.

## Slots and intentional deviations
Replace hardware-specific body with project-specific test procedures.
Keep environment -> methods -> numbered procedure organization.
Use standalone sections 1, 2, 3 instead of inherited chapter 7.
Apply Word Title style to main title, maintaining template's black 16 pt font.
Bold short subsection labels and keep headings with following text for navigation.
Enable widow control for readability; no forced page breaks or decorative additions.
Use natural flow and retain template geometry as content expands.
All expected outcomes are targets, never fabricated execution results.

## Source checks
Actual front-end labels, source lifecycle, report export and evaluator thresholds
checked against current project files. No change to project implementation or old reports.

## Rendering
Packaged render_docx.py failed because soffice.exe is absent.
Fallback: isolated Word COM instance opens DOCX read-only and exports PDF;
canonical render_docx rasterize uses Word PDF plus bundled pypdfium2 renderer
through task-local render_word_pdf.py. No system Python or LibreOffice used.
Reference: 3 pages visually inspected. Final inspection recorded after rendering.

Final QA: 8 pages. Every page inspected at original resolution. Pages 1-7 of the revised render are byte-identical to the inspected first render; page 8 inspected again. Removed single-line page 9 by shortening the final record block. No clipping, overlapping text, missing glyphs, detached headings or blank tail page. Title style verified; all 14 numbered steps present; hardware-template terms absent. Reference hash unchanged and all non-document/non-styles ZIP members preserved byte for byte.
