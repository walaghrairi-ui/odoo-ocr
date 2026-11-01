# =====================================================================
#  Image Odoo 19 + modules Invoice OCR (PyMuPDF, PaddleOCR, Groq)
# =====================================================================
FROM odoo:19.0

USER root

# Bibliotheques systeme necessaires a OpenCV / PaddleOCR
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0t64 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependances Python des modules.
# On fige d'abord les versions deja installees pour Odoo (Pillow, lxml,
# requests...) et on les passe en contraintes a pip : les nouveaux paquets
# s'adaptent a Odoo, jamais l'inverse.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip freeze --all 2>/dev/null \
        | grep '==' \
        | grep -viE '^(pip|setuptools|wheel)==' > /tmp/odoo-constraints.txt \
    && pip3 install --no-cache-dir --break-system-packages --prefer-binary \
        -c /tmp/odoo-constraints.txt \
        -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt /tmp/odoo-constraints.txt

# Modeles PaddleOCR telecharges des la construction de l'image.
# Sans cette etape, PaddleOCR les telecharge a la premiere lecture d'une
# image, dans le dossier temporaire du conteneur : la saisie devient tres
# lente, le conteneur cesse de repondre a Azure et il est redemarre.
# /opt/paddleocr est hors du volume /var/lib/odoo, donc conserve dans l'image.
# Memes parametres que le module invoice_ocr_image : memes modeles.
ENV PADDLE_OCR_BASE_DIR=/opt/paddleocr/
RUN mkdir -p /opt/paddleocr && chown odoo:odoo /opt/paddleocr
USER odoo
RUN python3 -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='fr')" \
    && find /opt/paddleocr -name '*.pdmodel' | sort
USER root

# Script de demarrage : injecte le mot de passe maitre fourni par Azure.
# sed retire d'eventuels retours chariot Windows (CRLF) qui casseraient bash.
COPY docker/odoo-entrypoint.sh /usr/local/bin/odoo-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/odoo-entrypoint.sh \
    && chmod 755 /usr/local/bin/odoo-entrypoint.sh

# Configuration Odoo et modules personnalises
COPY --chown=odoo:odoo config/odoo.conf /etc/odoo/odoo.conf
COPY --chown=odoo:odoo custom_addons /mnt/extra-addons

USER odoo

ENTRYPOINT ["/usr/local/bin/odoo-entrypoint.sh"]
CMD ["odoo"]
