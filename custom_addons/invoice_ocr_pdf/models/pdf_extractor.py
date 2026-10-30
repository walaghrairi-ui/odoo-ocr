from odoo import models
from odoo.exceptions import UserError


class PdfExtractor(models.AbstractModel):
    """Service d'extraction de texte depuis les fichiers PDF avec PyMuPDF"""
    _name = 'invoice.ocr.pdf'
    _description = 'Extracteur PDF avec PyMuPDF'

    def extract_text_from_pdf(self, file_data):
        """
        Extraire le texte d'un fichier PDF en utilisant PyMuPDF (fitz).
        :param file_data: contenu binaire du fichier PDF
        :return: texte extrait sous forme de chaîne
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise UserError("PyMuPDF n'est pas installé ! Exécutez : pip install pymupdf")

        extracted_text = ""
        pdf_document = fitz.open(stream=file_data, filetype="pdf")
        for page in pdf_document:
            extracted_text += page.get_text() + "\n"
        pdf_document.close()
        return extracted_text
