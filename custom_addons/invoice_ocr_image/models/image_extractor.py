from odoo import models
from odoo.exceptions import UserError
import io


class ImageExtractor(models.AbstractModel):
    """Service d'extraction de texte depuis les images avec PaddleOCR"""
    _name = 'invoice.ocr.image'
    _description = 'Extracteur Image avec PaddleOCR'

    def extract_text_from_image(self, file_data):
        try:
            from paddleocr import PaddleOCR
            import numpy as np
            from PIL import Image
        except ImportError:
            raise UserError("PaddleOCR n'est pas installé !")

        image = Image.open(io.BytesIO(file_data))
        image_np = np.array(image)

        ocr = PaddleOCR(use_angle_cls=True, lang='fr')

        result = ocr.ocr(image_np, cls=True)

        extracted_text = ""
        if result:
            for line in result:
                if line:
                    for word_info in line:
                        if word_info and len(word_info) >= 2:
                            extracted_text += word_info[1][0] + "\n"

        return extracted_text