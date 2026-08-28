# -*- coding: utf-8 -*-
"""O conteúdo inicial das quatro páginas, nos quatro idiomas, e a coluna do rodapé.

Por que numa migration, e não num comando que alguém precisa lembrar de rodar:
a loja não pode subir com quatro páginas em branco. O texto aqui é o ponto de
partida — o administrador reescreve pelo Admin, e a migration não volta para
apagar o que ele escreveu (ela roda uma vez).

O que **não** está escrito aqui: prazo em dias, prazo legal de desistência,
percentual de desconto de revenda, país específico de entrega. Nada disso está
definido no projeto, e inventar número em página institucional é criar
compromisso que a loja não assumiu. O texto descreve o processo e manda
confirmar o resto com a equipe.

A coluna do rodapé aponta para as páginas por **referência** (`FooterLink.page`),
não por endereço: é o que faz o link do visitante francês ir para `/fr/...`.
Os links não têm rótulo próprio de propósito — sem ele, o texto do link é o
título da página, então traduzir a página traduz o rodapé junto.
"""

from django.db import migrations

COLUNA = "paginas-institucionais"

TITULOS_DA_COLUNA = {
    "pt": "Informações",
    "fr": "Informations",
    "nl": "Informatie",
    "en": "Information",
}

PAGINAS = [
    {
        "slug": "envios-e-prazos",
        "sort_order": 1,
        "textos": {
            "pt": {
                "title": "Envios e prazos",
                "intro": (
                    "Cada peça é impressa sob encomenda. O prazo até a entrega tem duas "
                    "partes: o tempo de produção e o tempo de transporte."
                ),
                "body": (
                    "# Produção\n"
                    "A impressão começa depois da confirmação do pagamento. O tempo de "
                    "produção de cada peça está indicado na página do produto e é repetido "
                    "no resumo do pedido.\n"
                    "\n"
                    "# Preparação\n"
                    "Terminada a impressão, a peça passa por conferência, acabamento e "
                    "embalagem antes de ser despachada.\n"
                    "\n"
                    "# Transporte\n"
                    "O método de entrega e o prazo estimado aparecem no checkout, calculados "
                    "a partir do destino e do peso do pedido.\n"
                    "\n"
                    "# Destinos\n"
                    "Enviamos da Bélgica. Os países atendidos são os que aparecem na lista de "
                    "entrega do checkout.\n"
                    "\n"
                    "# Rastreamento\n"
                    "Quando o pedido é despachado, você recebe um e-mail com a transportadora "
                    "e o código de rastreio.\n"
                    "\n"
                    "# Variações de prazo\n"
                    "Um pedido com vários itens segue o prazo do item mais demorado. "
                    "Personalizações, feriados e períodos de grande procura podem alterar o "
                    "prazo — se isso acontecer, avisamos por e-mail."
                ),
                "meta_description": (
                    "Como funcionam a produção, o envio e o rastreamento dos pedidos."
                ),
            },
            "fr": {
                "title": "Livraisons et délais",
                "intro": (
                    "Chaque pièce est imprimée à la commande. Le délai jusqu'à la livraison "
                    "comprend deux parties : le temps de production et le temps de transport."
                ),
                "body": (
                    "# Production\n"
                    "L'impression commence après la confirmation du paiement. Le temps de "
                    "production de chaque pièce est indiqué sur la page du produit et repris "
                    "dans le récapitulatif de la commande.\n"
                    "\n"
                    "# Préparation\n"
                    "Une fois l'impression terminée, la pièce est contrôlée, finie et "
                    "emballée avant l'expédition.\n"
                    "\n"
                    "# Transport\n"
                    "Le mode de livraison et le délai estimé apparaissent au moment du "
                    "paiement, calculés selon la destination et le poids de la commande.\n"
                    "\n"
                    "# Destinations\n"
                    "Nous expédions depuis la Belgique. Les pays desservis sont ceux qui "
                    "apparaissent dans la liste de livraison lors du paiement.\n"
                    "\n"
                    "# Suivi\n"
                    "Dès l'expédition, vous recevez un e-mail avec le transporteur et le "
                    "numéro de suivi.\n"
                    "\n"
                    "# Variations de délai\n"
                    "Une commande de plusieurs articles suit le délai de l'article le plus "
                    "long. Les personnalisations, les jours fériés et les périodes de forte "
                    "demande peuvent modifier le délai — le cas échéant, nous vous "
                    "prévenons par e-mail."
                ),
                "meta_description": (
                    "Production, expédition et suivi des commandes : comment cela fonctionne."
                ),
            },
            "nl": {
                "title": "Verzending en levertijden",
                "intro": (
                    "Elk stuk wordt op bestelling geprint. De levertijd bestaat uit twee "
                    "delen: de productietijd en de transporttijd."
                ),
                "body": (
                    "# Productie\n"
                    "Het printen start na bevestiging van de betaling. De productietijd van "
                    "elk stuk staat op de productpagina en wordt herhaald in het overzicht "
                    "van de bestelling.\n"
                    "\n"
                    "# Voorbereiding\n"
                    "Na het printen wordt het stuk gecontroleerd, afgewerkt en verpakt voor "
                    "verzending.\n"
                    "\n"
                    "# Transport\n"
                    "De leveringswijze en de geschatte levertijd verschijnen bij het "
                    "afrekenen, berekend op basis van bestemming en gewicht.\n"
                    "\n"
                    "# Bestemmingen\n"
                    "We verzenden vanuit België. De landen waarheen we leveren staan in de "
                    "leveringslijst bij het afrekenen.\n"
                    "\n"
                    "# Tracering\n"
                    "Zodra de bestelling verzonden is, ontvangt u een e-mail met de "
                    "vervoerder en de trackingcode.\n"
                    "\n"
                    "# Afwijkende levertijden\n"
                    "Een bestelling met meerdere artikelen volgt de langste productietijd. "
                    "Personalisaties, feestdagen en drukke periodes kunnen de levertijd "
                    "wijzigen — gebeurt dat, dan laten we het per e-mail weten."
                ),
                "meta_description": (
                    "Hoe productie, verzending en tracering van de bestellingen werken."
                ),
            },
            "en": {
                "title": "Shipping and lead times",
                "intro": (
                    "Every piece is printed to order. The time until delivery has two parts: "
                    "production time and shipping time."
                ),
                "body": (
                    "# Production\n"
                    "Printing starts once the payment is confirmed. The production time for "
                    "each piece is shown on the product page and repeated in the order "
                    "summary.\n"
                    "\n"
                    "# Preparation\n"
                    "Once printed, the piece is checked, finished and packed before it is "
                    "shipped.\n"
                    "\n"
                    "# Shipping\n"
                    "The delivery method and the estimated time appear at checkout, based on "
                    "the destination and the weight of the order.\n"
                    "\n"
                    "# Destinations\n"
                    "We ship from Belgium. The countries we serve are the ones listed in the "
                    "delivery options at checkout.\n"
                    "\n"
                    "# Tracking\n"
                    "When the order ships, you receive an email with the carrier and the "
                    "tracking number.\n"
                    "\n"
                    "# Changes to lead times\n"
                    "An order with several items follows the longest production time. "
                    "Customisations, public holidays and busy periods can change the lead "
                    "time — if that happens, we let you know by email."
                ),
                "meta_description": (
                    "How production, shipping and order tracking work."
                ),
            },
        },
    },
    {
        "slug": "trocas-e-devolucoes",
        "sort_order": 2,
        "textos": {
            "pt": {
                "title": "Trocas e devoluções",
                "intro": (
                    "Se alguma coisa não estiver certa com o seu pedido, fale com a gente "
                    "antes de devolver — assim resolvemos mais rápido."
                ),
                "body": (
                    "# Como solicitar\n"
                    "Escreva para nós pela página de contato, informando o número do pedido e "
                    "o que aconteceu. Respondemos com as instruções do seu caso.\n"
                    "\n"
                    "# Produto incorreto ou danificado\n"
                    "Envie fotos junto com a mensagem. Sendo erro nosso ou dano no "
                    "transporte, resolvemos com reenvio ou reembolso, e o retorno não fica "
                    "por sua conta.\n"
                    "\n"
                    "# Condições gerais\n"
                    "O produto precisa estar completo e sem sinais de uso. Cada pedido é "
                    "analisado caso a caso, e confirmamos por escrito as condições que se "
                    "aplicam antes de qualquer devolução.\n"
                    "\n"
                    "# Produtos personalizados ou sob encomenda\n"
                    "Peças feitas a partir do seu arquivo ou com uma personalização "
                    "escolhida por você são produzidas exclusivamente para o seu pedido. "
                    "Nesses casos a troca por arrependimento é analisada caso a caso — "
                    "defeito de produção é sempre coberto."
                ),
                "meta_description": (
                    "Como pedir uma troca ou devolução e o que acontece em cada situação."
                ),
            },
            "fr": {
                "title": "Échanges et retours",
                "intro": (
                    "Si quelque chose ne va pas avec votre commande, contactez-nous avant de "
                    "renvoyer le colis — nous réglons cela plus vite."
                ),
                "body": (
                    "# Comment faire la demande\n"
                    "Écrivez-nous via la page de contact en indiquant le numéro de commande "
                    "et ce qui s'est passé. Nous répondons avec la marche à suivre.\n"
                    "\n"
                    "# Produit erroné ou endommagé\n"
                    "Joignez des photos à votre message. S'il s'agit d'une erreur de notre "
                    "part ou d'un dommage pendant le transport, nous réglons cela par un "
                    "renvoi ou un remboursement, et le retour n'est pas à votre charge.\n"
                    "\n"
                    "# Conditions générales\n"
                    "Le produit doit être complet et sans trace d'utilisation. Chaque demande "
                    "est examinée au cas par cas, et nous confirmons par écrit les conditions "
                    "applicables avant tout retour.\n"
                    "\n"
                    "# Produits personnalisés ou fabriqués sur commande\n"
                    "Les pièces réalisées à partir de votre fichier ou avec une "
                    "personnalisation de votre choix sont produites uniquement pour votre "
                    "commande. Dans ces cas, l'échange pour changement d'avis est examiné au "
                    "cas par cas — un défaut de fabrication est toujours couvert."
                ),
                "meta_description": (
                    "Comment demander un échange ou un retour, et ce qui se passe ensuite."
                ),
            },
            "nl": {
                "title": "Ruilen en retourneren",
                "intro": (
                    "Klopt er iets niet met uw bestelling? Neem contact op vóór u iets "
                    "terugstuurt — zo lossen we het sneller op."
                ),
                "body": (
                    "# Hoe aanvragen\n"
                    "Schrijf ons via de contactpagina, met het bestelnummer en wat er is "
                    "gebeurd. We antwoorden met de stappen voor uw situatie.\n"
                    "\n"
                    "# Verkeerd of beschadigd product\n"
                    "Stuur foto's mee met uw bericht. Gaat het om onze fout of om schade "
                    "tijdens het transport, dan lossen we het op met een nieuwe zending of "
                    "een terugbetaling, en de retour is niet voor uw rekening.\n"
                    "\n"
                    "# Algemene voorwaarden\n"
                    "Het product moet compleet zijn en geen gebruikssporen vertonen. Elke "
                    "aanvraag wordt afzonderlijk bekeken, en we bevestigen schriftelijk welke "
                    "voorwaarden gelden vóór een retour.\n"
                    "\n"
                    "# Gepersonaliseerde producten en maatwerk\n"
                    "Stukken die op basis van uw bestand of met een door u gekozen "
                    "personalisatie worden gemaakt, worden uitsluitend voor uw bestelling "
                    "geproduceerd. In die gevallen wordt ruilen bij bedenktijd afzonderlijk "
                    "bekeken — een productiefout is altijd gedekt."
                ),
                "meta_description": (
                    "Hoe u een ruil of retour aanvraagt en wat er in elke situatie gebeurt."
                ),
            },
            "en": {
                "title": "Returns and exchanges",
                "intro": (
                    "If something isn't right with your order, talk to us before sending it "
                    "back — that way we sort it out faster."
                ),
                "body": (
                    "# How to ask\n"
                    "Write to us through the contact page with your order number and what "
                    "happened. We reply with the steps for your case.\n"
                    "\n"
                    "# Wrong or damaged item\n"
                    "Attach photos to your message. If it was our mistake or damage in "
                    "transit, we sort it out with a replacement or a refund, and the return "
                    "is not at your cost.\n"
                    "\n"
                    "# General conditions\n"
                    "The item must be complete and show no signs of use. Every request is "
                    "reviewed case by case, and we confirm the applicable conditions in "
                    "writing before any return.\n"
                    "\n"
                    "# Personalised or made-to-order items\n"
                    "Pieces made from your file or with a customisation you chose are "
                    "produced only for your order. In those cases, an exchange because you "
                    "changed your mind is reviewed case by case — a manufacturing defect is "
                    "always covered."
                ),
                "meta_description": (
                    "How to request a return or exchange, and what happens in each case."
                ),
            },
        },
    },
    {
        "slug": "contato",
        "sort_order": 3,
        "textos": {
            "pt": {
                "title": "Contato",
                "intro": (
                    "Fale com a gente sobre um pedido, um orçamento ou uma dúvida. "
                    "Respondemos por e-mail."
                ),
                "body": (
                    "# Como falar com a gente\n"
                    "Use o formulário abaixo: ele chega direto para a nossa equipe, e a "
                    "resposta vai para o e-mail que você informar.\n"
                    "\n"
                    "# O que ajuda a responder mais rápido\n"
                    "Se a mensagem for sobre um pedido, informe o número dele. Se for sobre "
                    "um produto, diga o nome e, quando ajudar, envie fotos.\n"
                    "\n"
                    "# Revenda e parcerias\n"
                    "É o mesmo canal. Conte um pouco do seu negócio na mensagem e "
                    "respondemos por aqui."
                ),
                "meta_description": "Fale com a JD PRINT sobre um pedido, um orçamento ou uma dúvida.",
            },
            "fr": {
                "title": "Contact",
                "intro": (
                    "Écrivez-nous au sujet d'une commande, d'un devis ou d'une question. "
                    "Nous répondons par e-mail."
                ),
                "body": (
                    "# Comment nous joindre\n"
                    "Utilisez le formulaire ci-dessous : il arrive directement chez notre "
                    "équipe, et la réponse part vers l'adresse que vous indiquez.\n"
                    "\n"
                    "# Ce qui accélère la réponse\n"
                    "S'il s'agit d'une commande, indiquez son numéro. S'il s'agit d'un "
                    "produit, donnez son nom et, si cela aide, joignez des photos.\n"
                    "\n"
                    "# Revente et partenariats\n"
                    "C'est le même canal. Décrivez brièvement votre activité dans le "
                    "message et nous vous répondons ici."
                ),
                "meta_description": "Contactez JD PRINT au sujet d'une commande, d'un devis ou d'une question.",
            },
            "nl": {
                "title": "Contact",
                "intro": (
                    "Schrijf ons over een bestelling, een offerte of een vraag. We "
                    "antwoorden per e-mail."
                ),
                "body": (
                    "# Hoe u ons bereikt\n"
                    "Gebruik het formulier hieronder: het komt rechtstreeks bij ons team "
                    "terecht, en het antwoord gaat naar het e-mailadres dat u opgeeft.\n"
                    "\n"
                    "# Wat een snel antwoord helpt\n"
                    "Gaat het over een bestelling, vermeld dan het bestelnummer. Gaat het "
                    "over een product, noem de naam en stuur waar nuttig foto's mee.\n"
                    "\n"
                    "# Wederverkoop en samenwerking\n"
                    "Zelfde kanaal. Vertel kort iets over uw onderneming in het bericht en "
                    "we antwoorden hier."
                ),
                "meta_description": "Neem contact op met JD PRINT over een bestelling, een offerte of een vraag.",
            },
            "en": {
                "title": "Contact",
                "intro": (
                    "Write to us about an order, a quote or a question. We reply by email."
                ),
                "body": (
                    "# How to reach us\n"
                    "Use the form below: it goes straight to our team, and the reply goes to "
                    "the address you give us.\n"
                    "\n"
                    "# What helps us answer faster\n"
                    "If it's about an order, include the order number. If it's about a "
                    "product, give its name and, where it helps, attach photos.\n"
                    "\n"
                    "# Reselling and partnerships\n"
                    "Same channel. Tell us a little about your business in the message and "
                    "we'll reply here."
                ),
                "meta_description": "Get in touch with JD PRINT about an order, a quote or a question.",
            },
        },
    },
    {
        "slug": "revenda",
        "sort_order": 4,
        "textos": {
            "pt": {
                "title": "Seja um revendedor",
                "intro": (
                    "Trabalhamos com lojas, ateliês e profissionais que queiram levar as "
                    "nossas peças para os seus próprios clientes."
                ),
                "body": (
                    "# O programa\n"
                    "Produzimos por impressão 3D, sob encomenda. Isso permite trabalhar com "
                    "um catálogo enxuto e repor sem precisar de estoque parado. A revenda é "
                    "uma conversa direta com a nossa equipe.\n"
                    "\n"
                    "# Para quem é\n"
                    "Lojas físicas, lojas online, ateliês, estúdios de design e "
                    "profissionais que já vendem para um público próprio.\n"
                    "\n"
                    "# Condições\n"
                    "Volume, prazos e condições comerciais são analisados caso a caso. Não "
                    "trabalhamos com uma tabela única: o que faz sentido para uma loja de "
                    "bairro não é o que faz sentido para um distribuidor.\n"
                    "\n"
                    "# Como começar\n"
                    "Fale com a gente e conte quem você é, o que vende e que tipo de peça "
                    "pretende revender. Respondemos com os próximos passos."
                ),
                "meta_description": (
                    "Revenda as peças da JD PRINT na sua loja. Condições analisadas caso a caso."
                ),
            },
            "fr": {
                "title": "Devenez revendeur",
                "intro": (
                    "Nous travaillons avec des boutiques, des ateliers et des professionnels "
                    "qui souhaitent proposer nos pièces à leurs propres clients."
                ),
                "body": (
                    "# Le programme\n"
                    "Nous produisons par impression 3D, à la commande. Cela permet de "
                    "travailler avec un catalogue restreint et de réapprovisionner sans "
                    "stock dormant. La revente se décide dans un échange direct avec notre "
                    "équipe.\n"
                    "\n"
                    "# À qui cela s'adresse\n"
                    "Boutiques, boutiques en ligne, ateliers, studios de design et "
                    "professionnels qui vendent déjà à leur propre public.\n"
                    "\n"
                    "# Conditions\n"
                    "Volumes, délais et conditions commerciales sont étudiés au cas par cas. "
                    "Nous n'appliquons pas un barème unique : ce qui convient à une boutique "
                    "de quartier ne convient pas à un distributeur.\n"
                    "\n"
                    "# Pour commencer\n"
                    "Contactez-nous et dites-nous qui vous êtes, ce que vous vendez et quel "
                    "type de pièces vous souhaitez revendre. Nous répondons avec les "
                    "prochaines étapes."
                ),
                "meta_description": (
                    "Revendez les pièces JD PRINT dans votre boutique. Conditions étudiées "
                    "au cas par cas."
                ),
            },
            "nl": {
                "title": "Word wederverkoper",
                "intro": (
                    "We werken samen met winkels, ateliers en professionals die onze stukken "
                    "aan hun eigen klanten willen aanbieden."
                ),
                "body": (
                    "# Het programma\n"
                    "We produceren met 3D-printen, op bestelling. Zo kunt u met een compact "
                    "assortiment werken en bijbestellen zonder stilliggende voorraad. "
                    "Wederverkoop begint met een gesprek met ons team.\n"
                    "\n"
                    "# Voor wie\n"
                    "Winkels, webshops, ateliers, designstudio's en professionals die al aan "
                    "een eigen publiek verkopen.\n"
                    "\n"
                    "# Voorwaarden\n"
                    "Volume, termijnen en commerciële voorwaarden bekijken we per geval. We "
                    "hanteren geen vaste tabel: wat past bij een buurtwinkel, past niet bij "
                    "een distributeur.\n"
                    "\n"
                    "# Zo begint u\n"
                    "Neem contact op en vertel wie u bent, wat u verkoopt en welke stukken u "
                    "wilt aanbieden. We antwoorden met de volgende stappen."
                ),
                "meta_description": (
                    "Verkoop de stukken van JD PRINT in uw winkel. Voorwaarden per geval bekeken."
                ),
            },
            "en": {
                "title": "Become a reseller",
                "intro": (
                    "We work with shops, studios and professionals who want to offer our "
                    "pieces to their own customers."
                ),
                "body": (
                    "# The programme\n"
                    "We produce by 3D printing, to order. That lets you work with a lean "
                    "range and restock without dead stock sitting on a shelf. Reselling "
                    "starts as a direct conversation with our team.\n"
                    "\n"
                    "# Who it's for\n"
                    "Shops, online shops, studios, design practices and professionals who "
                    "already sell to an audience of their own.\n"
                    "\n"
                    "# Terms\n"
                    "Volume, lead times and commercial terms are looked at case by case. We "
                    "don't run a single price list: what suits a neighbourhood shop doesn't "
                    "suit a distributor.\n"
                    "\n"
                    "# Getting started\n"
                    "Get in touch and tell us who you are, what you sell and what kind of "
                    "pieces you'd like to offer. We reply with the next steps."
                ),
                "meta_description": (
                    "Resell JD PRINT pieces in your shop. Terms looked at case by case."
                ),
            },
        },
    },
]


def cadastrar(apps, schema_editor):
    InstitutionalPage = apps.get_model("storefront", "InstitutionalPage")
    InstitutionalPageTranslation = apps.get_model("storefront", "InstitutionalPageTranslation")
    FooterColumn = apps.get_model("storefront", "FooterColumn")
    FooterColumnTranslation = apps.get_model("storefront", "FooterColumnTranslation")
    FooterLink = apps.get_model("storefront", "FooterLink")

    paginas = {}
    for dados in PAGINAS:
        pagina, criada = InstitutionalPage.objects.get_or_create(
            slug=dados["slug"],
            defaults={"is_active": True, "show_in_footer": True, "sort_order": dados["sort_order"]},
        )
        paginas[dados["slug"]] = pagina
        if not criada:
            # Alguém já cadastrou esta página à mão: o texto dele manda.
            continue
        for idioma, textos in dados["textos"].items():
            InstitutionalPageTranslation.objects.create(
                master=pagina, language=idioma, **textos
            )

    # A coluna do rodapé só é criada se ainda não houver uma apontando para as
    # páginas — instalar duas colunas iguais seria pior que nenhuma.
    if FooterLink.objects.filter(page__isnull=False).exists():
        return

    coluna, criada = FooterColumn.objects.get_or_create(
        internal_name=COLUNA, defaults={"is_active": True, "sort_order": 10}
    )
    if criada:
        for idioma, titulo in TITULOS_DA_COLUNA.items():
            FooterColumnTranslation.objects.create(master=coluna, language=idioma, title=titulo)

    for ordem, dados in enumerate(PAGINAS, start=1):
        FooterLink.objects.get_or_create(
            column=coluna,
            page=paginas[dados["slug"]],
            defaults={"is_active": True, "sort_order": ordem, "url": ""},
        )


def apagar(apps, schema_editor):
    """Desfaz o que esta migration cadastrou, e só isso."""
    InstitutionalPage = apps.get_model("storefront", "InstitutionalPage")
    FooterColumn = apps.get_model("storefront", "FooterColumn")

    FooterColumn.objects.filter(internal_name=COLUNA).delete()
    InstitutionalPage.objects.filter(slug__in=[d["slug"] for d in PAGINAS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("storefront", "0004_remove_resellerapplication_reseller_handled_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(cadastrar, apagar),
    ]
