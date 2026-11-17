"""PDFs para impressão: DANFEs (A4, um por página) e etiquetas 10x15 com o resumo do ML no fim."""
import io

from pypdf import PdfReader, PdfWriter

LOTE_ETIQUETAS = 50  # máximo de shipment_ids por chamada


def gerar_danfes(ml, invoice_ids, destino):
    """Junta os DANFEs das notas em destino. Devolve quantos entraram."""
    writer = PdfWriter()
    vistos = []
    for inv_id in invoice_ids:
        if inv_id in vistos:
            continue
        nota = ml.get(f'/users/{ml.uid}/invoices/{inv_id}')
        pdf, _ = ml.get_pdf(nota['attributes']['danfe_location'])
        writer.append(PdfReader(io.BytesIO(pdf)))
        vistos.append(inv_id)
    if vistos:
        with open(destino, 'wb') as f:
            writer.write(f)
    return len(vistos)


def _eh_etiqueta(pagina):
    """Etiqueta é 10x15 cm (~283x425 pt); o resumo vem em A4."""
    return max(float(pagina.mediabox.width), float(pagina.mediabox.height)) < 500


def gerar_etiquetas(ml, envio_ids, pasta):
    """Grava as etiquetas em pasta, com o nome dado pelo ML. Devolve (nome do arquivo, etiquetas)."""
    writer = PdfWriter()
    resumo = []
    nome = None
    envio_ids = list(dict.fromkeys(envio_ids))
    for i in range(0, len(envio_ids), LOTE_ETIQUETAS):
        pdf, sugerido = ml.get_pdf('/shipment_labels', params={
            'shipment_ids': ','.join(map(str, envio_ids[i:i + LOTE_ETIQUETAS])), 'response_type': 'pdf'})
        nome = nome or sugerido
        for pagina in PdfReader(io.BytesIO(pdf)).pages:
            if _eh_etiqueta(pagina):
                writer.add_page(pagina)
            else:
                resumo.append(pagina)
    etiquetas = len(writer.pages)
    # Cada lote traz seu resumo: todos vão para o fim, como no painel.
    for pagina in resumo:
        writer.add_page(pagina)
    nome = nome or 'labels.pdf'
    with open(pasta / nome, 'wb') as f:
        writer.write(f)
    return nome, etiquetas
