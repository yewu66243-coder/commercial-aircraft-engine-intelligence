"""Use the skill rasterizer with a Word-exported PDF on this Windows host.

Packaged render_docx.py was attempted first: soffice.exe unavailable.
Only PDF conversion and raster backend are substituted; canonical rasterize
still controls output, DPI, ordering and page naming.
"""
import importlib.util
import sys
from pathlib import Path
import pypdfium2 as pdfium

skill = Path('C:/Users/18570/.codex/plugins/cache/openai-primary-runtime/documents/26.927.11222/skills/documents')
spec = importlib.util.spec_from_file_location('render_docx', skill / 'render_docx.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
docx, pdf, out = map(Path, sys.argv[1:4])
renderer.convert_to_pdf = lambda *a, **kw: (str(pdf.resolve()), 'Word read-only PDF export')

def raster(pdf_path, dpi, output_folder, **kwargs):
    paths = []
    document = pdfium.PdfDocument(pdf_path)
    for i in range(len(document)):
        page = document[i]
        path = Path(output_folder) / f'page0001-{i+1:02d}.png'
        page.render(scale=dpi / 72).to_pil().save(path)
        paths.append(str(path))
        page.close()
    document.close()
    return paths

renderer.convert_from_path = raster
dpi = renderer.calc_dpi_via_ooxml_docx(str(docx), 1600, 2100)
print('\n'.join(renderer.rasterize(str(docx), str(out), dpi, False, False)))
