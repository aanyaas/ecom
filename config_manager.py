import os
import json

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'global_config.json')

DEFAULT_CONFIG = {
    'COLOR_MAP': {
        'Multicolor': 'conic-gradient(red, yellow, lime, aqua, blue, magenta, red)',
        'Red': '#ff2424',
        'Blue': '#005ed0',
        'Green': '#00a63d',
        'Yellow': '#ffeb3b',
        'Pink': '#ffc1d6',
        'Black': '#000000',
        'White': '#ffffff',
        'Orange': '#e68a00',
        'Purple': '#a349d4',
        'Beige': '#ead8ad',
        'Brown': '#8d4b2d',
        'Grey': '#c0c0c0',
        'Maroon': '#b03060',
        'Peach': '#ffdab9'
    },
    'COLOR_NAME_MAP': {
        'bg': 'Beige',
        'wt': 'White',
        'bk': 'Black',
        'rd': 'Red',
        'bl': 'Blue',
        'gr': 'Green',
        'yl': 'Yellow',
        'mc': 'Multicolor',
        'pk': 'Pink',
        'or': 'Orange',
        'pr': 'Purple',
        'br': 'Brown',
        'gy': 'Grey',
        'mr': 'Maroon',
        'ph': 'Peach'
    },
    'FREE_SHIPPING_THRESHOLD': 500.00,
    'DEFAULT_SHIPPING_CHARGE': 99.00,
    'VALID_COUPONS': {
        'F10': {'discount': 10, 'type': 'percentage', 'min_order': 1500},
        'F15': {'discount': 15, 'type': 'percentage', 'min_order': 2500},
        'F20': {'discount': 20, 'type': 'percentage', 'min_order': 5000},
        'F25': {'discount': 25, 'type': 'percentage', 'min_order': 10000}
    },
    'INSTAGRAM_CACHE_TIMEOUT': 3600,
    'INSTAGRAM_MEDIA_COUNT': 6,
    'MAX_CONTENT_LENGTH': 16777216,
    'COLOR_CHOICES': [
        ['', 'Select Color'],
        ['Multicolor', 'Multicolor (MC)'],
        ['Red', 'Red (RD)'],
        ['Blue', 'Blue (BL)'],
        ['Green', 'Green (GR)'],
        ['Yellow', 'Yellow (YL)'],
        ['Pink', 'Pink (PK)'],
        ['Black', 'Black (BK)'],
        ['White', 'White (WT)'],
        ['Orange', 'Orange (OR)'],
        ['Purple', 'Purple (PR)'],
        ['Beige', 'Beige (BG)'],
        ['Brown', 'Brown (BR)'],
        ['Grey', 'Grey (GY)'],
        ['Maroon', 'Maroon (MR)'],
        ['Peach', 'Peach (PH)'],
        ['Other', 'Other (OT)']
    ],
    'SIZE_CHOICES': [
        ['', 'Select Size'],
        ['XS', 'Extra Small (XS)'],
        ['S', 'Small (S)'],
        ['M', 'Medium (M)'],
        ['L', 'Large (L)'],
        ['XL', 'Extra Large (XL)'],
        ['XXL', 'XX-Large'],
        ['one_size', 'One Size'],
        ['adjustable', 'Adjustable'],
        ['custom', 'Custom (Specify)']
    ],
    'GST_CHOICES': [
        ['0', '0%'],
        ['3', '3%'],
        ['5', '5%'],
        ['12', '12%'],
        ['18', '18%'],
        ['40', '40%']
    ],
    'MATERIAL_CHOICES': [
        ['paper', 'Paper'],
        ['thread', 'Thread'],
        ['oxidised', 'Oxidised'],
        ['brass', 'Brass'],
        ['fabric', 'Fabric'],
        ['other', 'Other']
    ],
    'HOME_BANNERS': [
        {
            'media_path': 'img/carousel-3.webm',
            'is_video': True,
            'title': 'Wearable Masterpieces',
            'subtitle': 'Discover the beauty of artisan-crafted jewelry',
            'link_url': '/shop'
        },
        {
            'media_path': 'img/carousel-1.jpg',
            'is_video': False,
            'title': 'Signature Collection 2026',
            'subtitle': 'Elegance in Every Detail',
            'link_url': '/shop'
        },
        {
            'media_path': 'img/carousel-2.jpg',
            'is_video': False,
            'title': 'Handcrafted with Love',
            'subtitle': 'Timeless Artisan Jewellery',
            'link_url': '/shop'
        }
    ],
    'HOME_CATEGORIES': ['necklaces', 'earrings', 'bags', 'hair'],
    'HOME_OFFERS': [
        {
            'title': 'Festive Collections',
            'subtitle': 'Exclusive Offer',
            'text': 'Up to 50% Off on all festive artisan jewellery.',
            'link_url': '/shop?category=festive',
            'bg_image': 'img/offer-1.jpg',
            'align': 'left'
        },
        {
            'title': 'Decoration Items',
            'subtitle': 'New Arrivals',
            'text': 'Up to 50% Off on handcrafted home decor pieces.',
            'link_url': '/shop?category=decorative',
            'bg_image': 'img/offer-2.jpg',
            'align': 'right'
        }
    ]
}

_cached_config = None

def load_config():
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            _cached_config = json.load(f)
            return _cached_config
    except Exception as e:
        print(f"Error loading config file: {e}")
        return DEFAULT_CONFIG

def save_config(config_data):
    global _cached_config
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        _cached_config = config_data
        return True
    except Exception as e:
        print(f"Error saving config file: {e}")
        return False

def get_config(key, default=None):
    config = load_config()
    return config.get(key, default)
