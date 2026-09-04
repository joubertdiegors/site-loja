"""Dados de demonstração para ver a Home funcionando.

Este comando NÃO é parte do sistema: serve para uma instalação nova ter o que
mostrar. Regras que ele respeita:

* nunca sobrescreve nem apaga nada que já exista (tudo é ``get_or_create``);
* produtos de demonstração usam SKU com o prefixo ``DEMO-``, para você
  identificar e remover depois (``--remover``);
* não cria imagens nem fotos de produto — a Home tem espaço reservado para
  isso e nenhuma foto é inventada;
* não cria vendas, avaliações ou estoque fictício além do necessário para os
  cards mostrarem estados diferentes (em estoque / sob encomenda);
* recusa-se a rodar com ``DEBUG=False`` sem o parâmetro ``--forcar``.

Uso::

    python manage.py seed_demo_data
    python manage.py seed_demo_data --remover
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import (
    Brand,
    Color,
    Material,
    PricingMode,
    Product,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.categories.models import Category, CategoryTranslation
from apps.core.models import DeliveryCountry, SiteLanguage
from apps.home.models import (
    CtaTarget,
    HomeBanner,
    HomeBannerTranslation,
    HomeCallout,
    HomeCalloutTranslation,
    HomeCard,
    HomeCardTranslation,
    HomeSection,
    HomeSectionLayout,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
)
from apps.shipping.models import ShippingCarrier, ShippingMethod, ShippingRate
from apps.storefront.models import (
    FooterColumn,
    FooterColumnTranslation,
    FooterLink,
    FooterLinkTranslation,
    FooterSettings,
    FooterSettingsTranslation,
    TopBarItem,
    TopBarItemTranslation,
)

DEMO_PREFIX = "DEMO-"

#: Os idiomas que a loja oferece hoje. Já existem na migration de dados do
#: `core`; aqui só garantimos que estão lá e ativos numa instalação nova.
#: `defaults` só age na criação — idioma que o administrador desativou fica
#: desativado.
LANGUAGES = [("pt-br", 0), ("fr", 1), ("nl", 2), ("en", 3)]

#: País de entrega mínimo para a loja funcionar (a sede).
COUNTRIES = [("BE", "Bélgica", "21.00")]

#: Marcas de filamento. Só o nome: o resto é o administrador quem preenche.
BRANDS = ["Prusament", "Polymaker", "Sunlu", "Eryone"]

#: Transportadoras usadas na Bélgica.
CARRIERS = [
    ("bpost", "BPost", 0, "https://track.bpost.be/btr/web/#/search?itemCode={code}"),
    ("dpd", "DPD", 1, "https://tracking.dpd.de/status/pt_PT/parcel/{code}"),
]

#: Métodos de entrega: (código, transportadora, nome, descrição, min, max, ordem).
SHIPPING_METHODS = [
    ("bpost-padrao", "bpost", "Correio padrão", "Entrega em casa, sem hora marcada.", 2, 4, 0),
    ("bpost-ponto", "bpost", "Ponto de retirada", "Você retira num ponto BPost perto de casa.", 2, 5, 1),
    ("dpd-expresso", "dpd", "Expresso DPD", "Entrega rápida, com rastreio.", 1, 2, 2),
]

#: Tarifas por faixa de peso, em gramas: (método, país, min, max, preço).
SHIPPING_RATES = [
    ("bpost-padrao", "BE", 0, 2000, "4.90"),
    ("bpost-padrao", "BE", 2001, 10000, "7.90"),
    ("bpost-ponto", "BE", 0, 2000, "3.90"),
    ("bpost-ponto", "BE", 2001, 10000, "6.50"),
    ("dpd-expresso", "BE", 0, 2000, "9.90"),
    ("dpd-expresso", "BE", 2001, 10000, "13.90"),
]

#: Os três cards abaixo do banner: (nome interno, ícone, cor, ordem, textos).
HOME_CARDS = [
    (
        "producao-propria", "cube", "brand", 0,
        {
            "pt": ("Produção própria", "Impressão feita por nós, com controle de camada e acabamento."),
            "fr": ("Production maison", "Impression réalisée par nos soins, du calibrage à la finition."),
            "nl": ("Eigen productie", "Wij printen zelf, met controle over laag en afwerking."),
            "en": ("Made in-house", "We print it ourselves, from layer height to finish."),
        },
    ),
    (
        "cores-materiais", "palette", "mint", 1,
        {
            "pt": ("Cores e materiais à escolha", "Cada peça sai na cor e no material que você escolher."),
            "fr": ("Couleurs et matériaux au choix", "Chaque pièce sort dans la couleur et la matière que vous choisissez."),
            "nl": ("Kleur en materiaal naar keuze", "Elk stuk komt in de kleur en het materiaal dat u kiest."),
            "en": ("Your colour, your material", "Every piece comes in the colour and material you pick."),
        },
    ),
    (
        "personalizacao", "sparkles", "coral", 2,
        {
            "pt": ("Personalização real", "Foto ou texto aplicados na peça antes da impressão."),
            "fr": ("Personnalisation réelle", "Photo ou texte appliqués sur la pièce avant l'impression."),
            "nl": ("Echte personalisatie", "Foto of tekst wordt vóór het printen op het stuk aangebracht."),
            "en": ("Real customisation", "Photo or text applied to the piece before printing."),
        },
    ),
]

#: A chamada final da Home, nos quatro idiomas.
HOME_CALLOUT = {
    "pt": ("Personalização", "Tem uma ideia? A gente imprime.",
           "Cores, tamanhos e materiais à sua escolha — e projetos sob encomenda para o que não existe na loja.",
           "Ver produtos"),
    "fr": ("Personnalisation", "Une idée ? Nous l'imprimons.",
           "Couleurs, tailles et matériaux au choix — et des projets sur mesure pour ce qui n'existe pas en boutique.",
           "Voir les produits"),
    "nl": ("Personalisatie", "Hebt u een idee? Wij printen het.",
           "Kleuren, maten en materialen naar keuze — en maatwerk voor wat niet in de winkel staat.",
           "Bekijk de producten"),
    "en": ("Customisation", "Got an idea? We print it.",
           "Colours, sizes and materials of your choice — and made-to-order projects for what the shop doesn't carry.",
           "See the products"),
}

#: A faixa do topo: (nome interno, ícone, ordem, textos).
TOP_BAR = [
    ("producao", "cube", 0, {
        "pt": "Produção própria na Bélgica",
        "fr": "Production maison en Belgique",
        "nl": "Eigen productie in België",
        "en": "Made in-house in Belgium",
    }),
    ("personalizacao", "sparkles", 1, {
        "pt": "Peças personalizadas sob encomenda",
        "fr": "Pièces personnalisées sur commande",
        "nl": "Gepersonaliseerde stukken op bestelling",
        "en": "Personalised pieces made to order",
    }),
    ("envio", "truck", 2, {
        "pt": "Envio para toda a Europa",
        "fr": "Livraison dans toute l'Europe",
        "nl": "Verzending naar heel Europa",
        "en": "Shipping across Europe",
    }),
]

#: Textos do rodapé, nos quatro idiomas.
FOOTER_TEXTS = {
    "pt": {
        "about_text": "Produtos criativos feitos com impressão 3D. Peças decorativas, acessórios e filamentos — com opção de personalização e produção sob encomenda.",
        "categories_title": "Categorias",
        "contact_title": "Fale com a gente",
        "copyright_text": "© JD PRINT. Todos os direitos reservados.",
        "badge_text": "Compra segura",
    },
    "fr": {
        "about_text": "Des produits créatifs réalisés en impression 3D. Objets décoratifs, accessoires et filaments — avec personnalisation et fabrication sur commande.",
        "categories_title": "Catégories",
        "contact_title": "Nous contacter",
        "copyright_text": "© JD PRINT. Tous droits réservés.",
        "badge_text": "Paiement sécurisé",
    },
    "nl": {
        "about_text": "Creatieve producten gemaakt met 3D-printen. Decoratie, accessoires en filamenten — met personalisatie en productie op bestelling.",
        "categories_title": "Categorieën",
        "contact_title": "Neem contact op",
        "copyright_text": "© JD PRINT. Alle rechten voorbehouden.",
        "badge_text": "Veilig betalen",
    },
    "en": {
        "about_text": "Creative products made with 3D printing. Decorative pieces, accessories and filaments — with customisation and made-to-order production.",
        "categories_title": "Categories",
        "contact_title": "Get in touch",
        "copyright_text": "© JD PRINT. All rights reserved.",
        "badge_text": "Secure checkout",
    },
}

#: A coluna de informações do rodapé, apontando para as páginas da loja.
FOOTER_COLUMN = "paginas-institucionais"
FOOTER_COLUMN_TITLES = {
    "pt": "Informações", "fr": "Informations", "nl": "Informatie", "en": "Information",
}

# --- catálogo ---------------------------------------------------------------

CATEGORIES = [
    # (slug, parent_slug, {idioma: nome}, ordem)
    ("modelos", None, {"pt": "Modelos", "fr": "Modèles", "en": "Models", "nl": "Modellen"}, 1),
    ("animais", "modelos", {"pt": "Animais", "fr": "Animaux", "en": "Animals", "nl": "Dieren"}, 1),
    ("decoracao", "modelos", {"pt": "Decoração", "fr": "Décoration", "en": "Decor", "nl": "Decoratie"}, 2),
    ("fantasia", "modelos", {"pt": "Fantasia", "fr": "Fantaisie", "en": "Fantasy", "nl": "Fantasie"}, 3),
    ("geek", "modelos", {"pt": "Geek", "fr": "Geek", "en": "Geek", "nl": "Geek"}, 4),
    ("gatos", "animais", {"pt": "Gatos", "fr": "Chats", "en": "Cats", "nl": "Katten"}, 1),
    ("cachorros", "animais", {"pt": "Cachorros", "fr": "Chiens", "en": "Dogs", "nl": "Honden"}, 2),
    ("filamentos", None, {"pt": "Filamentos", "fr": "Filaments", "en": "Filaments", "nl": "Filamenten"}, 2),
    ("acessorios", None, {"pt": "Acessórios", "fr": "Accessoires", "en": "Accessories", "nl": "Accessoires"}, 3),
]

#: Os filamentos que a loja usa. "Resina" e "Madeira" continuam porque
#: podem já existir em instalações antigas — o `get_or_create` não apaga.
MATERIALS = ["PLA", "PETG", "TPU", "ABS", "Resina", "Madeira"]

COLORS = [
    ("Preto", "#111111"),
    ("Branco", "#FFFFFF"),
    ("Roxo", "#7C3AED"),
    ("Rosa", "#EC4899"),
    ("Verde", "#10B981"),
    ("Âmbar", "#F59E0B"),
]

PRODUCTS = [
    {
        "sku": "DEMO-GATO-01",
        "category": "animais",
        "price": Decimal("8.90"),
        "filament": Decimal("1.60"),
        "energy": Decimal("0.40"),
        "stock": 12,
        "featured": True,
        "featured_order": 1,
        "print_time": timedelta(hours=2, minutes=35),
        "weight": Decimal("35"),
        "dimensions": (Decimal("50"), Decimal("70"), Decimal("6")),
        "materials": ["PLA"],
        "colors": ["Preto", "Branco", "Rosa"],
        "names": {
            "pt": ("Marcador de Página — Gato Pompom", "Marcador de livro em forma de gato, com pompom de feltro."),
            "fr": ("Marque-page — Chat Pompon", "Marque-page en forme de chat, avec pompon en feutre."),
            "en": ("Bookmark — Pompom Cat", "Cat-shaped bookmark with a felt pompom."),
        },
    },
    {
        "sku": "DEMO-DRAGAO-01",
        "category": "animais",
        "price": Decimal("24.50"),
        "filament": Decimal("6.20"),
        "energy": Decimal("1.80"),
        "stock": 4,
        "featured": True,
        "featured_order": 2,
        "print_time": timedelta(hours=9, minutes=10),
        "weight": Decimal("180"),
        "dimensions": (Decimal("160"), Decimal("90"), Decimal("70")),
        "materials": ["PLA", "PETG"],
        "colors": ["Verde", "Preto"],
        "names": {
            "pt": ("Dragão Articulado", "Dragão com elos móveis, impresso em peça única."),
            "fr": ("Dragon articulé", "Dragon à maillons mobiles, imprimé en une seule pièce."),
            "en": ("Articulated Dragon", "Dragon with movable links, printed in one piece."),
        },
    },
    {
        "sku": "DEMO-COELHO-01",
        "category": "animais",
        "price": Decimal("11.90"),
        "filament": Decimal("2.40"),
        "energy": Decimal("0.60"),
        "stock": 0,
        "made_to_order": True,
        "lead_time": 5,
        "print_time": timedelta(hours=3, minutes=45),
        "weight": Decimal("60"),
        "dimensions": (Decimal("70"), Decimal("95"), Decimal("55")),
        "materials": ["PLA"],
        "colors": ["Branco", "Rosa", "Roxo", "Âmbar"],
        "names": {
            "pt": ("Coelho Geométrico", "Escultura decorativa de baixo relevo geométrico."),
            "fr": ("Lapin géométrique", "Sculpture décorative à facettes géométriques."),
            "en": ("Geometric Rabbit", "Decorative low-poly sculpture."),
        },
    },
    {
        "sku": "DEMO-VASO-01",
        "category": "decoracao",
        "price": Decimal("19.90"),
        "filament": Decimal("4.10"),
        "energy": Decimal("1.20"),
        "stock": 7,
        "featured": True,
        "featured_order": 3,
        "print_time": timedelta(hours=6),
        "weight": Decimal("140"),
        "dimensions": (Decimal("110"), Decimal("180"), Decimal("110")),
        "materials": ["PETG"],
        "colors": ["Branco", "Roxo"],
        "names": {
            "pt": ("Vaso Espiral", "Vaso decorativo em parede única, acabamento acetinado."),
            "fr": ("Vase spirale", "Vase décoratif à paroi unique, finition satinée."),
            "en": ("Spiral Vase", "Single-wall decorative vase with satin finish."),
        },
    },
    {
        "sku": "DEMO-LUM-01",
        "category": "decoracao",
        "price": Decimal("32.00"),
        "filament": Decimal("7.80"),
        "energy": Decimal("2.40"),
        "stock": 3,
        "print_time": timedelta(hours=11, minutes=20),
        "weight": Decimal("260"),
        "dimensions": (Decimal("150"), Decimal("220"), Decimal("150")),
        "materials": ["PLA", "Madeira"],
        "colors": ["Âmbar", "Branco"],
        "names": {
            "pt": ("Luminária Lithophane", "Abajur que revela a textura quando aceso."),
            "fr": ("Lampe lithophane", "Abat-jour qui révèle sa texture une fois allumé."),
            "en": ("Lithophane Lamp", "Shade that reveals its texture when lit."),
        },
    },
    {
        "sku": "DEMO-ORG-01",
        "category": "acessorios",
        "price": Decimal("14.50"),
        "filament": Decimal("3.30"),
        "energy": Decimal("0.90"),
        "stock": 15,
        "print_time": timedelta(hours=4, minutes=15),
        "weight": Decimal("120"),
        "dimensions": (Decimal("180"), Decimal("60"), Decimal("120")),
        "materials": ["PETG"],
        "colors": ["Preto", "Roxo"],
        "names": {
            "pt": ("Organizador de Mesa", "Suporte para canetas, cabos e pequenos objetos."),
            "fr": ("Organiseur de bureau", "Support pour stylos, câbles et petits objets."),
            "en": ("Desk Organizer", "Holder for pens, cables and small objects."),
        },
    },
    {
        "sku": "DEMO-FIL-PLA-01",
        "category": "filamentos",
        "price": Decimal("21.90"),
        "filament": Decimal("13.00"),
        "energy": Decimal("0.00"),
        "stock": 24,
        "weight": Decimal("1000"),
        "materials": ["PLA"],
        "colors": ["Roxo"],
        "names": {
            "pt": ("Filamento PLA 1,75 mm — 1 kg", "Bobina de PLA para impressão do dia a dia."),
            "fr": ("Filament PLA 1,75 mm — 1 kg", "Bobine de PLA pour l'impression du quotidien."),
            "en": ("PLA Filament 1.75 mm — 1 kg", "PLA spool for everyday printing."),
        },
    },
    {
        "sku": "DEMO-FIL-PETG-01",
        "category": "filamentos",
        "price": Decimal("26.90"),
        "filament": Decimal("16.50"),
        "energy": Decimal("0.00"),
        "stock": 9,
        "weight": Decimal("1000"),
        "materials": ["PETG"],
        "colors": ["Preto"],
        "names": {
            "pt": ("Filamento PETG 1,75 mm — 1 kg", "Mais resistente e flexível que o PLA."),
            "fr": ("Filament PETG 1,75 mm — 1 kg", "Plus résistant et souple que le PLA."),
            "en": ("PETG Filament 1.75 mm — 1 kg", "Tougher and more flexible than PLA."),
        },
    },
    {
        "sku": "DEMO-CAO-01",
        "category": "cachorros",
        "price": Decimal("13.90"),
        "filament": Decimal("2.90"),
        "energy": Decimal("0.70"),
        "stock": 6,
        "print_time": timedelta(hours=4),
        "weight": Decimal("70"),
        "materials": ["PLA"],
        "colors": ["Preto", "Branco"],
        "names": {
            "pt": ("Cachorro Salsicha", "Miniatura de dachshund com elos móveis."),
            "fr": ("Chien Teckel", "Miniature de teckel à maillons mobiles."),
            "en": ("Dachshund Dog", "Dachshund miniature with movable links."),
        },
    },
    {
        "sku": "DEMO-GATO-02",
        "category": "gatos",
        "price": Decimal("10.50"),
        "filament": Decimal("2.10"),
        "energy": Decimal("0.50"),
        "stock": 9,
        "print_time": timedelta(hours=3),
        "weight": Decimal("55"),
        "materials": ["PLA"],
        "colors": ["Preto", "Roxo"],
        "names": {
            "pt": ("Gato Dorminhoco", "Escultura de gato enrolado para prateleira."),
            "fr": ("Chat endormi", "Sculpture de chat enroulé pour étagère."),
            "en": ("Sleeping Cat", "Curled-up cat sculpture for a shelf."),
        },
    },
    {
        "sku": "DEMO-DRAGAO-02",
        "category": "fantasia",
        "price": Decimal("29.90"),
        "filament": Decimal("7.40"),
        "energy": Decimal("2.10"),
        "stock": 0,
        "made_to_order": True,
        "lead_time": 7,
        "print_time": timedelta(hours=12),
        "weight": Decimal("210"),
        "materials": ["PLA", "PETG"],
        "colors": ["Roxo", "Verde"],
        "names": {
            "pt": ("Dragão de Cristal", "Dragão translúcido, impresso em camadas finas."),
            "fr": ("Dragon de cristal", "Dragon translucide, imprimé en couches fines."),
            "en": ("Crystal Dragon", "Translucent dragon printed in fine layers."),
        },
    },
    {
        "sku": "DEMO-GRIFO-01",
        "category": "fantasia",
        "price": Decimal("22.00"),
        "filament": Decimal("5.10"),
        "energy": Decimal("1.60"),
        "stock": 3,
        "print_time": timedelta(hours=8, minutes=30),
        "weight": Decimal("150"),
        "materials": ["PLA"],
        "colors": ["Âmbar", "Preto"],
        "names": {
            "pt": ("Grifo Guardião", "Estatueta de grifo com asas abertas."),
            "fr": ("Griffon gardien", "Statuette de griffon aux ailes ouvertes."),
            "en": ("Guardian Griffin", "Griffin statuette with open wings."),
        },
    },
    {
        "sku": "DEMO-GOBLIN-01",
        "category": "fantasia",
        "price": Decimal("9.90"),
        "filament": Decimal("1.80"),
        "energy": Decimal("0.60"),
        "stock": 14,
        "print_time": timedelta(hours=2, minutes=20),
        "weight": Decimal("40"),
        "materials": ["Resina"],
        "colors": ["Verde"],
        "names": {
            "pt": ("Goblin de Mesa", "Miniatura para jogos de tabuleiro."),
            "fr": ("Gobelin de table", "Miniature pour jeux de plateau."),
            "en": ("Tabletop Goblin", "Miniature for board games."),
        },
    },
    {
        "sku": "DEMO-CONTROLE-01",
        "category": "geek",
        "price": Decimal("16.90"),
        "filament": Decimal("3.60"),
        "energy": Decimal("1.00"),
        "stock": 8,
        "print_time": timedelta(hours=5),
        "weight": Decimal("110"),
        "materials": ["PETG"],
        "colors": ["Preto", "Roxo"],
        "names": {
            "pt": ("Suporte para Controle", "Apoio de mesa para controle de videogame."),
            "fr": ("Support de manette", "Support de bureau pour manette de jeu."),
            "en": ("Controller Stand", "Desk stand for a game controller."),
        },
    },
    {
        "sku": "DEMO-NAVE-01",
        "category": "geek",
        "price": Decimal("27.50"),
        "filament": Decimal("6.30"),
        "energy": Decimal("1.90"),
        "stock": 2,
        "print_time": timedelta(hours=9),
        "weight": Decimal("175"),
        "materials": ["PLA"],
        "colors": ["Branco", "Preto"],
        "names": {
            "pt": ("Nave Exploradora", "Réplica de nave com base de exposição."),
            "fr": ("Vaisseau explorateur", "Réplique de vaisseau avec socle."),
            "en": ("Explorer Ship", "Ship replica with display base."),
        },
    },
    {
        "sku": "DEMO-CHAVEIRO-01",
        "category": "geek",
        "price": Decimal("4.90"),
        "filament": Decimal("0.70"),
        "energy": Decimal("0.20"),
        "stock": 30,
        "print_time": timedelta(minutes=45),
        "weight": Decimal("12"),
        "materials": ["PLA"],
        "colors": ["Rosa", "Roxo", "Verde", "Âmbar", "Preto"],
        "names": {
            "pt": ("Chaveiro Pixel", "Chaveiro em estilo pixel art."),
            "fr": ("Porte-clés pixel", "Porte-clés style pixel art."),
            "en": ("Pixel Keyring", "Pixel-art style keyring."),
        },
    },
    {
        "sku": "DEMO-PORTA-01",
        "category": "decoracao",
        "price": Decimal("12.50"),
        "filament": Decimal("2.60"),
        "energy": Decimal("0.80"),
        "stock": 11,
        "print_time": timedelta(hours=3, minutes=30),
        "weight": Decimal("85"),
        "materials": ["PLA", "Madeira"],
        "colors": ["Branco"],
        "names": {
            "pt": ("Porta-Retrato Ondulado", "Moldura com textura de ondas."),
            "fr": ("Cadre photo ondulé", "Cadre à texture ondulée."),
            "en": ("Wavy Photo Frame", "Frame with a wave texture."),
        },
    },
    {
        "sku": "DEMO-INCENSO-01",
        "category": "decoracao",
        "price": Decimal("8.50"),
        "filament": Decimal("1.40"),
        "energy": Decimal("0.40"),
        "stock": 0,
        "made_to_order": True,
        "lead_time": 4,
        "print_time": timedelta(hours=2),
        "weight": Decimal("45"),
        "materials": ["PETG"],
        "colors": ["Preto", "Âmbar"],
        "names": {
            "pt": ("Suporte de Incenso Onda", "Base curva para varetas de incenso."),
            "fr": ("Porte-encens vague", "Base courbee pour batons d'encens."),
            "en": ("Wave Incense Holder", "Curved base for incense sticks."),
        },
    },
    {
        "sku": "DEMO-VASO-VAR",
        "category": "decoracao",
        "price": Decimal("19.90"),
        "filament": Decimal("4.10"),
        "energy": Decimal("1.20"),
        "stock": 0,
        "print_time": timedelta(hours=6),
        "weight": Decimal("140"),
        "materials": ["PETG"],
        "colors": ["Branco", "Preto", "Roxo"],
        "variants": [
            # (sufixo do SKU, cor, tamanho, preço, estoque)
            ("PRETO-15", "Preto", "15 cm", Decimal("19.90"), 6),
            ("BRANCO-15", "Branco", "15 cm", Decimal("19.90"), 3),
            ("ROXO-15", "Roxo", "15 cm", Decimal("21.90"), 0),
            ("PRETO-25", "Preto", "25 cm", Decimal("27.90"), 4),
            ("BRANCO-25", "Branco", "25 cm", Decimal("27.90"), 2),
        ],
        "names": {
            "pt": ("Vaso Facetado", "Vaso decorativo em duas alturas e três cores."),
            "fr": ("Vase à facettes", "Vase décoratif en deux hauteurs et trois couleurs."),
            "en": ("Faceted Vase", "Decorative vase in two heights and three colours."),
        },
    },
    {
        "sku": "DEMO-PLACA-FOTO",
        "category": "decoracao",
        "price": Decimal("24.90"),
        "filament": Decimal("5.20"),
        "energy": Decimal("1.80"),
        "stock": 0,
        "made_to_order": True,
        "lead_time": 6,
        "print_time": timedelta(hours=7),
        "weight": Decimal("160"),
        "materials": ["PLA"],
        "colors": ["Branco"],
        "personalization": "photo",
        "names": {
            "pt": ("Litofania da Sua Foto", "Sua foto vira luz: envie a imagem e imprimimos."),
            "fr": ("Lithophanie de votre photo", "Votre photo devient lumière : envoyez l'image."),
            "en": ("Lithophane of Your Photo", "Your photo becomes light: send us the image."),
        },
    },
    {
        "sku": "DEMO-CHAVEIRO-NOME",
        "category": "acessorios",
        "price": Decimal("7.90"),
        "filament": Decimal("1.10"),
        "energy": Decimal("0.30"),
        "stock": 25,
        "print_time": timedelta(hours=1, minutes=10),
        "weight": Decimal("18"),
        "materials": ["PLA"],
        "colors": ["Preto", "Rosa", "Roxo", "Verde"],
        "personalization": "text",
        "text_limit": 20,
        "names": {
            "pt": ("Chaveiro com Nome", "Escreva o nome e imprimimos no chaveiro."),
            "fr": ("Porte-clés avec prénom", "Indiquez le prénom et nous l'imprimons."),
            "en": ("Name Keyring", "Tell us the name and we print it on the keyring."),
        },
    },
    {
        "sku": "DEMO-PLACA-PORTA",
        "category": "decoracao",
        "price": Decimal("16.50"),
        "filament": Decimal("3.10"),
        "energy": Decimal("0.90"),
        "stock": 8,
        "print_time": timedelta(hours=3, minutes=20),
        "weight": Decimal("95"),
        "materials": ["PLA", "Madeira"],
        "colors": ["Branco", "Âmbar"],
        "personalization": "photo_or_text",
        "text_limit": 60,
        "names": {
            "pt": ("Placa de Porta Personalizada", "Escolha: sua foto em relevo ou um texto."),
            "fr": ("Plaque de porte personnalisée", "Au choix : votre photo en relief ou un texte."),
            "en": ("Custom Door Sign", "Your choice: your photo in relief or a text."),
        },
    },
]

# --- Home -------------------------------------------------------------------

#: O hero editorial da direção visual, nos quatro idiomas.
#:
#: `highlight` é um trecho que precisa EXISTIR dentro do título daquele idioma:
#: é ele que sai em roxo. Se a tradução mudar a palavra, o título continua
#: inteiro, só sem o destaque.
BANNER = {
    "internal_name": "Banner de boas-vindas (demonstração)",
    "layout": "editorial",
    "cta_secondary_url": "/contato/",
    "translations": {
        "pt": {
            "eyebrow": "Impressão 3D criativa",
            "title": "Ideias que ganham forma, camada por camada.",
            "title_highlight": "forma",
            "subtitle": "Modelos decorativos, brinquedos, acessórios e filamentos — impressos na Bélgica com a cor e o acabamento que você escolher.",
            "cta_label": "Ver produtos",
            "cta_secondary_label": "Pedir orçamento",
            "perks": ("Qualidade em cada detalhe", "Cores à escolha", "Envio em 3–5 dias"),
            "badges": ("PLA · 0.12 mm", "+120 cores", "★ 4.9 · 300+ pedidos"),
        },
        "fr": {
            "eyebrow": "Impression 3D créative",
            "title": "Des idées qui prennent forme, couche après couche.",
            "title_highlight": "forme",
            "subtitle": "Modèles décoratifs, jouets, accessoires et filaments — imprimés en Belgique, dans la couleur et la finition de votre choix.",
            "cta_label": "Voir les produits",
            "cta_secondary_label": "Demander un devis",
            "perks": ("Soin du détail", "Couleurs au choix", "Livraison en 3–5 jours"),
            "badges": ("PLA · 0,12 mm", "+120 couleurs", "★ 4,9 · 300+ commandes"),
        },
        "nl": {
            "eyebrow": "Creatief 3D-printen",
            "title": "Ideeën die vorm krijgen, laag na laag.",
            "title_highlight": "vorm",
            "subtitle": "Decoratieve modellen, speelgoed, accessoires en filament — geprint in België, in de kleur en afwerking die u kiest.",
            "cta_label": "Bekijk producten",
            "cta_secondary_label": "Offerte aanvragen",
            "perks": ("Oog voor detail", "Kleur naar keuze", "Levering in 3–5 dagen"),
            "badges": ("PLA · 0,12 mm", "+120 kleuren", "★ 4,9 · 300+ orders"),
        },
        "en": {
            "eyebrow": "Creative 3D printing",
            "title": "Ideas that take shape, layer by layer.",
            "title_highlight": "shape",
            "subtitle": "Decorative models, toys, accessories and filament — printed in Belgium, in the colour and finish you choose.",
            "cta_label": "Browse products",
            "cta_secondary_label": "Request a quote",
            "perks": ("Care in every detail", "Your colour", "Delivered in 3–5 days"),
            "badges": ("PLA · 0.12 mm", "+120 colours", "★ 4.9 · 300+ orders"),
        },
    },
}

#: Os outros dois desenhos do banner, para o Admin ver como ficam. Nascem
#: INATIVOS: a Home mostra o primeiro banner ativo, e o de boas-vindas
#: (editorial) continua sendo ele. Ativar um destes é uma decisão do Admin.
#:
#: Cada tradução é o dicionário de campos da `HomeBannerTranslation`, tal
#: qual; `title_highlight` precisa existir dentro do `title` do idioma.
EXTRA_BANNERS = [
    {
        "internal_name": "Poster Pop (demonstração)",
        "layout": "poster_pop",
        "sort_order": 2,
        "cta_secondary_url": "/contato/",
        "translations": {
            "pt": {
                "eyebrow": "Impressão 3D criativa",
                "title": "Do arquivo à sua mesa, camada por camada.",
                "title_highlight": "camada",
                "subtitle": "Modelos decorativos, brinquedos, acessórios e filamentos — impressos na Bélgica com a cor e o acabamento que você escolher.",
                "cta_label": "Ver produtos",
                "cta_secondary_label": "Pedir orçamento",
                "perk_1": "Qualidade em cada detalhe", "perk_2": "Cores à escolha", "perk_3": "Envio em 3–5 dias",
                "badge_mint": "+120 cores", "badge_white": "★ 4.9 · 300+ pedidos",
            },
            "fr": {
                "eyebrow": "Impression 3D créative",
                "title": "Du fichier à votre table, couche après couche.",
                "title_highlight": "couche",
                "subtitle": "Modèles décoratifs, jouets, accessoires et filaments — imprimés en Belgique, dans la couleur et la finition de votre choix.",
                "cta_label": "Voir les produits",
                "cta_secondary_label": "Demander un devis",
                "perk_1": "Soin du détail", "perk_2": "Couleurs au choix", "perk_3": "Livraison en 3–5 jours",
                "badge_mint": "+120 couleurs", "badge_white": "★ 4,9 · 300+ commandes",
            },
            "nl": {
                "eyebrow": "Creatief 3D-printen",
                "title": "Van bestand tot tafel, laag na laag.",
                "title_highlight": "laag",
                "subtitle": "Decoratieve modellen, speelgoed, accessoires en filament — geprint in België, in de kleur en afwerking die u kiest.",
                "cta_label": "Bekijk producten",
                "cta_secondary_label": "Offerte aanvragen",
                "perk_1": "Oog voor detail", "perk_2": "Kleur naar keuze", "perk_3": "Levering in 3–5 dagen",
                "badge_mint": "+120 kleuren", "badge_white": "★ 4,9 · 300+ orders",
            },
            "en": {
                "eyebrow": "Creative 3D printing",
                "title": "From file to your table, layer by layer.",
                "title_highlight": "layer",
                "subtitle": "Decorative models, toys, accessories and filament — printed in Belgium, in the colour and finish you choose.",
                "cta_label": "Browse products",
                "cta_secondary_label": "Request a quote",
                "perk_1": "Care in every detail", "perk_2": "Your colour", "perk_3": "Delivered in 3–5 days",
                "badge_mint": "+120 colours", "badge_white": "★ 4.9 · 300+ orders",
            },
        },
    },
    {
        "internal_name": "Bento Criativo (demonstração)",
        "layout": "bento_criativo",
        "sort_order": 3,
        "cta_secondary_url": "/contato/",
        "translations": {
            "pt": {
                "eyebrow": "Impressão 3D criativa",
                "title": "Imprimimos o que você imagina.",
                "title_highlight": "imagina",
                "subtitle": "Modelos decorativos, brinquedos, acessórios e filamentos — com a cor e o acabamento que você escolher, a partir de 1 unidade.",
                "cta_label": "Ver produtos",
                "cta_secondary_label": "Pedir orçamento",
                "perk_1": "Qualidade em cada detalhe", "perk_2": "Cores à escolha", "perk_3": "Envio em 3–5 dias",
                "badge_coral": "Feito na Bélgica", "badge_yellow": "PLA · 0.12 mm",
                "badge_mint": "+120 cores", "colors_note": "PLA · PETG · TPU",
                "rating_value": "4.9", "rating_note": "300+ pedidos entregues",
            },
            "fr": {
                "eyebrow": "Impression 3D créative",
                "title": "Nous imprimons ce que vous imaginez.",
                "title_highlight": "imaginez",
                "subtitle": "Modèles décoratifs, jouets, accessoires et filaments — dans la couleur et la finition de votre choix, dès 1 pièce.",
                "cta_label": "Voir les produits",
                "cta_secondary_label": "Demander un devis",
                "perk_1": "Soin du détail", "perk_2": "Couleurs au choix", "perk_3": "Livraison en 3–5 jours",
                "badge_coral": "Fait en Belgique", "badge_yellow": "PLA · 0,12 mm",
                "badge_mint": "+120 couleurs", "colors_note": "PLA · PETG · TPU",
                "rating_value": "4,9", "rating_note": "300+ commandes livrées",
            },
            "nl": {
                "eyebrow": "Creatief 3D-printen",
                "title": "Wij printen wat u bedenkt.",
                "title_highlight": "bedenkt",
                "subtitle": "Decoratieve modellen, speelgoed, accessoires en filament — in de kleur en afwerking die u kiest, vanaf 1 stuk.",
                "cta_label": "Bekijk producten",
                "cta_secondary_label": "Offerte aanvragen",
                "perk_1": "Oog voor detail", "perk_2": "Kleur naar keuze", "perk_3": "Levering in 3–5 dagen",
                "badge_coral": "Gemaakt in België", "badge_yellow": "PLA · 0,12 mm",
                "badge_mint": "+120 kleuren", "colors_note": "PLA · PETG · TPU",
                "rating_value": "4,9", "rating_note": "300+ bestellingen geleverd",
            },
            "en": {
                "eyebrow": "Creative 3D printing",
                "title": "We print what you imagine.",
                "title_highlight": "imagine",
                "subtitle": "Decorative models, toys, accessories and filament — in the colour and finish you choose, from 1 unit.",
                "cta_label": "Browse products",
                "cta_secondary_label": "Request a quote",
                "perk_1": "Care in every detail", "perk_2": "Your colour", "perk_3": "Delivered in 3–5 days",
                "badge_coral": "Made in Belgium", "badge_yellow": "PLA · 0.12 mm",
                "badge_mint": "+120 colours", "colors_note": "PLA · PETG · TPU",
                "rating_value": "4.9", "rating_note": "300+ orders delivered",
            },
        },
    },
]

SECTIONS = [
    {
        "internal_name": "Destaques de Modelos",
        "type": HomeSectionType.FEATURED_PRODUCTS,
        "layout": HomeSectionLayout.GRID,
        "limit": 4,
        "order": 1,
        "cta_category": "modelos",
        "translations": {
            "pt": ("Destaques de Modelos", "Confira alguns dos nossos modelos favoritos.", "Ver todos os modelos"),
            "fr": ("Modèles à la une", "Quelques-uns de nos modèles préférés.", "Voir tous les modèles"),
            "en": ("Featured Models", "A few of our favourite prints.", "See all models"),
        },
    },
    {
        "internal_name": "Nova Coleção",
        "type": HomeSectionType.MANUAL_PRODUCTS,
        "layout": HomeSectionLayout.CAROUSEL,
        "limit": 4,
        "order": 2,
        "products": ["DEMO-VASO-01", "DEMO-LUM-01", "DEMO-COELHO-01", "DEMO-ORG-01"],
        "translations": {
            "pt": ("Nova Coleção", "Peças recém-chegadas ao catálogo.", "Ver a coleção"),
            "fr": ("Nouvelle collection", "Les pièces qui viennent d'arriver.", "Voir la collection"),
            "en": ("New Collection", "Freshly added to the catalogue.", "See the collection"),
        },
    },
    {
        "internal_name": "Filamentos em Destaque",
        "type": HomeSectionType.CATEGORY_PRODUCTS,
        "layout": HomeSectionLayout.GRID,
        "limit": 4,
        "order": 3,
        "category": "filamentos",
        "cta_category": "filamentos",
        "translations": {
            "pt": ("Filamentos em Destaque", "Para quem imprime em casa.", "Ver filamentos"),
            "fr": ("Filaments en vedette", "Pour ceux qui impriment chez eux.", "Voir les filaments"),
            "en": ("Featured Filaments", "For printing at home.", "See filaments"),
        },
    },
    {
        "internal_name": "Novidades",
        "type": HomeSectionType.NEWEST_PRODUCTS,
        "layout": HomeSectionLayout.CAROUSEL,
        "limit": 8,
        "order": 4,
        "translations": {
            "pt": ("Chegou agora", "Os produtos mais recentes da loja.", "Ver tudo"),
            "fr": ("Nouveautés", "Les derniers produits de la boutique.", "Tout voir"),
            "en": ("Just arrived", "The newest products in the shop.", "See all"),
        },
    },
]


class Command(BaseCommand):
    help = "Cria dados de demonstração (catálogo + Home). Não sobrescreve nada existente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--remover",
            action="store_true",
            help="Remove apenas o que este comando criou (SKUs DEMO- e as seções de demonstração).",
        )
        parser.add_argument(
            "--forcar",
            action="store_true",
            help="Permite rodar com DEBUG=False. Use com consciência.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["forcar"]:
            raise CommandError(
                "Este comando é para desenvolvimento. Com DEBUG=False, use --forcar se tiver certeza."
            )

        if options["remover"]:
            self.remove_demo_data()
            return

        with transaction.atomic():
            self.ensure_languages()
            countries = self.ensure_countries()
            categories = self.create_categories()
            materials = self.create_materials()
            colors = self.create_colors()
            self.create_brands()
            products = self.create_products(categories, materials, colors)
            self.create_shipping(countries)
            self.create_banner(categories)
            self.create_sections(categories, products)
            self.create_cards()
            self.create_callout(categories)
            self.create_top_bar()
            self.create_footer()

        self.stdout.write(self.style.SUCCESS("Dados de demonstração prontos. Acesse / para ver a Home."))

    # -- catálogo ----------------------------------------------------------

    def create_categories(self):
        created = {}
        for slug, parent_slug, names, order in CATEGORIES:
            category, was_created = Category.objects.get_or_create(
                slug=slug,
                defaults={"parent": created.get(parent_slug), "sort_order": order},
            )
            created[slug] = category
            for language, name in names.items():
                CategoryTranslation.objects.get_or_create(
                    master=category, language=language, defaults={"name": name}
                )
            if was_created:
                self.stdout.write(f"  categoria: {slug}")
        return created

    def create_materials(self):
        return {name: Material.objects.get_or_create(name=name)[0] for name in MATERIALS}

    def create_colors(self):
        return {
            name: Color.objects.get_or_create(name=name, defaults={"hex_code": hex_code})[0]
            for name, hex_code in COLORS
        }

    def create_products(self, categories, materials, colors):
        """Produto genérico + as variantes, que é onde mora o comercial."""
        created = {}
        for data in PRODUCTS:
            product, was_created = Product.objects.get_or_create(
                sku=data["sku"],
                defaults={
                    "status": ProductStatus.ACTIVE,
                    "category": categories[data["category"]],
                    "is_featured": data.get("featured", False),
                    "featured_order": data.get("featured_order", 0),
                    "personalization_type": data.get("personalization", "none"),
                    "personalization_text_limit": data.get("text_limit", 200),
                },
            )
            created[data["sku"]] = product

            for language, (name, short) in data["names"].items():
                ProductTranslation.objects.get_or_create(
                    master=product,
                    language=language,
                    defaults={"name": name, "short_description": short},
                )

            if was_created:
                # O slug foi gerado a partir do SKU antes de a tradução existir.
                product.refresh_translations()
                product.slug = ""
                product.save()
                self.create_variants(product, data, colors, materials)
                self.stdout.write(f"  produto: {product.sku}")

        return created

    def create_variants(self, product, data, colors, materials):
        """As unidades vendáveis.

        Produto sem lista de variantes ganha **uma**, com o SKU do produto: é
        a mesma coisa que se vendia antes de a variante existir, só que agora
        na tabela certa. Produto com lista ganha as dela.
        """
        width, height, depth = data.get("dimensions", (None, None, None))
        material = next(
            (materials[name] for name in data.get("materials", []) if name in materials), None
        )
        comuns = {
            "product": product,
            "pricing_mode": PricingMode.PRICE,
            "filament_cost": data["filament"],
            "energy_cost": data["energy"],
            "made_to_order": data.get("made_to_order", False),
            "production_lead_time_days": data.get("lead_time"),
            "print_time": data.get("print_time"),
            "weight_grams": data.get("weight"),
            "width": width,
            "height": height,
            "depth": depth,
            "material": material,
        }

        variantes = data.get("variants", [])
        if not variantes:
            cores = data.get("colors", [])
            ProductVariant.objects.get_or_create(
                sku=product.sku,
                defaults={
                    **comuns,
                    "color": colors.get(cores[0]) if len(cores) == 1 else None,
                    "sale_price": data["price"],
                    "stock_quantity": data.get("stock", 0),
                    "sort_order": 0,
                },
            )
            return

        for order, (suffix, color, size, price, stock) in enumerate(variantes, 1):
            ProductVariant.objects.get_or_create(
                sku=f"{product.sku}-{suffix}",
                defaults={
                    **comuns,
                    "color": colors.get(color),
                    "size": size,
                    "sale_price": price,
                    "stock_quantity": stock,
                    "sort_order": order,
                },
            )

    # -- Home --------------------------------------------------------------

    def create_banner(self, categories):
        banner, was_created = HomeBanner.objects.get_or_create(
            internal_name=BANNER["internal_name"],
            defaults={
                "is_active": True,
                "sort_order": 1,
                "layout": BANNER["layout"],
                "cta_secondary_url": BANNER["cta_secondary_url"],
                "cta_target": CtaTarget.CATEGORY,
                "cta_category": categories["modelos"],
            },
        )
        for language, texto in BANNER["translations"].items():
            perk_1, perk_2, perk_3 = texto["perks"]
            amarelo, menta, branco = texto["badges"]
            HomeBannerTranslation.objects.get_or_create(
                master=banner,
                language=language,
                defaults={
                    "eyebrow": texto["eyebrow"],
                    "title": texto["title"],
                    "title_highlight": texto["title_highlight"],
                    "subtitle": texto["subtitle"],
                    "cta_label": texto["cta_label"],
                    "cta_secondary_label": texto["cta_secondary_label"],
                    "perk_1": perk_1, "perk_2": perk_2, "perk_3": perk_3,
                    "badge_yellow": amarelo, "badge_mint": menta, "badge_white": branco,
                },
            )
        if was_created:
            self.stdout.write("  banner de demonstração")

        # Os outros dois desenhos, inativos — ver `EXTRA_BANNERS`.
        for data in EXTRA_BANNERS:
            extra, was_created = HomeBanner.objects.get_or_create(
                internal_name=data["internal_name"],
                defaults={
                    "is_active": False,
                    "sort_order": data["sort_order"],
                    "layout": data["layout"],
                    "cta_secondary_url": data["cta_secondary_url"],
                    "cta_target": CtaTarget.CATEGORY,
                    "cta_category": categories["modelos"],
                },
            )
            for language, campos in data["translations"].items():
                HomeBannerTranslation.objects.get_or_create(
                    master=extra, language=language, defaults=campos
                )
            if was_created:
                self.stdout.write(f"  banner de demonstração ({data['layout']}, inativo)")

    def create_sections(self, categories, products):
        for data in SECTIONS:
            defaults = {
                "section_type": data["type"],
                "layout": data["layout"],
                "product_limit": data["limit"],
                "sort_order": data["order"],
                "is_active": True,
            }
            if "category" in data:
                defaults["category"] = categories[data["category"]]
            if "cta_category" in data:
                defaults["cta_target"] = CtaTarget.CATEGORY
                defaults["cta_category"] = categories[data["cta_category"]]

            section, was_created = HomeSection.objects.get_or_create(
                internal_name=data["internal_name"], defaults=defaults
            )

            for language, (title, subtitle, cta) in data["translations"].items():
                HomeSectionTranslation.objects.get_or_create(
                    master=section,
                    language=language,
                    defaults={"title": title, "subtitle": subtitle, "cta_label": cta},
                )

            for position, sku in enumerate(data.get("products", []), start=1):
                product = products.get(sku) or Product.objects.filter(sku=sku).first()
                if product is None:
                    continue
                HomeSectionProduct.objects.get_or_create(
                    section=section, product=product, defaults={"sort_order": position}
                )

            if was_created:
                self.stdout.write(f"  seção: {section.internal_name}")

    # -- base da loja ------------------------------------------------------

    def ensure_languages(self):
        """Garante os quatro idiomas da loja.

        Eles já vêm da migration de dados do `core`. Aqui só existe para uma
        instalação nova não depender da ordem em que as coisas rodaram.
        `defaults` age apenas na criação: idioma que o administrador desativou
        continua desativado.
        """
        for code, order in LANGUAGES:
            SiteLanguage.objects.get_or_create(
                code=code, defaults={"is_active": True, "sort_order": order}
            )

    def ensure_countries(self):
        """A Bélgica, no mínimo — sem país de entrega não há frete nem TVA."""
        countries = {}
        for iso, name, vat in COUNTRIES:
            country, _created = DeliveryCountry.objects.get_or_create(
                iso_code=iso, defaults={"name": name, "vat_rate": Decimal(vat), "is_active": True}
            )
            countries[iso] = country
        return countries

    def create_brands(self):
        return {
            name: Brand.objects.get_or_create(name=name)[0] for name in BRANDS
        }

    # -- entrega -----------------------------------------------------------

    def create_shipping(self, countries):
        """Transportadoras, métodos e tarifas.

        A chave de cada `get_or_create` é o que identifica a linha de verdade
        (o código, ou método+país+faixa de peso), nunca o preço: rodar de novo
        não mexe numa tarifa que o administrador ajustou.
        """
        carriers = {}
        for code, name, order, tracking in CARRIERS:
            carriers[code] = ShippingCarrier.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "sort_order": order,
                    "is_active": True,
                    "tracking_url_template": tracking,
                },
            )[0]

        methods = {}
        for code, carrier_code, name, description, min_days, max_days, order in SHIPPING_METHODS:
            # A unicidade do método é (transportadora, código) — ver o
            # UniqueConstraint do model. Procurar só pelo código encontraria a
            # linha errada no dia em que duas transportadoras usarem o mesmo.
            methods[code] = ShippingMethod.objects.get_or_create(
                carrier=carriers[carrier_code],
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "min_days": min_days,
                    "max_days": max_days,
                    "sort_order": order,
                    "is_active": True,
                },
            )[0]

        for method_code, iso, min_weight, max_weight, price in SHIPPING_RATES:
            country = countries.get(iso) or DeliveryCountry.objects.filter(iso_code=iso).first()
            if country is None:
                continue
            ShippingRate.objects.get_or_create(
                method=methods[method_code],
                country=country,
                min_weight_grams=min_weight,
                max_weight_grams=max_weight,
                defaults={"price": Decimal(price)},
            )
        return methods

    # -- Home: cards e chamada ---------------------------------------------

    def create_cards(self):
        for internal_name, icon, accent, order, texts in HOME_CARDS:
            card, _created = HomeCard.objects.get_or_create(
                internal_name=internal_name,
                defaults={"icon": icon, "accent": accent, "sort_order": order, "is_active": True},
            )
            for language, (title, text) in texts.items():
                HomeCardTranslation.objects.get_or_create(
                    master=card, language=language, defaults={"title": title, "text": text}
                )

    def create_callout(self, categories):
        """A chamada final. Uma linha só (`load()` cuida disso)."""
        callout = HomeCallout.load()
        target = categories.get("modelos")
        if target is not None and callout.cta_category_id is None and not callout.cta_url:
            callout.cta_target = CtaTarget.CATEGORY
            callout.cta_category = target
        callout.is_active = True
        callout.save()

        for language, (eyebrow, title, text, cta_label) in HOME_CALLOUT.items():
            HomeCalloutTranslation.objects.get_or_create(
                master=callout,
                language=language,
                defaults={
                    "eyebrow": eyebrow,
                    "title": title,
                    "text": text,
                    "cta_label": cta_label,
                },
            )

    # -- faixa do topo e rodapé --------------------------------------------

    def create_top_bar(self):
        for internal_name, icon, order, texts in TOP_BAR:
            item, _created = TopBarItem.objects.get_or_create(
                internal_name=internal_name,
                defaults={"icon": icon, "sort_order": order, "is_active": True},
            )
            for language, text in texts.items():
                TopBarItemTranslation.objects.get_or_create(
                    master=item, language=language, defaults={"text": text}
                )

    def create_footer(self):
        """Textos, contato e a coluna que aponta para as páginas da loja.

        A coluna já pode existir (migration `storefront.0005`); o
        `get_or_create` pelo nome interno faz este método não criar uma segunda.
        """
        settings_row = FooterSettings.load()
        settings_row.is_active = True
        if not settings_row.contact_email:
            settings_row.contact_email = "contato@jd-print.com"
        settings_row.save()

        for language, fields in FOOTER_TEXTS.items():
            FooterSettingsTranslation.objects.get_or_create(
                master=settings_row, language=language, defaults=fields
            )

        column, _created = FooterColumn.objects.get_or_create(
            internal_name=FOOTER_COLUMN, defaults={"is_active": True, "sort_order": 10}
        )
        for language, title in FOOTER_COLUMN_TITLES.items():
            FooterColumnTranslation.objects.get_or_create(
                master=column, language=language, defaults={"title": title}
            )

        # Um link por página institucional publicada, por **referência**: é o
        # que faz o endereço acompanhar o idioma do visitante.
        from apps.storefront.models import InstitutionalPage, PageSlug

        for order, slug in enumerate(PageSlug.values, start=1):
            page = InstitutionalPage.objects.filter(slug=slug).first()
            if page is None:
                continue
            FooterLink.objects.get_or_create(
                column=column,
                page=page,
                defaults={"is_active": True, "sort_order": order, "url": ""},
            )

    # -- remoção -----------------------------------------------------------

    def remove_demo_data(self):
        section_names = [data["internal_name"] for data in SECTIONS]
        sections = HomeSection.objects.filter(internal_name__in=section_names)
        banner_names = [BANNER["internal_name"]] + [d["internal_name"] for d in EXTRA_BANNERS]
        banners = HomeBanner.objects.filter(internal_name__in=banner_names)
        demo_products = Product.objects.filter(sku__startswith=DEMO_PREFIX)

        counts = (sections.count(), banners.count(), demo_products.count())
        sections.delete()
        banners.delete()
        demo_products.delete()

        self.stdout.write(
            self.style.WARNING(
                f"Removidos: {counts[0]} seção(ões), {counts[1]} banner(s), {counts[2]} produto(s) DEMO-. "
                "Categorias, materiais e cores foram mantidos."
            )
        )
