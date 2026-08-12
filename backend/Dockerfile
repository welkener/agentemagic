# Magic BI — backend (Django + Celery)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Leitura de documento em imagem (apps/documentos/imagem.py). São os dois únicos
# binários de sistema do projeto, e cada um paga o seu espaço:
#
#   libzbar0        ~200 KB — código de barras e QR. É o que DECODIFICA: borrão
#                   faz falhar, não faz errar. Sai daqui a maior parte do ganho.
#   tesseract-ocr   ~40 MB  — OCR. Sempre devolve algo, e por isso o resultado
#   tesseract-ocr-por ~2 MB   dele fica abaixo do limiar e não tira ninguém da
#                   fila; serve para o contador chegar com o documento já lido.
#
# O servidor é compartilhado com outros projetos do usuário: isso é disco de
# imagem, não memória residente — o tesseract só existe enquanto processa uma
# página, com teto de tempo em `imagem.SEGUNDOS_DE_OCR`.
# O nome do pacote do zbar mudou entre Debian 12 e 13 (transição t64); tenta os
# dois em vez de amarrar a build a uma versão do sistema-base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
    && (apt-get install -y --no-install-recommends libzbar0 \
        || apt-get install -y --no-install-recommends libzbar0t64) \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Comando padrão (o worker sobrescreve no docker-compose)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
