"""Arquivo enviado pelo cliente: como se reconhece, e como se entrega.

Duas coisas que estavam num app só e passaram a servir dois.

**Reconhecer** é ler os primeiros bytes. Extensão e ``Content-Type`` vêm do
navegador, e quem manda o arquivo manda os dois: um ``.jpg`` que na verdade é
um SVG com ``<script>`` dentro passa por qualquer checagem de nome. O que não
mente é a assinatura no começo do arquivo — e um SVG não tem nenhuma das que
estão aqui, então ele simplesmente não é reconhecido e não entra.

**Entregar** é responder com os bytes sem revelar o caminho em disco e sem
deixar o arquivo em cache de proxy compartilhado. Quem decide *se* pode
entregar é a view; esta função só sabe *como*.
"""

from django.http import FileResponse, Http404
from django.utils.text import get_valid_filename

# ---------------------------------------------------------------------------
# Reconhecer
# ---------------------------------------------------------------------------

#: Assinaturas de imagem aceitas. Confiar na extensão ou no content-type que o
#: navegador manda seria confiar no cliente.
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)

#: PDF. Fica separado das imagens de propósito: o carrinho aceita foto de
#: personalização e **só** foto — um PDF ali não é uma peça para imprimir.
PDF_SIGNATURE = b"%PDF-"


def sniff_image(header: bytes) -> tuple[str, str] | None:
    """(extensão, tipo) a partir dos primeiros bytes, ou ``None``."""
    for signature, extension, content_type in IMAGE_SIGNATURES:
        if header.startswith(signature):
            return extension, content_type
    # WEBP: "RIFF" + 4 bytes de tamanho + "WEBP"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def sniff_receipt(header: bytes) -> tuple[str, str] | None:
    """Comprovante de pagamento: imagem **ou** PDF.

    O PDF entra porque é o que o banco entrega: quem paga pelo aplicativo
    salva uma imagem, quem paga pelo internet banking baixa um PDF. Recusar um
    dos dois obrigaria o cliente a converter o arquivo antes de mandar.

    Nada além desses dois: nem SVG, nem HTML, nem arquivo de escritório. Um
    ``.svg`` não casa com assinatura nenhuma e para aqui.
    """
    if header.startswith(PDF_SIGNATURE):
        return "pdf", "application/pdf"
    return sniff_image(header)


# ---------------------------------------------------------------------------
# Entregar
# ---------------------------------------------------------------------------


def private_file_response(field, content_type: str = "", filename: str = ""):
    """Responde com os bytes de um ``FileField``, sem cache compartilhado.

    Levanta ``Http404`` quando o arquivo não abre. A linha existir no banco e o
    arquivo ter sumido do disco (restauração parcial, limpeza manual) é, para
    quem pede, a mesma coisa que não existir — e responder outra coisa contaria
    algo sobre o que existe no servidor.

    **Não** decide permissão. Quem chama já decidiu; se esta função fosse a
    porta, esquecer de trancá-la seria fácil demais.
    """
    try:
        arquivo = field.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("Arquivo não encontrado.")

    resposta = FileResponse(
        arquivo,
        content_type=content_type or "application/octet-stream",
        # O nome vem do cliente: o Django escapa, e ainda passamos pelo
        # saneador para o cabeçalho não virar veículo de nada.
        filename=get_valid_filename(filename or field.name),
    )
    # Cache só no navegador de quem tem direito, nunca em proxy compartilhado.
    resposta["Cache-Control"] = "private, max-age=0, no-store"
    return resposta


# ---------------------------------------------------------------------------
# Imagens da marca
#
# Logo, favicon e imagem padrão de produto são enviadas por quem administra a
# loja — não pelo cliente — e ficam numa pasta **pública**: elas aparecem em
# toda página, inclusive para quem não fez login.
#
# Isso muda o que precisa ser conferido. O risco aqui não é o arquivo vazar; é
# o arquivo **executar**. Um SVG é XML, e XML aceita `<script>`: servido do
# mesmo domínio da loja, ele roda com acesso ao cookie de sessão de quem
# estiver logado.
#
# Duas defesas, e as duas juntas:
#
# 1. o conteúdo é conferido (assinatura para os formatos binários, varredura
#    de texto para o SVG);
# 2. o site usa essas imagens só em `<img>` e em `<link rel="icon">`, e nesses
#    contextos o navegador não executa script dentro de SVG. A varredura existe
#    para o caso de alguém abrir o arquivo direto pela URL.
# ---------------------------------------------------------------------------

#: Formatos aceitos para logo, favicon e imagem padrão.
#:
#: `svg` está aqui porque logo é desenho vetorial: rasterizá-la para caber numa
#: lista de extensões "seguras" entregaria uma marca borrada em tela retina.
#: `ico` fica de fora — o favicon moderno é SVG ou PNG, e o formato antigo é um
#: contêiner com múltiplas imagens que não vale a pena conferir.
BRAND_IMAGE_EXTENSIONS = ("svg", "png", "webp", "avif", "jpg", "jpeg", "gif")

#: O que denuncia um SVG que faz mais do que desenhar.
#:
#: Não é uma sanitização — sanitizar XML de verdade exige um parser e uma lista
#: de permissões. É uma recusa: o arquivo com qualquer um destes trechos não
#: entra, e quem precisa de um SVG assim exporta um limpo do editor.
SVG_FORBIDDEN = (
    b"<script",
    b"<foreignobject",
    b"javascript:",
    b"<iframe",
    b"<embed",
    b"<object",
    b"onload=",
    b"onerror=",
    b"onclick=",
    b"<use href=\"http",
)


def sniff_brand_image(header: bytes) -> tuple[str, str] | None:
    """(extensão, tipo) de uma imagem de marca, ou ``None`` se não reconhecida.

    Estende o `sniff_image` com os contêineres que a marca usa e que não têm
    assinatura simples no primeiro byte: o WebP vive dentro de um RIFF e o AVIF
    dentro de um `ftyp`, os dois com a marca do formato deslocada.
    """
    reconhecida = sniff_image(header)
    if reconhecida is not None:
        return reconhecida

    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    if header[4:8] == b"ftyp" and header[8:12] in (b"avif", b"avis"):
        return "avif", "image/avif"
    # SVG é texto: não tem assinatura, só um começo reconhecível. O `<?xml` cobre
    # o arquivo com declaração; o `<svg` cobre o que o editor exporta sem ela.
    inicio = header.lstrip()[:200].lower()
    if inicio.startswith(b"<?xml") or inicio.startswith(b"<svg") or b"<svg" in inicio:
        return "svg", "image/svg+xml"
    return None


def validate_brand_image(file) -> None:
    """Confere tamanho, formato e — no SVG — o que há dentro.

    Levanta `ValidationError` com a explicação, porque quem vê a mensagem é a
    pessoa que acabou de escolher o arquivo no Admin.
    """
    from django.conf import settings
    from django.core.exceptions import ValidationError

    limite = getattr(settings, "BRAND_IMAGE_MAX_UPLOAD_SIZE", 2 * 1024 * 1024)
    if file.size and file.size > limite:
        raise ValidationError(
            f"Arquivo de {file.size // 1024} KB: o limite é "
            f"{limite // 1024} KB. Uma logo bem exportada não passa disso."
        )

    posicao = file.tell() if hasattr(file, "tell") else 0
    file.seek(0)
    cabecalho = file.read(4096)

    reconhecida = sniff_brand_image(cabecalho)
    if reconhecida is None:
        file.seek(posicao)
        raise ValidationError(
            "Não reconheci este arquivo como imagem. Aceito: "
            + ", ".join(BRAND_IMAGE_EXTENSIONS).upper()
            + "."
        )

    extensao, _tipo = reconhecida
    if extensao == "svg":
        # O SVG inteiro, não só o cabeçalho: o `<script>` pode estar no fim.
        file.seek(0)
        conteudo = file.read().lower()
        for proibido in SVG_FORBIDDEN:
            if proibido in conteudo:
                file.seek(posicao)
                raise ValidationError(
                    "Este SVG contém código executável ("
                    + proibido.decode("ascii", "replace")
                    + "). Exporte de novo pelo editor, sem script, ou envie um PNG."
                )

    file.seek(posicao)
