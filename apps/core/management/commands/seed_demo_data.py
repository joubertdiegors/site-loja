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
    Color,
    Material,
    PricingMode,
    Product,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.categories.models import Category, CategoryTranslation
from apps.home.models import (
    CtaTarget,
    HomeBanner,
    HomeBannerTranslation,
    HomeSection,
    HomeSectionLayout,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
)

DEMO_PREFIX = "DEMO-"

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

MATERIALS = ["PLA", "PETG", "Resina", "Madeira"]

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

BANNER = {
    "internal_name": "Banner de boas-vindas (demonstração)",
    "translations": {
        "pt": ("Peças únicas, impressas sob medida", "Modelos decorativos, acessórios e filamentos — com cor e acabamento à sua escolha.", "Ver produtos"),
        "fr": ("Des pièces uniques, imprimées sur mesure", "Modèles décoratifs, accessoires et filaments — couleur et finition au choix.", "Voir les produits"),
        "en": ("Unique pieces, printed to order", "Decorative models, accessories and filaments — your colour, your finish.", "Browse products"),
    },
}

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
            categories = self.create_categories()
            materials = self.create_materials()
            colors = self.create_colors()
            products = self.create_products(categories, materials, colors)
            self.create_banner(categories)
            self.create_sections(categories, products)

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
        created = {}
        for data in PRODUCTS:
            width, height, depth = data.get("dimensions", (None, None, None))
            product, was_created = Product.objects.get_or_create(
                sku=data["sku"],
                defaults={
                    "status": ProductStatus.ACTIVE,
                    "category": categories[data["category"]],
                    "pricing_mode": PricingMode.PRICE,
                    "sale_price": data["price"],
                    "filament_cost": data["filament"],
                    "energy_cost": data["energy"],
                    "stock_quantity": data.get("stock", 0),
                    "made_to_order": data.get("made_to_order", False),
                    "production_lead_time_days": data.get("lead_time"),
                    "print_time": data.get("print_time"),
                    "weight_grams": data.get("weight"),
                    "width": width,
                    "height": height,
                    "depth": depth,
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
                product.materials.set([materials[name] for name in data.get("materials", [])])
                product.colors.set([colors[name] for name in data.get("colors", [])])
                # O slug foi gerado a partir do SKU antes de a tradução existir.
                product.refresh_translations()
                product.slug = ""
                product.save()
                self.create_variants(product, data, colors)
                self.stdout.write(f"  produto: {product.sku}")

        return created

    def create_variants(self, product, data, colors):
        """Variantes do produto de demonstração, quando houver."""
        for order, (suffix, color, size, price, stock) in enumerate(data.get("variants", []), 1):
            ProductVariant.objects.get_or_create(
                sku=f"{product.sku}-{suffix}",
                defaults={
                    "product": product,
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
                "cta_target": CtaTarget.CATEGORY,
                "cta_category": categories["modelos"],
            },
        )
        for language, (title, subtitle, cta) in BANNER["translations"].items():
            HomeBannerTranslation.objects.get_or_create(
                master=banner,
                language=language,
                defaults={"title": title, "subtitle": subtitle, "cta_label": cta},
            )
        if was_created:
            self.stdout.write("  banner de demonstração")

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

    # -- remoção -----------------------------------------------------------

    def remove_demo_data(self):
        section_names = [data["internal_name"] for data in SECTIONS]
        sections = HomeSection.objects.filter(internal_name__in=section_names)
        banners = HomeBanner.objects.filter(internal_name=BANNER["internal_name"])
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
